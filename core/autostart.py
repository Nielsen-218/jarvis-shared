"""Start Jarvis when you sign in to Windows: a shortcut in the Startup folder
that runs it in the background (no console window; output goes to data/jarvis.log)."""
import os
import subprocess
import sys
from pathlib import Path

from core.config import ROOT

SHORTCUT = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Jarvis.lnk"


def _pythonw() -> Path:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def is_enabled() -> bool:
    return SHORTCUT.exists() or admin_mode_on()  # admin mode starts it at sign-in too


def enable() -> str:
    SHORTCUT.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:JARVIS_LNK); "
        "$s.TargetPath = $env:JARVIS_EXE; $s.Arguments = $env:JARVIS_ARGS; "
        "$s.WorkingDirectory = $env:JARVIS_DIR; $s.Description = 'J.A.R.V.I.S. assistant'; $s.Save()"
    )
    env = dict(os.environ, JARVIS_LNK=str(SHORTCUT), JARVIS_EXE=str(_pythonw()),
               JARVIS_ARGS=f'"{ROOT / "main.py"}" --background', JARVIS_DIR=str(ROOT))
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True,
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "Jarvis will now start when you sign in to Windows."


def disable() -> str:
    SHORTCUT.unlink(missing_ok=True)
    return "Jarvis won't start with Windows anymore."


# -- admin mode (see admin-mode.ps1): a "Jarvis" scheduled task that starts it as administrator ----------
_admin_cache = {"t": 0.0, "on": False}


def admin_mode_on() -> bool:
    import time

    if time.monotonic() - _admin_cache["t"] > 5:  # the tray asks every time the menu draws
        r = subprocess.run(["schtasks", "/query", "/tn", "Jarvis"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _admin_cache.update(t=time.monotonic(), on=r.returncode == 0)
    return _admin_cache["on"]


def toggle_admin_mode() -> None:
    """Runs admin-mode.ps1 (on, or -Off). Windows asks the person to approve it once; the script
    then restarts Jarvis with the new rights."""
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "admin-mode.ps1")]
    if admin_mode_on():
        args.append("-Off")
    _admin_cache["t"] = 0.0
    subprocess.Popen(args, cwd=str(ROOT))
