@echo off
rem Double-click me to install Jarvis (first time) or repair/update it (any time after).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
echo.
pause
