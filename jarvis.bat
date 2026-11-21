@echo off
rem Double-click to start Jarvis, or run "jarvis.bat --text" etc. from any folder.
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py %*
pause
