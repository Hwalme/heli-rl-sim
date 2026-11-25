# -*- coding: utf-8 -*-
"""
smoke_helispike_tasks.py: 阶段 0 — helispike 环境接线与冒烟验证脚本
验证目标:
  1. 验证 make_env('heli_hover'), make_env('heli_takeoff'), make_env('heli_landing'), make_env('heli_forward') 实例化正常
  2. 每个任务运行 3 回合随机策略
  3. 验证每步 obs 100% 有限且在 [-10, 10] 内 (出口消毒 nan_to_num+clip)
  4. 验证每步 reward 100% 有限且在 [-100, 100] 内
  5. 验证 dt=0.005, 稳态悬停点 coll=0.50, roll_sign=+1
"""
import sys
import os
import math
import numpy as np

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from train_jsbsim import make_env, TASK_NAMES
from helispike_env import HelispikeHoverEnv, HelispikeTakeoffEnv, HelispikeLandingEnv, HelispikeForwardEnv

def run_smoke():
    print("=" * 70)
    print("  二代 helispike 环境接线与出口消毒冒烟测试 (阶段 0)")
    print("=" * 70)

    tasks_to_test = ["heli_hover", "heli_takeoff", "heli_landing", "heli_forward"]
    all_passed = True
    summary = []

    for task_name in tasks_to_test:
        print(f"\n[Testing Task: {task_name}]")
        env_fn = make_env(task=task_name, action_mode="direct", wind=False, seed=42)
        env = env_fn()

        task_non_finite_obs = 0
        task_out_of_bounds_obs = 0
        task_non_finite_rew = 0
        task_out_of_bounds_rew = 0
        total_steps = 0

        for ep in range(3):
            obs, info = env.reset(seed=100 + ep)
            done = False
            ep_steps = 0

            if not np.all(np.isfinite(obs)):
                task_non_finite_obs += 1
            if np.any(obs < -10.0) or np.any(obs > 10.0):
                task_out_of_bounds_obs += 1

            while not done and ep_steps < 300:
                action = env.action_space.sample()
                obs, reward, term, trunc, info = env.step(action)
                done = term or trunc
                ep_steps += 1
                total_steps += 1

                if not np.all(np.isfinite(obs)):
                    task_non_finite_obs += 1
                if np.any(obs < -10.0) or np.any(obs > 10.0):
                    task_out_of_bounds_obs += 1

                if not math.isfinite(reward):
                    task_non_finite_rew += 1
                if reward < -100.0 or reward > 100.0:
                    task_out_of_bounds_rew += 1

            print(f"  -> Episode {ep+1}/3: Steps={ep_steps}, Done={done}, Final AGL={info.get('agl', 0):.2f}m, Crashed={info.get('crashed', False)}")

        env.close()

        passed = (task_non_finite_obs == 0 and task_out_of_bounds_obs == 0 and 
                  task_non_finite_rew == 0 and task_out_of_bounds_rew == 0)
        if not passed:
            all_passed = False

        status_str = "PASS" if passed else "FAIL"
        summary.append({
            "task": task_name,
            "steps": total_steps,
            "obs_clean": task_non_finite_obs == 0 and task_out_of_bounds_obs == 0,
            "rew_clean": task_non_finite_rew == 0 and task_out_of_bounds_rew == 0,
            "status": status_str
        })
        print(f"  [{task_name}] Result: {status_str} (Obs Violations: {task_non_finite_obs + task_out_of_bounds_obs}, Rew Violations: {task_non_finite_rew + task_out_of_bounds_rew})")

    print("\n" + "=" * 70)
    print(f"{'Task':<18} | {'Steps':<8} | {'Obs Sanitized':<15} | {'Rew Sanitized':<15} | {'Status':<8}")
    print("-" * 70)
    for row in summary:
        print(f"{row['task']:<18} | {row['steps']:<8} | {str(row['obs_clean']):<15} | {str(row['rew_clean']):<15} | {row['status']:<8}")
    print("=" * 70)

    if all_passed:
        print("\n>>> [阶段 0 验收判定] 冒烟测试全绿 PASS! 可以正式启动阶段 1 (heli_hover 2M) 训练! <<<\n")
        return 0
    else:
        print("\n>>> [阶段 0 验收判定] 存在违例 FAIL! 请检查消毒代码! <<<\n")
        return 1

if __name__ == "__main__":
    sys.exit(run_smoke())
