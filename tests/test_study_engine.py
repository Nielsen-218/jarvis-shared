"""The study engine with a fake model and a pinned clock: grounded questions, grading, explanations, SM-2, mastery."""
import json
import random
from datetime import date, datetime

import pytest

from core import study_engine as engine
from core import study_seed, study_store

PASSAGE = {"id": "m:0", "chapter": "Chapter 3: Switching", "section": "3.1 Frames", "page": 41, "page_end": 41,
           "material": "Net book",
           "text": "A switch is a device that forwards frames using the MAC address table. It learns the source address of every frame "
                   "it receives. Unknown destinations are flooded to all ports."}
OTHERS = [{"id": f"m:{n}", "chapter": "Chapter 3: Switching", "section": "", "page": 42, "material": "Net book", "text": t}
          for n, t in enumerate(["A router is a device that forwards packets between networks.",
                                 "A hub is a device that repeats every signal to all ports.",
                                 "A VLAN is a logical broadcast domain on a switch.",
                                 "A trunk is a link that carries several VLANs."], 1)]


class Model:
    """Replies with the queued values (dicts become JSON); records every prompt."""
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if self.replies else "no idea"
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    now = {"t": datetime(2026, 3, 2, 10, 0, 0)}
    monkeypatch.setattr(engine, "clock", lambda: now["t"])
    return now


MCQ = {"concept": "frame forwarding", "question": "How does a switch decide where to send a frame?",
       "answer": "It looks up the destination in its MAC address table",
       "distractors": ["It reads the destination IP address", "It sends every frame to all ports", "It asks the router"],
       "quote": "A switch is a device that forwards frames using the MAC address table.", "why": "Switches work on MAC addresses."}


def test_parse_json_takes_the_first_value_whatever_surrounds_it():
    assert engine.parse_json('<think>{"no": 1}</think>Sure!\n```json\n{"a": [1, 2]}\n``` hope that helps {"b": 2}') == {"a": [1, 2]}
    assert engine.parse_json("Here: [ {\"front\": \"x\"} ] trailing") == [{"front": "x"}]
    assert engine.parse_json("{broken") is None and engine.parse_json(None) is None


def test_a_grounded_model_question_is_kept_with_shuffled_options_and_its_page():
    model = Model(MCQ)
    q = engine.make_question(PASSAGE, "mcq", "net", model, rng=random.Random(1))
    assert q["source"] == "model" and len(q["options"]) == 4 and q["options"][q["answer_index"]] == MCQ["answer"]
    assert q["cite"] == "Net book, Chapter 3: Switching, page 41" and q["page"] == 41
    assert q["key"] == "net|chapter-3-switching|frame-forwarding"
    assert PASSAGE["text"] in model.prompts[0] and '"distractors"' in model.prompts[0]      # the shape is in the prompt


@pytest.mark.parametrize("draft", [
    dict(MCQ, quote="Switches use IP addresses to forward frames."),        # the proof is not in the passage
    dict(MCQ, distractors=["It reads the destination IP address", MCQ["answer"]]),   # not three wrong options
    "I cannot do that", RuntimeError("model down")])
def test_an_ungrounded_or_broken_draft_is_dropped_for_a_question_from_the_passage_itself(draft):
    q = engine.make_question(PASSAGE, "mcq", "net", Model(draft), siblings=OTHERS, rng=random.Random(2))
    assert q["source"] == "passage" and q["answer"] == "switch" and "_____" in q["stem"] and "switch" not in q["stem"].lower()
    assert set(q["options"]) - {"switch"} <= {"router", "hub", "VLAN", "trunk"} and len(set(q["options"])) == 4
    assert engine.grounded(q["quote"], PASSAGE["text"])


def test_without_enough_other_terms_the_fallback_is_a_written_question():
    q = engine.make_question(PASSAGE, "mcq", "net", None)
    assert q["kind"] == "short" and "switch" in q["stem"] and "options" not in q


def test_builtin_concepts_make_instant_multiple_choice_from_sibling_terms():
    passages = study_store.passages(study_seed.ID)
    dns = next(p for p in passages if p["concept"] == "DNS")
    model = Model()
    q = engine.make_question(dns, "mcq", study_seed.ID, model, siblings=passages, rng=random.Random(3))
    assert not model.prompts and q["answer"] == "DNS" and "DNS" not in q["stem"]
    networking = {t for t, _ in dict(study_seed.OUTLINE)["Networking"]}
    assert set(q["options"]) <= networking                     # wrong options come from the same chapter: plausible


def test_multiple_choice_is_graded_in_code_from_a_letter_an_ordinal_or_the_words():
    q = engine.make_question(PASSAGE, "mcq", "net", Model(MCQ), rng=random.Random(1))
    right, wrong = q["answer_index"], (q["answer_index"] + 1) % 4
    model = Model()
    assert engine.grade(q, "ABCD"[right], model)["correct"]
    assert engine.grade(q, f"I think option {'abcd'[right]}", model)["correct"]
    assert engine.grade(q, ["the first one", "the second one", "the third one", "the fourth one"][right], model)["correct"]
    assert engine.grade(q, "it looks up the destination in the mac address table", model)["correct"]
    miss = engine.grade(q, "ABCD"[wrong], model)
    assert not miss["correct"] and MCQ["answer"] in miss["feedback"] and miss["cite"].endswith("page 41")
    assert engine.grade(q, "banana", model)["understood"] is False
    assert not model.prompts                                    # the model never grades multiple choice


def test_a_written_answer_is_graded_by_the_model_against_the_reference():
    short = {"concept": "learning", "question": "How does a switch fill its table?",
             "answer": "It records the source address of each frame it receives.", "points": ["source address", "each frame"],
             "quote": "It learns the source address of every frame it receives."}
    q = engine.make_question(PASSAGE, "short", "net", Model(short))
    model = Model({"score": 1, "missing": ["that it is the SOURCE address"], "feedback": "Right idea, but say which address."})
    result = engine.grade(q, "it remembers addresses", model)
    assert result["score"] == 0.5 and not result["correct"] and result["missing"] and "which address" in result["feedback"]
    assert "it remembers addresses" in model.prompts[0] and short["answer"] in model.prompts[0]
    assert engine.grade(q, "it remembers addresses", Model({"score": 2, "feedback": "Yes."}))["correct"]


def test_without_a_model_a_written_answer_is_graded_by_what_it_covers_and_giving_up_is_never_right():
    q = engine.make_question(PASSAGE, "short", "net", None)
    assert engine.grade(q, "a switch forwards frames by using its mac address table", None)["correct"]
    assert not engine.grade(q, "it cooks dinner", Model("rubbish"))["correct"]
    ramble = "well a switch is a device and frames and tables and mac address stuff " + "and then some other things happen on the network " * 12
    rambling = engine.grade(q, ramble, None)
    assert not rambling["correct"] and rambling["score"] == 0.5           # the words are in there somewhere: partly, not right
    model = Model()
    gave_up = engine.grade(q, "I don't know", model)
    assert not gave_up["correct"] and gave_up["answer"] and not model.prompts


def test_explain_uses_the_level_and_another_way_never_repeats_an_analogy():
    first = {"explanation": "A switch is like a mail sorter.", "analogy": "a mail sorter", "limit": "Mail is slow.", "check": "Why?"}
    model = Model(first)
    out = engine.explain("switches", [PASSAGE], "elementary", model)
    assert out["source"] == "model" and out["analogy"] == "a mail sorter" and out["cite"].endswith("page 41")
    assert out["level"] == "elementary" and out["label"] == "Elementary" and out["board"]["steps"]     # a board comes with it
    assert "ELEMENTARY-SCHOOL level" in model.prompts[0] and "short story" in model.prompts[0]       # what makes the rung different
    again = Model(first, {"explanation": "A switch is like a receptionist.", "analogy": "an office receptionist", "limit": "x", "check": "y"})
    out2 = engine.explain("switches", [PASSAGE], "elementary", again, avoid=["a mail sorter"])
    assert out2["analogy"] == "an office receptionist" and len(again.prompts) == 3 and "a mail sorter" in again.prompts[0]
    assert "Here is an explanation" in again.prompts[2] and "Your last try was refused: You repeated a banned comparison" in again.prompts[1]
    # (the third call asks for the board alone: the accepted explanation had none)
    stuck = engine.explain("switches", [PASSAGE], "middle", Model(first, first), avoid=["a mail sorter"])
    assert stuck["source"] == "passage" and "MAC address table" in stuck["text"]      # the material's words, not a repeat
    assert engine.explain("switches", [PASSAGE], "college", None)["source"] == "passage"
    assert engine.explain("switches", [PASSAGE], "five", None)["level"] == "college"    # the old level names are gone: the start rung


def test_cards_are_grounded_or_built_from_definitions():
    drafts = [{"front": "What does a switch use to forward frames?", "back": "The MAC address table", "concept": "forwarding",
               "quote": "A switch is a device that forwards frames using the MAC address table."},
              {"front": "What port does HTTP use?", "back": "80", "quote": "HTTP uses port 80."}]
    cards = engine.make_cards([PASSAGE], "net", 5, Model(drafts))
    assert [c["back"] for c in cards] == ["The MAC address table"] and cards[0]["cite"].endswith("page 41")
    plain = engine.make_cards(OTHERS, "net", 3, None)
    assert [c["front"] for c in plain] == ["What does 'router' mean?", "What does 'hub' mean?", "What does 'VLAN' mean?"]
    assert len({c["id"] for c in plain}) == 3


def test_sm2_intervals_and_ease():
    today = date(2026, 3, 2)
    card = engine.sm2({}, 4, today)
    assert (card["interval"], card["reps"], card["ease"], card["due"]) == (1, 1, 2.5, "2026-03-03")
    card = engine.sm2(card, 4, today)
    assert (card["interval"], card["due"]) == (6, "2026-03-08")
    card = engine.sm2(card, 5, today)
    assert (card["interval"], card["ease"]) == (15, 2.6)                # round(6 * 2.5), ease + 0.1
    card = engine.sm2(card, 3, today)
    assert (card["interval"], card["ease"]) == (39, 2.46)               # round(15 * 2.6), ease - 0.14
    lapsed = engine.sm2(card, 1, today)
    assert (lapsed["interval"], lapsed["reps"], lapsed["lapses"], lapsed["due"]) == (1, 0, 1, "2026-03-03")
    assert engine.sm2({"ease": 1.3}, 0, today)["ease"] == 1.3           # never below the floor


def test_mastery_rises_with_right_answers_and_a_lucky_multiple_choice_counts_less():
    assert engine.bkt(0.2, True, "short") > engine.bkt(0.2, True, "mcq") > 0.2 > engine.bkt(0.2, False, "mcq") - 0.15
    p = 0.2
    for _ in range(4):
        p = engine.bkt(p, True, "short")
    assert p > 0.95
    assert engine.bkt(p, False, "short") < p and engine.bkt(0.5, False, "short") < 0.25


def test_events_from_both_machines_replay_into_one_state(pinned, monkeypatch):
    key = engine.concept_key(study_seed.ID, "Networking", "DNS")
    answer = {"type": "answer", "key": key, "course": study_seed.ID, "chapter": "Networking", "concept": "DNS", "kind": "short"}
    monkeypatch.setattr(engine, "_machine", lambda: "pc")
    engine.record(dict(answer, correct=False))
    card = engine.make_cards([p for p in study_store.passages(study_seed.ID) if p["concept"] == "DNS"], study_seed.ID)[0]
    engine.record(dict(card, type="card"))
    monkeypatch.setattr(engine, "_machine", lambda: "server")
    pinned["t"] = datetime(2026, 3, 3, 9, 0, 0)
    engine.record(dict(answer, correct=True))
    engine.record({"type": "review", "id": card["id"], "quality": 4})
    from core import storage
    assert sorted(p.name for p in (storage.user_dir() / "study").glob("events-*")) == ["events-pc.json", "events-server.json"]
    st = engine.state()
    m = st["mastery"][key]
    assert (m["n"], m["right"], m["last_correct"]) == (3, 2, True)      # two answers and the card review
    assert st["cards"][card["id"]]["due"] == "2026-03-04" and engine.due_cards(st) == []
    pinned["t"] = datetime(2026, 3, 4, 9, 0, 0)
    assert [c["id"] for c in engine.due_cards(engine.state())] == [card["id"]]


def test_progress_names_weak_concepts_and_one_right_answer_is_not_a_mastered_chapter():
    base = {"type": "answer", "course": study_seed.ID, "chapter": "Networking", "kind": "mcq"}
    for concept, results in (("DNS", [True, True, True]), ("NAT", [False]), ("Switch", [True])):
        for ok in results:
            engine.record(dict(base, concept=concept, key=engine.concept_key(study_seed.ID, "Networking", concept), correct=ok))
    prog = engine.progress(study_seed.ID)
    net = next(c for c in prog["chapters"] if c["chapter"] == "Networking")
    assert net["seen"] == 3 and net["of"] == 10 and 0 < net["mastery"] < 30 and net["weak"] == ["NAT"]
    assert [w["concept"] for w in prog["weak"]] == ["NAT"] and prog["answers"] == 5 and prog["right"] == 4
    assert next(c for c in prog["chapters"] if c["chapter"] == "Cloud")["mastery"] == 0


def test_the_exam_plan_starts_with_the_weakest_chapter_and_ends_with_a_practice_exam():
    base = {"type": "answer", "course": study_seed.ID, "chapter": "Hardware", "kind": "short", "correct": True}
    for concept in ("CPU", "RAM"):
        for _ in range(3):
            engine.record(dict(base, concept=concept, key=engine.concept_key(study_seed.ID, "Hardware", concept)))
    plan = engine.plan(study_seed.ID, "2026-03-12")
    assert plan["days_left"] == 10 and plan["order"][-1] == "Hardware" and plan["order"][0] == "Operating Systems"
    assert plan["days"][0]["date"] == "2026-03-02" and "Operating Systems" in plan["days"][0]["tasks"][0]
    assert plan["days"][-1]["date"] == "2026-03-11" and plan["days"][-1]["tasks"][0].startswith("Practice exam")
    assert any("Review Operating Systems" in t for d in plan["days"][2:] for t in d["tasks"])
    assert engine.plan(study_seed.ID, None)["days"] == [] and engine.plan(study_seed.ID, "2026-03-01")["days"] == []


def test_two_threads_recording_at_once_lose_nothing_and_an_unreadable_log_is_left_alone():
    import threading
    from core import storage
    threads = [threading.Thread(target=lambda n=n: [engine.record({"type": "answer", "key": f"k{n}-{i}", "correct": True}) for i in range(15)])
               for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(engine.events()) == 90
    path = next((storage.user_dir() / "study").glob("events-*.json"))
    path.write_text("[{\"t\": ", encoding="utf-8")
    engine.record({"type": "answer", "key": "late", "correct": True})
    assert path.read_text(encoding="utf-8") == "[{\"t\": " and engine.events() == []
