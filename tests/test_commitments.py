"""Partner stage 2 (docs/plans/partner-roadmap.md): goals, commitments, the morning report, approval-only proposals.

Rules tested: a proposed goal becomes active only on his explicit word, never on silence; a commitment exists only where a
real action started (a mission, a studio note, a one-off reminder) and resolves from that record; one with no result after
its window is reported as NOT done, never dropped; the report is built in code, once a day, never 22:00-07:00, never during a
game, never to an empty room; each machine writes only its own file and readers merge both."""
import json
import time
from datetime import datetime, timedelta

import pytest

from core import commitments as C
from core import missions, quick, storage, users

MORNING = datetime(2026, 11, 22, 8, 30)
HERE = {"present": True, "gaming": "", "idle": 5.0}


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MISSIONS_DIR", str(tmp_path / "missions"))
    monkeypatch.setattr(C, "_machine", lambda: "pc")
    monkeypatch.setattr(C, "_retry_after", {})
    users.set_current("owner")
    from core import habits
    monkeypatch.setattr(habits, "_turns_since", lambda t: False)
    yield
    users.set_current("owner")


_SEED = [
    {"gid": "seed-insurance", "text": "Grow the insurance business and its website",
     "spoken": "the insurance business", "aliases": ["insurance"]},
    {"gid": "seed-degree", "text": "Finish the IT degree", "spoken": "your IT degree",
     "aliases": ["degree", "it degree"]},
    {"gid": "seed-fitness", "text": "Fitness: the 12-week AFT program, the program",
     "spoken": "the Captain America program", "aliases": ["fitness", "captain america", "aft"]},
    {"gid": "seed-creole", "text": "Learn Haitian Creole", "spoken": "learning Creole",
     "aliases": ["creole", "kreyol", "haitian creole", "haitian"]},
    {"gid": "seed-family", "text": "Family", "spoken": "family",
     "aliases": ["family"]},
    {"gid": "seed-game", "text": "The game (on hold by your decision)", "spoken": "the game, which is on hold",
     "aliases": ["game", "the game"], "why": "On hold by his own decision (2026-11-19)."},
]


@pytest.fixture(autouse=True)
def _seed(monkeypatch):
    monkeypatch.setattr(C, "SEED", _SEED)



def turn_happened(monkeypatch):
    from core import habits
    monkeypatch.setattr(habits, "_turns_since", lambda t: True)


def say(text):
    return C.handle(text)


# -- 2.1 goals ---------------------------------------------------------------------------------------------------------
def test_he_states_a_goal_and_it_is_kept_with_why_and_next_step():
    reply = say("My goal is to get the insurance website live because it brings in clients")
    assert "New goal" in reply and "get the insurance website live" in reply
    (g,) = C.goals(("active",))
    assert g["goal"] == "get the insurance website live" and g["why"] == "it brings in clients"
    assert say("The next step is call the web designer") == "Noted. Next step for get the insurance website live: call the web designer."
    assert C.goals()[0]["next_step"] == "call the web designer"
    listing = say("What are my goals?")
    assert "1 goal" in listing and "next step call the web designer" in listing


def test_a_goal_survives_a_restart_it_is_on_disk():
    say("Add a goal: learn to swim")
    path = storage.user_dir("owner") / "goals-pc.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [g["goal"] for g in data["goals"].values()] == ["learn to swim"]


def test_mark_done_and_drop_by_name():
    say("New goal: run a 5k")
    say("New goal: read twelve books")
    assert say("Mark the 5k goal as done").startswith("Well done. I've marked run a 5k as done.")
    assert "dropped read twelve books" in say("Drop the books goal")
    assert "back as one of your goals" in say("Bring back the books goal")
    say("Drop the books goal")
    assert C.goals(("active",)) == []
    assert "haven't given me any goals" in say("my goals")


@pytest.mark.parametrize("line", [
    "The goal of this function is to parse the log",
    "What's the goal of the game?",
    "My goal in this game is to win the match",
    "My goal is to win this round",
    "Our goal is to finish by Friday",
    "Mark it as done",
    "Add it",
    "All of them",
    "Yes",
    "What are you working on?",
    "Are you working on it?",
    "What's the status on the thing that you were just working on?",
    "Drop it",
    "Add milk to the shopping list",
    "The next step is to restart the router",
])
def test_lines_that_are_not_goal_commands_are_left_to_the_model(line):
    assert say(line) is None


def test_quick_handle_routes_goal_lines():
    assert "New goal" in quick.handle("Jarvis, my goal is to save five thousand dollars")
    assert "1 goal" in quick.handle("[Sent from their phone] what are my goals")


def test_machines_write_their_own_file_and_readers_merge_newest(monkeypatch):
    monkeypatch.setattr(C, "_machine", lambda: "server")
    say("New goal: fix the gutter")                                  # from the phone, on the laptop
    monkeypatch.setattr(C, "_machine", lambda: "pc")
    g = C.goals()[0]
    assert g["goal"] == "fix the gutter"
    say("Mark the gutter goal done")                                 # on the PC: a full copy in the PC's file
    assert not (storage.user_dir("owner") / "goals-server.json").read_text(encoding="utf-8").count('"done"')
    assert C.goals()[0]["state"] == "done"


# -- 2.4 + seed: proposals are approval-only --------------------------------------------------------------------------
def test_the_seed_is_proposed_only_and_idempotent():
    added = C.seed_owner()
    assert len(added) == 6 and C.goals(("active",)) == []
    assert {g["state"] for g in C.goals()} == {"proposed"}
    assert C.seed_owner() == []                                       # twice: nothing new
    say("the game one is not a goal")                                 # he drops one...
    assert C.seed_owner() == []                                       # ...and the seed never brings it back
    assert C.seed_owner(dry_run=True) == []


def test_the_seed_dry_run_writes_nothing():
    assert len(C.seed_owner(dry_run=True)) == 6
    assert C.goals() == []


def test_first_report_asks_which_goals_are_real_and_silence_never_activates():
    C.seed_owner()
    text = C.morning_report(MORNING)
    assert "I think these are your goals" in text and "Tell me which are real" in text
    assert "the insurance business" in text and "and 3 more" in text      # never more than 3-4 names read aloud
    later = [C.morning_report(MORNING + timedelta(days=day)) for day in range(1, 5)]   # days pass, nobody answers
    assert C.goals(("active",)) == []
    assert all("I think these are your goals" in t for t in later[:2]) and later[2:] == ["", ""]   # asked 3 mornings, then quiet
    assert "I think these are your goals" in say("what are my goals")                              # still there when he asks


def test_he_confirms_one_by_name_then_hears_the_rest():
    C.seed_owner()
    C.morning_report(MORNING)
    assert say("Yes, the insurance one is a goal").startswith("Good, the insurance business is a goal now")
    others = say("What are the other goals?")
    assert "family" in others and "the game" in others
    assert say("The Creole one is real").startswith("Good, learning Creole is a goal now")
    active = {g["id"] for g in C.goals(("active",))}
    assert active == {"seed-insurance", "seed-creole"}


def test_all_of_them_only_answers_a_fresh_proposal(monkeypatch):
    C.seed_owner()
    assert say("All of them") is None                                  # nothing was proposed just now
    C.morning_report(MORNING)
    turn_happened(monkeypatch)
    assert say("All of them") is None                                  # he talked about something else since
    monkeypatch.undo()


def test_all_of_them_after_the_report_activates_every_proposal(monkeypatch):
    C.seed_owner()
    C.morning_report(MORNING)
    assert "those 3 are goals now" in say("Yes, all of them")
    assert {g["id"] for g in C.goals(("active",))} == {"seed-insurance", "seed-degree", "seed-fitness"}


def test_add_it_with_several_proposals_asks_which():
    C.seed_owner()
    C.morning_report(MORNING)
    assert say("Add it").startswith("Which one?")
    assert C.goals(("active",)) == []


def test_a_named_phrase_must_belong_to_one_goal():
    C.seed_owner()
    C.morning_report(MORNING)
    assert say("Add the insurance quote to my calendar") is None       # 'insurance' alone is not enough
    assert C.goals(("active",)) == []


# -- 2.2 commitments ---------------------------------------------------------------------------------------------------
def only(state=None):
    got = C.items()
    assert len(got) == 1
    return got[0]


def test_a_queued_mission_becomes_a_commitment_that_follows_its_state():
    m = missions.add("tidy my downloads folder")
    c = only()
    assert c["link"] == {"kind": "mission", "id": m["id"]} and c["state"] == "open" and c["what"] == "tidy my downloads folder"
    run = missions.claim_next("pc")
    C.resolve()
    assert only()["state"] == "in_progress"
    missions.finish(run["run"], "done", "tidied")
    C.resolve()
    assert only()["state"] == "done"
    assert "tidy my downloads folder" in C.morning_report(MORNING)
    assert only()["state"] == "reported" and only()["outcome"] == "done"
    assert "tidy my downloads" not in C.morning_report(MORNING + timedelta(days=1))   # said once


def test_a_failed_or_cancelled_mission_is_reported_as_not_done():
    a = missions.add("update the drivers")
    missions.finish(missions.claim_next("pc")["run"], "failed", "no network")
    b = missions.add("rename the photos")
    missions.cancel(b["id"])
    C.resolve()
    text = C.morning_report(MORNING)
    assert "Not done: update the drivers, it failed and rename the photos, you cancelled it." in text
    assert a and b


def test_a_mission_waiting_for_his_ok_is_said_as_waiting_on_him():
    m = missions.add("clean the temp files")
    run = missions.claim_next("pc")
    missions.park_for_approval(run["run"], {"tool": "delete_files"}, "delete 3 files")
    C.resolve()
    assert C.items()[0]["detail"] == "waiting for your OK on the phone"
    assert "Waiting on you: 1 approval on your phone." in C.morning_report(MORNING)
    assert m


def test_the_pc_leaves_a_laptop_mission_to_the_laptop(monkeypatch):
    monkeypatch.setattr(C, "_machine", lambda: "server")
    missions.add("back up the photos")
    monkeypatch.setattr(C, "_machine", lambda: "pc")
    C.resolve(time.time() + 10 * 86400)                               # long past its window, but not the PC's to judge
    assert C.items()[0]["state"] == "open"


def test_a_studio_note_resolves_from_its_work_order():
    from core import studio
    m = missions.add(f"{studio.PREFIX} the boss feels too slow")
    c = only()
    assert c["link"]["kind"] == "studio" and "the boss feels too slow" in c["what"]
    order = {"id": "WO-0001", "mission": m["id"], "state": "draft", "created": time.time()}
    studio.save(order)
    C.resolve()
    assert only()["detail"] == "waiting for your OK on the work order"
    studio.save({**order, "state": "red"})
    C.resolve()
    assert only()["state"] == "failed" and only()["detail"] == "NOT GREEN"


def test_a_reminder_is_owed_until_it_fires(monkeypatch):
    from tools import proactive
    due = datetime.now() + timedelta(minutes=5)
    proactive.set_reminder("call Marie", due.isoformat(timespec="minutes"), "none", False)
    c = only()
    assert c["link"]["kind"] == "reminder" and c["state"] == "open"
    proactive.due_reminders(due + timedelta(minutes=1))               # the scheduler pops it
    C.resolve(due.timestamp() + 60)
    assert only()["state"] == "done" and only()["detail"] == "reminded you"


def test_a_repeating_reminder_is_a_rule_not_a_commitment():
    from tools import proactive
    proactive.set_reminder("take the bins out", (datetime.now() + timedelta(hours=1)).isoformat(timespec="minutes"), "weekly", False)
    assert C.items() == []


def test_a_reminder_that_never_went_off_is_not_done(monkeypatch):
    from tools import proactive
    due = datetime.now() + timedelta(minutes=5)
    proactive.set_reminder("stretch", due.isoformat(timespec="minutes"), "none", False)
    C.resolve(due.timestamp() + 3600)
    assert only()["state"] == "failed" and only()["detail"] == "the reminder never went off"


def test_an_unlinked_commitment_past_its_window_is_reported_not_dropped(monkeypatch):
    C.record("send you the quote comparison")
    assert only()["state"] == "open"
    text = C.morning_report(datetime.fromtimestamp(time.time() + 2 * 86400))
    assert "Not done: send you the quote comparison, I haven't done it." in text
    assert only()["state"] == "reported" and only()["outcome"] == "failed"


def test_plain_chat_never_records_a_commitment():
    for line in ("I'll look into that for you", "My goal is to rest today", "What do you owe me?"):
        say(line)
    assert C.items() == []


def test_what_do_you_owe_me_lists_open_work_honestly():
    assert say("What do you owe me?").startswith("Nothing at the moment")
    missions.add("compare three insurance carriers")
    reply = say("What are you working on for me?")
    assert reply == "I owe you 1 thing: compare three insurance carriers: not started yet."


# -- 2.3 the morning report --------------------------------------------------------------------------------------------
def test_report_with_goals_reads_next_steps():
    say("My goal is to finish the IT degree")
    say("The next step is to register for spring classes")
    text = C.morning_report(MORNING)
    assert text == ("Your morning report. On your goals: finish the IT degree, next step register for spring classes.")
    say("New goal: pass the CompTIA exam")
    assert "next step register for spring classes; and pass the CompTIA exam, no next step yet." in say("what are my goals")


def test_nothing_to_say_means_silence():
    assert C.morning_report(MORNING) == ""


@pytest.mark.parametrize("when,sit", [
    (datetime(2026, 11, 22, 6, 59), HERE),
    (datetime(2026, 11, 22, 22, 0), HERE),
    (datetime(2026, 11, 22, 23, 30), HERE),
    (MORNING, {"present": True, "gaming": "Apex Legends"}),
    (MORNING, {"present": False, "gaming": ""}),
])
def test_never_in_quiet_hours_during_a_game_or_to_an_empty_room(when, sit):
    C.seed_owner()
    heard = []
    assert C.deliver_report(when, sit, deliver=heard.append) == "" and heard == []


def test_once_a_day():
    C.seed_owner()
    heard = []
    assert C.deliver_report(MORNING, HERE, deliver=heard.append)
    assert C.deliver_report(MORNING + timedelta(hours=2), HERE, deliver=heard.append) == ""
    assert C.deliver_report(MORNING + timedelta(days=1), HERE, deliver=heard.append)
    assert len(heard) == 2


def test_report_goes_through_attention(monkeypatch):
    from core import attention
    got = []
    monkeypatch.setattr(attention, "deliver", lambda text, urgency, key="", sit=None: got.append((text, key)) or "said")
    C.seed_owner()
    assert C.deliver_report(MORNING, HERE)
    assert got and got[0][1] == "partner-report:2026-11-22"


def test_a_held_report_cannot_be_answered_by_add_it(monkeypatch):
    from core import attention
    monkeypatch.setattr(attention, "deliver", lambda *a, **k: "held")
    C.seed_owner()
    C.deliver_report(MORNING, HERE)
    assert say("All of them") is None


# -- the scheduler ---------------------------------------------------------------------------------------------------
def test_scheduler_waits_for_the_briefing_and_keeps_quiet_hours(monkeypatch):
    from core import attention, scheduler
    said = []
    monkeypatch.setattr(attention, "deliver", lambda text, *a, **k: said.append(text) or "said")
    monkeypatch.setattr(scheduler, "ROLE", "all")
    C.seed_owner()
    s = scheduler.Scheduler(say=lambda t: None, ask=lambda p: None)
    s._sit = dict(HERE)
    s._briefed_at = time.time()
    assert s._partner_report(MORNING) == ""                           # the model's briefing is still being said
    s._briefed_at = 0
    assert s._partner_report(datetime(2026, 11, 22, 23, 0)) == ""
    assert "I think these are your goals" in s._partner_report(MORNING)
    assert len(said) == 1


def test_scheduler_report_is_pc_only(monkeypatch):
    from core import scheduler
    monkeypatch.setattr(scheduler, "ROLE", "server")
    C.seed_owner()
    s = scheduler.Scheduler(say=lambda t: None, ask=lambda p: None)
    s._sit = dict(HERE)
    assert s._partner_report(MORNING) == ""


def test_the_briefing_prompt_leaves_goals_to_the_report(monkeypatch):
    from core import scheduler
    from tools import gaming
    monkeypatch.setattr(scheduler, "ROLE", "all")
    monkeypatch.setattr(gaming, "game_mode", lambda: False)
    asked = []
    s = scheduler.Scheduler(say=lambda t: None, ask=asked.append)
    s.window = (0, 24)
    s._maybe_brief()
    assert asked and "Say nothing about goals" in asked[0]
    assert time.time() - s._briefed_at < 5


# -- review 2026-11-21 regressions -----------------------------------------------------------------------------------
def test_r1_all_of_them_activates_only_the_goals_he_heard():
    C.seed_owner()
    C.morning_report(MORNING)                                          # reads 3 of the 6
    say("All of them")
    assert {g["id"] for g in C.goals(("proposed",))} == {"seed-creole", "seed-family", "seed-game"}
    assert "The others" in say("the other goals")
    say("all of them")
    assert C.goals(("proposed",)) == []


@pytest.mark.parametrize("line", [
    "my goal is to build muscle", "my goal is fat loss, four days a week", "my goal is to pass the AFT",
    "my goal is 180 pounds", "my goal is glutes", "my goal is to lose weight",
    "set a goal weight of 180 pounds", "set a goal reminder for tomorrow", "add a goal to my calendar",
    "set a goal for the match", "record goal highlights", "save a goal for later",
])
def test_r2_trainer_goals_and_junk_are_not_life_goals(line):
    assert say(line) is None
    assert C.goals() == []


def test_r2_real_separators_still_add():
    assert "New goal: learn Python" in say("set a goal to learn Python")
    assert "New goal: open a second office" in say("add a goal: open a second office")
    assert "New goal: finish the IT degree" in say("My new goal is to finish the IT degree")


@pytest.mark.parametrize("line", [
    "cancel the game", "remove the website", "cancel my insurance", "forget about school", "cancel the training",
    "delete the family", "keep the game", "add the website", "yes the game", "drop the family",
])
def test_r3_ordinary_commands_never_touch_goals_after_a_listing(line):
    C.seed_owner()
    C.morning_report(MORNING)
    before = {g["id"]: g["state"] for g in C.goals()}
    say(line)
    assert {g["id"]: g["state"] for g in C.goals()} == before


def test_r3_the_x_one_needs_a_fresh_listing(monkeypatch):
    C.seed_owner()
    C.morning_report(MORNING)
    turn_happened(monkeypatch)
    assert say("drop the game one") is None and say("yes to the insurance one") is None
    assert all(g["state"] == "proposed" for g in C.goals())


def test_r3_a_drop_is_undoable():
    C.seed_owner()
    C.morning_report(MORNING)
    assert "Say 'bring back the insurance goal'" in say("drop the insurance goal")
    assert "back on the list I'm guessing at" in say("undo that")
    assert "seed-insurance" in {g["id"] for g in C.goals(("proposed",))}


@pytest.mark.parametrize("line", ["I hit my workout goal", "I finished my school goal", "I completed my fitness goal",
                                  "mark the fitness goal as done"])
def test_r4_a_proposed_goal_is_never_marked_done(line):
    C.seed_owner()
    say(line)
    assert C.goals(("done",)) == []


def test_r4_done_needs_an_active_goal_and_a_clear_phrase():
    say("New goal: run a 5k")
    assert say("I hit my 5k goal") is None
    assert "marked run a 5k as done" in say("the 5k goal is done")


def test_r5_a_held_report_marks_nothing_and_is_tried_again(monkeypatch):
    from core import attention
    verdicts = ["held", "said"]
    withdrawn = []
    monkeypatch.setattr(attention, "deliver", lambda *a, **k: verdicts.pop(0))
    monkeypatch.setattr(attention, "withdraw", lambda key: withdrawn.append(key) or True)
    missions.add("tidy my downloads folder")
    run = missions.claim_next("pc")
    missions.finish(run["run"], "done", "ok")
    assert C.deliver_report(MORNING, HERE) == ""
    assert withdrawn == ["partner-report:2026-11-22"]                 # never kept in attention's memory
    assert C.items()[0]["state"] == "done" and C._partner().get("report_day") is None
    assert C.deliver_report(MORNING, HERE) == ""                       # backing off
    C._retry_after.clear()
    assert "tidy my downloads folder" in C.deliver_report(MORNING, HERE)
    assert C.items()[0]["state"] == "reported" and C._partner()["report_day"] == "2026-11-22"


def test_r6_afternoon_wording_and_no_quiet_hours_flush(monkeypatch):
    say("New goal: run a 5k")
    heard = []
    assert C.deliver_report(datetime(2026, 11, 22, 15, 0), HERE, deliver=heard.append).startswith("Here's where things stand.")
    assert "morning" not in heard[0]
    from core import attention

    def holds(text, urgency="normal", key="", sit=None):
        attention._held.append({"text": text, "urgency": urgency, "key": key, "at": time.time()})
        return "held"
    attention._held.clear()
    monkeypatch.setattr(attention, "deliver", holds)
    C._retry_after.clear()
    C.deliver_report(MORNING + timedelta(days=1), HERE)
    assert attention._held == []                                       # nothing left to flush at 23:00


def test_r7_a_reminder_is_resolved_only_on_its_own_machine(monkeypatch):
    from tools import proactive
    monkeypatch.setattr(C, "_machine", lambda: "server")
    due = datetime.now() + timedelta(minutes=5)
    proactive.set_reminder("call Marie", due.isoformat(timespec="minutes"), "none", False)
    storage.save(proactive.REMINDERS, [])                              # as the other machine sees it before the sync
    monkeypatch.setattr(C, "_machine", lambda: "pc")
    C.resolve(due.timestamp() + 60)
    assert C.items()[0]["state"] == "open"


def test_r8_a_studio_note_without_a_work_order_is_never_done():
    from core import studio
    missions.add(f"{studio.PREFIX} the jump feels floaty")
    missions.finish(missions.claim_next("pc")["run"], "done", "finished")
    C.resolve()
    assert C.items()[0]["state"] == "failed" and C.items()[0]["detail"] == "no work order was written for it"


def test_r10_bad_synced_rows_are_skipped():
    say("New goal: run a 5k")
    path = storage.user_dir("owner") / "goals-server.json"
    path.write_text(json.dumps({"v": 1, "goals": {"bad1": {"goal": "x", "state": "active", "updated": "soon", "created": 1},
                                                  "bad2": "nope", "bad3": {"goal": "y", "state": "active", "created": [1]}}}),
                    encoding="utf-8")
    (storage.user_dir("owner") / "commitments-server.json").write_text(json.dumps(
        {"v": 1, "items": {"c1": {"what": "z", "state": "open", "due": "tomorrow", "created": 1}}}), encoding="utf-8")
    assert [g["goal"] for g in C.goals()] == ["run a 5k"]
    assert C.items() == [] and C.resolve() == []
    assert "1 goal" in say("what are my goals")
