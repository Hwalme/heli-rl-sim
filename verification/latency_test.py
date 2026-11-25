# -*- coding: utf-8 -*-
"""
latency_test.py: 低空具身智能端到端响应延迟实测脚本
"""
import os
import sys
import time
import json
import numpy as np
import jsbsim
from stable_baselines3 import PPO

# Force UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

script_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.abspath(os.path.join(script_dir, ".."))
sys.path.insert(0, os.path.join(project_dir, "rl_train"))

from jsbsim_helo_env import JSBSimHeloEnv


def benchmark_latency(n_samples=1000):
    print("=" * 65)
    print("  Low-Altitude Embodied Agent End-to-End Latency Benchmark")
    print(f"  Samples: {n_samples} closed-loop cycles")
    print("=" * 65)

    env = JSBSimHeloEnv(action_mode="direct")
    obs, info = env.reset(seed=42)

    model_path = os.path.join(project_dir, "rl_train", "results", "jsbsim_ppo_direct.zip")
    if os.path.exists(model_path):
        print(f"[Model] Loading trained weights from: {model_path}")
        model = PPO.load(model_path, device="cpu")
    else:
        print("[Model] Using standard PPO architecture")
        model = PPO("MlpPolicy", env, device="cpu")

    t_obs = []
    t_infer = []
    t_write = []
    t_step = []
    t_total = []

    # Warmup
    for _ in range(50):
        act, _ = model.predict(obs, deterministic=True)
        obs, r, term, trunc, _ = env.step(act)
        if term or trunc:
            obs, _ = env.reset()

    # Benchmark
    for i in range(n_samples):
        t0 = time.perf_counter_ns()

        t_s0 = time.perf_counter_ns()
        raw_s = env._get_raw_state()
        cur_obs = env._obs(raw_s)
        t_s1 = time.perf_counter_ns()

        t_i0 = time.perf_counter_ns()
        act, _ = model.predict(cur_obs, deterministic=True)
        t_i1 = time.perf_counter_ns()

        t_w0 = time.perf_counter_ns()
        env._apply_controls(float(act[0]), float(act[1]), float(act[2]), float(act[3]))
        t_w1 = time.perf_counter_ns()

        t_p0 = time.perf_counter_ns()
        env.fdm.run()
        t_p1 = time.perf_counter_ns()

        t1 = time.perf_counter_ns()

        t_obs.append((t_s1 - t_s0) / 1e6)
        t_infer.append((t_i1 - t_i0) / 1e6)
        t_write.append((t_w1 - t_w0) / 1e6)
        t_step.append((t_p1 - t_p0) / 1e6)
        t_total.append((t1 - t0) / 1e6)

    def stats(arr):
        return {
            "mean_ms": round(float(np.mean(arr)), 4),
            "p50_ms": round(float(np.percentile(arr, 50)), 4),
            "p95_ms": round(float(np.percentile(arr, 95)), 4),
            "p99_ms": round(float(np.percentile(arr, 99)), 4),
            "max_ms": round(float(np.max(arr)), 4),
        }

    results = {
        "benchmark_samples": n_samples,
        "procurement_requirement_target": "<= 200 ms",
        "breakdown": {
            "1_state_extraction": stats(t_obs),
            "2_policy_inference_cpu": stats(t_infer),
            "3_control_write": stats(t_write),
            "4_physics_step_jsbsim": stats(t_step),
            "total_end_to_end": stats(t_total),
        },
        "target_pass": bool(np.percentile(t_total, 99) <= 200.0),
        "verified_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    print("\n" + "=" * 65)
    print("  Latency Breakdown Table (Unit: ms)")
    print("=" * 65)
    print(f"  {'Stage':<24} | {'Mean':<8} | {'P50':<8} | {'P95':<8} | {'P99':<8}")
    print("-" * 65)
    for k, v in results["breakdown"].items():
        print(f"  {k:<24} | {v['mean_ms']:<8.3f} | {v['p50_ms']:<8.3f} | {v['p95_ms']:<8.3f} | {v['p99_ms']:<8.3f}")
    print("-" * 65)
    tot = results["breakdown"]["total_end_to_end"]
    print(f"  End-to-End P99 Latency: {tot['p99_ms']:.3f} ms (Target: <= 200 ms)")
    print(f"  Status: {'[PASS] Fully Compliant' if results['target_pass'] else '[FAIL]'}")
    print("=" * 65)

    res_path = os.path.join(script_dir, "latency_results.json")
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[Data Saved] Written to: {res_path}")

    return results


if __name__ == '__main__':
    benchmark_latency()
