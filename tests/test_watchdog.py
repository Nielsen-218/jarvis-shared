"""core/watchdog: every decision with fakes. No process, service, scheduled task, network or wall clock is touched."""
import json
import os

import pytest

from core import watchdog as w


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WATCHDOG_DIR", str(tmp_path / "wd"))
    monkeypatch.setenv("JARVIS_WATCHDOG_LOG", str(tmp_path / "watchdog.log"))
    return tmp_path


class Rig:
    def __init__(self):
        self.t, self.up, self.http, self.stopped = 1000.0, True, True, False
        self.restarts, self.logs, self.records = [], [], []
        self.dog = w.Watchdog(alive=lambda: self.up, answers=lambda: self.http, stopped=lambda: self.stopped,
                              restart=lambda: self.restarts.append(self.t), now=lambda: self.t, log=self.logs.append,
                              crash_for=lambda since: {"code": "0xc0000374", "module": "core/inputwatch.py", "cause": "x"},
                              record=lambda ts, info, ok: self.records.append((ts, info)))

    def step(self, secs=20):
        self.t += secs
        return self.dog.step()


def test_alive_and_answering_does_nothing():
    r = Rig()
    assert [r.step() for _ in range(5)] == ["ok"] * 5 and not r.restarts


def test_slow_but_alive_is_only_a_log_line_after_two_minutes():
    r = Rig()
    r.http = False
    out = [r.step() for _ in range(12)]
    assert out.count("unresponsive") == 1 and out[:5] == ["slow"] * 5 and not r.restarts
    assert sum("not restarting" in line for line in r.logs) == 1
    r.http = True
    assert r.step() == "ok"


def test_dead_and_not_on_purpose_restarts_and_records_the_crash():
    r = Rig()
    r.step()
    r.up = False
    assert r.step() == "restarted" and len(r.restarts) == 1
    assert r.records[0][1]["code"] == "0xc0000374"
    assert r.step() == "starting"            # grace: no second restart while he boots
    assert len(r.restarts) == 1


def test_stopped_on_purpose_is_respected_and_the_watchdog_ends_itself():
    r = Rig()
    r.step()
    r.up, r.stopped = False, True
    assert r.step() == "stopped" and not r.restarts
    for _ in range(14):
        r.step()
    assert r.step() == "exit" and r.dog.done and not r.restarts


def test_restart_script_gap_does_not_race():
    r = Rig()
    r.step()
    r.up, r.stopped = False, True           # stop-jarvis.bat
    for _ in range(4):
        assert r.step() == "stopped"
    r.stopped, r.up = False, True           # start-jarvis.bat clears the marker, he is back
    assert r.step() == "ok" and not r.restarts and not r.dog.done


def test_crash_loop_backs_off_ten_then_thirty_minutes():
    r = Rig()
    r.up = False
    n = 0
    while len(r.restarts) < 3:
        r.step(130)
        n += 1
        assert n < 20
    assert r.step(130) == "backoff" and len(r.restarts) == 3
    start = r.t
    while r.step(60) == "backoff":
        pass
    assert r.t - start >= 600 - 60 and len(r.restarts) == 4
    while len(r.restarts) < 6:
        r.step(130)
    assert r.step(130) == "backoff"
    start = r.t
    while r.step(60) == "backoff":
        pass
    assert r.t - start >= 1800 - 60


def test_stable_run_resets_the_ladder():
    r = Rig()
    r.dog.trips = 1
    for _ in range(40):
        r.step()
    assert r.dog.trips == 0


def test_only_one_watchdog(monkeypatch):
    assert w.take_lock()
    monkeypatch.setattr(w, "pid_alive", lambda pid: True)
    w.lock_file().write_text("999999")
    assert not w.take_lock()
    monkeypatch.setattr(w, "pid_alive", lambda pid: False)
    assert w.take_lock() and w.lock_file().read_text() == str(os.getpid())
    w.drop_lock()
    assert not w.lock_file().exists()


def test_ensure_running_respects_switch_and_existing(monkeypatch):
    launched = []
    monkeypatch.setattr(w.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(w.sys, "platform", "win32")
    monkeypatch.setenv("JARVIS_WATCHDOG", "off")
    assert not w.ensure_running() and not launched
    monkeypatch.setenv("JARVIS_WATCHDOG", "on")
    monkeypatch.setattr(w, "running", lambda: True)
    assert not w.ensure_running()
    monkeypatch.setattr(w, "running", lambda: False)
    assert w.ensure_running() and launched


def test_marker_roundtrip():
    assert not w.stopped_on_purpose()
    w.mark_stopped()
    assert w.stopped_on_purpose()
    w.clear_stopped()
    assert not w.stopped_on_purpose()


SAMPLE = r"""Windows fatal exception: code 0x8001010d

Current thread 0x00001111 (most recent call first):
  File "C:\x\foo.py", line 1 in a

Windows fatal exception: code 0xc0000374

Thread 0x0000b4a0 (most recent call first):
  File "C:\Python312\Lib\threading.py", line 359 in wait

Current thread 0x00005e6c (most recent call first):
  File "C:\Users\you\ECC\jarvis\core\inputwatch.py", line 71 in read_controllers
  File "C:\Python312\Lib\threading.py", line 1012 in run

Thread 0x0000bb5c (most recent call first):
  File "C:\Users\you\ECC\jarvis\tools\gaming.py", line 85 in _loop
"""


def test_parse_the_newest_crash_block():
    info = w.parse_crash(SAMPLE)
    assert info["code"] == "0xc0000374" and info["name"] == "heap corruption"
    assert info["cause"] == "the controller watcher" and info["module"].endswith("core/inputwatch.py")
    assert w.parse_crash("nothing here") is None
    assert w.parse_crash("Windows fatal exception: access violation\n\nCurrent thread 0x1 (most recent call first):\n"
                         '  File "C:\\x\\jarvis\\core\\zzz.py", line 3 in f\n')["code"] == "0xc0000005"


def test_crash_only_counts_when_newer_than_the_start(tmp_path):
    log = tmp_path / "crash.log"
    log.write_text(SAMPLE, encoding="utf-8")
    mtime = log.stat().st_mtime
    assert w.read_crash(log, mtime - 10)["code"] == "0xc0000374"
    assert w.read_crash(log, mtime + 10) is None
    assert w.read_crash(tmp_path / "missing.log", 0) is None


def test_record_then_notice_on_next_start_once():
    ts = 1762997760
    w.record_crash(ts, {"code": "0xc0000374", "name": "", "module": "m", "cause": "the controller watcher"}, True)
    sent = []
    line = w.announce_pending(lambda title, msg: sent.append(msg))
    assert sent == [line] and line.startswith("I crashed at ") and line.endswith("Cause: the controller watcher.")
    assert w.announce_pending(lambda *a: sent.append(a)) == "" and len(sent) == 1
    assert json.loads(w.crashes_file().read_text())[0]["announced"] is True


def test_voice_answers_only_crash_questions():
    assert "no crash on record" in w.voice("Did you crash?")
    w.record_crash(1762997760, {"code": "0x1", "cause": "the camera"}, True)
    assert "the camera" in w.voice("why did you restart", now=1762997760 + 60)
    for line in ("did the game crash", "my game crashed", "did you restart the router", "restart yourself",
                 "why did you say that", "did you crash my game"):
        assert w.voice(line) == "", line
