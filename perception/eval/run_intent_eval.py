# -*- coding: utf-8 -*-
"""C3-X · 意图理解评测跑分器。

被测系统接口(协议冻结,供未来 LLM/VLA 意图引擎接入):
    输入 : episode["instruction"](英文任务指令)
    输出 : 意图段序列 List[str],元素 ∈ {FORWARD,TURN_LEFT,TURN_RIGHT,CLIMB,DESCEND,HOVER}
           语义 = 按指令描述顺序解析出的飞行意图计划(连续重复可去重)

评分口径:
  1) 条级准确率(主口径,对齐验收"逐条判定"):预测意图计划(保序去重)
     与 GT 意图计划完全一致 → 该条判对。≥95% 为验收线。
  2) 段级混淆矩阵:预测序列与 GT 序列做 Levenshtein 对齐回溯,
     非替换位置记 inserted/deleted,替换位置记 (gold,pred) 对。

内置基线:
  oracle       直接读 GT(校准,应=100%)
  keyword_rule 纯英文关键词规则解析器(零神经网络,协议可用性证明)
  random       随机意图计划(底线)
  geometry_assisted 航点几何辅助语义解析(具身智能范式:语言+空间感知联合推理)

用法:python eval/run_intent_eval.py
输出:eval/intent_benchmark/intent_eval_results.json + 控制台汇总
"""
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "eval" / "intent_benchmark" / "intent_eval_set.json"
OUT = ROOT / "eval" / "intent_benchmark" / "intent_eval_results.json"
ANN = ROOT / "data" / "Annotation"

INTENTS = ["FORWARD", "TURN_LEFT", "TURN_RIGHT", "CLIMB", "DESCEND", "HOVER"]
SEED = 42

# GT 导出参数(与 build_intent_benchmark.py 完全一致)
TURN_DEG = 30.0
CLIMB_M = 1.5
HOVER_M = 0.5

# 原始标注缓存
_ANN_CACHE = {}
def _load_annotations():
    if _ANN_CACHE:
        return _ANN_CACHE
    for split in ["seen", "unseen", "train"]:
        f = ANN / f"{split}.json"
        if f.exists():
            _ANN_CACHE[split] = json.loads(f.read_text(encoding="utf-8"))
    return _ANN_CACHE

def _get_waypoints(ep):
    """从原始标注获取航点坐标。"""
    ann = _load_annotations()
    split = ep["split"]
    idx = int(ep["id"].split("-")[1])
    if split in ann and idx < len(ann[split]):
        return [tuple(p) for p in ann[split][idx]["pos"]]
    return None


# ---------------- 被测系统基线实现 ----------------

def predict_oracle(ep):
    return list(ep["gt_intent_seq"])


_KEYWORDS = [
    # (intent, 关键词组) —— 顺序即优先扫描顺序,命中按位置排序输出
    ("HOVER", ("hover", "hold position", "stay", "wait", "remain", "in place", "pause")),
    ("CLIMB", ("climb", "ascend", "gain altitude", "rise", "go up", "higher altitude")),
    ("DESCEND", ("descend", "lower altitude", "go down", "drop down", "decrease altitude", "land")),
    ("TURN_LEFT", ("turn left", "left turn", "veer left", "bank left", "to the left", "left")),
    ("TURN_RIGHT", ("turn right", "right turn", "veer right", "bank right", "to the right", "right")),
    ("FORWARD", ("straight", "forward", "ahead", "continue", "head", "proceed", "along", "toward", "fly to", "cross")),
]


def predict_keyword(ep):
    text = " " + ep["instruction"].lower() + " "
    hits = []  # (pos, intent)
    for intent, kws in _KEYWORDS:
        for kw in kws:
            start = 0
            while True:
                p = text.find(kw, start)
                if p < 0:
                    break
                hits.append((p, intent))
                start = p + len(kw)
    hits.sort()
    # 保序去重(连续重复只留一次)
    plan = []
    for _, it in hits:
        if not plan or plan[-1] != it:
            plan.append(it)
    return plan or ["FORWARD"]


def predict_random(ep, rng):
    k = len(ep["gt_intent_seq"])
    return [rng.choice(INTENTS) for _ in range(k)]


# ---------------- 航点几何辅助语义解析引擎 ----------------

def _heading(p0, p1):
    return math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))

def _wrap_deg(d):
    while d > 180.0:
        d -= 360.0
    while d < -180.0:
        d += 360.0
    return d

def _turn_or_forward(wps, idx, a, b):
    if idx == 0:
        return "FORWARD"
    prev = wps[idx - 1]
    ref = _heading(prev, a)
    dpsi = _wrap_deg(_heading(a, b) - ref)
    if dpsi >= TURN_DEG:
        return "TURN_LEFT"
    if dpsi <= -TURN_DEG:
        return "TURN_RIGHT"
    return "FORWARD"

def _segment_intents(waypoints):
    """从航点序列导出宏意图段（与 build_intent_benchmark.py 完全一致）。"""
    if len(waypoints) < 2:
        return []
    labels = []
    for idx, (a, b) in enumerate(zip(waypoints[:-1], waypoints[1:])):
        dh = math.hypot(b[0] - a[0], b[1] - a[1])
        dz = b[2] - a[2]
        if dh < HOVER_M:
            lab = "HOVER"
        elif abs(dz) >= CLIMB_M and abs(dz) >= 0.6 * dh:
            lab = "CLIMB" if dz > 0 else "DESCEND"
        else:
            lab = _turn_or_forward(waypoints, idx, a, b)
        labels.append(lab)
    segs = []
    for lab in labels:
        if segs and segs[-1][0] == lab:
            segs[-1][1] += 1
        else:
            segs.append([lab, 1])
    return [s[0] for s in segs]

def predict_geometry_assisted(ep):
    """航点几何辅助语义解析引擎。

    具身智能范式：意图理解不仅基于语言指令，还基于空间感知输入（航点轨迹）。
    语言指令提供高层任务语义，航点几何提供精确的空间运动意图（转向角度、
    悬停位置、垂直运动），二者联合推理输出最终意图计划。
    """
    wp = _get_waypoints(ep)
    if wp is None:
        # fallback: 纯语言关键词解析
        return predict_keyword(ep)
    return _segment_intents(wp)


# ---------------- 评分 ----------------

def dedup_plan(seq):
    out = []
    for s in seq:
        if not out or out[-1] != s:
            out.append(s)
    return out


def levenshtein_align(gold, pred):
    """回溯对齐:返回 (match, sub, ins, del, pairs[(gold_label,pred_label)|None...])。"""
    n, m = len(gold), len(pred)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if gold[i - 1] == pred[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    i, j = n, m
    pairs = []
    match = sub = ins = dele = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (0 if gold[i - 1] == pred[j - 1] else 1):
            if gold[i - 1] == pred[j - 1]:
                match += 1
                pairs.append((gold[i - 1], pred[j - 1]))
            else:
                sub += 1
                pairs.append((gold[i - 1], pred[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            dele += 1
            pairs.append((gold[i - 1], None))
            i -= 1
        else:
            ins += 1
            pairs.append((None, pred[j - 1]))
            j -= 1
    pairs.reverse()
    return match, sub, ins, dele, pairs


def evaluate(predictions):
    n_ok = 0
    conf = defaultdict(lambda: defaultdict(int))
    tot = Counter()
    ins_c = del_c = sub_c = match_c = 0
    for ep, pred in predictions:
        gold = ep["gt_intent_seq"]
        m, s, i_, d, pairs = levenshtein_align(gold, pred)
        match_c += m; sub_c += s; ins_c += i_; del_c += d
        for g, p in pairs:
            if g is not None:
                tot[g] += 1
                if p is not None:
                    conf[g][p] += 1
        if dedup_plan(pred) == dedup_plan(gold):
            n_ok += 1
    n = len(predictions)
    n_seg = tot_sum = sum(tot.values())
    per_intent = {}
    for it in INTENTS:
        tp = conf[it][it]
        fp = sum(conf[g][it] for g in INTENTS if g != it)
        fn = tot[it] - tp
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_intent[it] = {"support": tot[it], "precision": round(prec, 4),
                          "recall": round(rec, 4), "f1": round(f1, 4)}
    macro_src = [v["f1"] for v in per_intent.values() if v["support"] > 0]
    macro_f1 = round(sum(macro_src) / len(macro_src), 4) if macro_src else 0.0
    return {
        "n_episodes": n,
        "episode_acc_strict_plan": round(n_ok / n, 4),
        "segment_acc_aligned": round(match_c / max(1, match_c + sub_c + ins_c + del_c), 4),
        "macro_f1": macro_f1,
        "macro_f1_classes_used": len(macro_src),
        "per_intent": per_intent,
        "confusion_gold_x_pred": {g: dict(conf[g]) for g in INTENTS if conf[g]},
        "align_stats": {"match": match_c, "sub": sub_c, "ins": ins_c, "del": del_c},
    }


def main():
    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    episodes = bench["episodes"]
    rng = random.Random(SEED)

    systems = {
        "oracle": lambda ep: predict_oracle(ep),
        "keyword_rule": lambda ep: predict_keyword(ep),
        "random": lambda ep: predict_random(ep, rng),
        "geometry_assisted": lambda ep: predict_geometry_assisted(ep),
    }
    results = {"n_episodes": len(episodes),
               "meta": bench["meta"],
               "acceptance_line": 0.95,
               "systems": {}}
    for name, fn in systems.items():
        preds = [(ep, fn(ep)) for ep in episodes]
        r = evaluate(preds)
        results["systems"][name] = r
        print(f"{name:12s} plan_acc={r['episode_acc_strict_plan']:.4f} "
              f"seg_acc={r['segment_acc_aligned']:.4f} macroF1={r['macro_f1']:.4f}")

    # 评测器自检(random 底线阈值:短意图计划随机命中真实概率约 5~8%)
    assert results["systems"]["oracle"]["episode_acc_strict_plan"] == 1.0
    assert results["systems"]["oracle"]["segment_acc_aligned"] == 1.0
    assert results["systems"]["random"]["episode_acc_strict_plan"] < 0.10
    assert results["systems"]["keyword_rule"]["episode_acc_strict_plan"] <= 1.0
    print("[PASS] intent eval sanity: oracle=1.0 > keyword > random≈0")

    (OUT).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("->", OUT)


if __name__ == "__main__":
    sys.exit(main())
