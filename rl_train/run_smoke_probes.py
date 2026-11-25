# -*- coding: utf-8 -*-
"""
run_smoke_probes.py
对 heli_landing 和 heli_forward 进行 30,000 步 PPO 冒烟探测：
1. 验证模型在二代机型衍生科目上的训练启动稳定性
2. 检验单步奖励范围与有限性 (出口消毒)
3. 统计存活率、回合奖励分布与任务特征指标
4. 产出探测结果报告 JSON
"""
import os
import sys
import time
import math
import json
import numpy as np

# Force UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from stable_baselines3.common.monitor import Monitor

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from helispike_env import make_helispike_env
from train_jsbsim import init_from_model

def make_probe_env(task, seed):
    def _init():
        env = make_helispike_env(task=task, action_mode="direct", wind=True, seed=seed)
        return Monitor(env)
    return _init

def run_task_probe(task_name, init_model_path=None, total_steps=30000, n_envs=2):
    print("=" * 65)
    print(f"  [Smoke Probe 30k] Task: {task_name} | Steps: {total_steps} | Envs: {n_envs}")
    print("=" * 65)

    env_fns = [make_probe_env(task_name, seed=1000 + i) for i in range(n_envs)]
    vec_env = DummyVecEnv(env_fns)

    model = PPO(
        "MlpPolicy",
        vec_env,
        learning_rate=3e-4,
        n_steps=1024,
        batch_size=128,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.005,
        vf_coef=0.5,
        max_grad_norm=0.5,
        verbose=1,
        seed=42,
        device="cpu",
    )

    if init_model_path and os.path.exists(init_model_path):
        init_from_model(model, init_model_path)
    else:
        print(f"[Notice] Training from scratch (init model {init_model_path} not found)")

    t0 = time.time()
    model.learn(total_timesteps=total_steps)
    t1 = time.time()
    vec_env.close()

    duration = t1 - t0
    sps = total_steps / max(1e-3, duration)
    print(f"\n[Probe Complete] {task_name}: {total_steps} steps in {duration:.1f}s ({sps:.1f} sps)")

    # 评估 10 回合获取详细指标
    print(f"\n[Evaluating Post-Probe Policy: {task_name} (10 episodes)]...")
    eval_env_fn = make_probe_env(task_name, seed=8888)
    eval_env = eval_env_fn()

    successes = []
    crashes = []
    step_rewards = []
    ep_rewards = []
    ep_lengths = []
    extra_metrics = []

    for ep in range(10):
        obs, info = eval_env.reset(seed=5000 + ep * 10)
        done = False
        ep_rew = 0.0
        step_count = 0

        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, rew, terminated, truncated, info = eval_env.step(action)
            done = terminated or truncated
            step_rewards.append(float(rew))
            ep_rew += float(rew)
            step_count += 1

        successes.append(float(info.get("success", False)))
        crashes.append(float(info.get("crashed", False)))
        ep_rewards.append(ep_rew)
        ep_lengths.append(step_count)
        if task_name == "heli_landing":
            extra_metrics.append({
                "touchdown_vs": info.get("touchdown_vs"),
                "landed": info.get("landed", False),
                "net_drift_m": info.get("net_drift_m", 0.0),
            })
        elif task_name == "heli_forward":
            extra_metrics.append({
                "mean_speed_err": info.get("mean_alt_err", 0.0),
                "alt_err": info.get("alt_err", 0.0),
            })

    eval_env.close()

    step_rew_min = float(np.min(step_rewards)) if step_rewards else 0.0
    step_rew_max = float(np.max(step_rewards)) if step_rewards else 0.0
    step_rew_mean = float(np.mean(step_rewards)) if step_rewards else 0.0

    success_rate = float(np.mean(successes))
    crash_rate = float(np.mean(crashes))
    survival_rate = 1.0 - crash_rate

    print(f"  Results for {task_name}:")
    print(f"    Survival Rate: {survival_rate*100:.1f}% | Crash Rate: {crash_rate*100:.1f}% | Success Rate: {success_rate*100:.1f}%")
    print(f"    Step Reward: min={step_rew_min:.2f}, max={step_rew_max:.2f}, mean={step_rew_mean:.3f}")
    print(f"    Mean Episode Length: {np.mean(ep_lengths):.1f} steps")
    print(f"    Mean Episode Reward: {np.mean(ep_rewards):.2f}")

    probe_summary = {
        "task": task_name,
        "steps": total_steps,
        "train_seconds": round(duration, 1),
        "steps_per_second": round(sps, 1),
        "survival_rate": survival_rate,
        "crash_rate": crash_rate,
        "success_rate": success_rate,
        "step_reward_min": round(step_rew_min, 3),
        "step_reward_max": round(step_rew_max, 3),
        "step_reward_mean": round(step_rew_mean, 3),
        "ep_reward_mean": round(float(np.mean(ep_rewards)), 2),
        "ep_len_mean": round(float(np.mean(ep_lengths)), 1),
        "extra_metrics": extra_metrics,
    }
    return probe_summary

def main():
    init_model = os.path.join(script_dir, "results", "checkpoints", "jsbsim_ppo_heli_hover", "jsbsim_ppo_heli_hover_300000_steps.zip")
    results = {}

    for task in ["heli_landing", "heli_forward"]:
        results[task] = run_task_probe(task, init_model_path=init_model, total_steps=30000, n_envs=2)

    out_path = os.path.join(script_dir, "results", "heli_smoke_probes_30k.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[All Probes Complete] Summary written to {out_path}")

if __name__ == "__main__":
    main()
