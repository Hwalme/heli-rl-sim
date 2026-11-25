# -*- coding: utf-8 -*-
"""
live_demo.py — 一代策略实时闭环飞行演示 (主E)

真·live: PPO 策略以 50Hz 实时驱动 JSBSim, 每步把真值状态经 UDP 推给 FlightGear
显示壳 (--fdm=null, generic 文本协议)。你在 FG 窗口看到的是 AI **此刻**的操纵结果,
不是任何录制轨迹的回放。

用法 (FG 先用 fg_shell_launch.bat 启动):
  python live_demo.py --model ../results/jsbsim_ppo_takeoff_hard_v20260830T091717.zip --task takeoff_hard
  python live_demo.py --model ../results/jsbsim_ppo_landing_gold_gen1.zip --task landing
可选: --time-scale 0.5 (放慢一倍看清操纵) --hold-sec 10 (起飞后悬停展示秒数)
"""
import argparse
import contextlib
import io
import math
import os
import socket
import sys
import time

RL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'rl_train'))
sys.path.insert(0, RL_DIR)

import os

M_PER_DEG_LAT = 111320.0
M2FT = 1.0 / 0.3048
MPS2KT = 1.94384
ELEV_FT = 13.0  # PHNL
PACKET_FMT = "%.7f,%.7f,%.3f,%.3f,%.3f,%.3f,%.3f,%.1f"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5500)
    ap.add_argument("--time-scale", type=float, default=1.0, help="1.0=实时, 0.5=放慢一倍")
    ap.add_argument("--hold-sec", type=float, default=15.0, help="科目达成后悬停/保持展示秒数")
    ap.add_argument("--max-sec", type=float, default=90.0)
    args = ap.parse_args()

    import warnings
    warnings.filterwarnings("ignore")
    import numpy as np
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        from stable_baselines3 import PPO
        from train_jsbsim import make_env

    model = PPO.load(os.path.abspath(args.model), device="cpu")
    env_fn = make_env(task=args.task, action_mode="direct", wind=True, seed=999)
    env = env_fn()
    core = env
    while hasattr(core, "env"):
        core = core.env  # 剥 Monitor/Wrapper 到内核任务对象 (fdm 所在)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lat0 = lon0 = None

    def send_state(lat, lon, agl_m, roll_deg, pitch_deg, hdg_deg, gs_mps, rpm=550.0):
        pkt = PACKET_FMT % (lat, lon, ELEV_FT + agl_m * M2FT, roll_deg, pitch_deg, hdg_deg, gs_mps * MPS2KT, rpm)
        # generic input 包必须以换行符结尾（fg_shell README 环境事实2），
        # 缺换行 FG 凑不齐一行 -> 通道静默丢弃（首帧后 position 冻结的根因）。
        if not pkt.endswith("\n"):
            pkt = pkt + "\n"
        sock.sendto(pkt.encode("ascii"), (args.host, args.port))

    print("=" * 72)
    print("LIVE DEMO: 策略=%s | 科目=%s | 实时闭环 (非回放)" % (
        os.path.basename(args.model), args.task))
    print("=" * 72, flush=True)

    obs, info = env.reset(seed=777)
    raw = core._get_raw_state() if hasattr(core, '_get_raw_state') else core._raw()
    lat = core.fdm.get_property_value('position/lat-geod-deg')
    lon = core.fdm.get_property_value('position/long-gc-deg')
    # 场景重定位：JSBSim 真值坐标（Edwards AFB 沙漠 2283ft）与 FG 加载的 PHNL
    # 海岛场景相隔半个太平洋——直接喂真值坐标会让渲染端丢弃（position 冻结
    # 17.56ft 的第二根因，第一根因是缺 \n）。显示层改为 PHNL 锚定：
    #   lat_display = PHNL_LAT + (lat_jsbsim - lat0)   （位移平移，物理真值不变）
    #   alt_display = PHNL_ELEV + agl                   （场压 + 离地）
    PHNL_LAT, PHNL_LON, PHNL_ELEV_FT = 21.3187, -157.9224, 13.0
    lat0, lon0 = lat, lon
    coslat = math.cos(math.radians(lat0))
    t0 = time.time()
    dt_sim = 0.02
    achieved_at = None
    step = 0
    try:
        while True:
            el = time.time() - t0
            action, _ = model.predict(obs, deterministic=True)
            obs, r, te, tr, info = env.step(action)
            step += 1

            raw = core._get_raw_state() if hasattr(core, '_get_raw_state') else core._raw()
            lat = core.fdm.get_property_value('position/lat-geod-deg')
            lon = core.fdm.get_property_value('position/long-gc-deg')
            # 显示坐标 = PHNL 锚 + JSBSim 相对位移
            lat_d = PHNL_LAT + (lat - lat0)
            lon_d = PHNL_LON + (lon - lon0) / coslat
            gs = math.hypot(raw.get('vx', raw.get('u', 0.0)), raw.get('vy', raw.get('v', 0.0)))
            hdg = math.degrees(core.fdm.get_property_value('attitude/psi-rad')) % 360.0
            send_state(lat_d, lon_d, max(0.0, raw['agl']), math.degrees(raw['roll']),
                       math.degrees(raw['pitch']), hdg, gs)

            # 节奏: 实时放送 (50Hz 步进 vs 墙钟)
            target = t0 + step * dt_sim / args.time_scale
            wait = target - time.time()
            if wait > 0:
                time.sleep(wait)

            if step % 25 == 0:
                print("t=%5.1fs agl=%6.1fm vz=%+5.2fm/s roll=%+6.1f° pitch=%+6.1f° hdg=%6.1f°"
                      % (el, raw['agl'], raw['vz'], math.degrees(raw['roll']),
                         math.degrees(raw['pitch']), hdg), flush=True)

            if args.task == "takeoff_hard":
                if achieved_at is None and raw['agl'] >= 13.0:
                    achieved_at = time.time()
                    print("\n[达标] 爬升到 %.1fm —— 悬停展示 %ds (AI 正在实时保持)\n" % (raw['agl'], args.hold_sec), flush=True)
                if achieved_at and time.time() - achieved_at >= args.hold_sec:
                    print("[完成] 垂直起飞+悬停演示结束 (总 %.1fs)" % (time.time() - t0), flush=True)
                    break
            else:  # landing
                if info.get("landed"):
                    print("\n[完成] 接地! vs=%.2fm/s hs=%.2fm/s 漂移=%.1fm 航向=%.1f° —— 缓慢垂直降落演示结束"
                          % (info.get("touchdown_vs") or 0, info.get("touchdown_hs_mps") or 0,
                             info.get("net_drift_m", 0), info.get("max_heading_dev_deg", 0)), flush=True)
                    break
            if te or tr:
                print("\n[终局] crashed=%s landed=%s (info=%s)" % (
                    info.get("crashed"), info.get("landed"),
                    {k: round(v, 2) for k, v in info.items() if isinstance(v, float)}), flush=True)
                break
            if el > args.max_sec:
                print("\n[超时] %ds 上限" % args.max_sec, flush=True)
                break
    except KeyboardInterrupt:
        print("\n[手动终止]")
    finally:
        sock.close()
        print("演示结束。FG 窗口保留最后状态。")


if __name__ == "__main__":
    main()
