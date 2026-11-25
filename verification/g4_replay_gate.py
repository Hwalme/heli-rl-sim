# -*- coding: utf-8 -*-
"""
g4_replay_gate.py — G4 FG 回放闸门 (验收五道闸之第四道)

规则 (《事故复盘与整改令_20260830.md》§4 铁律5 + §5):
  1. 只允许回放 success=True 的回合 (2026-08-30 06:44 教训: 用失败轨迹回放
     也能拿 6/6 PASS, 那验证的是"显示器", 不是"飞得好");
  2. 回放回合还必须过轨迹质量判据:
       takeoff*: 净漂移 < 30 m, 航向漂移 < 30°, 爬升过冲 < 50%;
       landing : 接地垂直速度 > -3 m/s, 接地水平速度 < 2 m/s;
       hover   : 净漂移 < 20 m;
       其他    : 净漂移 < 50 m (宽松默认).
  3. 输出可直接复制的 B-Shell 回放命令 (verify_replay.py)。

位置来源: 新轨迹带 lat/lon/psi_deg 真值; 旧轨迹用 body 速度 + 偏航角速度
积分做航位推算 (阈值判据量级足够可靠)。

用法:
  python g4_replay_gate.py ../rl_train/results/jsbsim_ppo_takeoff_fg_v..._trajectory.json
  python g4_replay_gate.py <traj.json> --task takeoff   # 目录名推断失败时显式指定
"""
import argparse
import json
import math
import os
import re
import sys

DT = 0.02
M_PER_DEG = 111320.0

THRESHOLDS = {
    "takeoff":   {"net_drift_m": 30.0, "heading_drift_deg": 30.0, "overshoot_ratio": 1.0},  # v5.2: 用户令, 打转不可接受(180->30)
    "landing":   {"touchdown_vz_min": -3.0, "touchdown_hs_max": 2.0, "heading_drift_deg": 45.0},  # 主E 16:45裁决随训练门同步
    "hover":     {"net_drift_m": 20.0},
    "default":   {"net_drift_m": 50.0},
}

# 任务名 -> 阈值族 (takeoff_fg/takeoff_hard 同属起飞家族)
TASK_FAMILY = {
    "takeoff_fg": "takeoff", "takeoff_hard": "takeoff", "takeoff": "takeoff",
    "landing": "landing", "hover": "hover",
}


def family(task):
    return TASK_FAMILY.get(task, "default")


def infer_task(path, explicit=None):
    if explicit:
        return explicit
    m = re.search(r"jsbsim_ppo_(takeoff_fg|takeoff_hard|takeoff|landing|hover|forward|roll|yaw|waypoint)", os.path.basename(path))
    return m.group(1) if m else "default"


def episode_metrics(ep):
    """单个回合的质量指标。有真值用真值, 否则航位推算。"""
    frames = ep["frames"]
    n = len(frames)
    if n == 0:
        return {}
    has_truth = "lat" in frames[0] and frames[0]["lat"] is not None
    if has_truth:
        lat0, lon0 = frames[0]["lat"], frames[0]["lon"]
        coslat = math.cos(math.radians(lat0))
        last = frames[-1]
        net = math.hypot((last["lat"] - lat0) * M_PER_DEG,
                         (last["lon"] - lon0) * M_PER_DEG * coslat)
        path_len = 0.0
        prev = (lat0, lon0)
        for f in frames:
            path_len += math.hypot((f["lat"] - prev[0]) * M_PER_DEG,
                                   (f["lon"] - prev[1]) * M_PER_DEG * coslat)
            prev = (f["lat"], f["lon"])
        hdg0 = frames[0].get("psi_deg", 0.0) or 0.0
        hdg_last = frames[-1].get("psi_deg", hdg0)
        # 航向漂移取整段最大差 (考虑 360 环绕的最小角距)
        hdg_drift = 0.0
        for f in frames:
            d = (f.get("psi_deg", hdg0) - hdg0) % 360.0
            if d > 180.0:
                d -= 360.0
            hdg_drift = max(hdg_drift, abs(d))
    else:
        # 航位推算: 积分 yaw_rate 得相对航向, 再旋转 body 速度
        psi = 0.0
        x = y = 0.0  # 北, 东
        path_len = 0.0
        for f in frames:
            vx, vy = f.get("vx_mps", 0.0), f.get("vy_mps", 0.0)
            c, s = math.cos(psi), math.sin(psi)
            dn, de = vx * c - vy * s, vx * s + vy * c
            x += dn * DT
            y += de * DT
            path_len += math.hypot(dn, de) * DT
            psi += math.radians(f.get("yaw_rate_dps", 0.0)) * DT
        net = math.hypot(x, y)
        hdg_drift = abs(math.degrees(psi))

    m = {
        "net_drift_m": round(net, 1),
        "path_len_m": round(path_len, 1),
        "heading_drift_deg": round(hdg_drift, 1),
        "max_alt_m": round(max(f.get("agl_m", 0.0) for f in frames), 1),
        "end_alt_m": round(frames[-1].get("agl_m", 0.0), 1),
        "max_hs_mps": round(max(math.hypot(f.get("vx_mps", 0), f.get("vy_mps", 0)) for f in frames), 2),
        "end_vz_mps": round(frames[-1].get("vz_mps", 0.0), 2),
        "end_hs_mps": round(math.hypot(frames[-1].get("vx_mps", 0), frames[-1].get("vy_mps", 0)), 2),
        "duration_s": round(frames[-1].get("t", n * DT), 1),
        "position_source": "truth" if has_truth else "dead_reckoning",
    }
    return m


def judge(task, ep, m):
    """返回 (是否可回放, 失败原因列表)。前提: ep['success'] 必须 True。"""
    fails = []
    th = THRESHOLDS.get(family(task), THRESHOLDS["default"])
    if not ep.get("success"):
        fails.append("success=False (铁律5: 失败回合禁止回放)")
    if ep.get("crashed"):
        fails.append("crashed=True")
    if "net_drift_m" in th and m.get("net_drift_m", 1e9) > th["net_drift_m"]:
        fails.append("净漂移 %.0fm > %.0fm" % (m.get("net_drift_m", -1), th["net_drift_m"]))
    if "heading_drift_deg" in th and m.get("heading_drift_deg", 1e9) > th["heading_drift_deg"]:
        fails.append("航向漂移 %.0f° > %.0f°" % (m.get("heading_drift_deg", -1), th["heading_drift_deg"]))
    if "overshoot_ratio" in th and m.get("max_alt_m", 0) > 0:
        target = 15.0  # takeoff 家族目标高度
        over = (m["max_alt_m"] - target) / target
        if over > th["overshoot_ratio"]:
            fails.append("爬升过冲 %.0f%% > %.0f%%" % (100 * over, 100 * th["overshoot_ratio"]))
    if "touchdown_vz_min" in th:
        if m.get("end_alt_m", 99) > 0.5:
            fails.append("未接地 (end_agl=%.1fm)" % m.get("end_alt_m", -1))
        elif m.get("end_vz_mps", 0) < th["touchdown_vz_min"]:
            fails.append("接地垂速 %.1f < %.1f m/s" % (m.get("end_vz_mps", 0), th["touchdown_vz_min"]))
        if m.get("end_hs_mps", 99) > th["touchdown_hs_max"]:
            fails.append("接地水平速度 %.1f > %.1f m/s" % (m.get("end_hs_mps", 0), th["touchdown_hs_max"]))
    return (len(fails) == 0), fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traj_json")
    ap.add_argument("--task", default=None)
    args = ap.parse_args()

    path = os.path.abspath(args.traj_json)
    task = infer_task(path, args.task)
    d = json.load(open(path, encoding="utf-8"))
    print("=" * 84)
    print("G4 回放闸门 | 文件: %s" % os.path.basename(path))
    print("任务: %s | 阈值族: %s | 阈值: %s" % (task, family(task), THRESHOLDS.get(family(task), THRESHOLDS["default"])))
    print("=" * 84)

    replayable = []
    n_total = n_succ = 0
    for seed_group, eps in d.get("trajectories", {}).items():
        for ep in eps:
            n_total += 1
            if not ep.get("success"):
                continue
            n_succ += 1
            m = episode_metrics(ep)
            ok, fails = judge(task, ep, m)
            tag = "REPLAY-OK" if ok else "QUALITY-FAIL"
            print("  %s ep%-2d (%s)  %s" % (seed_group, ep["episode"], tag, m))
            for f in fails:
                print("      - %s" % f)
            if ok:
                replayable.append((seed_group, ep["episode"], m))

    print("-" * 84)
    print("记录回合 %d 个, 其中成功 %d 个, 质量+成功双达标(可回放) %d 个" % (n_total, n_succ, len(replayable)))
    if replayable:
        sg, eidx, m = replayable[0]
        seed_num = sg.replace("seed_", "")
        print("\n[建议回放命令] (B-Shell 显示壳, 需 FG 以 fg_shell 配置运行)")
        print("  cd platform/fg_shell && python verify_replay.py --file \"%s\" --seed %s --episode-index %d" % (
            path, seed_num, eidx))
        verdict = "PASS"
    elif n_succ == 0:
        print("  无成功回合 —— 该模型不具备回放资格 (先回 G2 训练门)")
        verdict = "FAIL(no-success)"
    else:
        print("  有成功回合但全部未过质量判据 —— 回 G1/G2 (奖励或训练不足)")
        verdict = "FAIL(quality)"
    out = {"file": path, "task": task, "verdict": verdict,
           "n_total": n_total, "n_success": n_succ, "replayable": replayable}
    outp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "g4_gate_%s_%s.json" % (task, os.path.basename(path).replace("_trajectory.json", "")))
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("证据已落盘: %s" % outp)
    print("G4 判定: %s" % verdict)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
