import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["JARVIS_SKILLS_ON"] = "all"   # tests load every skill module, including the opt-in ones


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    """Every test gets an empty data folder, so real memories/wardrobe are never touched."""
    from core import storage

    from core import users

    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setenv("JARVIS_PC_ROOT", str(tmp_path / "jarvis_pc"))   # never the owner's real learned skills in C:\\Jarvis
    try:
        from tools import pcskills
        monkeypatch.setattr(pcskills, "_skills", None)
    except Exception:
        pass
    companion_data = tmp_path / "companion"
    companion_data.mkdir(exist_ok=True)
    monkeypatch.setenv("JARVIS_COMPANION_DATA_DIR", str(companion_data))
    # The memory vault lives OUTSIDE storage.DATA (JARVIS_VAULT, else /mnt/jarvis-vault, else ROOT/vault —
    # the owner's real vault). Pin it to the temp folder too, so no test can ever read or delete real memories.
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    # PC plans awaiting review and grants live outside storage.DATA: a pending plan there was approved by the next sentence.
    monkeypatch.setenv("JARVIS_PC_STATE_DIR", str(tmp_path / "pc-control"))
    monkeypatch.setenv("JARVIS_OUTBOX_DIR", str(tmp_path / "outbox"))          # the PC's notification outbox is never the real one
    monkeypatch.setenv("JARVIS_MISSIONS_DIR", str(tmp_path / "missions"))      # the real mission queue is never touched
    # core/studio: work orders, the game's repo and its worktrees are never the real ones (a test never creates a branch or
    # worktree in Living Shadow World, never starts a worker and never spends the API credit).
    monkeypatch.setenv("JARVIS_STUDIO_DIR", str(tmp_path / "studio"))
    monkeypatch.setenv("JARVIS_LSW_REPO", str(tmp_path / "no-lsw-repo"))
    monkeypatch.setenv("JARVIS_LSW_WORKTREES", str(tmp_path / "lsw-worktrees"))
    for _v in ("JARVIS_STUDIO_BASE", "JARVIS_STUDIO_CREDIT_CAP_USD", "JARVIS_STUDIO_ORDER_USD", "JARVIS_STUDIO_MODEL",
               "JARVIS_STUDIO_FIX_ROUNDS", "JARVIS_STUDIO_WORKER_S"):
        monkeypatch.delenv(_v, raising=False)
    # Finished rigs are copied to Documents\JARVIS Rigged Characters: never the owner's real Documents in a test.
    monkeypatch.setenv("JARVIS_RIGGED_DIR", str(tmp_path / "Documents" / "JARVIS Rigged Characters"))
    monkeypatch.delenv("JARVIS_SERVER_SSH", raising=False)  # tests never reach the real laptop
    monkeypatch.setenv("HOMECAM_URL", "http://127.0.0.1:9")  # nor the house cameras on the Pi (a closed port: refused at once)
    monkeypatch.delenv("HOMECAM_SHOW_TEST", raising=False)
    monkeypatch.delenv("HOMECAM_WEBRTC_URL", raising=False)
    for _v in ("JARVIS_VOICE_WARM", "JARVIS_PHONE_VOICE"):      # the phone server's voice switches (core/appserver)
        monkeypatch.delenv(_v, raising=False)
    for _v in ("JARVIS_ROLE", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN"):
        monkeypatch.delenv(_v, raising=False)  # tests always run against the local vault / brain, not the live server
    # The teacher's budget and limits come from .env (JARVIS_TEACHER_LOCAL_MAX=0 there skipped the local teacher, so the
    # learning-loop tests asked nobody). Tests that need a teacher setting set it themselves.
    for _v in [k for k in os.environ if k.startswith("JARVIS_TEACHER_")]:
        monkeypatch.delenv(_v, raising=False)
    # core.config loads the owner's real .env on import. Its brain-tier switches (local-only, local-first, which
    # local provider) must not decide what the suite tests: JARVIS_LOCAL_ONLY=1 in .env failed 8 cloud/brain tests.
    # JARVIS_GATEWAY=shadow in .env (live since PR 1) made every Brain.ask in a test start a background gateway decision on the
    # real local model: it held the inference lock and answered other tests' fakes (local_model_busy, extra requests).
    # Tests that exercise the gateway set it themselves.
    for _v in ("JARVIS_LOCAL_ONLY", "JARVIS_INTELLIGENCE", "JARVIS_LOCAL_FAST_PROVIDER", "JARVIS_LOCAL_VISION_MODEL", "JARVIS_GATEWAY",
               "JARVIS_VISION_MODEL", "JARVIS_ENG_VISION_MODEL", "JARVIS_LEARN_MODEL",   # .env names the one brain for these
               # the API brain (core.cloudmodel, owner 2026-11-21): a test never calls the paid API because of .env
               "JARVIS_CLOUD_FALLBACK", "JARVIS_CLOUD_HARD", "JARVIS_CLOUD_PLAN", "JARVIS_CLOUD_MODEL", "JARVIS_CLOUD_EFFORT"):
        monkeypatch.delenv(_v, raising=False)
    # Voice-turn switches (audio/endturn, audio/bargein): a test sees the defaults, never the owner's .env choice.
    # JARVIS_CONV_SUMMARY: the running conversation summary (core/convsummary), on by default.
    for _v in ("JARVIS_SMART_TURN", "JARVIS_BARGE_IN_VOICE", "JARVIS_CONV_SUMMARY", "JARVIS_LEARNED_FIRST"):
        monkeypatch.delenv(_v, raising=False)
    # JARVIS_SYNC_WATCH: core/syncwatch starts the PC's Syncthing task when it is gone; a test never sees the .env choice.
    # (The thread ledger, core/thread.py, lives in data/users/<uid>/thread-<machine>.jsonl: under storage.DATA above.)
    monkeypatch.setenv("JARVIS_SYNC_WATCH", "on")   # shared repo ships it off; tests cover it on
    # JARVIS_PARTNER_REPORT: the goals morning report + commitment checks (core/commitments, the scheduler); default on.
    monkeypatch.delenv("JARVIS_PARTNER_REPORT", raising=False)
    # JARVIS_THREAD=off (the acceptance harness) turns the thread ledger off: a test sees the default. The PC's fetched
    # copy of the laptop's rows (core.thread._pulled) is per process: never carried from one test into another.
    monkeypatch.delenv("JARVIS_THREAD", raising=False)
    # core/protocols: a test never opens, closes or changes anything on the real PC through a protocol step ("gaming
    # protocol" would launch Apex). Tests that run steps install their own fakes; JARVIS_PROTOCOLS is the .env switch.
    monkeypatch.delenv("JARVIS_PROTOCOLS", raising=False)
    from core import protocols as _protocols

    def _no_real_pc(step, ctx):
        raise RuntimeError("a test reached a real PC action through a protocol step")
    monkeypatch.setattr(_protocols, "ACTIONS", {k: _no_real_pc for k in _protocols.ACTIONS})
    # core/gameboost (the Gaming protocol's first step and the game watcher's tick): a test never lowers a real
    # process's priority, closes a real launcher or starts one; tests that exercise it install a fake system.
    from core import gameboost as _gameboost

    class _NoRealBoost:
        def __getattr__(self, name):
            raise RuntimeError(f"a test reached the real PC through core.gameboost ({name})")
    monkeypatch.setattr(_gameboost, "OS", _NoRealBoost())
    monkeypatch.setattr(_gameboost, "_not_heavy", set())
    # core/obsstream: a test never reaches the real OBS (it could start a real broadcast); tests that drive it run a fake
    # OBS server and point these at it.
    from core import obsstream as _obsstream
    monkeypatch.setattr(_obsstream, "obs_running", lambda: False)
    monkeypatch.setattr(_obsstream, "settings", lambda: {"enabled": False, "port": 9, "password": ""})
    # Storm Watch (core/stormwatch): a test never fetches the live NHC / USGS / NWS feeds (tests pass recorded samples as
    # `get`), never reuses another test's check, and never sees the owner's JARVIS_STORMWATCH switch.
    monkeypatch.setenv("JARVIS_STORMWATCH", "on")   # shared repo ships it off; tests cover it on
    # core/habits: no test reads the real windows or carries the "what was open" memory into the next test.
    monkeypatch.delenv("JARVIS_HABITS", raising=False)
    monkeypatch.delenv("JARVIS_TEST_TRAFFIC", raising=False)   # core.turntrace.test_traffic: a test sees the default
    # The Brain's "sir" on one reply in three (speech.Polisher._thin_title) is tested on its own (tests/test_partner_character.py):
    # the conversation tests compare every word of a reply as the model wrote it.
    import brain.llm as _llm
    monkeypatch.setattr(_llm, "_THIN_TITLE", False)
    from core import hud as _hud
    monkeypatch.setattr(_hud, "in_front", lambda timeout=2.0: None)   # never the real desktop's front window ("cannot tell")
    from core import habits as _habits
    monkeypatch.setattr(_habits, "_prev_apps", None)
    monkeypatch.setattr(_habits, "_last_heartbeat", "")
    monkeypatch.setattr(_habits, "_first_look", 0.0)
    monkeypatch.setattr(_habits, "_logged_day", "")
    monkeypatch.setattr(_habits, "_logged_today", set())
    monkeypatch.setattr(_habits, "_turn_started", {})
    from core import stormwatch as _stormwatch

    def _no_live_feed(url, accept="application/json", timeout=15.0):
        raise RuntimeError("a test reached a live Storm Watch feed")
    monkeypatch.setattr(_stormwatch, "_http", _no_live_feed)
    monkeypatch.setattr(_stormwatch, "_snapshot", {})
    monkeypatch.setattr(_stormwatch, "_text_cache", {})
    monkeypatch.setattr(_protocols, "_last_outside", 0.0)
    monkeypatch.setattr(_protocols, "_storm_tries", {})
    from core import thread
    for _state in ("_pulled", "_pull_started", "_forgot_at", "_owed"):
        monkeypatch.setattr(thread, _state, {})
    # Proactive noticing (core/notice): the default, never the owner's .env; its slots, hooks and offer live in memory and
    # never carry from one test into the next (its state file, notice-state-<machine>.json, is under storage.DATA above).
    monkeypatch.delenv("JARVIS_NOTICE", raising=False)
    from core import notice
    notice.reset()
    # The inference gate (core/inferencegate) holds background model work while a game runs: a test never asks the real
    # PC what is running (a game during a suite run would stall the journal tests), and no wake-word reservation leaks.
    from core import inferencegate
    monkeypatch.setattr(inferencegate, "GAMING", lambda: False)
    monkeypatch.setattr(inferencegate, "_reserved_until", 0.0)
    users.set_current("owner")
    return tmp_path


@pytest.fixture(autouse=True)
def no_desktop_popups(monkeypatch):
    """The suite runs on the owner's PC: no test may pop a browser tab or launch a file. Skills that open a page
    (wardrobe, lookbook, benefits, lesson pages...) call webbrowser / os.startfile — record those calls instead.
    A test that cares what was opened reads this list (or patches webbrowser itself, which still works)."""
    import webbrowser
    opened: list[str] = []
    for fn in ("open", "open_new", "open_new_tab"):
        monkeypatch.setattr(webbrowser, fn, lambda url, *a, **k: opened.append(str(url)) or True)
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda path, *a, **k: opened.append(str(path)))
    return opened


@pytest.fixture(autouse=True)
def no_real_music_search(monkeypatch):
    """Jarvis's workout DJ (tools/pca_music) never reaches YouTube in a test: the search backend finds nothing and the
    failure memory starts empty. A test that wants results replaces pca_music.search (or _backend) with a recorder."""
    from tools import pca_music
    monkeypatch.setattr(pca_music, "_backend", lambda query, n: [])
    monkeypatch.setattr(pca_music, "_failed", {})


@pytest.fixture(autouse=True)
def owner_at_the_desk(monkeypatch):
    """No test reads the owner's real moment: core.attention.situation looked at his live game and his real idle clock,
    so a gate run while he played Crimson Desert held a test's spoken reminder (2026-11-11). He is here and not gaming;
    a test about gaming or being away patches attention.situation itself."""
    from core import attention
    monkeypatch.setattr(attention, "situation", lambda: {"present": True, "gaming": "", "idle": 0.0})


@pytest.fixture(scope="session", autouse=True)
def tools_loaded():
    from tools import load_all

    load_all()


@pytest.fixture(autouse=True)
def _clear_approvals():
    from core import approvals, turnstate

    def clear():
        approvals._pending.clear()
        approvals._direct.clear()
        turnstate.reset()    # which question each exchange asked (in memory): never carried from one test into the next
        # ...and what core.turnstate.open_items reads as waiting on him: 'which one?', 'save it first?', a guide shown
        # (test_gateway_execute leaves a choice for "owner" behind), a demonstration
        from core import demonstrate, gateway, saveprompt
        from tools import pcskills
        gateway._CHOICES.clear()
        gateway._CHAT.clear()   # a sentence judged talk (core.pcfast notes it): its PC tools are withheld for 60 s
        saveprompt._PENDING.clear()
        pcskills._PENDING.clear()
        demonstrate._state.update(recorder=None, task="")
        demonstrate._state.pop("at", None)

    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def never_the_live_machine_state(tmp_path, monkeypatch):
    """No test may open the owner's live machine-state (ledger, missions, watchdog). The env overrides point at temp
    folders; this is the second lock: each resolver refuses its live default, so a test that deletes the override (or a
    harness that forgot it) fails loudly instead of writing into the live PC-control ledger (2026-11: 386 test
    StepTimeout rows demoted working methods)."""
    from core import missions, pcledger, watchdog
    from core.config import ROOT
    monkeypatch.setenv("JARVIS_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    live = (ROOT / "machine-state").resolve()

    def guarded(real):
        def resolve(*a, **k):
            path = real(*a, **k)
            p = Path(path).resolve()
            assert p != live and live not in p.parents, f"a test resolved the live machine-state path: {path}"
            return path
        return resolve

    monkeypatch.setattr(pcledger, "state_dir", guarded(pcledger.state_dir))
    monkeypatch.setattr(missions, "_dir", guarded(missions._dir))
    monkeypatch.setattr(watchdog, "_dir", guarded(watchdog._dir))


@pytest.fixture(autouse=True)
def no_real_store_apps(monkeypatch):
    """The Store-app catalogue asks Windows (Get-StartApps): tests use their own app lists, the same on every machine."""
    from tools import apps
    monkeypatch.setattr(apps, "_store_apps", lambda: {})
    real = apps._installed                     # a test may swap _installed for a plain fake: clear the real cache only
    real.cache_clear()
    yield
    real.cache_clear()


@pytest.fixture(autouse=True)
def no_real_procedure_notes(monkeypatch, tmp_path):
    """Procedure notes (core/pcnotes) live on the PC beside the ledger: tests write their own file, never the owner's.
    The screenshot judge (core/screenjudge) would grab the real screen and call the local vision model: off unless a
    test turns it on with its own fakes."""
    monkeypatch.setenv("JARVIS_PC_NOTES_FILE", str(tmp_path / "procedure-notes.jsonl"))
    monkeypatch.setenv("JARVIS_SCREEN_JUDGE", "off")


@pytest.fixture(autouse=True)
def no_real_icon_naming(monkeypatch):
    """Screen-reading tests fake the OCR; the icon detector would grab the REAL screen and call the local vision server.
    tests/test_mark3_icons.py tests it with its own fakes."""
    monkeypatch.setenv("JARVIS_UI_ICONS", "off")
    from jarvis_pc.uiact import uia
    monkeypatch.setattr(uia, "_last_read", {"key": None, "labels": []})   # no reading carried from one test into the next


@pytest.fixture(autouse=True)
def no_real_creole_voice(monkeypatch):
    """The Creole voice model (facebook/mms-tts-hat) is on the owner's PC: a test never loads it, and no test starts
    with another test's conversation language."""
    import sys

    monkeypatch.setenv("JARVIS_KREYOL_MMS", "off")
    if "audio.tts" in sys.modules:
        monkeypatch.setattr(sys.modules["audio.tts"], "_creole_ready", None)
    from audio import language

    language.reset()
    yield
    language.reset()


@pytest.fixture(autouse=True)
def no_real_pocket_voice(monkeypatch):
    """The Pocket TTS voice (JARVIS_VOICE=pocket:<voice> in .env, core.config reads it on import) is never loaded or
    run by a test: its model counts as not installed, and no cooldown or slowness count carries between tests."""
    from audio import pocketvoice

    monkeypatch.setattr(pocketvoice, "installed", lambda: False)
    monkeypatch.delenv("JARVIS_POCKET_THREADS", raising=False)
    pocketvoice.reset()
    yield
    pocketvoice.reset()


@pytest.fixture(autouse=True)
def _forget_process_caches():
    """Caches that live for Jarvis's whole run must not carry one test's fake map answers into the next."""
    from core import intelligence
    from tools import kitchen

    kitchen._HOME_SPOTS.clear()
    intelligence._last_local.clear()
    intelligence._lm_down_until = 0.0
    yield
    kitchen._HOME_SPOTS.clear()
    intelligence._last_local.clear()
    intelligence._lm_down_until = 0.0


@pytest.fixture(autouse=True)
def no_real_background_work(request, monkeypatch):
    """No test may start REAL heavy work by accident: a routing test whose phrase resolves to a rigging job once started a real character-rigging
    job on the live PC (2026-11-01, docs/learning/INCIDENTS.md). The real entry points raise unless the test patches them itself (the normal way)
    or is marked @pytest.mark.real_jobs."""
    if request.node.get_closest_marker("real_jobs"):
        return

    def _blocked(name):
        def _raise(*a, **k):
            raise AssertionError(f"a test tried to run real background work ({name}); patch it with a fake, or mark the test @pytest.mark.real_jobs")
        return _raise
    from core import dojo
    from tools import charjobs, makechar, rigging
    for mod, attr in ((rigging, "rig_character"), (rigging, "image_to_character"), (makechar, "_make"), (charjobs, "_spawn")):
        monkeypatch.setattr(mod, attr, _blocked(f"{getattr(mod, '__name__', mod)}.{attr}"))
    real_start = dojo.Practice.start

    def _practice_start(self):
        if self.runner is dojo.run_case or self.asker is dojo.ask_json:     # the real Blender runner / the real model: only a fake-injected session is allowed
            _blocked("dojo.Practice.start with the real runner or model")()
        return real_start(self)
    monkeypatch.setattr(dojo.Practice, "start", _practice_start)


@pytest.fixture(autouse=True)
def no_real_tryon_or_game_state(monkeypatch):
    """The wardrobe try-on must never read the owner's live game state, start its queue thread or render on the real GPU
    from a test. A test that exercises them puts its own fake in (monkeypatch wins over this)."""
    from tools import fitting, studio

    def _no_render(job):
        raise AssertionError("a test tried to render a real try-on; patch tools.fitting._render with a fake")
    monkeypatch.setattr(studio, "game_running", lambda: "")
    monkeypatch.setattr(fitting, "_render", _no_render)
    monkeypatch.setattr(fitting, "_ensure_worker", lambda: None)
    monkeypatch.setattr(fitting, "cutout_ready", lambda: False)      # no background-removal model run unless a test fakes one
    fitting._jobs.clear()
    yield
    fitting._jobs.clear()


@pytest.fixture(scope="session", autouse=True)
def _shared_skill_lab():
    """One stable AppContainer lab for every installed-skill test. A new lab path means a new Windows AppContainer profile (they accumulate
    under %LOCALAPPDATA%\\Packages), so the tests reuse this one instead of a fresh temp folder per test."""
    if "JARVIS_SKILLS_LAB" not in os.environ:
        os.environ["JARVIS_SKILLS_LAB"] = str(Path(os.environ.get("TEMP", "/tmp")) / "jarvis-skill-test-lab")
    yield


@pytest.fixture(autouse=True)
def pcfacts_reads_no_real_catalogue(monkeypatch):
    """core.pcfacts decides whether a name in "Is X open?" is an app from the catalogue (Start Menu, Steam, Store: PowerShell).
    No test does that: only Windows' built-ins, no installed apps (a test that needs more patches core.pcfacts._catalogue)."""
    from core import pcfacts
    from tools import apps
    monkeypatch.setattr(pcfacts, "_catalogue", lambda: set(apps.BUILTINS))


@pytest.fixture(autouse=True)
def _never_touch_the_real_model_server(monkeypatch, tmp_path_factory):
    """No test may start or kill the owner's real `ollama serve` (a gate run once could, via an unfaked request path)."""
    from core import intelligence

    def refuse(*a, **k):
        raise OSError("tests may not start or stop the real model server")

    monkeypatch.setattr(intelligence, "_spawn_ollama", refuse)
    monkeypatch.setattr(intelligence, "_kill_ollama", refuse)
    monkeypatch.setenv("JARVIS_OLLAMA_LOG_DIR", str(tmp_path_factory.mktemp("ollama-log")))
    monkeypatch.setattr(intelligence, "_log_checked_at", -1e9)


@pytest.fixture(autouse=True)
def no_real_lm_studio(monkeypatch):
    """No test may drive the owner's REAL LM Studio. Fakes that report a loaded LM Studio model made core/intelligence
    run the real `lms unload <model>` on his PC (unloading what live Jarvis was using), and those tests failed whenever
    the real CLI refused (2026-11-19: 9 test_local_vision failures while gemma was loaded). Calls to `lms` succeed as a
    no-op here and are recorded on subprocess.run.lms_calls; every other command runs as normal. A test that needs to
    see these calls patches subprocess.run itself, after this fixture."""
    import subprocess

    real_run = subprocess.run
    calls = []

    def run(args, *a, **kw):
        exe = os.path.basename(str(args[0] if isinstance(args, (list, tuple)) and args else args)).lower()
        if exe in ("lms", "lms.exe"):
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, stdout=b"" if not kw.get("text") else "", stderr=b"" if not kw.get("text") else "")
        return real_run(args, *a, **kw)

    run.lms_calls = calls
    monkeypatch.setattr(subprocess, "run", run)
    return calls
