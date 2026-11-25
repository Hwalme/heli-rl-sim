#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_02.py — helispike 0.2 版飞行科目（主E任务书 0.2-1a/1b/1c）

一个连续剖面：
  1. 悬停建立（coll 0.72 果断离地 + 姿态/航向稳定吊车，符号=0.2 实测）
  2. 前飞配平测量（0.2-1a）：俯仰目标阶梯 -3°/-6°/-9°/-12°/-15°/-18°，
     每档 8s 记录稳态空速/实际俯仰/垂直速度 -> 前飞配平表数据
  3. 悬停-下降标定矩阵（0.2-1c）：coll 0.70→0.50 每档 8s -> collective→下降率
  4. 着陆/滑橇接地（0.2-1b）：缓降 -> 接地垂速/姿态/停稳判定
产物: probe_results/probe02_<stamp>.json
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

# 0.2 实测符号（spike v2 标定）
ROLL_SIGN = -1.0
PITCH_SIGN = +1.0
HDG_SIGN = +1.0
BASE_ELE = 0.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def wrap180(x):
    return (x + 180.0) % 360.0 - 180.0


class Heli:
    def __init__(self):
        self.fdm = jsbsim.FGFDMExec(ROOT, None)
        if not self.fdm.load_model("helispike", True):
            raise RuntimeError("load_model failed")
        self.fdm.load_ic("reset00.xml", True)
        self.fdm.run_ic()
        self.t = 0.0

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
            "rpm": f.get_property_value("propulsion/engine[0]/rotor-rpm"),
        }

    def run(self, n=1, sas=True, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=None):
        """姿态/航向稳定吊车（符号 = 0.2 实测）：cmd 为目标姿态角（度）。"""
        f = self.fdm
        for _ in range(n):
            if not sas:
                f.run()
                self.t += DT
                continue
            roll = f.get_property_value("attitude/phi-deg")
            pitch = f.get_property_value("attitude/theta-deg")
            hdg = f.get_property_value("attitude/psi-deg")
            p_ = f.get_property_value("velocities/p-radps")
            q_ = f.get_property_value("velocities/q-radps")
            r_ = f.get_property_value("velocities/r-radps")
            # 符号经 0.2 前标定：ail 对 roll 发散方向为 -1，ele 为 +1
            ail = clamp(ROLL_SIGN * (0.06 * (roll - cmd_roll) + 0.15 * p_ * D2R),
                        -0.3, 0.3)
            ele = clamp(PITCH_SIGN * (0.06 * (pitch - cmd_pitch) + 0.15 * q_ * D2R)
                        + BASE_ELE, -0.42, 0.42)
            if hdg_ref is not None:
                herr = wrap180(hdg - hdg_ref)
                rud = clamp(HDG_SIGN * (0.08 * herr) - 0.10 * r_ * D2R, -0.6, 0.6)
            else:
                rud = 0.0
            f.set_property_value("fcs/aileron-cmd-norm", ail)
            f.set_property_value("fcs/elevator-cmd-norm", ele)
            f.set_property_value("fcs/rudder-cmd-norm", rud)
            f.run()
            self.t += DT


def spool(heli):
    heli.start()
    for _ in range(int(90 / DT)):
        heli.run(n=1, sas=False)
        if int(heli.t / DT) % 100 == 0:
            s = heli.st()
            if s["nan"]:
                return False
            if 495 <= s["rpm"] <= 565 and heli.t > 12:
                return True
    s = heli.st()
    return 495 <= s["rpm"] <= 565


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {"stamp": stamp, "version": "helispike 0.2"}
    heli = Heli()
    print("=== helispike 0.2 飞行科目 ===")
    if not spool(heli):
        print("[FATAL] spool 未达标")
        return 1
    print(f"[spool] rpm={heli.st()['rpm']:.0f}")

    # ---- 1. 悬停建立（coll 0.72 果断离地）----
    hover_coll = 0.72
    heli.set_coll(hover_coll)
    hdg_ref = heli.st()["hdg"]
    airborne = False
    climb_ft = 0.0
    s = heli.st()
    for _ in range(int(15 / DT)):
        heli.set_coll(hover_coll)
        s = heli.st()
        climb_ft += s["vs_fpm"] / 60.0 * DT
        heli.run(n=1, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=hdg_ref)
        if int(_) % 400 == 0:
            print(f"  [est] t={s['t']:5.1f}s climb={climb_ft:6.1f}ft vs={s['vs_fpm']:+6.0f}fpm "
                  f"roll={s['roll']:+5.1f} pitch={s['pitch']:+5.1f}")
        if s["nan"]:
            print("[FATAL] 悬停建立 NaN")
            return 1
        if climb_ft > 10.0:
            airborne = True
            break
    print(f"[悬停] airborne={airborne} climb={climb_ft:.1f}ft vs={s['vs_fpm']:+.0f}fpm "
          f"roll={s['roll']:+.1f} pitch={s['pitch']:+.1f}")
    if not airborne:
        print("[FATAL] 未离地")
        return 1

    # ---- 2. 前飞配平测量（0.2-1a）：俯仰目标阶梯 ----
    fwd_points = []
    print("[1a] 前飞配平阶梯（pitch_cmd -3/-6/-9/-12/-15/-18°，每档 8s）...")
    for k in range(1, 7):
        pitch_cmd = -3.0 * k
        coll_f = min(hover_coll + 0.01 * k, 0.82)
        t0 = heli.t
        vs_sum = ve_sum = th_sum = n = 0
        diverged = False
        while heli.t - t0 < 8.0:
            heli.set_coll(coll_f)
            heli.run(n=1, cmd_roll=0.0, cmd_pitch=pitch_cmd, hdg_ref=hdg_ref)
            if int((heli.t - t0) / DT) % 40 == 0:
                s = heli.st()
                if s["nan"] or abs(s["roll"]) > 45:
                    diverged = True
                    break
                vs_sum += s["vs_fpm"]; ve_sum += s["ve_kts"]
                th_sum += s["pitch"]; n += 1
        s = heli.st()
        pt = {"pitch_cmd_deg": pitch_cmd, "ve_kts": round(ve_sum / max(n, 1), 1),
              "theta_deg": round(th_sum / max(n, 1), 2),
              "vs_fpm": round(vs_sum / max(n, 1), 0),
              "diverged": diverged}
        fwd_points.append(pt)
        print(f"  [{k}] cmd={pitch_cmd:+.0f}° -> ve={pt['ve_kts']:6.1f}kt "
              f"theta={pt['theta_deg']:+6.2f}° vs={pt['vs_fpm']:+6.0f}fpm"
              f"{' [发散/掉地]' if diverged else ''}")
        if diverged:
            break

    # 回中恢复悬停
    for _ in range(int(4 / DT)):
        heli.set_coll(hover_coll)
        heli.run(n=1, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=hdg_ref)

    # ---- 3. 悬停-下降标定矩阵（0.2-1c）----
    desc_points = []
    print("[1c] 下降矩阵（coll 0.70→0.50 每档 8s）...")
    for coll in (0.70, 0.66, 0.62, 0.58, 0.54, 0.50):
        t0 = heli.t
        vs_sum = n = 0
        diverged = False
        while heli.t - t0 < 8.0:
            heli.set_coll(coll)
            heli.run(n=1, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=hdg_ref)
            if int((heli.t - t0) / DT) % 40 == 0:
                s = heli.st()
                if s["nan"] or abs(s["roll"]) > 45:
                    diverged = True
                    break
                vs_sum += s["vs_fpm"]; n += 1
        s = heli.st()
        pt = {"coll": coll, "vs_fpm": round(vs_sum / max(n, 1), 0),
              "climb_ft": round(climb_ft, 1), "diverged": diverged}
        desc_points.append(pt)
        print(f"  [{coll:.2f}] vs={pt['vs_fpm']:+6.0f}fpm climb={climb_ft:6.1f}ft"
              f"{' [发散]' if diverged else ''}")
        if diverged or climb_ft < 1.0:
            break

    # ---- 4. 着陆/滑橇接地（0.2-1b）----
    print("[1b] 着陆探针（缓降至接地）...")
    heli.set_coll(0.74)
    for _ in range(int(10 / DT)):
        heli.run(n=1, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=hdg_ref)
        s = heli.st()
        if s["agl"] > 14.0:
            break
    print(f"  进近: agl={s['agl']:.1f}ft vs={s['vs_fpm']:+.0f}fpm")
    touched = False
    td_vs = None
    td_att = None
    exploded = False
    for _ in range(int(30 / DT)):
        s0 = heli.st()
        target = 0.55 if climb_ft > 8 else (0.585 if climb_ft > 4 else 0.62)
        heli.set_coll(target)
        heli.run(n=1, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=hdg_ref)
        s1 = heli.st()
        climb_ft += s1["vs_fpm"] / 60.0 * DT
        if int(heli.t / DT) % 20 == 0:
            s = s1
            if s["nan"]:
                exploded = True
                print("  NaN")
                break
            if climb_ft < 1.2:
                touched = True
                td_vs = s["vs_fpm"]
                td_att = (round(s["roll"], 1), round(s["pitch"], 1))
                print(f"  接地: climb={climb_ft:.2f}ft vs={td_vs:+.0f}fpm "
                      f"roll={s['roll']:+.1f} pitch={s['pitch']:+.1f}")
                break
    stable = False
    if touched:
        heli.set_coll(1.0)  # 平桨
        for _ in range(int(4 / DT)):
            heli.run(n=1, cmd_roll=0.0, cmd_pitch=0.0, hdg_ref=hdg_ref)
        s = heli.st()
        stable = (not s["nan"] and abs(s["roll"]) < 15 and abs(s["pitch"]) < 15)
        print(f"  停稳: roll={s['roll']:+.1f} pitch={s['pitch']:+.1f} "
              f"-> {'稳定' if stable else '不稳定'}")
    touchdown = {"touched": touched,
                 "td_vs_fpm": round(td_vs, 1) if td_vs is not None else None,
                 "td_roll_pitch": td_att, "post_settle_stable": stable,
                 "exploded": exploded}

    report = {"stamp": stamp, "version": "helispike 0.2",
              "forward_trim_points": fwd_points,
              "descent_matrix": desc_points,
              "touchdown": touchdown}
    out = OUT / f"probe02_{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n[产物]", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
