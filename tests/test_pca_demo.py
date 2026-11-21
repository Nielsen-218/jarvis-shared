"""Exercise demonstrations for Project Captain America: the video picker's rules (tools/pca_demo), its page actions, the
"day" sheet and the exercises before the start (tools/pca), and the animated figure tables of app/captain.html.

The search is a scripted recorder (tests/conftest.py already makes the real backend find nothing): no network, no model."""
import math
import re
from datetime import date, datetime
from pathlib import Path

import pytest

from core import coach_engine as coach
from core import pca_program as program
from core import storage, users
from tools import fitness, pca, pca_demo, pca_music, trainer
from tools import fitness_library as lib

MON = datetime(2026, 11, 23, 7, 0)
ANSWERS = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
           "equipment": "trap_bar,barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
           "runs_per_week": 1, "miles_per_week": 4, "weeks_since_last_run": 2, "weeks_since_last_heavy_lift": 1, "pain_level": 0,
           "medical_restriction": "no", "sickle_cell": "no", "sleep_hours": 7, "unit_pt_days": 3, "unit_pt_kind": "mixed"}
HTML = (Path(__file__).resolve().parent.parent / "app" / "captain.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(trainer, "clock", lambda: MON)
    monkeypatch.setattr(trainer, "REST", lambda seconds, line, uid: None)


def row(i, title, channel="Some Coach", dur=120):
    return {"id": f"vid{i:08d}"[:11].ljust(11, "x"), "title": title, "channel": channel, "duration_s": dur}


@pytest.fixture
def catalogue(monkeypatch):
    """The search answers from a table {query: rows}; every call is recorded."""
    box = {"rows": {}, "calls": []}

    def backend(query, n):
        box["calls"].append(query)
        return list(box["rows"].get(query, []))
    monkeypatch.setattr(pca_music, "_backend", backend)
    return box


def _begin():
    for qid, value in ANSWERS.items():
        assert pca.page_action({"action": "answer", "id": qid, "value": value}).startswith("Saved"), qid
    assert "starts today" in pca.page_action({"action": "begin"})


# ---- the rules ---------------------------------------------------------------------------------------------------------
def test_accept_needs_the_exercise_words_a_sensible_length_and_no_banned_words():
    ok = row(1, "Trap Bar Deadlift: proper form in 3 steps")
    assert pca_demo.accept(ok, "deadlift")
    assert not pca_demo.accept(dict(ok, duration_s=19), "deadlift") and pca_demo.accept(dict(ok, duration_s=20), "deadlift")
    assert not pca_demo.accept(dict(ok, duration_s=481), "deadlift") and pca_demo.accept(dict(ok, duration_s=480), "deadlift")
    assert not pca_demo.accept(dict(ok, duration_s=None), "deadlift")
    for bad in ("Deadlift FAIL compilation", "Funny deadlift moments", "Coach reacts: deadlift reaction", "Trap bar vs barbell deadlift",
                "10 minute deadlift workout", "Deadlift motivation", "Deadlift prank in the gym", "Trap bar review: is the deadlift better?",
                "500 lb deadlift challenge"):
        assert not pca_demo.accept(dict(ok, title=bad), "deadlift"), bad
    assert not pca_demo.accept(dict(ok, title="Hex bar shrug technique"), "deadlift")              # not the exercise
    assert not pca_demo.accept(dict(ok, title="Romanian deadlift proper form"), "deadlift")         # a different exercise
    assert not pca_demo.accept(dict(ok, title="Front squat proper form"), "back_squat")
    assert pca_demo.accept(dict(ok, title="How to back squat: technique"), "back_squat")
    assert not pca_demo.accept(dict(ok, title="Deadlift form #shorts", duration_s=15), "deadlift")
    assert pca_demo.accept(dict(ok, title="Deadlift form #shorts", duration_s=40), "deadlift")
    assert not pca_demo.accept(dict(ok, id="short"), "deadlift") and not pca_demo.accept(dict(ok, id="bad id here"), "deadlift")
    assert pca_demo.accept(row(2, "Hand-Release Push-Up: the Army standard"), "hand_release_pushup")
    assert not pca_demo.accept(row(2, "Push-up form for beginners"), "hand_release_pushup")
    # "workout" means a whole routine, except for a warm-up drill, where it can be the drill itself
    assert pca_demo.accept(row(3, "Arm circles warm up workout"), "arm_circles")
    assert not pca_demo.accept(row(3, "Full calf raise workout"), "calf_raise")


def test_rank_puts_a_preferred_channel_first_and_the_army_first_for_test_events():
    rows = [row(1, "Back squat proper form technique how to", "Random Gym", 90),
            row(2, "Back squat", "Squat University", 300),
            row(3, "Back squat form", "U.S. Army", 200)]
    ranked = pca_demo.rank(rows, "back_squat")
    assert ranked[0]["channel"] in ("Squat University", "U.S. Army") and ranked[-1]["channel"] == "Random Gym"
    assert [r["channel"] for r in ranked[:2]] == ["U.S. Army", "Squat University"]      # both preferred: the better title first
    hrp = [row(4, "Hand release push up technique proper form", "Jeff Nippard", 100),
           row(5, "Hand release push-up", "U.S. Army", 400), row(6, "Hand release push up how to", "Nobody", 60)]
    assert [r["channel"] for r in pca_demo.rank(hrp, "hand_release_pushup")] == ["U.S. Army", "Jeff Nippard", "Nobody"]
    same = [row(7, "Calf raise form", "Nobody", 300), row(8, "Calf raise form", "Nobody", 60)]
    assert [r["duration_s"] for r in pca_demo.rank(same, "calf_raise")] == [60, 300]       # then the shorter one
    assert pca_demo.rank(same, "calf_raise", rejected=[same[1]["id"]]) == [same[0]]
    # the same video twice (one search result without its channel): the copy that names the channel counts
    army = row(9, "ACFT Event 4 Sprint Drag Carry", "", 135)
    picks = pca_demo.rank([army, row(10, "Sprint drag carry tips and technique", "Nobody", 101), dict(army, channel="U.S. Army Holistic Health and Fitness")], "sdc")
    assert picks[0]["channel"] == "U.S. Army Holistic Health and Fitness" and len(picks) == 2
    # generic form words count at most twice, the exercise's own words more, and a clip under 45 s ranks lower
    nordic = [row(11, "Nordic Curl Tutorial - Proper Form and Technique", "A", 23), row(12, "How to do a Nordic hamstring curl", "B", 288)]
    assert pca_demo.rank(nordic, "nordic_curl")[0]["duration_s"] == 288
    assert pca_demo.channel_rank("U.S. Army Center for Initial Military Training", "sdc") == 2
    assert pca_demo.channel_rank("Usarmy fan page", "sdc") == 0


def test_every_program_key_has_searches_and_key_words():
    keys = pca_demo.known_keys()
    assert keys >= set(program.EX) | set(coach.TAGS) | {"bike", "leg_swing", "box_breathing"}
    for k in keys:
        assert pca_demo.QUERIES.get(k) or pca_demo._queries(k), k
        assert pca_demo.required(k), k
    assert pca_demo.QUERIES["deadlift"][0] == "trap bar deadlift proper form"
    assert "army" in pca_demo.QUERIES["sdc"][0] and "army" in pca_demo.QUERIES["hand_release_pushup"][0]


# ---- the API -----------------------------------------------------------------------------------------------------------
def test_demo_picks_the_best_and_searches_only_when_nothing_is_cached(catalogue):
    q1, q2 = pca_demo.QUERIES["nordic_curl"]
    catalogue["rows"] = {q1: [row(1, "Nordic hamstring curl proper form", "Nobody", 200), row(2, "Nordic curl FAIL", "Nobody", 60)],
                         q2: [row(3, "Nordic curl technique", "E3 Rehab", 240), row(4, "Nordic curl", "Nobody", 30)]}
    out = pca.page_action({"action": "demo", "key": "nordic_curl"})
    assert out["ok"] and out["video"]["channel"] == "E3 Rehab" and out["video"]["pinned"] is False
    assert out["alternatives"] == 2 and "not reviewed by a coach" in out["message"]
    assert sorted(catalogue["calls"]) == sorted([q1, q2])
    again = pca.page_action({"action": "demo", "key": "nordic_curl"})
    assert again["video"]["id"] == out["video"]["id"] and len(catalogue["calls"]) == 2      # cached: no second search
    assert pca_music.cached(q1) is None and pca_music.cached(q1, pca_demo.CACHE)             # never in the music cache


def test_next_rejects_for_good_and_pin_and_unpin(catalogue):
    q1, q2 = pca_demo.QUERIES["face_pull"]
    catalogue["rows"] = {q1: [row(1, "Face pull proper form", "ATHLEAN-X", 100), row(2, "Face pull how to", "Nobody", 90)],
                         q2: [row(3, "Cable face pull technique", "Nobody", 80)]}
    first = pca.page_action({"action": "demo", "key": "face_pull"})["video"]
    second = pca.page_action({"action": "demo_next", "key": "face_pull", "id": first["id"]})["video"]
    assert second["id"] != first["id"]
    kept = pca.page_action({"action": "demo_pin", "key": "face_pull", "id": second["id"]})
    assert kept["video"]["pinned"] is True and kept["message"].startswith("Kept")
    assert pca.page_action({"action": "demo", "key": "face_pull"})["video"] == dict(second, pinned=True)
    unpinned = pca.page_action({"action": "demo_unpin", "key": "face_pull"})
    assert unpinned["video"]["id"] == second["id"] and unpinned["video"]["pinned"] is False
    third = pca.page_action({"action": "demo_next", "key": "face_pull"})["video"]
    last = pca.page_action({"action": "demo_next", "key": "face_pull"})
    assert third["id"] not in (first["id"], second["id"]) and last["video"] is None and last["ok"] is False
    assert "No other video" in last["message"]
    for _ in range(3):                                                    # rejected ones never come back, cache or not
        assert pca.page_action({"action": "demo", "key": "face_pull"})["video"] is None
    st = storage.load(pca_demo.STATE, {})
    assert set(st["rejected"]["face_pull"]) == {first["id"], second["id"], third["id"]}


def test_a_pinned_video_that_is_rejected_is_unpinned(catalogue):
    q1, _ = pca_demo.QUERIES["dip"]
    catalogue["rows"] = {q1: [row(1, "Chest dip proper form", "Nobody", 100), row(2, "How to dip technique", "Nobody", 200)]}
    v = pca.page_action({"action": "demo", "key": "dip"})["video"]
    pca.page_action({"action": "demo_pin", "key": "dip"})
    nxt = pca.page_action({"action": "demo_next", "key": "dip"})["video"]
    assert nxt["id"] != v["id"] and not storage.load(pca_demo.STATE, {})["pinned"].get("dip")


def test_bad_keys_and_ids_are_refused(catalogue):
    for bad in ("nope", "", None, 5, ["deadlift"], "../deadlift", "DEADLIFT; drop", "x" * 300, float("nan")):
        for action in ("demo", "demo_next", "demo_pin", "demo_unpin"):
            out = pca.page_action({"action": action, "key": bad})
            assert out["ok"] is False and out["video"] is None and out["message"] == pca_demo.BAD_KEY, (action, bad)
    for bad in ("abc", 123, "zzzzzzzzzzzz", "bad id here", {"id": 1}):
        assert pca.page_action({"action": "demo_next", "key": "deadlift", "id": bad})["message"] == pca_demo.BAD_ID, bad
        assert pca.page_action({"action": "demo_pin", "key": "deadlift", "id": bad})["message"] == pca_demo.BAD_ID, bad
    assert pca.page_action({"action": "demo_pin", "key": "deadlift"})["message"].startswith("There is no video showing")
    assert catalogue["calls"] == []


def test_a_failed_search_is_one_plain_sentence(monkeypatch):
    def broken(query, n):
        raise RuntimeError("no network")
    monkeypatch.setattr(pca_music, "_backend", broken)
    out = pca.page_action({"action": "demo", "key": "deadlift"})
    assert out["ok"] is False and out["video"] is None
    assert out["message"].startswith("I could not reach YouTube search just now") and "animated figure" in out["message"]


def test_nothing_fit_is_said_plainly(catalogue):
    q1, q2 = pca_demo.QUERIES["pallof_press"]
    catalogue["rows"] = {q1: [row(1, "Pallof press FAIL compilation", "Nobody", 100)], q2: []}
    out = pca.page_action({"action": "demo", "key": "pallof_press"})
    assert out["video"] is None and out["message"].startswith("No video passed my checks")


def test_the_page_data_never_searches(catalogue):
    pca.page_data()
    _begin()
    data = pca.page_data()
    assert catalogue["calls"] == [] and "demo" not in str(data.get("audio", {}).get("dj") or "")


def test_two_people_have_their_own_picks(catalogue):
    member = users.add("demo member", {})
    q1, _ = pca_demo.QUERIES["curl"]
    catalogue["rows"] = {q1: [row(1, "Incline dumbbell curl proper form", "Nobody", 100), row(2, "Incline curl technique", "Nobody", 200)]}
    mine = pca.page_action({"action": "demo", "key": "curl"})["video"]
    pca.page_action({"action": "demo_pin", "key": "curl"})
    with users.acting_as(member):
        assert pca.page_action({"action": "demo", "key": "curl"})["video"]["id"] == mine["id"]      # the same search, their own state
        theirs = pca.page_action({"action": "demo_next", "key": "curl"})["video"]
        assert not storage.load(pca_demo.STATE, {}).get("pinned")
    assert theirs["id"] != mine["id"]
    assert pca.page_action({"action": "demo", "key": "curl"})["video"]["id"] == mine["id"]


def test_page_action_fuzz_never_raises(catalogue):
    _begin()
    junk = [None, 0, -1, 1.5, float("nan"), float("inf"), True, "", "x", [], {}, [1], {"a": 1}, "Mon" * 50, 10 ** 30]
    for action in ("demo", "demo_next", "demo_pin", "demo_unpin"):
        for k in junk:
            for vid in (None, *junk):
                out = pca.page_action({"action": action, "key": k, "id": vid})
                assert isinstance(out, dict) and isinstance(out["message"], str)
    for w in junk + [3, "3", 13, 0]:
        for d in junk + ["Fri", "fri", "Friday", "Sun", "Xyz"]:
            out = pca.page_action({"action": "day", "week": w, "day": d})
            assert isinstance(out, dict) and isinstance(out["message"], str) and (out["day"] is None or isinstance(out["day"], dict))


# ---- the day sheet and the exercises before the start --------------------------------------------------------------------
def test_day_is_the_full_planned_day_and_records_nothing():
    assert pca.page_action({"action": "day", "week": 3, "day": "Fri"})["message"].startswith("The plan appears")
    _begin()
    before_events, before_file = len(trainer.events()), storage.load(fitness.FILE, {})
    out = pca.page_action({"action": "day", "week": 3, "day": "Fri"})
    day = out["day"]
    assert out["ok"] and day["as_planned"] is True and day["week"] == 3 and day["day"] == "Fri" and day["date"] == "2026-12-11"
    assert out["message"].endswith("as planned.")
    assert day["title"] and day["why"] and day["warmup"] and day["cooldown"] and day["minutes"]
    keys = [i["key"] for i in day["items"]]
    assert keys[:2] == ["sprint", "sdc"] and "nordic_curl" in keys and "bulgarian_split_squat" in keys
    for it in day["items"]:
        assert it["purpose"] and it["progression"] and "test" in it and "bodyweight" in it, it["key"]
    assert next(i for i in day["items"] if i["key"] == "nordic_curl")["cues"]
    summary = next(d for w in pca.page_data()["program"]["weeks"] if w["week"] == 3 for d in w["days"] if d["day"] == "Fri")
    assert len(summary["items"]) == len(day["items"])
    assert len(trainer.events()) == before_events and storage.load(fitness.FILE, {}) == before_file
    assert pca.page_action({"action": "day", "week": 3, "day": "Sun"})["message"].endswith("it is a rest day.")
    assert pca.page_action({"action": "day", "week": 13, "day": "Mon"})["day"] is None
    assert pca.page_action({"action": "day", "week": 2.5, "day": "Mon"})["day"] is None
    assert pca.page_action({"action": "day", "week": "3", "day": "friday"})["day"]["day"] == "Fri"


def test_day_ignores_todays_check_in_and_pain():
    _begin()
    plain = pca.page_action({"action": "day", "week": 1, "day": "Mon"})["day"]
    fitness.log_checkin(4, 1, 5, date=MON.date().isoformat())
    pca.page_action({"action": "pain", "area": "knee", "level": 6})
    assert pca.page_action({"action": "day", "week": 1, "day": "Mon"})["day"]["items"] == plain["items"]


def test_exercises_are_there_during_onboarding_with_the_drills():
    page = pca.page_data()
    assert page["status"] == "onboarding"
    ex = page["exercises"]
    assert set(program.EX) <= set(ex) and ex["nordic_curl"]["cues"] and ex["deadlift"]["alternatives"] and ex["deadlift"]["drill"] is False
    assert ex["leg_swing"]["drill"] is True and ex["box_breathing"]["name"] and ex["bike"]["name"] == "Bike or rower"
    _begin()
    after = pca.page_data()["exercises"]
    assert {"leg_swing", "couch_stretch", "deadlift", "sdc"} <= set(after) and after["couch_stretch"]["drill"] is True


# ---- the animated figures: every key a plan can show has its own pose and phase labels ---------------------------------
def _table(name):
    block = HTML.split("/* DEMO-TABLES-BEGIN */", 1)[1].split("/* DEMO-TABLES-END */", 1)[0]
    body = block.split(f"const {name} = {{", 1)[1]
    depth, end = 1, 0
    for n, ch in enumerate(body):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            end = n
            break
    text = body[:end]
    return {m.group(1): m.start() for m in re.finditer(r"^  ([a-z_]+): ", text, re.M)}, text


def _plan_keys():
    keys = set()
    for six in (5, 6):
        for running in (False, True):
            plan = program.build({"days_per_week": six, "session_minutes": 90, "running_now": running}, date(2026, 11, 23))
            for w in plan["weeks"]:
                for d in w["days"]:
                    keys |= {i["key"] for i in d["items"]}
                    keys |= {i["exercise"] for p in d["warmup"] for i in p["items"]} | {i["exercise"] for i in d["cooldown"]}
    return keys


def test_every_key_a_plan_can_show_has_a_pose_and_phase_labels():
    poses, _ = _table("POSES")
    phases, text = _table("PHASE_LABELS")
    drills = {k for v in lib.WARMUPS.values() for _, items in v for k, _ in items} | {k for v in lib.COOLDOWNS.values() for k, _ in v}
    need = _plan_keys() | set(program.EX) | drills | set(coach.TAGS) | {"bike"}
    assert not need - set(poses), sorted(need - set(poses))                # no silent fallback to a standing figure
    assert not need - set(phases), sorted(need - set(phases))
    starts = sorted(phases.values()) + [len(text)]
    for key, at in phases.items():
        entry = text[at:starts[starts.index(at) + 1]]
        labels = re.findall(r'\["([^"]+)", \d+(?:, \d+)?\]', entry)
        assert 2 <= len(set(labels)) <= (5 if key == "sdc" else 4), (key, labels)
    sdc = text[phases["sdc"]:]
    assert all(w in sdc for w in ("Sprint", "Drag the sled backward", "Lateral shuffle", "Carry two 40 lb kettlebells"))


def test_the_page_shows_videos_only_through_the_no_cookie_player_after_a_tap():
    assert "No video yet" not in HTML
    assert HTML.count("youtube-nocookie.com/embed/") == 1 and "?rel=0&amp;playsinline=1" in HTML
    assert "chosen automatically; not reviewed by a coach" in HTML
    assert 'data-act="vid-watch"' in HTML and "Different video" in HTML and "Keep this one" in HTML and "Open in YouTube" in HTML
    assert "prefers-reduced-motion" in HTML and "figpair" in HTML                # reduced motion: two still poses side by side
    assert re.search(r'action: "day", week', HTML) and 'data-act="day-sheet"' in HTML and 'data-act="plan-ex"' in HTML


# ---- adversarial titles: sibling exercises and unsuitable content never pass (independent review, 2026-11-20) -----------
TITLES = [
    # (key, title, accepted?)
    ("nordic_curl", "Nordic walking technique", False), ("nordic_curl", "Nordic skiing technique", False),
    ("nordic_curl", "Nordic hamstring curl proper form", True), ("nordic_curl", "Nordic ham curl tutorial", True),
    ("overhead_triceps", "Overhead press and triceps form", False), ("overhead_triceps", "Overhead triceps extension form", True),
    ("overhead_triceps", "Triceps pushdown form", False), ("overhead_triceps", "Skull crusher triceps extension", False),
    ("plank", "Plank row technique", False), ("plank", "Plank jacks how to", False), ("plank", "Plank up-down form", False),
    ("plank", "Plank shoulder tap tutorial", False), ("plank", "Side plank form", False), ("plank", "Copenhagen plank form", False),
    ("plank", "Army AFT plank standard", True),
    ("deadlift", "Straight leg deadlift form", False), ("deadlift", "Stiff leg deadlift form", False), ("deadlift", "Rack pull technique deadlift", False),
    ("deadlift", "Romanian deadlift form", False), ("deadlift", "Sumo deadlift form", False), ("deadlift", "Trap bar deadlift form", True),
    ("back_squat", "Smith machine squat form", False), ("back_squat", "Safety bar squat form", False), ("back_squat", "Front-foot elevated squat", False),
    ("back_squat", "Front squat form", False), ("back_squat", "Goblet squat form", False), ("back_squat", "Bulgarian split squat form", False),
    ("back_squat", "Box squat technique", False), ("back_squat", "Overhead squat technique", False), ("back_squat", "How to back squat", True),
    ("dip", "Dip your toes: how to", False), ("dip", "Chest dip proper form", True), ("dip", "Bench dip triceps", False), ("dip", "Hip dips explained", False),
    ("run", "Run DMC interview technique", False), ("run", "Running form technique tips", True), ("run", "Run the jewels", False),
    ("bulgarian_split_squat", "Split squat form", False), ("bulgarian_split_squat", "Bulgarian split squat form", True),
    ("rdl", "Deadlift form", False), ("rdl", "Single leg RDL form", False), ("rdl", "Romanian deadlift form", True),
    ("side_plank", "Copenhagen side plank", False), ("side_plank", "Plank form", False), ("copenhagen_plank", "Side plank form", False),
    ("curl", "Nordic curl form", False), ("curl", "Leg curl machine form", False), ("curl", "Hammer curl form", False), ("curl", "Incline dumbbell curl form", True),
    ("hip_thrust", "Glute bridge form", False), ("glute_bridge", "Hip thrust form", False),
    ("lateral_raise", "Front raise form", False), ("lateral_raise", "Lateral raise form", True),
    ("face_pull", "Pull up form", False), ("pull_up", "Face pull form", False),
    ("chest_supported_row", "Bent over row form", False), ("chest_supported_row", "Upright row form", False), ("chest_supported_row", "Chest supported row form", True),
    ("row", "Upright row form", False), ("row", "Chest supported row form", False), ("row", "Bent over row form", True),
    ("pushdown", "Push up form", False), ("pushdown", "Triceps pushdown form", True),
    ("hanging_leg_raise", "Lying leg raise form", False), ("hanging_leg_raise", "Toes to bar hanging leg raise", False),
    ("calf_raise", "Calf stretch how to", False), ("calf_stretch", "Calf raise form", False), ("calf_raise", "Standing calf raise form", True),
    ("box_jump", "Broad jump form", False), ("broad_jump", "Box jump form", False),
    ("jumping_jack", "Plank jacks form", False), ("lateral_shuffle", "Shuffle dance tutorial", False), ("lateral_shuffle", "Lateral shuffle drill", True),
    ("bench_press", "Bench dip form", False), ("bench_press", "Dumbbell bench press form", False), ("bench_press", "Bench press form", True),
    ("pigeon", "Pigeon racing tips", False), ("bike", "Mountain bike technique", False), ("sled_drag", "Sled push form", False),
    # unsuitable content, any exercise
    ("deadlift", "Trap bar deadlift gone wrong", False), ("deadlift", "Worst trap bar deadlift form", False), ("deadlift", "Trap bar deadlift NSFW", False),
    ("deadlift", "Trap bar deadlift 18+", False), ("deadlift", "Man dies doing trap bar deadlift", False), ("deadlift", "Deadlift death", False),
    ("deadlift", "Deadlift injury compilation", False), ("deadlift", "Roast my trap bar deadlift form", False), ("deadlift", "1000 reps trap bar deadlift", False),
    ("deadlift", "Trap bar deadlift every day for a month", False), ("deadlift", "Trap bar deadlift for 30 days", False),
    ("deadlift", "My trap bar deadlift transformation", False), ("deadlift", "I tried the trap bar deadlift", False), ("deadlift", "Gym vlog: trap bar deadlift", False),
    ("deadlift", "What happens if you trap bar deadlift", False), ("deadlift", "Trap bar deadlift reaction", False), ("deadlift", "Ego lifting trap bar deadlift", False),
    ("deadlift", "Trap bar deadlift meme", False), ("deadlift", "Trap bar deadlift shorts challenge", False),
    ("deadlift", "Trap bar deadlift world record", True),                  # a test event: a record lift can still show the standard
    ("bench_press", "Bench press world record", False),
]


@pytest.mark.parametrize("key,title,ok", TITLES)
def test_sibling_exercises_and_unsuitable_titles_never_cross(key, title, ok):
    assert pca_demo.accept(row(1, title, "Some Coach", 120), key) is ok


def test_every_figure_passes_the_pose_validator():
    """scripts/pca_pose_check.mjs runs the page's own figure engine: 20 steps between every pair of keyframes, every bone
    within 15% of its length, nothing under the floor or off the picture, and every non-held figure really moving."""
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed here: the pose validator runs in scripts/pca_demo_browser_check.mjs")
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([node, str(root / "scripts" / "pca_pose_check.mjs")], capture_output=True, text=True, timeout=120)
    report = json.loads(out.stdout)
    assert report["checked"] >= 60 and report["failures"] == [], report["failures"]


def test_held_positions_count_like_a_timer():
    held = re.search(r"const HOLDS = \[([^\]]+)\]", HTML).group(1)
    for key in ("plank", "side_plank", "copenhagen_plank", "box_breathing", "couch_stretch", "calf_stretch", "childs_pose",
                "hamstring_stretch", "pigeon", "doorway_pec_stretch"):
        assert f'"{key}"' in held, key
    assert 'class="holdt on">Hold 0:00' in HTML


def test_sheets_trap_focus_and_close_on_back():
    for needle in ('history.pushState({ pcaSheet', 'window.addEventListener("popstate"', 'e.key !== "Tab"', "restoreFocus(",
                   'e.key === "Escape" && SHEETS.length) backSheet()'):
        assert needle in HTML, needle
