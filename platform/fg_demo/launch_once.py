# -*- coding: utf-8 -*-
"""最小发射脚本：不传送（新实例出生即干净），只验证→设参数→触发→逐2s记录全程。
用法: python launch_once.py <port> <ramp_fast> <punch>
"""
import json
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"


class FG:
    _VAL = re.compile(r"'([^']*)'")

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", int(port)), timeout=8)
        self.sock.settimeout(2.0)
        time.sleep(0.4)
        self._rd()

    def _rd(self):
        out = b""
        while True:
            try:
                c = self.sock.recv(65536)
            except socket.timeout:
                break
            if not c:
                break
            out += c
            if out.rstrip().endswith(b">"):
                return out.decode("utf-8", "replace")
        return out.decode("utf-8", "replace")

    def get(self, p, default=0.0):
        try:
            self.sock.sendall(f"get {p}\r\n".encode())
            time.sleep(0.05)
            m = self._VAL.findall(self._rd())
        except OSError:
            return default
        v = m[-1].strip() if m else "?"
        if v.lower() == "true":
            return 1.0
        if v.lower() == "false":
            return 0.0
        try:
            return float(v)
        except ValueError:
            return default

    def set(self, p, v):
        try:
            self.sock.sendall(f"set {p} {v}\r\n".encode())
            time.sleep(0.04)
            self._rd()
        except OSError:
            pass


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "5501"
    ramp = float(sys.argv[2]) if len(sys.argv) > 2 else 0.015
    punch = float(sys.argv[3]) if len(sys.argv) > 3 else 0.28

    fg = FG(port)
    # 就绪验证（不传送！出生点本来就是干净的）
    t0 = time.time()
    while time.time() - t0 < 120:
        fdm = fg.get("/sim/signals/fdm-initialized")
        rpm = fg.get("/rotors/main/rpm")
        cr = fg.get("/sim/crashed") if fdm > 0.5 else 0.0
        if fdm > 0.5 and cr > 0.5:
            print("[ABORT] 出生即坠机，实例脏，需重启")
            return 3
        if fdm > 0.5 and rpm > 380:
            break
        fg.set("/controls/engines/engine[0]/power", 1.0)
        fg.set("/controls/engines/engine[1]/power", 1.0)
        fg.set("/controls/engines/engine[0]/magnetos", 1)
        fg.set("/controls/engines/engine[1]/magnetos", 1)
        fg.set("/controls/rotor/brake", 0)
        time.sleep(2)
    roll = fg.get("/orientation/roll-deg")
    pitch = fg.get("/orientation/pitch-deg")
    agl = fg.get("/position/altitude-agl-ft")
    print(f"[就绪] rpm={fg.get('/rotors/main/rpm'):.0f} roll={roll:+.2f} pitch={pitch:+.2f} agl={agl:.2f} "
          f"lat={fg.get('/position/latitude-deg'):.5f} lon={fg.get('/position/longitude-deg'):.5f}", flush=True)
    if abs(roll) > 5 or abs(pitch) > 5 or not (-1 < agl < 15):
        print("[ABORT] 出生姿态异常")
        return 3

    # 参数 + 触发
    for p, v in [("/demo/tune/ramp-rate", ramp),
                 ("/demo/tune/punch-floor", punch),
                 ("/demo/tune/ramp-fast", ramp),
                 ("/demo/stabilizer/enabled", 1),
                 ("/demo/seq/run", 0), ("/demo/seq/start", 0), ("/demo/seq/done", 0)]:
        fg.set(p, v)
    print(f"[发射] ramp={ramp} punch={punch} (01:04 成功配方复现)", flush=True)
    fg.set("/demo/seq/run", 1)
    fg.set("/demo/seq/start", 1)

    ts = datetime.now().strftime("%H%M%S")
    frames = []
    PH = {0:"idle",1:"ground",2:"liftoff",3:"climb",4:"hover",5:"forward",6:"rollL",7:"lvl",
          8:"rollR",9:"lvl",10:"yawL",11:"fwd2",12:"yawL",13:"fwd3",14:"yawL",15:"land",
          16:"settle",17:"DONE"}
    t0 = time.time()
    while time.time() - t0 < 300:
        f = {"t": round(time.time() - t0, 1),
             "ph": PH.get(int(fg.get("/demo/seq/phase")), "?"),
             "agl": round(fg.get("/position/altitude-agl-ft"), 2),
             "vs": round(fg.get("/velocities/vertical-speed-fps"), 2),
             "coll": round(fg.get("/demo/pilot/coll"), 3),
             "roll": round(fg.get("/orientation/roll-deg"), 2),
             "pitch": round(fg.get("/orientation/pitch-deg"), 2),
             "hdg": round(fg.get("/orientation/heading-deg"), 1),
             "rud": round(fg.get("/controls/flight/rudder"), 3),
             "cr": fg.get("/sim/crashed")}
        frames.append(f)
        print(f"  [{f['t']:6.1f}s] {f['ph']:<8} agl={f['agl']:7.2f} vs={f['vs']:+6.1f} "
              f"coll={f['coll']:.3f} roll={f['roll']:+6.1f} pitch={f['pitch']:+6.1f} "
              f"hdg={f['hdg']:6.1f} rud={f['rud']:+5.2f}", flush=True)
        if f["ph"] == "DONE":
            print("[结果] 完整科目达成！", flush=True)
            break
        if f["cr"] > 0.5 or abs(f["roll"]) > 45:
            print(f"[结果] 坠机/翻转 @{f['t']}s", flush=True)
            break
        time.sleep(2.0)

    out = LOG_DIR / f"launch_once_{ts}.json"
    out.write_text(json.dumps(frames, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[日志] {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
