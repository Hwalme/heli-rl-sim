# -*- coding: utf-8 -*-
"""农场实例健康探针（只读，不写任何属性）。
用法: python farm/probe_instances.py
输出: 各端口连通性、ticks 内环频率、坠机标志、rpm —— 判断实例是"活/僵尸/死"。
"""
import re
import socket
import time

PORTS = {"A": 5501, "B": 5503, "C": 5505}
VAL = re.compile(r"'([^']*)'")


def probe(port):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
    except OSError as e:
        return {"port": port, "state": "DEAD", "err": str(e)}
    s.settimeout(1.5)
    try:
        s.recv(65536)
    except OSError:
        pass

    def get(p):
        try:
            s.sendall(f"get {p}\r\n".encode())
            time.sleep(0.05)
            m = VAL.findall(s.recv(65536).decode("utf-8", "replace"))
        except OSError:
            return None
        v = m[-1].strip() if m else "?"
        try:
            return float(v)
        except ValueError:
            return v

    ticks1 = get("/demo/stabilizer/ticks")
    time.sleep(2.0)
    ticks2 = get("/demo/stabilizer/ticks")
    hz = None
    if isinstance(ticks1, (int, float)) and isinstance(ticks2, (int, float)):
        hz = round((ticks2 - ticks1) / 2.0, 1)
    d = {
        "port": port,
        "ticks": ticks2,
        "inner_hz": hz,
        "crashed": get("/sim/crashed"),
        "rpm": get("/rotors/main/rpm"),
        "agl": get("/position/altitude-agl-ft"),
        "roll": get("/orientation/roll-deg"),
        "phase": get("/demo/seq/phase"),
        "seq_run": get("/demo/seq/run"),
    }
    cr = d["crashed"]
    hz = d["inner_hz"]
    cr_ok = isinstance(cr, (int, float))
    hz_ok = isinstance(hz, (int, float))
    if cr_ok and cr > 0.5:
        d["state"] = "WRECKED(需重启实例)"
    elif hz_ok and hz > 20:
        d["state"] = "ALIVE"
    else:
        d["state"] = "LISTENING-BUT-INNER-DEAD"
    s.close()
    return d


for tag, port in PORTS.items():
    d = probe(port)
    print(tag, port, json.dumps(d) if False else d)
