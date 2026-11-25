# -*- coding: utf-8 -*-
"""农场工人：在指定端口的 FG 实例上执行回合（重置→触发→判定），支持参数注入。
成功判定：到达 phase 17（完整科目：起飞-悬停-前飞-滚转-偏航-降落-停稳）。
"""
import json
import math
import os
import re
import socket
import subprocess
import sys
import time

PHNL_LAT, PHNL_LON, PAD_ALT = 21.32524, -157.94319, 21.3
PH_NAMES = {0:"idle",1:"ground",2:"liftoff",3:"climb",4:"hover",5:"forward",
            6:"roll-L",7:"level-1",8:"roll-R",9:"level-2",10:"yaw-L",11:"forward-2",
            12:"yaw-L",13:"forward-3",14:"yaw-L",15:"landing",16:"settled",17:"done"}


class FG:
    _VAL = re.compile(r"'([^']*)'")

    def __init__(self, port, tries=120):
        deadline = time.time() + tries * 2
        last = None
        while time.time() < deadline:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError as e:
                last = e
                time.sleep(2)
        else:
            raise ConnectionError(f"port {port}: {last}")
        self.sock.settimeout(2.0)
        time.sleep(0.3)
        self._rd()
        self._wait_fdm()

    def _wait_fdm(self):
        """等 FDM 初始化（fg_run_variant.py L89 同款）。冷启 >45s 时
        不等这里会导致写控制无效、rpm 恒 0 → not-ready（分析师发现 6）。"""
        t0 = time.time()
        while time.time() - t0 < 120:
            if self.get("/sim/signals/fdm-initialized") > 0.5:
                return
            time.sleep(2)

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
            return -1.0  # 修复：返回 None 会使调用方比较抛 TypeError（主任 bug1）
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


def kill_all_fgfs():
    """单实例农场约定：重启前清空全部 fgfs（本机 FG 为全局单例，
    且 crashed 残骸唯一恢复手段是重启）。"""
    subprocess.run(["taskkill", "/F", "/IM", "fgfs.exe"],
                   capture_output=True, timeout=30)
    time.sleep(2)


def relaunch(tag):
    kill_all_fgfs()
    # 清除脏 autosave（翻覆姿态会被存档，重启后侧躺出生、重置无效——主任 bug2）
    try:
        dirty = os.path.join(os.environ["TEMP"], "fghome_" + tag,
                             "autosave_2024_1.xml")
        if os.path.exists(dirty):
            os.remove(dirty)
    except (KeyError, OSError):
        pass
    bat = rf"C:\Users\Administrator\AppData\Local\Temp\fg_launch_{tag}.bat"
    subprocess.run(["wmic", "process", "call", "create", f"cmd /c {bat}"],
                   capture_output=True, timeout=30)


def reset_and_ready(fg, name):
    """确定性重置 + 引擎/姿态就绪检查。返回 (ok, info)"""
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
    time.sleep(1.5)

    # 引擎确保运转
    t0 = time.time()
    while time.time() - t0 < 45:
        rpm = fg.get("/rotors/main/rpm")
        if rpm > 380:
            break
        fg.set("/controls/engines/engine[0]/power", 1.0)
        fg.set("/controls/engines/engine[1]/power", 1.0)
        fg.set("/controls/engines/engine[0]/magnetos", 1)
        fg.set("/controls/engines/engine[1]/magnetos", 1)
        fg.set("/controls/rotor/brake", 0)
        if time.time() - t0 > 20 and rpm < 200:
            for n in (0, 1):
                fg.set(f"/controls/engines/engine[{n}]/starter", 1)
                fg.set(f"/controls/engines/engine[{n}]/ignition", 1)
            time.sleep(3)
            for n in (0, 1):
                fg.set(f"/controls/engines/engine[{n}]/starter", 0)
        time.sleep(2)
    rpm = fg.get("/rotors/main/rpm")
    roll = fg.get("/orientation/roll-deg")
    pitch = fg.get("/orientation/pitch-deg")
    agl = fg.get("/position/altitude-agl-ft")
    cr = fg.get("/sim/crashed")
    ok = (rpm > 380 and abs(roll) < 5 and abs(pitch) < 5
          and -1 < agl < 15 and cr < 0.5)
    return ok, {"rpm": rpm, "roll": roll, "pitch": pitch, "agl": agl, "crashed": cr}


def run_episode(fg, tag, params, ep_id):
    """单回合。返回 dict(result/score/...)。参数缺键时用 XML 默认值
    （与 rev8"无效项忽略"契约一致）。rev8 活跃维度：ramp_fast/punch/
    yaw_hold/damper/cyc_gain（ffw-gain 已被 rev8 移除，不再注入）。"""
    pd = lambda k, d: params.get(k, d)
    for p, v in [("/demo/stabilizer/sign-ail", 1.0),
                 ("/demo/stabilizer/sign-ele", 1.0),
                 ("/demo/stabilizer/sign-rud", -1.0),
                 ("/demo/seq/ffw-sign", 1.0),
                 ("/demo/tune/ramp-fast", pd("ramp_fast", 0.11)),
                 ("/demo/tune/punch-floor", pd("punch", 0.40)),
                 ("/demo/tune/yaw-hold", pd("yaw_hold", 1.0)),
                 ("/demo/tune/damper", pd("damper", 1.0)),
                 ("/demo/tune/cyc-gain", pd("cyc_gain", 1.0)),
                 ("/demo/tune/lo-trim", pd("lo_trim", -3.5)),
                 ("/demo/tune/ff-trq", pd("ff_trq", 0.6)),
                 ("/demo/tune/ff-band", pd("ff_band", 2.0)),
                 # ramp-fast 的别名层不在 seq start 触发（实测 ramp 恒 0.11），
                 # 必须直接写 ramp-rate；ramp-fast 双写保底旧版兼容
                 ("/demo/tune/ramp-rate", pd("ramp_fast", 0.11)),
                 ("/demo/tune/ramp-fast", pd("ramp_fast", 0.11)),
                 ("/demo/seq/run", 0), ("/demo/seq/start", 0), ("/demo/seq/done", 0)]:
        fg.set(p, v)
    fg.set("/demo/seq/run", 1)
    fg.set("/demo/seq/start", 1)

    t0 = time.time()
    max_phase = 0
    min_roll_after = 999
    flip_t = None
    seq_started = False
    while time.time() - t0 < 240:
        pn = fg.get("/demo/seq/phase")
        roll = fg.get("/orientation/roll-deg")
        cr = fg.get("/sim/crashed")
        run = fg.get("/demo/seq/run")
        max_phase = max(max_phase, pn)
        if pn >= 2:
            min_roll_after = min(min_roll_after, abs(roll))
        if pn == 17:
            return {"result": "success", "score": 1.0, "t": time.time() - t0,
                    "max_phase": 17, "ep": ep_id}
        if cr > 0.5:
            return {"result": "crashed", "score": max_phase / 34.0,
                    "t": time.time() - t0, "max_phase": max_phase,
                    "flip_t": flip_t, "ep": ep_id}
        if abs(roll) > 45 and flip_t is None:
            flip_t = time.time() - t0
        if abs(roll) > 120:
            return {"result": "flip", "score": max_phase / 34.0,
                    "t": time.time() - t0, "max_phase": max_phase,
                    "flip_t": flip_t, "ep": ep_id}
        # XML abort 路径会置 run=0（rollover 死锁时外环无需干等 240s，
        # 判定与超时一致，只是提前结束——分析师发现 5）
        if seq_started and run < 0.5:
            return {"result": "timeout", "score": max_phase / 34.0,
                    "t": time.time() - t0, "max_phase": max_phase,
                    "flip_t": flip_t, "ep": ep_id, "abort": 1}
        if run > 0.5:
            seq_started = True
        time.sleep(0.4)
    return {"result": "timeout", "score": max_phase / 34.0,
            "t": time.time() - t0, "max_phase": max_phase, "ep": ep_id}


def main():
    port = int(sys.argv[1])
    tag = sys.argv[2]
    params = json.loads(sys.argv[3])
    eps = int(sys.argv[4]) if len(sys.argv) > 4 else 2

    results = []
    for ep in range(eps):
        try:
            fg = FG(port)
        except ConnectionError as e:
            print(f"[{tag}] relaunching: {e}", flush=True)
            relaunch(tag)
            try:
                fg = FG(port)
            except ConnectionError:
                results.append({"result": "no-instance", "score": 0.0, "ep": ep})
                continue
        ok, info = reset_and_ready(fg, tag)
        if not ok:
            # not-ready 二次机会：崩溃残骸/冷启竞态 → 清实例重启再试一次
            #（crashed=1 的实例会永远监听但永远 not-ready，分析师批次2根因1）
            print(f"[{tag}] ep{ep} not-ready, relaunching once: {info}", flush=True)
            relaunch(tag)
            try:
                fg = FG(port)
            except ConnectionError:
                results.append({"result": "no-instance", "score": 0.0, "ep": ep})
                continue
            ok, info = reset_and_ready(fg, tag)
            if not ok:
                print(f"[{tag}] ep{ep} not ready again: {info}", flush=True)
                results.append({"result": "not-ready", "score": 0.0, "ep": ep, **info})
                continue
        r = run_episode(fg, tag, params, ep)
        r["params"] = params
        r["tag"] = tag
        results.append(r)
        print(f"[{tag}] ep{ep}: {r['result']} score={r['score']:.2f} "
              f"max_phase={r['max_phase']} ({PH_NAMES.get(r['max_phase'],'?')})",
              flush=True)
    print("RESULTS_JSON:" + json.dumps(results), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
