@echo off
echo ========================================
echo   FlightGear 启动脚本 (直升机模式)
echo ========================================
echo.

REM 检查FlightGear是否安装
where fgfs >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未找到FlightGear!
    echo 请先安装FlightGear: https://www.flightgear.org/download/
    echo 安装时记得勾选 Helicopters -> Bo-105
    pause
    exit /b 1
)

echo [1/2] 启动FlightGear (Bo-105直升机)...
echo.
echo 参数说明:
echo   --fdm=external    使用外部飞行动力学
echo   --generic=...    UDP通信端口配置
echo.

REM 启动FlightGear
fgfs --aircraft=bo105 ^
     --fdm=external ^
     --generic=socket,in,10,127.0.0.1,5500,udp ^
     --generic=socket,out,10,127.0.0.1,5501,udp ^
     --enable-full-screen=no ^
     --prop:/sim/view/configured-width=1024 ^
     --prop:/sim/view/configured-height=768

echo.
echo FlightGear 已退出
pause
