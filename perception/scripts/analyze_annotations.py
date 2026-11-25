# -*- coding: utf-8 -*-
"""C1 · Step3a: OpenFly 标注全量统计 + 可视化(纯本地,不联网)。

输入 : perception/data/Annotation/{train,seen,unseen}.json
输出 : perception/analysis/data_stats.json            全量统计表
       perception/analysis/fig_env_dist.png           环境分布柱状图
       perception/analysis/fig_action_dist.png        动作类型分布
       perception/analysis/fig_instr_len.png          指令词数分布
       perception/analysis/fig_traj_len_vs_height.png 轨迹长度/高度分布
"""
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ANN = DATA / "Annotation"
OUT = ROOT / "analysis"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
plt.rcParams["axes.unicode_minus"] = False


def load(name):
    p = ANN / f"{name}.json"
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def env_of(item):
    """image_path = <env>/<scenario>/<traj_id>,取第一段为环境。"""
    return item["image_path"].split("/")[0]


def scenario_of(item):
    parts = item["image_path"].split("/")
    return parts[1] if len(parts) >= 3 else "?"


def analyze_split(name, items, budget):
    n = len(items)
    env_c = Counter(env_of(i) for i in items)
    scen_c = Counter(f"{env_of(i)}/{scenario_of(i)}" for i in items)
    act_c = Counter()
    traj_steps, instr_words, heights, euclid = [], [], [], []
    for it in items:
        act_c.update(it.get("action", []))
        traj_steps.append(len(it.get("action", [])))
        instr_words.append(len(it.get("gpt_instruction", "").split()))
        pos = it.get("pos") or []
        zs = [p[2] for p in pos]
        if zs:
            heights.append(max(zs) - min(zs))
        if len(pos) >= 2:
            euclid.append(math.dist(pos[0], pos[-1]))
    return {
        "split": name,
        "n_samples": n,
        "n_envs": len(env_c),
        "env_dist": dict(env_c.most_common()),
        "scenario_dist": dict(scen_c.most_common()),
        "action_dist": {str(k): v for k, v in sorted(act_c.items())},
        "traj_steps": {"mean": sum(traj_steps)/n, "p50": sorted(traj_steps)[n//2],
                       "min": min(traj_steps), "max": max(traj_steps)},
        "instr_words": {"mean": sum(instr_words)/n, "p50": sorted(instr_words)[n//2],
                        "min": min(instr_words), "max": max(instr_words)},
        "height_span_m": {"mean": sum(heights)/len(heights)} if heights else None,
        "euclid_start_goal_m": {"mean": sum(euclid)/len(euclid),
                                "max": max(euclid)} if euclid else None,
        "sample_budget_1pct": budget,
    }


def fig_bar(counter, title, fname, xlabel, top=14):
    ks = list(counter)[:top]
    vs = [counter[k] for k in ks]
    plt.figure(figsize=(9, 4.2))
    plt.bar(range(len(vs)), vs, color="#3465a4")
    plt.xticks(range(len(vs)), ks, rotation=30, ha="right", fontsize=8)
    plt.ylabel("样本数")
    plt.title(title)
    plt.xlabel(xlabel)
    plt.tight_layout()
    plt.savefig(OUT / fname, dpi=140)
    plt.close()


def main():
    random.seed(42)
    splits = {}
    for name in ("train", "seen", "unseen"):
        print(f"loading {name}.json ...", flush=True)
        splits[name] = load(name)
        print(f"  {len(splits[name])} samples", flush=True)

    total = sum(len(v) for v in splits.values())
    stats = {
        "dataset": "IPEC-COMMUNITY/OpenFly",
        "endpoint": "hf-mirror.com",
        "generated": "2026-08-28",
        "total_samples": total,
        "splits": [analyze_split(k, v, max(1, round(len(v) * 0.01))) for k, v in splits.items()],
    }
    (OUT / "data_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print("stats ->", OUT / "data_stats.json")

    # 可视化 1: 环境分布(train split)
    tr = stats["splits"][0]["env_dist"]
    fig_bar(tr, f"OpenFly train split 环境分布 (n={stats['splits'][0]['n_samples']:,})",
            "fig_env_dist.png", "环境")
    # 可视化 2: 动作类型分布(合并三 split)
    merged = Counter()
    for s in stats["splits"]:
        merged.update(Counter(s["action_dist"]))
    fig_bar(merged, "离散动作 ID 分布 (action 字段, 合并全部 split)",
            "fig_action_dist.png", "动作 ID")
    # 可视化 3: 指令词数
    plt.figure(figsize=(7, 4))
    for name, items in splits.items():
        ws = [len(i.get("gpt_instruction", "").split()) for i in items]
        plt.hist(ws, bins=50, alpha=0.5, label=f"{name} (n={len(ws)})")
    plt.xlabel("指令词数"); plt.ylabel("样本数"); plt.legend()
    plt.title("语言指令长度分布")
    plt.tight_layout(); plt.savefig(OUT / "fig_instr_len.png", dpi=140); plt.close()
    # 可视化 4: 轨迹欧氏距离 vs 高度跨度(seen split 抽 800 条画散点)
    plt.figure(figsize=(7, 4.5))
    pts = []
    for it in splits["seen"]:
        pos = it.get("pos") or []
        if len(pos) >= 2:
            pts.append((math.dist(pos[0], pos[-1]), max(p[2] for p in pos) - min(p[2] for p in pos)))
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    plt.scatter(xs, ys, s=6, alpha=0.4, color="#c0392b")
    plt.xlabel("起点→终点直线距离 (m)"); plt.ylabel("高度跨度 (m)")
    plt.title(f"seen split 轨迹几何 (n={len(pts)})")
    plt.tight_layout(); plt.savefig(OUT / "fig_traj_len_vs_height.png", dpi=140); plt.close()
    print("figures ->", OUT)
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
