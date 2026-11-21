@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 贝尔娜提塔 · 一键启动

echo ============================================
echo   贝尔娜提塔 bot  启动中
echo ============================================
echo.

if not exist config.json (
  echo [!] 没有 config.json，正在从模板复制...
  copy /y config.example.json config.json >nul
  echo [!] 请先编辑 config.json，填 llm.api_key 和 master_ids 里的你的QQ号
  echo.
  notepad config.json
)

echo [1/3] 检查 QQ 协议端配置...
python setupll.py
echo.

echo [2/3] 启动 bot.py（这个窗口要一直开着）
echo      看到「监听 ws://0.0.0.0:8080」后再开 LuckyLillia
echo.
start "贝尔娜提塔 bot" cmd /k python bot.py

timeout /t 3 >nul

echo [3/3] 启动 LuckyLillia（弹出窗口里扫码登录小号）
echo.
if exist "LuckyLillia-Desktop-win-x64\LuckyLillia.exe" (
  start "" "LuckyLillia-Desktop-win-x64\LuckyLillia.exe"
) else (
  echo [!] 没找到 LuckyLillia-Desktop-win-x64\LuckyLillia.exe
)

echo.
echo 都起来了。剩下的：
echo   - LuckyLillia 里用 QQ 小号扫码登录
echo   - bot 窗口出现「QQ 端已连接」就是成功
echo   - WebUI: http://127.0.0.1:3080
echo.
pause
