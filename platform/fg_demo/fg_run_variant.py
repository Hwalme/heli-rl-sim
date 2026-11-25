# -*- coding: utf-8 -*-
"""并行变体试飞器：连接指定端口的 FG 实例，设置前馈符号，触发科目序列，监控到结束。
用法: python fg_run_variant.py --port 5501 --name A --ffw-sign 1 [--ramp-floor 0.45]
"""
import json
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class FG:
    _VAL = re.compile(r"'([^']*)'")

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=8)
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
        self.sock.sendall(f"get {p}\r\n".encode())
        time.sleep(0.06)
        m = self._VAL.findall(self._rd())
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
        self.sock.sendall(f"set {p} {v}\r\n".encode())
        time.sleep(0.05)
        self._rd()


def main():
    port = 5501
    name = "A"
    ffw = 1.0
    floor = 0.45
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--port":
            port = int(args[i + 1])
        if a == "--name":
            name = args[i + 1]
        if a == "--ffw-sign":
            ffw = float(args[i + 1])
        if a == "--ramp-floor":
            floor = float(args[i + 1])

    tag = datetime.now().strftime("%H%M%S")
    t0 = time.time()
    try:
        fg = FG(port)
    except OSError as e:
        print(f"[{name}] 连接失败: {e}")
        return 3

    # 预检+符号+前馈符号
    t0w = time.time()
    while time.time() - t0w < 90 and fg.get("/sim/signals/fdm-initialized") < 0.5:
        time.sleep(2)
    for p, v in [("/demo/stabilizer/sign-ail", 1.0),
                 ("/demo/stabilizer/sign-ele", 1.0),
                 ("/demo/stabilizer/sign-rud", -1.0),
                 ("/demo/seq/ffw-sign", ffw),
                 ("/controls/engines/engine[0]/power", 1.0),
                 ("/controls/engines/engine[1]/power", 1.0),
                 ("/controls/engines/engine[0]/magnetos", 1),
                 ("/controls/engines/engine[1]/magnetos", 1),
                 ("/controls/rotor/brake", 0),
                 ("/demo/seq/ramp-floor", floor)]:
        fg.set(p, v)

    # ---- 确定性状态重置：冻结 -> 置位置/姿态/速度 -> 解冻 ----
    # （翻机后歪斜姿态会被 autosave 存成出生点，不重置=侧躺着出生=假失败）
    PHNL_LAT, PHNL_LON, PAD_ALT = 21.32524, -157.94319, 21.3
    fg.set("/sim/freeze/master", 1)
    time.sleep(0.5)
    fg.set("/position/latitude-deg", PHNL_LAT)
    fg.set("/position/longitude-deg", PHNL_LON)
    fg.set("/position/altitude-ft", PAD_ALT)
    fg.set("/orientation/roll-deg", 0)
    fg.set("/orientation/pitch-deg", 0)
    fg.set("/orientation/heading-deg", 111.0)
    for vp in ["/velocities/speed-north-fps", "/velocities/speed-east-fps",
               "/velocities/speed-down-fps", "/velocities/vertical-speed-fps",
               "/velocities/uBody-fps", "/velocities/vBody-fps", "/velocities/wBody-fps",
               "/velocities/groundspeed-kt"]:
        fg.set(vp, 0)
    fg.set("/orientation/roll-rate-degps", 0)
    fg.set("/orientation/pitch-rate-degps", 0)
    fg.set("/orientation/yaw-rate-degps", 0)
    time.sleep(0.5)
    fg.set("/sim/freeze/master", 0)
    time.sleep(2.0)

    roll0 = fg.get("/orientation/roll-deg")
    pitch0 = fg.get("/orientation/pitch-deg")
    agl0v = fg.get("/position/altitude-agl-ft")
    ok_att = abs(roll0) < 5 and abs(pitch0) < 5 and -1 < agl0v < 15
    print(f"[{name}] reset: roll={roll0:+.2f} pitch={pitch0:+.2f} agl={agl0v:.2f} "
          f"({'OK' if ok_att else 'FAIL'})", flush=True)
    if not ok_att:
        print(f"[{name}] 重置后姿态异常，放弃（需重启实例）")
        return 3

    rpm = fg.get("/rotors/main/rpm")
    t1 = fg.get("/demo/stabilizer/ticks")
    time.sleep(1.5)
    t2 = fg.get("/demo/stabilizer/ticks")
    crashed0 = fg.get("/sim/crashed")
    print(f"[{name}] rpm={rpm:.0f} inner={(t2-t1)/1.5:.0f}Hz ffw={ffw:+.0f} "
          f"floor={floor:.2f} crashed0={crashed0:.0f}", flush=True)
    if crashed0 > 0.5 or rpm < 380 or t2 <= t1:
        print(f"[{name}] 未就绪，放弃")
        return 3

    # 触发
    fg.set("/demo/seq/run", 0)
    fg.set("/demo/seq/start", 0)
    fg.set("/demo/seq/done", 0)
    fg.set("/demo/seq/run", 1)
    fg.set("/demo/seq/start", 1)
    t0 = time.time()
    t_frame = t0
    last_phase = -1
    flip_time = None
    while time.time() - t0 < 300:
        pn = fg.get("/demo/seq/phase")
        agl = fg.get("/position/altitude-agl-ft")
        vs = fg.get("/velocities/vertical-speed-fps")
        roll = fg.get("/orientation/roll-deg")
        hdg = fg.get("/orientation/heading-deg")
        coll = fg.get("/demo/pilot/coll")
        cr = fg.get("/sim/crashed")
        if pn != last_phase:
            print(f"[{name}] t={time.time()-t0:5.1f}s -> phase {pn:.0f}", flush=True)
            last_phase = pn
        if pn == 17:
            print(f"[{name}] DONE: agl={agl:.2f} vs={vs:.2f} "
                  f"总耗时={time.time()-t0:.0f}s", flush=True)
            with open(LOG_DIR / f"variant_{name}_{tag}.json", "w", encoding="utf-8") as f:
                json.dump({"name": name, "ffw": ffw, "floor": floor,
                           "result": "success", "t": time.time() - t0,
                           "agl": agl, "vs": vs}, f)
            return 0
        if cr > 0.5 or abs(roll) > 45:
            flip_time = time.time() - t0
            print(f"[{name}] FLIP/CRASH at t={flip_time:.1f}s roll={roll:.1f} "
                  f"coll={coll:.3f}", flush=True)
            with open(LOG_DIR / f"variant_{name}_{tag}.json", "w", encoding="utf-8") as f:
                json.dump({"name": name, "ffw": ffw, "floor": floor,
                           "result": "flip", "t": flip_time,
                           "roll": roll, "coll": coll}, f)
            return 2
        time.sleep(max(0.0, 0.4 - (time.time() - t_frame)))
        t_frame = time.time()
    print(f"[{name}] 超时未完成")
    return 2


if __name__ == "__main__":
    sys.exit(main())
