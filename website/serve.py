#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""website/serve.py —— 一键启动：数据 watch 自动构建 + HTTP 静态服务

用法:
    python website/serve.py [--port 8080] [--interval 5]

功能:
    1. 后台线程运行 build.py --watch，监控数据目录变化自动重建 data.js
    2. 主线程运行 http.server，服务 website/ 目录
    3. 浏览器打开 http://127.0.0.1:8080/ 即可看到自动更新的最新数据
"""
import argparse
import os
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "website")


def run_watch(interval):
    """在后台线程中运行 watch 构建。"""
    # 延迟导入，确保 build.py 的 print 输出到当前 stdout
    sys.path.insert(0, WEB)
    import build
    try:
        build.watch_and_build(interval=interval)
    except Exception as e:
        print(f"[watch 线程异常] {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="低空具身智能展示站 —— 自动更新服务")
    parser.add_argument("--port", type=int, default=8080, help="HTTP 服务端口（默认 8080）")
    parser.add_argument("--interval", type=int, default=5, help="文件监控间隔秒数（默认 5）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    os.chdir(WEB)

    print("=" * 60)
    print("  低空具身智能 · 展示站自动更新服务")
    print(f"  项目根目录: {ROOT}")
    print(f"  网站目录:   {WEB}")
    print(f"  服务地址:   http://127.0.0.1:{args.port}/")
    print(f"  监控间隔:   每 {args.interval} 秒检测数据文件变化")
    print("=" * 60)
    print()

    # 启动 watch 后台线程
    watch_thread = threading.Thread(target=run_watch, args=(args.interval,), daemon=True)
    watch_thread.start()
    print(f"[启动] 数据 watch 线程已启动（后台自动构建 data.js）")

    # 等待首次构建完成
    time.sleep(3)

    # 启动 HTTP 服务
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            # 精简日志，只打印非 200 的请求
            if args[1] not in ("200", "304"):
                sys.stderr.write("[http] %s - %s\n" % (self.address_string(), format % args))

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[启动] HTTP 服务已启动: http://127.0.0.1:{args.port}/")
    print("[提示] 训练结果写入 rl_train/results/ 或 FG 日志写入 platform/fg_demo/logs/ 后，")
    print("       网页将在 5-15 秒内自动刷新显示最新数据，无需手动操作。")
    print("       按 Ctrl+C 停止服务。")
    print()

    if not args.no_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[停止] 服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
