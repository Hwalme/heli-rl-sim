#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_stab.py — 稳定悬停失败诊断：全通道 10Hz 采样，看发散源头与舵饱和。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_fdm_spike_v2 import Heli, Stabilizer, DT, clamp

heli = Heli()
stab = Stabilizer(heli)
coll = 0.72

heli.fdm.load_ic("reset00.xml", True)
heli.fdm.run_ic()
heli.t = 0.0
heli.start()
spool_ok = False
for _ in range(int(90 / DT)):
    heli.run()
    heli.set_coll(0.0)          # spool 期零总距（不在滑橇上硬扛推力）
    stab.apply(0.0, hdg_ref=None)
    s = heli.st()
    if s["nan"]:
        print("NaN during spool at coll=0")
        sys.exit(1)
    if 495 <= s["rpm"] <= 565 and s["rpm"] and heli.t > 15:
        spool_ok = True
        break
print(f"spool_ok={spool_ok} rpm={s['rpm']:.0f} agl={s['agl']:.1f}")

rows = []
for _ in range(int(12 / DT)):
    heli.set_coll(coll)
    s = stab.apply(coll, hdg_ref=None)
    heli.run()
    if int(heli.t / DT) % 20 == 0:
        st = heli.st()
        st["ail"] = heli.fdm.get_property_value("fcs/aileron-cmd-norm")
        st["ele"] = heli.fdm.get_property_value("fcs/elevator-cmd-norm")
        st["thrust"] = heli.fdm.get_property_value("propulsion/engine[0]/thrust-lbs")
        st["q_dps"] = st["q"]
        rows.append(st)
        flag = ""
        if abs(st["ail"]) >= 0.295: flag += " AIL-SAT"
        if abs(st["ele"]) >= 0.295: flag += " ELE-SAT"
        if abs(st["rud"]) >= 0.595: flag += " RUD-SAT"
        print(f"t={st['t']:6.1f} agl={st['agl']:7.2f} roll={st['roll']:+7.1f} pitch={st['pitch']:+7.1f} "
              f"hdg={st['hdg']:7.1f} r={st['r']:+6.1f} coll={st['coll']:.2f} "
              f"ail={st['ail']:+.3f} ele={st['ele']:+.3f} rud={st['rud']:+.3f} "
              f"T={st['thrust']:.0f}lb q={st['q_dps']:+5.1f}{flag}")
        if st["nan"] or abs(st["roll"]) > 70:
            break

import json
from pathlib import Path
Path("diag_stab_last.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
print("saved diag_stab_last.json")
