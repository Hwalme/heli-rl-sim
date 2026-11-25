# -*- coding: utf-8 -*-
"""起飞诊断 v5：rev8.9 XML + punch_floor精细调优"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from farm_worker import FG, reset_and_ready, relaunch, PH_NAMES

PORT = 5504
LOGDIR = r"platform\fg_demo\logs"
os.makedirs(LOGDIR, exist_ok=True)

def set_params(fg, p):
    for prop, val in [
        ("/demo/stabilizer/sign-ail", 1.0),
        ("/demo/stabilizer/sign-ele", 1.0),
        ("/demo/stabilizer/sign-rud", -1.0),
        ("/demo/seq/ffw-sign", 1.0),
        ("/demo/tune/ramp-fast", p["ramp_fast"]),
        ("/demo/tune/ramp-rate", p["ramp_fast"]),
        ("/demo/tune/punch-floor", p["punch"]),
        ("/demo/tune/yaw-hold", p["yaw_hold"]),
        ("/demo/tune/damper", p["damper"]),
        ("/demo/tune/cyc-gain", p["cyc_gain"]),
        ("/demo/tune/lo-trim", p["lo_trim"]),
        ("/demo/tune/ff-trq", p["ff_trq"]),
        ("/demo/tune/ff-band", p["ff_band"]),
        ("/demo/seq/run", 0), ("/demo/seq/start", 0), ("/demo/seq/done", 0)]:
        fg.set(prop, val)
    time.sleep(0.3)

def run_episode(fg, p, max_t=50, log_path=None):
    set_params(fg, p)
    fg.set("/demo/seq/run", 1)
    fg.set("/demo/seq/start", 1)
    t0 = time.time()
    frames = []
    flipped = False
    flip_info = None
    while time.time() - t0 < max_t:
        time.sleep(0.15)
        t = time.time() - t0
        f = {
            "t": round(t,1), "ph": fg.get("/demo/seq/phase"),
            "agl": fg.get("/position/altitude-agl-ft"),
            "vs": fg.get("/velocities/vertical-speed-fps"),
            "coll": fg.get("/controls/engines/engine[0]/throttle"),
            "roll": fg.get("/orientation/roll-deg"),
            "pitch": fg.get("/orientation/pitch-deg"),
            "hdg": fg.get("/orientation/heading-deg"),
            "yawr": fg.get("/orientation/yaw-rate-degps"),
            "rud": fg.get("/controls/flight/rudder"),
            "cr": fg.get("/sim/crashed"),
        }
        frames.append(f)
        if f["cr"] > 0.5:
            flipped = True; flip_info = f; print("  CRASH t=%.1f" % t, flush=True); break
        if abs(f["roll"]) > 50:
            flipped = True; flip_info = f
            print("  ROLL FLIP t=%.1f roll=%.1f ph=%s" % (t, f["roll"], PH_NAMES.get(int(f["ph"]),"?")), flush=True); break
        if abs(f["pitch"]) > 50:
            flipped = True; flip_info = f
            print("  PITCH FLIP t=%.1f pitch=%.1f ph=%s" % (t, f["pitch"], PH_NAMES.get(int(f["ph"]),"?")), flush=True); break
        if f["ph"] >= 4 and f["agl"] > 30 and t > 10:
            print("  REACHED PHASE4 t=%.1f agl=%.1f" % (t, f["agl"]), flush=True); break
    fg.set("/demo/seq/run", 0)
    if log_path:
        json.dump(frames, open(log_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return flipped, flip_info, frames

tests = [
    {"name": "rev89_pf0.56", "ramp_fast": 0.02, "punch": 0.56, "yaw_hold": 1.0,
     "damper": 0.5, "cyc_gain": 0.5, "lo_trim": -3.5, "ff_trq": 1.0, "ff_band": 3.0},
    {"name": "rev89_pf0.58", "ramp_fast": 0.02, "punch": 0.58, "yaw_hold": 1.0,
     "damper": 0.5, "cyc_gain": 0.5, "lo_trim": -3.5, "ff_trq": 1.0, "ff_band": 3.0},
    {"name": "rev89_pf0.55", "ramp_fast": 0.02, "punch": 0.55, "yaw_hold": 1.0,
     "damper": 0.5, "cyc_gain": 0.5, "lo_trim": -3.5, "ff_trq": 1.0, "ff_band": 3.0},
]

print("Connecting FG (will relaunch to load rev8.9 XML)...", flush=True)
relaunch("A")  # 强制重启加载新XML
time.sleep(5)
fg = FG(PORT)
print("FG connected with rev8.9", flush=True)

for p in tests:
    name = p.pop("name")
    print("\n=== %s ===" % name, flush=True)
    ok, info = reset_and_ready(fg, "A")
    if not ok:
        print("  reset failed, relaunching...", flush=True)
        relaunch("A"); time.sleep(5); fg = FG(PORT)
        ok, info = reset_and_ready(fg, "A")
    print("  reset: ok=%s rpm=%.0f" % (ok, info.get("rpm",0)), flush=True)
    if not ok:
        print("  SKIP", flush=True); p["name"] = name; continue
    log = os.path.join(LOGDIR, "diag_%s.json" % name)
    flipped, flip_info, frames = run_episode(fg, p, max_t=45, log_path=log)
    if flipped and flip_info:
        ph_name = PH_NAMES.get(int(flip_info["ph"]), "?")
        print("  FLIP: t=%.1f ph=%s coll=%.3f agl=%.1f roll=%.1f pitch=%.1f yawr=%.1f rud=%.2f" % (
            flip_info["t"], ph_name, flip_info["coll"], flip_info["agl"],
            flip_info["roll"], flip_info["pitch"], flip_info["yawr"], flip_info["rud"]), flush=True)
    else:
        # check if actually took off
        max_agl = max((f["agl"] or 0) for f in frames)
        print("  NO FLIP! max_agl=%.1f frames=%d" % (max_agl, len(frames)), flush=True)
    p["name"] = name
    time.sleep(2)
