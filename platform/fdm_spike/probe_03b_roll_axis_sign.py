#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_03b_roll_axis_sign.py — T2 收官：直接阶跃 ail 杆测滚转通道斜率

不再做“前飞中发散”，而是把前飞因素完全剥离开：悬停+小幅 ail 阶跃±0.05/±0.10/±0.15，
记录 t0..t0+3s 的滚转角度轨迹，求线性斜率 dphi/dt vs ail 的回归斜率。
- 斜率 >0（正 ail 产生正 roll 增）→ 当前 ROLL_SIGN=-1 是错的（副翼的物理是同向：正 ail=正滚）
- 斜率 <0（正 ail 产生负 roll 增）→ 当前 ROLL_SIGN=-1 是对的

跑前 6s 强制 0 ail 把 roll 归零做基线。每档 ail 阶跃后恢复 0 看回稳。
产物 probe_results/probe03b_<stamp>.json
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

# 候选 roll 符号都要试一遍
SIGNS = [-1.0, +1.0]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Heli:
    def __init__(self, roll_sign):
        self.fdm = jsbsim.FGFDMExec(ROOT, None)
        if not self.fdm.load_model("helispike", True):
            raise RuntimeError
        self.fdm.load_ic("reset00.xml", True)
        self.fdm.run_ic()
        self.t = 0.0
        self.roll_sign = roll_sign

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
        out = {"t": self.t,
            "roll": f.get_property_value("attitude/phi-deg") if f.get_property_value("attitude/phi-deg") is not None else 0.0,
            "pitch": f.get_property_value("attitude/theta-deg") if f.get_property_value("attitude/theta-deg") is not None else 0.0,
            "ele": f.get_property_value("fcs/elevator-cmd-norm") or 0,
            "rud": f.get_property_value("propulsion/engine[1]/antitorque-ctrl-rad") or 0,
            "ail": f.get_property_value("fcs/aileron-cmd-norm") or 0,
            "vs_fpm": (f.get_property_value("velocities/h-dot-fps") or 0) * 60.0,
            "rpm": f.get_property_value("propulsion/engine[0]/rotor-rpm") or 0,
        }

    def step(self, n=1, ail_cmd=0.0, ele_cmd=0.0, hdg_ref=None,
             ele_sign=+1.0, rud_sign=+1.0):
        f = self.fdm
        for _ in range(n):
            p_ = f.get_property_value("velocities/p-radps")
            q_ = f.get_property_value("velocities/q-radps")
            r_ = f.get_property_value("velocities/r-radps")
            roll = f.get_property_value("attitude/phi-deg")
            pitch = f.get_property_value("attitude/theta-deg")
            hdg = f.get_property_value("attitude/psi-deg")
            # 阶跃信号：直接设 ail_cmd，无速率阻尼（r-dead 现象使 P 项恒 0）
            ail = clamp(ail_cmd, -0.3, 0.3)
            ele = clamp(ele_sign * 0.10 * q_ * D2R + ele_cmd, -0.3, 0.3)
            if hdg_ref is not None:
                rud = clamp(rud_sign * 0.10 * wrap(hdg - hdg_ref), -0.6, 0.6)
            else:
                rud = 0.0
            f.set_property_value("fcs/aileron-cmd-norm", ail)
            f.set_property_value("fcs/elevator-cmd-norm", ele)
            f.set_property_value("fcs/rudder-cmd-norm", rud)
            f.run()
            self.t += DT


def wrap(x):
    return (x + 180.0) % 360.0 - 180.0


def spool(h):
    h.start()
    for _ in range(int(90 / DT)):
        h.step(n=1)
        if int(h.t / DT) % 100 == 0:
            try:
                rpm = (h.st() or {}).get("rpm") or 0
            except Exception:
                rpm = 0
            if 495 <= rpm <= 565 and h.t > 12:
                return True
    try:
        rpm = (h.st() or {}).get("rpm") or 0
    except Exception:
        rpm = 0
    return 495 <= rpm <= 565


def ascend(h, hdg_ref, target_ft=15, max_s=30):
    h.set_coll(0.74)
    climb = 0.0
    s = h.st()
    t0 = h.t
    while h.t - t0 < max_s:
        h.step(n=1, hdg_ref=hdg_ref)
        s = h.st()
        if s["roll"] is None or math.isnan(s["roll"]):
            return False, s
        climb += s["vs_fpm"] / 60.0 * DT
        if climb > target_ft:
            return True, s
    return False, s


def step_response(h, ail_target, sign, t_window=(0.0, 2.0)):
    """悬停中 2s 强制 ail=target，记录 roll 轨迹，线性回归斜率。"""
    hdg_ref = h.st()["hdg"]  # 视作零偏航
    # 基线 1s
    roll0 = []
    for _ in range(int(1.0 / DT)):
        h.step(n=1, ail_cmd=0.0, hdg_ref=hdg_ref)
        roll0.append(h.st()["roll"])
    r0 = sum(roll0) / len(roll0)
    # 注入 2s
    samples = []
    for _ in range(int(t_window[1] / DT)):
        h.step(n=1, ail_cmd=ail_target, hdg_ref=hdg_ref)
        samples.append(h.st()["roll"])
    # 线性回归: dphi vs t
    n = len(samples)
    s_t = sum((i * DT) for i in range(n))
    s_p = sum(samples)
    s_tp = sum((i * DT) * p for i, p in enumerate(samples))
    s_t2 = sum((i * DT) ** 2 for i in range(n))
    slope = (n * s_tp - s_t * s_p) / (n * s_t2 - s_t * s_t) if n * s_t2 != s_t * s_t else 0
    # 恢复 1s
    for _ in range(int(1.0 / DT)):
        h.step(n=1, ail_cmd=0.0, hdg_ref=hdg_ref)
    return {"r0": round(r0, 3), "slope_dps": round(slope, 3), "r_end": round(samples[-1], 3),
            "r_delta": round(samples[-1] - r0, 3)}


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("=== T2 收官：副翼阶跃测滚转通道斜率（剥离前飞因素）===")
    results = []
    for sign in SIGNS:
        print(f"\n--- roll_sign={sign:+.0f} ---")
        h = Heli(roll_sign=sign)
        if not spool(h):
            print("  [FATAL] spool fail")
            results.append({"roll_sign": sign, "spool": False, "st": None})
            continue
        last = results[-1] if results else {}
        hdg_val = (last.get("st") or h.st() or {}).get("hdg", 0.0)
        ok, s = ascend(h, hdg_val, target_ft=15)
        if not ok:
            print("  [WARN] ascend partial")
        hdg_ref = (h.st() or {}).get("hdg", 0.0)
        # 3 档 ail 阶跃
        for ail_target in (0.05, 0.10, 0.15):
            r = step_response(h, ail_target, sign)
            r["roll_sign"] = sign; r["ail_target"] = ail_target
            results.append(r)
            print(f"  ail_cmd={ail_target:+.2f}  r0={r['r0']:+.3f}  slope={r['slope_dps']:+6.3f}°/s  "
                  f"r_end={r['r_end']:+.3f} Δr={r['r_delta']:+.3f}")
        # 反向档
        for ail_target in (-0.05, -0.10, -0.15):
            r = step_response(h, ail_target, sign)
            r["roll_sign"] = sign; r["ail_target"] = ail_target
            results.append(r)
            print(f"  ail_cmd={ail_target:+.2f}  r0={r['r0']:+.3f}  slope={r['slope_dps']:+6.3f}°/s  "
                  f"r_end={r['r_end']:+.3f} Δr={r['r_delta']:+.3f}")
    out = OUT / f"probe03b_{stamp}.json"
    out.write_text(json.dumps({"stamp": stamp, "results": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== T2 收官汇总 ===")
    for sign in SIGNS:
        rs = [r for r in results if r.get("roll_sign") == sign and "slope_dps" in r]
        if not rs:
            continue
        slopes = [r["slope_dps"] for r in rs if r["ail_target"] > 0]
        sign_str = "同向" if all(s > 0 for s in slopes if s != 0) else "反向" if all(s < 0 for s in slopes if s != 0) else "混合"
        avg = sum(slopes) / max(len(slopes), 1)
        print(f"roll_sign={sign:+.0f}: 平均斜率 {avg:+.3f}°/s (正 ail -> {('正' if avg>0 else '负')} roll 增量, {sign_str})")
    print("\n判定:")
    # 取绝对值更大的方向作为实际物理方向
    s_neg = sum(r["slope_dps"] for r in results if r.get("roll_sign") == -1 and r.get("ail_target", 0) > 0) / max(sum(1 for r in results if r.get("roll_sign") == -1 and r.get("ail_target", 0) > 0), 1)
    s_pos = sum(r["slope_dps"] for r in results if r.get("roll_sign") == +1 and r.get("ail_target", 0) > 0) / max(sum(1 for r in results if r.get("roll_sign") == +1 and r.get("ail_target", 0) > 0), 1)
    print(f"  当前 ROLL_SIGN=-1: 正 ail -> 实际 roll 增量斜率 {s_neg:+.3f}°/s")
    print(f"  ROLL_SIGN=+1:   正 ail -> 实际 roll 增量斜率 {s_pos:+.3f}°/s")
    print("  -> " + ("正 ail 推正 roll，应 ROLL_SIGN=+1" if abs(s_pos) > abs(s_neg) and s_pos > 0
          else "正 ail 推负 roll，ROLL_SIGN=-1 正确" if abs(s_neg) > abs(s_pos) and s_neg < 0
          else "数据噪声，需重复"))
    print("\n[产物]", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
