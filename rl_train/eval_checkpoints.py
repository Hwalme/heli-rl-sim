# -*- coding: utf-8 -*-
"""
eval_checkpoints.py — 检查点择优 (G2 收敛门的配套工具)

背景 (2026-08-30 run2 教训): 3M 步训练中奖励在 ~2M 处达峰 (ep_rew 298) 后
持续劣化到 169, 最终保存的末态模型只有 72% 成功率 —— 峰值策略被丢掉了。
本脚本对 results/checkpoints/<model>/ 下的快照逐个评估, 找出真正的最佳时刻。

两阶段:
  1) 粗筛: 每个检查点用 seed 42 x 20 回合, 按 (成功率, -坠毁率) 排序;
  2) 精评: 前 3 名用 5 种子 x 20 回合 (=与验收协议一致), 出最终排名。

用法:
  python eval_checkpoints.py results/checkpoints/jsbsim_ppo_takeoff_fg_v20260830T080721
  python eval_checkpoints.py <ckpt_dir> --top 3 --episodes 20
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stable_baselines3 import PPO

from train_jsbsim import make_env, evaluate_policy


def quick_eval(zip_path, task, mode, seed, n_episodes):
    model = PPO.load(zip_path, device="cpu")
    env_fn = make_env(task=task, action_mode=mode, wind=True, seed=999)
    res = evaluate_policy(model, env_fn, n_episodes=n_episodes, seed=seed)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ckpt_dir")
    ap.add_argument("--task", default=None, help="默认从目录名推断 takeoff_fg 等")
    ap.add_argument("--mode", default="direct")
    ap.add_argument("--top", type=int, default=3, help="精评名额")
    ap.add_argument("--episodes", type=int, default=20)
    args = ap.parse_args()

    ckpt_dir = os.path.abspath(args.ckpt_dir)
    base = os.path.basename(ckpt_dir.rstrip("/\\"))
    task = args.task
    if task is None:
        stem = base.replace("jsbsim_ppo_", "")
        task = "hover" if "direct" in stem else stem.split("_v")[0]
    zips = sorted(f for f in os.listdir(ckpt_dir) if f.endswith(".zip"))
    if not zips:
        print("no checkpoints found in", ckpt_dir)
        return 1
    print("=" * 78)
    print("检查点择优: %d 个快照 | task=%s mode=%s | 粗筛 seed42 x %d 回合" % (
        len(zips), task, args.mode, args.episodes))
    print("=" * 78)

    screened = []
    for f in zips:
        p = os.path.join(ckpt_dir, f)
        t0 = time.time()
        try:
            r = quick_eval(p, task, args.mode, seed=42, n_episodes=args.episodes)
        except Exception as e:
            print("  %-46s ERROR %s" % (f, e))
            continue
        screened.append((f, r))
        print("  %-46s succ=%5.1f%% crash=%4.1f%%  (%.0fs)" % (
            f, 100 * r["success_rate"], 100 * r["crash_rate"], time.time() - t0),
            flush=True)

    screened.sort(key=lambda x: (-x[1]["success_rate"], x[1]["crash_rate"]))
    finalists = screened[:args.top]
    print("\n精评前 %d 名 (5 种子 x %d 回合, 与验收协议一致):" % (len(finalists), args.episodes))
    final_rows = []
    for f, _ in finalists:
        p = os.path.join(ckpt_dir, f)
        seed_res, succs, crashes = {}, [], []
        for s in (42, 123, 7, 0, 99):
            r = quick_eval(p, task, args.mode, seed=s, n_episodes=args.episodes)
            seed_res["seed_%d" % s] = {"success_rate": r["success_rate"], "crash_rate": r["crash_rate"]}
            succs.append(r["success_rate"]); crashes.append(r["crash_rate"])
        row = {"checkpoint": f, "overall_success_rate": float(np.mean(succs)),
               "overall_crash_rate": float(np.mean(crashes)), "seed_results": seed_res}
        final_rows.append(row)
        print("  %-46s succ=%5.1f%% crash=%4.1f%%" % (
            f, 100 * row["overall_success_rate"], 100 * row["overall_crash_rate"]), flush=True)

    final_rows.sort(key=lambda x: (-x["overall_success_rate"], x["overall_crash_rate"]))
    best = final_rows[0] if final_rows else None
    out = {"ckpt_dir": ckpt_dir, "task": task, "mode": args.mode,
           "screen": [{"checkpoint": f, "success_rate": r["success_rate"],
                       "crash_rate": r["crash_rate"]} for f, r in screened],
           "final": final_rows,
           "best": best,
           "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
    res_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    fp = os.path.join(res_dir, "checkpoint_eval_%s_%s.json" % (base, time.strftime("%Y%m%dT%H%M%S")))
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("\nBEST:", best["checkpoint"] if best else None,
          "-> succ %.1f%% crash %.1f%%" % (100 * best["overall_success_rate"], 100 * best["overall_crash_rate"]) if best else "")
    print("证据已落盘:", fp)
    if best:
        print("\n[建议] 若 best 通过验收 (>=85%% 且 0 坠毁), 复制为金标准:\n"
              "  cp \"%s\" \"%s\"" % (os.path.join(ckpt_dir, best["checkpoint"]),
                                     os.path.join(res_dir, "jsbsim_ppo_gold_" + task + ".zip")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
