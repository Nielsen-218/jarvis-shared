"""School subjects for planning a task of several dependent steps (blueprint area 22), graded on the finished state of the folders.

The teacher gets one request that needs its steps in the right order (make a folder, move files into it, pack it, record how many).
Every step is a normal gated command inside the sandbox; the harness checks the END state, not the commands.
"""
from __future__ import annotations

import os
import zipfile
from typing import List, Tuple

from .curriculum import Context, HIGH_SCHOOL, Subject, Task


def _tidy_and_report(c: Context) -> Task:
    ask = "tidy {dir}: move every .log file into a new folder called logs inside it, zip that logs folder to {zip}, and write how many logs were moved into count.txt inside {dir}"
    d = c.folder(c.word(6))
    n = c.rng.randint(3, 6)
    logs = {c.word(6) + ".log": c.word(12) for _ in range(n)}
    keep = [c.word(6) + ".txt" for _ in range(2)]
    for name, body in logs.items():
        c.write(os.path.join(d, name), body)
    for name in keep:
        c.write(os.path.join(d, name), c.word(10))
    out = os.path.join(c.sandbox, c.word(6) + ".zip")

    def check() -> Tuple[bool, str]:
        dest = os.path.join(d, "logs")
        for name, body in logs.items():
            p = os.path.join(dest, name)
            if not os.path.isfile(p) or open(p, encoding="utf-8").read() != body:
                return False, f"{name} did not land in the logs folder intact"
            if os.path.exists(os.path.join(d, name)):
                return False, f"{name} is still in the original folder"
        if not all(os.path.isfile(os.path.join(d, k)) for k in keep):
            return False, "a file that was not a log was moved or lost"
        if not os.path.isfile(out):
            return False, "the zip was not created"
        try:
            with zipfile.ZipFile(out) as z:
                got = {os.path.basename(i) for i in z.namelist()}
        except zipfile.BadZipFile:
            return False, "the zip is not a valid archive"
        if not set(logs) <= got:
            return False, "the zip is missing logs"
        p = os.path.join(d, "count.txt")
        text = open(p, encoding="utf-8").read().strip() if os.path.isfile(p) else ""
        if text != str(n):
            return False, f"count.txt should say {n}, it says {text!r}"
        return True, "folder, move, zip and count are all right"
    return Task(ask, {"dir": d, "zip": out}, [], check, 1)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("plan_tidy_and_report", "plan and finish a four-step tidy-up", hs, _tidy_and_report, ("multi-step job", "organise then archive"),
                needs=("job_move_matching", "job_zip")),
    ]
