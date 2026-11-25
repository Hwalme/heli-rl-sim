# -*- coding: utf-8 -*-
"""翻转前遥测采样器（诊断工具）：复位→注入参数→触发→高频记录→崩溃后导出
最后 N 秒窗口。用法:
  python flip_sampler.py '{"ramp_fast":0.02,"punch":0.26,"yaw_hold":1.0,
                          "damper":0.5,"cyc_gain":0.5,"lo_trim":-3.5}'
输出: farm/flip_sample_<ts>.csv + 控制台打印最后 5 秒。
"""
import json
import re
import socket
import sys
import time
from datetime import datetime

PORT = 5501
PHNL_LAT, PHNL_LON, PAD_ALT = 21.32524, -157.94319, 21.3
_VAL = re.compile(r"'([^']*)'")

POLL = [("/demo/seq/phase", "ph"),
        ("/orientation/roll-deg", "roll"),
        ("/controls/flight/rudder", "rud"),
        ("/demo/stabilizer/rud-i", "rud_i"),
        ("/orientation/yaw-rate-degps", "yawr"),
        ("/demo/pilot/coll", "coll"),
        ("/position/altitude-agl-ft", "agl"),
        ("/sim/crashed", "crash")]


class T:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.s.settimeout(2.0)
        time.sleep(0.3)
        self._rd()

    def _rd(self):
        out = b""
        while True:
            try:
                c = self.s.recv(65536)
            except socket.timeout:
                break
            if not c:
                break
            out += c
            if out.rstrip().endswith(b">"):
                return out.decode("utf-8", "replace")
        return out.decode("utf-8", "replace")

    def get(self, p):
        try:
            self.s.sendall(f"get {p}\r\n".encode())
            time.sleep(0.02)
            m = _VAL.findall(self._rd())
            v = m[-1].strip() if m else "?"
            if v.lower() == "true":
                return 1.0
            if v.lower() == "false":
                return 0.0
            return float(v)
        except (OSError, ValueError):
            return float("nan")

    def set(self, p, v):
        try:
            self.s.sendall(f"set {p} {v}\r\n".encode())
            time.sleep(0.03)
            self._rd()
        except OSError:
            pass


def main():
    params = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    fg = T(PORT)
    # 复位（与 farm_worker.reset_and_ready 同款）
    fg.set("/sim/freeze/master", 1)
    time.sleep(0.4)
    for p, v in [("/position/latitude-deg", PHNL_LAT),
                 ("/position/longitude-deg", PHNL_LON),
                 ("/position/altitude-ft", PAD_ALT),
                 ("/orientation/roll-deg", 0), ("/orientation/pitch-deg", 0),
                 ("/orientation/heading-deg", 111.0),
                 ("/velocities/speed-north-fps", 0), ("/velocities/speed-east-fps", 0),
                 ("/velocities/speed-down-fps", 0), ("/velocities/vertical-speed-fps", 0)]:
        fg.set(p, v)
    time.sleep(0.4)
    fg.set("/sim/freeze/master", 0)
    t0 = time.time()
    while time.time() - t0 < 45:
        if fg.get("/rotors/main/rpm") > 380:
            break
        time.sleep(2)
    # 注入
    defaults = {"ramp_fast": 0.11, "punch": 0.40, "yaw_hold": 1.0,
                "damper": 1.0, "cyc_gain": 1.0, "lo_trim": -3.5}
    for k, d in defaults.items():
        key = {"ramp_fast": "/demo/tune/ramp-rate", "punch": "/demo/tune/punch-floor",
               "yaw_hold": "/demo/tune/yaw-hold", "damper": "/demo/tune/damper",
               "cyc_gain": "/demo/tune/cyc-gain", "lo_trim": "/demo/tune/lo-trim", "ff_trq": "/demo/tune/ff-trq"}[k]
        fg.set(key, params.get(k, d))
    fg.set("/demo/tune/ramp-fast", params.get("ramp_fast", 0.11))
    for p, v in [("/demo/stabilizer/sign-ail", 1.0), ("/demo/stabilizer/sign-ele", 1.0),
                 ("/demo/stabilizer/sign-rud", -1.0), ("/demo/seq/run", 0),
                 ("/demo/seq/start", 0), ("/demo/seq/done", 0)]:
        fg.set(p, v)
    fg.set("/demo/seq/run", 1)
    fg.set("/demo/seq/start", 1)

    rows = []
    t0 = time.time()
    while time.time() - t0 < 90:
        rec = {"t": round(time.time() - t0, 2)}
        for p, k in POLL:
            rec[k] = fg.get(p)
        rows.append(rec)
        if rec["crash"] == 1.0 or rec["ph"] >= 17:
            print(f"[sampler] end at t={rec['t']} crash={rec['crash']} phase={rec['ph']}")
            break
        time.sleep(0.15)
    fn = f"farm/flip_sample_{datetime.now().strftime('%H%M%S')}.csv"
    with open(fn, "w", encoding="utf-8") as f:
        f.write("t," + ",".join(k for _, k in POLL) + "\n")
        for r in rows:
            f.write(str(r["t"]) + "," + ",".join(str(r[k]) for _, k in POLL) + "\n")
    print(f"[sampler] saved {fn} ({len(rows)} rows)")
    print("== last 5s ==")
    print("t,ph,roll,rud,rud_i,yawr,coll,agl")
    for r in rows:
        if r["t"] >= rows[-1]["t"] - 5.0:
            print(f"{r['t']:5.2f},{int(r['ph']):2d},{r['roll']:8.2f},{r['rud']:6.3f},"
                  f"{r['rud_i']:6.3f},{r['yawr']:6.2f},{r['coll']:5.3f},{r['agl']:6.2f}")


if __name__ == "__main__":
    main()
