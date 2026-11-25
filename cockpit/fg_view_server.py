# -*- coding: utf-8 -*-
r"""
FlightGear 窗口抓屏服务

FlightGear 2024.1 的 --httpd 不再提供可靠的 /screenshot JPEG，
因此用 ctypes 找到 FG 窗口，mss 抓取客户区，通过 HTTP 提供 JPEG。
驾驶舱 img 经桥接 /api/fg/view 访问，避免跨域与路径问题。

启动：python cockpit/fg_view_server.py [--port 8081] [--fps 5]
"""
from __future__ import annotations

import argparse
import io
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Windows-only window capture via ctypes + Pillow
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

try:
    from PIL import Image, ImageGrab
except ImportError as e:
    sys.stderr.write("缺少依赖：%s\n请运行 pip install Pillow\n" % e)
    raise


def get_fg_pids():
    """通过进程快照找到所有 fgfs.exe 的 PID。"""
    pids = []
    TH32CS_SNAPPROCESS = 0x00000002
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.CHAR * 260),
        ]

    hSnap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if hSnap == -1:
        return pids
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    if kernel32.Process32First(hSnap, ctypes.byref(pe)):
        while True:
            # 直接用 bytes 比较，避免编码问题
            name = bytes(pe.szExeFile)
            idx = name.find(b"\x00")
            if idx != -1:
                name = name[:idx]
            if name.lower() == b"fgfs.exe":
                pids.append(pe.th32ProcessID)
            if not kernel32.Process32Next(hSnap, ctypes.byref(pe)):
                break
    kernel32.CloseHandle(hSnap)
    return pids


def find_fg_window():
    """查找属于 fgfs.exe 进程、客户区足够大的可见渲染窗口。"""
    fg_pids = set(get_fg_pids())
    if not fg_pids:
        return None
    found = []

    def enum(hwnd, extra):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in fg_pids:
            return True
        r = window_rect(hwnd)
        if not r or r[2] < 200 or r[3] < 200:
            return True
        found.append((hwnd, r[2] * r[3], r))
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(EnumWindowsProc(enum), 0)
    if not found:
        return None
    # 选客户区最大的，通常是 3D 渲染窗口
    found.sort(key=lambda x: x[1], reverse=True)
    return found[0][0]


def window_rect(hwnd):
    """返回窗口客户区在屏幕上的 (left, top, width, height)。"""
    rect = wintypes.RECT()
    if user32.GetClientRect(hwnd, ctypes.byref(rect)):
        # 映射到屏幕坐标
        pt = wintypes.POINT(rect.left, rect.top)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top
    return None


class Capture:
    def __init__(self, fps: float = 5.0):
        self.fps = max(1.0, min(30.0, fps))
        self._lock = threading.Lock()
        self._frame = None
        self._last = 0.0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            t0 = time.time()
            frame = self._grab()
            if frame:
                with self._lock:
                    self._frame = frame
                    self._last = time.time()
            sleep = max(0.0, 1.0 / self.fps - (time.time() - t0))
            time.sleep(sleep)

    def _grab(self):
        hwnd = find_fg_window()
        if not hwnd:
            return None
        # 把 FG 窗口提到最前，避免被其他窗口遮挡导致抓屏抓到别的内容
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        r = window_rect(hwnd)
        if not r or r[2] < 8 or r[3] < 8:
            return None
        left, top, w, h = r
        try:
            pil = ImageGrab.grab(bbox=(left, top, left + w, top + h))
            if not pil or pil.size[0] < 8 or pil.size[1] < 8:
                return None
            bio = io.BytesIO()
            pil.convert("RGB").save(bio, format="JPEG", quality=80)
            data = bio.getvalue()
            if not data:
                return None
            return data
        except Exception:
            return None

    def get(self):
        with self._lock:
            return self._frame, self._last

    def stop(self):
        self._running = False
        self._thread.join(timeout=1.0)


class Handler(BaseHTTPRequestHandler):
    cap: Capture = None

    def log_message(self, fmt, *args):
        pass

    def _jpeg(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/health"):
            frame, ts = self.cap.get()
            ok = frame is not None and time.time() - ts < 3.0
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(('{"ok": %s, "ts": %.3f}' % ("true" if ok else "false", ts)).encode())
            return
        if self.path in ("/view.jpg", "/screenshot"):
            frame, ts = self.cap.get()
            if not frame:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                msg = "FG window not found / not rendered".encode("utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            self._jpeg(frame)
            return
        self.send_error(404, "Not Found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--fps", type=float, default=5.0)
    args = ap.parse_args()

    cap = Capture(fps=args.fps)
    Handler.cap = cap

    socket.setdefaulttimeout(10)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print("FG 窗口抓屏服务 : http://127.0.0.1:%d/view.jpg" % args.port)
    print("按 Ctrl+C 退出")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cap.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
