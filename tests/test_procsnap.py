"""core/procsnap: one system call for every process's name and memory; performance_check answers from it."""
import os
import sys
import time

import pytest

from core import procsnap
from tools import gaming


@pytest.mark.skipif(sys.platform != "win32", reason="the one-call table is Windows-only")
def test_the_snapshot_holds_this_process_with_its_real_memory_and_is_fast():
    import psutil
    started = time.monotonic()
    rows = procsnap._windows()
    took = time.monotonic() - started
    mine = [r for r in rows if r[0] == os.getpid()]
    assert len(mine) == 1 and mine[0][1].lower().startswith("python"), mine
    real = psutil.Process().memory_info().rss
    assert abs(mine[0][2] - real) < 64_000_000, (mine[0][2], real)       # same figure, give or take what moved between the two reads
    assert len(rows) > 20 and took < 3, (len(rows), took)


def test_a_failed_system_call_falls_back_to_the_portable_reader(monkeypatch):
    monkeypatch.setattr(procsnap, "_windows", lambda: (_ for _ in ()).throw(OSError("no table")))
    monkeypatch.setattr(procsnap, "_portable", lambda: [(1, "a.exe", 5)])
    assert procsnap.processes() == [(1, "a.exe", 5)]


def test_performance_check_reports_recorders_and_heavy_apps_from_the_snapshot(monkeypatch):
    rows = [(10, "obs64.exe", 300_000_000), (11, "Huge.exe", 2_000_000_000), (12, "small.exe", 10_000_000),
            (13, "python.exe", 3_000_000_000)]
    monkeypatch.setattr(procsnap, "processes", lambda: rows)

    class _Proc:
        def __init__(self, pid):
            self.pid = pid

        def cwd(self):
            from core.config import ROOT
            return str(ROOT) if self.pid == 13 else "C:/elsewhere"

    monkeypatch.setattr(gaming.psutil, "Process", _Proc)
    text = gaming.performance_check()
    assert "OBS" in text and "Huge.exe (2000 MB)" in text
    assert "small.exe" not in text and "python.exe" not in text       # Jarvis's own process is never reported


def test_performance_check_never_asks_each_process_for_its_memory(monkeypatch):
    monkeypatch.setattr(procsnap, "processes", lambda: [(12, "small.exe", 10_000_000)])
    monkeypatch.setattr(gaming.psutil, "process_iter", lambda *a, **k: (_ for _ in ()).throw(AssertionError("per-process read")))
    assert "Heavy background apps: none" in gaming.performance_check()
