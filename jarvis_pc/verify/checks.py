"""Verification checks. A task is not done until checks pass."""
from __future__ import annotations

from ..text import plural

import hashlib
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from ..actions.shell import IS_WINDOWS, _kill_tree, run, shell_argv
from ..guardrails.gate import Guardrails


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    evidence: str = ""


def file_check(path: str, sha256: Optional[str] = None, min_size: int = 0) -> CheckResult:
    name = f"file {path}"
    if not os.path.isfile(path):
        return CheckResult(name, False, "missing")
    size = os.path.getsize(path)
    if size < min_size:
        return CheckResult(name, False, f"too small ({size} bytes)")
    if sha256:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest().lower() != sha256.lower():
            return CheckResult(name, False, "hash mismatch", h.hexdigest())
        return CheckResult(name, True, "hash matches", h.hexdigest())
    return CheckResult(name, True, f"{size} bytes")


def dir_check(path: str, min_files: int = 1) -> CheckResult:
    name = f"folder {path}"
    if not os.path.isdir(path):
        return CheckResult(name, False, "missing")
    count = sum(len(files) for _, _, files in os.walk(path))
    return CheckResult(name, count >= min_files, f"{plural(count, 'file')}")


def port_check(host: str, port: int, timeout: float = 2.0) -> CheckResult:
    name = f"port {host}:{port}"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return CheckResult(name, True, "accepting connections")
    except OSError as e:
        return CheckResult(name, False, str(e))


def process_check(name_part: str) -> CheckResult:
    name = f"process {name_part}"
    argv = ["tasklist", "/FO", "CSV", "/NH"] if IS_WINDOWS else ["ps", "-A", "-o", "comm="]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=15, errors="replace").stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return CheckResult(name, False, str(e))
    hits = [ln for ln in out.splitlines() if name_part.lower() in ln.lower()]
    return CheckResult(name, bool(hits), f"{len(hits)} match(es)", "\n".join(hits[:5]))


def command_check(guard: Guardrails, command: str, expect: Optional[str] = None,
                  cwd: Optional[str] = None, timeout: float = 30.0) -> CheckResult:
    """Run a command (through the gate) and check exit code and optional output text."""
    name = f"command {command}"
    res = run(guard, command, cwd=cwd, timeout=timeout, intent="verify")
    if not res.ran:
        return CheckResult(name, False, "blocked by guardrails: " + "; ".join(res.decision.reasons[-2:]))
    text = (res.stdout + "\n" + res.stderr).strip()
    if res.exit_code != 0:
        return CheckResult(name, False, f"exit {res.exit_code} ({res.error_kind})", text[-500:])
    if expect and expect.lower() not in text.lower():
        return CheckResult(name, False, f"output does not contain {expect!r}", text[-500:])
    return CheckResult(name, True, "ok", text[-500:])


def launch_check(guard: Guardrails, command: str, cwd: Optional[str] = None, wait: float = 5.0,
                 expect_alive: bool = True) -> CheckResult:
    """Launch a program (through the gate) and see whether it survives.

    expect_alive=True: for apps/servers, passes if still running after `wait` seconds.
    expect_alive=False: for tools, passes if it exits with code 0 within `wait` seconds.
    The process is stopped afterwards either way.
    """
    name = f"launch {command}"
    d = guard.authorize(command, cwd=cwd, intent="verify launch")
    if not d.allowed:
        return CheckResult(name, False, "blocked by guardrails: " + "; ".join(d.reasons[-2:]))
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  text=True, errors="replace", cwd=cwd)
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(shell_argv(command), **kwargs)  # type: ignore[arg-type]
    except OSError as e:
        return CheckResult(name, False, str(e))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and proc.poll() is None:
        guard.killswitch.check()
        time.sleep(0.1)
    alive = proc.poll() is None
    if alive:
        _kill_tree(proc)
    try:
        out = proc.communicate(timeout=5)[0] or ""
    except subprocess.TimeoutExpired:
        out = ""
    code = proc.returncode
    guard.record_result(d, ran=True, exit_code=code, intent="verify launch", alive_after_wait=alive)
    if expect_alive:
        return CheckResult(name, alive, "still running after wait" if alive else f"exited early (code {code})",
                           out[-500:])
    if alive:
        return CheckResult(name, False, f"still running after {wait}s", out[-500:])
    return CheckResult(name, code == 0, f"exit {code}", out[-500:])
