"""`python main.py --text` (jarvis.bat --text): a typed console session. It never takes the voice Jarvis's single-instance
mutex, so it may run beside the live, elevated Jarvis: then it is a second process, and the files that have one writer
per machine (the thread ledger, core.thread) and the PC's Syncthing watch stay the live process's (review 2026-11-11)."""
import ast
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

import main
from core import thread


@pytest.mark.skipif(sys.platform != "win32", reason="the single-instance mutex is Windows only")
def test_another_instance_is_seen_without_taking_its_mutex():
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    name = f"Local\\JarvisTextModeTest-{uuid.uuid4().hex}"
    assert main._another_instance(name) is False, "nothing holds a mutex of that name yet"
    assert main._another_instance(name) is False, "checking never creates (or keeps) the mutex itself"
    handle = kernel32.CreateMutexW(None, False, name)
    try:
        assert main._another_instance(name) is True
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def test_beside_the_live_jarvis_the_console_writes_no_thread_rows(monkeypatch):
    monkeypatch.setenv("JARVIS_THREAD", "on")       # restored after the test (_second_process sets it for good)
    assert thread.recording()
    main._second_process()
    assert not thread.recording(), "two processes rewriting thread-pc.jsonl lose each other's rows"


def test_main_starts_the_sync_watch_and_the_thread_only_when_it_is_the_one_jarvis():
    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    src = ast.get_source_segment(Path(main.__file__).read_text(encoding="utf-8"), fn)
    lines = [line.strip() for line in src.splitlines()]
    assert "beside_live = args.text and _another_instance()" in lines
    assert lines[lines.index("if beside_live:") + 1] == "_second_process()"
    watch = lines.index("from core import syncwatch")
    assert lines[watch - 1].startswith("if not beside_live:") and lines[watch + 1] == "syncwatch.start()"


def test_a_console_turn_is_typed_not_voice(monkeypatch):
    class Console:
        channel = None
        asked: list = []

        @contextmanager
        def gathered(self, channel="app"):
            self.channel = channel
            yield self
            self.channel = None

        def ask(self, text, say):
            self.asked.append((text, self.channel))
    lines = iter(["what's on today", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    console = Console()
    main.text_loop(console, type("Speaker", (), {"wait": lambda self: None})(), print)
    assert console.asked == [("what's on today", "typed")]
