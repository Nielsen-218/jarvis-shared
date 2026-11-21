"""The PC is off and his phone asks something that needs the model: Jarvis says so, promptly and in plain words.

Found 2026-11-19 by reading the path (the records hold no real case): the laptop's thinking runs on the PC, and with the
PC unreachable every tier failed in turn and the turn ended in "I lost my train of thought there, sir. Could you say it
once more?" That is not what happened, and saying it once more gets the same line."""
import time
from types import SimpleNamespace

import pytest

from core import intelligence, pcbridge, pchelp, storage, turntrace, users


@pytest.fixture(autouse=True)
def link(monkeypatch):
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example.ts.net")
    monkeypatch.setenv("JARVIS_PC_TOKEN", "token")
    monkeypatch.setattr(pcbridge, "_down", {"until": 0.0, "healthy_until": 0.0})
    monkeypatch.delenv("JARVIS_PC_WAKE_MAC", raising=False)
    intelligence.take_refused_promise()                           # an earlier test's note is not this turn's


def _laptop(monkeypatch, down=True):
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    if down:
        pcbridge._down["until"] = time.monotonic() + 25           # a hop found the PC silent a moment ago


def test_the_laptop_knows_when_its_brain_is_out_of_reach(monkeypatch):
    _laptop(monkeypatch, down=False)
    assert not pcbridge.brain_unreachable()
    pcbridge._down["until"] = time.monotonic() + 25
    assert pcbridge.brain_unreachable()
    pcbridge._down["until"] = time.monotonic() - 1                # a while ago: it is asked again, nothing is assumed
    assert not pcbridge.brain_unreachable()


def test_the_pc_itself_is_never_out_of_its_own_reach(monkeypatch):
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    pcbridge._down["until"] = time.monotonic() + 25
    assert not pcbridge.brain_unreachable()


def test_a_laptop_with_no_pc_link_thinks_for_itself(monkeypatch):
    _laptop(monkeypatch)
    monkeypatch.delenv("JARVIS_PC_URL")
    assert not pcbridge.brain_unreachable()


def test_with_the_pc_off_the_line_says_so(monkeypatch):
    _laptop(monkeypatch)
    line = pchelp.model_silent("sir")
    assert line.startswith("I can't reach the PC right now, sir")
    assert "train of thought" not in line and "say it once more" not in line
    assert "asleep" in line and "wake" not in line.lower()        # no way to wake it is set up: none is offered


def test_the_owner_is_offered_the_wake_that_works_without_the_model(monkeypatch):
    _laptop(monkeypatch)
    monkeypatch.setenv("JARVIS_PC_WAKE_MAC", "001122334455")
    assert "wake my PC" in pchelp.model_silent("sir")
    wife = users.add("Marie", {"en": "madame"}, "wife")
    with users.acting_as(wife):                                   # waking the PC is the owner's
        line = pchelp.model_silent("madame")
    assert line.startswith("I can't reach the PC right now, madame") and "wake my PC" not in line


def test_the_offered_words_do_wake_it_through_the_laptop(monkeypatch):
    from core import pcfast, pcwake
    _laptop(monkeypatch)
    monkeypatch.setenv("JARVIS_PC_WAKE_MAC", "001122334455")
    sent = []
    monkeypatch.setattr(pcwake, "wake", lambda: sent.append("packet") or "Wake packet sent.")
    monkeypatch.setattr(pcbridge, "run", lambda *a, **k: pytest.fail("asked the sleeping PC"))
    assert pcfast.handle("[Sent from their Jarvis phone app.] wake my PC") == "Wake packet sent."
    assert sent == ["packet"]


def test_a_reachable_pc_whose_model_said_nothing_keeps_the_old_line(monkeypatch):
    _laptop(monkeypatch, down=False)
    assert pchelp.model_silent("sir").startswith("I lost my train of thought")


def test_a_refused_promise_is_not_blamed_on_the_pc(monkeypatch):
    """The model DID answer (a promise the guard refused): the PC was reachable for that, whatever a later hop found."""
    _laptop(monkeypatch, down=False)
    intelligence.note_refused_promise(["I will let you know when it is ready."], "Make me a humanoid.")
    assert pchelp.model_silent("sir").startswith("I haven't started that, sir")


# -- through the Brain: every tier fails because the PC does not answer ---------------------------------------------------
@pytest.fixture
def brain(monkeypatch):
    from brain import llm
    from core import pcfast, quick
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_ONLY", "1")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(llm.Brain, "_language_request", lambda self, text, emit: False)
    tries = []

    def never_answers(address, timeout):
        tries.append(timeout)
        raise OSError("timed out")
    monkeypatch.setattr(pcbridge.socket, "create_connection", never_answers)
    monkeypatch.setattr(intelligence, "complete_local", lambda *a, **k: pytest.fail("waited on the laptop's own CPU model"))
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.tries = tries
    return b


def test_a_phone_question_with_the_pc_off_is_answered_honestly_after_one_look(brain):
    said = []
    brain.ask("What is the capital of France?", said.append)
    reply = " ".join(said)
    assert reply.startswith("I can't reach the PC right now, sir"), reply
    assert "train of thought" not in reply and "Paris" not in reply
    with users.acting_as("owner"):
        assert reply == pchelp.pc_out_of_reach(users.title("owner"))      # the line whose audio is made at start
    assert brain.tries == [pcbridge._CONNECT_SECONDS]             # one look of a few seconds, not one per tier


def test_the_second_question_in_a_row_does_not_wait_again(brain):
    brain.ask("What is the capital of France?", [].append)
    said = []
    brain.ask("And of Spain?", said.append)
    assert " ".join(said).startswith("I can't reach the PC right now, sir")
    assert len(brain.tries) == 1                                  # remembered: no second look, so no second wait


def test_the_failed_looks_are_on_the_record(brain, tmp_path):
    since = time.time() - 0.01
    brain.ask("What is the capital of France?", [].append)
    parts = turntrace.breakdown(since)["relay"]["parts"]
    assert set(parts) == {"intelligence failed"}
    rows = storage.load("intelligence_usage-server.json", [])
    assert any(r["status"] == "pc_local_provider_unavailable" for r in rows)


# -- review round 1 ---------------------------------------------------------------------------------------------------
def test_after_a_step_already_ran_the_line_never_says_ask_me_again(monkeypatch):
    """The model picked a tool, the tool ran, then the PC dropped before the result could be worded: "ask me again"
    would have him repeat something that already happened."""
    _laptop(monkeypatch)
    monkeypatch.setenv("JARVIS_PC_WAKE_MAC", "001122334455")
    line = pchelp.model_silent("sir", ran=True)
    assert line.startswith("I can't reach the PC right now, sir")
    assert "ask me again" not in line and "check what happened" in line
    # a step that was only started, refused or failed leaves the same trace: the line claims no more than "started"
    assert "already started" in line and "ran" not in line.split()
    assert pchelp.model_silent("sir", ran=False) != line


def test_the_brain_says_so_when_a_tool_ran_before_the_pc_dropped(brain, monkeypatch):
    from anthropic.types import ToolUseBlock
    from tools import registry
    ran = []
    monkeypatch.setattr(registry, "run", lambda name, args: (ran.append(name) or "x" * 900 + "\nsecond line", False))
    monkeypatch.setattr(registry, "is_direct", lambda name: False)
    first = SimpleNamespace(content=[ToolUseBlock(type="tool_use", id="t1", name="add_to_list", input={"item": "eggs"})],
                            stop_reason="tool_use")
    real, calls = intelligence.request, []

    def request(role, system, history, tools, cancelled, **kw):
        calls.append(role)
        if len(calls) == 1:
            return first                                   # the PC answered: a tool is picked and runs on the laptop
        return real(role, system, history, tools, cancelled, **kw)      # then the PC no longer answers
    monkeypatch.setattr(intelligence, "request", request)
    said = []
    brain.ask("Add eggs to my shopping list.", said.append)
    reply = " ".join(said)
    assert ran == ["add_to_list"]
    assert reply.startswith("I can't reach the PC right now, sir") and "ask me again" not in reply, reply
    assert "already started" in reply


# -- finding 2: a slow background pull is not a sleeping PC -----------------------------------------------------------
class _Conn:
    def close(self):
        pass


def test_a_background_pull_that_times_out_does_not_mark_the_pc_asleep(monkeypatch):
    _laptop(monkeypatch, down=False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(pcbridge.socket, "create_connection", lambda address, timeout: _Conn())     # it is awake

    class Opener:
        def open(self, req, timeout):
            raise TimeoutError("timed out")                # ...but busy: the quick pull gave up after its 4 s
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(pcbridge.PCError):
        pcbridge.json_request("/api/pc/thread")
    assert pcbridge._down["until"] == 0.0 and not pcbridge.brain_unreachable()
    # a request he is waiting on that the PC never answers still marks it, as before
    with pytest.raises(pcbridge.PCError):
        pcbridge.json_request("/api/pc/status")
    assert pcbridge.brain_unreachable()


def test_a_background_pull_that_cannot_connect_at_all_still_marks_it(monkeypatch):
    _laptop(monkeypatch, down=False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")

    def refused(address, timeout):
        raise OSError("no route to host")
    monkeypatch.setattr(pcbridge.socket, "create_connection", refused)
    with pytest.raises(pcbridge.PCError):
        pcbridge.json_request("/api/pc/thread")
    assert pcbridge.brain_unreachable()


# -- review round 2 ---------------------------------------------------------------------------------------------------
def test_a_failed_background_pull_makes_the_next_request_look_before_it_waits(monkeypatch):
    """The pull's failure does not mark the PC asleep (a busy PC), but it must not leave "it answered a moment ago"
    standing either: a PC that really went off would then cost the next phone turn the whole wait of its request."""
    _laptop(monkeypatch, down=False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    pcbridge._down["healthy_until"] = time.monotonic() + 8      # a hop succeeded a moment ago
    looks = []

    class Opener:
        def open(self, req, timeout):
            raise TimeoutError("timed out")
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(pcbridge.socket, "create_connection", lambda address, timeout: looks.append(timeout) or _Conn())
    with pytest.raises(pcbridge.PCError):
        pcbridge.json_request("/api/pc/thread")                 # skipped the look (recently healthy), then failed
    assert looks == [] and pcbridge._down["until"] == 0.0
    assert pcbridge._down["healthy_until"] == 0.0               # ...so the next request looks first

    def gone(address, timeout):
        looks.append(timeout)
        raise OSError("timed out")
    monkeypatch.setattr(pcbridge.socket, "create_connection", gone)
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: pytest.fail("waited on the request itself"))
    with pytest.raises(pcbridge.PCError) as error:
        pcbridge.json_request("/api/pc/intelligence", "POST", {})
    assert error.value.unreachable and looks == [pcbridge._CONNECT_SECONDS]      # one look of a few seconds
    assert pcbridge.brain_unreachable()


# -- live check 2026-11-19 16:35: PC Jarvis restarting behind its Tailscale proxy ------------------------------------
# The PC itself takes the connection (the proxy answers), so the 3-second look succeeds; the proxy then answers 502
# (504 on its timeout) because Jarvis on the PC is not listening. The phone heard "I lost my train of thought" four
# times in a row during a 20-second restart. No route of Jarvis on the PC answers 502 or 504 itself: every 502 in
# core/pcbridge.py is raised on the laptop.
def _proxy_says(monkeypatch, code):
    import urllib.error
    _laptop(monkeypatch, down=False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(pcbridge.socket, "create_connection", lambda address, timeout: _Conn())

    class Opener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "proxy", {}, None)
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: Opener())


@pytest.mark.parametrize("code", [502, 504])
def test_the_proxy_saying_jarvis_is_not_running_counts_as_out_of_reach(monkeypatch, code):
    _proxy_says(monkeypatch, code)
    with pytest.raises(pcbridge.PCError) as error:
        pcbridge.json_request("/api/pc/intelligence", "POST", {})
    assert error.value.unreachable and pcbridge.brain_unreachable()
    assert pchelp.model_silent("sir").startswith("I can't reach the PC right now, sir")


@pytest.mark.parametrize("code", [503, 500, 404, 400])
def test_a_real_answer_from_jarvis_on_the_pc_is_not_out_of_reach(monkeypatch, code):
    """503 is the PC's own 'the model failed' (core/pcroutes /api/pc/intelligence): the PC is there."""
    _proxy_says(monkeypatch, code)
    with pytest.raises(pcbridge.PCError) as error:
        pcbridge.json_request("/api/pc/intelligence", "POST", {})
    assert not error.value.unreachable and not pcbridge.brain_unreachable()


def test_the_brain_says_the_pc_is_out_of_reach_while_its_jarvis_restarts(brain, monkeypatch):
    import urllib.error
    monkeypatch.setattr(pcbridge.socket, "create_connection", lambda address, timeout: _Conn())

    class Opener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: Opener())
    said = []
    brain.ask("What is the capital of France?", said.append)
    assert " ".join(said).startswith("I can't reach the PC right now, sir"), said
