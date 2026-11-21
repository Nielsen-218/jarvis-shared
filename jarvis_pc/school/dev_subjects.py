"""School subjects for programming and development environments (blueprint area 11), graded against files the harness writes
and the real tools installed on this PC. Read-only: the gate already lets `python --version` and file reads run automatically.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import List

from .curriculum import Absent, Context, HIGH_SCHOOL, Num, Subject, Task


def _none(ask: str, params: dict) -> Task:
    return Task(ask, params, [], None, 0)


def _python_version(c: Context) -> Task:
    ask = "which version of Python is installed on this PC"
    exe = shutil.which("python")
    if not exe:
        return _none(ask, {})
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout.strip() or ""
    except Exception:  # noqa: BLE001
        return _none(ask, {})
    version = out.replace("Python", "").strip()
    return Task(ask, {}, [version] if version else [], None, 0)


def _requirements_count(c: Context) -> Task:
    ask = "how many packages does the requirements file {file} list"
    n = c.rng.randint(3, 9)
    lines = ["# packages for the project", ""]
    for _ in range(n):
        lines.append(f"{c.word(7)}=={c.rng.randint(1, 9)}.{c.rng.randint(0, 9)}.{c.rng.randint(0, 9)}")
    path = c.write(os.path.join(c.folder(c.word(6)), "requirements.txt"), "\n".join(lines) + "\n")
    return Task(ask, {"file": path}, [Num(n, 0.0)], None, 0)


def _package_name(c: Context) -> Task:
    ask = "what is the name of the project described by the package file {file}"
    name = c.word(6) + "-" + c.word(5)
    decoys = [f"{c.rng.randint(1, 9)}.{c.rng.randint(10, 99)}.{c.rng.randint(10, 99)}", c.word(9)]
    text = '{\n  "name": "%s",\n  "version": "%s",\n  "description": "%s"\n}\n' % (name, decoys[0], decoys[1])
    path = c.write(os.path.join(c.folder(c.word(6)), "package.json"), text)
    return Task(ask, {"file": path}, [name, Absent(tuple(decoys))], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("dev_python_version", "which Python version is installed", hs, _python_version, ("python version", "interpreter version")),
        Subject("dev_requirements_count", "count the packages a project needs", hs, _requirements_count, ("dependency count", "pip requirements"), needs=("file_read",)),
        Subject("dev_package_name", "read a project's name from its package file", hs, _package_name, ("project name", "package json"), needs=("file_read",)),
    ]
