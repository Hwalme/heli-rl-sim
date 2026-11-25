# -*- coding: utf-8 -*-
"""yaw r3 快速验证: 配平收敛效果 + 双向转向权限 + reward 单步 sanity"""
import sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
import io, contextlib, math
import numpy as np
with contextlib.redirect_stdout(io.StringIO()):
    from jsbsim_tasks import YawManeuverTask
    import train_jsbsim as tj
for ep in range(3):
    env = YawManeuverTask()
    env.attach_hover_policy(tj._get_hover_policy())
    with contextlib.redirect_stdout(io.StringIO()):
        obs, info = env.reset(seed=3000 + ep)
    s = env._raw()
    print("ep%d settle: r=%6.2fdps trim=%+0.3f agl=%5.1fm u=%5.2fkt tgt=%+6.1fdeg" % (
        ep, math.degrees(s['r']), env._rud_trim, s['agl'], s['u_kts'],
        math.degrees(env.target_psi - env.psi0)))
    # 5s 脚蹬双向权限测试
    for uy in (+1.0, -1.0):
        rs = []
        for _ in range(250):
            obs, r, term, trunc, info = env.step(np.array([0.0, 0.0, 0.0, uy]))
            rs.append(math.degrees(env._raw()['r']))
            if term or trunc: break
        print("   u_yaw=%+4.1f -> r_mean=%7.2fdps r_max=%7.2f r_min=%7.2f" % (uy, np.mean(rs), max(rs), min(rs)))
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=3000 + ep)
print("reward sanity:")
env = YawManeuverTask(); env.attach_hover_policy(tj._get_hover_policy())
with contextlib.redirect_stdout(io.StringIO()):
    env.reset(seed=1)
obs, r, term, trunc, info = env.step(np.zeros(4))
print("  zero-action first reward:", round(float(r), 4))
