@echo off
title FlightGear B1 Autonomous Demo (bo105 @ PHNL)
echo ============================================================
echo   FlightGear Bo105 自动起落闭环演示 (B1 验证版)
echo   1) 此脚本只在 FlightGear 已经启动时才会自动连接
echo   2) 如未启动, 请先双击 platform\fg_demo\start_fg_demo.bat
echo   3) 也可让本脚本自动启动 (见下方 prompt)
echo ============================================================
echo.

set "FG_BIN=D:\Program Files\FlightGear 2024.1\bin\fgfs.exe"

if not exist "%FG_BIN%" (
    echo [错误] 未发现 FlightGear 可执行程序: %FG_BIN%
    echo        请确认 FlightGear 2024.1 已安装到此路径, 或修改本脚本.
    echo.
    echo [替代方案] 手工两步启动:
    echo   1) 双击 platform\fg_demo\start_fg_demo.bat  (启动 FG + 加载 demo_stabilizer.xml)
    echo   2) 在本项目根目录重新运行本脚本 (自动连接并执行 B1 演示)
    pause
    exit /b 1
)

echo [选择]
echo   1 = 自动启动 FlightGear 并运行 B1 闭环演示
echo   2 = 我已手动启动 FG, 请直接连接运行演示
echo.
set /p CH=请选择 (1 或 2, 默认 1):
if "%CH%"=="" set "CH=1"

if "%CH%"=="2" goto run_demo

echo.
echo [1/2] 启动 FlightGear 2024.1 (bo105 @ PHNL, 内置 Nasal 增稳) ...
echo        调用: platform\fg_demo\start_fg_demo.bat
echo.
call "platform\fg_demo\start_fg_demo.bat"
if errorlevel 1 (
    echo [失败] FlightGear 启动失败
    pause
    exit /b 1
)

:run_demo
echo.
echo [2/2] 运行 B1 自动飞行演示 (含校准 + 爬升 + 悬停 + 降落) ...
echo.
cd /d "%~dp0"
python platform\fg_demo\fg_demo.py
set EXITCODE=%errorlevel%
echo.
echo ============================================================
if "%EXITCODE%"=="0" (
    echo   [结论] B1 全闭环演示成功: 起飞 + 悬停 + 软着陆
) else if "%EXITCODE%"=="2" (
    echo   [结论] 本次飞行中途中止或坠毁, 请查看 logs\flight_*.json
) else (
    echo   [结论] 启动失败或连接异常, 退出码=%EXITCODE%
)
echo   详细日志: platform\fg_demo\logs\flight_*.json
echo ============================================================
pause
exit /b %EXITCODE%