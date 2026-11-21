"""Know what is in flight (upgrade 2, 2026-11-10): 'done', 'no', 'yes' and 'the second one' go to the open question, a bare no
declines what waits, and a yes counts only for the question he was just asked.

Live 2026-11-10: "Jarvis, watch me save this on Blender." -> "Okay, I'm watching. Say 'done' when you've finished." -> "Done." ->
"Of course, sir." and the mic slept (main's dismiss filter took 'done'). A bare "no" to a plan ended the conversation and left
the plan approvable for 300 s. No audio and no model here: the meaning model, quick answers and the PC are faked."""
import pytest

import main
from core import approvals, demonstrate, pcbridge, pcfast, pcledger, quick, turnstate, users

PLAN = {"kind": "plan", "id": "a" * 32, "question": "type hello in Notepad", "t": None}
DEMO = {"kind": "demonstrate", "id": "demonstrate", "question": "Say 'done' when you've finished.", "t": None}
STEPS = {"steps": [{"tool": "use_app", "arguments": {"app": "notepad", "goal": 'type "hello"'}}]}


@pytest.fixture
def meaning_model(monkeypatch):
    """core.meaning.choose, faked: records every line it was asked about."""
    from core import meaning
    asked = []

    def choose(said, meanings, context="", **kw):
        asked.append(said)
        return "dismiss" if said == "done" else None
    monkeypatch.setattr(meaning, "choose", choose)
    return asked


# ------------------------------------------------------------------------------------------------ main.pre_brain_route
def test_done_reaches_the_demonstration_instead_of_the_dismiss_filter(meaning_model):
    assert main.pre_brain_route("done", [DEMO]) == "respond"
    assert main.pre_brain_route("i'm done", [DEMO]) == "respond"
    assert main.pre_brain_route("cancel", [DEMO]) == "respond", "the demonstration's own cancel word reaches it"
    assert meaning_model == [], "no meaning call while something is open"


def test_with_nothing_open_the_filters_are_exactly_as_before(meaning_model):
    assert main.pre_brain_route("done", []) == "dismiss" and meaning_model == ["done"]   # today's result, via meaning.choose
    assert main.pre_brain_route("no", []) == "dismiss"
    assert main.pre_brain_route("thanks", []) == "dismiss"
    assert main.pre_brain_route("stop", []) == "stop"
    assert main.pre_brain_route("goodbye", []) == "shutdown"
    assert main.pre_brain_route("open notepad", []) == "respond"


def test_a_bare_no_to_a_waiting_plan_is_decline(meaning_model):
    for said in ("no", "nope", "nah", "no thanks", "nah, leave it", "never mind", "no no"):
        assert main.pre_brain_route(said, [PLAN]) == "decline", said
    assert meaning_model == []


def test_short_lines_go_to_the_open_question(meaning_model):
    assert main.pre_brain_route("yes", [PLAN]) == "respond"
    assert main.pre_brain_route("sounds good", [PLAN]) == "respond"
    assert main.pre_brain_route("the second one", [{"kind": "choice", "id": "choice:1", "question": "Which one?"}]) == "respond"
    assert main.pre_brain_route("no", [{"kind": "question", "id": "question:3", "question": "Should I try it now?"}]) == "respond"
    assert meaning_model == [], "echo_free_meaning is skipped while items exist"


def test_shutdown_and_stop_words_still_work_while_something_waits(meaning_model):
    assert main.pre_brain_route("goodbye", [PLAN]) == "shutdown"
    assert main.pre_brain_route("stop", [PLAN]) == "stop"
    assert main.pre_brain_route("wait", [DEMO]) == "stop"
    # a long goodbye still ends the conversation
    assert main.pre_brain_route("thank you jarvis you can go to sleep for now", [PLAN]) == "dismiss"


QUESTION = {"kind": "question", "id": "question:3", "question": "Should I try it now?", "t": None}


@pytest.mark.parametrize("said", ["thanks", "thank you", "sa bon", "oke mèsi", "ça ira", "c'est tout", "that's it", "stand by",
                                  "nothing", "go to sleep", "you can go to sleep now", "that's all", "that will be all"])
def test_his_explicit_dismissals_still_end_the_conversation_while_something_is_open(said, meaning_model):
    """Review 2026-11-10: with anything open (a plan, Jarvis's own closing question for 300 s) every line of six words or fewer
    skipped DISMISS and GO_TO_SLEEP; 'Oke mèsi' then reached the meaning model as a yes to the waiting plan."""
    for items in ([PLAN], [QUESTION], [{"kind": "learning", "id": "learning:blender:0", "question": "blender"}]):
        assert main.pre_brain_route(said, items) == "dismiss", (said, items)
    assert meaning_model == []


@pytest.mark.parametrize("said", ["shut up", "be quiet", "hush", "quiet"])
def test_be_quiet_silences_him_while_something_is_open(said, meaning_model):
    assert main.pre_brain_route(said, [QUESTION]) == "stop"
    assert main.pre_brain_route(said, [PLAN]) == "stop"


def test_answers_to_the_open_question_still_reach_it(meaning_model):
    assert main.pre_brain_route("that's it", [DEMO]) == "respond", "the demonstration's own 'done'"
    assert main.pre_brain_route("no thanks", [QUESTION]) == "respond", "a no to 'Should I try it now?'"
    assert main.pre_brain_route("no thanks", [PLAN]) == "decline"
    assert meaning_model == []


def test_decline_declines_every_plan_and_approval_waiting(monkeypatch):
    declined = []
    monkeypatch.setattr(pcledger, "decline", lambda rid: declined.append(rid))
    approvals.request("owner", "delete:/tmp/x", "delete x (1 MB)")
    items = [PLAN, {"kind": "approval", "id": "approval:delete:/tmp/x", "question": "delete x (1 MB)"}]
    assert main.pre_brain_route("no", items) == "decline"
    assert turnstate.decline("owner", "No.", items) == "Okay, I won't."
    assert declined == [PLAN["id"]] and approvals.pending("owner") is None


def test_a_no_is_not_a_decline_when_it_answers_save_it_first():
    save = {"kind": "save", "id": "save:1", "question": "Save Notepad before I close it?"}
    assert turnstate.decline("owner", "no", [PLAN, save]) is None      # core.saveprompt: 'no' means close without saving
    assert turnstate.decline("owner", "no", [DEMO]) is None            # nothing to decline
    assert turnstate.decline("owner", "no thanks, I'll do it later", [PLAN]) is None


# ------------------------------------------------------------------------------------------------ open_items
def test_open_items_reads_every_store(monkeypatch):
    from core import gateway, saveprompt
    assert turnstate.open_items("owner") == [], "nothing waits"
    monkeypatch.setitem(demonstrate._state, "recorder", object())
    approvals.request("owner", "delete:/tmp/x", "delete x (1 MB)")
    rid = pcledger.create("plan", {"task": "type hello into notepad", "plan": STEPS, "route": "gateway", "cloud_usd": 0})
    monkeypatch.setitem(gateway._CHOICES, "owner", {"task": "open the file", "choices": ["a", "b"], "remaining": [], "t": 1e18})
    monkeypatch.setitem(saveprompt._PENDING, "owner", {"app": "notepad", "hwnd": 0, "t": 1e18})
    items = turnstate.open_items("owner")
    assert {i["kind"] for i in items} == {"demonstrate", "approval", "plan", "choice", "save"}
    plan = next(i for i in items if i["kind"] == "plan")
    assert plan["id"] == rid and "Notepad" in plan["question"]


# ------------------------------------------------------------------------------------------------ the Brain, end to end
@pytest.fixture
def brain(monkeypatch):
    """A Brain whose quick answers tell the time, whose PC layer is the real core.pcfast (gateway on) except for the
    sentence that puts a plan up for review, and whose PC control is a fake that records what would run."""
    from brain import llm
    from core import gateway, intelligence
    from tools import pcview
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(pcbridge, "require_owner", lambda *a, **k: "owner")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "original_message", lambda: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(quick, "handle", lambda text: "It's 3:00 PM." if "time" in text.lower() else None)
    ran = []
    monkeypatch.setattr(pcview, "control_pc", lambda task, *a, **k: ran.append(task) or [{"type": "text", "text": "Done in Notepad."}])
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: (_ for _ in ()).throw(
        AssertionError(f"the gateway was asked about {raw!r}")))
    real = pcfast.handle

    def handle(text, history=None, **kw):
        if text.startswith("type hello"):       # the gateway put a plan up for review (pchybrid needs_review)
            pcledger.create("plan", {"task": text, "plan": STEPS, "route": "gateway", "cloud_usd": 0})
            return "Here's my plan: type hello in Notepad. Want me to go ahead?"
        return real(text, history, **kw)
    monkeypatch.setattr(pcfast, "handle", handle)
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    b.ran = ran
    return b


def say(b, text):
    heard = []
    b.ask(text, heard.append)
    return " ".join(heard)


def test_a_yes_right_after_the_plan_runs_it(brain):
    say(brain, "type hello into notepad and save it")
    assert brain._last_question["kind"] == "plan"
    assert say(brain, "Yes.") == "Done in Notepad." and brain.ran == ["type hello into notepad and save it"]


def test_a_yes_after_another_exchange_is_asked_once_more_and_runs_nothing(brain):
    """'type hello into notepad and save it' (a plan for review), 'what time is it?', then 'yes': a re-ask, nothing runs."""
    say(brain, "type hello into notepad and save it")
    assert say(brain, "What time is it?") == "It's 3:00 PM." and brain._last_question is None
    reask = say(brain, "Yes.")
    assert reask.startswith("To be clear, sir:") and "notepad" in reask.lower() and reask.endswith("?")
    assert brain.ran == [] and pcledger.pending(), "nothing ran; the plan still waits for an answer to the re-ask"
    assert brain._last_question["kind"] == "plan", "the re-ask is now the question just asked"
    assert say(brain, "Yes.") == "Done in Notepad." and brain.ran == ["type hello into notepad and save it"]


def test_a_bare_no_declines_the_plan_and_says_so(brain):
    say(brain, "type hello into notepad and save it")
    assert say(brain, "No.") == "Okay, I won't."
    assert pcledger.pending() == [], "no later yes can approve it"
    assert brain.messages[-2:] == [{"role": "user", "content": "No."}, {"role": "assistant", "content": "Okay, I won't."}]
    say(brain, "Yes.")
    assert brain.ran == []


def test_a_new_request_never_approves_the_waiting_plan(brain, monkeypatch):
    """The 2026-11-10 night incident: a pending test plan was approved by 'Add eggs and milk to my shopping list.'"""
    from core import meaning
    monkeypatch.setattr(meaning, "choose", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked the model")))
    say(brain, "type hello into notepad and save it")
    assert approvals.meant("owner", "Add eggs and milk to my shopping list.") is None
    assert brain.ran == [] and pcledger.pending()


def test_a_reply_ending_on_a_real_question_is_the_open_question():
    turnstate.note_exchange("owner", [], [], "Got it, one step in Blender. Should I try it now?")
    items = turnstate.open_items("owner")
    assert [i["kind"] for i in items] == ["question"] and items[0]["question"] == "Should I try it now?"
    turnstate.note_exchange("owner", [], [], "It's on the kitchen page. Anything else?")      # a closing offer asks nothing
    assert turnstate.open_items("owner") == []


def test_a_stale_guide_is_reasked_by_its_goal(monkeypatch):
    from tools import pcskills
    monkeypatch.setitem(pcskills._PENDING, "owner", {"key": "pc-guide:abc", "run": lambda: "ran", "t": 1e18,
                                                      "summary": "Goal: set up ripgrep\nSteps (1): 1. [T0] winget install ripgrep"})
    items = turnstate.open_items("owner")
    turnstate.note_exchange("owner", [], items, "Here's the plan. Want me to go ahead?")
    turnstate.note_exchange("owner", items, items, "It's 3:00 PM.")
    assert approvals.meant("owner", "yes") == "reask"
    assert turnstate.reask("owner", "sir") == "To be clear, sir: set up ripgrep?"


def test_an_approval_asked_again_by_the_brain_is_the_question_just_asked(monkeypatch):
    """A brain-path approval (delete a file): stale after another exchange, fresh again once the brain asks it anew."""
    ticks = iter(range(1000, 100000, 2))
    monkeypatch.setattr(approvals.time, "monotonic", lambda: next(ticks))
    approvals.request("owner", "delete:/tmp/x", "delete x (1 MB)")
    first = turnstate.open_items("owner")
    turnstate.note_exchange("owner", [], first, "That deletes x for good. Delete x (1 MB)?")
    turnstate.note_exchange("owner", first, first, "It's 3:00 PM.")
    assert approvals.meant("owner", "yes") == "reask"
    assert turnstate.reask("owner") == "To be clear, sir: delete x (1 MB)?"
    turnstate.note_exchange("owner", first, first, "To be clear, sir: delete x (1 MB)?")
    approvals.on_user_message("owner", "yes")
    assert approvals.consume("owner", "delete:/tmp/x"), "the yes to the re-ask approves exactly that deletion"
    approvals.request("owner", "delete:/tmp/y", "delete y (2 MB)")
    turnstate.note_exchange("owner", first, turnstate.open_items("owner"), "Delete y (2 MB)?")
    assert approvals.meant("owner", "yes") == "yes"


# ------------------------------------------------------------------------------------------------ review 2026-11-10: re-asks
def _plan_asked():
    rid = pcledger.create("plan", {"task": "type hello into notepad", "plan": STEPS, "route": "gateway", "cloud_usd": 0})
    items = turnstate.open_items("owner")
    turnstate.note_exchange("owner", [], items, "Here's my plan: type hello in Notepad. Want me to go ahead?")
    return rid, items


def test_a_reply_asking_again_in_its_own_words_asks_the_plan_it_names():
    """'What's it going to type?' -> 'It'll type hello into Notepad, sir. Shall I go ahead?': his yes answers that, not a
    templated re-ask (review 2026-11-10: he had to say yes three times)."""
    rid, items = _plan_asked()
    turnstate.note_exchange("owner", items, items, "It'll type hello into Notepad, sir. Shall I go ahead?")
    assert approvals.meant("owner", "yes") == "yes" and turnstate.answering("owner")[1]["id"] == rid
    turnstate.note_exchange("owner", items, items, "I can look that up for you. Shall I?")      # names nothing of the plan
    state, item = turnstate.answering("owner")
    assert state == "fresh" and item["kind"] == "question", "his yes is for the lookup offer"
    assert pcfast._yes_runs("owner") == (None, None), "...and never runs the plan"


def test_an_approval_asked_again_in_words_is_the_question_just_asked(monkeypatch):
    approvals.request("owner", "delete:/tmp/x", "delete x (1 MB)")
    items = turnstate.open_items("owner")
    turnstate.note_exchange("owner", [], items, "Delete x (1 MB)?")
    turnstate.note_exchange("owner", items, items, "It's 3:00 PM.")
    turnstate.note_exchange("owner", items, items, "Just to confirm, delete x?")
    approvals.on_user_message("owner", "yes")
    assert approvals.consume("owner", "delete:/tmp/x")


def test_a_reask_he_never_heard_asks_nothing():
    """He cut in before 'To be clear, sir: ...?' was said (the Brain notes the exchange with nothing heard): his next yes is
    stale again, never a yes to a question he did not hear (review 2026-11-10)."""
    _rid, items = _plan_asked()
    turnstate.note_exchange("owner", items, items, "It's 3:00 PM.")
    assert approvals.meant("owner", "yes") == "reask"
    turnstate.reask("owner")
    turnstate.note_exchange("owner", items, [], "")
    assert approvals.meant("owner", "yes") == "reask"


def test_a_relayed_reask_is_answered_by_the_next_relayed_yes():
    """From the phone (core.pctasks -> pcfast.handle, no Brain exchange on the PC): the re-ask is the question just asked, so
    the next yes counts instead of being re-asked until the plan expires (review 2026-11-10)."""
    rid, items = _plan_asked()
    turnstate.note_exchange("owner", items, items, "It's 3:00 PM.")
    assert approvals.meant("owner", "yes") == "reask"
    turnstate.reask("owner")                                  # relayed: no exchange records it
    assert approvals.meant("owner", "yes") == "yes" and turnstate.answering("owner") == ("fresh", turnstate.answering("owner")[1])
    assert turnstate.answering("owner")[1]["id"] == rid


def test_a_relayed_reask_is_never_credited_to_another_exchange():
    _rid, items = _plan_asked()
    turnstate.note_exchange("owner", items, items, "It's 3:00 PM.")
    turnstate.reask("owner")                                  # relayed to the phone
    turnstate.note_exchange("owner", items, items, "It's 3:01 PM.")   # then 'what time is it?' by voice at the PC
    assert approvals.meant("owner", "yes") == "reask", "the voice exchange never asked the plan"


# ------------------------------------------------------------------------------------------------ review 2026-11-10: narration
@pytest.mark.parametrize("said", ["I'm going to go in and press save here... I'm going to click on save.",
                                  "Once I'm done clicking on Save, the window will close. Okay?"])
def test_his_narration_reaches_the_brain_without_pc_tools(monkeypatch, said):
    """Review 2026-11-10: narration was a chat Decision, but pcfast returned before gateway.execute, so note_chat never ran and
    the one brain was still offered close_app and control_computer for his own narration."""
    from core import gateway, intelligence
    from tools import registry
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    monkeypatch.setattr(pcbridge, "require_owner", lambda *a, **k: "owner")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "original_message", lambda: None)
    assert pcfast.handle(said) is None
    assert gateway.judged_chat(said)
    pc = [{"name": n} for n, t in registry._TOOLS.items() if t["fn"].__module__ in gateway.PC_TOOL_MODULES][:3]
    assert pc and intelligence._without_pc_tools_if_chat(said, pc + [{"name": "get_weather"}]) == [{"name": "get_weather"}]
    assert not gateway.judged_chat("close blender"), "only that very sentence was judged talk"
    assert intelligence._without_pc_tools_if_chat("close blender", pc) == pc


# ------------------------------------------------------------------------------------------------ review 2026-11-10: binding
def _quick_too(monkeypatch, answers):
    """quick.handle answers `answers` (a phrase in the line -> reply or a callable giving one) besides the time."""
    real = quick.handle

    def handle(text):
        for phrase, reply in answers.items():
            if phrase in text.lower():
                return reply() if callable(reply) else reply
        return real(text)
    monkeypatch.setattr(quick, "handle", handle)


def _asks_to_delete(monkeypatch, brain, more=None):
    """'Delete the file x.' asks approval A, as a brain-path skill does (tools/homelab); the model turn after it deletes x
    only when A was approved (approvals.consume)."""
    def ask():
        approvals.request("owner", "delete:/tmp/x", "delete x (1 MB)")
        return "That deletes x for good. Delete x (1 MB)?"
    _quick_too(monkeypatch, {"delete the file x": ask, **(more or {})})
    deleted = []

    def run(emit, start):
        ok = approvals.consume("owner", "delete:/tmp/x")
        if ok:
            deleted.append("x")
        emit("Deleted x." if ok else "I didn't delete it.")
    monkeypatch.setattr(brain, "_run", run)
    return deleted


def test_a_yes_answers_the_approval_just_asked_never_an_older_plan(brain, monkeypatch):
    """Plan P put up, then approval A asked, then 'Yes.': A is approved and carried out; P never runs on that yes."""
    deleted = _asks_to_delete(monkeypatch, brain)
    say(brain, "type hello into notepad and save it")
    assert say(brain, "Delete the file x.").endswith("Delete x (1 MB)?")
    assert say(brain, "Yes.") == "Deleted x."
    assert deleted == ["x"] and brain.ran == [], "the older Notepad plan never ran on the yes to the deletion"
    assert pcledger.pending(), "P still waits (stale now)"
    reask = say(brain, "Yes.")
    assert reask.startswith("To be clear, sir:") and "notepad" in reask.lower() and brain.ran == []


def test_a_yes_to_the_plan_just_asked_never_approves_an_older_approval(brain, monkeypatch):
    deleted = _asks_to_delete(monkeypatch, brain)
    say(brain, "Delete the file x.")
    say(brain, "type hello into notepad and save it")
    assert say(brain, "Yes.") == "Done in Notepad." and brain.ran == ["type hello into notepad and save it"]
    p = approvals.pending("owner")
    assert p is None or not p["approved"], "the yes to the plan approved nothing else"
    assert deleted == []


def test_a_reasked_approval_runs_the_approval_not_the_older_plan(brain, monkeypatch):
    """P asked, A asked, 'what time is it?', 'Yes.' -> the re-ask names A; the next 'Yes.' carries out A, never P."""
    deleted = _asks_to_delete(monkeypatch, brain)
    say(brain, "type hello into notepad and save it")
    say(brain, "Delete the file x.")
    say(brain, "What time is it?")
    assert say(brain, "Yes.") == "To be clear, sir: delete x (1 MB)?"
    assert say(brain, "Yes.") == "Deleted x." and deleted == ["x"] and brain.ran == []


def test_a_yes_to_a_fresh_plan_never_runs_a_stale_guide(brain, monkeypatch):
    from tools import pcskills
    guide_ran = []
    monkeypatch.setitem(pcskills._PENDING, "owner", {"key": "pc-guide:abc", "run": lambda: guide_ran.append(1) or "ran the guide",
                                                      "t": 1e18, "summary": "Goal: set up ripgrep\nSteps (1): 1. winget"})
    turnstate.note_exchange("owner", [], turnstate.open_items("owner"), "Here's the plan. Want me to go ahead?")
    say(brain, "What time is it?")
    say(brain, "type hello into notepad and save it")
    assert say(brain, "Yes.") == "Done in Notepad." and brain.ran == ["type hello into notepad and save it"]
    assert guide_ran == []


def test_a_guide_and_its_own_approval_are_one_question(monkeypatch):
    """A guide waits with an approval of the same key (tools/pcskills._two_phase): his yes approves exactly that guide."""
    from tools import pcskills
    monkeypatch.setitem(pcskills._PENDING, "owner", {"key": "pc-guide:abc", "run": lambda: "ran", "t": 1e18,
                                                      "summary": "Goal: set up ripgrep\nSteps (1): 1. winget"})
    approvals.request("owner", "pc-guide:abc", "Goal: set up ripgrep")
    items = turnstate.open_items("owner")
    assert [i["kind"] for i in items] == ["guide"]
    turnstate.note_exchange("owner", [], items, "Here's the plan. Want me to go ahead?")
    approvals.on_user_message("owner", "yes")
    assert approvals.consume("owner", "pc-guide:abc"), "the yes approves the guide's own approval"


def _weather_asks_its_own_question(monkeypatch, brain, reply):
    _quick_too(monkeypatch, {"weather": "It's 72 and sunny, sir. Do you want tomorrow's forecast too?"})
    model = []
    monkeypatch.setattr(brain, "_run", lambda emit, start: model.append(1) or emit(reply))
    from core import gateway
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: None)
    return model


def test_a_no_to_the_question_just_asked_never_declines_an_older_plan(brain, monkeypatch):
    """A plan waits; the weather answer asks a question of its own; 'No.' answers that question, not the plan."""
    model = _weather_asks_its_own_question(monkeypatch, brain, "Very well, sir.")
    say(brain, "type hello into notepad and save it")
    say(brain, "What's the weather?")
    assert say(brain, "No.") == "Very well, sir." and model == [1]
    assert pcledger.pending(), "the plan is not declined by a no to the forecast"


def test_a_yes_to_the_question_just_asked_is_no_reask_of_an_older_plan(brain, monkeypatch):
    model = _weather_asks_its_own_question(monkeypatch, brain, "Tomorrow is 75 and clear, sir.")
    say(brain, "type hello into notepad and save it")
    say(brain, "What's the weather?")
    assert say(brain, "Yes.") == "Tomorrow is 75 and clear, sir." and model == [1]
    assert brain.ran == [] and pcledger.pending()


def test_a_cancel_word_ends_the_demonstration_even_while_a_plan_waits(brain, monkeypatch):
    """'Watch me ...' while a plan waits, then 'Cancel.': the demonstration stops (its recorder is let go); the plan waits."""
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    stopped = []

    class Rec:
        listening = True

        def stop(self):
            stopped.append(1)
            return []
    monkeypatch.setattr(demonstrate, "Recorder", lambda: Rec())
    monkeypatch.setitem(demonstrate._state, "recorder", None)
    _quick_too(monkeypatch, {"watch me": lambda: demonstrate.handle("Watch me save this in Blender."),
                             "cancel": lambda: demonstrate.handle("Cancel.")})
    say(brain, "type hello into notepad and save it")
    assert say(brain, "Watch me save this in Blender.").startswith("Okay, I'm watching.")
    assert main.pre_brain_route("cancel", turnstate.open_items("owner", brain)) == "respond"
    assert say(brain, "Cancel.") == "Okay, I stopped watching. Nothing was saved."
    assert not demonstrate.recording() and stopped == [1]


def test_a_no_to_a_walkthrough_offer_is_the_offers_answer(brain, monkeypatch):
    """The latest exchange offered a guided walkthrough while a plan waited from before: 'No.' answers the offer."""
    import time as _time
    from core import guided_learning
    state = {}
    monkeypatch.setattr(guided_learning, "_state", lambda: dict(state))

    def offer():
        state.update(status="offered", at=_time.time(), topic="blender", index=0)
        return "I can walk you through Blender. Want to start?"

    def answer():
        state.clear()
        return "No problem."
    _quick_too(monkeypatch, {"help me with blender": offer, "no": lambda: answer() if state else None})
    say(brain, "type hello into notepad and save it")
    say(brain, "Help me with Blender.")
    assert say(brain, "No.") == "No problem." and pcledger.pending()
