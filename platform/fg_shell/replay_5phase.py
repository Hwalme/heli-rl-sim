#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replay_5phase.py — E 终审路径（2026-09-02）：5 段 G2 PASS 轨迹回放，
playback.py 用 UDP 推给 FG (fdm=null)，全程白天 1280x800 录屏。

5 段顺序：takeoff_hard → hover(direct) → forward → roll → landing
E 终审附录：takeoff/hover/forward/roll/landing 四段一 roll
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FG_SHELL = ROOT / "platform" / "fg_shell"
TRAJ = ROOT / "rl_train" / "results"

# 5 段 G2 PASS / G4 PASS 轨迹，挑定 success=True
SEGMENTS = [
    {"name": "1-takeoff_hard", "file": TRAJ / "jsbsim_ppo_takeoff_hard_v20260830T091717_trajectory.json",
     "seed": 42, "ep": 0, "loop": 1, "rate": 30, "note": "G4 PASS 15/15 seed_42 ep0 7.5s"},
    {"name": "2-hover", "file": TRAJ / "jsbsim_ppo_direct_trajectory.json",
     "seed": 42, "ep": 1, "loop": 1, "rate": 30, "note": "G4 PASS seed_42 ep1 12s success"},
    {"name": "3-forward", "file": TRAJ / "jsbsim_ppo_forward_v3_trajectory.json",
     "seed": 42, "ep": 1, "loop": 1, "rate": 30, "note": "前飞 seed_42 ep1 20s success"},
    {"name": "4-roll", "file": TRAJ / "jsbsim_ppo_roll_trajectory.json",
     "seed": 42, "ep": 0, "loop": 1, "rate": 30, "note": "滚转 seed_42 ep0 8s success"},
    {"name": "5-landing", "file": TRAJ / "jsbsim_ppo_landing_250k_g2verify_trajectory.json",
     "seed": 42, "ep": 0, "loop": 1, "rate": 30, "note": "G2 verify seed_42 ep0 9.5s success"},
]


def run_one(seg):
    cmd = [sys.executable, str(FG_SHELL / "playback.py"),
           "--file", str(seg["file"]),
           "--seed", str(seg["seed"]),
           "--episode-index", str(seg["ep"]),
           "--rate", str(seg["rate"]),
           "--loop", str(seg["loop"]),
           "--host", "127.0.0.1",
           "--port", "5500"]
    print(f"[replay] {seg['name']}  file={seg['file'].name}  seed=seed_{seg['seed']} ep={seg['ep']}  ({seg['note']})")
    t0 = time.time()
    rc = subprocess.run(cmd, cwd=str(FG_SHELL)).returncode
    dur = time.time() - t0
    print(f"  exit={rc} dur={dur:.1f}s")
    return rc


def main():
    print("=" * 70)
    print("replay_5phase — E 终审演示路径 (B6 2026-09-02)")
    print("=" * 70)
    total_t0 = time.time()
    for i, seg in enumerate(SEGMENTS):
        rc = run_one(seg)
        if rc != 0:
            print(f"[replay] {seg['name']} 失败 rc={rc}")
        # 段间留 1s 让 FG 收敛
        if i < len(SEGMENTS) - 1:
            time.sleep(1.0)
    print(f"[replay] 全 5 段完成 总耗时 {time.time()-total_t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())