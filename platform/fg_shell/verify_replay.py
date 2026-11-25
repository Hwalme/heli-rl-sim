#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B-Shell 回放验证器（G4 闸门版）：边回放边从 telnet 轮询 FG 属性树，与轨迹同刻数据比对。

G4 闸门（整改令 2026-08-30 铁律5）：
  1. 只回放 trajectory_success=true 的回合（--auto-select 自动查找，或手动指定）
  2. 轨迹质量判据：
     - 起飞: 水平漂移<30m 且 航向漂移<30°
     - 悬停: 净漂移<20m
     - 降落: 水平接地速度<2m/s
     - 前飞/滚转/偏航/航点: 回放保真度即可
  3. verdict 需同时满足回放保真度 + 轨迹质量

流程：
  1. 后台线程启动 Replayer（UDP -> 127.0.0.1:5500）
  2. 主线程连 telnet 5503，周期采样高度/航向/位置
  3. 每个样本按采样时刻映射到轨迹时间，与最近轨迹帧比对
  4. 读 /sim/crashed 确认 FG 不坠机
  5. 结果落 platform/fg_shell/logs/verify_<task>_<stamp>.json/.txt
"""
import argparse
import json
import math
import os
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import playback as pb

# G4 闸门单一事实源（铁律5, 整改令 2026-08-30）：verdict 必须以
# verification/g4_replay_gate.py 的判定为准（success + 轨迹质量）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VERIF_DIR = os.path.join(_REPO_ROOT, "verification")
if _VERIF_DIR not in sys.path:
    sys.path.insert(0, _VERIF_DIR)
try:
    import g4_replay_gate as g4
except ImportError as e:  # pragma: no cover
    print("[G4] 致命: 无法导入 verification/g4_replay_gate.py —— %s" % e)
    raise


class TelnetProps(object):
    """FG telnet props 通道最小客户端。"""

    def __init__(self, port, timeout=10.0):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.sock.settimeout(2.0)
        time.sleep(0.3)
        self.drain()

    def drain(self):
        out = b""
        while True:
            try:
                c = self.sock.recv(65536)
            except socket.timeout:
                break
            if not c:
                break
            out += c
            if out.rstrip().endswith(b">"):
                break
        return out.decode("utf-8", "replace")

    def get(self, prop):
        self.sock.sendall(("get %s\r\n" % prop).encode())
        time.sleep(0.05)
        m = re.findall(r"'([^']*)'", self.drain())
        v = m[-1].strip() if m else ""
        if v.lower() == "true":
            return 1.0
        if v.lower() in ("false", "(unknown)", ""):
            return None if v == "" else 0.0
        try:
            return float(v)
        except ValueError:
            return None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def track_alt_ft(s, elev_ft):
    return elev_ft + s["agl_m"] * pb.M2FT


def load_traj(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def g4_gate_episode(traj_data, task, seed_group, episode_index):
    """对单个回合执行 G4 闸门（success + 轨迹质量，g4_replay_gate 为唯一判据）。
    返回 (passed, fail_reasons, metrics)。"""
    eps = traj_data.get("trajectories", {}).get(seed_group)
    if not eps or episode_index < 0 or episode_index >= len(eps):
        return False, ["episode 不存在: %s[%d]" % (seed_group, episode_index)], {}
    ep = eps[episode_index]
    m = g4.episode_metrics(ep)
    ok, fails = g4.judge(task, ep, m)
    return ok, fails, m


def find_gate_pass_episode(file_path, task):
    """铁律5: 自动选取第一个通过 G4 闸门（成功+质量）的回合。
    返回 (seed_num, episode_index, metrics) 或 None。"""
    data = load_traj(file_path)
    for seed_key in sorted(data.get("trajectories", {}).keys()):
        for i in range(len(data["trajectories"][seed_key])):
            ok, _reasons, m = g4_gate_episode(data, task, seed_key, i)
            if ok:
                mm = re.search(r"(\d+)", seed_key)
                return (int(mm.group(1)) if mm else None), i, m
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="verify FG replay against trajectory (G4 gate)")
    ap.add_argument("--file", default=os.path.join(pb.DEFAULT_TRAJ_DIR,
                    "jsbsim_ppo_direct_trajectory.json"))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--episode-index", type=int, default=0)
    ap.add_argument("--auto-select", action="store_true",
                    help="自动查找通过 G4 闸门（模型级+回合级）的回合（铁律5）")
    ap.add_argument("--min-model-success", type=float, default=0.85,
                    help="模型级成功率下限（验收指标5 口径 0.85）；低于则整文件拒绝回放（E 工作单 2026-08-30 11:00）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5500)
    ap.add_argument("--rate", type=float, default=100.0)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--loop", type=int, default=0)
    ap.add_argument("--gap", type=float, default=1.0)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--telnet-port", type=int, default=5503)
    ap.add_argument("--lat0", type=float, default=pb.DEFAULT_ORIGIN[0])
    ap.add_argument("--lon0", type=float, default=pb.DEFAULT_ORIGIN[1])
    ap.add_argument("--elev-ft", type=float, default=pb.DEFAULT_ELEV_FT)
    ap.add_argument("--heading0", type=float, default=0.0)
    ap.add_argument("--pos-every", type=int, default=3)
    ap.add_argument("--log-dir", default=os.path.join(os.path.dirname(
                    os.path.abspath(__file__)), "logs"))
    args = ap.parse_args(argv)

    # ============ G4 闸门（铁律5）: 回放前判定, FAIL 直接拒绝 ============
    traj_data = load_traj(args.file)
    gate_task = g4.infer_task(os.path.abspath(args.file))

    # --- 模型级闸门：低成功率模型不得靠挑回合拿演示 Pass（E 工作单 08-30 11:00）---
    def model_success_rate(td):
        v = td.get("overall_success_rate")
        if isinstance(v, (int, float)):
            return float(v), "overall_success_rate 字段"
        tr = td.get("trajectories") or {}
        tot = succ = 0
        for eps in tr.values():
            for ep in eps:
                tot += 1
                succ += 1 if ep.get("success") else 0
        if tot == 0:
            return None, "无回合可统计"
        return succ / tot, "按 %d 回合计算" % tot

    model_rate, rate_src = model_success_rate(traj_data)
    if model_rate is None or model_rate < args.min_model_success:
        print("[G4] REFUSAL: 模型级成功率 %s（%s）< 下限 %.2f，整文件拒绝回放"
              % ("未知" if model_rate is None else "%.1f%%" % (100 * model_rate),
                 rate_src, args.min_model_success), flush=True)
        print("[G4] 该模型须先回 G2 训练门达标（>=85%），再以新轨迹文件重试", flush=True)
        return 2
    print("[G4] 模型级闸门通过: 成功率 %.1f%%（%s）>= %.0f%%"
          % (100 * model_rate, rate_src, 100 * args.min_model_success), flush=True)

    if args.auto_select:
        found = find_gate_pass_episode(args.file, gate_task)
        if found is None:
            print("[G4] FAIL: 全部回合未通过闸门（成功+质量），拒绝回放", flush=True)
            return 2
        args.seed, args.episode_index, gate_metrics = found
        seed_group = "seed_%d" % args.seed
        gate_reasons = []
        print("[G4] auto-select（闸门通过）: seed=%s ep=%d metrics=%s"
              % (args.seed, args.episode_index, gate_metrics), flush=True)
    else:
        seed_group = "seed_%d" % args.seed if args.seed is not None else None
        if seed_group is None:
            tr = traj_data.get("trajectories") or {}
            if not tr:
                print("[G4] FAIL: 轨迹文件无 trajectories，拒绝回放", flush=True)
                return 2
            seed_group = sorted(tr)[0]
        gate_ok, gate_reasons, gate_metrics = g4_gate_episode(
            traj_data, gate_task, seed_group, args.episode_index)
        if not gate_ok:
            print("[G4] REFUSAL: 回合 %s[%d] 未通过闸门，拒绝回放（铁律5）"
                  % (seed_group, args.episode_index), flush=True)
            for r in gate_reasons:
                print("   - %s" % r)
            print("[G4] 可用放行回合查询: python verification/g4_replay_gate.py \"%s\""
                  % os.path.abspath(args.file), flush=True)
            return 2
        print("[G4] 闸门通过: seed_group=%s ep=%d metrics=%s"
              % (seed_group, args.episode_index, gate_metrics), flush=True)

    frames, meta = pb.load_episode(args.file, seed=args.seed,
                                   episode_index=args.episode_index)

    track, integ = pb.build_track(frames, heading0=args.heading0)
    duration = track[-1]["t"]
    task = meta["task"] or gate_task

    os.makedirs(args.log_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(args.log_dir, "replay_%s_verify_%s.csv" % (task, stamp))

    print("[verify] task=%s file=%s dur=%.1fs speed=%.2f samples=%d"
          % (task, meta["file"], duration, args.speed, args.samples))

    stop = threading.Event()
    rp = pb.Replayer(track, host=args.host, port=args.port, rate=args.rate,
                     speed=args.speed, loop=args.loop, gap=args.gap,
                     lat0=args.lat0, lon0=args.lon0, elev_ft=args.elev_ft,
                     log_path=csv_path, stop_flag=stop)
    th = threading.Thread(target=rp.run, daemon=True)
    th.start()
    time.sleep(1.0)

    tn = TelnetProps(args.telnet_port)
    samples = []
    crashed = None
    try:
        while len(samples) < args.samples:
            t0 = time.perf_counter()
            alt = tn.get("/position/altitude-ft")
            hdg = tn.get("/orientation/heading-deg")
            t1 = time.perf_counter()
            if len(samples) % args.pos_every == 0:
                lat = tn.get("/position/latitude-deg")
                lon = tn.get("/position/longitude-deg")
            else:
                lat = lon = None
            tmid = (t0 + t1) / 2.0
            pt = ((tmid - rp.start_wall) * args.speed) % duration
            if pt < 3.0:
                time.sleep(0.2)
                continue
            lo, hi = 0, len(track) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if track[mid]["t"] < pt:
                    lo = mid + 1
                else:
                    hi = mid
            j = lo
            if j > 0 and abs(track[j - 1]["t"] - pt) < abs(track[j]["t"] - pt):
                j -= 1
            s = track[j]
            d_alt = None if alt is None else alt - track_alt_ft(s, args.elev_ft)
            d_hdg = None
            if hdg is not None:
                d_hdg = (hdg - s["heading"] + 180.0) % 360.0 - 180.0
            d_hor = None
            if lat is not None and lon is not None:
                dn = (lat - (args.lat0 + s["north_m"] / pb.M_PER_DEG_LAT)) * pb.M_PER_DEG_LAT
                de = (lon - (args.lon0 + s["east_m"] /
                     (pb.M_PER_DEG_LAT * math.cos(math.radians(args.lat0))))) * \
                    (pb.M_PER_DEG_LAT * math.cos(math.radians(args.lat0)))
                d_hor = math.hypot(dn, de)
            samples.append({
                "i": len(samples), "wall_s": round(tmid - rp.start_wall, 3),
                "traj_t": round(pt, 3), "frame_t": s["t"],
                "fg_alt_ft": alt, "traj_alt_ft": round(track_alt_ft(s, args.elev_ft), 3),
                "d_alt_ft": None if d_alt is None else round(d_alt, 3),
                "fg_hdg_deg": hdg, "traj_hdg_deg": round(s["heading"], 3),
                "d_hdg_deg": None if d_hdg is None else round(d_hdg, 3),
                "fg_lat": lat, "fg_lon": lon,
                "d_hor_m": None if d_hor is None else round(d_hor, 2),
            })
            print("  #%02d t=%6.2fs traj_t=%6.2fs alt=%8.2f (d=%+7.2f) hdg=%7.2f (d=%+7.2f)"
                  % (len(samples)-1, tmid - rp.start_wall, pt, alt,
                     d_alt if d_alt is not None else float("nan"),
                     hdg, d_hdg if d_hdg is not None else float("nan")))
        crashed = tn.get("/sim/crashed")
    finally:
        tn.close()
        stop.set()
        th.join(timeout=10)

    def stats(key):
        vals = [s[key] for s in samples if s.get(key) is not None]
        if not vals:
            return None
        return {"max_abs": round(max(abs(v) for v in vals), 3),
                "mean": round(sum(vals) / len(vals), 3),
                "rms": round(math.sqrt(sum(v * v for v in vals) / len(vals)), 3)}

    pos = [s for s in samples if s.get("d_hor_m") is not None]
    fg_pos_total_m = 0.0
    for a, b in zip(pos, pos[1:]):
        fg_pos_total_m += math.hypot(
            (b["fg_lat"] - a["fg_lat"]) * pb.M_PER_DEG_LAT,
            (b["fg_lon"] - a["fg_lon"]) * pb.M_PER_DEG_LAT *
            math.cos(math.radians(args.lat0)))

    # 回放保真度（在 summary 定义后计算）
    summary = {
        "timestamp": stamp,
        "file": meta["file"], "task": task, "model": meta["model"],
        "seed_group": meta["seed_group"], "episode": meta["episode"],
        "ep_seed": meta["ep_seed"], "trajectory_success": meta["success"],
        "trajectory_crashed": meta["crashed"],
        "duration_s": duration, "speed": args.speed, "rate_hz": args.rate,
        "udp": "%s:%d" % (args.host, args.port),
        "telnet_port": args.telnet_port, "samples": len(samples),
        "fg_sim_crashed": crashed,
        "packets_sent": rp.packets,
        "d_alt_ft": stats("d_alt_ft"),
        "d_hdg_deg": stats("d_hdg_deg"),
        "d_hor_m": stats("d_hor_m"),
        "fg_position_total_move_m": round(fg_pos_total_m, 2),
        "fg_position_changed": fg_pos_total_m > 1.0,
        "g4_gate": {"task": gate_task, "family": g4.family(gate_task),
                    "passed": True, "metrics": gate_metrics,
                    "seed_group": seed_group,
                    "model_success_rate": model_rate, "rate_source": rate_src,
                    "min_model_success": args.min_model_success,
                    "source": "verification/g4_replay_gate.py"},
        "g4_fidelity_passed": None,
        "verdict": None,
        "note": "G4闸门: 回放保真度 + 轨迹质量双判据; "
                "telnet 单次往返 ~0.4-0.5s; FG=display shell(fdm=null); "
                "rendered model bo105 is VISUAL STAND-IN for AH-1S",
    }

    fidelity_ok = (crashed is not None and crashed < 0.5
                   and summary["fg_position_changed"]
                   and summary["d_alt_ft"] and summary["d_alt_ft"]["max_abs"] < 30.0
                   and summary["d_hdg_deg"] and summary["d_hdg_deg"]["max_abs"] < 25.0)
    summary["g4_fidelity_passed"] = fidelity_ok

    # G4: verdict = 回放保真度（闸门已在回放前强制通过, 铁律5）
    ok = fidelity_ok
    summary["verdict"] = "PASS" if ok else "FAIL"
    if not ok:
        summary["fail_reasons"] = ["fidelity"]

    jpath = os.path.join(args.log_dir, "verify_%s_%s.json" % (task, stamp))
    tpath = os.path.join(args.log_dir, "verify_%s_%s.txt" % (task, stamp))
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "samples": samples}, f,
                  ensure_ascii=False, indent=1)
    with open(tpath, "w", encoding="utf-8") as f:
        f.write("B-Shell replay verification (G4 gate) %s\n" % stamp)
        f.write("task=%s file=%s ep=%s/%s dur=%.1fs speed=%.2f\n"
                % (task, meta["file"], meta["seed_group"], meta["episode"],
                   duration, args.speed))
        f.write("trajectory_success=%s crashed=%s\n" % (meta["success"], meta["crashed"]))
        f.write("G4 gate: task=%s family=%s metrics=%s\n"
                % (gate_task, g4.family(gate_task), gate_metrics))
        f.write("packets=%d fg_sim_crashed=%s fg_position_moved=%.1fm\n"
                % (rp.packets, crashed, fg_pos_total_m))
        f.write("d_alt_ft : %s\n" % summary["d_alt_ft"])
        f.write("d_hdg_deg: %s\n" % summary["d_hdg_deg"])
        f.write("d_hor_m  : %s\n" % summary["d_hor_m"])
        f.write("verdict  : %s (reasons=%s)\n" % (summary["verdict"], summary.get("fail_reasons", [])))
        f.write("\nper-sample:\n")
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print("summary: %s" % json.dumps(
        {k: summary[k] for k in ("d_alt_ft", "d_hdg_deg", "d_hor_m",
                                 "fg_sim_crashed", "fg_position_changed",
                                 "g4_gate",
                                 "g4_fidelity_passed", "verdict", "fail_reasons")
         if k in summary}, ensure_ascii=False))
    print("logs: %s" % jpath)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
