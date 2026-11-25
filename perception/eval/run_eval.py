# -*- coding: utf-8 -*-
"""C3 · 评测入口:在 OpenFly 标注的航点 GT 上运行基线策略并计算指标。

基线(信息集诚实标注,构成指标灵敏度梯子):
  gt_oracle   回放 GT 航点(用完整 GT)      → 期望 SR=SPL=nDTW=1,校准评测器
  noisy_5m    GT 航点 + 每步 N(0,5m) 噪声    → 定位误差敏感性,SR 应明显下降
  truncated_60 只走 GT 轨迹前 60% 后悬停     → 模拟"未到达",SR=0 但 OSR 高
  random_walk 同长度随机游走(不知目标)     → 底线,各指标应接近 0

用法:python eval/run_eval.py
输出:perception/eval/baseline_results.json
"""
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from metrics import evaluate_episodes  # noqa: E402

DATA = ROOT / "data"
ANN = DATA / "Annotation"
OUT = ROOT / "eval"
SEED = 42

TRAIN_SUBSET = 1000   # train split 参与评测的样本数(分层见下);seen/unseen 全量


def load_split(name):
    with (ANN / f"{name}.json").open(encoding="utf-8") as f:
        return json.load(f)


# ---------- 基线策略:输入 GT 航点序列,输出预测轨迹 ----------

def gt_oracle(gt):
    return [tuple(p) for p in gt]


def noisy_5m(gt, rng, sigma=5.0):
    out = []
    for p in gt:
        out.append((p[0] + rng.gauss(0, sigma),
                    p[1] + rng.gauss(0, sigma),
                    p[2] + rng.gauss(0, sigma)))
    return out


def truncated_60(gt):
    k = max(2, int(len(gt) * 0.6))
    return [tuple(p) for p in gt[:k]]


def random_walk(gt, rng, step=10.0):
    cur = tuple(gt[0])
    out = [cur]
    for _ in range(len(gt) - 1):
        nxt = (cur[0] + rng.gauss(0, step),
               cur[1] + rng.gauss(0, step),
               cur[2] + rng.gauss(0, step * 0.2))
        out.append(nxt)
        cur = nxt
    return out


def main():
    rng = random.Random(SEED)
    episodes_by_policy = {k: [] for k in
                          ("gt_oracle", "noisy_5m", "truncated_60", "random_walk")}
    counts = {}
    for name in ("train", "seen", "unseen"):
        items = load_split(name)
        if name == "train":
            rng2 = random.Random(SEED)
            items = rng2.sample(items, min(TRAIN_SUBSET, len(items)))
        counts[name] = len(items)
        print(f"{name}: evaluating {len(items)} episodes ...", flush=True)
        for it in items:
            gt = [tuple(p) for p in it["pos"]]
            if len(gt) < 2:
                continue
            episodes_by_policy["gt_oracle"].append((gt, gt_oracle(gt)))
            episodes_by_policy["noisy_5m"].append((gt, noisy_5m(gt, rng)))
            episodes_by_policy["truncated_60"].append((gt, truncated_60(gt)))
            episodes_by_policy["random_walk"].append((gt, random_walk(gt, rng)))

    results = {"dataset": "IPEC-COMMUNITY/OpenFly(标注航点 GT)",
               "episodes": counts, "seed": SEED,
               "note": "gt_oracle/noisy/truncated 使用 GT 目标信息,作评测器校准用;"
                       "random_walk 不使用目标信息,是无信息基线下限。",
               "policies": {}}
    for pol, eps in episodes_by_policy.items():
        r = evaluate_episodes(eps)
        results["policies"][pol] = r
        pretty = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
        print(f"  {pol:12s} {pretty}")

    (OUT / "baseline_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("results ->", OUT / "baseline_results.json")

    # 灵敏度梯子断言(评测器正确性自检)
    p = results["policies"]
    assert p["gt_oracle"]["SR"] == 1.0 and p["gt_oracle"]["SPL"] == 1.0
    assert p["gt_oracle"]["nDTW"] > 0.99
    assert 0.0 < p["noisy_5m"]["SR"] < 1.0, "5m 噪声下 SR 应部分下降"
    # truncated 60% 后悬停:因 GT 末段常悬停(action=9),前 60% 可能已接近目标
    assert p["truncated_60"]["SR"] < 0.10, "truncated 60% 大部分应失败"
    assert p["random_walk"]["SR"] < 0.01
    print("[PASS] baseline sanity ladder: oracle=1 > noisy > truncated(SR<0.1) > random≈0")


if __name__ == "__main__":
    sys.exit(main())
