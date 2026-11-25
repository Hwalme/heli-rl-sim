# -*- coding: utf-8 -*-
"""yaw/waypoint v3 冒烟探针: 加载冒烟策略, 固定 seed 跑 5 回合, 记录任务行为迹象。
yaw:      逐回合 on_heading 步数 / 末段航向误差 / 坠毁
waypoint: 逐回合 到达航点数 / 末段水平速率 / 距离下降量 / 坠毁
判据见 notes_yaw_waypoint_reward_v3.md §5 (冒烟判据, 非验收)。"""
import sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
import io, contextlib
import math
import numpy as np

TASK = sys.argv[1] if len(sys.argv) > 1 else "yaw"
ZIP = sys.argv[2] if len(sys.argv) > 2 else ("results/jsbsim_ppo_yaw_smoke_v3.zip" if TASK == "yaw" else "results/jsbsim_ppo_waypoint_smoke_v3.zip")

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    from stable_baselines3 import PPO
    from jsbsim_tasks import YawManeuverTask, WaypointTrackTask
    import train_jsbsim as tj
    model = PPO.load(ZIP, device="cpu")
    env = (YawManeuverTask() if TASK == "yaw" else WaypointTrackTask())
    env.attach_hover_policy(tj._get_hover_policy())

print("probe: %s v3 smoke policy %s, 5 episodes (deterministic)" % (TASK, ZIP))
ok_eps = 0
for ep in range(5):
    with contextlib.redirect_stdout(io.StringIO()):
        obs, info = env.reset(seed=1000 + ep)
    crash = False
    if TASK == "yaw":
        on_hdg = 0
        while True:
            act, _ = model.predict(obs, deterministic=True)
            obs, r, term, trunc, info = env.step(act)
            on_hdg = env.on_heading_steps
            if term or trunc:
                crash = bool(info.get("crashed", False))
                break
        s = env._raw()
        hdg_err_end = math.degrees(abs(math.atan2(math.sin(env.target_psi - s['psi']), math.cos(env.target_psi - s['psi']))))
        n_tail = max(1, int(0.1 * len(env.psi_errs)))
        tail_err = math.degrees(float(np.mean(env.psi_errs[-n_tail:])))
        moved = (on_hdg >= 20 or tail_err < 50.0)
        ok_eps += int(moved and not crash)
        print("ep%d: on_hdg_steps=%4d tail_err=%6.1fdeg end_err=%6.1fdeg crash=%s -> %s"
              % (ep, on_hdg, tail_err, hdg_err_end, crash, "SIGNUM" if moved else "weak"))
    else:
        d0 = None
        while True:
            act, _ = model.predict(obs, deterministic=True)
            obs, r, term, trunc, info = env.step(act)
            if d0 is None:
                d0 = env._wp_rel()[2] if env.wp_idx == 0 else 0.0
            if term or trunc:
                crash = bool(info.get("crashed", False))
                break
        s = env._raw()
        v_h = math.hypot(s['u'], s['v'])
        d_end = env._wp_rel()[2] if env.wp_idx < len(env.wps) else 0.0
        closed = (d0 - d_end)
        moved = (env.wp_idx >= 1) or (closed > 40.0)
        ok_eps += int(moved and not crash)
        print("ep%d: wp_reached=%d/%d closed=%6.1fm end_vh=%5.2fm/s crash=%s -> %s"
              % (ep, env.wp_idx, len(env.wps), closed, v_h, crash, "SIGNUM" if moved else "weak"))
print("verdict: %d/5 episodes show directed-behavior signature" % ok_eps)
