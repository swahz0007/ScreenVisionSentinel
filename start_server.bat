@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ScreenVision Sentinel OCR API Server
echo 正在启动 OCR 后台服务，请稍候...
echo.
set "SVS_ROOT=%~dp0"
if "%SVS_ROOT:~-1%"=="\" set "SVS_ROOT=%SVS_ROOT:~0,-1%"
cd /d "%SVS_ROOT%"
set "PYTHONPATH=%SVS_ROOT%\src;%PYTHONPATH%"
if not exist "%SVS_ROOT%\.venv\Scripts\python.exe" (
    echo [ERROR] 找不到 Python：%SVS_ROOT%\.venv\Scripts\python.exe
    echo 请确认当前目录是开发环境项目根目录。
    pause
    exit /b 1
)
if "%SVS_STARTUP_CHECK%"=="1" (
    echo startup_check_ok
    exit /b 0
)
"%SVS_ROOT%\.venv\Scripts\python.exe" -m screenvision_sentinel.server
pause
