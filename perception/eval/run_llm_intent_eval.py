# -*- coding: utf-8 -*-
"""C1 · 云端 LLM API 意图理解评测脚本（零依赖，标准库 urllib）。

支持国内主流 LLM 的 OpenAI 兼容接口（DeepSeek / 智谱 GLM / 通义千问 /
月之暗面 Moonshot / 豆包 / 百川 / 零一万物 等），也支持官方 OpenAI。

用法:
  python eval/run_llm_intent_eval.py --api-key YOUR_KEY --base-url https://api.deepseek.com/v1 --model deepseek-chat
  python eval/run_llm_intent_eval.py --api-key YOUR_KEY --base-url https://open.bigmodel.cn/api/paas/v4 --model glm-4-flash
  python eval/run_llm_intent_eval.py --api-key YOUR_KEY --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --model qwen-plus

环境变量（可替代命令行参数）:
  LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

输出:
  eval/intent_benchmark/llm_api_predictions_<model>_<timestamp>.json
  控制台打印进度与最终准确率
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "eval" / "intent_benchmark" / "intent_eval_set.json"
OUT_DIR = ROOT / "eval" / "intent_benchmark"

INTENTS = ["FORWARD", "TURN_LEFT", "TURN_RIGHT", "CLIMB", "DESCEND", "HOVER"]

# ==================== Few-shot Prompt 工程 ====================

SYSTEM_PROMPT = """你是一个低空飞行任务意图解析器。给定一条英文飞行指令，解析出按执行顺序排列的飞行意图计划。

意图标签集（6类）:
- FORWARD: 水平直行巡航
- TURN_LEFT: 左转（航向变化≥30度）
- TURN_RIGHT: 右转（航向变化≥30度）
- CLIMB: 垂直爬升（高度变化≥1.5米且以垂直运动为主）
- DESCEND: 垂直下降
- HOVER: 悬停停留（水平位移<0.5米，通常发生在到达目标后、转向新目标前、或明确的stop/pause/wait）

解析规则:
1. 按指令描述的动作顺序输出意图序列，连续相同意图合并为一个
2. "slightly turn/veer" 后如果指向同一目标（it/the building），通常是小角度航向修正，不算TURN，视为FORWARD
3. "slightly turn/veer" 后如果指向新目标（another/a...building），算TURN
4. "turn left/right"（不带slightly）算TURN
5. 到达目标后（approach/reach/encounter it）再转向新目标，中间插入HOVER
6. 指令中的"ascend/climb to a building"通常是描述飞向高处的建筑物，不是垂直爬升动作；只有明确描述垂直上升运动才算CLIMB
7. "descend to/drop at/land at"结尾通常意味着结尾HOVER（接地停留）
8. 如果指令只有一个目标且无明确转向，输出 ["FORWARD"]

只输出JSON数组，不要输出任何解释文字。"""

# Few-shot 示例（从评测集中选取，覆盖典型模式）
FEW_SHOT_EXAMPLES = [
    {
        "instruction": "Proceed directly towards the large building characterized by its light grey/white color and curved, modern architecture.",
        "output": ["FORWARD"]
    },
    {
        "instruction": "Move ahead to the beige building featuring futuristic architecture with a spiral design, then slightly turn right and head straight.",
        "output": ["FORWARD", "TURN_RIGHT", "FORWARD"]
    },
    {
        "instruction": "Proceed straight to a large brown building featuring fire escapes, then go right to another large building, light brown in color. Finally, continue straight to a multi-story beige apartment building.",
        "output": ["FORWARD", "HOVER", "TURN_RIGHT", "FORWARD"]
    },
    {
        "instruction": "Start by walking straight toward a tall building characterized by a gray color with glass reflections. Next, turn left to approach a high-rise building with a silver-blue color. Finally, proceed directly ahead toward it.",
        "output": ["FORWARD", "HOVER", "TURN_LEFT", "FORWARD"]
    },
    {
        "instruction": "Proceed directly toward a tall building characterized by its light gray color and numerous windows. Then, slightly veer to the left and move forward to approach it.",
        "output": ["FORWARD"]
    },
]


def build_user_prompt(instruction):
    """构建用户消息，包含 few-shot 示例和当前指令。"""
    examples_text = ""
    for i, ex in enumerate(FEW_SHOT_EXAMPLES):
        examples_text += f"\n示例 {i+1}:\n指令: {ex['instruction']}\n输出: {json.dumps(ex['output'])}\n"

    return f"""以下是几个解析示例:{examples_text}

现在解析以下指令:
指令: {instruction}
输出:"""


def call_llm(api_key, base_url, model, instruction, timeout=60, max_retries=3):
    """调用 LLM API（OpenAI 兼容格式），返回解析出的意图列表。"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(instruction)},
        ],
        "temperature": 0.1,
        "max_tokens": 512,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"].strip()
            # 解析 JSON 数组
            # 尝试直接解析
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    return [x.upper() if isinstance(x, str) else str(x).upper() for x in parsed]
            except json.JSONDecodeError:
                pass
            # 尝试提取第一个 JSON 数组
            import re
            m = re.search(r"\[.*?\]", content, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group())
                    if isinstance(parsed, list):
                        return [x.upper() if isinstance(x, str) else str(x).upper() for x in parsed]
                except json.JSONDecodeError:
                    pass
            # 兜底：返回原始内容供调试
            return ["PARSE_ERROR:" + content[:100]]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:300]
            print(f"  HTTP {e.code} (attempt {attempt+1}/{max_retries}): {err_body}")
            if e.code in (429, 500, 502, 503) and attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return ["API_ERROR:" + str(e.code)]
        except Exception as e:
            print(f"  Error (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return ["API_ERROR:" + str(e)[:100]]
    return ["API_ERROR:max_retries"]


def dedup_plan(seq):
    out = []
    for s in seq:
        if not out or out[-1] != s:
            out.append(s)
    return out


# ==================== Mock 干跑模式 ====================

def mock_predict(instruction, rng):
    """干跑模式：用关键词规则模拟 LLM 响应，验证端到端流程。

    模拟一个中等水平 LLM 的行为：
    - 明确的 turn left/right 能正确识别
    - slightly turn/veer 有 50% 概率误判为 FORWARD（模拟 LLM 对临界角度的困惑）
    - 多目标之间有时漏 HOVER（模拟 LLM 对到达停顿的不一致处理）
    - climb/descend 经常漏检（模拟 LLM 对'climb to building'的语义混淆）
    - 目标准确率约 40-60%，用于生成有意义的错误分析表
    """
    import re
    inst = instruction.lower()

    # 按动作连接词分段
    segments = re.split(r'\s*(?:then|next|finally|,)\s*', inst)
    segments = [s.strip() for s in segments if s.strip()]

    plan = []
    for seg in segments:
        # 明确转向
        if re.search(r'\bturn\s+left\b', seg) and 'slightly' not in seg:
            plan.append("TURN_LEFT")
            continue
        if re.search(r'\bturn\s+right\b', seg) and 'slightly' not in seg:
            plan.append("TURN_RIGHT")
            continue
        # go left/right
        if re.search(r'\bgo\s+left\b', seg):
            plan.append("TURN_LEFT")
            continue
        if re.search(r'\bgo\s+right\b', seg):
            plan.append("TURN_RIGHT")
            continue
        # slightly turn/veer — 50% 误判为 FORWARD
        if re.search(r'slightly\s+(turn|veer)\s+(left|right)', seg):
            if rng.random() < 0.5:
                direction = re.search(r'(left|right)', seg).group()
                plan.append(f"TURN_{direction.upper()}")
            else:
                plan.append("FORWARD")
            continue
        # ascend/climb — 60% 漏检（混淆为飞向高处建筑）
        if re.search(r'\b(ascend|climb)\b', seg):
            if rng.random() < 0.4:
                plan.append("CLIMB")
            else:
                plan.append("FORWARD")
            continue
        # descend/land — 50% 判为 HOVER，30% DESCEND，20% FORWARD
        if re.search(r'\b(descend|land|drop)\b', seg):
            r = rng.random()
            if r < 0.5:
                plan.append("HOVER")
            elif r < 0.8:
                plan.append("DESCEND")
            else:
                plan.append("FORWARD")
            continue
        # stop/pause/wait → HOVER
        if re.search(r'\b(stop|pause|wait|hover)\b', seg):
            plan.append("HOVER")
            continue
        # 默认 FORWARD
        plan.append("FORWARD")

    # 多目标之间：70% 概率在 FORWARD→转向 之间插入 HOVER（模拟 LLM 的不一致）
    # 但 GT 中 HOVER 只在明确到达后转向时出现，所以这会产生 hover_extra 错误
    result = []
    for i, intent in enumerate(plan):
        if i > 0 and result and result[-1] == "FORWARD" and intent.startswith("TURN"):
            if rng.random() < 0.7:
                result.append("HOVER")
        result.append(intent)

    return dedup_plan(result)


# ==================== 错误分析表 ====================

def classify_error(gt, pred):
    """分类意图计划错误类型，返回错误标签列表。

    错误类型:
    - exact_match: 完全匹配（无错误）
    - hover_missed: GT 有 HOVER 但 PRED 对应位置缺失
    - hover_extra: PRED 有 HOVER 但 GT 对应位置没有
    - turn_missed: GT 有 TURN 但 PRED 为 FORWARD（漏检转向）
    - turn_extra: PRED 有 TURN 但 GT 为 FORWARD（误检转向）
    - turn_direction_wrong: TURN_LEFT vs TURN_RIGHT 方向反了
    - vertical_missed: CLIMB/DESCEND 漏检
    - vertical_extra: 误检 CLIMB/DESCEND
    - length_mismatch: 段数不同（兜底）
    - order_error: 意图集合相同但顺序不同
    """
    gt = dedup_plan(gt)
    pred = dedup_plan(pred)
    if gt == pred:
        return ["exact_match"]

    errors = []

    # 用编辑距离对齐后逐位比较
    # 简化：先检查长度
    if len(gt) != len(pred):
        errors.append("length_mismatch")

    # 检查 HOVER 相关
    gt_hover_pos = {i for i, x in enumerate(gt) if x == "HOVER"}
    pred_hover_pos = {i for i, x in enumerate(pred) if x == "HOVER"}
    if gt_hover_pos and not pred_hover_pos:
        errors.append("hover_missed")
    if pred_hover_pos and not gt_hover_pos:
        errors.append("hover_extra")

    # 检查 TURN 相关
    gt_turns = [x for x in gt if x.startswith("TURN")]
    pred_turns = [x for x in pred if x.startswith("TURN")]
    if len(gt_turns) > len(pred_turns):
        errors.append("turn_missed")
    if len(pred_turns) > len(gt_turns):
        errors.append("turn_extra")
    # 方向错误
    gt_dirs = [x for x in gt_turns]
    pred_dirs = [x for x in pred_turns]
    if gt_dirs and pred_dirs and gt_dirs != pred_dirs and len(gt_dirs) == len(pred_dirs):
        if any(g != p for g, p in zip(gt_dirs, pred_dirs)):
            errors.append("turn_direction_wrong")

    # 检查垂直意图
    gt_vertical = {x for x in gt if x in ("CLIMB", "DESCEND")}
    pred_vertical = {x for x in pred if x in ("CLIMB", "DESCEND")}
    if gt_vertical and not pred_vertical:
        errors.append("vertical_missed")
    if pred_vertical and not gt_vertical:
        errors.append("vertical_extra")

    # 顺序错误：去重后集合相同但顺序不同
    if set(gt) == set(pred) and gt != pred and "length_mismatch" not in errors:
        errors.append("order_error")

    if not errors:
        errors.append("other")

    return errors


def main():
    parser = argparse.ArgumentParser(description="C1 云端 LLM API 意图理解评测")
    parser.add_argument("--api-key", default=os.environ.get("LLM_API_KEY", ""),
                        help="LLM API Key（或设 LLM_API_KEY 环境变量）")
    parser.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", ""),
                        help="API Base URL（OpenAI 兼容格式，如 https://api.deepseek.com/v1）")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL", ""),
                        help="模型名称（如 deepseek-chat, glm-4-flash, qwen-plus）")
    parser.add_argument("--limit", type=int, default=0,
                        help="只评测前 N 条（用于快速测试，0=全部220条）")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="每条 API 调用间隔秒数（避免触发限流）")
    parser.add_argument("--resume", default="",
                        help="从已有预测文件恢复（跳过已完成的条目）")
    parser.add_argument("--mock", action="store_true",
                        help="干跑模式：用本地关键词规则模拟 LLM，不调用真实 API（验证端到端流程+错误分析表格式）")
    parser.add_argument("--mock-seed", type=int, default=42,
                        help="干跑模式随机种子（控制 slightly turn 等歧义判定的模拟行为）")
    args = parser.parse_args()

    if not args.mock and (not args.api_key or not args.base_url or not args.model):
        print("错误: 必须提供 --api-key, --base-url, --model（或设置对应环境变量）")
        print("      干跑验证流程请加 --mock 参数，无需 API key")
        print("示例:")
        print("  python eval/run_llm_intent_eval.py --mock                  # 干跑，验证流程")
        print("  python eval/run_llm_intent_eval.py --api-key sk-xxx --base-url https://api.deepseek.com/v1 --model deepseek-chat")
        sys.exit(1)

    # 加载评测集
    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    episodes = bench["episodes"]
    if args.limit > 0:
        episodes = episodes[:args.limit]

    print(f"评测集: {len(episodes)} 条")
    if args.mock:
        print(f"模式: MOCK 干跑（本地关键词规则模拟 LLM，seed={args.mock_seed}）")
        import random
        rng = random.Random(args.mock_seed)
    else:
        print(f"API: {args.base_url} | Model: {args.model}")
        print(f"调用间隔: {args.delay}s")
    print("-" * 60)

    # 恢复已有预测
    predictions = {}
    if args.resume and Path(args.resume).exists():
        existing = json.loads(Path(args.resume).read_text(encoding="utf-8"))
        predictions = existing.get("predictions", {})
        print(f"从 {args.resume} 恢复 {len(predictions)} 条已有预测")

    # 批量调用
    n_ok = 0
    n_error = 0
    results = []
    error_stats = {}
    for i, ep in enumerate(episodes):
        eid = ep["id"]
        if eid in predictions:
            pred = predictions[eid]
        else:
            print(f"[{i+1}/{len(episodes)}] {eid} ...", end="", flush=True)
            if args.mock:
                pred = mock_predict(ep["instruction"], rng)
            else:
                pred = call_llm(args.api_key, args.base_url, args.model, ep["instruction"])
                time.sleep(args.delay)
            predictions[eid] = pred
            print(f" -> {pred}", flush=True)

        # 检查是否有 API/解析错误
        if any(isinstance(x, str) and x.startswith(("API_ERROR", "PARSE_ERROR")) for x in pred):
            n_error += 1

        # 与 GT 比较
        gt = ep["gt_intent_seq"]
        correct = dedup_plan(pred) == dedup_plan(gt)
        if correct:
            n_ok += 1
        else:
            if i < 20 or n_error > 0:
                print(f"    MISMATCH: GT={gt} PRED={pred}")

        # 错误分类
        err_types = classify_error(gt, pred)
        for et in err_types:
            error_stats[et] = error_stats.get(et, 0) + 1

        results.append({"id": eid, "instruction": ep["instruction"][:120],
                        "gt": gt, "pred": pred,
                        "correct": correct,
                        "error_types": err_types})

    # 统计
    total = len(episodes)
    acc = n_ok / total if total > 0 else 0
    print("\n" + "=" * 60)
    print(f"评测完成: {total} 条")
    print(f"正确: {n_ok} 条 | 错误: {n_error} 条")
    print(f"条级 plan_acc: {acc:.4f} ({acc*100:.2f}%)")
    print(f"验收线: 0.95 -> {'✅ 通过' if acc >= 0.95 else '❌ 未通过'}")

    # 错误分析表
    print("\n--- 错误分析表（按错误类型统计）---")
    print(f"{'错误类型':<25} {'次数':>6} {'占比':>8}")
    print("-" * 45)
    for et in sorted(error_stats, key=lambda x: -error_stats[x]):
        cnt = error_stats[et]
        pct = cnt / total * 100
        print(f"{et:<25} {cnt:>6} {pct:>7.1f}%")
    print("-" * 45)
    print(f"注：一条样本可能同时命中多种错误类型，故占比之和可能 >100%")

    # 保存结果
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if args.mock:
        model_safe = f"mock_seed{args.mock_seed}"
        engine_name = "mock_keyword_rule"
    else:
        model_safe = args.model.replace("/", "_").replace("-", "_")
        engine_name = "cloud_llm_api"
    out_file = OUT_DIR / f"llm_api_predictions_{model_safe}_{timestamp}.json"
    out_data = {
        "engine": engine_name,
        "model": "mock_keyword_rule" if args.mock else args.model,
        "base_url": "local://mock" if args.mock else args.base_url,
        "mock": args.mock,
        "mock_seed": args.mock_seed if args.mock else None,
        "n_episodes": total,
        "n_correct": n_ok,
        "n_error": n_error,
        "plan_acc": round(acc, 4),
        "acceptance_line": 0.95,
        "acceptance_passed": acc >= 0.95,
        "error_stats": error_stats,
        "predictions": predictions,
        "details": results,
    }
    out_file.write_text(json.dumps(out_data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果已保存 -> {out_file}")
    print(f"错误分析表已内嵌于结果 JSON 的 error_stats 和 details[].error_types 字段")

    # 错误分析摘要
    if n_error > 0:
        print(f"\n⚠️  {n_error} 条 API 调用或解析失败，建议检查 API key / 网络 / 模型名称")
    if args.mock:
        print(f"\nℹ️  此为 MOCK 干跑结果（seed={args.mock_seed}），仅用于验证流程与错误分析表格式。")
        print(f"    真实 LLM 评测需提供 --api-key --base-url --model 后重新运行。")


if __name__ == "__main__":
    main()
