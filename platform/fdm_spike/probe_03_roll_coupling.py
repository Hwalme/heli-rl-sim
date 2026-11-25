#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_03_roll_coupling.py — T2 滚转-前飞耦合专项探针

三个假设（同时打探针，看哪个解释了 >23kt 发散）:
  H1: 前飞 transverse flow 致副翼有效方向反转
  H2: helispike 配平表中 roll-trim-from-ff 的符号与量级与前飞段不匹配
  H3: +ele 低头与 +ail 同号时的横向通道交叉耦合

做法: 探针逐阶俯仰(ve), 测 4 模式下的滚转速率最大值:
  M0 baseline                  (探针 0.2-1a 的 +ele -3..-18° 配平)
  M1 only FF roll-trim 强制关闭
  M2 only FF yaw-trim 强制关闭（让前飞段缺前馈量、暴露偏航驱动力）
  M3 反号 ail 副翼(roll_sign=-1 -> +1)

判定: 任何模式让最大 |roll| 速率显著下降(>50%)，则该假设为有效。
 成功基线: max |roll| 速率 <5°/s 持续 5s。
产物: probe_results/probe03_<stamp>.json + 控制台表
"""
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import jsbsim

HERE = Path(__file__).resolve().parent
ROOT = jsbsim.get_default_root_dir()
OUT = HERE / "probe_results"
OUT.mkdir(exist_ok=True)
DT = 0.005
D2R = 57.29578

ROLL_SIGN = +1.0
PITCH_SIGN = +1.0
HDG_SIGN = +1.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def wrap180(x):
    return (x + 180.0) % 360.0 - 180.0


class Heli:
    def __init__(self, mode="M0", roll_sign_override=None, roll_sign=ROLL_SIGN):
        self.fdm = jsbsim.FGFDMExec(ROOT, None)
        if not self.fdm.load_model("helispike", True):
            raise RuntimeError
        self.fdm.load_ic("reset00.xml", True)
        self.fdm.run_ic()
        self.t = 0.0
        self.mode = mode
        self.roll_sign = roll_sign_override if roll_sign_override is not None else roll_sign
        # M1: 关 FF roll-trim-from-ff
        if mode == "M1":
            self.fdm.set_property_value("fcs/hs/roll-trim-from-ff", 0.0)
        # M2: 关 FF yaw-trim (collective→antitorque) -- 模拟前飞段无偏航补偿
        # 实际做法: 设 fcs/hs/antitorque-ff = 0
        if mode == "M2":
            self.fdm.set_property_value("fcs/hs/antitorque-ff", 0.0)

    def start(self):
        f = self.fdm
        f.set_property_value("propulsion/engine[0]/set-running", 1)
        f.set_property_value("propulsion/engine[1]/set-running", 1)
        f.set_property_value("propulsion/engine[0]/clutch", 1)
        f.set_property_value("fcs/throttle-cmd-norm[0]", 1.0)
        f.set_property_value("fcs/hs/afcs-enable", 1.0)
        self.fdm.set_property_value("fcs/collective-cmd-norm", 0.0)

    def set_coll(self, v):
        self.fdm.set_property_value("fcs/collective-cmd-norm", v)

    def st(self):
        f = self.fdm
        agl = f.get_property_value("position/agl-ft")
        return {
            "t": self.t, "agl": agl,
            "nan": math.isnan(agl),
            "vs_fpm": f.get_property_value("velocities/h-dot-fps") * 60.0,
            "ve_kts": f.get_property_value("velocities/ve-kts"),
            "roll": f.get_property_value("attitude/phi-deg"),
            "pitch": f.get_property_value("attitude/theta-deg"),
            "hdg": f.get_property_value("attitude/psi-deg"),
            "coll": f.get_property_value("fcs/collective-cmd-norm"),
            "ele": f.get_property_value("fcs/elevator-cmd-norm"),
            "rud": f.get_property_value("propulsion/engine[1]/antitorque-ctrl-rad"),
            "ail": f.get_property_value("fcs/aileron-cmd-norm"),
            "rpm": f.get_property_value("propulsion/engine[0]/rotor-rpm"),
        }

    def step(self, n=1, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=None):
        f = self.fdm
        for _ in range(n):
            roll = f.get_property_value("attitude/phi-deg")
            pitch = f.get_property_value("attitude/theta-deg")
            hdg = f.get_property_value("attitude/psi-deg")
            p_ = f.get_property_value("velocities/p-radps")
            q_ = f.get_property_value("velocities/q-radps")
            r_ = f.get_property_value("velocities/r-radps")
            # v-dead 现象：r_=0 长期，故 P 项自动退化为 0 → SAS 仅姿态环 + hdg P
            ail = clamp(self.roll_sign * (0.06 * (roll - cmd_roll)), -0.3, 0.3)
            ele = clamp(PITCH_SIGN * (0.06 * (pitch - cmd_pitch)) + 0.0, -0.42, 0.42)
            if hdg_ref is not None:
                rud = clamp(HDG_SIGN * (0.08 * wrap180(hdg - hdg_ref)), -0.6, 0.6)
            else:
                rud = 0.0
            f.set_property_value("fcs/aileron-cmd-norm", ail)
            f.set_property_value("fcs/elevator-cmd-norm", ele)
            f.set_property_value("fcs/rudder-cmd-norm", rud)
            f.run()
            self.t += DT


def spool(h):
    h.start()
    for _ in range(int(90 / DT)):
        h.step(n=1, hdg_ref=None)
        if int(h.t / DT) % 100 == 0:
            s = h.st()
            if s["nan"]:
                return False
            if 495 <= s["rpm"] <= 565 and h.t > 12:
                return True
    return 495 <= h.st()["rpm"] <= 565


def ascend(h, hdg_ref, target_ft=15, tol_ft=3, max_s=30):
    h.set_coll(0.74)
    climb = 0.0
    s = h.st()
    t0 = h.t
    while h.t - t0 < max_s:
        h.step(n=1, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=hdg_ref)
        s = h.st()
        if s["nan"]:
            return False, s
        climb += s["vs_fpm"] / 60.0 * DT
        if int((h.t - t0) / DT) % 100 == 0:
            s = h.st()
            if climb > target_ft - tol_ft:
                return True, s
            if climb < 1 and h.t - t0 > 10:
                return False, s
    return False, s


def run_forward_sweep(h, hdg_ref, pitch_cmd, total_s=12.0, sample_hz=20):
    h.fdm.set_property_value("fcs/collective-cmd-norm", min(0.72 + 0.01 * abs(pitch_cmd) / 3, 0.82))
    t0 = h.t
    n = int(total_s)
    peak_p = 0.0  # |phi| 峰
    peak_r = 0.0  # |dphi/dt|
    roll_prev = h.st()["roll"]
    peak_pitch = 0.0
    ve_sum = n_obs = 0
    pitch_sum = 0
    diverged = False
    s_diverge = None
    for k in range(int(total_s / DT)):
        h.step(n=1, cmd_roll=0.0, cmd_pitch=pitch_cmd, hdg_ref=hdg_ref)
        s = h.st()
        if s["nan"] or abs(s["roll"]) > 60 or abs(s["pitch"]) > 60:
            diverged = True
            s_diverge = s
            break
        peak_p = max(peak_p, abs(s["roll"]))
        peak_pitch = max(peak_pitch, abs(s["pitch"]))
        if k % int(1 / (DT * sample_hz)) == 0:
            ve_sum += s["ve_kts"]; pitch_sum += s["pitch"]; n_obs += 1
        roll_now = s["roll"]
        rd = abs((roll_now - roll_prev) / DT)
        peak_r = max(peak_r, rd)
        roll_prev = roll_now
    return {
        "ve_kts": round(ve_sum / max(n_obs, 1), 1),
        "theta_deg": round(pitch_sum / max(n_obs, 1), 2),
        "peak_roll_deg": round(peak_p, 1),
        "peak_roll_rate_dps": round(peak_r, 1),
        "peak_pitch_deg": round(peak_pitch, 1),
        "diverged": diverged,
        "end_state": s_diverge,
    }


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("=== T2 滚转-前飞耦合专项 ===")
    modes = [
        ("M0", "baseline",  None),
        ("M1", "FF roll-trim OFF",  None),
        ("M2", "FF yaw-trim OFF",  None),
        ("M3", "roll sign reversed (=-1)",  -1.0),  # M0 已改 +1，M3 对照原 = -1
    ]
    results = []
    for tag, label, sign in modes:
        print(f"\n--- {tag}: {label} (roll_sign={sign if sign else ROLL_SIGN}) ---")
        h = Heli(mode=tag.split()[0], roll_sign_override=sign)
        if not spool(h):
            print(f"  [FATAL] spool fail")
            results.append({"mode": tag, "label": label, "spool": False})
            continue
        # 悬停建立
        h.set_coll(0.72)
        hdg_ref = h.st()["hdg"]
        ok, _ = ascend(h, hdg_ref, target_ft=15)
        if not ok:
            print(f"  [WARN] ascend fail (mode {tag} may not reach cruise)")
        # 前飞 3 档（按 0.2-1a 失败的 23kt 前后）
        for pitch_cmd in (-6, -9, -12):
            r = run_forward_sweep(h, hdg_ref, pitch_cmd, total_s=10.0)
            r["mode"] = tag; r["label"] = label
            r["pitch_cmd"] = pitch_cmd
            results.append(r)
            d = " [发散]" if r["diverged"] else ""
            print(f"  cmd={pitch_cmd:+d}°: ve={r['ve_kts']:5.1f}kt theta={r['theta_deg']:+5.1f}° "
                  f"peak|roll|={r['peak_roll_deg']:5.1f}° rate={r['peak_roll_rate_dps']:5.1f}°/s{d}")
    out = OUT / f"probe03_{stamp}.json"
    out.write_text(json.dumps({"stamp": stamp, "results": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== T2 汇总 ===")
    for r in results:
        if r.get("spool") is False:
            print(f"  {r['mode']}: SPOOL FAIL"); continue
        d = "DIV" if r.get("diverged") else "ok "
        print(f"  {r['mode']:>3} cmd={r['pitch_cmd']:+d}°: peak|roll|={r['peak_roll_deg']:5.1f}° "
              f"rate={r['peak_roll_rate_dps']:5.1f}°/s [{d}]")
    print("\n[产物]", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
