# -*- coding: utf-8 -*-
"""
independent_eval.py: 会话1 独立复验脚本

目的: 不信任任何已有报告数字，用已保存的 jsbsim_ppo_direct.zip 权重，
按与 train_jsbsim.py 完全相同的评测协议(5种子 x 20回合, deterministic, 有风,
目标高度 10~30m)独立重跑, 并记录逐回合明细, 供人工核查。

输出: verification/independent_eval_results.json
"""
import os
import sys
import time
import json
import math
import numpy as np

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
RL = os.path.abspath(os.path.join(HERE, "..", "rl_train"))
sys.path.insert(0, RL)

from stable_baselines3 import PPO
from jsbsim_helo_env import JSBSimHeloEnv

EVAL_SEEDS = [42, 123, 7, 0, 99]
N_EPISODES = 20
DT = 0.02
EP_STEPS = 1000


def run_episode(model, env, seed):
    obs, info = env.reset(seed=seed)
    done = False
    max_roll = 0.0
    max_pitch = 0.0
    steps = 0
    last_info = info
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        steps += 1
        max_roll = max(max_roll, abs(math.degrees(float(env.fdm.get_property_value('attitude/phi-rad')))))
        max_pitch = max(max_pitch, abs(math.degrees(float(env.fdm.get_property_value('attitude/theta-rad')))))
        last_info = info
    return {
        "eval_seed": seed,
        "success": bool(last_info.get("success", False)),
        "crashed": bool(last_info.get("crashed", False)),
        "mean_alt_err_m": round(float(last_info.get("mean_alt_err", -1)), 3),
        "final_agl_m": round(float(last_info.get("agl", -1)), 2),
        "target_alt_m": round(float(last_info.get("target_alt", -1)), 2),
        "max_roll_deg": round(max_roll, 1),
        "max_pitch_deg": round(max_pitch, 1),
        "steps": steps,
    }


def main():
    model_path = os.path.join(RL, "results", "jsbsim_ppo_direct.zip")
    model = PPO.load(model_path, device="cpu")
    print(f"[Model Loaded] {model_path}")

    env = JSBSimHeloEnv(action_mode="direct", wind=True, target_range=(10.0, 30.0))

    seed_results = {}
    episodes_all = []
    t_wall = time.time()

    for s in EVAL_SEEDS:
        eps = []
        for ep in range(N_EPISODES):
            rec = run_episode(model, env, seed=s + ep * 100)
            eps.append(rec)
            episodes_all.append(rec)
        sr = float(np.mean([e["success"] for e in eps]))
        cr = float(np.mean([e["crashed"] for e in eps]))
        ae = float(np.mean([e["mean_alt_err_m"] for e in eps]))
        seed_results[f"seed_{s}"] = {
            "success_rate": sr,
            "crash_rate": cr,
            "mean_alt_err_m": round(ae, 3),
            "episodes": N_EPISODES,
        }
        print(f"  Seed {s:4d}: Success={sr*100:5.1f}%  Crash={cr*100:4.1f}%  AltErr={ae:5.2f}m")

    overall_success = float(np.mean([e["success"] for e in episodes_all]))
    overall_crash = float(np.mean([e["crashed"] for e in episodes_all]))
    overall_ae = float(np.mean([e["mean_alt_err_m"] for e in episodes_all]))
    n_fail = sum(1 for e in episodes_all if not e["success"])

    # 全闭环(含奖励/观测/坠机判定)单步耗时, 与 latency_test.py 的分段口径互补
    obs, _ = env.reset(seed=7)
    for _ in range(50):
        act, _ = model.predict(obs, deterministic=True)
        obs, r, te, tr, _ = env.step(act)
        if te or tr:
            obs, _ = env.reset()
    t_lat = []
    for _ in range(1000):
        t0 = time.perf_counter_ns()
        act, _ = model.predict(obs, deterministic=True)
        obs, r, te, tr, _ = env.step(act)
        t_lat.append((time.perf_counter_ns() - t0) / 1e6)
        if te or tr:
            obs, _ = env.reset()

    results = {
        "verifier": "session-1 (independent re-run, zcode)",
        "model": model_path,
        "protocol": "identical to train_jsbsim.py evaluate_policy: 5 seeds x 20 episodes, deterministic, wind on, target 10-30m",
        "jsbsim_root": __import__('jsbsim').get_default_root_dir(),
        "episodes_total": len(episodes_all),
        "overall_success_rate": overall_success,
        "overall_crash_rate": overall_crash,
        "overall_mean_alt_err_m": round(overall_ae, 3),
        "failed_episodes": n_fail,
        "full_step_latency_ms": {
            "mean": round(float(np.mean(t_lat)), 4),
            "p99": round(float(np.percentile(t_lat, 99)), 4),
            "max": round(float(np.max(t_lat)), 4),
        },
        "wall_seconds": round(time.time() - t_wall, 1),
        "seed_results": seed_results,
        "episodes": episodes_all,
        "verified_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    out = os.path.join(HERE, "independent_eval_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"[INDEPENDENT RESULT] Success={overall_success*100:.1f}%  Crash={overall_crash*100:.1f}%  AltErr={overall_ae:.2f}m  Failed={n_fail}/{len(episodes_all)}")
    print(f"[Full-step latency] mean={results['full_step_latency_ms']['mean']:.3f}ms p99={results['full_step_latency_ms']['p99']:.3f}ms")
    print(f"[Saved] {out}")


if __name__ == '__main__':
    main()
