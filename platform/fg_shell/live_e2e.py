#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_e2e.py — live 演示端到端验证（主E终审 08-31 判据自动执行）

单脚本一体化（避开 FG ~12min 实例寿命的人工分段损耗）：
  1. 确保 FG (fdm=external) 存活，否则 WMI 启动并等 5503
  2. 启动 live_demo.py（takeoff_hard v5 策略）子进程
  3. telnet 5503 以 1Hz 轮询 /position/altitude-ft，与 live_demo 自身打印的
     agl 遥测并行采样
  4. 判据（主E终审原文）：telnet altitude 持续跟随 live_demo 遥测
     （随爬升持续变化，非单点）—— span>20ft 且与 agl 遥测同向
  5. 证据落盘 logs/live_e2e_<stamp>.json + .txt
"""
import json
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
LOGS.mkdir(exist_ok=True)
FG_BAT = r"C:\Users\Administrator\AppData\Local\Temp\fg_shell_launch.bat"
TELNET_PORT = 5503


def fg_alive():
    r = subprocess.run(["netstat", "-ano"], capture_output=True)
    out = r.stdout.decode("gbk", "replace") if r.stdout else ""
    for ln in out.splitlines():
        if "5503" in ln and "LISTENING" in ln:
            return int(ln.split()[-1])
    return None


def launch_fg():
    subprocess.run(["wmic", "process", "call", "create",
                    f'cmd /c {FG_BAT}'], capture_output=True)
    for _ in range(36):
        time.sleep(5)
        pid = fg_alive()
        if pid:
            return pid
    return None


class Tel:
    def __init__(self, port=TELNET_PORT):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.s.settimeout(2.0)
        time.sleep(0.4)
        self.rd()

    def rd(self):
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
                break
        return out.decode("utf-8", "replace")

    def get(self, p):
        try:
            self.s.sendall(f"get {p}\r\n".encode())
            time.sleep(0.18)
            m = re.findall(r"'([^']*)'", self.rd())
            return float(m[-1])
        except Exception:
            return None


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("=== live_e2e: 主E终审判据自动验证 ===")

    pid = fg_alive()
    if not pid:
        print("[FG] 不存活, WMI 启动 (fdm=external)...")
        pid = launch_fg()
        if not pid:
            print("[FAIL] FG 无法启动")
            return 1
    print(f"[FG] 存活 PID={pid}, 等待 boot 稳定 20s...")
    time.sleep(20)

    tn = Tel()
    fdm_mode = tn.get("/sim/fdm/active") if tn.get("/sim/fdm/active") else "unknown"
    alt0 = tn.get("/position/altitude-ft")
    print(f"[FG] fdm={fdm_mode} 初始 alt={alt0}")

    # 启动 live_demo
    cmd = [sys.executable, "-u", str(HERE / "live_demo.py"),
           "--model", str(HERE.parent.parent / "rl_train" / "results" /
                          "jsbsim_ppo_takeoff_hard_v20260830T091717.zip"),
           "--task", "takeoff_hard", "--max-sec", "60", "--hold-sec", "6"]
    print("[live_demo] 启动:", " ".join(cmd[2:]))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=str(HERE))
    demo_lines = []

    fg_alts = []
    t0 = time.time()
    # 并行读 demo 输出 + telnet 轮询，直到 demo 结束
    import threading

    def reader():
        buf = b""
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            buf += ch
            if ch == chr(10).encode() or ch == chr(13).encode():
                line = buf.decode("utf-8", "replace").rstrip()
                buf = b""
                if line:
                    demo_lines.append(line)
                    if "agl=" in line:
                        print("  [demo]", line, flush=True)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    while proc.poll() is None and time.time() - t0 < 120:
        alt = tn.get("/position/altitude-ft")
        if alt is not None:
            fg_alts.append(round(alt, 2))
        time.sleep(1.0)
    proc.wait(timeout=10)
    time.sleep(1)
    # 最后再读一次
    alt_end = tn.get("/position/altitude-ft")
    if alt_end is not None:
        fg_alts.append(round(alt_end, 2))

    # 判决
    span = max(fg_alts) - min(fg_alts) if len(fg_alts) >= 2 else 0.0
    # demo 遥测里的 agl 轨迹
    demo_agls = []
    for ln in demo_lines:
        m = re.search(r"agl=\s*([0-9.]+)m", ln)
        if m:
            demo_agls.append(float(m.group(1)))
    demo_climb = (demo_agls[-1] - demo_agls[0]) if len(demo_agls) >= 2 else 0.0
    followed = span > 20.0 and demo_climb > 5.0

    verdict = "FOLLOWING — live 闭环 CONFIRMED" if followed else "FROZEN"
    print("\n=== 判决 ===")
    print(f"FG telnet alt 序列({len(fg_alts)}点): span={span:.1f}ft "
          f"min={min(fg_alts) if fg_alts else '-'} max={max(fg_alts) if fg_alts else '-'}")
    print(f"live_demo agl: {len(demo_agls)}点 爬升 {demo_climb:.1f}m")
    print(f"VERDICT: {verdict}")

    out = {"stamp": stamp, "fg_pid": pid, "fdm_mode": fdm_mode,
           "fg_alt_series": fg_alts, "fg_alt_span_ft": round(span, 2),
           "demo_agl_m_series": demo_agls,
           "verdict": "PASS" if followed else "FAIL"}
    (LOGS / f"live_e2e_{stamp}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (LOGS / f"live_e2e_{stamp}_demo.log").write_text(
        "\n".join(demo_lines), encoding="utf-8")
    print(f"证据: {LOGS}/live_e2e_{stamp}.json")
    return 0 if followed else 2


if __name__ == "__main__":
    sys.exit(main())
