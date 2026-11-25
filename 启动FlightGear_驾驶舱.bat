@echo off
chcp 65001 >nul
title FlightGear · 驾驶舱数据源 (bo105 @ PHNL)
cd /d "%~dp0"

set "FG_BIN=D:\Program Files\FlightGear 2024.1\bin\fgfs.exe"
set "FG_ROOT=D:\Program Files\FlightGear 2024.1\downloads\fgdata_2024_1"
set "FG_AIRCRAFT=D:\Program Files\FlightGear 2024.1\downloads\Aircraft"

if not exist "%FG_BIN%" (
    echo [错误] 未找到 FlightGear 可执行程序:
    echo        %FG_BIN%
    pause
    exit /b 1
)
if not exist "%FG_ROOT%" (
    echo [错误] 未找到 FG_ROOT 数据目录:
    echo        %FG_ROOT%
    pause
    exit /b 1
)

echo ============================================================
echo   启动 FlightGear（驾驶舱实时数据源）
echo   机型  : bo105
echo   机场  : PHNL
echo   数据源: --telnet=socket,in,5,127.0.0.1,5501,tcp
echo                       ^^^^ 驾驶舱桥接服务通过它读取属性
echo ============================================================
echo.
echo   起飞后请在另一个终端确认桥接已连上：
echo     curl http://127.0.0.1:8777/api/health
echo     期望看到 "fg_connected": true
echo.
echo   FlightGear 正在启动（首次加载较慢，请等待窗口出现）...
echo ============================================================

start "" "%FG_BIN%" ^
  --fg-root="%FG_ROOT%" ^
  --fg-aircraft="%FG_AIRCRAFT%" ^
  --aircraft=bo105 ^
  --airport=PHNL ^
  --telnet=socket,in,5,127.0.0.1,5501,tcp ^
  --disable-terrasync ^
  --disable-splash-screen ^
  --timeofday=noon ^
  --geometry=1280x720 ^
  --prop:/sim/rendering/multi-sample-buffers=1 ^
  --prop:/sim/rendering/multi-samples=4

echo.
echo   FlightGear 已在后台启动。
echo   若窗口迟迟不出现，查看 fgfs 进程是否已在任务管理器中运行。
timeout /t 5 /nobreak >nul
