"""core/gameboost: the Gaming protocol's first step puts heavy background work behind the game and closes idle launchers,
and everything is put back when the game closes (owner 2026-11-20: "I want maximum performance").

Rules tested here: only known heavy work is lowered, and only from normal priority or above; a reused process id is never
touched; a launcher is left alone when it has a window, is running something else, is busy, or cannot be started again;
what is closed is written down before it is closed; everything comes back after the game (once it has really ended),
after a game that never started, after a Jarvis restart mid-game, and on "stop the gaming protocol" (from the phone
too); nothing is started twice, and nothing is restarted after a reboot; a priority he changed himself is kept.
No real process is touched (a fake system)."""
import pytest

from core import gameboost as B
from core import protocols as P

NORMAL, BELOW = 32, B.BELOW_NORMAL
EPIC = '"C:\\Epic\\EpicGamesLauncher.exe" -silent -launchcontext=boot'
ONEDRIVE = '"C:\\OneDrive\\OneDrive.exe" /background'


class FakeSystem:
    def __init__(self):
        self.procs = {}           # pid -> dict(name, created, prio, cmd, window, exe, kids, busy)
        self.ended, self.launched, self.paused = [], [], []
        self.run_keys = {"OneDrive": ONEDRIVE, "EpicGamesLauncher": EPIC,
                         "RiotClient": "C:\\Riot Games\\Riot Client\\RiotClientServices.exe --launch-background-mode",
                         "Grammarly": '"C:\\G\\Grammarly.Desktop.exe" --autostart'}
        self.files = {"C:\\OneDrive\\OneDrive.exe", "C:\\Epic\\EpicGamesLauncher.exe",
                      "C:\\Riot Games\\Riot Client\\RiotClientServices.exe", "C:\\G\\Grammarly.Desktop.exe"}
        self.launch_ok, self.end_ok, self.end_raises, self.launch_raises = True, True, False, False
        self.booted = 0.0

    def add(self, pid, name, prio=NORMAL, cmd="", window=False, created=100.0, exe="", kids=(), busy=False):
        self.procs[pid] = {"name": name, "created": created, "prio": prio, "cmd": cmd, "window": window, "exe": exe,
                           "kids": set(kids), "busy": busy}

    def processes(self):
        return [{"pid": pid, "name": p["name"], "created": p["created"]} for pid, p in self.procs.items()]

    def _same(self, pid, created):
        p = self.procs.get(pid)
        return p if p and abs(p["created"] - created) < 1.0 else None

    def cmdline(self, pid):
        return self.procs.get(pid, {}).get("cmd", "")

    def priority(self, pid, created):
        p = self._same(pid, created)
        return p["prio"] if p else None

    def set_priority(self, pid, created, value):
        p = self._same(pid, created)
        if not p:
            return False
        p["prio"] = value
        return True

    def exe(self, pid):
        return self.procs.get(pid, {}).get("exe", "")

    def descendants(self, pid):
        return self.procs[pid]["kids"]

    def busy(self, pids):
        return any(self.procs[p]["busy"] for p in pids)

    def has_window(self, pids):
        return any(self.procs[p]["window"] for p in pids if p in self.procs)

    def end(self, pid, created):
        name = self.procs[pid]["name"]
        if self.end_ok:
            self.ended.append(name)
            del self.procs[pid]
        if self.end_raises:
            raise TimeoutError("taskkill hung")
        return self.end_ok

    def pause_onedrive(self, exe, pids):
        self.paused.append(exe)
        for p in pids:
            self.procs.pop(p, None)
        return True

    def run_command(self, value):
        return self.run_keys.get(value, "")

    def is_file(self, path):
        return path in self.files

    def boot_time(self):
        return self.booted

    def running(self, exe_name):
        return any(p["name"] == exe_name for p in self.procs.values())

    def launch(self, command, label):
        if self.launch_raises:
            self.launched.append(command)
            raise OSError("explorer.exe could not be started")
        if self.launch_ok:
            self.launched.append(command)
        return self.launch_ok


@pytest.fixture
def pc(monkeypatch, tmp_path):
    fake = FakeSystem()
    monkeypatch.setattr(B, "OS", fake)
    monkeypatch.setenv("JARVIS_PC_STATE_DIR", str(tmp_path / "pc-state"))
    monkeypatch.setattr(B, "_last_sweep", 0.0)
    monkeypatch.setattr(B, "_no_game", 0)
    return fake


def busy_pc(pc):
    """His PC on 2026-11-20: a test run, a coding agent, Jarvis-unrelated python, idle launchers, his Discord and browser."""
    pc.add(11, "python.exe", cmd="python -m pytest tests/ -q -p no:cacheprovider")
    pc.add(12, "python.exe", cmd="python server.py")                   # not heavy work: untouched
    pc.add(13, "claude.exe")
    pc.add(15, "python.exe", cmd="python -m scripts.gate check")       # the gate and its pytest: one job to him
    pc.add(14, "shadercompileworker.exe", prio=BELOW)                  # already behind: untouched, not recorded
    pc.add(20, "epicgameslauncher.exe", kids={"epicwebhelper.exe"})
    pc.add(21, "onedrive.exe")
    pc.add(22, "grammarly.desktop.exe", window=True)                   # he has its window open: left alone
    pc.add(30, "discord.exe")
    pc.add(31, "msedge.exe", window=True)
    pc.add(32, "r5apex_dx12.exe")


def game_over(pc):
    """The game closed and stays closed: the watcher's checks until it puts everything back."""
    pc.procs.pop(32, None)
    said = ""
    for _ in range(B.NO_GAME_TICKS):
        said = B.tick(None)
    return said


def test_heavy_work_goes_behind_the_game_and_idle_launchers_close(pc):
    busy_pc(pc)
    changed, text = B.free_up()
    assert changed
    assert pc.procs[11]["prio"] == BELOW and pc.procs[13]["prio"] == BELOW and pc.procs[15]["prio"] == BELOW
    assert pc.procs[12]["prio"] == NORMAL, "a python process that is not a test run or build is not his heavy work"
    assert pc.procs[14]["prio"] == BELOW and len(B._load()["lowered"]) == 3, "one already behind is not recorded"
    assert pc.ended == ["epicgameslauncher.exe"] and pc.paused == ["C:\\OneDrive\\OneDrive.exe"]
    for untouched in (22, 30, 31, 32):
        assert untouched in pc.procs and pc.procs[untouched]["prio"] == NORMAL
    assert "put Claude coding agents and a full test run behind the game" in text
    assert "closed Epic Games Launcher" in text and "paused OneDrive syncing" in text
    assert "comes back when the game closes" in text and "left alone: Grammarly (it has a window open)" in text


def test_nothing_running_says_so_and_keeps_no_state(pc):
    pc.add(30, "discord.exe")
    assert B.free_up() == (False, "nothing heavy was running in the background")
    assert not B.active()


@pytest.mark.parametrize("setup,why", [
    (lambda pc: pc.run_keys.pop("EpicGamesLauncher"), "I couldn't tell how to start it again"),
    (lambda pc: pc.files.discard("C:\\Epic\\EpicGamesLauncher.exe"), "I couldn't tell how to start it again"),
    (lambda pc: pc.procs[20]["kids"].add("fortniteclient-win64-shipping.exe"), "it is running a game or a tool it started"),
    (lambda pc: pc.procs[20].update(busy=True), "it is busy downloading or installing"),
])
def test_a_launcher_that_must_stay_open_is_left_alone(pc, setup, why):
    pc.add(20, "epicgameslauncher.exe", kids={"epicwebhelper.exe"})
    setup(pc)
    changed, text = B.free_up()
    assert not changed and pc.ended == [] and f"Epic Games Launcher ({why}" in text and not B.active()


def test_a_launcher_that_would_not_close_is_said_not_hidden(pc):
    pc.add(20, "epicgameslauncher.exe")
    pc.end_ok = False
    changed, text = B.free_up()
    assert not changed and "Epic Games Launcher (it didn't close)" in text and not B.active()


def test_a_close_that_crashes_halfway_is_still_put_back(pc):
    """Review 2026-11-20: taskkill hung after the launcher had gone; it was never written down, so never came back."""
    pc.add(20, "epicgameslauncher.exe")
    pc.add(13, "claude.exe")
    pc.end_raises = True
    changed, text = B.free_up()
    assert changed and "closed Epic Games Launcher" in text
    state = B._load()
    assert [c["name"] for c in state["closed"]] == ["Epic Games Launcher"] and len(state["lowered"]) == 1
    B.tick("Apex Legends")
    assert "started Epic Games Launcher again" in game_over(pc) and pc.launched == [EPIC]


def test_everything_comes_back_after_the_game(pc):
    busy_pc(pc)
    B.free_up()
    assert B.tick("Apex Legends") == "" and B._load()["saw_game"]
    said = game_over(pc)
    assert pc.procs[11]["prio"] == NORMAL and pc.procs[13]["prio"] == NORMAL and pc.procs[15]["prio"] == NORMAL
    assert pc.launched == [ONEDRIVE, EPIC], "the way Windows starts them at sign-in"
    assert "background work is back to its usual priority (3 processes)" in said
    assert "started OneDrive and Epic Games Launcher again" in said
    assert not B.active() and B.tick(None) == "", "put back once"


def test_a_game_that_restarts_within_seconds_is_not_the_end(pc):
    pc.add(20, "epicgameslauncher.exe")
    B.free_up()
    B.tick("Apex Legends")
    for _ in range(B.NO_GAME_TICKS - 1):
        assert B.tick(None) == "", "the game crashed and he is relaunching it"
    B.tick("Apex Legends")
    assert B.tick(None) == "" and B.active() and pc.launched == []


def test_a_priority_he_changed_himself_is_kept(pc):
    busy_pc(pc)
    B.free_up()
    pc.procs[13]["prio"] = 64                                          # he set the agents to idle in Task Manager
    B.tick("Apex Legends")
    game_over(pc)
    assert pc.procs[13]["prio"] == 64 and pc.procs[11]["prio"] == NORMAL


def test_a_reused_process_id_is_never_touched(pc):
    pc.add(13, "claude.exe")
    B.free_up()
    pc.procs[13] = {"name": "notepad.exe", "created": 999.0, "prio": BELOW, "cmd": "", "window": True, "exe": "",
                    "kids": set(), "busy": False}
    B.tick("Apex Legends")
    game_over(pc)
    assert pc.procs[13]["prio"] == BELOW, "another process now has that id: left exactly as it is"


def test_heavy_work_started_during_the_game_goes_behind_it_too(pc, monkeypatch):
    pc.add(13, "claude.exe")
    B.free_up()
    pc.add(40, "unrealbuildtool.exe", created=200.0)
    B.tick("Apex Legends")
    assert pc.procs[40]["prio"] == NORMAL, "swept at most every SWEEP_EVERY_S, not on every 5-second check"
    monkeypatch.setattr(B, "_last_sweep", 0.0)
    B.tick("Apex Legends")
    assert pc.procs[40]["prio"] == BELOW
    monkeypatch.setattr(B, "_last_sweep", 0.0)
    B.tick("Apex Legends")
    assert len(B._load()["lowered"]) == 2, "recorded once"


def test_an_unreadable_command_line_is_asked_again(pc, monkeypatch):
    pc.add(11, "python.exe", cmd="")                                   # just started: its command line not readable yet
    B.free_up()
    pc.procs[11]["cmd"] = "python -m pytest tests/"
    pc.add(13, "claude.exe")
    B.free_up()
    assert pc.procs[11]["prio"] == BELOW


def test_a_game_that_never_starts_gives_everything_back_after_ten_minutes(pc):
    pc.add(20, "epicgameslauncher.exe")
    B.free_up()
    assert B.tick(None) == "", "Steam can take minutes to start the game"
    state = B._load()
    state["since"] -= B.RESTORE_IF_NO_GAME_S + 1
    B._save(state)
    assert "started Epic Games Launcher again" in B.tick(None)


def test_running_the_protocol_again_restarts_the_ten_minutes(pc):
    """Review 2026-11-20: saying it again at minute 9 still put everything back at minute 10, mid-load."""
    pc.add(20, "epicgameslauncher.exe")
    B.free_up()
    state = B._load()
    state["since"] -= B.RESTORE_IF_NO_GAME_S - 30
    B._save(state)
    pc.add(13, "claude.exe")
    B.free_up()
    state = B._load()
    state["since"] -= 60
    B._save(state)
    assert B.tick(None) == "" and B.active()


def test_a_jarvis_restart_mid_game_still_puts_everything_back(pc, monkeypatch):
    busy_pc(pc)
    B.free_up()
    B.tick("Apex Legends")
    for name, value in (("_last_sweep", 0.0), ("_not_heavy", set()), ("_no_game", 0)):
        monkeypatch.setattr(B, name, value)                            # a new Jarvis process: only the file remembers
    B.tick("Apex Legends")
    assert "back to its usual priority" in game_over(pc)


def test_after_a_reboot_nothing_is_started_again(pc):
    pc.add(20, "epicgameslauncher.exe")
    B.free_up()
    B.tick("Apex Legends")
    pc.booted = B._load()["since"] + 60                                 # the PC restarted during the game
    pc.add(50, "epicgameslauncher.exe", created=300.0)                 # Windows started it at sign-in
    said = game_over(pc)
    assert pc.launched == [] and "Windows already started everything again" in said and not B.active()


def test_a_launcher_he_already_reopened_is_not_started_twice(pc):
    pc.add(20, "epicgameslauncher.exe")
    B.free_up()
    pc.add(50, "epicgameslauncher.exe", created=300.0)
    B.tick("Apex Legends")
    assert "Epic Games Launcher was already open again" in game_over(pc) and pc.launched == []


def test_a_launch_that_fails_is_said(pc):
    pc.add(20, "epicgameslauncher.exe")
    B.free_up()
    pc.launch_ok = False
    B.tick("Apex Legends")
    said = game_over(pc)
    assert "couldn't start Epic Games Launcher again" in said and not B.active()


def test_a_launch_that_raises_never_starts_anything_twice(pc):
    """Review 2026-11-20: an exception left the state behind and the next check started the first app again."""
    pc.add(20, "epicgameslauncher.exe")
    pc.add(21, "onedrive.exe")
    B.free_up()
    B.tick("Apex Legends")
    pc.launch_raises = True
    said = game_over(pc)
    assert "couldn't start OneDrive and Epic Games Launcher again" in said and not B.active()
    for _ in range(B.NO_GAME_TICKS + 1):
        B.tick(None)
    assert pc.launched == [ONEDRIVE, EPIC]


@pytest.mark.parametrize("command,expected", [
    ('"C:\\Program Files\\Microsoft OneDrive\\OneDrive.exe" /background',
     ("C:\\Program Files\\Microsoft OneDrive\\OneDrive.exe", "/background")),
    ("C:\\Riot Games\\Riot Client\\RiotClientServices.exe --launch-background-mode",
     ("C:\\Riot Games\\Riot Client\\RiotClientServices.exe", "--launch-background-mode")),
    ('"C:\\G\\Grammarly.Desktop.exe"', ("C:\\G\\Grammarly.Desktop.exe", "")),
    ("", ("", "")),
])
def test_split_command(command, expected):
    assert B.split_command(command) == expected


# -- the protocol ------------------------------------------------------------------------------------------------------
def test_the_gaming_protocol_clears_the_background_first():
    assert P.GAMING["steps"][0] == {"do": "free_up"}
    assert P.step_text({"do": "free_up"}) == "clear the background for the game"
    assert "free_up" in P.PC_STEPS


def test_the_step_reports_what_it_did(pc, monkeypatch):
    from core import pcbridge
    from tools import gaming
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(gaming, "performance_check", lambda: "Other recorders: none. NVIDIA Instant Replay alone is enough.\nHeavy: none.")
    pc.add(13, "claude.exe")
    status, detail = P._free_up({"do": "free_up"}, {})
    assert status == P.DONE and detail.startswith("put Claude coding agents behind the game")
    monkeypatch.setattr(gaming, "performance_check",
                        lambda: "Other recorders: Windows Game Bar background recording is on. NVIDIA alone is enough.\nHeavy: none.")
    status, detail = P._free_up({"do": "free_up"}, {})
    assert status == P.DONE, "nothing heavy running is the usual good case, not a step that failed to happen"
    assert detail == ("nothing heavy was running in the background; "
                      "FPS note: also recording in the background: Windows Game Bar background recording is on")


def test_the_laptop_leaves_it_to_the_pc(pc, monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    pc.add(13, "claude.exe")
    assert P._free_up({"do": "free_up"}, {}) == (P.SKIPPED, "clearing the background happens on the PC")
    assert pc.procs[13]["prio"] == NORMAL


def test_stop_the_gaming_protocol_puts_everything_back(pc):
    pc.add(13, "claude.exe")
    B.free_up()
    said = P.handle("stop the gaming protocol")
    assert said.startswith("Putting everything back: background work is back to its usual priority")
    assert pc.procs[13]["prio"] == NORMAL and not B.active()
    assert P.handle("stop the gaming protocol") == "The gaming protocol isn't running, so there's nothing to stop."


def test_stop_from_the_phone_asks_the_pc(pc, monkeypatch):
    """Review 2026-11-20: the laptop looked in its own (empty) state and said there was nothing to stop."""
    from core import pcbridge, pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    asked = []
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: asked.append(raw) or "Putting everything back: OneDrive again.")
    assert P.handle("stop the gaming protocol") == "Putting everything back: OneDrive again."
    assert asked == ["stop the gaming protocol"]


# -- review round 2 ------------------------------------------------------------------------------------------------
def test_the_row_is_on_disk_before_the_launcher_is_closed(pc):
    seen = []
    real_end = pc.end

    def end(pid, created):
        seen.append([c["name"] for c in (B._load() or {}).get("closed", [])])
        return real_end(pid, created)
    pc.end = end
    pc.add(20, "epicgameslauncher.exe")
    B.free_up()
    assert seen == [["Epic Games Launcher"]], "a hard crash during the close must still find it written down"


def test_every_instance_is_tried_and_a_half_closed_launcher_is_recorded(pc):
    pc.add(20, "epicgameslauncher.exe")
    pc.add(21, "epicgameslauncher.exe", created=150.0)
    calls = []

    def end(pid, created):
        calls.append(pid)
        if pid == 20:
            del pc.procs[20]
            return True
        return False                                                   # the second one would not close
    pc.end = end
    changed, text = B.free_up()
    assert calls == [20, 21], "the second instance is tried too"
    assert "Epic Games Launcher (it didn't close)" in text and not B.active(), "one instance still runs: nothing to restart"


def test_a_stop_while_the_background_is_being_cleared_waits(pc, monkeypatch):
    pc.add(13, "claude.exe")
    B.free_up()
    monkeypatch.setattr(B, "BUSY_WAIT_S", 0.05)
    with B._busy:                                                      # free_up is still closing launchers
        assert B.restore() == "I'm still clearing the background; say it again in a minute."
        assert B.tick(None) == "" and B.active()
        state = B._load()
        state["since"] -= B.RESTORE_IF_NO_GAME_S + 1
        B._save(state)
        assert B.tick(None) == "", "never put back halfway through a clear"
    assert "back to its usual priority" in B.tick(None)


def test_state_from_before_a_restart_is_dropped_by_the_next_run(pc):
    pc.add(20, "epicgameslauncher.exe")
    B.free_up()
    assert pc.ended == ["epicgameslauncher.exe"]
    pc.booted = 5000.0                                                 # the PC restarted; Windows started Epic again
    pc.add(50, "epicgameslauncher.exe", created=6000.0)
    B.free_up()
    assert pc.ended == ["epicgameslauncher.exe", "epicgameslauncher.exe"], "the old record did not make it skip the new one"
    state = B._load()
    assert [c["name"] for c in state["closed"]] == ["Epic Games Launcher"] and state["boot"] == 5000.0, "recorded once, fresh"


def test_the_start_command_is_read_with_variables_expanded(monkeypatch):
    import winreg

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: Key())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (r"%JB_TEST_DIR%\OneDrive.exe /background", 2))
    monkeypatch.setenv("JB_TEST_DIR", r"C:\Users\x\AppData\Local\Microsoft\OneDrive")
    assert B._System().run_command("OneDrive") == r"C:\Users\x\AppData\Local\Microsoft\OneDrive\OneDrive.exe /background"
