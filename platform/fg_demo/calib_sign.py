# -*- coding: utf-8 -*-
"""calib_sign.py -- B1 rev4 inner-loop sign calibration (ground -> light lift).

Phases:
  A  throttle 0.55 (light rotor on wheels), observe err-roll / err-pitch trends
  B  ramp collective to 0.40 (past hover point ~0.42, reversed mapping) -> get
     light / lift a few feet, watch attitude; auto-flip sign-* on divergence
  C  land: throttle -> 1.0 (flat pitch, reversed mapping = no lift), settle

Safety: if |roll|>40 or |pitch|>30 or agl>120 or vs<-25 -> throttle 1.0 (kill
lift) immediately.  crashed=1 -> stop and report.

ASCII output only (GBK console safe).  Log: logs/calib_<ts>.json
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
SIGNS = ["/demo/stabilizer/sign-ail", "/demo/stabilizer/sign-ele",
         "/demo/stabilizer/sign-rud"]

LIFT_COLL = 0.40
RAMP_RATE = 0.010          # collective units per second (0.55 -> 0.40 in 15 s)
FLIP_ERR = 8.0             # deg sustained error growth -> flip that channel
ABORT_ROLL, ABORT_PITCH, ABORT_AGL, ABORT_VS = 40.0, 30.0, 120.0, -25.0


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


def sample(fg):
    keys = ["roll", "pitch", "roll_r", "pitch_r", "yaw_r",
            "err_roll", "err_pitch", "ail", "ele", "rud", "agl", "vs",
            "crashed", "active"]
    paths = ["/orientation/roll-deg", "/orientation/pitch-deg",
             "/orientation/roll-rate-degps", "/orientation/pitch-rate-degps",
             "/orientation/yaw-rate-degps",
             "/demo/stabilizer/err-roll", "/demo/stabilizer/err-pitch",
             "/controls/flight/aileron", "/controls/flight/elevator",
             "/controls/flight/rudder",
             "/position/altitude-agl-ft", "/velocities/vertical-speed-fps",
             "/sim/crashed", "/demo/stabilizer/active"]
    d = dict(zip(keys, (fg.get(p) for p in paths)))
    return d


def worst(log, key, n):
    """max |value| over last n samples"""
    vals = [abs(e[key]) for e in log[-n:]]
    return max(vals) if vals else 0.0


def main():
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fg = FG()
    print("[OK] connected")

    t1 = fg.get("/demo/stabilizer/ticks")
    time.sleep(1.0)
    if fg.get("/demo/stabilizer/ticks") <= t1:
        print("[FAIL] inner loop dead"); return 3
    print("[OK] inner loop alive")

    fg.set("/demo/stabilizer/enabled", 1)
    fg.set("/demo/target/roll-deg", 0.0)
    fg.set("/demo/target/pitch-deg", 0.0)
    for p in ("starter", ):
        fg.set(f"/controls/engines/engine[0]/{p}", 0)
        fg.set(f"/controls/engines/engine[1]/{p}", 0)
    fg.set("/controls/rotor/brake", 0)
    fg.set("/controls/engines/engine[0]/power", 1.0)
    fg.set("/controls/engines/engine[1]/power", 1.0)

    log = []
    signs = {p: fg.get(p) for p in SIGNS}
    print(f"[start] signs {signs}")

    def emit(phase, coll, s):
        s["t"] = round(time.time() - t0, 1)
        s["phase"] = phase
        s["coll"] = coll
        log.append(s)
        print(f"  [{s['t']:6.1f}] {phase:<10} coll={coll:.3f} "
              f"roll={s['roll']:+7.2f} pit={s['pitch']:+7.2f} "
              f"errR={s['err_roll']:+7.2f} errP={s['err_pitch']:+7.2f} "
              f"agl={s['agl']:6.2f} vs={s['vs']:+6.1f} "
              f"ail={s['ail']:+.3f} ele={s['ele']:+.3f} rud={s['rud']:+.3f}")

    t0 = time.time()
    flipped = []

    def try_flip(channel, s):
        """channel: 'ail'|'ele'|'rud'"""
        p = f"/demo/stabilizer/sign-{channel}"
        nv = -signs[p]
        signs[p] = nv
        fg.set(p, nv)
        flipped.append({"t": s["t"], "channel": channel, "new_sign": nv,
                        "err_roll": s["err_roll"], "err_pitch": s["err_pitch"]})
        print(f"  >>> FLIP sign-{channel} -> {nv:+.0f} "
              f"(errR={s['err_roll']:+.1f} errP={s['err_pitch']:+.1f})")

    def abort_ok(s):
        return (abs(s["roll"]) > ABORT_ROLL or abs(s["pitch"]) > ABORT_PITCH
                or s["agl"] > ABORT_AGL or s["vs"] < ABORT_VS)

    # ---- Phase A: light rotor on wheels ----
    print("[Phase A] light rotor 0.55, 15 s")
    coll = fg.coll(0.55)
    for i in range(15):
        s = sample(fg)
        emit("A_light", coll, s)
        if s["crashed"] > 0.5:
            print("[FAIL] crashed on ground"); return dump(ts, log, signs, flipped, 2)
        time.sleep(1.0)

    # ---- Phase B: ramp to lift ----
    print(f"[Phase B] ramp to {LIFT_COLL}, watch attitude")
    coll = 0.55
    hold_until = None
    b_start = time.time()
    while time.time() - b_start < 90:
        s = sample(fg)
        if s["crashed"] > 0.5:
            print("[FAIL] crashed during lift test")
            return dump(ts, log, signs, flipped, 2)
        if abort_ok(s):
            print("[ABORT] attitude limits, killing lift")
            coll = fg.coll(1.0)
            emit("B_abort", coll, s)
            break
        if hold_until is None:
            coll = max(LIFT_COLL, coll - RAMP_RATE * 1.0)
            coll = fg.coll(coll)
            if coll <= LIFT_COLL + 1e-6:
                hold_until = time.time() + 35
                print("  [Phase B] lift collective reached, holding")
        emit("B_lift", coll, s)
        # flip decisions (once per channel per direction)
        if worst([e for e in log if e["phase"].startswith("B")], "err_roll", 6) > FLIP_ERR \
                and all(f["channel"] != "ail" for f in flipped):
            try_flip("ail", s)
        if worst([e for e in log if e["phase"].startswith("B")], "err_pitch", 6) > FLIP_ERR \
                and all(f["channel"] != "ele" for f in flipped):
            try_flip("ele", s)
        if abs(s["yaw_r"]) > 25 and worst(log, "yaw_r", 4) > 25 \
                and all(f["channel"] != "rud" for f in flipped):
            try_flip("rud", s)
        # once flipped and stable for 20 s, finish
        b_log = [e for e in log if e["phase"].startswith("B")]
        if hold_until and time.time() > hold_until:
            break
        if len(flipped) >= 2 and b_log and worst(b_log, "err_roll", 8) < 4 \
                and worst(b_log, "err_pitch", 8) < 4 and hold_until:
            break
        time.sleep(1.0)

    # ---- Phase C: land ----
    print("[Phase C] landing: flat pitch (1.0)")
    coll = fg.coll(1.0)
    for i in range(25):
        time.sleep(1.2)
        s = sample(fg)
        emit("C_land", coll, s)
        if s["crashed"] > 0.5:
            print("[NOTE] crashed during landing")
            return dump(ts, log, signs, flipped, 2)
        if i > 6 and s["agl"] < 1.2 and abs(s["vs"]) < 2.0:
            print("[OK] settled on ground")
            break

    return dump(ts, log, signs, flipped, 0)


def dump(ts, log, signs, flipped, rc):
    (LOG_DIR / f"calib_{ts}.json").write_text(
        json.dumps({"signs": signs, "flips": flipped, "log": log},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[log] logs/calib_{ts}.json")
    print(f"[final signs] {signs}  flips={flipped}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
