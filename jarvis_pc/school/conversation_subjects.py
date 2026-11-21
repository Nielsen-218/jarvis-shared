"""School subjects for natural conversation and assistance (blueprint area 26): the same facts asked the way people really talk.

Casual wording, filler, typos, indirect questions and a follow-up that leans on context ("and in that folder, how many .txt?").
Graded on the same ground truth as the plain versions, so a right answer proves Jarvis understood the intent, not a fixed phrasing.
"""
from __future__ import annotations

import os
from typing import List

from .curriculum import Context, HIGH_SCHOOL, Num, Subject, Task

_NAME = ["hey quick one, what's this machine called", "yo what do you call this pc", "can u tell me the name of the computer im on pls",
         "sorry, dumb question - whats my computer's name again"]
_SPACE = ["hey how much room do i have left on c", "am i running out of space on c? how much is free", "tell me whats left on my c drive pls",
          "ugh my disk feels full, how much free space is on C"]
_UNITS = (1.0, 1e3, 1e6, 1e9, 1e12, 1024.0, 1024.0 ** 2, 1024.0 ** 3, 1024.0 ** 4)


def _casual_name(c: Context) -> Task:
    return Task(c.rng.choice(_NAME), {}, [c.facts.computer_name()])


def _casual_space(c: Context) -> Task:
    return Task(c.rng.choice(_SPACE), {}, [Num(c.facts.disk()[1], 0.03, _UNITS)])


def _follow_up(c: Context) -> Task:
    d = c.folder(c.word(6))
    txt = c.rng.randint(2, 6)
    logs = c.rng.randint(txt + 1, txt + 4)          # never equal to the .txt count, so "count everything" cannot pass
    for _ in range(txt):
        c.write(os.path.join(d, c.word(6) + ".txt"), c.word(10))
    for _ in range(logs):
        c.write(os.path.join(d, c.word(6) + ".log"), c.word(10))
    ask = "thanks! and in {dir}, how many of those files are .txt? just the number, not the total"
    return Task(ask, {"dir": d}, [Num(txt, 0.0)], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("conv_casual_name", "answer a casually worded question", hs, _casual_name, ("slang question", "informal ask"), needs=("pc_name",), needs_windows=True),
        Subject("conv_casual_space", "answer a worried, indirect question", hs, _casual_space, ("worried user", "indirect ask"), needs=("pc_disk_free",), needs_windows=True),
        Subject("conv_follow_up", "answer a follow-up that leans on context", hs, _follow_up, ("follow-up question", "context"), needs=("file_find_ext",)),
    ]
