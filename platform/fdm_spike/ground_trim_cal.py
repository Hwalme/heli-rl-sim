#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ground_trim_cal.py — 地面配平标定战役（FDM SPIKE 收官）
滑橇压地（coll 0.5，不离地）测 psi 自旋速率：
  1) ff_sign=+1 / -1 各测一次 -> 选慢者定符号
  2) 按自旋比例缩放 FF 表幅值（改 fcs/hs/ff-gain 属性，无需重启）
  3) 迭代至 coll 0.5 与 0.7（悬停，带稳定吊车）自旋都 <2°/s
产物: platform/fdm_spike/trim_cal_result.json + 过程日志
"""
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) if False else None
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_fdm_spike_v2 import Heli, Stabilizer, DT, clamp, wrap180

OUT = Path(__file__).resolve().parent / "trim_cal_result.json"
LOG = Path(__file__).resolve().parent / "trim_cal_log.txt"


def logline(s):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(s + "\n")
    print(s, flush=True)


def ground_spin(heli, stab, coll, seconds=6.0):
    """压地测 psi 自旋速率（°/s，带符号）。返回 (rate, stable)。"""
    h0 = heli.st()["hdg"]
    t0 = heli.t
    for _ in range(int(seconds / DT)):
        heli.set_coll(coll)
        stab.apply(coll, hdg_ref=None)  # 航向环不开——要测的就是自旋
        heli.run()
    t1 = heli.t
    h1 = heli.st()["hdg"]
    agl = heli.st()["agl"]
    if math.isnan(agl):
        return float("nan"), False
    rate = wrap180(h1 - h0) / (t1 - t0)
    return rate, agl < 3.0  # stable = 仍压地


def main():
    open(LOG, "w").close()
    result = {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"), "trials": []}
    heli = Heli()
    stab = Stabilizer(heli)

    # 初始化：地面、双发、离合、governor、coll 0.5
    heli.fdm.load_ic("reset00.xml", True)
    heli.fdm.run_ic()
    heli.start()
    for _ in range(int(90 / DT)):
        heli.run()
        heli.set_coll(0.5)
        stab.apply(0.5, hdg_ref=None)
        s = heli.st()
        if s["nan"]:
            logline("[FATAL] NaN during initial spool")
            OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            return 1
        if int(heli.t / DT) % 100 == 0 and 480 <= s["rpm"] <= 570 and heli.t > 15:
            break
    logline(f"[init] rpm={s['rpm']:.0f} agl={s['agl']:.1f} coll=0.5")

    ff_sign = +1.0
    ff_gain = 1.0
    heli.fdm.set_property_value("fcs/hs/ff-sign", ff_sign)
    heli.fdm.set_property_value("fcs/hs/ff-gain", ff_gain)

    best = None
    for rnd in range(8):
        r5, on5 = ground_spin(heli, stab, 0.5, 6.0)
        logline(f"[rnd{rnd}] sign={ff_sign:+.0f} gain={ff_gain:.2f} coll0.5 spin={r5:+.1f}°/s "
                f"(on_skids={on5})")
        result["trials"].append({"rnd": rnd, "sign": ff_sign, "gain": ff_gain,
                                 "spin05": round(r5, 2) if not math.isnan(r5) else None})
        if math.isnan(r5):
            logline("[FATAL] NaN in ground spin — 停止")
            break
        if abs(r5) < 2.0 and on5:
            best = (ff_sign, ff_gain)
            logline(f"[rnd{rnd}] coll0.5 达标（<2°/s）")
            # 上 0.7 悬停验证（带稳定吊车+航向保持）
            hdg0 = heli.st()["hdg"]
            ok_hover = True
            peak = 0.0
            for _ in range(int(10 / DT)):
                heli.set_coll(0.70)
                stab.apply(0.70, hdg_ref=hdg0)
                heli.run()
                if int(heli.t / DT) % 50 == 0:
                    s = heli.st()
                    if s["nan"]:
                        ok_hover = False
                        break
                    if s["agl"] > 3.0:
                        peak = max(peak, abs(wrap180(s["hdg"] - hdg0)) / max(s["t"], 0.1))
            logline(f"[rnd{rnd}] coll0.70 悬停: ok={ok_hover} 航向漂移率={peak:.2f}°/s")
            if ok_hover and peak < 3.0:
                best = (ff_sign, ff_gain)
                result["final"] = {"sign": ff_sign, "gain": ff_gain,
                                   "verdict": "PASS",
                                   "hover_drift_rate_dps": round(peak, 2)}
                break
            else:
                # 悬停段漂移仍大 -> 再缩放
                ff_gain *= 1.6 if peak > 0 else 1.0
                heli.fdm.set_property_value("fcs/hs/ff-gain", ff_gain)
                continue
        # 未达标：换符号或缩放
        if rnd == 0:
            ff_sign *= -1.0
        else:
            prev = result["trials"][-2]["spin05"]
            cur = result["trials"][-1]["spin05"]
            if not math.isnan(prev) and abs(cur) > 0.1 and abs(prev) > 0.1:
                ff_gain *= max(0.3, min(3.0, abs(prev) / abs(cur)))
            else:
                ff_gain *= 1.5
            if rnd == 2:
                ff_sign *= -1.0
        heli.fdm.set_property_value("fcs/hs/ff-sign", ff_sign)
        heli.fdm.set_property_value("fcs/hs/ff-gain", ff_gain)

    result["best"] = best
    if best is None:
        result["verdict"] = "FAIL(未在 8 轮内收敛)"
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    logline("=== 地面配平标定收官: " + json.dumps(result.get("final", result["verdict"]),
                                                ensure_ascii=False) + " ===")
    return 0 if result.get("final") else 1


if __name__ == "__main__":
    sys.exit(main())
