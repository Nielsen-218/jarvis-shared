@echo off
rem Closes Jarvis completely. Asks it to quit (works even in admin mode), waits until it has
rem finished saving (data\jarvis.running disappears), then makes sure nothing is left.
rem Mark it as stopped on purpose first, so the crash watchdog (scripts/watchdog.py) does not bring him back.
if not exist "%~dp0machine-state\watchdog" mkdir "%~dp0machine-state\watchdog"
echo stopped> "%~dp0machine-state\watchdog\stopped-on-purpose"
echo.> "%~dp0data\quit.flag"
powershell -NoProfile -Command "$m = Join-Path '%~dp0' 'data\jarvis.running'; for ($i=0; $i -lt 25; $i++) { if (-not (Test-Path $m)) { break }; Start-Sleep 1 }; Remove-Item $m -ErrorAction SilentlyContinue; Start-Sleep 1; Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*main.py*--background*' -or $_.CommandLine -like '*jarvis*main.py*') } | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }"
