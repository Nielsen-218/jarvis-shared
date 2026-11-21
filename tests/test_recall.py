"""Hybrid recall (core/recall.py): stems + relation aliases + names + recency over his facts, the journal and the
lessons; corrections kept as history, forgets erased (tools/memory.py). Every test runs on the sandboxed data folder
that tests/conftest.py gives it: never the owner's real memory."""
import json
import time
from datetime import date, timedelta

import pytest

from core import intelligence, journal, recall, storage, users
from tools import memory

def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _facts(*rows, household=False):
    """Facts written straight into the store, as an older version (or the other machine) left them."""
    out = [{"id": ("h" if household else "") + f"r{i:05d}", "fact": f, "category": c, "saved": s}
           for i, (f, c, s) in enumerate(rows)]
    storage.save(memory.HOUSEHOLD_FILE if household else memory.FILE, out)
    return out


# Realistic stores have dozens of unrelated facts; BM25's word weights only mean something next to them.
FILLER = [(f"Prefers {thing} for {meal}.", "preference", _days_ago(40)) for thing, meal in [
    ("oatmeal", "breakfast"), ("rice and beans", "lunch"), ("grilled fish", "dinner"), ("plantains", "sides"),
    ("black coffee", "mornings"), ("green tea", "evenings"), ("mango juice", "workouts"), ("popcorn", "movie night")]] + [
    ("Works night shifts on weekends.", "work", _days_ago(50)), ("His unit trains on Thursdays.", "work", _days_ago(50)),
    ("Plays Apex Legends most evenings.", "preference", _days_ago(45)), ("Wants to learn Blender rigging.", "goal", _days_ago(45)),
    ("Keeps his gym bag in the truck.", "other", _days_ago(45)), ("Likes Haitian music.", "preference", _days_ago(45)),
    ("Is saving for a new PC monitor.", "goal", _days_ago(45)), ("Drinks water before every run.", "health", _days_ago(45))]


def _lines(text):
    return recall.pick(text, users.current())


# -- aliases: "sibling" finds the sister ----------------------------------------------------------------------------------
def test_a_sibling_question_finds_the_sister_fact():
    _facts(("Their sister Marie lives in Miami.", "family", _days_ago(3)), *FILLER)
    assert "Marie" in memory.recall("sibling")                       # the tool: before, "Nothing remembered about 'sibling'"
    assert any("Marie" in line for line in _lines("Where does my sibling live?"))
    assert "Marie" in intelligence.relevant_memory("Where does my sibling live?")


def test_the_missus_is_his_wife_and_the_family_question_finds_his_people():
    _facts(("Their wife is Maya.", "family", _days_ago(10)), ("Their son is Noah.", "family", _days_ago(10)), *FILLER)
    assert "Maya" in memory.recall("the missus")
    family = intelligence.relevant_memory("What do you know about my family?")
    assert "Maya" in family and "Noah" in family and "oatmeal" not in family


def test_the_alias_table_does_not_over_match():
    _facts(("Their sister Marie lives in Miami.", "family", _days_ago(3)),
           ("Their brother Paul lives in Norfolk.", "family", _days_ago(3)),
           ("Their mom Rose cooks on Sundays.", "family", _days_ago(3)),
           ("Their dad Jean fixes cars.", "family", _days_ago(3)), *FILLER)
    sister = memory.recall("sister")
    assert "Marie" in sister and "Paul" not in sister                 # asked about one: not the other
    mom = memory.recall("mom")
    assert "Rose" in mom and "Jean" not in mom
    both = memory.recall("siblings")
    assert "Marie" in both and "Paul" in both and "Rose" not in both
    assert "Rose" in memory.recall("mother") and "Jean" in memory.recall("father")


def test_stems_and_names_rank_the_right_person_first():
    _facts(("Their sister Marie lives in Miami.", "family", _days_ago(60)),
           ("Their brother Paul lives in Norfolk.", "family", _days_ago(60)), *FILLER)
    hits = recall.search("Where is Marie living these days?", {"fact"})
    assert hits[0].doc.text == "Their sister Marie lives in Miami."   # "living" finds "lives"; the name decides


def test_old_substring_overlap_no_longer_matches_inside_words():
    _facts(("Their son is Noah.", "family", _days_ago(5)), ("Has a personal trainer named Max.", "health", _days_ago(5)),
           *FILLER)
    found = memory.recall("son")
    assert "Noah" in found and "trainer" not in found                 # "son" is not in "person"


# -- recency, budget, cut ------------------------------------------------------------------------------------------------
def test_a_recent_fact_beats_an_old_one_at_equal_bm25():
    _facts(("Likes the color blue.", "preference", _days_ago(200)), ("Likes the color green.", "preference", _days_ago(2)),
           *FILLER)
    hits = recall.search("favorite color", {"fact"})
    assert [h.doc.text for h in hits[:2]] == ["Likes the color green.", "Likes the color blue."]
    assert hits[0].score / hits[1].score == pytest.approx(1.14, abs=1e-6)      # recency scales a match, adds nothing
    assert memory.recall("color").splitlines()[0].endswith("Likes the color green.")


# His real lines are long, full of filler, and name several people ("a meal plan for me, my wife and my son").
FAMILY = [("Their wife Maya loves akasan and cornmeal porridge.", "family", _days_ago(60)),
          ("Their son Noah drinks powdered milk.", "family", _days_ago(60)),
          ("Their wife is Maya.", "family", _days_ago(60)),
          ("Is testing Jarvis on the PC this week.", "other", _days_ago(2)),
          ("Needs to renew his ID card next week.", "other", _days_ago(3)),
          ("Wants a meal plan app that builds the weekly plan for him.", "goal", _days_ago(2))]


def test_a_long_real_line_keeps_his_family_over_recent_notes_that_share_a_filler_word():
    """A flat +0.14 for recent notes outweighed a one-word match on a 7-word question: 'this week' in two recent notes
    beat his wife, and the 50% cut dropped her ("what kind of food does my wife like")."""
    _facts(*FAMILY, *FILLER)
    food = _lines("Jarvis, what kind of food does my wife like, I'm trying to plan dinner for this week")
    assert any("akasan" in line for line in food)
    plan = _lines("Build a meal plan for me, my wife and my son this week.")
    assert any("meal plan app" in line for line in plan)                         # the strongest match stays first...
    assert any("Noah" in line for line in plan) and any("Maya" in line for line in plan)   # ...and his people come too
    son = _lines("What's my son's name again? I need it for the school form this week.")
    assert son and "Noah" in son[0]


def test_the_phone_apps_channel_note_is_not_his_question():
    """From the phone, 'What's my wife's name?' arrived as '[Sent from their Jarvis phone app...] What's my wife's
    name?': the note's words outranked his wife, and a phone 'thanks' still ran recall."""
    note = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
    _facts(("Their wife is Maya.", "family", _days_ago(30)), ("Their sister is Marie.", "family", _days_ago(30)),
           ("Wants Jarvis to keep replies short.", "preference", _days_ago(2)),
           ("Paired the phone app again on the laptop.", "other", _days_ago(2)),
           ("Uses the Jarvis phone app at work.", "other", _days_ago(3)), *FILLER)
    journal.add_lesson("On the phone app, keep replies to two sentences.", "owner")
    pc = recall.for_turn("What's my wife's name?")
    assert pc and "Maya" in pc[0]
    assert recall.for_turn(note + "What's my wife's name?") == pc
    assert intelligence.relevant_memory(note + "What's my wife's name?") == intelligence.relevant_memory("What's my wife's name?")
    assert recall.for_turn(note + "thanks") == [] and intelligence.relevant_memory(note + "Thanks!") == ""


def test_the_still_open_label_is_shown_but_never_searched():
    """Journal docs were indexed with the words 'Still open:', so 'open' and 'still' matched every entry with open
    items: 'Is my Steam open?' got stale PC chores and was answered from notes, with nothing checked."""
    storage.save("journal-pc.json", [
        {"id": "a", "t": _days_ago(3) + "T10:00", "summary": "Cleaned up disk space.", "open": ["delete the old backups on D drive"]},
        {"id": "b", "t": _days_ago(4) + "T10:00", "summary": "Installed updates.", "open": ["restart the PC after the update"]}])
    _facts(*FILLER)
    for said in ("Is my Steam open?", "Is Blender still open on my PC?", "Is the garage door open?", "open Steam"):
        assert not any("Still open" in line for line in recall.for_turn(said)), said
        assert "Still open" not in intelligence.relevant_memory(said), said
    assert recall.for_turn("Is my Steam open?") == []
    backups = recall.for_turn("Did I delete the old backups yet?")             # the open items themselves still count
    assert backups and "Still open: delete the old backups on D drive." in backups[0]
    assert journal.search("restart the PC")[0]["id"] == "b"


def test_thanks_and_trivial_replies_recall_nothing():
    _facts(("Their wife is Maya.", "family", _days_ago(1)), ("Says thanks a lot.", "other", _days_ago(1)), *FILLER)
    for said in ("thanks", "Thanks.", "Thank you, Jarvis.", "okay", "yes", "no", "Cool, thanks!"):
        assert recall.for_turn(said) == [], said
        assert intelligence.relevant_memory(said) == "", said


def test_at_most_eight_items_or_350_tokens():
    _facts(*[(f"Guitar lesson {i}: practise chord shape number {i} for ten minutes before bed.", "goal", _days_ago(4))
             for i in range(20)], *FILLER)
    lines = _lines("When are my guitar lessons?")
    assert 1 <= len(lines) <= recall.MAX_ITEMS
    assert sum(recall._cost(line) for line in lines) <= recall.MAX_TOKENS
    long = "Guitar " + "notes " * 200
    _facts((long, "goal", _days_ago(1)), *FILLER)
    (line,) = _lines("guitar notes")
    assert len(line) < recall.LINE_CHARS + 40                         # one long item is cut, never the whole budget


def test_items_under_half_the_best_score_are_dropped():
    _facts(("Their sister Marie is a nurse at Jackson Memorial in Miami.", "family", _days_ago(2)),
           ("Miami Heat games are on Tuesdays.", "other", _days_ago(2)), *FILLER)
    lines = _lines("Is my sister Marie still a nurse in Miami?")
    assert any("Jackson Memorial" in line for line in lines)
    assert not any("Heat" in line for line in lines)                  # matched one word: under half the best
    assert "Heat" in memory.recall("Miami")                           # the tool still lists every match


def test_each_line_says_how_old_it_is_and_where_it_came_from():
    memory.remember("Their sister Marie lives in Miami.", "family", False, source="said", evidence="my sister Marie")
    storage.save(memory.FILE, storage.load(memory.FILE, []) + [
        {"id": "old001", "fact": "Their brother Paul lives in Norfolk.", "category": "family", "saved": _days_ago(3)}])
    storage.save("journal-pc.json", [{"id": "j1", "t": _days_ago(9) + "T10:00", "summary": "Planned the Miami trip to see Marie.",
                                      "open": ["book the flights"], "source": "local_model"}])
    lines = _lines("When do I see my sister Marie in Miami?")
    assert "- (today, you said) Their sister Marie lives in Miami." in lines
    assert any(line.startswith("- (9d ago, our conversation) Planned the Miami trip") and "Still open: book the flights" in line
               for line in lines)
    assert recall.render(recall.Doc("f:old001", "fact", "Their brother Paul lives in Norfolk.", when=_days_ago(3))) \
        == "- (3d ago, noted) Their brother Paul lives in Norfolk."


def test_lessons_are_offered_for_a_request_but_never_answer_a_question_about_him():
    journal.add_lesson("Proton apps are per-user installs: use their own uninstaller.", "owner")
    _facts(*FILLER)
    assert any("Proton apps" in line for line in _lines("uninstall the proton apps"))
    # a question about him: a non-empty block means "already looked up" (intelligence.retrieved_notes), so a rule for
    # Jarvis must not stand in for an answer
    assert not any("Proton" in line for line in _lines("which proton apps do I have?"))


def test_a_fact_is_attached_again_when_a_later_question_needs_it():
    """Design note (mem0's per-conversation seen set) NOT adopted: contextfit drops the lookups attached to earlier
    questions, so a fact held back the second time would be missing from what the local model sees."""
    from core import contextfit
    _facts(("Their wife is Maya.", "family", _days_ago(1)), *FILLER)
    first = intelligence.relevant_memory("What's my wife's name?")
    again = intelligence.relevant_memory("Sorry, what was my wife's name again?")
    assert "Maya" in first and "Maya" in again
    history = [{"role": "system", "content": "You are Jarvis."},
               {"role": "user", "content": [{"type": "text", "text": f"<retrieved-memory>\n{first}\n</retrieved-memory>"},
                                            {"type": "text", "text": "What's my wife's name?"}]},
               {"role": "assistant", "content": "Maya, sir."},
               {"role": "user", "content": "Sorry, what was my wife's name again?"}]
    sent = json.dumps(contextfit.fit(history, []))
    assert "Maya, sir." in sent and "Their wife is Maya." not in sent    # the earlier lookup is gone


def test_the_turn_fails_open_after_300_ms(monkeypatch):
    _facts(("Their wife is Maya.", "family", _days_ago(1)))
    monkeypatch.setattr(recall, "pick", lambda *a, **k: time.sleep(1) or ["- late"])
    start = time.perf_counter()
    assert recall.for_turn("What's my wife's name?", timeout=0.05) == []
    assert time.perf_counter() - start < 0.5
    monkeypatch.setattr(recall, "pick", lambda *a, **k: (_ for _ in ()).throw(ValueError("broken store")))
    assert recall.for_turn("What's my wife's name?") == []           # a broken store never blocks the answer


def test_identity_facts_always_reach_the_background():
    """The background kept only the newest 120 facts: his wife's name, among the first saved, fell out of it."""
    _facts(("Their wife is Maya.", "family", _days_ago(30)), ("I live in Springfield, Illinois.", "personal", _days_ago(30)),
           ("Likes spicy wings.", "preference", _days_ago(30)),
           *[(f"Note number {i} about the homelab drive.", "other", _days_ago(2)) for i in range(130)])
    ctx = memory.context_block()
    assert "Their wife is Maya." in ctx and "I live in Springfield, Illinois." in ctx
    assert "Likes spicy wings." not in ctx                             # not identity: the newest 120 as before
    assert "Note number 129" in ctx and "Note number 9 " not in ctx


# -- corrections keep history ---------------------------------------------------------------------------------------------
def test_a_correction_closes_the_old_row_and_recall_returns_the_new_one():
    assert memory.remember("I live in Decatur.", "personal", False).startswith("Noted")
    out = memory.remember("I live in Springfield now.", "personal", False)
    assert "this replaces: you live in Decatur." in out
    assert [m["fact"] for m in memory._all()] == ["I live in Springfield now."]   # memory.json: only what is true now
    now = memory.recall("where do I live")
    assert "Springfield" in now and "Newport" not in now
    assert "Newport" not in intelligence.relevant_memory("Where do I live?")
    log = [json.loads(x) for x in storage.path(f"memory-log-{recall.machine()}.jsonl").read_text(encoding="utf-8").splitlines()]
    closed = [e for e in log if e["op"] == "close"]
    assert closed and closed[0]["row"]["fact"] == "I live in Decatur." and closed[0]["reason"] == "corrected"
    assert closed[0]["by"] == "I live in Springfield now."


def test_where_did_i_live_before_returns_the_closed_row():
    memory.remember("I live in Decatur.", "personal", False)
    memory.remember("I live in Springfield now.", "personal", False)
    before = memory.recall("where did I live before Springfield?")
    assert "I live in Decatur." in before and "no longer current since" in before
    assert "now: I live in Springfield now." in before
    turn = intelligence.relevant_memory("Where did I live before Springfield?")
    assert "Decatur" in turn and "no longer current" in turn


def test_a_plan_never_closes_the_current_row():
    memory.remember("I live in Springfield.", "personal", False)
    for plan in ("I'm moving to Denver in March.", "I live in Denver starting March.", "I will live in Denver next year.",
                 "My address is going to be 12 Elm Street."):
        memory.remember(plan, "personal", False)
    facts = [m["fact"] for m in memory._all()]
    assert "I live in Springfield." in facts
    assert not memory.corrects("I'm moving to Denver in March.", "I live in Springfield.")
    assert not memory.corrects("I live in Denver starting March.", "I live in Springfield.")
    assert memory.corrects("I live in Denver now.", "I live in Springfield.")
    assert memory.corrects("My birthday is in March.", "My birthday is in April.")      # a date IS the value here
    assert memory.corrects("My boss is Will.", "My boss is Dana.")                       # Will is a name, not a plan
    assert "Springfield" in memory.recall("where do I live")
    # only the slot's own statement is judged: a plan that qualifies the value counts, another statement does not
    assert not memory.corrects("My address is 12 Elm Street, starting next month.", "My address is 4 Oak Lane.")
    assert memory.corrects("My wife's name is Marie, I'll tell her you said hi.", "My wife's name is Maya.")
    assert memory.corrects("I'm going to tell you one more time: my wife's name is Marie.", "My wife's name is Maya.")


def test_his_sisters_home_is_not_his_home():
    memory.remember("I live in Springfield.", "personal", False)
    memory.remember("My sister Marie lives in Miami.", "family", False)
    assert "I live in Springfield." in [m["fact"] for m in memory._all()]


def test_a_kind_of_home_or_a_wider_area_never_closes_his_city():
    """Any 'live(s) in <anything>' was his home: 'I live in an apartment.' closed 'I live in Springfield, Illinois.'"""
    memory.remember("I live in Springfield, Illinois.", "personal", False)
    for other in ("I live in an apartment.", "We live in a townhouse near Lincoln.", "I live in Norfolk during the week.",
                  "Lives in Springfield with his wife Maya and son Noah.", "I live in Springfield now, near where I was stationed."):
        assert "replaces" not in memory.remember(other, "personal", False), other
    facts = [m["fact"] for m in memory._all()]
    assert "I live in Springfield, Illinois." in facts and "I live in an apartment." in facts
    assert "I live in Springfield now, near where I was stationed." in facts         # a sure statement is never "uncertain"
    assert "Springfield" in memory.context_block()
    assert not memory.corrects("I live in Springfield.", "I live in Virginia.")      # a city inside the state, not a move
    assert not memory.corrects("I live in Virginia.", "I live in Springfield.")
    assert memory.corrects("I live in Texas now.", "I live in Virginia.")
    assert memory.corrects("I live in Decatur.", "I live in Springfield, Illinois.")
    assert memory.corrects("My car is a Tesla Model 3 now, I'll sell the Civic soon.", "My car is a Honda Civic.")


def test_his_own_home_never_ends_the_households():
    """'We live in Springfield, Illinois.' (household) was deleted for everyone by one person's 'I live in Norfolk ...'."""
    member = users.add("Teen", {"en": "buddy"}, "member")
    memory.remember("We live in Springfield, Illinois.", "personal", True)
    with users.acting_as(member):
        memory.remember("I live in Norfolk now.", "personal", False)
    memory.remember("I live in Richmond.", "personal", False)
    assert [m["fact"] for m in storage.load(memory.HOUSEHOLD_FILE, [])] == ["We live in Springfield, Illinois."]
    assert memory.corrects("We live in Denver now.", "We live in Springfield, Illinois.")   # the household's own move


def test_a_members_private_fact_never_reaches_the_household_log():
    """Closing 'Our wifi name is HomeNet.' by a member's private 'My wifi name is SecretDorm5G.' wrote that text into
    the household log, where every user's recall read it ('what was the wifi before?')."""
    member = users.add("Teen", {"en": "buddy"}, "member")
    memory.remember("Our wifi name is HomeNet.", "other", True)
    with users.acting_as(member):
        memory.remember("My wifi name is SecretDorm5G.", "other", False)
    assert "SecretDorm5G" not in _files_text(storage.DATA / "household")
    assert "SecretDorm5G" not in memory.recall("what was the wifi name before?")
    assert "SecretDorm5G" not in intelligence.relevant_memory("What was our wifi name before?")
    with users.acting_as(member):
        memory.remember("Our wifi name is CasaNet.", "other", True)               # a household fact closing his own:
    assert "SecretDorm5G" not in _files_text(storage.DATA / "household")         # the household row names none of it


def _files_text(folder) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix in (".json", ".jsonl"))


def test_forget_erases_the_text_everywhere_and_never_brings_it_back(monkeypatch):
    """'Forgotten: X' while X stayed in the log and the backup (both synced to the laptop) and came back in any turn
    that said 'delete' or 'forget', the cloud fallback's payload included."""
    machine = {"name": "server"}
    monkeypatch.setattr(recall, "machine", lambda: machine["name"])
    memory.remember("Their sister Marie lives in Miami.", "family", False)   # on the laptop: its log and backup
    memory.remember("Marie's garage door code is 4417.", "other", False)
    machine["name"] = "pc"
    memory.remember("Their son is Noah.", "family", False)                     # on the PC: its own log and backup
    mid = next(m["id"] for m in memory._all() if "4417" in m["fact"])
    assert memory.forget(mid) == "Forgotten: Marie's garage door code is 4417."
    user = storage.user_dir()
    assert "4417" not in (user / "memory.json").read_text(encoding="utf-8")
    assert "4417" not in (user / "memory-log-pc.jsonl").read_text(encoding="utf-8")
    assert "4417" not in (user / "memory.before-recall-pc.json").read_text(encoding="utf-8")
    for said in ("Delete the old photos in the garage folder", "I forgot where the garage remote is",
                 "What's Marie's garage door code?"):
        assert "4417" not in intelligence.relevant_memory(said), said
    assert "4417" not in memory.recall("garage") and "4417" not in memory.recall("what did you forget?")
    assert "erased" in memory.recall("what did you forget?")
    machine["name"] = "server"                                                 # the laptop erases its own copies
    memory.remember("Their dog is Biscuit.", "family", False)
    assert "4417" not in _files_text(storage.DATA)
    memory.remember("Marie's garage door code is 4417.", "other", False)       # said again: simply remembered again
    assert "4417" in memory.recall("garage")


def test_a_closed_rows_now_is_what_is_current_now():
    """Springfield -> Denver -> Austin: Springfield's history line said 'now: I live in Denver.'"""
    for city in ("Springfield", "Denver", "Austin"):
        memory.remember(f"I live in {city}.", "personal", False)
    before = memory.recall("where did I live before?")
    assert "now: I live in Austin." in before and "now: I live in Denver." not in before
    turn = intelligence.relevant_memory("Where did I live before Austin?")
    assert "now: I live in Denver." not in turn and "now: I live in Austin." in turn


def test_history_is_searched_only_for_a_question_about_the_past():
    """'before' anywhere ('call my wife before 5') attached the ended rows, ranked above what is true now."""
    memory.remember("My wife's name is Maya.", "people", False)
    memory.remember("My wife's name is Joanne.", "people", False)              # his speech-to-text's spelling
    for said in ("Remind me to call my wife before 5", "Text my wife before dinner"):
        assert "no longer current" not in intelligence.relevant_memory(said), said
    for said in ("Who was my wife before?", "What was my wife's name before Joanne?", "Before I moved, where did I live?"):
        assert ("no longer current" in intelligence.relevant_memory(said)) == ("wife" in said), said
    assert recall.intent_kinds("Before I moved, where did I live?") == {"closed"}


def test_the_background_keeps_identity_facts_within_a_bound():
    _facts(*[(f"Takes the kids to practice number {i}.", "schedule", _days_ago(90)) for i in range(300)],
           *[(f"Note number {i} about the homelab drive.", "other", _days_ago(2)) for i in range(130)])
    lines = memory.context_block().splitlines()
    assert sum("Takes the kids" in line for line in lines) == memory._IDENTITY_KEPT
    assert "- Takes the kids to practice number 0." in lines                   # the first saved are the ones kept


def test_a_correction_back_is_current_not_history():
    memory.remember("My wife's name is Maya.", "people", False)
    memory.remember("My wife's name is Marie.", "people", False)
    memory.remember("My wife's name is Maya.", "people", False)
    before = memory.recall("who was my wife before?")
    history = [line for line in before.splitlines() if "no longer current" in line]
    assert len(history) == 1 and history[0].endswith(") My wife's name is Marie.")   # Maya is current again
    assert "now: My wife's name is Maya." in history[0]


def test_household_history_goes_to_the_household_log():
    memory.remember("The wifi name is HomeNet.", "other", True)
    memory.remember("The wifi name is CasaNet.", "other", True)
    assert (storage.DATA / "household" / f"memory-log-{recall.machine()}.jsonl").exists()
    assert "HomeNet" in memory.recall("what was the old wifi name before?")


# -- migration: old rows keep working, the file is backed up first --------------------------------------------------------
def test_existing_memories_migrate_without_loss():
    legacy = _facts(("Their wife is Maya.", "family", "2026-10-12"), ("Works night shifts on weekends.", "work", "2026-10-12"))
    before = storage.path(memory.FILE).read_bytes()
    assert "Maya" in memory.recall("wife")                         # old rows (no source) are recalled as they are
    memory.remember("Their sister Marie lives in Miami.", "family", False)
    rows = storage.load(memory.FILE, [])
    assert rows[:2] == legacy                                         # untouched, field for field
    assert rows[2]["source"] == "remember" and rows[2]["saved"] == date.today().isoformat()
    backup = storage.path(f"memory.before-recall-{recall.machine()}.json")
    assert backup.read_bytes() == before                              # the file as it was, copied before the first write
    memory.remember("Their son is Noah.", "family", False)
    assert backup.read_bytes() == before                              # copied once, never overwritten


def test_without_fts5_a_python_bm25_does_the_same_job(monkeypatch):
    monkeypatch.setattr(recall, "_fts5", False)
    recall._cache.clear()
    _facts(("Their sister Marie lives in Miami.", "family", _days_ago(3)), *FILLER)
    assert "Marie" in memory.recall("sibling")
    assert "Marie" in memory.recall("lived")                          # the same stems on both sides
    recall._cache.clear()


def test_journal_search_uses_the_same_recall():
    storage.save("journal-pc.json", [
        {"id": "a", "t": "2026-10-01T10:00", "summary": "Fixed the homelab drive mount.", "open": []},
        {"id": "b", "t": "2026-11-01T10:00", "summary": "Talked about Noah's school.", "open": ["buy the kids' uniforms"]}])
    assert journal.search("homelab")[0]["id"] == "a"
    assert journal.search("my son's uniforms")[0]["id"] == "b"         # an open item, found through the alias
    assert journal.search("thanks") == []


# -- speed ---------------------------------------------------------------------------------------------------------------
def test_a_thousand_facts_recall_in_under_20_ms():
    words = ["garage", "router", "printer", "garden", "invoice", "camera", "bicycle", "laptop", "kitchen", "monitor"]
    _facts(*[(f"Fact {i}: the {words[i % 10]} needs {words[(i * 7) % 10]} parts by week {i % 52}.", "other",
              _days_ago(i % 90)) for i in range(1000)],
           ("Their sister Marie lives in Miami.", "family", _days_ago(3)))
    start = time.perf_counter()
    assert "Marie" in memory.recall("sibling")                        # cold: reads and indexes everything
    cold = time.perf_counter() - start
    timings = []
    for _ in range(7):
        start = time.perf_counter()
        memory.recall("sibling")
        recall.pick("Where does my sister live?", users.current())
        timings.append(time.perf_counter() - start)
    assert sorted(timings)[3] < 0.020, timings                         # warm (median): the index is reused
    assert cold < recall.TIMEOUT, cold


# -- the core background (upgrade 7): what every turn needs; the rest is looked up per turn ------------------------------
def _store_everything():
    _facts(("Their wife is Maya.", "family", _days_ago(300)), ("I live in Springfield, Illinois.", "personal", _days_ago(300)),
           ("Their son is Noah.", "family", _days_ago(200)),
           ("The owner wants Jarvis to act right away on a clear instruction.", "preference", _days_ago(20)),
           *[(f"Note number {i} about the homelab drive and its backups.", "other", _days_ago(2)) for i in range(200)],
           ("Likes spicy wings.", "preference", _days_ago(1)))
    _facts(("The Wi-Fi is called HomeNet.", "other", _days_ago(50)), household=True)
    storage.save("journal-pc.json", [{"id": f"j{i}", "t": f"2026-10-{i // 10 + 1:02d}T{i % 10 + 8:02d}:00",
                                      "summary": f"Talked through part {i} of the garage shelving plan in detail. " * 3,
                                      "open": [f"buy brackets for shelf {i}"]} for i in range(30)])
    storage.save(journal.LESSONS, [{"lesson": f"Lesson {i}: when the owner says X, check Y before saying it is done. " * 2,
                                    "t": _days_ago(1)} for i in range(150)])


def test_the_core_background_stays_small_and_keeps_who_he_is():
    from core import contextfit
    _store_everything()
    core = memory.context_block(core_only=True)
    assert contextfit.tokens([{"content": core}]) <= 1100               # was ~3,000 on his real data
    assert "Current date and time" in core and "You are talking with" in core
    assert "Their wife is Maya." in core and "I live in Springfield, Illinois." in core and "Their son is Noah." in core
    assert "act right away on a clear instruction" in core            # how he wants Jarvis to behave: every turn
    assert "The Wi-Fi is called HomeNet." in core                       # the household's shared facts
    assert "Likes spicy wings." not in core and "Note number" not in core   # looked up per turn (relevant_memory)
    assert "part 29 of the garage" in core and "buy brackets for shelf 29" in core   # the latest conversation, open item
    assert "part 27 of the garage" not in core                          # only the latest two
    assert "Lesson 149:" in core and "Lesson 139:" not in core          # the newest lessons, at most 10
    full = memory.context_block(core_only=False)
    assert "Likes spicy wings." in full and contextfit.tokens([{"content": full}]) > 2000


def test_the_core_background_is_the_default_only_for_the_local_brain(monkeypatch):
    from core import intelligence
    _store_everything()
    assert "Likes spicy wings." in memory.context_block()                # the cloud brain: everything, as before
    monkeypatch.setattr(intelligence, "enabled", lambda: True)          # the local brain looks the rest up each turn
    assert "Likes spicy wings." not in memory.context_block() and "Their wife is Maya." in memory.context_block()


def test_identity_outlasts_everything_else_when_the_core_is_crowded():
    from core import contextfit
    _facts(*[(f"Their cousin number {i} is called Name{i} and lives far away in another state.", "family", _days_ago(9))
             for i in range(80)])
    core = memory.context_block(core_only=True)
    assert contextfit.tokens([{"content": core}]) <= 1100
    assert "Their cousin number 0 is called Name0" in core             # the first identity facts saved stay


def test_lessons_keep_their_share_when_behaviour_rules_crowd_the_core():
    """Lessons were trimmed first: on his real data 6 of 65 survived, and every new 'Jarvis should...' preference pushed
    them further toward none. A lesson for a content-free reply ('No, you don't have to.') is never recalled per turn,
    so it has to be here. What per-turn recall brings back (household facts, the older conversation) goes first."""
    _facts(("Their wife is Maya.", "family", _days_ago(300)),
           *[(f"The owner wants Jarvis to follow house rule number {i} about the {i}th kind of reply.", "preference",
              _days_ago(30)) for i in range(40)])
    _facts(*[(f"The garage code for door {i} is written on the fridge.", "other", _days_ago(50)) for i in range(6)],
           household=True)
    storage.save("journal-pc.json", [{"id": f"j{i}", "t": f"2026-10-{i + 1:02d}T09:00",
                                      "summary": f"Talked through part {i} of the garage shelving plan.", "open": []}
                                     for i in range(5)])
    storage.save(journal.LESSONS, [{"lesson": f"Lesson {i}: after 'no, you don't have to', stop offering it again.",
                                    "t": _days_ago(1)} for i in range(65)])
    core = memory.context_block(core_only=True)
    lessons = [n for n in range(65) if f"Lesson {n}:" in core]
    assert "Lesson 64:" in core and len(lessons) >= 5, lessons
    assert "Their wife is Maya." in core and "house rule number 39" in core
    assert "garage code" not in core                                   # recalled per turn when it is asked about
    assert "part 4 of the garage" in core and "part 3 of the garage" not in core


def test_a_preference_about_how_he_is_answered_is_part_of_the_core():
    """_pinned needed the literal word 'jarvis': 'prefers brief summaries when asking for a status update' was left to
    per-turn recall, which a plain 'how are things?' never matches."""
    _facts(("Prefers brief summaries when asking for a status update.", "preference", _days_ago(10)),
           ("Prefers black coffee for mornings.", "preference", _days_ago(10)))
    core = memory.context_block(core_only=True)
    assert "brief summaries" in core and "black coffee" not in core


def test_the_first_identity_facts_saved_outlast_later_ones():
    """Identity facts were trimmed oldest first: his wife and home, the first facts saved, went before the 80th
    cousin. _window keeps the first identity facts for exactly this reason (2026-10-30)."""
    from core import contextfit
    _facts(("Their wife is Maya.", "family", _days_ago(300)), ("I live in Springfield, Illinois.", "personal", _days_ago(300)),
           *[(f"Their cousin number {i} is called Name{i} and lives far away in another state.", "family", _days_ago(9))
             for i in range(80)])
    core = memory.context_block(core_only=True)
    assert contextfit.tokens([{"content": core}]) <= 1100
    assert "Their wife is Maya." in core and "I live in Springfield, Illinois." in core
    assert "Their cousin number 79 is called" not in core              # the newest are looked up per turn
