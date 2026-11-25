#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B-Shell 主线：JSBSim 轨迹 -> FlightGear generic UDP 回放器（显示壳）。

架构终裁（coordination/mailbox-B.md 2026-08-28 20:35）：
  FG = 显示壳（--fdm=null），JSBSim = 物理权威。
  本脚本读取 rl_train/results/jsbsim_ppo_*_trajectory.json（轨道 A 已产出的
  验收同口径 6-DoF 轨迹），按轨迹时间戳以 20~30Hz 向 127.0.0.1:5500 发 UDP 包，
  由 protocol/jsbsim_replay.xml 映射到 /position /orientation 属性树。

数据字段与单位（rl_train/train_jsbsim.py 记录口径，实测核对）：
  t        轨迹时间 s（dt=0.02, 50Hz）
  agl_m    相对离地高度 m（已减 AH-1S 停机基准 6.30ft，max(0,...) 截断）
  vz_mps   当地垂速 m/s（向上为正，来自 -v-down-fps）
  vx_mps   机体前向速度 m/s（u-fps）
  vy_mps   机体右向速度 m/s（v-fps）
  roll_deg / pitch_deg   姿态角 deg（JSBSim phi/theta，与 FG 同号）
  yaw_rate_dps           机体偏航角速度 deg/s（r-rad_sec）
  yaw_deg     仅 direct(hover)/landing 两个文件有：绝对航向 deg [0,360)
  x_m / y_m   仅 direct(hover)/landing 有：记录层对 vx/vy 的“不旋转”积分
              （实测误差 <0.005m 复现；当航向≠0 时它≠真实地迹，
              见 README §数据口径勘误）
  rotor_rpm   仅 direct(hover)/landing 有（spool 1->343 实测）

位置/航向重建（对没有 x_m/y_m/yaw_deg 的 4 个文件必须积分）：
  航向  psi(k+1) = psi(k) + yaw_rate_dps * dt（有 yaw_deg 的文件直接用记录值）
  地速  Vn = u cos(psi) - v sin(psi)
        Ve = u sin(psi) + v cos(psi)        （忽略机体 w 轴分量的显示级近似）
  北东  n += Vn*dt ;  e += Ve*dt
  经纬  平地近似：lat = lat0 + n/111320 ; lon = lon0 + e/(111320 cos(lat0))
  高度  alt_ft = elev0_ft + agl_m / 0.3048   （agl_m 逐帧记录值，无需积分）

包格式（8 个逗号分隔浮点，\\n 结尾，与 protocol/jsbsim_replay.xml 顺序一致）：
  lat, lon, alt_ft, roll, pitch, hdg, gs_kt, rpm

用法示例：
  python playback.py --file <trajectory.json> --episode-index 0 --rate 30 --loop 0
  python playback.py --list-episodes --file jsbsim_ppo_waypoint_trajectory.json

注意：本管线渲染机型为 bo105（视觉替身），物理是 AH-1S(repaired)。
所有对外展示必须披露这一点。
"""
import argparse
import bisect
import csv
import json
import math
import os
import socket
import sys
import time

M2FT = 1.0 / 0.3048
MPS2KT = 3600.0 / 1852.0
M_PER_DEG_LAT = 111320.0

DEFAULT_TRAJ_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "rl_train", "results"))
DEFAULT_ORIGIN = (21.32525, -157.92516)   # PHNL 08L/26R 中点（apt.dat 实测）
DEFAULT_ELEV_FT = 13.0                    # PHNL 场压高（apt.dat: "1 13 1 1 PHNL"）
DEFAULT_NOMINAL_RPM = 428.0               # bo105 governor 飞行位实测（B1 交接文档 §3.3）


def wrap360(x):
    return x - 360.0 * math.floor(x / 360.0)


def load_episode(path, seed=None, episode_index=0):
    """从 trajectory.json 取一条 episode 的 frames。"""
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    tr = d.get("trajectories") or {}
    if not tr:
        raise SystemExit("no 'trajectories' in %s" % path)
    if seed is not None:
        key = "seed_%s" % seed
        if key not in tr:
            raise SystemExit("seed %s not in %s (have %s)" % (seed, path, sorted(tr)))
    else:
        key = sorted(tr)[0]
    eps = tr[key]
    if episode_index >= len(eps):
        raise SystemExit("episode_index %d out of range (%d episodes in %s)"
                         % (episode_index, len(eps), key))
    ep = eps[episode_index]
    meta = {
        "file": os.path.basename(path), "task": d.get("task"), "model": d.get("model"),
        "dt": d.get("dt"), "engine": d.get("engine"), "aircraft": d.get("aircraft"),
        "seed_group": key, "episode": ep.get("episode"),
        "ep_seed": ep.get("seed"), "success": ep.get("success"),
        "crashed": ep.get("crashed"), "n_steps": ep.get("n_steps"),
        "duration_s": ep["frames"][-1]["t"] - ep["frames"][0]["t"],
    }
    return ep["frames"], meta


def build_track(frames, heading0=0.0, dedup=True):
    """frames -> 回放采样点（北东坐标 + 姿态 + 航向）。

    返回 (track, integ_info)。track 每项:
      t, north_m, east_m, alt_ft_base(不含 elev0), roll, pitch, heading, gs_mps, rpm
    """
    has_yaw = ("yaw_deg" in frames[0]) or ("psi_deg" in frames[0])
    def _frame_yaw(f, fallback):
        # v1.1(主E): 真值航向键兼容 —— 新轨迹记录 psi_deg(绝对航向), 旧键名 yaw_deg;
        # 2026-08-31 演示穿模事故: psi_deg=205° 被当 0°, 地速方向错 205° 导致入地穿模
        if "yaw_deg" in f:
            return f["yaw_deg"]
        if "psi_deg" in f:
            return f["psi_deg"]
        return fallback
    has_rpm = "rotor_rpm" in frames[0]
    psi = _frame_yaw(frames[0], float(heading0))
    north = east = 0.0
    track = []
    for i, f in enumerate(frames):
        if i > 0:
            dt = max(0.0, f["t"] - frames[i - 1]["t"])
            if has_yaw:
                psi = _frame_yaw(f, psi)
            else:
                psi = wrap360(psi + f.get("yaw_rate_dps", 0.0) * dt)
            u = f.get("vx_mps", 0.0)
            v = f.get("vy_mps", 0.0)
            r = math.radians(psi)
            vn = u * math.cos(r) - v * math.sin(r)
            ve = u * math.sin(r) + v * math.cos(r)
            north += vn * dt
            east += ve * dt
        else:
            vn = ve = 0.0
        agl = max(0.0, f.get("agl_m", 0.0))
        rpm = f["rotor_rpm"] if has_rpm else DEFAULT_NOMINAL_RPM
        track.append({
            "t": f["t"],
            "north_m": north, "east_m": east,
            "agl_m": agl,
            "roll": f.get("roll_deg", 0.0), "pitch": f.get("pitch_deg", 0.0),
            "heading": psi,
            "gs_mps": math.hypot(vn, ve),
            "rpm": max(0.0, rpm),
        })
    # 采样点过密（50Hz 记录 vs 30Hz 发送），保留全部由发送端按时间对齐即可，
    # dedup 仅去掉 t 完全重复的帧。
    if dedup:
        out, last_t = [], None
        for s in track:
            if last_t is None or s["t"] > last_t:
                out.append(s)
                last_t = s["t"]
        track = out
    integ_info = {
        "yaw_source": "recorded yaw_deg" if has_yaw
                      else "integrated yaw_rate_dps (heading0=%.1f)" % heading0,
        "position_source": "integrated body u,v -> NED (w-axis term neglected)",
        "duration_s": track[-1]["t"],
        "max_north_m": max(s["north_m"] for s in track),
        "max_east_m": max(s["east_m"] for s in track),
        "max_agl_m": max(s["agl_m"] for s in track),
        "max_speed_mps": max(s["gs_mps"] for s in track),
    }
    return track, integ_info


def sample_to_packet(s, lat0, lon0, elev_ft):
    lat = lat0 + s["north_m"] / M_PER_DEG_LAT
    lon = lon0 + s["east_m"] / (M_PER_DEG_LAT * math.cos(math.radians(lat0)))
    alt_ft = elev_ft + s["agl_m"] * M2FT
    gs_kt = s["gs_mps"] * MPS2KT
    return (lat, lon, alt_ft, s["roll"], s["pitch"], s["heading"], gs_kt, s["rpm"])


PACKET_FMT = "%.7f,%.7f,%.3f,%.3f,%.3f,%.3f,%.3f,%.1f"


class Replayer(object):
    """把 track 按轨迹时间戳发 UDP。可在线程里跑（verify_replay.py 复用）。"""

    def __init__(self, track, host="127.0.0.1", port=5500, rate=100.0, speed=1.0,
                 loop=1, gap=1.0, lat0=DEFAULT_ORIGIN[0], lon0=DEFAULT_ORIGIN[1],
                 elev_ft=DEFAULT_ELEV_FT, log_path=None, stop_flag=None):
        self.track = track
        self.host, self.port = host, port
        self.rate, self.speed, self.loop, self.gap = rate, speed, loop, gap
        self.lat0, self.lon0, self.elev_ft = lat0, lon0, elev_ft
        self.log_path = log_path
        self.stop_flag = stop_flag   # threading.Event() 可选
        self.packets = 0
        self.duration = track[-1]["t"]
        self.start_wall = None

    def run(self):
        """按 self.rate 的节拍发送：每个 tick 发送当前轨迹时刻的最新帧。

        必须以 PACKET_FMT + '\\n' 结尾（protocol <line_separator>newline）：
        缺少换行符时 FG 的 generic input 永远凑不齐一行，通道静默不生效
        （2026-08-28 真机实测踩坑，详见 README）。
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        logf = None
        wr = None
        if self.log_path:
            logf = open(self.log_path, "w", newline="", encoding="utf-8")
            wr = csv.writer(logf)
            wr.writerow(["packet", "loop", "wall_s", "traj_t",
                         "lat_deg", "lon_deg", "alt_ft", "roll_deg",
                         "pitch_deg", "heading_deg", "gs_kt", "rpm"])
        self.start_wall = time.perf_counter()
        t0 = self.start_wall
        tick = 1.0 / max(1.0, self.rate)
        ts = [s["t"] for s in self.track]
        n = 0
        try:
            li = 0
            while True:
                li += 1
                loop_start = time.perf_counter()
                k = 0
                while True:
                    if self.stop_flag is not None and self.stop_flag.is_set():
                        return n
                    traj_t = k * tick * self.speed
                    if traj_t > self.duration:
                        break
                    # 当前 tick 对应的最新轨迹帧（二分推进指针）
                    idx = bisect.bisect_right(ts, traj_t) - 1
                    if idx < 0:
                        idx = 0
                    s = self.track[idx]
                    target_wall = loop_start + k * tick
                    now = time.perf_counter()
                    if now < target_wall:
                        time.sleep(target_wall - now)
                    if self.stop_flag is not None and self.stop_flag.is_set():
                        return n
                    vals = sample_to_packet(s, self.lat0, self.lon0, self.elev_ft)
                    pkt = (PACKET_FMT % vals) + "\n"
                    sock.sendto(pkt.encode("ascii"), (self.host, self.port))
                    n += 1
                    if wr:
                        wr.writerow([n, li, round(time.perf_counter() - t0, 4),
                                     s["t"]] + [round(x, 7) for x in vals])
                    k += 1
                if self.loop and li >= self.loop:
                    break
                if self.stop_flag is not None and self.stop_flag.is_set():
                    break
                if self.gap > 0:
                    end_hold = time.perf_counter() + self.gap
                    while time.perf_counter() < end_hold:
                        if self.stop_flag is not None and self.stop_flag.is_set():
                            break
                        time.sleep(0.05)
        finally:
            sock.close()
            if logf:
                logf.close()
        return n


def list_episodes(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    for key, eps in sorted(d["trajectories"].items()):
        for i, ep in enumerate(eps):
            fr = ep["frames"]
            print("%-9s ep%d seed=%-4s steps=%-5s dur=%6.2fs success=%s crashed=%s"
                  % (key, i, ep.get("seed"), ep.get("n_steps"),
                     fr[-1]["t"] - fr[0]["t"], ep.get("success"), ep.get("crashed")))


def main(argv=None):
    ap = argparse.ArgumentParser(description="JSBSim trajectory -> FlightGear UDP replay")
    ap.add_argument("--file", default=os.path.join(DEFAULT_TRAJ_DIR,
                    "jsbsim_ppo_direct_trajectory.json"),
                    help="trajectory.json 路径（默认 direct/hover）")
    ap.add_argument("--list-episodes", action="store_true")
    ap.add_argument("--seed", type=int, default=None, help="seed 组（如 42）")
    ap.add_argument("--episode-index", type=int, default=0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5500)
    ap.add_argument("--rate", type=float, default=100.0, help="发送频率 Hz (推荐100，压缩属性漂移窗口)")
    ap.add_argument("--speed", type=float, default=1.0, help="回放倍速")
    ap.add_argument("--loop", type=int, default=0,
                    help="循环次数；0=无限循环 (Ctrl+C 结束)")
    ap.add_argument("--gap", type=float, default=1.0, help="循环间停顿 s")
    ap.add_argument("--lat0", type=float, default=DEFAULT_ORIGIN[0])
    ap.add_argument("--lon0", type=float, default=DEFAULT_ORIGIN[1])
    ap.add_argument("--elev-ft", type=float, default=DEFAULT_ELEV_FT)
    ap.add_argument("--heading0", type=float, default=0.0,
                    help="无 yaw_deg 文件的初始航向 deg")
    ap.add_argument("--log-dir", default=os.path.join(os.path.dirname(
                    os.path.abspath(__file__)), "logs"))
    args = ap.parse_args(argv)

    if args.list_episodes:
        list_episodes(args.file)
        return 0

    frames, meta = load_episode(args.file, seed=args.seed,
                                episode_index=args.episode_index)
    track, integ = build_track(frames, heading0=args.heading0)

    os.makedirs(args.log_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    task = meta["task"] or "traj"
    csv_path = os.path.join(args.log_dir, "replay_%s_%s.csv" % (task, stamp))
    meta_path = os.path.join(args.log_dir, "replay_%s_%s.meta.json" % (task, stamp))
    meta_out = dict(meta)
    meta_out.update(integ)
    meta_out.update({"origin_lat": args.lat0, "origin_lon": args.lon0,
                     "origin_elev_ft": args.elev_ft, "rate_hz": args.rate,
                     "speed": args.speed, "loop": args.loop, "port": args.port,
                     "protocol": "jsbsim_replay", "packet_format": PACKET_FMT,
                     "replay_log": os.path.basename(csv_path),
                     "started": stamp,
                     "note": "FG=display shell (fdm=null); JSBSim=physics authority; "
                             "rendered model bo105 is a VISUAL STAND-IN for AH-1S"})
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_out, f, ensure_ascii=False, indent=1)

    print("[B-Shell replay] %s (%s/%s ep%s, %.1fs, %.1f steps) -> %s:%d @%.0fHz x%s speed"
          % (meta["file"], meta["seed_group"], task, meta["episode"],
             integ["duration_s"], meta["n_steps"], args.host, args.port,
             args.rate, "inf" if args.loop == 0 else args.loop))
    print("  yaw: %s | pos: %s" % (integ["yaw_source"], integ["position_source"]))
    print("  excursion: north %.0fm east %.0fm maxAgl %.0fm maxGs %.0fm/s"
          % (integ["max_north_m"], integ["max_east_m"],
             integ["max_agl_m"], integ["max_speed_mps"]))
    print("  log: %s" % csv_path)
    rp = Replayer(track, host=args.host, port=args.port, rate=args.rate,
                  speed=args.speed, loop=args.loop, gap=args.gap,
                  lat0=args.lat0, lon0=args.lon0, elev_ft=args.elev_ft,
                  log_path=csv_path)
    try:
        n = rp.run()
    except KeyboardInterrupt:
        n = rp.packets
        print("\n[interrupted]")
    print("done: %d packets in %.1fs wall" % (n, time.perf_counter() - rp.start_wall))
    return 0


if __name__ == "__main__":
    sys.exit(main())
