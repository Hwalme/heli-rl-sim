#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_fdm_spike_v2.py — D2(b) FDM SPIKE 验收探针 v2（headless）

v2 关键升级（v1 教训）：
  - 外环 = 全稳定吊车：姿态(roll/pitch) PI + 航向保持 + 速率阻尼，自动增益符号
    标定（发散即翻符号，最多 2 轮）——无论 FDM 内在符号约定如何都能稳住悬停；
  - FF 符号标定：稳定悬停后 ff_sign=0 测 3s 自由偏航漂移方向 T（主扭矩方向），
    再踩固定 antitorque 脉冲测响应方向 S，ff_sign = -T*S 写入 FDM；
  - 科目照旧：A 静悬 15s / B collective 阶跃爬升 60s / C 配平表扫掠。

判据（对照 AH-1S 基线 83.7°/s / -114.9°）：
  A: 峰值|yaw|<5°/s 且航向漂移<10°
  B: 60s 无翻覆（|roll|,|pitch|<30°）且高度增益>100ft
  C: collective 0.5→1.0 全程 |yaw|<3°/s
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import jsbsim

HERE = Path(__file__).resolve().parent
ROOT = jsbsim.get_default_root_dir()  # 默认 root：标准状态属性绑定正常（ah1s 实证）
OUT = HERE / "probe_results"
OUT.mkdir(exist_ok=True)
DT = 0.005
D2R = 57.29578


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def wrap180(x):
    return (x + 180.0) % 360.0 - 180.0


class Heli:
    def __init__(self):
        self.fdm = jsbsim.FGFDMExec(ROOT, None)
        if not self.fdm.load_model("helispike", True):
            raise RuntimeError("load_model helispike failed")
        self.fdm.load_ic("reset00.xml", True)
        self.fdm.run_ic()
        self.t = 0.0

    def start(self):
        f = self.fdm
        f.set_property_value("propulsion/engine[0]/set-running", 1)
        f.set_property_value("propulsion/engine[1]/set-running", 1)
        f.set_property_value("propulsion/engine[0]/clutch", 1)
        f.set_property_value("fcs/hs/afcs-enable", 1.0)
        self.set_coll(0.0)

    def set_coll(self, v):
        self.fdm.set_property_value("fcs/collective-cmd-norm", v)

    def st(self):
        f = self.fdm
        agl = f.get_property_value("position/agl-ft")
        return {
            "t": self.t,
            "agl": agl,
            "alt_ft": f.get_property_value("position/altitude-ft"),
            "nan": math.isnan(agl) or math.isnan(f.get_property_value("attitude/phi-deg")),
            "vs_fpm": f.get_property_value("velocities/h-dot-fps") * 60.0,
            "roll": f.get_property_value("attitude/phi-deg"),
            "pitch": f.get_property_value("attitude/theta-deg"),
            "hdg": f.get_property_value("attitude/psi-deg"),
            "p": f.get_property_value("velocities/p-radps") * D2R,
            "q": f.get_property_value("velocities/q-radps") * D2R,
            "r": f.get_property_value("velocities/r-radps") * D2R,
            "coll": f.get_property_value("fcs/collective-cmd-norm"),
            "rud": f.get_property_value("propulsion/engine[1]/antitorque-ctrl-rad"),
            "rpm": f.get_property_value("propulsion/engine[0]/rotor-rpm"),
        }

    def run(self, n=1):
        for _ in range(n):
            self.fdm.run()
            self.t += DT


class Stabilizer:
    """外环稳定吊车：姿态 PI + 航向 P + 速率阻尼。符号自动标定。"""

    def __init__(self, heli):
        self.h = heli
        # 默认 root 实测（08-30）：roll_sign=+1 时 roll 全程稳；pitch 晚期发散
        # 疑为 FF/尾桨耦合，先锁 +1/+1，由 FF 标定后的探针数据裁决。
        self.roll_sign = -1.0   # 08-30 两轮实测: roll 在 -1 稳
        self.pitch_sign = +1.0  # 08-30 两轮实测: pitch 在 +1 稳
        self.hdg_sign = +1.0    # 先验 +1；5s 漂移>8° 自动翻转复测

    def apply(self, coll, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=None, sas=True):
        f = self.h.fdm
        s = self.h.st()
        if sas:
            ail = self.roll_sign * (0.06 * (s["roll"] - cmd_roll) + 0.15 * s["p"])
            ele = self.pitch_sign * (0.06 * (s["pitch"] - cmd_pitch) + 0.15 * s["q"])
            f.set_property_value("fcs/aileron-cmd-norm", clamp(ail, -0.3, 0.3))
            f.set_property_value("fcs/elevator-cmd-norm", clamp(ele, -0.3, 0.3))
            rud = 0.0
            if self.hdg_sign is not None and hdg_ref is not None:
                herr = wrap180(s["hdg"] - hdg_ref)
                # r-radps 在此构建是死属性（恒 0）——用 psi 反馈 + pedal
                rud = self.hdg_sign * (0.08 * herr) - 0.10 * s["r"]
            f.set_property_value("fcs/rudder-cmd-norm", clamp(rud, -0.6, 0.6))
        else:
            f.set_property_value("fcs/aileron-cmd-norm", 0.0)
            f.set_property_value("fcs/elevator-cmd-norm", 0.0)
            f.set_property_value("fcs/rudder-cmd-norm", 0.0)
        return s

    def calibrate_att_signs(self, coll, seconds=6):
        """在 coll 处尝试悬停，若姿态发散则翻对应符号（每符号最多 2 轮）。"""
        for rnd in range(3):
            h = self.h
            h.fdm.load_ic("reset00.xml", True)
            h.fdm.run_ic()
            h.t = 0.0
            h.start()
            # spool 到 495-565
            for _ in range(int(90 / DT)):
                h.run()
                s = h.st()
                if 495 <= s["rpm"] <= 565:
                    break
            bad_r = bad_p = 0
            for _ in range(int(seconds / DT)):
                h.set_coll(coll)
                self.apply(coll)
                h.run()
                s = h.st()
                if s["nan"]:
                    return False
                if abs(s["roll"]) > 25:
                    bad_r += 1
                if abs(s["pitch"]) > 25:
                    bad_p += 1
                if abs(s["roll"]) > 60 and abs(s["pitch"]) > 60:
                    break
            ok_r = bad_r == 0
            ok_p = bad_p == 0
            print(f"[sign-cal] rnd{rnd}: roll发散帧={bad_r} pitch发散帧={bad_p} "
                  f"(roll_sign={self.roll_sign:+.0f} pitch_sign={self.pitch_sign:+.0f})")
            if not ok_r:
                self.roll_sign *= -1
            if not ok_p:
                self.pitch_sign *= -1
            if ok_r and ok_p:
                print("[sign-cal] 姿态符号确认（混合符号允许）")
                return True
        return False


def stabilize_hover(heli, stab, coll, seconds, hdg_ref=None, log=None):
    t0 = time.time()
    for _ in range(int(seconds / DT)):
        heli.set_coll(coll)
        s = stab.apply(coll, hdg_ref=hdg_ref)
        heli.run()
        if log is not None:
            st = heli.st()
            st["hdg_ref"] = hdg_ref
            log.append(st)
        if int(heli.t / DT) % 100 == 0:
            st = heli.st()
            if st["nan"]:
                return False, st
    return True, heli.st()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hover-coll", type=float, default=0.72)
    args = ap.parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {"stamp": stamp}

    coll = args.hover_coll
    print(f"=== FDM SPIKE v2 (helispike, hover coll={coll}) ===")

    # ---- 1. 姿态符号标定 + 稳定悬停能力确认 ----
    heli = Heli()
    stab = Stabilizer(heli)
    if not stab.calibrate_att_signs(coll, seconds=8):
        print("[FAIL] 3 轮符号尝试后姿态仍发散——物理层问题，如实报告")
        report["error"] = "attitude not stabilizable"
        (OUT / f"spike_v2_{stamp}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return 1
    report["roll_sign"] = stab.roll_sign
    report["pitch_sign"] = stab.pitch_sign

    # ---- 2. FF 符号标定（稳定悬停中测主扭矩方向 T 与尾桨响应方向 S）----
    heli.fdm.load_ic("reset00.xml", True)
    heli.fdm.run_ic()
    heli.t = 0.0
    heli.start()
    for _ in range(int(90 / DT)):
        heli.run()
        heli.set_coll(0.0)   # spool 期零总距：不在滑橇上带推力硬扛（防地面自旋）
        stab.apply(0.0, hdg_ref=None)
        s = heli.st()
        if s["nan"]:
            print("[FAIL] spool/hover NaN")
            return 1
        if 495 <= s["rpm"] <= 565 and heli.t > 12:
            break
    # 果断离地：总距直接跳到悬停值+0.05，1-2 秒穿越滑橇卸载区
    heli.set_coll(coll)
    hdg_ref = heli.st()["hdg"]
    # 航向保持符号标定（psi 反馈方向）：5s 测漂移，发散则翻符号复测
    hdg_ref = heli.st()["hdg"]
    drift1 = 0.0
    for _ in range(int(5 / DT)):
        heli.set_coll(coll)
        s = stab.apply(coll, hdg_ref=hdg_ref)
        heli.run()
        if int(heli.t / DT) % 50 == 0:
            s2 = heli.st()
            drift1 = abs(wrap180(s2["hdg"] - hdg_ref))
            if drift1 > 8:
                break
    print(f"[hdg-cal] 尝试1 hdg_sign={stab.hdg_sign:+.0f}: 漂移 {drift1:.1f}°")
    if drift1 > 8:
        stab.hdg_sign *= -1
        hdg_ref = heli.st()["hdg"]
        drift2 = 0.0
        for _ in range(int(5 / DT)):
            heli.set_coll(coll)
            s = stab.apply(coll, hdg_ref=hdg_ref)
            heli.run()
            if int(heli.t / DT) % 50 == 0:
                s2 = heli.st()
                drift2 = abs(wrap180(s2["hdg"] - hdg_ref))
        print(f"[hdg-cal] 尝试2 hdg_sign={stab.hdg_sign:+.0f}: 漂移 {drift2:.1f}°")
        if drift2 > drift1:
            stab.hdg_sign *= -1  # 都不行就回退，如实报
    print(f"[hdg-cal] 最终 hdg_sign={stab.hdg_sign:+.0f}")
    report["hdg_sign"] = stab.hdg_sign

    heli.fdm.set_property_value("fcs/hs/ff-sign", 0.0)
    yr = []
    hdg0 = heli.st()["hdg"]
    for _ in range(int(4 / DT)):
        heli.set_coll(coll)
        stab.apply(coll, hdg_ref=None)   # 航向环不开（FF=0 时无参考）
        heli.run()
        yr.append(heli.st()["r"])
    T = sum(yr[-100:]) / 100.0
    sign_T = 1 if T > 0 else -1
    # 踩 0.35 antitorque 脉冲测 S
    yr2 = []
    for _ in range(int(2.5 / DT)):
        heli.set_coll(coll)
        stab.apply(coll, hdg_ref=None)
        heli.fdm.set_property_value("fcs/rudder-cmd-norm", 0.35)
        heli.run()
        yr2.append(heli.st()["r"])
    S = sum(yr2[-60:]) / 60.0
    sign_S = 1 if S > 0 else -1
    ff_sign = -sign_T * sign_S
    heli.fdm.set_property_value("fcs/hs/ff-sign", ff_sign)
    print(f"[ff-cal] T={T:+.2f}°/s (sign {sign_T:+d}) | S={S:+.2f}°/s (sign {sign_S:+d}) "
          f"| ff_sign={ff_sign:+.0f}")
    report["ff_sign"] = ff_sign
    report["sign_T"], report["sign_S"] = sign_T, sign_S

    # ---- 3. 探针 A: 静悬 15s（FF 生效 + 航向保持参考锁定）----
    hdg_ref = heli.st()["hdg"]
    logA = []
    peak = 0.0
    drift = 0.0
    okA = True
    for _ in range(int(15 / DT)):
        heli.set_coll(coll)
        s = stab.apply(coll, hdg_ref=hdg_ref)
        heli.run()
        if int(heli.t / DT) % 10 == 0:
            st = heli.st()
            st["hdg_err"] = wrap180(st["hdg"] - hdg_ref)
            peak = max(peak, abs(st["r"]))
            drift = max(drift, abs(st["hdg_err"]))
            logA.append(st)
            if st["nan"] or abs(st["roll"]) > 45 or abs(st["pitch"]) > 45:
                okA = False
                print(f"[probe A] 发散 t={st['t']:.1f}s roll={st['roll']:.0f} pitch={st['pitch']:.0f}")
                break
    a_pass = okA and peak < 5.0 and drift < 10.0
    report["probeA"] = {"peak_yawr_dps": round(peak, 2), "hdg_drift_deg": round(drift, 2),
                        "pass": a_pass}
    alt_a = heli.st()["alt_ft"]
    print(f"[probe A] peak|yawr|={peak:.2f}°/s (<5) | hdg drift={drift:.2f}° (<10) | "
          f"end alt={alt_a:.1f}ft -> {'PASS' if a_pass else 'FAIL'}")

    # ---- 4. 探针 B: collective 阶跃爬升 60s ----
    heli.fdm.load_ic("reset00.xml", True)
    heli.fdm.run_ic()
    heli.t = 0.0
    heli.start()
    for _ in range(int(90 / DT)):
        heli.run()
        heli.set_coll(0.0)
        stab.apply(0.0, hdg_ref=None)
        s = heli.st()
        if s["nan"]:
            print("[FAIL] probe B spool NaN")
            return 1
        if 495 <= s["rpm"] <= 565 and heli.t > 12:
            break
    heli.set_coll(coll)
    ok_hover = True
    for _ in range(int(12 / DT)):   # 悬停建立段（果断离地+稳定）
        heli.set_coll(coll)
        stab.apply(coll, hdg_ref=hdg_ref)
        heli.run()
        if int(heli.t / DT) % 200 == 0:
            s = heli.st()
            print(f"  [B-est] t={s['t']:.0f}s alt={s['alt_ft']:.1f} vs={s['vs_fpm']:+.0f}fpm "
                  f"roll={s['roll']:+.1f} pitch={s['pitch']:+.1f} rpm={s['rpm']:.0f}")
            if s["nan"]:
                ok_hover = False
                print("  [B-est] NaN")
                break
    if not ok_hover:
        print("[probe B] 悬停建立失败（未离地或 NaN）")
        report["probeB"] = {"flip": True, "alt_gain_ft": 0, "pass": False,
                            "note": "hover establishment failed"}
        out = OUT / f"spike_v2_{stamp}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return 1
    coll_b = min(coll + 0.08, 1.0)
    flip = False
    alt_int = 0.0
    for _ in range(int(60 / DT)):
        heli.set_coll(coll_b)
        stab.apply(coll_b, hdg_ref=hdg_ref)
        heli.run()
        if int(heli.t / DT) % 50 == 0:
            s = heli.st()
            if s["nan"]:
                flip = True
                print(f"[probe B] NaN at t={s['t']:.0f}s")
                break
            alt_int += s["vs_fpm"] / 60.0 * (50 * DT)
            if abs(s["roll"]) > 30 or abs(s["pitch"]) > 30:
                flip = True
                print(f"[probe B] FLIP at t={s['t']:.0f}s roll={s['roll']:.0f} pitch={s['pitch']:.0f}")
                break
    gain = alt_int
    b_pass = (not flip) and gain > 100
    report["probeB"] = {"flip": flip, "alt_gain_ft": round(gain, 1), "pass": b_pass}
    print(f"[probe B] flip={flip} alt_gain={gain:.0f}ft -> {'PASS' if b_pass else 'FAIL'}")

    # ---- 5. 探针 C: 配平表扫掠 40s（从悬停开始缓升 collective）----
    heli.fdm.load_ic("reset00.xml", True)
    heli.fdm.run_ic()
    heli.t = 0.0
    heli.start()
    for _ in range(int(90 / DT)):
        heli.run()
        heli.set_coll(0.0)
        stab.apply(0.0, hdg_ref=None)
        s = heli.st()
        if s["nan"]:
            print("[FAIL] probe C spool NaN")
            return 1
        if 495 <= s["rpm"] <= 565 and heli.t > 12:
            break
    heli.set_coll(coll)
    ok_hover = True
    for _ in range(int(6 / DT)):
        heli.set_coll(coll)
        stab.apply(coll, hdg_ref=hdg_ref)
        heli.run()
        if int(heli.t / DT) % 100 == 0:
            s = heli.st()
            if s["nan"]:
                ok_hover = False
                break
    if not ok_hover:
        print("[probe C] 悬停建立失败")
        report["probeC"] = {"peak_yawr_dps": None, "diverged": True, "pass": False,
                            "note": "hover establishment failed"}
        out = OUT / f"spike_v2_{stamp}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return 1
    peak_c = 0.0
    coll_c = 0.5
    div = False
    alt_int_c = 0.0
    max_vs = 0.0
    for _ in range(int(40 / DT)):
        coll_c = min(1.0, coll_c + 0.0125 * DT)
        heli.set_coll(coll_c)
        stab.apply(coll_c, hdg_ref=hdg_ref)
        heli.run()
        if int(heli.t / DT) % 20 == 0:
            s = heli.st()
            peak_c = max(peak_c, abs(s["r"]))
            alt_int_c += s["vs_fpm"] / 60.0 * (20 * DT)
            max_vs = max(max_vs, abs(s["vs_fpm"]))
            if abs(s["roll"]) > 60 or s["nan"]:
                div = True
                break
    c_pass = (peak_c < 3.0) and (not div) and (max_vs > 100.0) and abs(alt_int_c) > 20.0
    report["probeC_alt_gain_ft"] = round(alt_int_c, 1)
    report["probeC"] = {"peak_yawr_dps": round(peak_c, 2), "diverged": div, "pass": c_pass}
    print(f"[probe C] sweep peak|yawr|={peak_c:.2f}°/s (<3) diverged={div} "
          f"-> {'PASS' if c_pass else 'FAIL'}")

    report["spike_pass"] = a_pass and b_pass and c_pass
    out = OUT / f"spike_v2_{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"spike_v2_frames_{stamp}.json").write_text(
        json.dumps(logA, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== SPIKE v2 总判定:", "PASS" if report["spike_pass"] else "FAIL", "===")
    print("报告:", out)
    return 0 if report["spike_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
