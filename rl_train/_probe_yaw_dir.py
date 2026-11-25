# -*- coding: utf-8 -*-
"""yaw 方向可行性探针: 左转/右转/先左加载trim再右转, 记录 r(t) 与 psi(t)"""
import sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
import io, contextlib, math
import numpy as np
with contextlib.redirect_stdout(io.StringIO()):
    from jsbsim_tasks import YawManeuverTask

def run(seq, seed=7, label=""):
    env = YawManeuverTask()
    with contextlib.redirect_stdout(io.StringIO()):
        obs, info = env.reset(seed=seed)
    t0 = env.target_psi - env.psi0
    print("--- %s | target_hdg_change=%+7.1f deg" % (label, math.degrees(t0)))
    n = 0
    crashed = False
    while True:
        u_yaw = seq(min(n * 0.02, 29.9))
        obs, r, term, trunc, info = env.step(np.array([0.0, 0.0, 0.0, u_yaw]))
        n += 1
        if n % 250 == 0:
            s = env._raw()
            print("   t=%5.1fs u_yaw=%+4.1f r=%7.1fdps psi=%8.1fdeg hdg_err=%7.1fdeg roll=%6.1fdeg agl=%5.1fm trim=%+0.3f"
                  % (n * 0.02, u_yaw, math.degrees(s['r']), math.degrees(s['psi']),
                     math.degrees(abs(math.atan2(math.sin(env.target_psi - s['psi']), math.cos(env.target_psi - s['psi'])))),
                     math.degrees(s['roll']), s['agl'], env._rud_trim))
        if term or trunc:
            crashed = bool(info.get("crashed", False))
            s = env._raw()
            print("   END t=%.0fs crashed=%s hdg_err=%.1fdeg agl=%.1fm" % (n*0.02, crashed,
                  math.degrees(abs(math.atan2(math.sin(env.target_psi - s['psi']), math.cos(env.target_psi - s['psi'])))), s['agl']))
            break

run(lambda t: +1.0, label="LEFT-spin hard (u_yaw=+1)")
run(lambda t: -1.0, label="RIGHT attempt hard (u_yaw=-1)")
def seq1(t): return -0.6 if t < 15 else 0.0
run(seq1, label="RIGHT via -0.6 then release")
def seq2(t): return -0.3 if t < 8 else -1.0
run(seq2, label="mild -0.3 8s then -1.0")
