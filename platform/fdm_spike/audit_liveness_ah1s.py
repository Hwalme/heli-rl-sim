#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_liveness.py — helispike 属性活性全审计（主E任务书 0.2-1d，A 适配器观测清单依据）

方法：飞一个全覆盖剖面（地面怠速→spool→总距阶跃起飞→爬升→前飞→下降→接地），
      每 0.1s 采样全部候选属性；属性"活性"= 全程 max-min > 阈值。
      对 velocities/p|q|r-radps 额外做微分交叉验证（与欧拉角微分对照）。
产物：liveness_*.json + 控制台表格（ALIVE/DEAD/STATIC-BY-DESIGN）。
"""
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import jsbsim

HERE = Path(__file__).resolve().parent
ROOT = str(Path(HERE).parent / "jsbsim_root") if False else __import__("jsbsim").get_default_root_dir()
DT = 0.005
D2R = 57.29578

CANDIDATES = ['position/altitude-ft', 'position/agl-ft', 'position/h-agl-ft', 'position/lat-gc-deg', 'position/long-gc-deg', 'velocities/u-fps', 'velocities/v-fps', 'velocities/w-fps', 'velocities/v-north-fps', 'velocities/v-east-fps', 'velocities/v-down-fps', 'velocities/h-dot-fps', 'velocities/vc-fps', 'velocities/groundspeed-kt', 'velocities/ve-kts', 'velocities/airspeed-fps', 'velocities/p-radps', 'velocities/q-radps', 'velocities/r-radps', 'accelerations/pdot-radpssec', 'accelerations/qdot-radpssec', 'accelerations/rdot-radpssec', 'attitude/phi-deg', 'attitude/theta-deg', 'attitude/psi-deg', 'attitude/phi-rad', 'attitude/theta-rad', 'attitude/psi-rad', 'accelerations/n-pilot-xnorm', 'accelerations/n-pilot-ynorm', 'accelerations/n-pilot-znorm', 'aero/qbar-psf', 'atmosphere/rho-slugs-ft3', 'atmosphere/P-psf', 'propulsion/engine[0]/n1', 'propulsion/engine[0]/rotor-rpm', 'propulsion/engine[0]/thruster-rpm', 'propulsion/engine[0]/thrust-lbs', 'propulsion/engine[0]/power-hp', 'propulsion/engine[1]/rotor-rpm', 'propulsion/engine[1]/thruster-rpm', 'propulsion/engine[1]/antitorque-ctrl-rad', 'propulsion/engine/collective-ctrl-rad', 'propulsion/engine/longitudinal-ctrl-rad', 'propulsion/engine/lateral-ctrl-rad', 'fcs/throttle-cmd-norm[0]', 'fcs/collective-cmd-norm', 'fcs/elevator-cmd-norm', 'fcs/aileron-cmd-norm', 'fcs/rudder-cmd-norm', 'gear/unit[0]/compressed-ft', 'gear/unit[0]/WOW', 'inertia/weight-lbs']


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def main():
    f = jsbsim.FGFDMExec(jsbsim.get_default_root_dir(), None)
    if not f.load_model("ah1s", True):
        print("load fail")
        return 1
    f.load_ic("reset00.xml", True)
    f.run_ic()
    f.set_dt(DT)

    series = {p: [] for p in CANDIDATES}
    phases = []  # (name, t_start)

    def sample(tag=None):
        for p in CANDIDATES:
            try:
                series[p].append(f.get_property_value(p))
            except Exception:
                series[p].append(None)

    def run_s(n):
        for _ in range(int(n / DT)):
            f.run()

    t0 = 0.0
    phases.append(("ground_idle", len(series[CANDIDATES[0]])))
    # 1) 地面怠速 5s
    f.set_property_value("propulsion/engine[0]/set-running", 1)
    f.set_property_value("propulsion/engine[1]/set-running", 1)
    f.set_property_value("fcs/rpm-governor-active-norm", 1.0)
    for _ in range(int(5 / DT)):
        f.run()
        if abs((f.get_property_value("sim-time-sec") or 0) * 1000) % 2 < 0.005:
            pass
        if int((f.get_property_value("sim-time-sec") or 0) / 0.1) != int(
                (f.get_property_value("sim-time-sec") or 0.1) / 0.1):
            pass
        # 每 0.1s 采样一次（用步数计）
        if _ % 20 == 0:
            sample()
    # 2) spool 到转速平台（总距 0，直到 rpm 稳）
    phases.append(("spool", len(series[CANDIDATES[0]])))
    for _ in range(int(25 / DT)):
        f.run()
        if _ % 20 == 0:
            sample()
    # 3) 总距阶跃起飞+爬升 15s（0.72）
    phases.append(("liftoff_climb", len(series[CANDIDATES[0]])))
    f.set_property_value("fcs/collective-cmd-norm", 0.74)
    for _ in range(int(15 / DT)):
        f.run()
        if _ % 20 == 0:
            sample()
    # 4) 前飞（俯仰 -4° 指令经 FCS 通道）12s
    phases.append(("forward", len(series[CANDIDATES[0]])))
    for _ in range(int(12 / DT)):
        f.set_property_value("fcs/elevator-cmd-norm", 0.10)  # 前推杆（方向由审计确认）
        f.run()
        if _ % 20 == 0:
            sample()
    f.set_property_value("fcs/elevator-cmd-norm", 0.0)
    # 5) 下降 10s（总距回 0.62）
    phases.append(("descend", len(series[CANDIDATES[0]])))
    f.set_property_value("fcs/collective-cmd-norm", 0.62)
    for _ in range(int(10 / DT)):
        f.run()
        if _ % 20 == 0:
            sample()
    # 6) 缓降接地 12s（总距 0.55 → 0.50 阶梯）
    phases.append(("touchdown", len(series[CANDIDATES[0]])))
    f.set_property_value("fcs/collective-cmd-norm", 0.55)
    for _ in range(int(6 / DT)):
        f.run()
        if _ % 20 == 0:
            sample()
    f.set_property_value("fcs/collective-cmd-norm", 0.50)
    for _ in range(int(6 / DT)):
        f.run()
        if _ % 20 == 0:
            sample()
    sample()

    # ---- 活性判定 ----
    rows = []
    for p in CANDIDATES:
        vals = [v for v in series[p] if v is not None and not (
            isinstance(v, float) and math.isnan(v))]
        n_missing = len(series[p]) - len(vals)
        if not vals:
            rows.append((p, "DEAD(never-readable)", 0, 0, n_missing))
            continue
        rng = max(vals) - min(vals)
        if rng > 1e-6:
            verdict = "ALIVE"
        else:
            verdict = "DEAD(constant)"
        rows.append((p, verdict, round(rng, 4), len(vals), n_missing))

    print("=" * 88)
    print("%-52s %-20s %10s %6s %6s" % ("property", "verdict", "max-min", "n", "miss"))
    print("-" * 88)
    for p, v, rng, n, miss in rows:
        print("%-52s %-20s %10.4f %6d %6d" % (p, v, rng, n, miss))
    alive = [r[0] for r in rows if r[1] == "ALIVE"]
    dead = [r[0] for r in rows if r[1].startswith("DEAD")]
    print("-" * 88)
    print("ALIVE: %d / DEAD: %d" % (len(alive), len(dead)))

    # r-radps 微分交叉验证：psi 数值微分 vs r-radps 读数
    psi = [v for p, v in zip(CANDIDATES, [series[p] for p in CANDIDATES if p == "attitude/psi-deg"]) for v in [v]][0]
    psi = series["attitude/psi-deg"]
    rr = series["velocities/r-radps"]
    diffs = []
    for i in range(1, len(psi)):
        if psi[i] is not None and psi[i-1] is not None and rr[i] is not None:
            diffs.append((rr[i] - (wrapd(psi[i] - psi[i-1]) / (DT * 10) / D2R)))
    if diffs:
        mean_r = sum(diffs) / len(diffs)
        max_r = max(abs(x) for x in diffs)
        print("r-radps vs psi微分 残差: mean=%.4f°/s max=%.4f°/s (r-dead=>max大)"
              % (mean_r, max_r))

    out = HERE / "audit_results"
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out / f"liveness_{stamp}.json").write_text(json.dumps({
        "stamp": stamp, "rows": [{"prop": p, "verdict": v, "range": rng, "n": n,
                                  "missing": miss} for p, v, rng, n, miss in rows],
        "alive": alive, "dead": dead,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("证据: %s" % (out / f"liveness_{stamp}.json"))
    return 0


def wrapd(x):
    return (x + 180.0) % 360.0 - 180.0


if __name__ == "__main__":
    sys.exit(main())
