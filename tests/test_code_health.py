"""Catch mistakes like a missing import before they crash Jarvis at runtime."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_undefined_names():
    out = subprocess.run([sys.executable, "-m", "pyflakes", "main.py", "brain", "core", "audio", "tools", "imaging", "hud"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    problems = [line for line in out.splitlines() if "undefined name" in line or "syntax" in line.lower()]
    assert not problems, "\n".join(problems)
