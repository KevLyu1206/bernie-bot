@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 贝尔娜提塔 QQ bot ===
python bot.py
echo.
echo 进程已结束（正常退出 / 报错都停在这里）
pause
