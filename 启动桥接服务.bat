@echo off
chcp 65001 >nul
title CockpitBridge · 驾驶舱桥接服务
cd /d "%~dp0"

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

set "LOG=%~dp0cockpit\bridge.log"
set "ARGS=%*"
if "%ARGS%"=="" set "ARGS=--port 8777 --fg-port 5502 --allow-write"

echo ============================================================ >> "%LOG%"
echo [%date% %time%] 桥接服务启动，参数：%ARGS% >> "%LOG%"
echo ============================================================ >> "%LOG%"

REM 若 8777 已被占用（例如上一次未关闭或后台残留），先释放，避免绑定失败死循环
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /r ":8777.*LISTENING"') do taskkill /PID %%a /F >nul 2>&1

:loop
"%PY%" -u cockpit\bridge_server.py %ARGS% >> "%LOG%" 2>&1
echo [%date% %time%] 桥接服务已退出（代码 %errorlevel%），3 秒后自动重启 >> "%LOG%"
timeout /t 3 /nobreak >nul
goto loop
