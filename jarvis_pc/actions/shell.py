"""Gated shell runner.

run() asks the Guardrails gate first, then executes with a timeout, polls the
kill switch while the command runs, caps output, and classifies errors so the
caller can decide what to do next. Everything is audit-logged.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..guardrails.gate import Decision, Guardrails

IS_WINDOWS = os.name == "nt"

_ERROR_RULES = [
    ("permissions", r"permission denied|access (is )?denied|requires elevation|operation not permitted|run as administrator"),
    ("not_found", r"not found|not recognized|no such file|cannot find|does not exist|unable to locate"),
    ("network", r"could not resolve|name or service not known|network is unreachable|connection (refused|reset|timed out)|no internet|unable to connect|certificate"),
    ("syntax", r"syntax error|unexpected token|parsererror|unrecognized (option|argument)|invalid (option|argument)|usage:"),
    ("disk_full", r"no space left|disk full|not enough space"),
]


@dataclass
class ShellResult:
    ran: bool
    decision: Decision
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    killed: bool = False
    truncated: bool = False
    duration: float = 0.0
    error_kind: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.ran and self.exit_code == 0 and not self.timed_out and not self.killed


def classify_error(exit_code: Optional[int], stderr: str, timed_out: bool = False, killed: bool = False) -> Optional[str]:
    if killed:
        return "killed"
    if timed_out:
        return "timeout"
    if exit_code in (0, None):
        return None
    for kind, pat in _ERROR_RULES:
        if re.search(pat, stderr, re.I):
            return kind
    return "unknown"


def shell_argv(command: str) -> List[str]:
    if IS_WINDOWS:
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["bash", "-c", command]


def windows_powershell_module_path(path: Optional[str]) -> Optional[str]:
    """PSModulePath fit for Windows PowerShell 5.1. A parent that is PowerShell 7 (pwsh) hands down ITS module folders first, and
    5.1 then autoloads pwsh's Microsoft.PowerShell.Utility/Security manifests, which it cannot run: Get-FileHash and
    Get-AuthenticodeSignature stop existing ('not recognized', 'module could not be loaded'). Drop the pwsh folders, keep the rest."""
    if not path:
        return path
    keep = [p for p in path.split(os.pathsep)
            if p and not re.search(r"[\\/]PowerShell[\\/]Modules|microsoft\.powershell_\d", p, re.I)]
    return os.pathsep.join(keep)


def child_env(env: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """The environment a shell child gets: the caller's, or ours, with PSModulePath made safe for Windows PowerShell."""
    if not IS_WINDOWS:
        return env
    base = dict(os.environ if env is None else env)
    for key in [k for k in base if k.upper() == "PSMODULEPATH"]:      # os.environ copies carry the key UPPER-CASED on Windows
        base[key] = windows_powershell_module_path(base[key])
    return base


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, check=False)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def _tail(text: str, limit: int) -> "tuple[str, bool]":
    if len(text) <= limit:
        return text, False
    return "[...truncated...]\n" + text[-limit:], True


def make_runner(guard: Guardrails, cwd: Optional[str] = None, timeout: float = 60.0, intent: str = ""):
    """Bind guard/cwd/timeout so other modules can take a simple runner(command) callable."""
    def runner(command: str, timeout_override: Optional[float] = None) -> "ShellResult":
        return run(guard, command, cwd=cwd, timeout=timeout_override or timeout, intent=intent)
    return runner


def run(
    guard: Guardrails,
    command: str,
    *,
    cwd: Optional[str] = None,
    timeout: float = 60.0,
    intent: str = "",
    max_output: int = 200_000,
    env: Optional[Dict[str, str]] = None,
) -> ShellResult:
    decision = guard.authorize(command, cwd=cwd, intent=intent)
    if not decision.allowed:
        return ShellResult(ran=False, decision=decision)

    start = time.monotonic()
    popen_kwargs = dict(
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace", cwd=cwd, env=child_env(env),
    )
    if IS_WINDOWS:
        # CREATE_NO_WINDOW (2026-11-10 review): Jarvis runs under pythonw (no console), so powershell would get a NEW visible
        # console window that comes to the front - over a game, or over the owner's full-screen video during a mission.
        # The output is piped, so nothing needs the console; taskkill /T still ends the group.
        popen_kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                                         | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(shell_argv(command), **popen_kwargs)  # type: ignore[arg-type]
    except OSError as e:
        res = ShellResult(ran=False, decision=decision, stderr=str(e), error_kind="not_found")
        guard.record_result(decision, ran=False, error=str(e))
        return res

    timed_out = killed = False
    deadline = start + timeout
    while True:
        try:
            out, err = proc.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if guard.killswitch.is_set():
                killed = True
            elif time.monotonic() >= deadline:
                timed_out = True
            if killed or timed_out:
                _kill_tree(proc)
                out, err = proc.communicate()
                break

    duration = time.monotonic() - start
    out, t1 = _tail(out or "", max_output)
    err, t2 = _tail(err or "", max_output)
    code = proc.returncode
    kind = classify_error(code, err, timed_out, killed)
    res = ShellResult(
        ran=True, decision=decision, exit_code=code, stdout=out, stderr=err,
        timed_out=timed_out, killed=killed, truncated=t1 or t2, duration=round(duration, 3),
        error_kind=kind,
    )
    guard.record_result(
        decision, ran=True, exit_code=code, timed_out=timed_out, killed=killed,
        duration=res.duration, error_kind=kind, stdout_tail=out[-2000:], stderr_tail=err[-2000:],
    )
    return res
