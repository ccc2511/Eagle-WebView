@echo off
setlocal enabledelayedexpansion
title Eagle Proxy
cd /d "%~dp0"

where python > nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: python not found.
    pause
    exit /b 1
)

echo ================================
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    set _ip=%%a
    set _ip=!_ip: =!
    echo   http://!_ip!:8080/
    echo   http://!_ip!:8080/tag-normalizer
)
echo ================================
echo.

python eagle_proxy.py
pause
