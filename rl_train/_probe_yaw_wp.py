# -*- coding: utf-8 -*-
"""yaw/waypoint v3 设计诊断探针 (reward 改写前的物理事实采集):
  A) YawManeuverTask: 脚蹬幅值扫描 -> 偏航速率/滚转耦合/坠毁边界
  B) WaypointTrackTask: 基类 SAS 下 u_pitch 扫描 -> 能否维持前飞(速度/俯仰/高度)
  C) 两任务零动作基线的每步收益 (idle 账)
"""
import sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
import io, contextlib
import math
import numpy as np

with contextlib.redirect_stdout(io.StringIO()):
    from jsbsim_tasks import YawManeuverTask, WaypointTrackTask

def run_scripted(env, action_fn, seed=7, label=""):
    obs, info = env.reset(seed=seed)
    hov_ok = info.get("hover_established")
    rows = []
    crashed = False
    while True:
        a = action_fn(env)
        obs, r, term, trunc, info = env.step(a)
        s = env._raw()
        rows.append((env.steps, r, s))
        if term or trunc:
            crashed = bool(info.get("crashed", False))
            break
    n = len(rows)
    print(f"--- {label} (hover_ok={hov_ok}, steps={n}, crashed={crashed})")
    for frac in (0.1, 0.25, 0.5, 0.75, 0.95):
        i = min(n - 1, int(n * frac))
        t, r, s = rows[i]
        print("   t=%5.1fs r/step=%7.3f agl=%6.1fm u=%6.2fkt psi_err=%6.1fdeg pitch=%6.1fdeg roll=%6.1fdeg r=%7.1fdps"
              % (t * 0.02, r, s['agl'], s['u_kts'],
                 math.degrees(abs(math.atan2(math.sin(env.target_psi - s['psi']), math.cos(env.target_psi - s['psi'])))) if hasattr(env, 'target_psi') else float('nan'),
                 math.degrees(s['pitch']), math.degrees(s['roll']), math.degrees(s['r'])))
    total_r = sum(x[1] for x in rows)
    print("   episode total reward = %8.1f" % total_r)
    return rows

print("=" * 72)
print("A) YAW: scripted pedal sweep (base SAS + trim integrator, 30s episodes)")
print("=" * 72)
for uy in (0.0, 0.3, 0.6, 1.0):
    env = YawManeuverTask()
    run_scripted(env, lambda e, uy=uy: np.array([0.0, 0.0, 0.0, uy]), seed=7,
                 label=f"YAW u_yaw=+{uy} (target heading set at reset)")

print("=" * 72)
print("B) WAYPOINT: u_pitch sweep under BASE SAS (can it sustain forward flight?)")
print("=" * 72)
for up in (0.0, 0.2, 0.4, 0.6, 0.8):
    env = WaypointTrackTask()
    run_scripted(env, lambda e, up=up: np.array([0.0, up, 0.0, 0.0]), seed=7,
                 label=f"WP u_pitch=+{up} (nose-down cmd), u_coll=0")

print("=" * 72)
print("B2) WAYPOINT: u_pitch + coll feedforward (level fwd flight attempt)")
print("=" * 72)
for up, uc in ((0.4, 0.3), (0.6, 0.5), (0.8, 0.7)):
    env = WaypointTrackTask()
    run_scripted(env, lambda e, up=up, uc=uc: np.array([uc, up, 0.0, 0.0]), seed=7,
                 label=f"WP u_pitch=+{up} u_coll=+{uc}")

print("=" * 72)
print("C) zero-action idle baseline")
print("=" * 72)
env = YawManeuverTask(); run_scripted(env, lambda e: np.zeros(4), seed=7, label="YAW zero-action")
env = WaypointTrackTask(); run_scripted(env, lambda e: np.zeros(4), seed=7, label="WP zero-action")
print("done.")
