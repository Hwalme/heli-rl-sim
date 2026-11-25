@echo off
chcp 65001 >nul
title 驾驶舱 Cockpit · 低空具身智能 
cd /d "%~dp0"

echo ============================================================
echo   全数字仿真驾驶舱 · 启动中
echo   数据源 1：FlightGear 实时（telnet 127.0.0.1:5502，驾驶舱专用实例）
echo   数据源 2：RL 轨迹回放（rl_train\results，离线可用）
echo ============================================================
echo.

echo [1/2] 启动桥接服务（端口 8777，崩溃自动重启）
start "CockpitBridge" /min "%~dp0启动桥接服务.bat"

echo [2/2] 等待服务就绪...
timeout /t 3 /nobreak >nul

start "" http://127.0.0.1:8777/cockpit.html

echo.
echo   驾驶舱地址：http://127.0.0.1:8777/cockpit.html
echo     ⚠ 必须用它自己的 8777 端口打开；IDE 内置的静态预览面板
echo       只加载页面文件、没有后端，必然显示「未连接」。
echo.
echo   · FG 实时（驾驶舱专用实例，telnet 127.0.0.1:5502 + httpd 8080）：
echo     顶栏显示「FG 未连接」时，直接【点击它】即可由桥接服务一键拉起
echo     FlightGear 与窗口抓屏服务，无需手动开 .bat。
echo     手动启动参数：--telnet=socket,in,5,127.0.0.1,5502,tcp
echo   · RL 回放：无需 FlightGear，直接在页面切到「RL 回放」页签选择轨迹。
echo.
echo   · 操纵台（摇杆/总距/脚蹬/按钮/旋钮）：默认「跟随仿真数据」，
echo     点顶栏「接管操纵」切到本地操纵，操作将写回 FlightGear（桥接已 --allow-write）。
echo.
echo   · 回归自检：python cockpit\test_controls.py
echo   · 桥接日志：cockpit\bridge.log
echo.
echo   停止服务：任务管理器结束 CockpitBridge（cmd.exe / python.exe）
echo ============================================================
pause
