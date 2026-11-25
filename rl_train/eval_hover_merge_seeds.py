# -*- coding: utf-8 -*-
"""
eval_hover_merge_seeds.py
补齐 hover（direct）门禁缺口：交接包要求 5 种子×20 回合（42/123/7/0/99），
原 jsbsim_ppo_direct_results.json 仅含 42/123/7（3 种子），缺 seed_0、seed_99。

本脚本：
  1. 加载已训模型 results/jsbsim_ppo_direct.zip（不重新训练，沿用原权重）
  2. 仅对 seed_0、seed_99 各跑 20 回合（记录前 3 回合逐帧轨迹，与现有轨迹格式一致）
  3. 合并进现有 results / trajectory 文件，**保留** seed_42/123/7，重算 5 种子总账
  4. 绝不覆盖已有 3 种子的成绩与轨迹

运行：python eval_hover_merge_seeds.py   （需系统 Python3.13 + gymnasium/JSBSim/SB3/torch）
"""
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import train_jsbsim as T  # 复用 evaluate_policy / make_env / PPO，保证环境配置一致

RESULTS = os.path.join(HERE, "results")
MODEL = os.path.join(RESULTS, "jsbsim_ppo_direct.zip")
RES_PATH = os.path.join(RESULTS, "jsbsim_ppo_direct_results.json")
TRAJ_PATH = os.path.join(RESULTS, "jsbsim_ppo_direct_trajectory.json")

NEW_SEEDS = [0, 99]


def main():
    print("=" * 60)
    print("  Hover 门禁补齐：补跑 seed_0 / seed_99（20 回合/种子）")
    print("=" * 60)

    print("[Model] 加载 %s ..." % MODEL)
    model = T.PPO.load(MODEL, device="cpu")
    env_fn = T.make_env(task="hover", action_mode="direct", wind=True, seed=999)

    seed_results = {}
    all_trajectories = {}
    for s in NEW_SEEDS:
        print("\n[Eval] seed=%d (20 回合, 记录前 3 回合轨迹) ..." % s)
        res = T.evaluate_policy(
            model, env_fn, n_episodes=20, seed=s,
            record_trajectory=True, max_recorded_episodes=3,
        )
        traj = res.pop("trajectory", []) if "trajectory" in res else []
        seed_results["seed_%d" % s] = res
        all_trajectories["seed_%d" % s] = traj
        print("  -> success=%5.1f%%  crash=%4.1f%%  altErr=%5.2f m  recorded_eps=%d"
              % (res["success_rate"] * 100, res["crash_rate"] * 100,
                 res["mean_alt_err_m"], len(traj)))

    # ---- 合并进现有 results.json（保留 42/123/7） ----
    with open(RES_PATH, encoding="utf-8") as f:
        rep = json.load(f)
    rep.setdefault("seed_results", {})
    rep["seed_results"].update(seed_results)

    present = sorted(rep["seed_results"].keys(),
                     key=lambda k: int(k.split("_")[1]))
    sr = [rep["seed_results"][k]["success_rate"] for k in present]
    cr = [rep["seed_results"][k]["crash_rate"] for k in present]
    ae = [rep["seed_results"][k]["mean_alt_err_m"] for k in present]
    rep["overall_success_rate"] = float(np.mean(sr))
    rep["overall_crash_rate"] = float(np.mean(cr))
    rep["overall_mean_alt_err_m"] = round(float(np.mean(ae)), 2)
    rep["acceptance_passed"] = bool(rep["overall_success_rate"] >= 0.85
                                    and rep["overall_crash_rate"] == 0.0)
    rep["verified_timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rep["seed_note"] = "merged seeds: %s" % present
    with open(RES_PATH, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print("\n[Results] 合并完成 -> %s" % RES_PATH)
    print("  现有种子: %s" % present)
    print("  5 种子总成功=%.1f%%  总坠毁=%.1f%%  平均高度误差=%.2f m"
          % (rep["overall_success_rate"] * 100, rep["overall_crash_rate"] * 100,
             rep["overall_mean_alt_err_m"]))
    print("  门禁(5种子×20回合, >=90%%内部/>=85%%验收, 0%%坠毁): %s"
          % ("PASS" if rep["acceptance_passed"] else "FAIL"))

    # ---- 合并进现有 trajectory.json（保留 42/123/7 轨迹） ----
    with open(TRAJ_PATH, encoding="utf-8") as f:
        traj = json.load(f)
    traj.setdefault("trajectories", {})
    traj["trajectories"].update(all_trajectories)
    existing = list(traj.get("seeds", []))
    for s in NEW_SEEDS:
        if s not in existing:
            existing.append(s)
    traj["seeds"] = existing
    # 同步整体统计（与 results 对齐；轨迹整体率基于完整 20 回合评估，非仅录制回合）
    traj["overall_success_rate"] = rep["overall_success_rate"]
    traj["overall_crash_rate"] = rep["overall_crash_rate"]
    traj["verified_timestamp"] = rep["verified_timestamp"]
    with open(TRAJ_PATH, "w", encoding="utf-8") as f:
        json.dump(traj, f, ensure_ascii=False, separators=(",", ":"))
    print("[Traj]   合并完成 -> %s" % TRAJ_PATH)
    print("  轨迹种子: %s" % existing)

    print("\n=DONE=")


if __name__ == "__main__":
    main()
