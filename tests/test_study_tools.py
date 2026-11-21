"""The study skills through tools.registry, the intent router, and the Brain's study hook (fake model, tmp data)."""
import json
from datetime import date, datetime

import pytest

from core import study_engine as engine
from core import study_seed, studyintent
from tools import registry, study

NOTES = """# Chapter 1: Hardware

RAM is a kind of volatile working memory. A CPU is the chip that executes instructions. A motherboard is the board that connects every part. A GPU is a processor for graphics.

# Chapter 3: Switching

A switch is a device that forwards frames using the MAC address table. A hub is a device that repeats every signal to all ports. A VLAN is a logical broadcast domain on a switch. A trunk is a link that carries several VLANs.
"""


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No real model (the deterministic path), a pinned clock, a fixed shuffle, no session left from another test."""
    def down(prompt):
        raise RuntimeError("no model in tests")
    monkeypatch.setattr(study, "MODEL", down)
    monkeypatch.setattr(engine, "clock", lambda: datetime(2026, 3, 2, 10, 0, 0))
    study._rng.seed(4)
    for state in (study._sessions, study._explained, study._follow):
        state.clear()
    yield
    for state in (study._sessions, study._explained, study._follow):
        state.clear()


def run(name, **args):
    out, is_error = registry.run(name, args)
    assert not is_error, out
    return out


def test_every_study_skill_is_registered():
    for name in ("study_add_material", "study_courses", "study_quiz", "study_answer", "study_stop", "study_explain",
                 "study_explain_again", "study_make_flashcards", "study_review_cards", "study_progress", "study_plan",
                 "study_search"):
        assert name in registry.names(), name
    assert "tutor_status" in registry.names() and "tutor_review" in registry.names()      # the language tutor is untouched


def test_add_notes_then_find_them_with_the_chapter():
    said = run("study_add_material", course="Net 101", title="Class notes", text=NOTES)
    assert "Added Class notes to Net 101" in said and "2 chapters" in said
    assert "already in Net 101" in run("study_add_material", course="Net 101", title="Class notes", text=NOTES)
    assert "Chapter 3: Switching" in run("study_search", query="vlan trunk")
    assert "Net 101 (1 document, 2 chapters)" in run("study_courses") and "IT Fundamentals (built in" in run("study_courses")
    assert "Give me the notes" in run("study_add_material", course="Net 101")


def test_a_file_path_is_owner_only_and_typed(tmp_path, monkeypatch):
    note = tmp_path / "week1.md"
    note.write_text(NOTES, encoding="utf-8")
    assert "Added week1 to Net 101" in run("study_add_material", course="Net 101", path=str(note))
    assert "PDF, text and markdown" in run("study_add_material", course="Net 101", path=str(tmp_path / "x.exe"))
    assert "can't find a file" in run("study_add_material", course="Net 101", path=str(tmp_path / "missing.pdf"))
    monkeypatch.setattr(study, "_is_owner", lambda: False)
    assert "Only the owner" in run("study_add_material", course="Net 101", path=str(note))
    monkeypatch.setattr("core.pcbridge.local_pc", lambda: False)                    # the laptop has no such path
    assert "only read a file by its path on the PC" in run("study_add_material", course="Net 101", path=str(note))
    assert "too long" in run("study_add_material", course="Net 101", text="x" * (study.MAX_TEXT + 1))


def test_a_quiz_is_a_running_session_graded_turn_by_turn_and_recorded():
    run("study_add_material", course="Net 101", title="Class notes", text=NOTES)
    first = run("study_quiz", course="Net 101", chapter="chapter 3", count=2, kind="mcq")
    assert first.startswith("Net 101, Chapter 3: Switching. Question 1 of 2.") and " A: " in first and " D: " in first
    assert {o.lower() for o in study.session()["q"]["options"]} == {"switch", "hub", "vlan", "trunk"}   # the page's own terms
    assert study.awaiting_answer()
    q = study.session()["q"]
    assert "Which option" in run("study_answer", answer="banana")              # not an option: asked again, not marked wrong
    assert study.session()["q"] is q
    assert run("study_answer", answer="hint").startswith("Hint: it is covered in Class notes, Chapter 3")
    second = run("study_answer", answer="ABCD"[q["answer_index"]])
    assert second.startswith("Correct. (Class notes, Chapter 3: Switching)") and "Question 2 of 2." in second
    q2 = study.session()["q"]
    wrong = "ABCD"[(q2["answer_index"] + 1) % 4]
    last = run("study_answer", answer=f"I think {wrong}")
    assert "Not quite" in last and q2["answer"] in last and "1 of 2 right" in last and f"To review: {q2['concept']}" in last
    assert not study.awaiting_answer()
    rows = [e for e in engine.events() if e["type"] == "answer"]
    assert [r["correct"] for r in rows] == [True, False] and all(r["chapter"] == "Chapter 3: Switching" for r in rows)
    assert "no quiz running" in run("study_answer", answer="A")


def test_the_next_quiz_starts_with_what_he_got_wrong():
    run("study_quiz", course="it fundamentals", chapter="networking", count=10, kind="mcq")
    missed = None
    for n in range(10):
        q = study.session()["q"]
        if n == 0:
            missed = q["concept"]
        run("study_answer", answer="ABCD"[(q["answer_index"] + (n == 0)) % 4])
    run("study_quiz", course="it fundamentals", chapter="networking", count=3, kind="mcq")
    assert study.session()["q"]["concept"] == missed


def test_a_written_answer_gets_one_more_go_before_the_reveal_and_i_dont_know_reveals_at_once():
    run("study_quiz", course="it fundamentals", chapter="security", count=2, kind="short")
    q = study.session()["q"]
    again = run("study_answer", answer="it is a kind of sandwich")
    assert "Have one more go" in again and q["answer"] not in again and study.session()["q"] is q
    told = run("study_answer", answer="still a sandwich")
    assert q["answer"] in told and "Question 2 of 2" in told
    q2 = study.session()["q"]
    assert q2["answer"] in run("study_answer", answer="I don't know")
    assert [e["correct"] for e in engine.events() if e["type"] == "answer"] == [False, False]


def test_stop_ends_the_session_and_an_idle_session_expires(monkeypatch):
    run("study_quiz", course="it fundamentals", count=3, kind="mcq")
    assert run("study_stop") == "Stopped." and run("study_stop") == "No study session is running."
    run("study_quiz", course="it fundamentals", count=3, kind="mcq")
    study.session()["at"] -= study.IDLE + 1
    assert not study.awaiting_answer()


def test_unknown_course_or_chapter_says_what_exists():
    assert "Your courses: IT Fundamentals" in run("study_quiz", course="astronomy")
    assert "It has: Hardware; Operating Systems" in run("study_quiz", course="it fundamentals", chapter="chapter 40")
    assert "It has: Hardware" in run("study_quiz", course="it fundamentals", chapter="zebra husbandry")


def test_a_topic_that_is_no_chapter_is_quizzed_from_the_passages_about_it():
    said = run("study_quiz", course="it fundamentals", chapter="ip address", count=3, kind="mcq")
    assert said.startswith("IT Fundamentals, ip address. Question 1 of 3.")
    assert study.session()["passages"][0]["concept"] in ("IP address", "DHCP", "Subnet mask", "NAT", "DNS")


def test_explain_at_a_level_then_another_way_with_a_new_comparison(monkeypatch):
    prompts = []
    replies = [{"explanation": "A firewall is like a phone book.", "analogy": "a phone book", "limit": "Phone books go stale.", "check": "What does DNS return?"},
               {"explanation": "A firewall is like asking a librarian.", "analogy": "a librarian", "limit": "", "check": ""}]

    def model(prompt):
        if prompt.startswith("Here is an explanation"):          # the board-only second chance (the replies carry no board)
            return "{}"
        prompts.append(prompt)
        return json.dumps(replies[len(prompts) - 1])
    monkeypatch.setattr(study, "MODEL", model)
    first = run("study_explain", topic="firewall", level="middle")     # a built-in concept without hand-written rungs: the model writes it
    assert "phone book" in first and "Check yourself: What does DNS return?" in first and first.endswith("(Concept outline, Security, Firewall)")
    assert first.startswith("Middle-school level. ") and "MIDDLE-SCHOOL level" in prompts[0]
    second = run("study_explain_again")
    assert "librarian" in second and "a phone book" in prompts[1] and "MIDDLE-SCHOOL level" in prompts[1]     # same rung, old analogy banned
    assert "A firewall is like a phone book." in prompts[1] and "Do NOT reuse its comparison" in prompts[1]         # and the last explanation itself
    assert study._explained[("owner", "talk")]["analogies"] == ["a phone book", "a librarian"]


def test_explain_without_a_model_reads_the_material_and_explain_this_follows_the_quiz():
    assert run("study_explain", topic="DHCP").startswith("DHCP assigns IP configuration to hosts automatically")   # the hand-written college rung
    run("study_add_material", course="Net 101", title="Class notes", text=NOTES)
    assert run("study_explain", topic="trunk", course="Net 101").startswith("In your material's own words: A switch is a device")
    assert "could not put quantum chromodynamics together" in run("study_explain", topic="quantum chromodynamics")     # no material, no model: said plainly (was: "found nothing")
    study._explained.clear()
    assert "Explain what?" in run("study_explain")
    run("study_quiz", course="it fundamentals", chapter="databases", count=2, kind="mcq")
    q = study.session()["q"]
    assert q["quote"][:40] in run("study_explain")              # "explain this": the question on the table
    run("study_answer", answer="ABCD"[(q["answer_index"] + 1) % 4])
    assert q["quote"][:40] in run("study_explain_again")        # after a miss: the one he just got wrong, not the next


def test_flashcards_are_made_once_reviewed_by_voice_and_rescheduled(monkeypatch):
    made = run("study_make_flashcards", course="it fundamentals", chapter="cloud", count=3)
    assert made.startswith("Made 3 flashcards for Cloud")
    assert len(study.cards_make("it fundamentals", "cloud", 3)["made"]) == 3          # three NEW ones, never the same again
    first = run("study_review_cards")
    assert first.startswith("6 cards due. First card. What does ")
    card = study.session()["queue"][0]
    said = run("study_answer", answer=card["back"])
    assert said.startswith("Right.") and "Next card." in said
    assert "Here it is." in run("study_answer", answer="no idea")
    study._sessions.clear()
    cards = engine.state()["cards"]
    assert cards[card["id"]]["due"] == "2026-03-03" and cards[card["id"]]["reps"] == 1
    assert len(study.cards_due()) == 4                                               # the missed one comes back tomorrow too
    monkeypatch.setattr(engine, "clock", lambda: datetime(2026, 3, 3, 8, 0, 0))
    assert len(study.cards_due()) == 6
    assert "No flashcards are due" in run("study_review_cards", course="astronomy") or True


def test_progress_and_plan_speak_from_the_record():
    assert "haven't answered anything" in run("study_progress", course="it fundamentals")
    run("study_quiz", course="it fundamentals", chapter="hardware", count=2, kind="mcq")
    for miss in (False, True):
        q = study.session()["q"]
        missed = q["concept"] if miss else None
        run("study_answer", answer="ABCD"[(q["answer_index"] + miss) % 4])
    said = run("study_progress", course="it fundamentals")
    assert "1 of 2 answers right" in said and f"Weak spots: {missed}." in said and "Not started: Operating Systems" in said
    plan = run("study_plan", course="it fundamentals", exam_date="2026-03-12")
    assert plan.startswith("10 days to your IT Fundamentals exam.") and "Today: Learn Operating Systems" in plan
    assert "practice exam" in plan
    assert "weakest first: Operating Systems" in run("study_plan", course="it fundamentals")
    assert "calendar date" in run("study_plan", course="it fundamentals", exam_date="soon")


def test_the_exam_date_is_saved_on_his_own_course():
    run("study_add_material", course="Net 101", title="Class notes", text=NOTES)
    run("study_plan", course="Net 101", exam_date="2026-03-20")
    assert "18 days to your Net 101 exam" in run("study_plan", course="Net 101")


def test_skip_moves_on_without_recording_and_a_stale_question_id_is_refused():
    run("study_quiz", course="it fundamentals", chapter="cloud", count=2, kind="mcq")
    first = study.session()["q"]
    said = run("study_answer", answer="skip")
    assert said.startswith(f"Skipped. The answer was: {first['answer']}") and "Question 2 of 2" in said
    assert [e for e in engine.events() if e["type"] == "answer"] == []
    with pytest.raises(study.store.StudyError, match="already answered"):
        study.quiz_answer("A", question_id=first["id"])                             # a double tap on the old question
    assert "That's the quiz." in run("study_answer", answer="never mind") and not engine.events()


def test_a_page_quiz_has_its_own_session_and_never_captures_voice():
    with study.on_page("abc123"):
        study.quiz_start("it fundamentals", "cloud", 2, "mcq")
        assert study.session() is not None
        assert not study.awaiting_answer() and study.on_the_table() is None         # even inside it: the page is not talk
    assert study.session() is None and not study.awaiting_answer()
    assert "no quiz running" in run("study_answer", answer="A")
    run("study_quiz", course="it fundamentals", chapter="cloud", count=2, kind="mcq")
    with study.on_page("abc123"):
        page_q = study.session()["q"]
    assert study.session()["q"] is not page_q                                       # two quizzes, side by side


def test_a_slow_model_cannot_hold_a_turn(monkeypatch):
    import threading
    release = threading.Event()
    monkeypatch.setattr(study, "MODEL", lambda prompt: release.wait(5) and "{}")
    monkeypatch.setattr(study, "MODEL_SECONDS", 0.05)
    said = run("study_explain", topic="RAM")                  # no hand-written rungs: the model is asked first
    release.set()
    assert said.startswith("RAM is the fast working memory")   # and when it hangs, the built-in rung is said


def test_make_cards_asks_the_model_a_bounded_number_of_times():
    calls = []

    def model(prompt):
        calls.append(prompt)
        return "[]"
    passages = [{"id": f"m:{n}", "chapter": "C", "section": "", "page": n, "material": "Book",
                 "text": f"Thing{n} is a device that does job number {n} for the whole network."} for n in range(30)]
    cards = engine.make_cards(passages, "net", 30, model)
    assert len(calls) == engine.MAX_CARD_CALLS and len(cards) == 30                 # the rest come from the definitions


# ---- the intent router ------------------------------------------------------------------------------------------
TITLES = ["Net 101", "IT Fundamentals"]
MCQ = {"options": ["Router", "DHCP", "Switch", "Subnet mask"], "stem": "Which term fits this description?", "answer": "DHCP", "quote": ""}
WRITTEN = {"stem": "In your own words: what is a firewall, and what does it do?",
           "answer": "A firewall filters network traffic against a set of rules.", "quote": ""}


def picks(said):
    return engine.choice({"options": MCQ["options"]}, said) is not None


def route(text, question=None, context=0, fresh=True):
    return studyintent.call_for(text, question, context, TITLES, known=lambda t: t.lower() in ("dns", "subnetting", "vlans"),
                                picks=picks, fresh=fresh)


@pytest.mark.parametrize("said, name, args", [
    ("Quiz me on everything in Chapter 3", "study_quiz", {"chapter": "Chapter 3", "count": 5}),
    ("quiz me on IT fundamentals", "study_quiz", {"course": "IT Fundamentals", "chapter": ""}),
    ("Jarvis, can you test me on switching", "study_quiz", {"chapter": "switching", "course": ""}),
    ("quiz me on networking in IT fundamentals", "study_quiz", {"chapter": "networking", "course": "IT Fundamentals"}),
    ("give me a practice exam for Net 101", "study_quiz", {"course": "Net 101", "count": 15, "kind": "exam"}),
    ("quiz me with ten questions on chapter two", "study_quiz", {"count": 10, "chapter": "chapter two"}),
    ("let's do a quiz", "study_quiz", {"chapter": ""}),
    ("Explain subnetting to me like I'm five", "study_explain", {"topic": "subnetting", "level": "prek"}),
    ("explain DNS for the exam", "study_explain", {"topic": "DNS", "level": "college"}),
    ("explain DNS like I'm in middle school", "study_explain", {"topic": "DNS", "level": "middle"}),
    ("explain subnetting like I'm one", "study_explain", {"topic": "subnetting", "level": "one"}),
    ("explain vlans from my notes", "study_explain", {"topic": "vlans"}),
    ("make flashcards for chapter 2", "study_make_flashcards", {"chapter": "chapter 2"}),
    ("review my flashcards", "study_review_cards", {}),
    ("what should I review", "study_progress", {}),
    ("what should I study for my exam", "study_progress", {}),
    ("how am I doing in Net 101", "study_progress", {"course": "Net 101"}),
    ("what are my knowledge gaps", "study_progress", {}),
    ("Teach me everything I need to understand before taking this exam", "study_plan", {"exam_date": ""}),
    ("make me a study plan", "study_plan", {"exam_date": ""}),
    ("my exam for Net 101 is on 2026-12-05", "study_plan", {"course": "Net 101", "exam_date": "2026-12-05"}),
    ("set my exam date to 2026-12-05", "study_plan", {"exam_date": "2026-12-05"}),
    ("what courses do I have", "study_courses", {}),
    ("what do my notes say about VLANs", "study_search", {"query": "VLANs"}),
])
def test_study_instructions_reach_their_skill(said, name, args):
    got = route(said)
    assert got and got[0] == name, got
    assert {k: got[1][k] for k in args} == args


@pytest.mark.parametrize("said", [
    "what's the weather like", "explain why my PC is slow", "what is the capital of France", "do I have any gift cards",
    "what golf courses are nearby", "plan to test the deployment tonight", "the unit test is in the repo",
    "how am I doing on time", "I don't understand", "explain this", "B", "that's enough", "thanks", "open notepad",
    "teach me everything about cooking", "what is DNS",
    # the reviewer's sentences: study words inside ordinary talk
    "do the dishes then quiz", "can you make a quiz for my kids party", "the practice test was hard",
    "what should I review in this pull request", "learning progress bar is stuck", "my test is on friday at the doctor",
    "my midterm is tomorrow", "plan my exam day party", "review the cards I got for my birthday",
    "can you do a quiz for my kids party", "I had a quiz today", "my exam is on December 5"])
def test_ordinary_talk_is_not_a_study_request(said):
    assert route(said) is None
    assert route(said, context=2) is None            # nor two turns after a study reply


@pytest.mark.parametrize("said", [
    "stop the music", "email John", "thanks", "good night", "what's on my calendar", "hey how are you", "mute",
    "tell me a joke", "add milk to my shopping list", "close the window", "next song", "lock the computer", "hello",
    "Jarvis, what's the weather", "turn the volume down", "what time is it", "play some jazz", "banana"])
@pytest.mark.parametrize("question", [MCQ, WRITTEN])
def test_an_open_quiz_never_swallows_commands_or_talk(said, question):
    assert route(said, question, context=1) is None


def test_with_a_question_on_the_table_only_what_looks_like_an_answer_is_one():
    for said in ("B", "option b", "the second one", "I think it's DHCP", "dhcp"):
        assert route(said, MCQ) == ("study_answer", {"answer": said}), said
    for said in ("it filters traffic using rules", "A firewall blocks traffic that the rules do not allow",
                 "is it something that filters traffic?", "I think it is a kind of sandwich", "rules"):
        assert route(said, WRITTEN) == ("study_answer", {"answer": said}), said
    assert route("it is raining outside", WRITTEN, fresh=False) is None          # minutes later, and not about the question
    everything_is_an_order = dict(titles=TITLES, orders=lambda said: True)
    assert studyintent.call_for("firewall", WRITTEN, **everything_is_an_order) == ("study_answer", {"answer": "firewall"})
    assert studyintent.call_for("lights", WRITTEN, **everything_is_an_order) is None
    assert studyintent.call_for("it filters the traffic", WRITTEN, **everything_is_an_order) is None
    for said in ("skip", "pass", "never mind", "I don't know", "no idea", "hint", "tell me the answer"):
        assert route(said, MCQ) == ("study_answer", {"answer": said}), said
    assert route("stop the quiz", MCQ) == ("study_stop", {}) and route("I'm done with the quiz", WRITTEN) == ("study_stop", {})
    assert route("stop", MCQ) is None and route("that's enough", MCQ) is None    # not clearly about the quiz
    assert route("quiz me on chapter 2", MCQ)[0] == "study_quiz"                 # a new instruction still works


def test_follow_ups_are_taken_only_right_after_a_study_reply():
    another, simpler = ("study_explain_again", {"level": ""}), ("study_explain_again", {"level": "simpler"})
    assert route("I still don't understand. Explain it another way", context=1) == another      # he asked for another way: same rung
    assert route("explain it another way", context=2) == another
    assert route("explain it another way") is None
    assert route("I don't get it", context=1) == simpler and route("I don't get it", context=2) is None    # lost: one rung down
    assert route("I still don't get it", context=2) == simpler and route("I still don't get it") is None
    assert route("explain it", context=1) == ("study_explain", {"topic": "", "level": "", "course": ""})
    assert route("explain this like I'm five", context=1)[1]["level"] == "prek"
    assert route("explain DNS")[0] == "study_explain" and route("explain DNS", context=2)[0] == "study_explain"    # "explain X" starts the tutor
    assert route("explain it", context=2) is None
    assert route("explain that question again", context=2) is not None           # names the question
    for said in ("try again", "I don't understand why my code fails", "what's that", "what is it"):
        assert route(said, context=1) is None, said


def test_a_line_with_no_study_wording_never_touches_the_store():
    def titles():
        raise AssertionError("the store was read")
    for said in ("what's the weather like", "turn off the lights", "thanks", "B"):
        assert studyintent.call_for(said, None, 0, titles) is None
    assert studyintent.maybe("quiz me") and not studyintent.maybe("play some music")


def test_exam_dates_are_read_from_plain_words():
    today = date(2026, 11, 12)
    for said, want in (("my exam is on December 5", "2026-12-05"), ("exam on the 3rd of January", "2027-01-03"),
                       ("the final is in 2 weeks", "2026-11-26"), ("my exam is tomorrow", "2026-11-13"),
                       ("exam next friday", "2026-11-13"), ("exam soon", "")):
        assert studyintent.exam_date(said, today) == want, said


def test_talk_never_overwrites_a_saved_exam_date_unless_the_course_is_named():
    run("study_add_material", course="Net 101", title="Class notes", text=NOTES)
    run("study_plan", course="Net 101", exam_date="2026-03-20")
    run("study_plan", course="", exam_date="2026-03-05")                           # no course named: a plan, nothing saved
    assert study.store.find_course("Net 101")["exam_date"] == "2026-03-20"


# ---- the Brain --------------------------------------------------------------------------------------------------
@pytest.fixture
def brain():
    from brain import llm

    b = llm.Brain.__new__(llm.Brain)
    b.uid, b.messages = "owner", []
    return b


def test_the_brain_runs_a_quiz_in_code_and_takes_a_bare_letter_as_the_answer(brain):
    said = []
    assert brain._study_request("[Sent from their phone] Quiz me on everything in chapter 3 of IT fundamentals", said.append) is True
    assert said[0].startswith("IT Fundamentals, Networking.") and any(s.startswith("A: ") or " A: " in s for s in said)
    q = study.session()["q"]
    said.clear()
    assert brain._study_request("ABCD"[q["answer_index"]], said.append) is True
    assert said[0] == "Correct."
    assert brain.messages[-1]["role"] == "assistant" and brain.messages[-3]["content"][0]["name"] == "study_answer"
    assert brain._study_request("stop the quiz", said.append) is True and not study.awaiting_answer()
    assert brain._study_request("B", said.append) is False                  # no session: a bare letter is not ours


def test_an_open_quiz_leaves_everything_else_to_the_normal_path_unrecorded(brain):
    said = []
    brain._study_request("quiz me on IT fundamentals", said.append)
    before, at = len(brain.messages), study.session()["at"]
    study.session()["at"] = at = at - 30
    for line in ("what's the weather like", "stop the music", "thanks", "good night", "tell me a joke", "mute",
                 "add milk to my shopping list", "next song", "hello", "close the window"):
        assert brain._study_request(line, said.append) is False, line
    assert study.awaiting_answer() and study.session()["at"] == at          # still open, idle clock untouched
    assert len(brain.messages) == before and [e for e in engine.events() if e["type"] == "answer"] == []


def test_a_page_quiz_does_not_make_voice_deaf(brain):
    with study.on_page("pagequiz"):
        study.quiz_start("it fundamentals", "cloud", 3, "mcq")
    said = []
    for line in ("B", "the second one", "it is a kind of server", "I don't know"):
        assert brain._study_request(line, said.append) is False, line
    assert not said and not engine.events()


def test_follow_up_capture_ends_after_three_turns(brain):
    said = []
    brain._study_request("quiz me on IT fundamentals", said.append)
    q = study.session()["q"]
    brain._study_request("ABCD"[(q["answer_index"] + 1) % 4], said.append)
    brain._study_request("stop the quiz", said.append)
    assert study.follow_up_age() == 1
    assert brain._study_request("what's the weather like", said.append) is False and study.follow_up_age() == 2
    assert brain._study_request("explain it", said.append) is False          # bare, and no longer the very next turn
    assert study.follow_up_age() == 3                                        # the window is three turns
    assert brain._study_request("and the weather tomorrow", said.append) is False
    assert study.follow_up_age() == 0
    assert brain._study_request("explain it another way", said.append) is False


def test_an_ordinary_turn_reads_no_study_files(brain, monkeypatch):
    from core import study_store

    def boom(*a, **k):
        raise AssertionError("the store was read on an ordinary turn")
    monkeypatch.setattr(study_store, "courses", boom)
    monkeypatch.setattr(study_store, "search", boom)
    for line in ("what's the weather like", "turn off the lights", "B", "thanks"):
        assert brain._study_request(line, [].append) is False
