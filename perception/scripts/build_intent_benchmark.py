# -*- coding: utf-8 -*-
"""C3-X · 任务意图理解评测集构建(验收指标3: 意图理解准确率 ≥95%)。

方法:GT 意图**从标注航点几何导出**,与 action 编码语义解耦(官方 statistc.py
仅证实 9≈stop,无完整语义表;几何导出可审计、可复现)。

宏意图集(6 类,对齐验收语境"任务规划、航路导航场景"):
  FORWARD     直行巡航(航向变化 <30° 且水平位移足够)
  TURN_LEFT   左转(航向变化 ≥+30°,右手系 z 朝上)
  TURN_RIGHT  右转(航向变化 ≤-30°)
  CLIMB       爬升(|Δz| ≥ 1.5m 且 Δz>0)
  DESCEND     下降(|Δz| ≥ 1.5m 且 Δz<0)
  HOVER       悬停等待(水平位移 < 0.5m)

逐段判定优先级:HOVER > CLIMB/DESCEND > TURN > FORWARD(每相邻航点对判定一次,
然后 run-length 压缩为意图段)。

评测集:seen 100 + unseen 60 + train 60 = 220 条 ≥200(映射表 §4.1 要求),seed=42。
输出:perception/eval/intent_benchmark/intent_eval_set.json
"""
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANN = ROOT / "data" / "Annotation"
OUT_DIR = ROOT / "eval" / "intent_benchmark"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SEED = 42
BUDGET = {"seen": 100, "unseen": 60, "train": 60}
TURN_DEG = 30.0
CLIMB_M = 1.5
HOVER_M = 0.5

INTENTS = ["FORWARD", "TURN_LEFT", "TURN_RIGHT", "CLIMB", "DESCEND", "HOVER"]


def heading(p0, p1):
    return math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))


def wrap_deg(d):
    while d > 180.0:
        d -= 360.0
    while d < -180.0:
        d += 360.0
    return d


def segment_intents(waypoints):
    """从航点序列导出宏意图段列表 [(intent, n_steps)]。"""
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
    # run-length 压缩
    segs = []
    for lab in labels:
        if segs and segs[-1][0] == lab:
            segs[-1][1] += 1
        else:
            segs.append([lab, 1])
    return [(s[0], s[1]) for s in segs]


def _turn_or_forward(wps, idx, a, b):
    """有前一段方向参照时判转向,否则按直行处理。"""
    if idx == 0:
        return "FORWARD"
    prev = wps[idx - 1]
    ref = heading(prev, a)
    dpsi = wrap_deg(heading(a, b) - ref)
    if dpsi >= TURN_DEG:
        return "TURN_LEFT"
    if dpsi <= -TURN_DEG:
        return "TURN_RIGHT"
    return "FORWARD"


def build_record(split, item, idx):
    wp = [tuple(p) for p in item["pos"]]
    segs = segment_intents(wp)
    if not segs:
        return None
    return {
        "id": f"{split}-{idx:06d}",
        "split": split,
        "image_path": item["image_path"],
        "instruction": item["gpt_instruction"],
        "n_waypoints": len(wp),
        "gt_intent_segments": segs,               # [(intent, n_steps)]
        "gt_intent_seq": [s[0] for s in segs],    # 逐段标签
    }


def stratified_pick(items, k):
    """按环境分层抽 k 条,每环境 ≥1。"""
    rng = random.Random(SEED)
    by_env = {}
    for i, it in enumerate(items):
        by_env.setdefault(it["image_path"].split("/")[0], []).append(i)
    picked = []
    envs = sorted(by_env)
    quota = {e: max(1, round(len(by_env[e]) / len(items) * k)) for e in envs}
    while sum(quota.values()) > k:
        e = max(quota, key=lambda x: quota[x])
        if quota[e] <= 1:
            break
        quota[e] -= 1
    for e in envs:
        idxs = by_env[e][:]
        rng.shuffle(idxs)
        picked.extend(idxs[:quota[e]])
    return sorted(picked)[:k]


def main():
    records = []
    for split, k in BUDGET.items():
        items = json.loads((ANN / f"{split}.json").read_text(encoding="utf-8"))
        for i in stratified_pick(items, k):
            r = build_record(split, items[i], i)
            if r:
                records.append(r)
        print(f"{split}: {k} budget -> {sum(1 for r in records if r['split']==split)} usable")

    # 分布统计
    from collections import Counter
    seg_c = Counter(s for r in records for s in r["gt_intent_seq"])
    n_seg = sum(seg_c.values())
    stats = {
        "n_episodes": len(records),
        "n_segments": n_seg,
        "segments_per_intent": dict(seg_c),
        "segment_share": {k: round(v / n_seg, 4) for k, v in seg_c.items()},
        "seed": SEED, "budget": BUDGET,
        "rules": {"turn_deg": TURN_DEG, "climb_m": CLIMB_M, "hover_m": HOVER_M},
    }
    out = {"meta": stats, "episodes": records}
    (OUT_DIR / "intent_eval_set.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"episodes={len(records)} segments={n_seg}")
    print("share:", stats["segment_share"])
    print("->", OUT_DIR / "intent_eval_set.json")


if __name__ == "__main__":
    main()
