@echo off
rem Starts Jarvis in the background. In admin mode (see admin-mode.ps1) it starts the "Jarvis"
rem scheduled task, which runs it with administrator rights without the Windows prompt.
rem Starting on purpose clears the "stopped on purpose" marker (stop-jarvis.bat, tray Quit).
del "%~dp0machine-state\watchdog\stopped-on-purpose" >nul 2>&1
schtasks /query /tn "Jarvis" >nul 2>&1
if %errorlevel%==0 (
  schtasks /run /tn "Jarvis" >nul
) else (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py" --background
)
