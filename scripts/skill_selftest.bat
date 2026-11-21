@echo off
rem Run the skill-sandbox selftest. Right-click this file and choose "Run as administrator" to test the ELEVATED case JARVIS runs in.
rem Uses only %TEMP% (lab + skills folder): it never creates anything under data\.
cd /d "%~dp0\.."
set JARVIS_SKILLS_LAB=%TEMP%\jarvis-skill-selftest-lab
set JARVIS_SKILLS_DIR=%TEMP%\jarvis-skill-selftest-skills
.venv\Scripts\python.exe manage.py skill selftest > "%TEMP%\jarvis-skill-selftest.txt" 2>&1
type "%TEMP%\jarvis-skill-selftest.txt"
echo.
echo Result also saved to %TEMP%\jarvis-skill-selftest.txt
pause
