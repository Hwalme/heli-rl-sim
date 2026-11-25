#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_fdm_spike.py — D2(b) 自研 FDM SPIKE 验收探针（headless，不依赖 FG）

科目（对照 AH-1S 基线：峰值 |yaw| 83.7°/s、航向漂移 -114.9°）：
  0. 自动标定：spool-up → 找悬停总距（skid 卸载且 vs≈0 的 collective）
  A. 静悬 15s：手离杆（AFCS 有效），判据 峰值|yaw rate| < 5°/s 且 航向漂移 < 10°
  B. collective 阶跃爬升 60s：无翻覆（|roll|,|pitch| < 30°），高度上升
  C. 配平表扫掠：collective 0.5→1.0 缓扫（40s），全程 |yaw rate| < 3°/s

用法:
  python probe_fdm_spike.py           # 全部科目
  python probe_fdm_spike.py --skip-cal --hover-coll 0.70
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
ROOT = str(HERE / "jsbsim_root")
OUT = HERE / "probe_results"
OUT.mkdir(exist_ok=True)
DT = 0.005  # 200 Hz (Alouette 注记: 滑橇接触建议 >=200Hz)

PH = {0: "idle", 1: "ground", 2: "liftoff", 3: "climb", 4: "hover", 5: "forward"}


def wrap180(d):
    return (d + 180.0) % 360.0 - 180.0


class Heli:
    def __init__(self):
        self.fdm = jsbsim.FGFDMExec(ROOT, None)
        if not self.fdm.load_model("helispike", True):
            raise RuntimeError("load_model helispike failed")
        self.fdm.load_ic("reset00.xml", True)
        self.fdm.run_ic()
        self.fdm.set_dt(DT)
        self.t = 0.0

    def spool(self):
        f = self.fdm
        f.set_property_value("propulsion/engine[0]/set-running", 1)
        f.set_property_value("propulsion/engine[1]/set-running", 1)
        f.set_property_value("propulsion/engine[0]/clutch", 1)
        f.set_property_value("fcs/throttle-cmd-norm[0]", 1.0)
        f.set_property_value("fcs/hs/afcs-enable", 1.0)
        self.set_collective(0.0)
        for _ in range(int(90 / DT)):
            f.run()
            self.t += DT
            if int(self.t / DT) % 100 == 0:
                rpm = f.get_property_value("propulsion/engine[0]/rotor-rpm")
                if 495 <= rpm <= 565:
                    break
        rpm = f.get_property_value("propulsion/engine[0]/rotor-rpm")
        n1 = f.get_property_value("propulsion/engine[0]/n1")
        print(f"[spool] main rotor rpm={rpm:.0f} n1={n1:.0f}% (t={self.t:.1f}s)")

    def set_collective(self, v):
        self.fdm.set_property_value("fcs/collective-cmd-norm", v)

    def state(self):
        f = self.fdm
        return {
            "t": self.t,
            "agl_ft": f.get_property_value("position/agl-ft"),
            "vs_fpm": f.get_property_value("velocities/h-dot-fps") * 60.0,
            "roll": f.get_property_value("attitude/phi-deg"),
            "pitch": f.get_property_value("attitude/theta-deg"),
            "hdg": f.get_property_value("attitude/psi-deg"),
            "yawr_dps": f.get_property_value("velocities/r-radps") * 57.29578,
            "coll": f.get_property_value("fcs/collective-cmd-norm"),
            "rud": f.get_property_value("propulsion/engine[1]/antitorque-ctrl-rad"),
            "rud_i": f.get_property_value("fcs/hs/antitorque-ff"),
        }

    def step(self, n=1, sas=True):
        f = self.fdm
        for _ in range(n):
            if sas:
                # 外环 SAS（速率阻尼）：jsbsim 1.3.1 自定义 root 下 XML 引用
                # velocities/*-radps 在 run_ic 即炸，故阻尼放在 harness 层。
                p_ = f.get_property_value("velocities/p-radps")
                q_ = f.get_property_value("velocities/q-radps")
                r_ = f.get_property_value("velocities/r-radps")
                f.set_property_value("fcs/elevator-cmd-norm",
                                     max(-0.3, min(0.3, -0.045 * q_ * 57.29578)))
                f.set_property_value("fcs/aileron-cmd-norm",
                                     max(-0.3, min(0.3, -0.045 * p_ * 57.29578)))
                f.set_property_value("fcs/rudder-cmd-norm",
                                     max(-0.6, min(0.6, -0.06 * r_ * 57.29578)))
            f.run()
            self.t += DT

    def hold(self, seconds, coll, log=None):
        """保持 collective 运行 seconds 秒。返回 (peak_yawr_dps, hdg_drift_deg, frames)"""
        frames = []
        h0 = self.state()["hdg"]
        peak = 0.0
        for _ in range(int(seconds / DT)):
            self.set_collective(coll)
            self.step()
            if int(self.t / DT) % 20 == 0:  # 10 Hz 采样
                s = self.state()
                peak = max(peak, abs(s["yawr_dps"]))
                s["hdg_drift"] = wrap180(s["hdg"] - h0)
                frames.append(s)
                if log is not None:
                    log.append(s)
                if math.isnan(s["agl_ft"]) or abs(s["roll"]) > 90:
                    break
        return peak, wrap180(frames[-1]["hdg"] - h0) if frames else 0.0, frames


def find_hover_coll(heli, lo=0.55, hi=0.90):
    """二分找悬停总距：判据 = 持续离地（agl>2ft）且不暴冲（vs<+500fpm）。
    注意：不能拿 vs≈0 当悬停——总距不足时机体压在滑橇上滑转，h-dot 也是 0。"""
    best = None
    for it in range(5):
        mid = (lo + hi) / 2
        heli.fdm.load_ic("reset00.xml", True)
        heli.fdm.run_ic()
        heli.t = 0.0
        heli.spool()
        max_agl = 0.0
        end_vs = 0.0
        bad_att = False
        for _ in range(int(14 / DT)):
            heli.set_collective(mid)
            heli.step()
            st = heli.state()
            max_agl = max(max_agl, st["agl_ft"])
            end_vs = st["vs_fpm"]
            if abs(st["roll"]) > 45 or abs(st["pitch"]) > 45:
                bad_att = True
                break
        airborne = max_agl > 2.0 and not bad_att
        print(f"  cal it{it}: coll={mid:.3f} max_agl={max_agl:.1f}ft end_vs={end_vs:+.0f}fpm "
              f"{'AIRBORNE' if airborne else 'on/below skids'}{' ATT-EXC' if bad_att else ''}")
        if bad_att:
            hi = mid  # 姿态发散多半是推力过猛
        elif airborne and end_vs < 500:
            best = (mid, max_agl)
            hi = mid  # 已离地，往下找最小离地总距
        elif airborne:
            hi = mid  # 离地但暴冲
        else:
            lo = mid  # 没离地
        if not bad_att and airborne and end_vs < 300:
            return mid, max_agl
    return best if best else ((lo + hi) / 2, 0.0)


def cal_ff_sign(hover_coll):
    """两次 3s 短暂离地跳，实测主扭矩方向 T 与尾桨响应方向 S。
    ff_sign = -sign(T)*sign(S)（使前馈方向恰好抵消主旋翼扭矩）。"""
    import math as _m
    sign_T = sign_S = 0
    # hop1: FF 关闭, 踏板 0 -> 量主扭矩引起的 yawr
    for attempt in range(3):
        h = Heli()
        h.spool()
        h.set_collective(hover_coll + 0.02)
        h.fdm.set_property_value("fcs/hs/ff-sign", 0.0)
        h.fdm.set_property_value("fcs/hs/afcs-enable", 1.0)
        yr = []
        for _ in range(int(6.0 / DT)):
            h.set_collective(hover_coll + 0.02)
            h.step()
            st = h.state()
            yr.append(st["yawr_dps"])
            if st["agl_ft"] > 1.0 and len(yr) > 100:
                break
            if _m.isnan(st["agl_ft"]):
                break
        window = yr[-150:]
        mean_y = sum(window) / max(len(window), 1)
        if abs(mean_y) > 1.5:
            sign_T = 1 if mean_y > 0 else -1
            print(f"[cal-ff] hop1: FF=0 时 mean yawr={mean_y:+.1f}°/s -> 主扭矩方向 T={sign_T:+d}")
            break
    # hop2: FF 关, 踏板 +0.35 -> 量尾桨响应方向
    for attempt in range(3):
        h = Heli()
        h.spool()
        h.fdm.set_property_value("fcs/hs/ff-sign", 0.0)
        h.fdm.set_property_value("fcs/hs/afcs-enable", 1.0)
        yr = []
        for _ in range(int(6.0 / DT)):
            h.set_collective(hover_coll + 0.02)
            h.fdm.set_property_value("fcs/rudder-cmd-norm", 0.35)
            h.step()
            st = h.state()
            yr.append(st["yawr_dps"])
            if st["agl_ft"] > 1.0 and len(yr) > 100:
                break
            if _m.isnan(st["agl_ft"]):
                break
        window = yr[-150:]
        mean_y = sum(window) / max(len(window), 1)
        if abs(mean_y) > 1.5:
            sign_S = 1 if mean_y > 0 else -1
            print(f"[cal-ff] hop2: 踏板+0.35 时 mean yawr={mean_y:+.1f}°/s -> 尾桨响应 S={sign_S:+d}")
            break
    if sign_T == 0 or sign_S == 0:
        print("[cal-ff] 标定失败（跳不离地或响应过弱），ff_sign 保持 +1")
        return 1.0
    ff = -sign_T * sign_S
    print(f"[cal-ff] ff_sign = -T*S = {ff:+.0f}")
    return float(ff)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-cal", action="store_true")
    ap.add_argument("--hover-coll", type=float, default=None)
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {"stamp": stamp, "dt": DT}

    heli = Heli()
    print("=== FDM SPIKE probe (helispike, root=%s) ===" % ROOT)

    if args.skip_cal and args.hover_coll:
        hover_coll = args.hover_coll
    else:
        print("[cal] 寻找悬停总距 ...")
        hover_coll, _ = find_hover_coll(heli)
    report["hover_collective"] = round(hover_coll, 3)
    print(f"[cal] hover collective = {hover_coll:.3f}")
    ff_sign = cal_ff_sign(hover_coll)
    report["ff_sign"] = ff_sign

    # ---------- 探针 A: 静悬 15s ----------
    heli.fdm.load_ic("reset00.xml", True)
    heli.fdm.run_ic()
    heli.t = 0.0
    heli.spool()
    heli.fdm.set_property_value("fcs/hs/ff-sign", ff_sign)
    print("[probe A] 静悬 15s @ coll=%.3f (手离杆) ..." % hover_coll)
    logA = []
    peak, drift, frames = heli.hold(15.0, hover_coll, log=logA)
    last = frames[-1] if frames else {}
    a_pass = peak < 5.0 and abs(drift) < 10.0
    report["probeA"] = {"peak_yawr_dps": round(peak, 2), "hdg_drift_deg": round(drift, 2),
                        "agl_ft_end": round(last.get("agl_ft", -1), 1),
                        "pass": a_pass}
    print(f"[probe A] peak|yawr|={peak:.2f}°/s (target<5) | hdg drift={drift:+.2f}° "
          f"(target<10) | end agl={last.get('agl_ft', 0):.1f}ft -> "
          f"{'PASS' if a_pass else 'FAIL'}")

    # ---------- 探针 B: collective 阶跃爬升 60s ----------
    heli.fdm.load_ic("reset00.xml", True)
    heli.fdm.run_ic()
    heli.t = 0.0
    heli.spool()
    heli.fdm.set_property_value("fcs/hs/ff-sign", ff_sign)
    print(f"[probe B] collective 阶跃 +0.08 (→{hover_coll+0.08:.3f}) 爬升 60s ...")
    flip = False
    alt0 = None
    alt_end = 0.0
    coll_b = min(hover_coll + 0.08, 1.0)
    for _ in range(int(90 / DT)):
        heli.set_collective(coll_b)
        heli.step()
        if int(heli.t / DT) % 100 == 0:  # 20Hz
            st = heli.state()
            if alt0 is None:
                alt0 = st["agl_ft"]
            alt_end = st["agl_ft"]
            if abs(st["roll"]) > 30 or abs(st["pitch"]) > 30 or st["crashed"] if False else False:
                pass
            if math.isnan(st["agl_ft"]):
                flip = True
                print(f"[probe B] NaN/sim-death at t={heli.t:.0f}s")
                break
            if abs(st["roll"]) > 30 or abs(st["pitch"]) > 30:
                flip = True
                print(f"[probe B] FLIP at t={heli.t:.0f}s roll={st['roll']:.0f} pitch={st['pitch']:.0f}")
                break
    b_pass = (not flip) and alt_end > (alt0 or 0) + 100
    report["probeB"] = {"flip_or_crash": flip, "alt_gain_ft": round(alt_end - (alt0 or 0), 1),
                        "pass": b_pass}
    print(f"[probe B] 翻覆/坠毁={flip} 高度增益={alt_end-(alt0 or 0):.0f}ft -> "
          f"{'PASS' if b_pass else 'FAIL'}")

    # ---------- 探针 C: 配平表扫掠 ----------
    heli.fdm.load_ic("reset00.xml", True)
    heli.fdm.run_ic()
    heli.t = 0.0
    heli.spool()
    heli.fdm.set_property_value("fcs/hs/ff-sign", ff_sign)
    print("[probe C] collective 0.5→1.0 缓扫 40s（配平表全程有效性）...")
    # 先到悬停
    heli.hold(5.0, hover_coll)
    peak_c = 0.0
    coll_c = 0.5
    diverged = False
    for _ in range(int(40 / DT)):
        coll_c = min(1.0, coll_c + 0.0125 * DT)
        heli.set_collective(coll_c)
        heli.step()
        if int(heli.t / DT) % 20 == 0:
            st = heli.state()
            peak_c = max(peak_c, abs(st["yawr_dps"]))
            if abs(st["roll"]) > 60:
                diverged = True
                break
    c_pass = (peak_c < 3.0) and (not diverged)
    report["probeC"] = {"peak_yawr_dps": round(peak_c, 2), "diverged": diverged,
                        "pass": c_pass}
    print(f"[probe C] sweep peak|yawr|={peak_c:.2f}°/s (target<3) diverged={diverged} "
          f"-> {'PASS' if c_pass else 'FAIL'}")

    report["probeA_frames"] = len(logA)
    report["spike_pass"] = a_pass and b_pass and c_pass
    out = OUT / f"spike_probe_{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"spoke_frames_{stamp}_A.json").write_text(
        json.dumps(logA, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== SPIKE 总判定:", "PASS" if report["spike_pass"] else "FAIL",
          "=== ")
    print("报告:", out)
    return 0 if report["spike_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
