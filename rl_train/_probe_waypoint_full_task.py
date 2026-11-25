# -*- coding: utf-8 -*-
"""
_probe_waypoint_full_task.py — waypoint 全任务口径评估 (课程阶段强制 course_phase=2)

背景: WaypointTrackTask 的课程阶段绑定环境回合计数器 (_wp_ep_counter // 500),
任何新建评估 env 的前 500 回合都跑在单点阶段 (course_phase=0, 单航点 60m/半径15m),
train_jsbsim.py 内置 G2 评估测的是"单点到达率"而非 3 航点全任务成功率。
v4.1 的 13% 即单点口径。

本脚本构造 course_phase_eps=1 的评估 env (第 2 回合起即三点完整任务; 首回合为
单点热身, 丢弃), 按标准 G2 协议 (5 种子 × 20 回合, deterministic) 评估全任务口径:
success = 3 航点全部到达 (完整 80m L 形, 到点半径 10m) 且不坠毁。

用法:
  python _probe_waypoint_full_task.py --model results/jsbsim_ppo_waypoint_vXXX.zip
输出: 控制台汇总 + results/waypoint_fulltask_eval_<ts>.json
"""
import argparse
import json
import os
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EVAL_SEEDS = [42, 123, 7, 0, 99]
EPISODES_PER_SEED = 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--episodes", type=int, default=EPISODES_PER_SEED)
    args = ap.parse_args()

    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        from stable_baselines3 import PPO
        from jsbsim_tasks import WaypointTrackTask

        model = PPO.load(args.model, device="cpu")

        def make_eval_env():
            env = WaypointTrackTask(episode_steps=3500)
            env.course_phase_eps = 1   # 强制: 第 2 回合起即三点全任务
            env._wp_ep_counter = 1     # 从三点阶段开始 (首回合即 course_phase=2)
            try:
                env.attach_hover_policy(PPO.load(
                    os.path.join(SCRIPT_DIR, "results", "jsbsim_ppo_direct.zip"), device="cpu"))
            except Exception:
                pass
            return env

    seed_results = {}
    all_succ, all_crash, all_miss = [], [], []
    for seed in EVAL_SEEDS:
        env = make_eval_env()
        succ = crash = 0
        misses = []
        for ep in range(args.episodes):
            if ep == 0:
                # 首回合计为课程热身 (course_phase 转换), 结果丢弃
                obs, info = env.reset(seed=seed + ep * 100)
                done = False
                while not done:
                    act, _ = model.predict(obs, deterministic=True)
                    obs, r, term, trunc, info = env.step(act)
                    done = term or trunc
                continue
            obs, info = env.reset(seed=seed + ep * 100)
            done = False
            while not done:
                act, _ = model.predict(obs, deterministic=True)
                obs, r, term, trunc, info = env.step(act)
                done = term or trunc
            succ += bool(info.get("success"))
            crash += bool(info.get("crashed"))
            misses.append(float(info.get("mean_err", -1)))
        n = args.episodes - 1
        seed_results[f"seed_{seed}"] = {
            "success_rate": round(succ / n, 4),
            "crash_rate": round(crash / n, 4),
            "mean_wp_miss_m": round(float(np.mean(misses)), 2) if misses else None,
            "episodes": n,
        }
        all_succ.append(succ / n)
        all_crash.append(crash / n)
        print(f"  Seed {seed:4d}: Success = {succ/n*100:5.1f}%, Crash = {crash/n*100:4.1f}%, "
              f"MeanMiss = {np.mean(misses):7.1f} m")

    overall = {
        "model": args.model,
        "criterion": "FULL-TASK (course_phase=2 forced): 3 waypoints (80m L-shape, 10m radius) all reached, no crash",
        "episodes_per_seed": args.episodes - 1,
        "overall_success_rate": round(float(np.mean(all_succ)), 4),
        "overall_crash_rate": round(float(np.mean(all_crash)), 4),
        "acceptance_passed": bool(np.mean(all_succ) >= 0.85 and np.mean(all_crash) == 0.0),
        "seed_results": seed_results,
        "verified_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    print("\n[Full-Task Result] success=%.1f%% crash=%.1f%% acceptance=%s"
          % (overall["overall_success_rate"] * 100, overall["overall_crash_rate"] * 100,
             "PASS" if overall["acceptance_passed"] else "FAIL"))

    out = os.path.join(SCRIPT_DIR, "results",
                       "waypoint_fulltask_eval_%s.json" % time.strftime("%Y%m%dT%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(overall, f, ensure_ascii=False, indent=2)
    print("证据已落盘: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
