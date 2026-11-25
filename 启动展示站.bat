@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   低空具身智能 · 展示站自动更新服务
echo   启动后将自动打开浏览器 http://127.0.0.1:8080/
echo   训练/FG结果写入后网页自动刷新，无需手动操作
echo   按 Ctrl+C 停止服务
echo ============================================================
echo.
python website\serve.py --port 8080 --interval 5
pause
