@echo off
rem ============================================================
rem B-Shell display-shell launcher (platform/fg_shell)
rem 2026-09-02 E终审：回放路径 (fdm=null + playback.py)
rem JSBSim 物理权威 (已完成) → playback.py 回放 → fdm=null FG 显示壳 → 录屏
rem 根除三症状：
rem   1. 黑夜 → --timeofday=noon
rem   2. 穿地 → playback 用真值坐标 + PHNL锚定 + 实测标高基准 22.9ft (2026-11 修)
rem   3. 副屏 → FG 窗口由用户主控；这里只确保默认 1280x800 居中
rem VISUAL STAND-IN: rendered model is bo105, physics is AH-1S(repaired)。
rem ============================================================
set PROTO=%~1
if "%PROTO%"=="" set PROTO=jsbsim_replay
"D:\Program Files\FlightGear 2024.1\bin\fgfs.exe" ^
  --launcher=0 ^
  --fdm=null ^
  --aircraft=bo105 ^
  --airport=PHNL ^
  --fg-root="D:\Program Files\FlightGear 2024.1\downloads\fgdata_2024_1" ^
  --fg-aircraft="D:\Program Files\FlightGear 2024.1\downloads\Aircraft" ^
  --generic=socket,in,100,127.0.0.1,5500,udp,%PROTO% ^
  --telnet=socket,in,5,127.0.0.1,5503,tcp ^
  --console=0 ^
  --prop:/environment/wind-speed-kt=0 ^
  --prop:/environment/turbulence/magnitude=0 ^
  --disable-real-weather-fetch ^
  --disable-terrasync ^
  --timeofday=noon ^
  --disable-ai-models ^
  --disable-ai-traffic ^
  --disable-random-objects ^
  --fullscreen=0 ^
  --geometry=1280x800 ^
  --prop:/sim/view/configured-width=1280 ^
  --prop:/sim/view/configured-height=800
