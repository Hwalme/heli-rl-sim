# -*- coding: utf-8 -*-
"""forward v3 冒烟探针: 加载 smoke 策略, 固定 seed 跑 5 回合, 记录每回合
mean/max 前向速度 (u, m/s 与 kt), 判定是否出现前飞迹象。"""
import sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
import io, contextlib
import numpy as np
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    from stable_baselines3 import PPO
    from jsbsim_tasks import ForwardFlightTask
    model = PPO.load("results/jsbsim_ppo_forward.zip", device="cpu")
    env = ForwardFlightTask()
print("probe: forward v3 production policy, 5 episodes (deterministic)")
ok_eps = 0
for ep in range(5):
    with contextlib.redirect_stdout(io.StringIO()):
        obs, info = env.reset(seed=1000 + ep)
    us = []; terms = 0; trunc = 0
    while True:
        act, _ = model.predict(obs, deterministic=True)
        obs, r, term, trunc, info = env.step(act)
        us.append(info["u_kts"])
        if term: terms += 1
        if trunc: trunc += 1
        if term or trunc: break
    us = np.array(us)
    tail = us[-250:]  # 末段 5 s
    moved = us.max() >= 10.0 or tail.mean() >= 5.0
    ok_eps += int(moved)
    print("ep%d: mean_u=%6.2f kt  tail5s_mean=%6.2f kt  max_u=%6.2f kt  min_u=%6.2f kt  crash=%s  -> %s"
          % (ep, us.mean(), tail.mean(), us.max(), us.min(), info.get("crashed"), "MOVED" if moved else "static"))
print("verdict: %d/5 episodes show forward motion signature" % ok_eps)
