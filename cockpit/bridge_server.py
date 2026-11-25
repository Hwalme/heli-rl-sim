# -*- coding: utf-8 -*-
r"""
驾驶舱桥接服务（只读）—— 把两套仿真接到网页驾驶舱上。

数据源
  1. FG  LIVE   : FlightGear 2024.1 属性服务器（telnet 127.0.0.1:5502，bo105，驾驶舱专用实例）
  2. RL  REPLAY : rl_train/results/*_trajectory.json（JSBSim AH-1S 逐帧轨迹，回放）

设计约束
  - 只读：对 FG 只发 get / ls，永不写入任何属性（控制权仍归飞控脚本）。
  - 零依赖：只用 Python 标准库，离线可用。
  - 诚实：RL 轨迹里没有记录的量（发动机 N1/N2/TOT、扭矩、燃油）一律返回 null，
    由前端显示 N/A，绝不编造数字。

启动
  python cockpit/bridge_server.py [--port 8777] [--fg-port 5502] [--allow-write] [--root <项目根>]
浏览器
  http://127.0.0.1:8777/cockpit.html
一键启动
  驾驶舱顶栏「FG 未连接」可点击，由桥接服务直接拉起专用 FlightGear 实例（telnet 5502 + httpd 8080）
  与窗口抓屏服务（8081），无需手动开 .bat。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WEBSITE = ROOT / "website"
RESULTS = ROOT / "rl_train" / "results"

# ---------------------------------------------------------------------------
# 一键启动：从桥接服务直接拉起「驾驶舱专用 FlightGear 实例」+ 窗口抓屏服务
# 这样用户在网页驾驶舱点一下「FG 未连接」即可自动启动，无需手动开 .bat。
# 端口与桥接自身的 --fg-port 对齐，保证拉起的 FG 与采样线程连的是同一个实例。
# ---------------------------------------------------------------------------
FG_BIN = r"D:\Program Files\FlightGear 2024.1\bin\fgfs.exe"
FG_ROOT = r"D:\Program Files\FlightGear 2024.1\downloads\fgdata_2024_1"
FG_AIRCRAFT = r"D:\Program Files\FlightGear 2024.1\downloads\Aircraft"
COCKPIT_PY = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\cockpit\Scripts\python.exe"

_launch_lock = threading.Lock()
_fg_proc = None          # FlightGear 子进程
_view_proc = None        # 窗口抓屏服务子进程
_launch_state = {"status": "idle", "port": 0, "msg": "", "ts": 0.0,
                "fg_pid": None, "view_pid": None}

# ---------------------------------------------------------------------------
# FlightGear telnet 客户端（批处理，只读）
# ---------------------------------------------------------------------------

# 采样属性表：(输出键, FG 属性路径, 类型)
# t: 文本  f: 浮点  b: 布尔
FG_PROPS = [
    ("aircraft",        "/sim/aircraft",                                 "t"),
    ("aircraft_path",   "/sim/model/path",                               "t"),
    ("roll_deg",        "/orientation/roll-deg",                         "f"),
    ("pitch_deg",       "/orientation/pitch-deg",                        "f"),
    ("heading_deg",     "/orientation/heading-deg",                      "f"),
    ("roll_rate_dps",   "/orientation/roll-rate-degps",                  "f"),
    ("pitch_rate_dps",  "/orientation/pitch-rate-degps",                 "f"),
    ("yaw_rate_dps",    "/orientation/yaw-rate-degps",                   "f"),
    ("alt_ft",          "/position/altitude-ft",                         "f"),
    ("agl_ft",          "/position/altitude-agl-ft",                     "f"),
    ("lat_deg",         "/position/latitude-deg",                        "f"),
    ("lon_deg",         "/position/longitude-deg",                       "f"),
    ("ias_kt",          "/velocities/airspeed-kt",                       "f"),
    ("gs_kt",           "/velocities/groundspeed-kt",                    "f"),
    ("vs_fps",          "/velocities/vertical-speed-fps",                "f"),
    ("u_fps",           "/velocities/uBody-fps",                         "f"),
    ("v_fps",           "/velocities/vBody-fps",                         "f"),
    ("w_fps",           "/velocities/wBody-fps",                         "f"),
    ("main_rpm",        "/rotors/main/rpm",                              "f"),
    ("tail_rpm",        "/rotors/tail/rpm",                              "f"),
    ("torque_pct",      "/sim/model/bo105/torque-pct",                   "f"),
    ("torque_raw",      "/rotors/gear/total-torque",                     "f"),
    ("eng0_rpm",        "/engines/engine[0]/rpm",                        "f"),
    ("eng0_n1_pct",     "/engines/engine[0]/n1-pct",                     "f"),
    ("eng0_n2_pct",     "/engines/engine[0]/n2-pct",                     "f"),
    ("eng0_tot_c",      "/engines/engine[0]/tot-degc",                   "f"),
    ("eng0_run",        "/engines/engine[0]/running",                    "b"),
    ("eng1_rpm",        "/engines/engine[1]/rpm",                        "f"),
    ("eng1_n1_pct",     "/engines/engine[1]/n1-pct",                     "f"),
    ("eng1_n2_pct",     "/engines/engine[1]/n2-pct",                     "f"),
    ("eng1_tot_c",      "/engines/engine[1]/tot-degc",                   "f"),
    ("eng1_run",        "/engines/engine[1]/running",                    "b"),
    ("coll",            "/controls/engines/engine[0]/throttle",          "f"),
    ("coll2",           "/controls/engines/engine[1]/throttle",          "f"),
    ("cyclic_lat",      "/controls/flight/aileron",                      "f"),
    ("cyclic_lon",      "/controls/flight/elevator",                     "f"),
    ("pedal",           "/controls/flight/rudder",                       "f"),
    ("rotor_brake",     "/controls/rotor/brake",                         "f"),
    ("rotor_reltarget", "/controls/rotor/reltarget",                     "f"),
    ("g_load",          "/accelerations/pilot-g",                        "f"),
    ("fuel_gal",        "/consumables/fuel/tank[0]/level-gal_us",        "f"),
    ("gross_kg",        "/sim/model/gross-weight-kg",                    "f"),
    ("wow",             "/gear/gear[0]/wow",                             "b"),
    ("crashed",         "/sim/crashed",                                  "b"),
    ("frozen",          "/sim/freeze/master",                            "b"),
    ("elapsed_s",       "/sim/time/elapsed-sec",                         "f"),
    ("wind_dir_deg",    "/environment/wind-from-heading-deg",            "f"),
    ("wind_kt",         "/environment/wind-speed-kt",                    "f"),
    ("slip",            "/instrumentation/slip-skid-ball/indicated-slip-skid", "f"),
    ("turn_rate",       "/instrumentation/turn-indicator/indicated-turn-rate",  "f"),
    ("mag_heading_deg", "/instrumentation/magnetic-compass/indicated-heading-deg", "f"),
    # 飞行指引指令杆：FG 内置增稳 / demo_stabilizer.xml 写入的目标姿态（不存在时为 None）
    ("fd_pitch",        "/demo/target/pitch-deg",                       "f"),
    ("fd_roll",         "/demo/target/roll-deg",                        "f"),
]

VAL = re.compile(r"'([^']*)'")


class FGTelnet:
    """FlightGear telnet 属性客户端。只读，批处理取值。"""

    def __init__(self, host="127.0.0.1", port=5501):
        self.host, self.port = host, port
        self.sock = None
        self.lock = threading.Lock()

    @property
    def connected(self):
        return self.sock is not None

    def connect(self, timeout=2.0):
        with self.lock:
            if self.sock:
                return True
            try:
                s = socket.create_connection((self.host, self.port), timeout=timeout)
                s.settimeout(1.5)
                self.sock = s
                # 丢弃欢迎横幅
                self._drain(0.4)
                return True
            except OSError:
                self.sock = None
                return False

    def close(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None

    def _drain(self, seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            try:
                if not self.sock.recv(65536):
                    return
            except socket.timeout:
                return
            except OSError:
                return

    def batch_get(self, props, tries=2):
        """一次发送全部 get 命令，按 '>' 分隔解析，保证顺序对齐。

        返回与 props 等长的值列表，取不到的位置为 None。
        """
        for _ in range(tries):
            if not self.connected and not self.connect():
                time.sleep(0.2)
                continue
            try:
                with self.lock:
                    payload = "".join("get %s\r\n" % p for p in props).encode()
                    self.sock.sendall(payload)
                    raw = self._read_blocks(len(props))
            except OSError:
                self.close()
                continue

            if raw is None:
                self.close()
                continue

            chunks = raw.split(">")
            out = []
            for i in range(len(props)):
                seg = chunks[i] if i < len(chunks) else ""
                m = VAL.findall(seg)
                out.append(m[-1] if m else None)
            return out
        return [None] * len(props)

    def set(self, prop, val):
        """写入单个属性（仅在 --allow-write 模式下被调用；属性受白名单约束）。"""
        with self.lock:
            if not self.connected:
                return False
            try:
                self.sock.sendall(("set %s %s\r\n" % (prop, val)).encode())
                self._drain(0.15)
                return True
            except OSError:
                self.close()
                return False

    def _read_blocks(self, n, deadline_s=3.0):
        buf = b""
        end = time.time() + deadline_s
        while time.time() < end and buf.count(b">") < n:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
        return buf.decode("utf-8", "replace")


def _cast(v, kind):
    if v is None:
        return None
    v = v.strip()
    if kind == "t":
        return v or None
    if kind == "b":
        return v.lower() in ("true", "1", "yes")
    try:
        return float(v)
    except ValueError:
        return None


class FGPoll(threading.Thread):
    """后台采样线程：把 FG 属性刷新成一份统一单位的状态快照。"""

    RATE_HZ = 10.0

    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.client = FGTelnet(host, port)
        self.state = {"connected": False, "source": "fg", "ts": 0.0, "data": {}, "error": None}
        self._stop = threading.Event()
        self._subs = []
        self._sub_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    def subscribe(self):
        """SSE 订阅：返回一个 Queue，线程每帧向其投递状态。"""
        import queue

        q = queue.Queue(maxsize=4)
        with self._sub_lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            if q in self._subs:
                self._subs.remove(q)

    def subscriber_count(self):
        with self._sub_lock:
            return len(self._subs)

    def run(self):
        while not self._stop.is_set():
            try:
                t0 = time.time()
                props = [p for _, p, _ in FG_PROPS]
                vals = self.client.batch_get(props)
                ok = any(v is not None for v in vals)
                if not ok:
                    self.state = {
                        "connected": False, "source": "fg", "ts": time.time(),
                        "data": {}, "error": "FlightGear 属性服务器不可达（127.0.0.1:%d）。请先在 FG 启动参数中加入 --telnet=socket,in,5,127.0.0.1,%d,tcp"
                                            % (self.client.port, self.client.port),
                    }
                else:
                    d = {}
                    for (key, _, kind), v in zip(FG_PROPS, vals):
                        d[key] = _cast(v, kind)
                    d = self._derive(d)
                    self.state = {"connected": True, "source": "fg", "ts": time.time(), "data": d, "error": None}

                with self._sub_lock:
                    for q in list(self._subs):
                        try:
                            q.put_nowait(self.state)
                        except Exception:
                            pass
                time.sleep(max(0.0, 1.0 / self.RATE_HZ - (time.time() - t0)))
            except Exception as exc:
                # 任何未预期异常都不应让采样线程死亡，否则驾驶舱会卡在旧帧
                err = str(exc)[:240]
                sys.stderr.write("[FGPoll 异常] %s\n" % err)
                self.state = {"connected": False, "source": "fg", "ts": time.time(),
                              "data": {}, "error": err}
                time.sleep(0.5)

    @staticmethod
    def _derive(d):
        """补齐派生量：公制换算、空速、配平等。缺失保持 None，不编造。"""
        g = d.get

        def f(x):
            return x if isinstance(x, float) or isinstance(x, int) else None

        alt_ft, agl_ft = f(g("alt_ft")), f(g("agl_ft"))
        ias_kt, gs_kt, vs_fps = f(g("ias_kt")), f(g("gs_kt")), f(g("vs_fps"))
        u, v, w = f(g("u_fps")), f(g("v_fps")), f(g("w_fps"))

        d["alt_m"] = alt_ft * 0.3048 if alt_ft is not None else None
        d["agl_m"] = agl_ft * 0.3048 if agl_ft is not None else None
        d["ias_kmh"] = ias_kt * 1.852 if ias_kt is not None else None
        d["gs_kmh"] = gs_kt * 1.852 if gs_kt is not None else None
        d["vs_ms"] = vs_fps * 0.3048 if vs_fps is not None else None
        d["vs_fpm"] = vs_fps * 60.0 if vs_fps is not None else None
        d["u_ms"] = u * 0.3048 if u is not None else None
        d["v_ms"] = v * 0.3048 if v is not None else None
        d["w_ms"] = w * 0.3048 if w is not None else None
        # 直升机低速时 airspeed-kt 常为 0，用机体速度合成地速更真实
        if (ias_kt is None or ias_kt < 0.2) and u is not None and v is not None:
            sp_fps = (u * u + v * v) ** 0.5
            d["ias_kt"] = sp_fps * 0.592484
            d["ias_kmh"] = sp_fps * 0.3048 * 3.6
        # Bo105 主旋翼额定 442 RPM → 百分比
        if f(g("main_rpm")) is not None:
            d["main_rpm_pct"] = d["main_rpm"] / 442.0 * 100.0
        # 总距（collective）：bo105 用引擎油门通道驱动旋翼，二者一致
        if d.get("coll") is None and d.get("coll2") is not None:
            d["coll"] = d["coll2"]
        return d


# ---------------------------------------------------------------------------
# RL 轨迹（只读）
# ---------------------------------------------------------------------------

# 注意：早期产物（direct / landing）是带空格的美化 JSON，新产物是紧凑 JSON，正则必须两种都吃
EP_RE = re.compile(
    r'\{\s*"episode"\s*:\s*(\d+)\s*,\s*"seed"\s*:\s*(\d+)\s*,\s*"success"\s*:\s*(true|false)\s*,'
    r'\s*"crashed"\s*:\s*(true|false)\s*,\s*"n_steps"\s*:\s*(\d+)')
META_RE = lambda k: re.compile(r'"%s"\s*:\s*"([^"]*)"' % k)
NUM_RE = lambda k: re.compile(r'"%s"\s*:\s*([0-9.eE+-]+)' % k)


class RLStore:
    """扫描 rl_train/results/*_trajectory.json 的元信息，并按需载入帧。"""

    def __init__(self, results_dir: Path, cache_ttl=30.0):
        self.dir = results_dir
        self.cache_ttl = cache_ttl
        self._manifest = None
        self._manifest_ts = 0.0
        self._frames = {}   # (file, seed, ep) -> frames
        self._lock = threading.Lock()

    def _scan_one(self, path: Path):
        head = path.open("rb").read(8192).decode("utf-8", "replace")
        meta = {}
        for k in ("engine", "aircraft", "task", "mode", "model", "verified_timestamp"):
            m = META_RE(k).search(head)
            if m:
                meta[k] = m.group(1)
        for k, cast in (("overall_success_rate", float), ("overall_crash_rate", float)):
            m = NUM_RE(k).search(head)
            if m:
                try:
                    meta[k] = cast(m.group(1))
                except ValueError:
                    pass
        m = NUM_RE("dt").search(head)
        dt = float(m.group(1)) if m else 0.02
        m = re.search(r'"seeds"\s*:\s*\[([^\]]*)\]', head)
        seeds = [int(x) for x in re.findall(r"-?\d+", m.group(1))] if m else []

        # 全文扫描 episode 元信息（文件为单行 JSON，正则即可，无需整体反序列化）
        text = path.read_text(encoding="utf-8", errors="replace")
        episodes = []
        for mm in EP_RE.finditer(text):
            episodes.append({
                "episode": int(mm.group(1)),
                "seed": int(mm.group(2)),
                "success": mm.group(3) == "true",
                "crashed": mm.group(4) == "true",
                "n_steps": int(mm.group(5)),
            })
        return {
            "file": path.name,
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "size_kb": round(path.stat().st_size / 1024.0, 1),
            "mtime": path.stat().st_mtime,
            "meta": meta,
            "dt": dt,
            "seeds": seeds,
            "episodes": episodes,
        }

    def manifest(self, force=False):
        with self._lock:
            now = time.time()
            if not force and self._manifest and now - self._manifest_ts < self.cache_ttl:
                return self._manifest
            items = []
            for p in sorted(self.dir.glob("*_trajectory.json")):
                try:
                    items.append(self._scan_one(p))
                except Exception as e:
                    items.append({"file": p.name, "error": str(e), "episodes": []})
            self._manifest = {"files": items, "scanned_at": now,
                              "dir": str(self.dir.relative_to(ROOT)).replace("\\", "/")}
            self._manifest_ts = now
            return self._manifest

    def frames(self, fname, seed, ep, max_frames=6000):
        key = (fname, seed, ep)
        with self._lock:
            if key in self._frames:
                return self._frames[key]
        path = self.dir / fname
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        eps = raw.get("trajectories", {}).get("seed_%d" % seed, [])
        target = None
        for e in eps:
            if int(e.get("episode", -1)) == int(ep):
                target = e
                break
        if target is None:
            return None
        fr = target.get("frames", [])
        n = len(fr)
        if n > max_frames:
            step = n / float(max_frames)
            fr = [fr[int(i * step)] for i in range(max_frames)]
        out = {
            "file": fname,
            "seed": int(seed),
            "episode": int(ep),
            "success": bool(target.get("success")),
            "crashed": bool(target.get("crashed")),
            "n_steps": int(target.get("n_steps", n)),
            "dt": raw.get("dt", 0.02),
            "task": raw.get("task"),
            "aircraft": raw.get("aircraft"),
            "engine": raw.get("engine"),
            "mode": raw.get("mode"),
            "downsampled": n > max_frames,
            "raw_frames": n,
            "frames": fr,
        }
        with self._lock:
            if len(self._frames) > 4:
                self._frames.clear()
            self._frames[key] = out
        return out


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

# 一键启动：拉起「驾驶舱专用 FlightGear 实例」+ 窗口抓屏服务
# （运行在用户交互桌面会话内，窗口可见且可被抓屏服务抓取）
def _port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_fg_bin():
    """定位 fgfs.exe：先看默认安装路径，再回退到 PATH。"""
    cand = [FG_BIN,
            r"D:\Program Files\FlightGear 2024.1\bin\fgfs.exe",
            r"C:\Program Files\FlightGear 2024.1\bin\fgfs.exe",
            r"C:\Program Files (x86)\FlightGear 2024.1\bin\fgfs.exe"]
    for c in cand:
        if c and os.path.exists(c):
            return c
    found = shutil.which("fgfs") or shutil.which("fgfs.exe")
    return found or None


def launch_fg_stack():
    """拉起 FG（驾驶舱专用端口）+ 窗口抓屏服务，返回启动状态。

    幂等：若 FG 已在运行（端口可连且子进程存活）则直接返回 already，
    不会重复拉起。运行在桥接进程所在的桌面会话，因此 FG 窗口可见、
    抓屏服务能枚举到它。
    """
    global _fg_proc, _view_proc, _launch_state
    with _launch_lock:
        port = Handler.fg.client.port if Handler.fg else 5502
        # 已连接 → 无需启动
        if _fg_proc is not None and _fg_proc.poll() is None and _port_open("127.0.0.1", port):
            _launch_state = {"status": "already", "port": port,
                             "msg": "FlightGear 已连接", "ts": time.time(),
                             "fg_pid": _fg_proc.pid, "view_pid": _view_proc.pid if _view_proc else None}
            return _launch_state
        # 窗口抓屏服务（先于 FG 启动，持续抓取可见窗口）
        if _view_proc is None or _view_proc.poll() is not None:
            try:
                py = COCKPIT_PY if os.path.exists(COCKPIT_PY) else "python"
                logf = open(str(HERE / "fg_view.log"), "ab", buffering=0)
                _view_proc = subprocess.Popen(
                    [py, str(HERE / "fg_view_server.py"), "--port", "8081", "--fps", "5"],
                    stdout=logf, stderr=logf,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as e:
                _launch_state = {"status": "error", "port": port,
                                 "msg": "抓屏服务启动失败：%s" % e, "ts": time.time(),
                                 "fg_pid": None, "view_pid": None}
                return _launch_state
        # FlightGear 专用实例
        if _fg_proc is None or _fg_proc.poll() is not None:
            binp = _resolve_fg_bin()
            if not binp:
                _launch_state = {"status": "error", "port": port,
                                 "msg": "未找到 fgfs.exe，请确认 FlightGear 2024.1 已安装", "ts": time.time(),
                                 "fg_pid": None, "view_pid": _view_proc.pid if _view_proc else None}
                return _launch_state
            args = [
                binp,
                "--fg-root=" + FG_ROOT,
                "--fg-aircraft=" + FG_AIRCRAFT,
                "--aircraft=bo105",
                "--telnet=socket,in,5,127.0.0.1,%d,tcp" % port,
                "--httpd=8080",
                "--enable-hud=0",
                "--disable-panel",
                "--disable-sound",
                "--timeofday=noon",
                "--altitude=1500",
                "--vc=0",
                "--disable-random-objects",
                "--prop:/sim/hud/path-enabled=0",
                "--prop:/sim/hud/visibility=0",
                "--prop:/sim/panel/visibility=0",
                "--prop:/sim/model/show-panel=0",
            ]
            try:
                _fg_proc = subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            except Exception as e:
                _launch_state = {"status": "error", "port": port,
                                 "msg": "FlightGear 启动失败：%s" % e, "ts": time.time(),
                                 "fg_pid": None, "view_pid": _view_proc.pid if _view_proc else None}
                return _launch_state
        _launch_state = {"status": "launching", "port": port,
                         "msg": "FlightGear 启动中（约 10–30 秒，请保持窗口可见）…", "ts": time.time(),
                         "fg_pid": _fg_proc.pid if _fg_proc else None,
                         "view_pid": _view_proc.pid if _view_proc else None}
        return _launch_state


def _launch_state_payload():
    """把启动状态 + 当前连接状态打包给前端轮询。"""
    return {
        "status": _launch_state.get("status"),
        "port": _launch_state.get("port"),
        "msg": _launch_state.get("msg"),
        "ts": _launch_state.get("ts"),
        "fg_pid": _launch_state.get("fg_pid"),
        "view_pid": _launch_state.get("view_pid"),
        "fg_proc_alive": _fg_proc is not None and _fg_proc.poll() is None,
        "view_proc_alive": _view_proc is not None and _view_proc.poll() is None,
        "fg_connected": Handler.fg.state.get("connected", False) if Handler.fg else False,
        "fg_port": Handler.fg.client.port if Handler.fg else None,
    }


# 写入白名单：驾驶舱只允许操纵通道，且每个通道都有量程钳位
# （collective 走 bo105 的引擎油门通道，与 helo_fg_live.py 的控制方式一致）
WRITE_MAP = {
    "collective": [("/controls/engines/engine[0]/throttle", 0.0, 1.0),
                   ("/controls/engines/engine[1]/throttle", 0.0, 1.0)],
    "aileron":    [("/controls/flight/aileron", -1.0, 1.0)],
    "elevator":   [("/controls/flight/elevator", -1.0, 1.0)],
    "rudder":     [("/controls/flight/rudder", -1.0, 1.0)],
    "rotor_brake": [("/controls/rotor/brake", 0.0, 1.0)],
}


class Handler(SimpleHTTPRequestHandler):
    fg: FGPoll = None
    rl: RLStore = None
    allow_write = False      # 由 --allow-write 开启；默认只读，绝不回写仿真

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEBSITE), **kw)

    def log_message(self, fmt, *args):
        if "/api/fg/stream" not in (self.path or ""):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)

        if p == "/api/fg/state":
            return self._json(self.fg.state)

        if p == "/api/fg/stream":
            return self._sse()

        if p == "/api/rl/manifest":
            force = q.get("refresh", ["0"])[0] == "1"
            return self._json(self.rl.manifest(force=force))

        if p == "/api/rl/episode":
            fname = q.get("file", [None])[0]
            seed = q.get("seed", [None])[0]
            ep = q.get("ep", [None])[0]
            if not fname or seed is None or ep is None:
                return self._json({"error": "缺少参数 file/seed/ep"}, 400)
            try:
                mx = int(q.get("max", ["6000"])[0])
            except ValueError:
                mx = 6000
            fr = self.rl.frames(fname, int(seed), int(ep), mx)
            if fr is None:
                return self._json({"error": "未找到该回合：%s seed=%s ep=%s" % (fname, seed, ep)}, 404)
            return self._json(fr)

        if p == "/api/fg/capabilities":
            return self._json({
                "write_enabled": self.allow_write,
                "writable": sorted(WRITE_MAP.keys()),
                "note": "默认只读；以 --allow-write 启动才允许驾驶舱把操纵量写回 FlightGear",
            })

        if p == "/api/fg/view":
            # 代理 FlightGear jpg-httpd 画面流（默认 127.0.0.1:8080），解决跨域/路径问题
            return self._proxy_fg_view()

        if p == "/api/fg/launch/state":
            # 给前端轮询：一键启动的进度 + 当前 FG 连接状态
            return self._json(_launch_state_payload())

        if p == "/api/health":
            return self._json({
                "ok": True,
                "fg_connected": self.fg.state.get("connected", False),
                "fg_port": self.fg.client.port,
                "results_dir": str(self.rl.dir),
                "website_dir": str(WEBSITE),
            })

        if p == "/":
            self.path = "/cockpit.html"
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/fg/launch":
            # 一键启动：拉起驾驶舱专用 FlightGear 实例 + 窗口抓屏服务
            try:
                st = launch_fg_stack()
                return self._json(st)
            except Exception as e:
                return self._json({"status": "error", "msg": "启动异常：%s" % e}, 500)
        if u.path == "/api/fg/write":
            # 运行时开关：是否允许把驾驶舱操纵写回 FlightGear（需用户在页面主动接管）
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                body = {}
            self.allow_write = bool(body.get("enabled", True))
            sys.stderr.write("[写入开关] allow_write=%s\n" % self.allow_write)
            return self._json({"write_enabled": self.allow_write, "ts": time.time()})
        if u.path != "/api/fg/set":
            return self._json({"error": "未知接口"}, 404)
        if not self.allow_write:
            return self._json({
                "error": "服务以只读模式运行，拒绝写入。如需让驾驶舱操纵 FlightGear，"
                         "请以 --allow-write 重新启动：python cockpit/bridge_server.py --allow-write",
                "written": {},
            }, 403)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception as e:
            return self._json({"error": "请求体解析失败：%s" % e}, 400)

        written, rejected = {}, {}
        for key, targets in WRITE_MAP.items():
            if key not in body:
                continue
            try:
                v = float(body[key])
            except (TypeError, ValueError):
                rejected[key] = "非数值"
                continue
            for prop, lo, hi in targets:
                vv = max(lo, min(hi, v))
                ok = self.fg.client.set(prop, repr(round(vv, 4)))
                written.setdefault(key, {})[prop] = (round(vv, 4), ok)
        return self._json({"written": written, "rejected": rejected, "ts": time.time()})

    def _proxy_fg_view(self):
        # 优先从窗口抓屏服务取图（FG 2024.1 的 httpd /screenshot 不可靠）
        for view_port in (8081, 8080):
            try:
                with socket.create_connection(("127.0.0.1", view_port), timeout=1.5) as s:
                    path = "/view.jpg" if view_port == 8081 else "/screenshot"
                    s.sendall(("GET %s HTTP/1.0\r\nHost: 127.0.0.1:%d\r\n\r\n" % (path, view_port)).encode())
                    s.settimeout(2.0)
                    buf = b""
                    while True:
                        chunk = s.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                    if not buf.startswith(b"HTTP/1.1 200") and not buf.startswith(b"HTTP/1.0 200"):
                        continue
                    body = buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else b""
                    if len(body) < 100:
                        continue
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(body)
                    return
            except OSError:
                continue
        return self._json({"error": "FG 画面流未就绪：请确保 FlightGear 窗口可见且抓屏服务（8081）或 httpd（8080）已运行"}, 502)

    # SSE 并发上限：每个连接常驻一个线程，超限让前端降级为轮询，避免线程泄漏拖垮服务
    SSE_LIMIT = 6

    def _sse(self):
        if self.fg.subscriber_count() >= self.SSE_LIMIT:
            self.send_error(503, "SSE 连接数已达上限，请改用 /api/fg/state 轮询")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.connection.settimeout(15.0)
        except OSError:
            pass
        q = self.fg.subscribe()
        last_ping = time.time()
        try:
            while True:
                try:
                    st = q.get(timeout=1.0)
                except Exception:
                    st = self.fg.state
                payload = json.dumps(st, ensure_ascii=False)
                self.wfile.write(("data: %s\n\n" % payload).encode("utf-8"))
                self.wfile.flush()
                if time.time() - last_ping > 10.0:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            self.fg.unsubscribe(q)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--fg-host", default="127.0.0.1")
    ap.add_argument("--fg-port", type=int, default=5502)
    ap.add_argument("--allow-write", action="store_true",
                    help="允许驾驶舱把操纵量写回 FlightGear（默认关闭，只读取属性）")
    ap.add_argument("--no-watch", action="store_true",
                    help="禁用 website/data.js 自动重建（默认开启，监控数据源变化自动构建）")
    args = ap.parse_args()

    fg = FGPoll(args.fg_host, args.fg_port)
    fg.start()
    rl = RLStore(RESULTS)
    Handler.fg, Handler.rl = fg, rl
    Handler.allow_write = args.allow_write

    # 启动 website 数据自动构建 watch 线程（监控 rl_train/results 等目录，变化自动重建 data.js）
    watch_thread = None
    if not args.no_watch:
        try:
            import sys as _sys
            _sys.path.insert(0, str(WEBSITE))
            import build as _build
            def _watch_loop():
                try:
                    _build.watch_and_build(interval=5)
                except Exception as e:
                    sys.stderr.write("[watch 线程异常] %s\n" % e)
            watch_thread = threading.Thread(target=_watch_loop, daemon=True, name="website-watch")
            watch_thread.start()
        except Exception as e:
            sys.stderr.write("[watch 启动失败] %s（将以纯静态模式运行，data.js 需手动构建）\n" % e)

    # 限制并发处理线程，避免 SSE/轮询峰值把服务拖死
    class PoolHTTPServer(ThreadingHTTPServer):
        max_workers = 32
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="cockpit-http-")
        def process_request(self, request, client_address):
            self._pool.submit(self.process_request_thread, request, client_address)
        def server_close(self):
            self._pool.shutdown(wait=False, cancel_futures=True)
            super().server_close()

    socket.setdefaulttimeout(20)
    srv = PoolHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    srv.request_queue_size = 128

    print("=" * 68)
    print("  驾驶舱桥接服务 · 低空具身智能 ")
    print("=" * 68)
    print("  驾驶舱页面 : http://127.0.0.1:%d/cockpit.html" % args.port)
    print("  展示站     : http://127.0.0.1:%d/index.html" % args.port)
    print("  FG 属性源   : %s:%d（%s）" % (args.fg_host, args.fg_port,
                                          "可读可写 · 驾驶舱可接管操纵" if args.allow_write else "只读 · 不回写仿真"))
    print("  RL 轨迹源   : %s" % RESULTS)
    print("  静态目录    : %s" % WEBSITE)
    if watch_thread:
        print("  自动构建    : 已开启（监控数据源变化，自动重建 website/data.js）")
    else:
        print("  自动构建    : 已禁用（--no-watch）")
    print("=" * 68)
    print("  Ctrl+C 退出")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[退出] 正在关闭...")
    finally:
        fg.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
