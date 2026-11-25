# -*- coding: utf-8 -*-
"""calib_sign2.py -- B1 rev5 sign calibration (safe lift pulses).

Protocol per channel (ail / ele / rud):
  enable stabilizer (fresh hdg capture) -> ramp collective 1.0 to hover band
  -> observe.  On divergence (|err| rising past 6 deg) or control saturation:
  cut lift -> settle on ground -> disable -> flip that channel's sign ->
  lift again.  Success: 3 consecutive samples in air with |err|<5 deg and no
  saturation.  Hard abort: |roll|>25 |pitch|>20 agl>60 vs<-20 crashed.

Requires demo_stabilizer.xml rev5 (enabled defaults 0, rising-edge hdg reset).
ASCII output only.  Log: logs/calib2_<ts>.json
"""
import json
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
COLL_L = "/controls/engines/engine[0]/throttle"
COLL_R = "/controls/engines/engine[1]/throttle"

SAMPLE_PATHS = [
    "/orientation/roll-deg", "/orientation/pitch-deg",
    "/demo/stabilizer/err-roll", "/demo/stabilizer/err-pitch",
    "/position/altitude-agl-ft", "/velocities/vertical-speed-fps",
    "/controls/flight/aileron", "/controls/flight/elevator",
    "/controls/flight/rudder", "/orientation/yaw-rate-degps",
    "/sim/crashed", "/demo/stabilizer/active",
]
KEYS = ["roll", "pitch", "err_roll", "err_pitch", "agl", "vs",
        "ail", "ele", "rud", "yaw_r", "crashed", "active"]

RAMP_COLL = 0.40            # lift target collective (hover approx 0.42, reversed)
RAMP_RATE = 0.06            # per second
HOVER_LO, HOVER_HI = 0.36, 0.50
ABORT_ROLL, ABORT_PITCH, ABORT_AGL, ABORT_VS = 25.0, 20.0, 60.0, -20.0
SAT = 0.49                  # saturation detector
FLIP_ERR = 6.0
MAX_ATTEMPTS = 6


class FG:
    _VAL = re.compile(r"'([^']*)'")

    def __init__(self, port=5501):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.sock.settimeout(2.5)
        time.sleep(0.3)
        self._rd()

    def _rd(self):
        deadline, out = time.time() + 4.0, b""
        while time.time() < deadline:
            try:
                c = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not c:
                break
            out += c
            if out.rstrip().endswith(b">"):
                break
        return out.decode("utf-8", "replace")

    def get(self, p, default=0.0):
        self.sock.sendall(f"get {p}\r\n".encode())
        vals = self._VAL.findall(self._rd())
        if vals:
            raw = vals[-1].strip()
            if raw.lower() == "true":
                return 1.0
            if raw.lower() == "false":
                return 0.0
            try:
                return float(raw)
            except ValueError:
                return default
        return default

    def set(self, p, v):
        self.sock.sendall(f"set {p} {v}\r\n".encode())
        self._rd()

    def coll(self, v):
        v = round(max(0.15, min(1.0, v)), 4)
        self.set(COLL_L, v)
        self.set(COLL_R, v)
        return v


def main():
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fg = FG()
    print("[OK] connected", flush=True)

    t1 = fg.get("/demo/stabilizer/ticks")
    time.sleep(1.0)
    if fg.get("/demo/stabilizer/ticks") <= t1:
        print("[FAIL] inner loop dead"); return 3
    print("[OK] inner loop alive", flush=True)

    rev = "rev5?"
    log, t0 = [], time.time()

    def sample(phase, coll):
        s = dict(zip(KEYS, (fg.get(p) for p in SAMPLE_PATHS)))
        s["t"] = round(time.time() - t0, 1)
        s["phase"] = phase
        s["coll"] = coll
        log.append(s)
        print(f"  [{s['t']:6.1f}] {phase:<10} coll={coll:.3f} "
              f"roll={s['roll']:+7.2f} pit={s['pitch']:+7.2f} "
              f"eR={s['err_roll']:+7.2f} eP={s['err_pitch']:+7.2f} "
              f"agl={s['agl']:6.2f} vs={s['vs']:+6.1f} "
              f"a={s['ail']:+.2f} e={s['ele']:+.2f} r={s['rud']:+.2f} "
              f"yr={s['yaw_r']:+6.1f}", flush=True)
        return s

    def dump(rc):
        (LOG_DIR / f"calib2_{ts}.json").write_text(
            json.dumps({"log": log, "signs": signs, "flips": flips},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[log] logs/calib2_{ts}.json", flush=True)
        print(f"[final signs] ail={signs['ail']:+.0f} ele={signs['ele']:+.0f} "
              f"rud={signs['rud']:+.0f}  flips={flips}", flush=True)
        return rc

    # parked setup
    fg.set("/demo/stabilizer/enabled", 0)
    fg.coll(1.0)
    fg.set("/controls/engines/engine[0]/starter", 0)
    fg.set("/controls/engines/engine[1]/starter", 0)
    fg.set("/controls/rotor/brake", 0)
    fg.set("/controls/engines/engine[0]/power", 1.0)
    fg.set("/controls/engines/engine[1]/power", 1.0)

    signs = {"ail": fg.get("/demo/stabilizer/sign-ail"),
             "ele": fg.get("/demo/stabilizer/sign-ele"),
             "rud": fg.get("/demo/stabilizer/sign-rud")}
    print(f"[start] signs {signs}", flush=True)

    flips = []

    def set_sign(ch, val):
        signs[ch] = val
        fg.set(f"/demo/stabilizer/sign-{ch}", val)

    def settle_down(reason):
        print(f"  >>> cutting lift ({reason})", flush=True)
        fg.coll(0.50)          # gentle sink first
        for _ in range(6):
            time.sleep(1.2)
            s = sample("settle", 0.50)
            if s["crashed"] > 0.5:
                return False
            if s["agl"] < 1.5:
                break
        fg.coll(1.0)
        time.sleep(2.0)
        fg.set("/demo/stabilizer/enabled", 0)
        for _ in range(3):
            time.sleep(1.2)
            s = sample("park", 1.0)
            if s["crashed"] > 0.5:
                return False
        return True

    def hard_abort(s):
        return (abs(s["roll"]) > ABORT_ROLL or abs(s["pitch"]) > ABORT_PITCH
                or s["agl"] > ABORT_AGL or s["vs"] < ABORT_VS)

    attempt = 0
    ok = False
    while attempt < MAX_ATTEMPTS and not ok:
        attempt += 1
        print(f"[lift attempt {attempt}] enable + ramp to {RAMP_COLL}", flush=True)
        fg.set("/demo/stabilizer/enabled", 1)   # rising edge: fresh hdg_ref
        coll = 1.0
        hover_t = None
        bad = None
        while True:
            s = sample("lift", coll)
            if s["crashed"] > 0.5:
                print("[FAIL] crashed"); return dump(2)
            if hard_abort(s):
                bad = "hard abort"
                break
            sat = max(abs(s["ail"]), abs(s["ele"])) >= SAT
            if sat:
                bad = "control saturation"
                break
            if s["active"] < 0.5:
                time.sleep(0.5)
                continue
            if coll > RAMP_COLL:
                coll = fg.coll(max(RAMP_COLL, coll - RAMP_RATE * 3.5))
                continue
            # hover band regulation
            if s["vs"] > 4.0:
                coll = fg.coll(min(HOVER_HI, coll + 0.01))
            elif s["vs"] < -4.0:
                coll = fg.coll(max(HOVER_LO, coll - 0.01))
            if hover_t is None and s["agl"] > 1.5:
                hover_t = time.time()
                print("  [lift] airborne", flush=True)
            if hover_t is None:
                if time.time() - t0 > 600:
                    bad = "never airborne"
                    break
                time.sleep(0.5)
                continue
            # in air: judge
            er, ep = abs(s["err_roll"]), abs(s["err_pitch"])
            if er < 5.0 and ep < 5.0:
                prev = log[-2] if len(log) >= 2 else s
                prev2 = log[-3] if len(log) >= 3 else s
                if (abs(prev["err_roll"]) < 5.0 and abs(prev["err_pitch"]) < 5.0
                        and abs(prev2["err_roll"]) < 5.0
                        and abs(prev2["err_pitch"]) < 5.0
                        and prev["phase"] == "lift" and prev2["phase"] == "lift"):
                    print("[SUCCESS] attitude held 3 samples in air", flush=True)
                    ok = True
                    break
            if er > FLIP_ERR and er > abs(log[-2]["err_roll"] if len(log) > 1 else 0):
                bad = "roll divergence"
                break
            if ep > FLIP_ERR and ep > abs(log[-2]["err_pitch"] if len(log) > 1 else 0):
                bad = "pitch divergence"
                break
            if abs(s["yaw_r"]) > 20:
                bad = "yaw divergence"
                break
            if time.time() - hover_t > 40:
                bad = "no convergence in 40 s"
                break
            time.sleep(0.8)
        if ok:
            break
        print(f"  >>> attempt {attempt} failed: {bad}", flush=True)
        if not settle_down(bad):
            return dump(2)
        # pick channel to flip
        if bad == "roll divergence" or bad == "control saturation" and abs(log[-1]["ail"]) >= SAT:
            if "ail" not in [f["ch"] for f in flips]:
                set_sign("ail", -signs["ail"])
                flips.append({"ch": "ail", "new": signs["ail"]})
                print(f"  >>> FLIP sign-ail -> {signs['ail']:+.0f}", flush=True)
            else:
                print("  [WARN] ail already flipped; no more ideas", flush=True)
        elif bad == "pitch divergence" or (bad == "control saturation" and abs(log[-1]["ele"]) >= SAT):
            if "ele" not in [f["ch"] for f in flips]:
                set_sign("ele", -signs["ele"])
                flips.append({"ch": "ele", "new": signs["ele"]})
                print(f"  >>> FLIP sign-ele -> {signs['ele']:+.0f}", flush=True)
            else:
                print("  [WARN] ele already flipped; no more ideas", flush=True)
        elif bad == "yaw divergence":
            if "rud" not in [f["ch"] for f in flips]:
                set_sign("rud", -signs["rud"])
                flips.append({"ch": "rud", "new": signs["rud"]})
                print(f"  >>> FLIP sign-rud -> {signs['rud']:+.0f}", flush=True)
            else:
                print("  [WARN] rud already flipped", flush=True)
        else:
            print(f"  [WARN] unhandled failure '{bad}', retrying", flush=True)

    # land
    print("[land] final approach", flush=True)
    fg.coll(0.50)
    for _ in range(10):
        time.sleep(1.2)
        s = sample("land", 0.50)
        if s["crashed"] > 0.5:
            print("[NOTE] crashed on landing"); return dump(2)
        if s["agl"] < 1.5:
            break
    fg.coll(1.0)
    time.sleep(2.0)
    fg.set("/demo/stabilizer/enabled", 0)
    time.sleep(1.5)
    s = sample("park", 1.0)
    if s["crashed"] > 0.5:
        print("[FAIL] crashed at end"); return dump(2)
    print("[OK] parked, stabilizer disabled", flush=True)
    return dump(0 if ok else 2)


if __name__ == "__main__":
    sys.exit(main())
