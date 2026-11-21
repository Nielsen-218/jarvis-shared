@echo off
rem Stops Jarvis and starts it again in the background (tray icon + HUD).
call "%~dp0stop-jarvis.bat"
rem Wait until the old instance has really ended: "schtasks /run" is ignored while the task still runs, and the elevated
rem instance can't be killed from here (2026-11-08: with practice loading the PC it took longer than 2 s and never restarted).
powershell -NoProfile -Command "for ($i=0; $i -lt 90; $i++) { $s = (schtasks /query /tn 'Jarvis' /fo LIST 2>$null | Select-String 'Status:'); $port = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if (-not ($s -match 'Running') -and -not $port) { break }; Start-Sleep 1 }; Start-Sleep 1"
call "%~dp0start-jarvis.bat"
