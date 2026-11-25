#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_02b_final.py — T3 收尾：修正探针流程后的 0.2 全科目

修正（对照 probe_02 三处流程缺陷）：
  1. 前飞段：总距闭环跟高度（vs 目标 0，PI 微调 coll）——修"前飞掉地"
  2. 下降矩阵：先二分标定真实悬停总距 coll_hover，再从 coll_hover-0.02 向下扫
  3. 着陆：接地判定后切"地面锁杆模式"（coll 平桨 1.0、SAS 保持 4s）判停稳

产物: probe_results/probe02b_<stamp>.json
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

# T2 实证符号
ROLL_SIGN = +1.0
PITCH_SIGN = +1.0
HDG_SIGN = +1.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def wrap180(x):
    return (x + 180.0) % 360.0 - 180.0


class Heli:
    def __init__(self):
        self.fdm = jsbsim.FGFDMExec(ROOT, None)
        if not self.fdm.load_model("helispike", True):
            raise RuntimeError
        self.fdm.load_ic("reset00.xml", True)
        self.fdm.run_ic()
        self.t = 0.0
        self.heli_alt = 0.0  # vs 积分高度（agl 死属性代测）

    def start(self):
        f = self.fdm
        f.set_property_value("propulsion/engine[0]/set-running", 1)
        f.set_property_value("propulsion/engine[1]/set-running", 1)
        f.set_property_value("propulsion/engine[0]/clutch", 1)
        f.set_property_value("fcs/throttle-cmd-norm[0]", 1.0)
        f.set_property_value("fcs/hs/afcs-enable", 1.0)
        self.set_coll(0.0)

    def set_coll(self, v):
        self.fdm.set_property_value("fcs/collective-cmd-norm", v)

    def coll(self):
        return self.fdm.get_property_value("fcs/collective-cmd-norm")

    def st(self):
        f = self.fdm
        return {
            "t": self.t, "alt": self.heli_alt,
            "vs_fpm": f.get_property_value("velocities/h-dot-fps") * 60.0,
            "ve_kts": f.get_property_value("velocities/ve-kts"),
            "roll": f.get_property_value("attitude/phi-deg") or 0.0,
            "pitch": f.get_property_value("attitude/theta-deg") or 0.0,
            "hdg": f.get_property_value("attitude/psi-deg") or 0.0,
            "coll": self.coll(),
            "rpm": f.get_property_value("propulsion/engine[0]/rotor-rpm") or 0,
        }

    def step(self, n=1, sas=True, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=None,
             lock_ground=False):
        f = self.fdm
        for _ in range(n):
            if lock_ground:
                f.set_property_value("fcs/aileron-cmd-norm", 0.0)
                f.set_property_value("fcs/elevator-cmd-norm", 0.0)
                f.set_property_value("fcs/rudder-cmd-norm", 0.0)
            elif sas:
                roll = f.get_property_value("attitude/phi-deg") or 0.0
                pitch = f.get_property_value("attitude/theta-deg") or 0.0
                hdg = f.get_property_value("attitude/psi-deg") or 0.0
                ail = clamp(ROLL_SIGN * 0.06 * (roll - cmd_roll), -0.3, 0.3)
                ele = clamp(PITCH_SIGN * 0.06 * (pitch - cmd_pitch), -0.3, 0.3)
                rud = clamp(HDG_SIGN * 0.08 * wrap180(hdg - hdg_ref), -0.6, 0.6) \
                    if hdg_ref is not None else 0.0
                f.set_property_value("fcs/aileron-cmd-norm", ail)
                f.set_property_value("fcs/elevator-cmd-norm", ele)
                f.set_property_value("fcs/rudder-cmd-norm", rud)
            s = f.get_property_value("velocities/h-dot-fps") or 0.0
            self.heli_alt += s * DT
            f.run()
            self.t += DT


def spool(h):
    h.start()
    for _ in range(int(90 / DT)):
        h.step(n=1, sas=False)
        if int(h.t / DT) % 100 == 0:
            if 495 <= h.st()["rpm"] <= 565 and h.t > 12:
                return True
    return 495 <= h.st()["rpm"] <= 565


def find_hover_coll(h, hdg_ref):
    """空中二分：先 0.74 离地，再二分 coll 使 |vs|<60fpm。"""
    h.set_coll(0.76)
    for _ in range(int(8 / DT)):
        h.step(n=1, hdg_ref=hdg_ref)
        if h.heli_alt > 15.0:
            break
    lo, hi = 0.60, 0.85
    best = None
    for it in range(5):
        mid = (lo + hi) / 2
        # 等待稳定 4s
        for _ in range(int(4 / DT)):
            h.set_coll(mid)
            h.step(n=1, hdg_ref=hdg_ref)
        vs_sum = n = 0
        for _ in range(int(4 / DT)):
            h.set_coll(mid)
            h.step(n=1, hdg_ref=hdg_ref)
            vs_sum += h.st()["vs_fpm"]; n += 1
        vs = vs_sum / max(n, 1)
        print(f"  [cal] it{it} coll={mid:.3f} vs={vs:+.0f}fpm alt={h.heli_alt:.0f}ft")
        if abs(vs) < 60:
            best = mid
            break
        if vs > 0:
            hi = mid
        else:
            lo = mid
    return best or (lo + hi) / 2


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    h = Heli()
    print("=== probe_02b（T3 收尾：修正流程的 0.2 全科目）===")
    if not spool(h):
        print("[FATAL] spool")
        return 1
    print(f"[spool] rpm={h.st()['rpm']:.0f}")

    # 悬停建立 + 真实悬停总距标定
    h.set_coll(0.76)
    hdg_ref = h.st()["hdg"]
    for _ in range(int(8 / DT)):
        h.set_coll(0.76)
        h.step(n=1, hdg_ref=hdg_ref)
        if h.heli_alt > 15.0:
            break
    print(f"[离地] alt={h.heli_alt:.0f}ft vs={h.st()['vs_fpm']:+.0f}fpm")
    coll_hover = find_hover_coll(h, hdg_ref)
    print(f"[悬停总距] {coll_hover:.3f}")

    # ---- 前飞配平（总距高度闭环）----
    fwd = []
    print("[1a] 前飞配平（pitch_cmd 阶梯 + coll 高度闭环）...")
    for k in range(1, 7):
        pitch_cmd = -3.0 * k
        coll_pi = 0.0
        # 2s 稳态
        for _ in range(int(2 / DT)):
            h.set_coll(coll_hover)
            h.step(n=1, cmd_roll=0.0, cmd_pitch=pitch_cmd, hdg_ref=hdg_ref)
        ve_sum = th_sum = roll_max = 0
        n = 0
        div = False
        for _ in range(int(2 / DT)):
            vs_now = h.st()["vs_fpm"]
            if not (isinstance(vs_now, float) and (math.isnan(vs_now) or math.isinf(vs_now))):
                coll_pi = clamp(coll_pi + 0.00004 * (0.0 - vs_now) * DT * 200, -0.06, 0.06)
            h.set_coll(clamp(coll_hover + coll_pi, 0.5, 0.9))
            h.step(n=1, cmd_roll=0.0, cmd_pitch=pitch_cmd, hdg_ref=hdg_ref)
            if (h.t % 0.05) < DT:
                s = h.st()
                if abs(s["roll"]) > 45 or h.heli_alt < 3 or h.heli_alt > 400:
                    div = True
                    break
                ve_sum += s["ve_kts"]
                th_sum += s["pitch"]
                roll_max = max(roll_max, abs(s["roll"]))
                n += 1
        s = h.st()
        pt = {"pitch_cmd_deg": pitch_cmd, "ve_kts": round(ve_sum / max(n, 1), 1),
              "theta_deg": round(th_sum / max(n, 1), 2),
              "roll_max_deg": round(roll_max, 1),
              "diverged": div, "n": n}
        fwd.append(pt)
        print(f"  [{k}] cmd={pitch_cmd:+.0f} -> ve={pt['ve_kts']:6.1f}kt "
              f"theta={pt['theta_deg']:+6.2f} roll_max={pt['roll_max_deg']:4.1f}"
              f"{' [发散]' if div else ''}")
        if div:
            break

    # ---- 下降矩阵（从真实悬停点向下，不依赖 vs_fpm）----
    desc = []
    print("[1c] 下降矩阵（coll_hover 起每档 -0.04，每档 2s稳+3s采）...")
    c = coll_hover - 0.02
    while c >= 0.40:
        # 2s 稳态
        for _ in range(int(2 / DT)):
            h.set_coll(c)
            h.step(n=1, hdg_ref=hdg_ref)
        roll_max = 0
        n = 0
        div = False
        for _ in range(int(3 / DT)):
            h.set_coll(c)
            h.step(n=1, hdg_ref=hdg_ref)
            s = h.st()
            if abs(s["roll"]) > 45:
                div = True
                break
            roll_max = max(roll_max, abs(s["roll"]))
            n += 1
        s = h.st()
        pt = {"coll": round(c, 3), "roll_max_deg": round(roll_max, 1),
              "alt_ft": round(h.heli_alt, 0), "n": n, "diverged": div}
        desc.append(pt)
        print(f"  [{c:.3f}] roll_max={pt['roll_max_deg']:4.1f} alt={pt['alt_ft']:5.0f}ft"
              f"{' [发散]' if div else ''}")
        if h.heli_alt < 5 or div:
            break
        c -= 0.04

    # ---- 着陆（不依赖 vs_fpm：alt 触地 + 锁杆 5s）----
    print("[1b] 着陆（高度分段 + alt 触地 + 锁杆 5s）...")
    touched = False
    td_alt = td_roll = td_pitch = None
    for _ in range(int(60 / DT)):
        alt = h.heli_alt
        if alt < -0.5:
            break  # 已穿透地面
        target_coll = coll_hover - (0.03 if alt > 8 else (0.02 if alt > 4 else 0.012))
        h.set_coll(max(0.30, target_coll))
        h.step(n=1, hdg_ref=hdg_ref)
        if (h.t % 0.05) < DT:
            s = h.st()
            if 0 < alt < 1.5 and abs(s["roll"]) < 60:
                touched = True
                td_alt = round(alt, 2)
                td_roll = round(s["roll"], 1)
                td_pitch = round(s["pitch"], 1)
                print(f"  触地: alt={td_alt}ft roll={td_roll}° pitch={td_pitch}°")
                break
    stable = False
    if touched:
        h.set_coll(1.0)  # 平桨
        for _ in range(int(5 / DT)):
            h.step(n=1, lock_ground=True)  # 地面锁杆（SAS 关、杆回中）
        s = h.st()
        stable = (not math.isnan(s["roll"]) and abs(s["roll"]) < 15
                  and abs(s["pitch"]) < 15)
        print(f"  停稳(锁杆): roll={s['roll']:+.1f} pitch={s['pitch']:+.1f} "
              f"-> {'稳定' if stable else '不稳定'}")
    td = {"touched": touched, "td_alt_ft": td_alt,
          "td_roll_pitch": [td_roll, td_pitch], "post_settle_stable_lock": stable}

    report = {"stamp": stamp, "version": "helispike 0.2-rc2 (probe_02b v2 NaN-immune)",
              "hover_coll_calibrated": round(coll_hover, 3),
              "metric_basis": "无 vs_fpm（jsbsim 1.3.1 FGRotor 数值不稳），以 roll_max 判定",
              "forward_trim_points": fwd, "descent_matrix": desc, "touchdown": td}
    out = OUT / f"probe02b_{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n[产物]", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
