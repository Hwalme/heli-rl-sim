# -*- coding: utf-8 -*-
"""calib3.py -- fast transient observer at liftoff crossover.

Reads ONLY [pitch, ele] at max cadence (~0.9 s) through the collective
crossover, so the first seconds of the liftoff transient are visible.
Slow ramp: 1.0 -> 0.62 at 0.08/s, then 0.62 -> 0.48 at 0.012/s, hold.
Early abort: |pitch|>8 or |roll|>10 -> kill lift instantly, settle, disable.
Purpose: determine whether elevator moves WITH or AGAINST the pitch
deviation (sign question), record breakout collective + rate.

Log: logs/calib3_<ts>.json.  ASCII only.
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
FAST = ["/orientation/pitch-deg", "/controls/flight/elevator"]
SLOW = ["/orientation/roll-deg", "/position/altitude-agl-ft",
        "/velocities/vertical-speed-fps", "/sim/crashed",
        "/demo/stabilizer/active"]


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
    rows = []
    t0 = time.time()

    def row(**kw):
        kw["t"] = round(time.time() - t0, 2)
        rows.append(kw)
        print("  " + " ".join(f"{k}={v}" for k, v in kw.items()
                              if not isinstance(v, float))
              + " " + " ".join(f"{k}={v:+.2f}" for k, v in kw.items()
                               if isinstance(v, float)), flush=True)

    def slow():
        vals = [fg.get(p) for p in SLOW]
        return dict(zip(["roll", "agl", "vs", "crashed", "act"], vals))

    # parked setup
    fg.set("/demo/stabilizer/enabled", 0)
    fg.coll(1.0)
    fg.set("/controls/rotor/brake", 0)
    fg.set("/controls/engines/engine[0]/power", 1.0)
    fg.set("/controls/engines/engine[1]/power", 1.0)
    sg = fg.get("/demo/stabilizer/sign-ele")
    sa = fg.get("/demo/stabilizer/sign-ail")
    print(f"[start] sign-ele={sg:+.0f} sign-ail={sa:+.0f}", flush=True)

    # enable + immediate slow ramp, fast sampling on pitch/ele
    fg.set("/demo/stabilizer/enabled", 1)
    coll = 1.0
    state = "ramp1"
    busted = None
    while time.time() - t0 < 150:
        p = fg.get(FAST[0])
        e = fg.get(FAST[1])
        rec = {"pitch": p, "ele": e, "coll": coll, "phase": state}
        if len(rows) % 4 == 0:
            s = slow()
            rec.update({k: s[k] for k in ("roll", "agl", "vs")})
            if s["crashed"] > 0.5:
                row(**rec, **{"crashed": 1}); busted = "crashed"; break
            if s["act"] < 0.5:
                row(**rec); busted = "stab inactive"; break
        row(**rec)
        if abs(p) > 8.0:
            busted = f"pitch transient p={p:+.1f}"
            break
        if "roll" in rec and abs(rec["roll"]) > 12.0:
            busted = f"roll transient r={rec['roll']:+.1f}"
            break
        if state == "ramp1":
            coll = fg.coll(max(0.62, coll - 0.08 * 1.0))
            if coll <= 0.6201:
                state = "ramp2"
        elif state == "ramp2":
            coll = fg.coll(max(0.48, coll - 0.012 * 1.0))
            if coll <= 0.4801:
                state = "hold"
        else:
            s = slow()
            if s["agl"] > 1.2 and abs(p) < 6.0:
                ok_agg = 0
            # hold up to 40 s
            if rows and rows[0].get("t", 0) and time.time() - t0 > 0:
                pass
            if len([r for r in rows if r["phase"] == "hold"]) > 25:
                busted = "hold complete (no divergence)"
                break
    print(f"[result] {busted}", flush=True)

    # kill lift + settle + disable
    fg.coll(1.0)
    for _ in range(8):
        time.sleep(1.2)
        s = slow()
        row(pitch=fg.get(FAST[0]), ele=fg.get(FAST[1]), phase="settle",
            roll=s["roll"], agl=s["agl"], vs=s["vs"])
        if s["crashed"] > 0.5:
            print("[NOTE] crashed"); break
        if s["agl"] < 1.2 and abs(s["vs"]) < 2.0:
            break
    fg.set("/demo/stabilizer/enabled", 0)
    time.sleep(1.0)

    (LOG_DIR / f"calib3_{ts}.json").write_text(
        json.dumps(rows, indent=1), encoding="utf-8")
    print(f"[log] logs/calib3_{ts}.json", flush=True)
    # quick analysis: ele vs pitch trend during ramp2/hold
    seg = [r for r in rows if r.get("phase") in ("ramp2", "hold")
           and "pitch" in r]
    if len(seg) >= 4:
        import statistics
        dp = [seg[i + 1]["pitch"] - seg[i]["pitch"] for i in range(len(seg) - 1)]
        de = [seg[i + 1]["ele"] - seg[i]["ele"] for i in range(len(seg) - 1)]
        try:
            corr = statistics.correlation(dp, de)
            print(f"[analysis] d(pitch) vs d(ele) correlation = {corr:+.2f} "
                  f"(positive = ele moves WITH pitch = positive feedback)",
                  flush=True)
        except Exception as ex:
            print(f"[analysis] corr failed: {ex}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
