"""Basic abilities: time, system status, opening websites, timers."""
import os
import shutil
import threading
import time
import webbrowser
from datetime import datetime
from urllib.parse import urlparse

import psutil

from tools.registry import tool

# Set by main.py so timers can speak when they fire.
announce = print

@tool("How much Jarvis has cost so far today and this month (Claude API usage) against the monthly budget.")
def get_usage() -> str:
    from core import usage

    return usage.summary()


@tool("Get the current local date and time.")
def get_time() -> str:
    return datetime.now().strftime("%A %d %B %Y, %H:%M")


@tool("Measure this computer's health right now: CPU load, memory used, disk space, how long it has been on (uptime), "
      "the graphics card's temperature, load and free video memory (VRAM), and battery. Use for 'how hot is my PC/GPU', "
      "'how long has my PC been on', 'how much VRAM is free', 'health report on my computer'. CPU temperature and internet "
      "speed are NOT measured here.")
def get_system_status() -> dict:
    vm = psutil.virtual_memory()
    status = {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "memory_used_gb": round(vm.used / 2**30, 1),
        "memory_total_gb": round(vm.total / 2**30, 1),
        "memory_percent": vm.percent,
        "disk_free_gb": round(shutil.disk_usage(os.path.expanduser("~")).free / 2**30, 1),
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
        "cpu_temperature": "not readable on this PC",
    }
    gpu = _gpu_reading()
    if gpu:
        status.update(gpu)
    battery = psutil.sensors_battery()
    if battery:
        status["battery_percent"] = round(battery.percent)
        status["plugged_in"] = battery.power_plugged
    return status


def _gpu_reading() -> dict:
    """The NVIDIA card's own numbers (nvidia-smi), or {} when there is no NVIDIA card or driver. 2026-11-10: asked 'how hot
    is my CPU', the brain INVENTED 60 degrees for a GPU at 46 - there was nothing real to answer from. No console window:
    Jarvis runs under pythonw, where a console program gets a visible window in front (core/notice reads this every 10 min)."""
    import subprocess
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        name, temp, util, used, total = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"gpu": name, "gpu_temperature_c": int(float(temp)), "gpu_load_percent": int(float(util)),
                "vram_free_gb": round((float(total) - float(used)) / 1024, 1), "vram_total_gb": round(float(total) / 1024, 1)}
    except Exception:  # noqa: BLE001 - no NVIDIA card or driver: simply not reported
        return {}


@tool(
    "Open a website in the default browser.",
    {"url": {"type": "string", "description": "Full http(s) URL"}},
)
def open_website(url: str) -> str:
    if urlparse(url).scheme not in ("http", "https"):
        return "Only http and https links can be opened."
    from jarvis_pc.guardrails.stepfence import require_foreground
    require_foreground("open a website in the browser")   # a browser window comes to the front: never from a mission
    from tools.software import is_admin, launch_as_user

    if is_admin():
        launch_as_user(url)  # admin mode: the browser must not inherit admin rights
    else:
        webbrowser.open(url)
    return f"Opened {url}."


def timer_done(name: str, uid: str | None = None) -> None:
    """A timer finished: say it out loud, and ping the phone too (you may have walked away from the PC)."""
    from core import notify
    announce(f"Your {_timer_words(name)} is done.")
    notify.notify("Timer done", f"Your {_timer_words(name)} is done.", uid or "owner", "reminders", urgent=True)


@tool(
    "Set a countdown timer that announces itself when done.",
    {
        "seconds": {"type": "integer", "description": "Duration in seconds"},
        "label": {"type": "string", "description": "What the timer is for, e.g. 'pasta'"},
    },
    direct=True,
)
def set_timer(seconds: int, label: str) -> str:
    if not 1 <= seconds <= 86400:
        return "Timers must be between one second and one day."
    from core import timers, users   # kept in one place so the PC companion and dashboard can show and cancel it
    from tools.life import _say_dur
    uid = users.current()
    t = timers.add(seconds, label, lambda name: timer_done(name, uid))
    return f"{_say_dur(seconds).capitalize()} on the {_timer_words(t['label'])}. I'll tell you when it's done."


def _timer_words(label: str) -> str:
    """'pasta' -> 'pasta timer'; a label that already says timer ('timer', 'oven timer') is not doubled ("the timer timer",
    qwen3.5 2026-11-09)."""
    label = (label or "").strip()
    return label if label.lower().endswith("timer") else f"{label} timer".strip()


# -- power: shut down / restart / sleep / lock / sign out ----------------------------------------
import subprocess  # noqa: E402

_power = {"pending": None}  # "shutdown" / "restart" while a countdown is running
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
POWER_WORDS = ("shut down", "shutdown", "turn off", "power off", "restart", "reboot", "sleep", "lock",
               "sign out", "log off", "log out", "etenn", "fèmen", "redemare", "éteins", "redémarre", "verrouille")


class PowerCommandError(RuntimeError):
    def __init__(self,code:int):
        self.returncode=code
        super().__init__(f"Windows power command failed (exit {code}).")


def _cmd(*args: str) -> None:
    result=subprocess.run(list(args), capture_output=True, timeout=30,
                          creationflags=_NO_WINDOW)
    if result.returncode!=0:
        # Without this check Jarvis says "shutting down" even when Windows
        # actually refused the command, e.g. due to privileges/policy.
        raise PowerCommandError(result.returncode)


def power_pending() -> str | None:
    return _power["pending"]


def cancel_power() -> str:
    """Abort a shutdown/restart countdown (also called when the user says "stop"/"cancel")."""
    was = _power["pending"]
    # Windows owns the countdown, which can outlive this Jarvis process.
    # Always ask Windows to abort, even after a Jarvis restart lost _pending.
    try:
        _cmd("shutdown", "/a")
    except PowerCommandError as error:
        if error.returncode!=1116:raise  # ERROR_NO_SHUTDOWN_IN_PROGRESS
        _power["pending"] = None
        return "Nothing was scheduled."
    _power["pending"] = None
    return f"Cancelled the {was}." if was else "Cancelled the pending Windows shutdown or restart."


@tool(
    "Shut down, restart, sleep, lock or sign out of this PC, or cancel a pending shutdown/restart. If the user "
    "told you to, just do it. Shutdown and restart wait 30 seconds first (tell them they can say 'cancel'); "
    "pass delay_seconds 0 only if they say 'now' or 'immediately'.",
    {"action": {"type": "string", "enum": ["shutdown", "restart", "sleep", "lock", "sign_out", "cancel"]},
     "delay_seconds": {"type": "integer", "description": "For shutdown/restart: seconds to wait (default 30, max 3600)"}},
    required=["action"],
)
def power(action: str, delay_seconds: int = 30) -> str:
    from core import approvals, users

    if action == "cancel":
        return cancel_power()
    uid = users.current()
    # A direct request must match the EXACT requested PC operation. This
    # fixes "turn off my PC" asking again and blocks "turn off the lights".
    said_so = approvals.power_direct(uid, action)
    key = f"power:{action}"
    if not (said_so or approvals.consume(uid, key)):
        return approvals.request(uid, key, f"{action.replace('_', ' ')} the PC")
    delay = max(0, min(int(delay_seconds if delay_seconds is not None else 30), 3600))
    if action in ("shutdown", "restart"):
        flag = "/s" if action == "shutdown" else "/r"
        _cmd("shutdown", flag, "/t", str(delay), "/c", f"Jarvis: {action} in {delay} seconds. Say 'cancel' to stop it.")
        _power["pending"] = action if delay else None
        verb = "Shutting down" if action == "shutdown" else "Restarting"
        return f"{verb} the PC in {delay} seconds (Windows accepted the command); say 'cancel shutdown my PC' to stop it." if delay else f"{verb} now (Windows accepted the command)."
    if action == "lock":
        _cmd("rundll32.exe", "user32.dll,LockWorkStation")
        return "Locked the PC."
    if action == "sign_out":
        _cmd("shutdown", "/l")
        return "Signing out."
    # sleep: let the reply be spoken first, then suspend
    threading.Timer(4.0, lambda: _cmd("powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)")).start()
    return "Putting the PC to sleep in a few seconds."
