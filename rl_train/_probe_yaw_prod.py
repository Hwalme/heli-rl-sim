# -*- coding: utf-8 -*-
"""yaw v3 生产行为细查: 逐回合 heading 带宽驻留 / 高度 / 速度 / 偏航速率"""
import sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
import io, contextlib, math
import numpy as np
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    from stable_baselines3 import PPO
    from jsbsim_tasks import YawManeuverTask
    import train_jsbsim as tj
    model = PPO.load("results/jsbsim_ppo_yaw.zip", device="cpu")
    env = YawManeuverTask()
    env.attach_hover_policy(tj._get_hover_policy())
for ep in range(6):
    with contextlib.redirect_stdout(io.StringIO()):
        obs, info = env.reset(seed=2000 + ep)
    tgt_chg = math.degrees(env.target_psi - env.psi0)
    b5 = b10 = b20 = 0; rot = []
    while True:
        act, _ = model.predict(obs, deterministic=True)
        obs, r, term, trunc, info = env.step(act)
        s = env._raw()
        err = abs(math.atan2(math.sin(env.target_psi - s['psi']), math.cos(env.target_psi - s['psi'])))
        b5 += err < math.radians(5); b10 += err < math.radians(10); b20 += err < math.radians(20)
        rot.append(math.degrees(s['r']))
        if term or trunc: break
    s = env._raw()
    print("ep%d tgt=%+6.1f | err5s=%3d err10s=%3d err20s=%3d | end_err=%6.1f end_agl=%5.1fm u=%6.1fkt r_end=%6.1fdps crash=%s"
          % (ep, tgt_chg, b5, b10, b20,
             math.degrees(abs(math.atan2(math.sin(env.target_psi - s['psi']), math.cos(env.target_psi - s['psi'])))),
             s['agl'], s['u_kts'], math.degrees(s['r']), info.get("crashed")))
