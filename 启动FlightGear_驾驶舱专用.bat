@echo off
chcp 65001 >nul
setlocal

REM 驾驶舱专用 FlightGear 实例：独立端口 5502 + 窗口抓屏服务 8081，不占用 5501 B 线主实验道
REM 说明：驾驶舱网页顶栏「FG 未连接」现已支持一键拉起本实例（由桥接服务调用），
REM       通常无需手动运行此 .bat；保留作手动兜底与调试用。
set FG="D:\Program Files\FlightGear 2024.1\bin\fgfs.exe"
set FG_ROOT="D:\Program Files\FlightGear 2024.1\downloads\fgdata_2024_1"
set FG_AIRCRAFT="D:\Program Files\FlightGear 2024.1\downloads\Aircraft"
set PY="C:\Users\Administrator\.workbuddy\binaries\python\envs\cockpit\Scripts\python.exe"

REM 启动窗口抓屏服务（先于 FG，持续抓取可见窗口）
start "FG View Server" %PY% "%~dp0fg_view_server.py" --port 8081 --fps 5

:loop
start "FlightGear Cockpit" /MIN %FG% ^
  --fg-root=%FG_ROOT% ^
  --fg-aircraft=%FG_AIRCRAFT% ^
  --aircraft=bo105 ^
  --telnet=socket,in,5,127.0.0.1,5502,tcp ^
  --httpd=8080 ^
  --enable-hud=0 ^
  --disable-panel ^
  --disable-sound ^
  --timeofday=noon ^
  --altitude=1500 ^
  --vc=0 ^
  --disable-random-objects ^
  --prop:/sim/hud/path-enabled=0 ^
  --prop:/sim/hud/visibility=0 ^
  --prop:/sim/panel/visibility=0 ^
  --prop:/sim/model/show-panel=0

REM 崩溃/退出后自动重启，避免画面突然消失
echo [FG 已退出，3 秒后重启] %date% %time%
timeout /t 3 /nobreak >nul
goto loop
