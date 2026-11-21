"""Controllers count as "the owner is here" (Windows' idle timer does not see them); stick drift does not."""
import pytest

from core import acquire, inputwatch

PAD = ("xinput", 0)
REST = (0, 0, 0, 1200, -900, 300, 0)


def test_stick_drift_is_not_use_but_buttons_triggers_and_sticks_are():
    assert not inputwatch.used(PAD, REST, (0, 3, 0, 2500, -2000, 900, 400))     # a pad lying on the desk
    assert inputwatch.used(PAD, REST, (0x1000, 0, 0) + REST[3:])                   # A button
    assert inputwatch.used(PAD, REST, (0, 200, 0) + REST[3:])                      # left trigger
    assert inputwatch.used(PAD, REST, (0, 0, 0, 30000, -900, 300, 0))              # stick pushed
    joy = ("joy", 0)
    rest = (0, 65535, 32768, 32768, 0, 0, 0, 0)
    assert not inputwatch.used(joy, rest, (0, 65535, 33500, 32000, 0, 0, 0, 0))
    assert inputwatch.used(joy, rest, (0, 9000) + rest[2:])                       # d-pad
    assert inputwatch.used(joy, None, rest)                                        # plugged in


def test_using_a_controller_changes_the_activity_token_and_resets_idle(monkeypatch):
    monkeypatch.setattr(inputwatch, "start", lambda: None)
    monkeypatch.setattr(inputwatch, "_started", 0.0)
    monkeypatch.setattr(inputwatch, "_last_use", None)
    monkeypatch.setattr(inputwatch, "_uses", 0)
    monkeypatch.setattr(inputwatch, "_last", {PAD: REST})
    monkeypatch.setattr(inputwatch, "keyboard_mouse_tick", lambda: 5)
    monkeypatch.setattr(inputwatch, "keyboard_mouse_idle", lambda: 3600.0)       # keyboard and mouse untouched for an hour
    monkeypatch.setattr(inputwatch, "turn_idle", lambda: float("inf"))
    before = inputwatch.activity_token()
    inputwatch.observe({PAD: (0, 2, 0, 1500, -700, 200, 100)}, now=10.0)       # drift only
    assert inputwatch.activity_token() == before
    inputwatch.observe({PAD: (0x2000,) + REST[1:]}, now=20.0)                  # B pressed
    assert inputwatch.activity_token() != before
    monkeypatch.setattr(inputwatch.time, "monotonic", lambda: 80.0)
    assert inputwatch.idle_seconds() == 60.0                                    # the controller, not the keyboard, decides


def test_the_lab_and_the_learner_see_controller_use():
    from core import skilllab
    import inspect
    assert "inputwatch.activity_token" in inspect.getsource(skilllab._last_input)
    from core import scheduler
    assert "inputwatch.idle_seconds" in inspect.getsource(scheduler.idle_seconds)


def test_a_game_from_a_library_folder_blocks_practice_even_outside_jarvis(monkeypatch):
    import psutil

    class P:
        def __init__(self, name, exe):
            self.info = {"name": name, "exe": exe}
    procs = [P("explorer.exe", r"C:\Windows\explorer.exe"), P("Helldivers2.exe", r"D:\SteamLibrary\steamapps\common\Helldivers 2\bin\Helldivers2.exe")]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(procs))
    assert acquire.running_game() == "Helldivers2.exe"
    procs[1] = P("UnityCrashHandler64.exe", r"D:\SteamLibrary\steamapps\common\X\UnityCrashHandler64.exe")
    assert acquire.running_game() == ""


def test_asking_jarvis_by_voice_or_phone_counts_as_being_here(monkeypatch, tmp_path):
    """2026-11-08: a phone request waited 121 s behind practice that thought nobody was there."""
    monkeypatch.setenv("JARVIS_TURN_DIR", str(tmp_path))
    monkeypatch.setattr(inputwatch, "start", lambda: None)
    monkeypatch.setattr(inputwatch, "keyboard_mouse_idle", lambda: 3600.0)
    monkeypatch.setattr(inputwatch, "controller_idle", lambda: 3600.0)
    monkeypatch.setattr(inputwatch, "keyboard_mouse_tick", lambda: 5)
    assert inputwatch.idle_seconds() == 3600.0                       # no request yet
    before = inputwatch.activity_token()
    inputwatch.note_turn()
    assert inputwatch.idle_seconds() < 5                             # the practice watcher sees the owner at once
    assert inputwatch.activity_token() != before                     # ... and so do the running learner and lab guards


class _FakeApis:
    """XInput, winmm and Windows' raw-input device count stand-ins: which slots hold a controller, how often each slot
    was asked, and how many input devices Windows reports (it changes when one arrives or leaves)."""

    def __init__(self, xinput=(), joy=()):
        self.x_live, self.j_live = set(xinput), set(joy)
        self.x_calls, self.j_calls = {}, {}
        self.devices, self.raise_on, self.raise_always = 40, None, set()
        self.x_state = {}
        outer = self

        class X:
            def XInputGetState(self, i, st):
                outer.x_calls[i] = outer.x_calls.get(i, 0) + 1
                if i in outer.x_live and i in outer.x_state:
                    st._obj.pad.buttons = outer.x_state[i]
                return 0 if i in outer.x_live else 1167          # ERROR_DEVICE_NOT_CONNECTED

        class J:
            def joyGetPosEx(self, j, _ji):
                outer.j_calls[j] = outer.j_calls.get(j, 0) + 1
                if outer.raise_on == j or j in outer.raise_always:
                    outer.raise_on = None
                    raise OSError("exception: access violation reading 0x0000000000000010")
                return 0 if j in outer.j_live else 165           # JOYERR_PARMS: no such joystick (what this PC answers)
        self.xinput, self.joy = X(), J()

    def plug(self, api, slot, counted=True):
        (self.x_live if api == "xinput" else self.j_live).add(slot)
        self.devices += counted                                    # a real arrival changes Windows' device count


def _poll(apis, slots, seconds, start=0.0, step=inputwatch.POLL_SECONDS, on=None, monkeypatch=None):
    monkeypatch.setattr(inputwatch, "device_count", lambda: apis.devices)
    t, seen = start, {}
    while t < start + seconds:
        if on:
            on(t)
        for device in inputwatch.read_controllers(apis.xinput, apis.joy, slots, now=t):
            seen.setdefault(device, t)
        t += step
    return seen


def test_an_empty_joystick_0_is_asked_only_after_a_device_arrives(monkeypatch):
    """Crash 2026-11-11 21:25 (0xC0000374): asked about joystick 0 with no device there, winmm makes DirectInput rebuild
    its device list (18-39 ms, every time); 16 slots 4 times a second was ~350,000 rebuilds a day and the heap broke in
    one. A connected pad is still read every poll; the cheap empty slots every 3 s; an empty joystick 0 waits for a
    device to arrive (or the slow fallback)."""
    apis = _FakeApis(xinput={0})
    _poll(apis, inputwatch.new_slots(), seconds=60, monkeypatch=monkeypatch)
    polls = int(60 / inputwatch.POLL_SECONDS)
    assert apis.x_calls[0] == polls                                    # the pad in use: every poll
    assert all(apis.x_calls[i] <= 60 / inputwatch.XINPUT_EMPTY_EVERY + 1 for i in (1, 2, 3))
    assert apis.j_calls[0] == 1                                        # one look at start; was 240 a minute
    assert all(apis.j_calls[j] <= 60 / inputwatch.JOY_EMPTY_EVERY + 1 for j in range(1, 16))


def test_a_pad_turned_on_is_seen_at_once_when_windows_counts_a_new_device(monkeypatch):
    """Review 2026-11-11: with a fixed 30 s winmm period, a DualSense or wheel that only winmm sees was invisible for up to
    30 s while he pressed buttons (practice or a mission step kept running). A device arrival is seen within one poll."""
    apis = _FakeApis()

    def on(t):
        if t == 5.0:
            apis.plug("joy", 0)
        if t == 9.0:
            apis.plug("xinput", 2)
    seen = _poll(apis, inputwatch.new_slots(), seconds=20, on=on, monkeypatch=monkeypatch)
    assert seen[("joy", 0)] == 5.0 and seen[("xinput", 2)] == 9.0


def test_a_pad_that_answers_winmm_a_moment_after_windows_counts_it_is_found_by_the_next_arrival_check(monkeypatch):
    """Review 2026-11-11 (v2): if the count changed before winmm mapped the pad, slot 0 was not asked again for 10 min."""
    apis = _FakeApis()

    def on(t):
        if t == 5.0:
            apis.devices += 1                                          # Windows counts it...
        if t == 6.5:
            apis.j_live.add(0)                                         # ...winmm answers for it 1.5 s later
    seen = _poll(apis, inputwatch.new_slots(), seconds=20, on=on, monkeypatch=monkeypatch)
    assert seen[("joy", 0)] == min(5.0 + c for c in inputwatch.ARRIVAL_CHECKS if 5.0 + c >= 6.5)   # the next check after it answers


def test_a_pad_windows_does_not_count_is_still_found(monkeypatch):
    apis = _FakeApis()

    def on(t):
        if t == 5.0:
            apis.plug("joy", 3, counted=False)
            apis.plug("xinput", 1, counted=False)
        if t == 7.0:
            apis.plug("joy", 0, counted=False)
    seen = _poll(apis, inputwatch.new_slots(), seconds=inputwatch.JOY0_EMPTY_EVERY + 5, on=on, monkeypatch=monkeypatch)
    assert 5.0 <= seen[("xinput", 1)] <= 5.0 + inputwatch.XINPUT_EMPTY_EVERY
    assert 5.0 <= seen[("joy", 3)] <= 5.0 + inputwatch.JOY_EMPTY_EVERY
    assert 7.0 <= seen[("joy", 0)] <= inputwatch.JOY0_EMPTY_EVERY + inputwatch.POLL_SECONDS     # the slow fallback


def test_one_failed_read_of_a_pad_is_asked_again_soon_not_dropped_for_minutes(monkeypatch):
    """Review 2026-11-11: one glitched read dropped a winmm pad until the next full check (then 30 s)."""
    apis = _FakeApis(joy={0})
    slots = inputwatch.new_slots()
    _poll(apis, slots, seconds=2.0, monkeypatch=monkeypatch)
    apis.j_live.discard(0)                                             # one read fails...
    _poll(apis, slots, seconds=0.25, start=2.0, monkeypatch=monkeypatch)
    apis.j_live.add(0)                                                 # ...and the pad answers again
    seen = _poll(apis, slots, seconds=3.0, start=2.25, monkeypatch=monkeypatch)
    assert seen[("joy", 0)] <= 2.0 + inputwatch.RETRY_AFTER + inputwatch.POLL_SECONDS


def test_a_failed_read_on_an_arrival_check_keeps_its_retry(monkeypatch):
    """Review 2026-11-11 (v2): the retry was erased when the failed read landed on a full check: the pad next read 600 s
    later."""
    apis = _FakeApis(joy={3})
    slots = inputwatch.new_slots()
    _poll(apis, slots, seconds=10.0, monkeypatch=monkeypatch)

    def on(t):
        if t == 10.0:
            apis.devices += 1                                          # a headset arrives...
            apis.j_live.discard(3)                                     # ...and the pad's read fails on that poll
        if t == 10.25:
            apis.j_live.add(3)
    seen = _poll(apis, slots, seconds=5.0, start=10.0, on=on, monkeypatch=monkeypatch)
    assert seen[("joy", 3)] <= 10.0 + inputwatch.RETRY_AFTER + inputwatch.POLL_SECONDS


def test_an_unplugged_joystick_0_is_asked_once_more_then_left_alone(monkeypatch):
    apis = _FakeApis(joy={0})
    slots = inputwatch.new_slots()
    _poll(apis, slots, seconds=2.0, monkeypatch=monkeypatch)
    apis.j_live.discard(0)
    before = apis.j_calls[0]
    _poll(apis, slots, seconds=60, start=2.0, monkeypatch=monkeypatch)
    assert apis.j_calls[0] - before == 2                              # the failed read and the one retry
    assert 0 not in slots["joy"].live


def test_a_device_count_that_keeps_changing_rebuilds_at_most_every_rebuild_gap(monkeypatch):
    apis = _FakeApis()

    def on(t):
        apis.devices += 1                                              # flaps on every poll
    _poll(apis, inputwatch.new_slots(), seconds=60, on=on, monkeypatch=monkeypatch)
    assert apis.j_calls[0] <= 60 / inputwatch.REBUILD_GAP + 1          # was 240 a minute


@pytest.mark.parametrize("api", ["xinput", "joy"])
def test_one_failed_read_of_a_pad_on_the_desk_is_not_use(monkeypatch, api):
    """Review 2026-11-11 (pre-existing): the pad dropped out of the reads for one poll, so its next read was a 'plug-in',
    i.e. use: a running lab job stopped, missions waited 10 min, practice waited another 15."""
    apis = _FakeApis(**{api: {0}})
    live = apis.x_live if api == "xinput" else apis.j_live

    def on_sleep(n):
        if n == 20:
            live.discard(0)                                            # one read fails...
        if n == 21:
            live.add(0)                                                # ...the pad answers again
    _run_loop(monkeypatch, apis, 60, on_sleep)
    assert inputwatch._uses == 0


def test_a_pad_unplugged_and_plugged_back_in_still_counts_as_use(monkeypatch):
    apis = _FakeApis(xinput={0})

    def on_sleep(n):
        if n == 10:
            apis.x_live.discard(0)
            apis.devices -= 1
        if n == 30:                                                    # 5 s later
            apis.x_live.add(0)
            apis.devices += 1
    _run_loop(monkeypatch, apis, 40, on_sleep)
    assert inputwatch._uses == 1


class _Stop(Exception):
    pass


def _run_loop(monkeypatch, apis, loops, on_sleep=None):
    clock = {"t": 0.0, "loops": 0}

    def sleep(s):
        clock["t"] += s
        clock["loops"] += 1
        if on_sleep:
            on_sleep(clock["loops"])
        if clock["loops"] >= loops:
            raise _Stop
    monkeypatch.setattr(inputwatch, "_load", lambda: (apis.xinput, apis.joy))
    monkeypatch.setattr(inputwatch, "device_count", lambda: apis.devices)
    monkeypatch.setattr(inputwatch.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(inputwatch.time, "sleep", sleep)
    monkeypatch.setattr(inputwatch, "_last", {})
    monkeypatch.setattr(inputwatch, "_missing", {})
    monkeypatch.setattr(inputwatch, "_uses", 0)
    try:
        inputwatch._loop()
    except _Stop:
        pass


def test_an_error_in_one_api_rests_it_and_the_other_is_still_read(monkeypatch):
    """Review 2026-11-11: an OSError used to feed observe() 'no controllers' (every pad on the desk became a 'plug-in'),
    then v2 re-ran the failing check on every poll, the crash path at the old rate, throwing away XInput's reads too.
    ctypes reports an access violation inside the DLL as OSError: such a call rests for API_BACKOFF."""
    apis = _FakeApis(xinput={0})
    apis.raise_always = {0}                                            # joystick 0 fails on every call

    def on_sleep(n):
        if n == 40:
            apis.x_state[0] = 0x1000                                   # he presses A
    _run_loop(monkeypatch, apis, 240, on_sleep)
    assert apis.j_calls[0] <= 5                                        # 1 s, 5 s, 30 s apart: not 240 a minute
    assert apis.x_calls[0] >= 239 and inputwatch._uses == 1            # the pad is still read; the press is seen


def test_the_watcher_thread_asks_an_empty_joystick_0_only_on_a_device_change(monkeypatch):
    """The background loop itself (not only read_controllers) keeps to it."""
    apis = _FakeApis()
    _run_loop(monkeypatch, apis, 240)                                  # one minute of polls
    assert apis.j_calls[0] == 1 and max(apis.x_calls.values()) <= 21
    assert max(apis.j_calls[j] for j in range(1, 16)) <= 21


def test_the_device_count_is_read_from_windows():
    import os
    n = inputwatch.device_count()
    assert (n > 0) if os.name == "nt" else n == -1


def test_the_winmm_joystick_api_is_not_loaded_unless_asked_for(monkeypatch):
    """2026-11-12 20:36: joyGetPosEx corrupted the heap again (0xC0000374) and Jarvis stayed down for 83 minutes. The
    call is no longer loaded at all unless JARVIS_JOY_WINMM=on; XInput alone watches for a controller."""
    loaded = []

    class _Dll:
        def __init__(self, name):
            loaded.append(name)
    monkeypatch.setattr(inputwatch.os, "name", "nt")
    monkeypatch.setattr(inputwatch.ctypes, "WinDLL", _Dll, raising=False)
    monkeypatch.delenv("JARVIS_JOY_WINMM", raising=False)
    xinput, joy = inputwatch._load()
    assert joy is None and xinput is not None and "winmm" not in loaded
    monkeypatch.setenv("JARVIS_JOY_WINMM", "on")
    assert inputwatch._load()[1] is not None and "winmm" in loaded
