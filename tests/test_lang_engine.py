"""The language teacher's engine: curriculum, answer checking, the learner profile, lesson planning and the lesson
state machine. No model unless a fake one is passed; the clock is pinned."""
import json
from datetime import datetime, timedelta

import pytest

from core import lang_curriculum as course
from core import lang_engine as engine

NOW = datetime(2026, 3, 2, 10, 0, 0)


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(engine, "clock", lambda: state["now"])
    return state


def texts(parts):
    return " ".join(p if isinstance(p, str) else p["text"] for p in parts)


def right_answer(s):
    st = engine.step(s)
    item = s["items"][st["item"]]
    return item["english"] if st["kind"] == "meaning" else item["text"].replace("...", "Sam")


# ---- curriculum ---------------------------------------------------------------------------------------------------
def test_every_language_has_the_same_eight_units_with_complete_items():
    assert set(course.LANGUAGES) == {"fr", "es", "zh", "ja", "ht"}
    for lang in course.LANGUAGES:
        units = course.units(lang)
        assert [u["id"] for u in units] == ["greetings", "introductions", "numbers", "family", "food", "directions", "routine", "past_future"]
        for u in units:
            assert len(u["items"]) >= 7 and u["tip"] or u["id"] == "numbers"
            assert u["reply"] in course.items(lang) and (u["ask"] is None or u["ask"] in course.items(lang))
            for item in u["items"]:
                assert item["text"] and item["english"] and item["accept"], item
                if lang in ("zh", "ja"):
                    assert item["roman"] and engine._cjk(item["text"]), item
                else:
                    assert not engine._cjk(item["text"]), item


def test_mandarin_has_tone_marks_japanese_has_kana_and_creole_its_own_spelling():
    zh, ja, ht = course.items("zh"), course.items("ja"), course.items("ht")
    assert zh["zh:hello"]["roman"] == "nǐ hǎo" and zh["zh:thanks"]["text"] == "谢谢"
    assert ja["ja:hello"]["text"] == "こんにちは" and ja["ja:hello"]["roman"] == "konnichiwa"
    assert ht["ht:thanks"]["text"] == "Mèsi" and ht["ht:hungry"]["text"] == "Mwen grangou"
    assert "pral" in ht["ht:go_tomorrow"]["text"] and " te " in ht["ht:i_ate"]["text"]


# ---- answer checking ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lang,key,said", [
    ("es", "how_are_you", "como estas"),                 # no accents, no question marks
    ("es", "how_are_you", "¿Cómo estás?"),
    ("es", "thanks", "Gracias."),
    ("es", "thanks", "it's gracias"),
    ("es", "two", "2"),                                  # a recogniser writes the digit
    ("fr", "mother", "mère"),                            # the article is optional
    ("fr", "please", "s'il vous plait"),
    ("fr", "water_please", "je voudrais de l'eau s'il vous plaît"),
    ("ht", "thanks", "mesi"),
    ("ht", "how_are_you", "kijan ou ye"),
    ("ht", "sorry", "eskize mwen"),
    ("zh", "hello", "你好"),
    ("zh", "hello", "ni hao"),                           # typed pinyin without tones
    ("zh", "hello", "nǐhǎo"),
    ("ja", "hello", "こんにちは"),
    ("ja", "hello", "konnichiwa"),
    ("ja", "how_are_you", "お元気ですか"),                 # the recogniser writes kanji
    ("ja", "water", "水"),
    ("es", "my_name_is", "me llamo Sam"),                # an opening with his own ending
    ("ja", "i_am_from", "アメリカから来ました"),
])
def test_right_answers_are_taken_however_the_recogniser_spells_them(lang, key, said):
    assert engine.check_target(course.items(lang)[f"{lang}:{key}"], said)["correct"], said


@pytest.mark.parametrize("lang,key,said", [
    ("es", "thanks", "hola"), ("es", "two", "tres"), ("fr", "mother", "le père"), ("ht", "thanks", "bonjou"),
    ("zh", "hello", "谢谢"), ("ja", "hello", "さようなら"), ("es", "thanks", ""), ("es", "one", "un"),
])
def test_wrong_answers_are_not_taken(lang, key, said):
    assert not engine.check_target(course.items(lang)[f"{lang}:{key}"], said)["correct"], said


def test_a_near_miss_is_right_but_flagged_so_the_form_is_said_back():
    v = engine.check_target(course.items("es")["es:good_night"], "buenas noche")
    assert v["correct"] and v["close"]
    v = engine.check_target(course.items("es")["es:good_night"], "buenas tardes")
    assert not v["correct"] and v["near"]


@pytest.mark.parametrize("said,ok", [("thank you", True), ("it means thanks", True), ("I think it's thank you", True),
                                     ("hello", False), ("please", False), ("", False)])
def test_meaning_check(said, ok):
    assert engine.check_meaning(course.items("es")["es:thanks"], said)["correct"] is ok


def test_meaning_check_reads_contractions():
    item = course.items("fr")["fr:hungry"]
    assert engine.check_meaning(item, "I'm hungry")["correct"] and engine.check_meaning(item, "i am hungry")["correct"]
    assert not engine.check_meaning(item, "I am lost")["correct"]


def test_giving_up_is_recognised():
    assert engine.gave_up("I don't know") and engine.gave_up("no idea") and not engine.gave_up("no entiendo")


# ---- the learner profile ------------------------------------------------------------------------------------------
def test_events_fold_into_a_profile_with_spaced_repetition(pinned):
    engine.record({"type": "item", "lang": "es", "item": "es:hello", "correct": True, "q": 5})
    engine.record({"type": "item", "lang": "es", "item": "es:thanks", "correct": False, "q": 1})
    engine.record({"type": "item", "lang": "fr", "item": "fr:hello", "correct": True, "q": 5})
    prof = engine.profile("es")
    assert prof["items"]["es:hello"]["due"] == "2026-03-03" and prof["items"]["es:hello"]["right"] == 1
    assert prof["items"]["es:thanks"]["wrong"] == 1 and "fr:hello" not in prof["items"]
    assert [i["id"] for i in engine.weak_items("es", prof)] == ["es:thanks"]
    assert engine.due_items("es", prof) == []                                    # nothing is due the same day
    pinned["now"] = NOW + timedelta(days=1)
    assert [i["id"] for i in engine.due_items("es", engine.profile("es"))] == ["es:thanks", "es:hello"]     # the weak one first
    pinned["now"] = NOW + timedelta(days=1)
    engine.record({"type": "item", "lang": "es", "item": "es:hello", "correct": True, "q": 5})
    assert engine.profile("es")["items"]["es:hello"]["due"] == "2026-03-09"      # second success: six days


def test_a_damaged_log_and_junk_rows_never_break_the_profile(tmp_path):
    from core import storage
    folder = storage.user_dir() / "languages"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "events-other.json").write_text("{not json", encoding="utf-8")
    (folder / "events-third.json").write_text(json.dumps([{"type": "item"}, 7, {"type": "item", "lang": "es", "item": "es:hello",
                                                                                 "correct": True, "q": "x", "t": "2026-03-01T09:00:00"}]), encoding="utf-8")
    prof = engine.profile("es")
    assert prof["items"]["es:hello"]["right"] == 1


def test_progress_names_the_level_units_due_and_weak(pinned):
    assert engine.progress("ja")["level"] == "Not started"
    for item in course.units("ja")[0]["items"]:
        engine.record({"type": "item", "lang": "ja", "item": item["id"], "correct": True, "q": 5})
    engine.record({"type": "item", "lang": "ja", "item": "ja:your_name", "correct": False, "q": 1})
    pinned["now"] = NOW + timedelta(days=2)
    p = engine.progress("ja")
    assert p["level"] == "Beginner, unit 2 of 8" and p["unit"]["id"] == "introductions"
    assert p["units"][0]["seen"] == p["units"][0]["total"] and p["units"][1]["seen"] == 1
    assert len(p["due"]) == len(course.units("ja")[0]["items"]) + 1 and p["due"][0]["id"] == "ja:your_name"
    assert p["weak"][0]["id"] == "ja:your_name" and p["weak"][0]["roman"]


def test_lessons_build_a_streak(pinned):
    for day in (0, 1, 2, 5):
        pinned["now"] = NOW + timedelta(days=day)
        engine.record({"type": "lesson", "lang": "fr", "minutes": 10, "right": 8, "of": 10})
    prof = engine.profile("fr")
    assert prof["lessons"] == 4 and prof["minutes"] == 40 and prof["streak"] == 1 and prof["accuracy"] == 0.8


# ---- planning -----------------------------------------------------------------------------------------------------
def test_a_first_lesson_teaches_then_practises_then_talks():
    steps = engine.plan("es", 10, engine.profile("es"))
    phases = [s["phase"] for s in steps]
    assert phases[0] == "new" and "warmup" not in phases and phases[-1] == "talk"
    assert phases == sorted(phases, key=["warmup", "new", "guided", "talk"].index)
    new = [s["item"] for s in steps if s["phase"] == "new"]
    assert new[:2] == ["es:hello", "es:thanks"]                                   # the course order
    for iid in new:                                                               # everything taught is practised both ways
        assert {s["kind"] for s in steps if s["item"] == iid and s["phase"] == "guided"} >= {"meaning", "say"}


def test_a_lesson_is_sized_to_the_minutes():
    prof = engine.profile("fr")
    short, long = engine.plan("fr", 5, prof), engine.plan("fr", 20, prof)
    assert len(short) <= 10 < len(long) <= 40
    assert sum(s["phase"] == "new" for s in short) < sum(s["phase"] == "new" for s in long)
    assert sum(s["phase"] == "talk" for s in long) == 2


def test_a_later_lesson_warms_up_with_due_and_weak_items_and_moves_on(pinned):
    for item in course.units("es")[0]["items"][:4]:
        engine.record({"type": "item", "lang": "es", "item": item["id"], "correct": item["key"] != "please", "q": 5 if item["key"] != "please" else 1})
    pinned["now"] = NOW + timedelta(days=1)
    steps = engine.plan("es", 10, engine.profile("es"))
    warm = [s["item"] for s in steps if s["phase"] == "warmup"]
    assert warm[0] == "es:please" and set(warm) <= {"es:hello", "es:thanks", "es:please", "es:how_are_you"}
    new = [s["item"] for s in steps if s["phase"] == "new"]
    assert new and new[0] == "es:im_fine"                                         # the first item he has not met


def test_difficulty_adapts_to_the_last_lesson(pinned):
    def new_count(right):
        engine.record({"type": "lesson", "lang": "fr", "minutes": 20, "right": right, "of": 10})
        return sum(s["phase"] == "new" for s in engine.plan("fr", 20, engine.profile("fr")))
    strong = new_count(10)
    weak = new_count(3)
    assert strong > weak >= 1


def test_from_zero_starts_at_the_first_unit_even_with_history():
    for item in course.units("ja")[0]["items"]:
        engine.record({"type": "item", "lang": "ja", "item": item["id"], "correct": True, "q": 5})
    steps = engine.plan("ja", 10, engine.profile("ja"), from_zero=True)
    assert steps[0] == {"phase": "new", "kind": "repeat", "item": "ja:hello"}


# ---- the lesson ---------------------------------------------------------------------------------------------------
def test_a_whole_lesson_runs_to_the_recap_and_is_remembered(pinned):
    s = engine.start("es", 5)
    said = texts(engine.opening(s))
    assert "Spanish from the very beginning" in said and "Hola" in said
    seen = []
    for _ in range(60):
        seen.append(engine.phase(s))
        out = engine.answer(s, right_answer(s))
        if out["done"]:
            break
    assert out["done"] and "That's the lesson" in texts(out["parts"]) and "New today" in texts(out["parts"])
    assert [p for n, p in enumerate(seen) if n == 0 or seen[n - 1] != p] == ["new", "guided", "talk"]
    prof = engine.profile("es")
    assert prof["lessons"] == 1 and prof["accuracy"] == 1.0 and "es:hello" in prof["items"]
    assert engine.answer(s, "hola")["done"]                                       # nothing happens twice
    assert engine.profile("es")["lessons"] == 1


def test_the_clock_ends_the_lesson_on_time(pinned):
    s = engine.start("fr", 10)
    engine.opening(s)
    assert not engine.answer(s, right_answer(s))["done"]
    pinned["now"] = NOW + timedelta(minutes=10, seconds=1)
    out = engine.answer(s, right_answer(s))
    assert out["done"] and "That's the lesson" in texts(out["parts"])


def test_a_miss_gets_a_recast_first_and_the_reason_when_it_repeats(pinned):
    s = engine.start("es", 10)
    engine.opening(s)
    while engine.step(s)["kind"] != "say":
        engine.answer(s, right_answer(s))
    item = s["items"][engine.step(s)["item"]]
    first = engine.answer(s, "no se que")
    assert first.get("retry") and item["text"] in texts(first["parts"]) and "Once more" in texts(first["parts"])
    assert [p for p in first["parts"] if isinstance(p, dict)][0]["slow"]          # the recast is said slowly
    at = s["i"]
    second = engine.answer(s, "no se que")
    assert s["i"] == at + 1 and not second.get("retry")
    tip = course.units("es")[0]["tip"]
    assert tip in texts(second["parts"])                                          # the same slip again: a short English reason
    assert sum(1 for st in s["steps"][s["i"]:] if st["item"] == item["id"] and st.get("again")) == 1     # and it comes back
    prof = engine.profile("es")
    assert prof["items"][item["id"]]["wrong"] == 1 and prof["items"][item["id"]]["last_correct"] is False


def test_right_on_the_second_try_counts_as_shaky(pinned):
    s = engine.start("es", 10)
    engine.opening(s)
    item = s["items"][engine.step(s)["item"]]
    assert engine.answer(s, "blah blah").get("retry")
    out = engine.answer(s, item["text"])
    assert out["correct"] and s["right"] == 0 and s["of"] == 1
    assert engine.profile("es")["items"][item["id"]]["interval"] == 1


def test_the_reason_can_come_from_the_model_and_bad_model_output_is_ignored(pinned):
    def run(model):
        s = engine.start("fr", 10)
        engine.opening(s)
        engine.answer(s, "zzz", model)
        return texts(engine.answer(s, "zzz", model)["parts"])
    assert "Drop the final t sound." in run(lambda p: 'Sure! {"why": "Drop the final t sound"}')
    tip = course.units("fr")[0]["tip"]
    assert tip in run(lambda p: "I cannot answer that")
    assert tip in run(lambda p: (_ for _ in ()).throw(RuntimeError("down")))


def test_i_dont_know_gives_the_answer_without_a_second_try(pinned):
    s = engine.start("ht", 10)
    engine.opening(s)
    out = engine.answer(s, "I don't know")
    assert not out.get("retry") and "Bonjou" in texts(out["parts"]) and s["i"] == 1


def test_conversation_takes_anything_and_uses_the_model_when_it_is_there(pinned):
    def to_talk(model):
        s = engine.start("es", 5)
        engine.opening(s)
        while engine.step(s)["kind"] != "talk":
            engine.answer(s, right_answer(s))
        return s, engine.answer(s, "yo bien", model)
    s, out = to_talk(None)
    assert out["done"] and "Estoy bien" in texts(out["parts"])                    # no model: one good way to say it
    reply = json.dumps({"recast": "Estoy bien.", "reply": "¡Muy bien!", "english": "Very good!"})
    s, out = to_talk(lambda p: reply)
    said = texts(out["parts"])
    assert "You could say:" in said and "Estoy bien." in said and "¡Muy bien!" in said and "Very good" in said
    s, out = to_talk(lambda p: '{"reply": "<script>"}')
    assert "Estoy bien" in texts(out["parts"])


def test_skip_and_repeat(pinned):
    s = engine.start("ja", 10)
    engine.opening(s)
    again = engine.repeat(s, slower=True)
    assert "こんにちは" in texts(again) and all(p["slow"] for p in again if isinstance(p, dict))
    out = engine.skip(s)
    assert "Skipped." in texts(out["parts"]) and s["i"] == 1 and "ja:hello" not in engine.profile("ja")["items"]


# ---- looking things up --------------------------------------------------------------------------------------------
def test_phrases_are_found_both_ways_and_the_model_fills_the_gaps():
    assert engine.find_phrase("ht", "thank you")["text"] == "Mèsi"
    assert engine.find_phrase("zh", "How are you?")["roman"] == "nǐ hǎo ma"
    assert engine.find_phrase("es", "the spaceship is late") is None
    assert engine.find_meaning("es", "buenas noches")["english"] == "good night"
    assert engine.find_meaning("es", "zapato") is None
    got = engine.translate("es", "shoe", False, lambda p: '{"text": "el zapato", "english": "the shoe"}')
    assert got == {"text": "el zapato", "roman": "", "english": "the shoe"}
    assert engine.translate("es", "shoe", False, lambda p: "no") is None and engine.translate("es", "shoe", False, None) is None


def test_his_own_words_join_the_review(pinned):
    engine.record({"type": "word", "lang": "es", "text": "el zapato", "english": "the shoe"})
    prof = engine.profile("es")
    due = engine.due_items("es", prof)
    assert [i["text"] for i in due] == ["el zapato"]
    steps = engine.plan("es", 10, prof)
    assert steps[0]["phase"] == "warmup" and steps[0]["item"] == due[0]["id"]
    assert engine.find_phrase("es", "shoe", prof)["text"] == "el zapato"
