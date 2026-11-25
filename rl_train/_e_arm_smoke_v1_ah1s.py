# -*- coding: utf-8 -*-
"""D1 审计脚本 v1: 一代 JSBSimHeloEnv (AH-1S) action_mode='arm' 链路冒烟 + 审计写入。

与 _e_arm_gen1.py 完全相同的物理冒烟 (120 步零动作),但额外把结果写
入 rl_train/results/arm_smoke_gen1_ah1s.json,结构化记录最终状态以
供后续 D1 进度对比/D1→D2 移植的基线参考。

不动 train_jsbsim.py / helispike_env.py (那是主 E 的活)。
"""
import json
import os
import sys
import warnings
import datetime
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from jsbsim_helo_env import JSBSimHeloEnv

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
JSON_PATH = os.path.join(RESULTS_DIR, "arm_smoke_gen1_ah1s.json")
os.makedirs(RESULTS_DIR, exist_ok=True)


def _safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None


def run_smoke(ep_steps=120, seed=42):
    env = JSBSimHeloEnv(action_mode='arm', wind=False, target_range=(15.0, 15.0))
    info_action_space = str(env.action_space)
    obs_shape = int(np.prod(env.observation_space.shape))
    obs, info = env.reset(seed=seed)
    final_agl = None
    final_vz = None
    crashed_step = None
    crashed_flag = False
    rpm_final = None
    last_info = {}
    for i in range(ep_steps):
        obs, r, te, tr, inf = env.step(np.zeros(7))
        last_info = inf
        if te or tr:
            crashed_step = i + 1
            crashed_flag = bool(inf.get("crashed", False))
            break
    env.close()

    final_agl = _safe_float(last_info.get("agl"))
    final_vz = _safe_float(last_info.get("vz"))
    rpm_raw = last_info.get("rpm")
    rpm_final = _safe_float(rpm_raw) if rpm_raw is not None else None
    return {
        "action_space": info_action_space,
        "obs_shape": obs_shape,
        "crashed_step": crashed_step,
        "crashed": crashed_flag,
        "final_agl_m": final_agl,
        "final_vz_mps": final_vz,
        "rpm_final": rpm_final,
    }


def main():
    print("--- arm_smoke_gen1_ah1s (v1 audit capture) ---")
    res = run_smoke(ep_steps=120, seed=42)
    print(f"  action_space = {res['action_space']}")
    print(f"  obs_shape    = {res['obs_shape']}")
    print(f"  crashed      = {res['crashed']}  (at step {res['crashed_step']})")
    print(f"  final_agl_m  = {res['final_agl_m']}")
    print(f"  final_vz_mps = {res['final_vz_mps']}")
    print(f"  rpm_final    = {res['rpm_final']}")

    ended = res["crashed_step"]
    verdict = "arm_mode_path_boots_and_step_OK" if (
        not res["crashed"] and ended is None
        and res["final_agl_m"] is not None and res["final_vz_mps"] is not None
    ) else "arm_mode_path_failed"

    record = {
        "test": "arm_smoke_gen1_ah1s",
        "timestamp_iso": datetime.datetime.now().isoformat(timespec="seconds"),
        "aircraft": "ah1s",
        "env_class": "JSBSimHeloEnv",
        "action_mode": "arm",
        "task": "heli_hover",
        "ep_steps": 120,
        "ended_at_step": ended,
        "final_agl_m": res["final_agl_m"],
        "final_vz_mps": res["final_vz_mps"],
        "crashed": bool(res["crashed"]),
        "rpm_final": res["rpm_final"],
        "verdict": verdict,
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"  wrote {JSON_PATH}")
    print(f"  verdict = {verdict}")


if __name__ == "__main__":
    main()