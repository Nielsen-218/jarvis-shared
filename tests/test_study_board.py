"""The tutor's explanation ladder and whiteboard: rungs, scenes, routing, routes and the page (no model, tmp data)."""
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import study_board as board
from core import study_engine as engine
from core import study_ladder as ladder
from core import study_seed, studyintent, users
from tools import registry, study

APP = Path(__file__).resolve().parent.parent / "app"
NETWORKING = [term for chapter, items in study_seed.OUTLINE if chapter == "Networking" for term, _ in items]


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No real model, a pinned clock, nothing opened on the screen, no state left from another test."""
    def down(prompt):
        raise RuntimeError("no model in tests")
    monkeypatch.setattr(study, "MODEL", down)
    monkeypatch.setattr(engine, "clock", lambda: datetime(2026, 3, 2, 10, 0, 0))
    opened = []
    monkeypatch.setattr(study, "OPEN_BOARD", lambda: bool(study.at_pc.get()) and (opened.append(1) or True))     # as the real one: never for a phone turn
    for state in (study._sessions, study._explained, study._follow, study._boards):
        state.clear()
    yield opened
    for state in (study._sessions, study._explained, study._follow, study._boards):
        state.clear()


def run(name, **args):
    out, is_error = registry.run(name, args)
    assert not is_error, out
    return out


# ---- the ladder -----------------------------------------------------------------------------------------------------
def test_six_rungs_in_order_and_a_move_is_one_rung():
    assert ladder.RUNGS == ("college", "high", "middle", "elementary", "prek", "one") and ladder.START == "college"
    walk = ["college"]
    for _ in range(7):
        walk.append(ladder.move(walk[-1], "simpler"))
    assert walk == ["college", "high", "middle", "elementary", "prek", "one", "one", "one"]      # one at a time, never past the bottom
    assert ladder.move("elementary", "deeper") == "middle" and ladder.move("college", "deeper") == "college"
    assert ladder.move("college", "one") == "one" and ladder.move("one", "middle") == "middle"   # a named rung is taken as named
    assert ladder.move("prek", "") == "prek" and ladder.move("", "") == "college" and ladder.move("high", "nonsense") == "high"
    assert ladder.norm("five") == "simpler" and ladder.norm("exam") == "college" and ladder.norm("beginner") == "middle"   # the old three levels


@pytest.mark.parametrize("said, rung", [
    ("explain it like I'm in college", "college"), ("like I'm a college student", "college"), ("for the exam", "college"),
    ("like I'm in high school", "high"), ("at a high school level", "high"),
    ("like I'm in middle school", "middle"), ("like I am a middle schooler", "middle"),
    ("like I'm in elementary school", "elementary"), ("like I'm in third grade", "elementary"),
    ("like I'm in pre-k", "prek"), ("like I'm a preschooler", "prek"), ("like I'm three", "prek"),
    ("like I'm one", "one"), ("like I was 1 years old", "one"), ("like I'm a one year old", "one"), ("like I'm a baby", "one"),
])
def test_a_rung_he_names(said, rung):
    assert ladder.named(said) == rung and ladder.level_in(said) == rung


def test_unnamed_requests_move_one_step_and_ordinary_words_name_nothing():
    for said in ("in simple terms", "simpler please", "dumb it down"):
        assert ladder.named(said) == "" and ladder.level_in(said) == "simpler", said
    for said in ("more detail", "go deeper", "explain it properly", "in more depth"):
        assert ladder.level_in(said) == "deeper", said
    for said in ("like I'm one of the team", "I'm in college now", "the high school is closed", "explain DNS", "like I'm 12 minutes late"):
        assert ladder.level_in(said) == "", said


def test_every_networking_concept_has_six_distinct_hand_written_rungs():
    assert len(NETWORKING) == 10 and set(NETWORKING) == set(ladder.HAND)
    for term in NETWORKING:
        rungs, written = ladder.builtin(term)
        assert written and tuple(rungs) == ladder.RUNGS
        texts = list(rungs.values())
        assert len({t.lower() for t in texts}) == 6, term
        for a in range(6):                                        # genuinely different: no rung is another one trimmed
            for b in range(a + 1, 6):
                assert texts[b] not in texts[a] and texts[a][:60] != texts[b][:60], (term, a, b)
        words = [len(t.split()) for t in texts]
        assert words[0] > 50 and words[4] <= 20 and words[5] <= 12, (term, words)      # college is full, pre-K tiny, one a handful
        assert "(" in rungs["one"] and "Worked example" in rungs["high"] and "new term" in rungs["middle"], term
        assert not re.search(r"\b(?:IP|DNS|DHCP|TCP|UDP|NAT|MAC|router|switch|packet|address)\b", rungs["prek"] + rungs["one"], re.I) \
            or term in ("IP address",), term                      # nothing technical at the bottom (IP address keeps "number")


def test_other_built_in_concepts_are_templated_and_say_so():
    rungs, written = ladder.builtin("RAM", "RAM is the fast working memory. It is volatile.", "Hardware")
    assert not written and len(set(rungs.values())) == 6 and "RAM" in rungs["one"] and "(" in rungs["one"]
    passage = {"id": "seed:hardware:ram", "chapter": "Hardware", "section": "RAM", "page": None, "concept": "RAM", "material": "Concept outline",
               "text": "RAM is the fast working memory a computer uses. It is volatile, so everything in it is lost when the power goes off."}
    out = engine.explain("RAM", [passage], "elementary", None)
    assert out["source"] == "builtin" and out["templated"] is True and out["board"]["source"] == "fallback"


# ---- the ladder in the skills ---------------------------------------------------------------------------------------
def test_the_ladder_moves_one_rung_per_request_and_is_remembered_per_concept():
    first = run("study_explain", topic="DNS")
    assert first.startswith("DNS is the hierarchical, distributed naming system")              # college, no model needed
    down1 = run("study_explain_again", level="simpler")
    assert down1.startswith("High-school level. DNS turns a name") and "Worked example" in down1
    down2 = run("study_explain_again", level="simpler")
    assert down2.startswith("Middle-school level. DNS is like the contacts list")
    assert run("study_explain", topic="DHCP").startswith("DHCP assigns IP configuration")         # another concept starts at the top
    study._explained.clear()                                                                     # the memory is on disk, not in the session
    assert study.explain("DNS")["level"] == "middle"                                             # the Study page picks up where he left it
    assert run("study_explain", topic="DNS", level="deeper").startswith("High-school level.")
    assert run("study_explain", topic="DNS", level="one").startswith("One-year-old level. DNS! Where's doggy")
    said = run("study_explain_again", level="simpler")                                           # past the bottom: kind, no repeat, the picture offered
    assert said.startswith("That is already as simple as I can make it") and "whiteboard" in said and "doggy" not in said
    assert engine.state()["rungs"] == {"dns": "one", "dhcp": "college"}
    assert [e["rung"] for e in engine.events() if e["type"] == "rung" and e["key"] == "dns"] == ["college", "high", "middle", "high", "one"]


def test_the_rung_is_per_person():
    run("study_explain", topic="router", level="prek")
    wife = users.add("Marie", {"en": "madame"}, "wife")
    with users.acting_as(wife):
        assert engine.state()["rungs"] == {}
        assert study.explain("router")["level"] == "college"
    assert study.explain("router")["level"] == "prek"


def test_explain_this_after_a_quiz_answer_gets_the_ladder_and_a_board():
    run("study_quiz", course="it fundamentals", chapter="networking", count=1, kind="mcq")
    q = study.session()["q"]
    run("study_answer", answer="ABCD"[q["answer_index"]])
    out = study.explain("")
    assert out["concept"] == q["concept"] and out["level"] == "college" and out["board"]["source"] == "hand"
    assert study.explain("", "simpler", another=True)["level"] == "high"


def test_the_model_prompt_states_the_rung_and_forbids_the_previous_comparison(monkeypatch):
    prompts = []
    replies = [{"explanation": "A firewall is like a doorman with a guest list. It checks every visitor.", "analogy": "a doorman",
                "board": {"steps": [{"say": "A doorman checks.", "ops": [{"op": "icon", "id": "d", "icon": "person", "text": "Doorman"},
                                                                         {"op": "box", "id": "net", "text": "Network"},
                                                                         {"op": "arrow", "from": "d", "to": "net", "text": "lets in"}]}]}},
               {"explanation": "Once there was a castle with one gate. The guard only opened it for friends.", "analogy": "a castle gate"}]

    def model(prompt):
        prompts.append(prompt)
        return json.dumps(replies[len(prompts) - 1])
    monkeypatch.setattr(study, "MODEL", model)
    first = study.explain("firewall", "middle")
    assert first["source"] == "model" and first["board"]["source"] == "repaired" and any(o["op"] == "arrow" for s in first["board"]["steps"] for o in s["ops"])
    assert ladder.RULES["middle"] in prompts[0] and "ONE everyday comparison" in prompts[0] and "icons: computer, phone, router" in prompts[0]
    second = study.explain("firewall", "simpler")
    assert second["level"] == "elementary" and ladder.RULES["elementary"] in prompts[1] and "short story" in prompts[1]
    assert "A firewall is like a doorman with a guest list." in prompts[1] and "exactly ONE step simpler" in prompts[1]
    assert '"a doorman"' not in prompts[1]        # stepping down is not "I did not follow that picture": only another way at the SAME rung bans it
    assert second["board"]["source"] == "fallback" and second["board"]["steps"]   # the model drew nothing: the board is built in code


# ---- scenes ---------------------------------------------------------------------------------------------------------
def _ids(scene):
    return [op["id"] for s in scene["steps"] for op in s["ops"] if op["op"] in ("box", "circle", "icon", "label")]


def test_validation_drops_unknown_ops_and_missing_targets():
    scene = board.validate({"steps": [{"say": "One.", "ops": [
        {"op": "box", "id": "a", "text": "A"}, {"op": "icon", "id": "b", "icon": "server", "text": "B"},
        {"op": "script", "src": "x"}, {"op": "foreignObject", "html": "<b>x</b>"}, {"op": "explode", "target": "a"},
        {"op": "arrow", "from": "a", "to": "ghost"}, {"op": "arrow", "from": "a", "to": "a"}, {"op": "arrow", "from": "a", "to": "b", "text": "ok"},
        {"op": "arrow", "from": "a", "to": "b", "text": "again"},
        {"op": "highlight", "target": "nobody"}, {"op": "move", "path": ["a", "ghost"]}, {"op": "move", "path": ["a", "ghost", "b"], "text": "pkt"},
        {"op": "erase", "target": "ghost"}, {"op": "bounce", "target": "b"}, "not an op", None, 7]}]})
    ops = scene["steps"][0]["ops"]
    assert [o["op"] for o in ops] == ["box", "icon", "arrow", "move", "bounce"]
    assert ops[2] == {"op": "arrow", "id": "a-b", "from": "a", "to": "b", "text": "ok"} and ops[3]["path"] == ["a", "b"]


def test_validation_clamps_numbers_and_keeps_every_shape_on_the_board():
    scene = board.validate({"steps": [{"say": "x", "ops": [
        {"op": "box", "id": "a", "x": 9e12, "y": -5000, "w": 1e9, "h": 0, "text": "far"},
        {"op": "circle", "id": "b", "x": float("nan"), "y": float("inf"), "r": 1e6, "text": "nan"},
        {"op": "icon", "id": "c", "icon": "cloud", "x": "200", "y": True, "size": -3},
        {"op": "line", "id": "l", "x1": -99999, "y1": 10, "x2": 1e9, "y2": 99999}]}]}, "college")
    shapes = {o["id"]: o for o in scene["steps"][0]["ops"]}
    for s in (shapes["a"], shapes["b"], shapes["c"]):
        assert 0 <= s["x"] <= board.W and 0 <= s["y"] <= board.H
    assert shapes["a"]["w"] <= 640 and shapes["a"]["h"] >= 44 and shapes["b"]["r"] <= 220 and 50 <= shapes["c"]["size"] <= 340
    assert shapes["l"] == {"op": "line", "id": "l", "x1": 0.0, "y1": 10.0, "x2": 1000.0, "y2": 560.0}
    assert json.dumps(scene)                                                   # nothing that is not plain JSON (no NaN, no inf)
    assert "NaN" not in json.dumps(scene) and "Infinity" not in json.dumps(scene)


def test_validation_strips_markup_and_script_from_every_text():
    scene = board.validate({"steps": [{"say": "<img src=x onerror=alert(1)>Hello <b>there</b>", "ops": [
        {"op": "box", "id": "a", "text": "<script>alert(1)</script>"}, {"op": "box", "id": "b", "text": "<b>Router</b> & <i>switch</i>"},
        {"op": "box", "id": "c", "text": "javascript:alert(1)"}, {"op": "label", "id": "d", "text": "x" * 500},
        {"op": "icon", "id": "<svg onload=x>", "icon": "computer", "text": {"nested": "object"}},
        {"op": "arrow", "from": "a", "to": "b", "text": "a < b > c \x00"}]}]})
    flat = json.dumps(scene)
    assert "<" not in flat and ">" not in flat and "script" not in flat.lower() and "onerror" not in flat and "javascript" not in flat
    ops = scene["steps"][0]["ops"]
    assert ops[0]["text"] == "" and ops[1]["text"] == "Router & switch" and ops[2]["text"] == "" and len(ops[3]["text"]) == 40
    assert re.fullmatch(r"[a-z0-9_-]+", ops[4]["id"]) and ops[4]["text"] == ""
    assert scene["steps"][0]["say"] == ""                                       # a caption that carried markup with a handler is dropped whole


def test_validation_refuses_what_is_not_a_scene_and_limits_its_size():
    for junk in (None, "draw a cat", 42, [], {}, {"steps": "x"}, {"steps": [{"ops": [{"op": "highlight", "target": "a"}]}]},
                 {"steps": [{"say": "only talk", "ops": []}]}, {"steps": [{"ops": [{"op": "icon", "icon": "unicorn"}]}]}):
        assert board.validate(junk) is None, junk
    many = {"steps": [{"say": f"s{n}", "ops": [{"op": "box", "id": f"b{n}{k}", "text": "x"} for k in range(30)]} for n in range(40)]}
    scene = board.validate(many, "college")
    assert len(scene["steps"]) <= board.MAX_STEPS and all(len(s["ops"]) <= board.MAX_OPS for s in scene["steps"])
    assert len(_ids(scene)) <= board.CAP["college"]
    assert len(_ids(board.validate(many, "one"))) == 2 and len(_ids(board.validate(many, "prek"))) == 4       # simpler rung, fewer shapes
    icon = board.validate({"steps": [{"ops": [{"op": "icon", "id": "u", "icon": "unicorn", "text": "Unicorn"}]}]})
    assert icon["steps"][0]["ops"][0]["op"] == "box" and icon["steps"][0]["ops"][0]["text"] == "Unicorn"       # repaired: its name in a box


def test_the_models_shapes_are_laid_out_in_code_without_overlap():
    draft = {"steps": [{"say": "a", "ops": [{"op": "box", "id": f"s{n}", "x": 500, "y": 280, "text": f"S{n}"} for n in range(5)]}]}
    for layout in ("auto", "force"):
        solid = board.validate(draft, "college", layout)["steps"][0]["ops"]
        for n, a in enumerate(solid):
            for b in solid[n + 1:]:
                assert abs(a["x"] - b["x"]) >= (a["w"] + b["w"]) / 2 or abs(a["y"] - b["y"]) >= (a["h"] + b["h"]) / 2, (a, b)
    made = board.from_model({"steps": [{"ops": [{"op": "circle", "id": "x", "text": "X"}]}, {"say": "mine", "ops": [{"op": "bounce", "target": "x"}]}]},
                            "one", "First sentence. Second sentence.")
    assert [s["say"] for s in made["steps"]] == ["First sentence.", "mine"] and made["source"] == "model"
    assert board.from_model({"steps": []}, "college", "x") is None and board.from_model("nope", "college", "x") is None


def test_the_fallback_scene_is_built_from_the_explanations_key_terms():
    text = ("A firewall filters network traffic against a set of rules. It allows what is expected and blocks the rest. "
            "It sits at the edge of a network or on a single computer.")
    scene = board.fallback("Firewall", text, "college")
    ops = [op for s in scene["steps"] for op in s["ops"]]
    assert scene["source"] == "fallback" and ops[0]["op"] == "box" and ops[0]["text"] == "Firewall"
    terms = [o["text"] for o in ops if o["op"] == "box"][1:]
    assert 2 <= len(terms) <= 5 and all(t.lower() in text.lower() and t.lower() != "firewall" for t in terms)
    assert all(o["from"] == "main" for o in ops if o["op"] == "arrow") and [s["say"] for s in scene["steps"]] == board.sentences(text)
    assert board.validate(scene, "college", "keep")["steps"] == scene["steps"]          # already valid: validating again changes nothing
    baby = board.fallback("Firewall", "Door! Closed! (push the door)", "one")
    shapes = [o for s in baby["steps"] for o in s["ops"]]
    assert [o["op"] for o in shapes] == ["circle", "bounce"] and shapes[0]["r"] >= 150  # one big shape and a bounce
    assert board.fallback("", "", "middle")["steps"]                                    # never empty, whatever it is given
    assert board.fallback("<script>x</script>", "<b>bold</b> words everywhere here", "high")["steps"][0]["ops"][0]["text"] == "This idea"


def test_every_networking_concept_has_a_hand_made_board_that_gets_simpler_down_the_ladder():
    assert set(board.HAND) == set(NETWORKING)
    for term in NETWORKING:
        counts = {}
        for rung in ladder.RUNGS:
            text = ladder.HAND[term][rung]
            scene = board.builtin(term, rung, text)
            assert scene["source"] == "hand" and scene["rung"] == rung
            assert board.validate(scene, rung, "keep")["steps"] == scene["steps"], (term, rung)     # it passes the same gate as the model's
            assert all(s["say"] and s["ops"] for s in scene["steps"]), (term, rung)
            assert " ".join(s["say"] for s in scene["steps"]) == " ".join(board.sentences(text))    # the captions ARE the explanation
            solid = [op for s in scene["steps"] for op in s["ops"] if op["op"] in ("box", "circle", "icon")]
            counts[rung] = len(solid)
            for op in solid:
                assert 0 < op["x"] < board.W and 0 < op["y"] < board.H
        assert counts["college"] >= counts["elementary"] >= counts["prek"] >= counts["one"], (term, counts)
        assert counts["one"] <= 2 and counts["prek"] <= 2, (term, counts)
        drawn = {r: sum(len(s["ops"]) for s in board.builtin(term, r, ladder.HAND[term][r])["steps"]) for r in ("college", "elementary", "one")}
        assert drawn["college"] >= drawn["elementary"] > drawn["one"], (term, drawn)                # never more on a simpler board
        baby = board.builtin(term, "one", ladder.HAND[term]["one"])
        ops = [op for s in baby["steps"] for op in s["ops"]]
        assert any(op["op"] == "bounce" for op in ops) and min(op["size"] for op in ops if op["op"] == "icon") >= 200
    dns = [op for s in board.builtin("DNS", "college", ladder.HAND["DNS"]["college"])["steps"] for op in s["ops"]]
    assert any(op["op"] == "move" and op["path"] == ["pc", "res"] for op in dns)                     # a query travels to the resolver
    kid = [op.get("text") for s in board.builtin("DNS", "elementary", ladder.HAND["DNS"]["elementary"])["steps"] for op in s["ops"]]
    assert "Librarian" in kid and "DNS resolver" not in kid                                          # friendlier names on the story rung


# ---- routing --------------------------------------------------------------------------------------------------------
KNOWN = ("dns", "subnetting", "tcp handshake", "router", "switch", "card", "nat")


def route(text, context=0):
    return studyintent.call_for(text, None, context, [], known=lambda t: t.lower() in KNOWN)


@pytest.mark.parametrize("said, topic", [
    ("show me DNS on the whiteboard", "DNS"), ("Jarvis, can you show me how DNS works on the whiteboard", "DNS"),
    ("put subnetting on the board", "subnetting"), ("draw subnetting", "subnetting"), ("draw the TCP handshake", "TCP handshake"),
    ("draw a router on the whiteboard", "router"), ("whiteboard NAT", "NAT"), ("sketch DNS", "DNS"),
])
def test_whiteboard_requests_reach_the_whiteboard_skill_at_any_time(said, topic):
    assert route(said) == ("study_whiteboard", {"topic": topic, "level": "", "course": ""})


def test_a_whiteboard_request_can_name_a_rung():
    assert route("show me DNS on the whiteboard like I'm in middle school")[1] == {"topic": "DNS", "level": "middle", "course": ""}
    assert route("draw it on the whiteboard", context=1) == ("study_whiteboard", {"topic": "", "level": "", "course": ""})
    assert route("draw it on the whiteboard") is None


@pytest.mark.parametrize("said", [
    "draw a bath", "draw me a picture", "draw the curtains", "draw a card", "draw a switch", "draw up a contract for the router",
    "explain why the build failed", "more detail on the invoice", "more detail", "simpler", "go deeper", "like I'm in middle school",
    "put the cheese on the board", "show me the dashboard on the board", "the whiteboard in my office is broken", "I need a new whiteboard",
    "he is on the board of directors", "too hard", "I still don't get it", "that was too easy",
])
def test_ordinary_sentences_are_not_captured_outside_study(said):
    assert route(said) is None


@pytest.mark.parametrize("said", ["draw a bath", "draw the curtains", "more detail on the invoice", "explain why the build failed",
                                  "simpler times are coming", "like I'm one of the team", "make the font simpler in the report",
                                  "it was too hard to park", "go deeper into the budget numbers"])
def test_nor_right_after_a_study_reply_when_they_are_about_something_else(said):
    assert route(said, context=1) is None


@pytest.mark.parametrize("said, level", [
    ("simpler", "simpler"), ("ok, simpler please", "simpler"), ("explain it more simply", "simpler"), ("I still don't get it", "simpler"),
    ("explain it like I'm five", "prek"), ("that's too hard", "simpler"), ("dumb it down", "simpler"),
    ("more detail", "deeper"), ("go deeper", "deeper"), ("explain it properly", "deeper"), ("in more detail please", "deeper"), ("too easy", "deeper"),
    ("like I'm in middle school", "middle"), ("now like I'm one", "one"), ("explain it like I'm in pre-k", "prek"),
    ("explain it another way", ""),
])
def test_ladder_moves_right_after_a_study_reply(said, level):
    assert route(said, context=1) == ("study_explain_again", {"level": level}), said
    assert route(said, context=2) == ("study_explain_again", {"level": level}), said


def test_while_studying_a_bare_explain_or_draw_is_about_his_material():
    assert route("explain NAT", context=2) == ("study_explain", {"topic": "NAT", "level": "", "course": ""})
    assert route("draw a switch", context=1) == ("study_whiteboard", {"topic": "switch", "level": "", "course": ""})
    assert route("explain the weather forecast", context=1)[0] == "study_explain"         # while studying, a plain subject is taught (from general knowledge)
    assert route("explain this error", context=1) is None and route("explain what happened yesterday", context=1) is None


# ---- the whiteboard skill and the Brain -----------------------------------------------------------------------------
def test_the_whiteboard_skill_is_registered_explains_and_makes_the_board_available(quiet):
    import tools
    assert "study_whiteboard" in registry.names() and "study" in tools.ALL
    assert study.last_board() is None
    said = run("study_whiteboard", topic="TCP handshake", level="high")
    assert said.startswith("High-school level. TCP and UDP are two ways to send data") and said.endswith("It is on the whiteboard on your screen.")
    assert quiet == [1]
    last = study.last_board()
    assert last["concept"] == "TCP and UDP" and last["level"] == "high" and last["board"]["source"] == "hand" and last["ladder"][0]["id"] == "college"
    with study.on_page("teach"):
        study.explain("DNS", "one")
    assert study.last_board()["concept"] == "TCP and UDP"                       # what the page asks for is not "what Jarvis just said"


def test_the_board_opens_on_the_pc_only_for_a_turn_at_the_pc(monkeypatch, no_desktop_popups):
    opened = no_desktop_popups
    from core import appserver, pcbridge
    from tools import software
    monkeypatch.setattr(study, "OPEN_BOARD", study._open_board)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(software, "is_admin", lambda: False)
    monkeypatch.setattr(appserver, "pc_page_url", lambda uid, page: f"http://127.0.0.1:1/{page}#t=test")
    token = study.at_pc.set(False)
    try:
        assert run("study_whiteboard", topic="DNS").endswith("The whiteboard button is under my reply.") and opened == []
    finally:
        study.at_pc.reset(token)
    assert run("study_whiteboard", topic="DNS").endswith("It is on the whiteboard on your screen.")
    assert opened == ["http://127.0.0.1:1/study.html?board=last#t=test"]
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)                    # the laptop serves the phone: nothing to open there
    assert run("study_whiteboard", topic="DNS").endswith("Open the Study page to watch it on the whiteboard.") and len(opened) == 1


@pytest.fixture
def brain():
    from brain import llm

    b = llm.Brain.__new__(llm.Brain)
    b.uid, b.messages = "owner", []
    return b


def test_the_brain_takes_whiteboard_and_ladder_lines_and_leaves_the_rest(brain, quiet):
    said = []
    for line in ("draw a bath", "explain why the build failed", "more detail on the invoice", "simpler"):
        assert brain._study_request(line, said.append) is False, line
    assert brain._study_request("[Sent from their phone] show me DNS on the whiteboard", said.append) is True
    assert said[0].startswith("DNS is the hierarchical") and " ".join(said).endswith("The whiteboard button is under my reply.")
    assert quiet == []                                                           # the phone turn opened nothing on the PC screen
    said.clear()
    assert brain._study_request("simpler", said.append) is True and said[0] == "High-school level."
    assert brain._study_request("more detail on the invoice", said.append) is False
    said.clear()
    assert brain._study_request("draw the TCP handshake", said.append) is True    # known from the real material, by its own words
    assert "TCP" in " ".join(said) and brain.messages[-3]["content"][0]["name"] == "study_whiteboard"
    assert study.last_board()["concept"] == "TCP and UDP"


def test_a_phone_turn_does_not_open_the_pc_screen(brain, monkeypatch):
    seen = []
    monkeypatch.setattr(study, "OPEN_BOARD", lambda: seen.append(study.at_pc.get()) or False)
    brain._study_request("[Sent from their phone] show me DNS on the whiteboard", lambda s: None)
    brain._study_request("show me DNS on the whiteboard", lambda s: None)
    assert seen == [False, True] and study.at_pc.get() is True


# ---- the API and the page -------------------------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    from core import appserver

    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    return TestClient(appserver.AppServer(lambda uid: None).app)


def pair(client, uid="owner"):
    from tools import voice

    voice.pair_codes["444444"] = uid
    r = client.post("/api/pair", json={"code": "444444", "device": "Phone"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_explain_route_returns_the_ladder_and_a_board_and_moves_one_rung(client):
    h = pair(client)
    assert client.post("/api/study/explain", json={"topic": "DNS"}).status_code == 401
    x = client.post("/api/study/explain", headers=h, json={"topic": "DNS", "course": "it-fundamentals", "session": "teach"}).json()
    assert x["level"] == "college" and x["label"] == "College" and x["concept"] == "DNS" and x["source"] == "builtin" and not x["templated"]
    assert [r["id"] for r in x["ladder"]] == list(ladder.RUNGS) and x["ladder"][5]["label"] == "One-year-old"
    assert x["board"]["v"] == 1 and x["board"]["w"] == 1000 and x["board"]["steps"][0]["say"] and x["board"]["source"] == "hand"
    y = client.post("/api/study/explain", headers=h, json={"topic": "DNS", "level": "simpler", "session": "teach"}).json()
    assert y["level"] == "high" and y["text"] != x["text"]
    z = client.post("/api/study/explain", headers=h, json={"topic": "DNS", "level": "one", "session": "teach"}).json()
    assert z["level"] == "one" and len([o for s in z["board"]["steps"] for o in s["ops"] if o["op"] == "icon"]) <= 2
    again = client.post("/api/study/explain", headers=h, json={"topic": "DNS", "level": "simpler", "session": "teach"}).json()
    assert again["level"] == "one" and again["edge"] == "bottom"
    assert client.post("/api/study/explain", headers=h, json={"topic": "DNS", "level": "<script>"}).json()["level"] == "one"   # junk level: where he is
    assert client.post("/api/study/explain", headers=h, json={"topic": "zebra husbandry"}).status_code == 400


def test_the_concepts_route_lists_the_course_with_the_rung_he_is_on(client):
    h = pair(client)
    assert client.get("/api/study/concepts").status_code == 401
    client.post("/api/study/explain", headers=h, json={"topic": "NAT", "level": "elementary"})
    data = client.get("/api/study/concepts?course=it-fundamentals", headers=h).json()
    net = next(c for c in data["chapters"] if c["chapter"] == "Networking")
    assert data["builtin"] and len(data["chapters"]) == len(study_seed.OUTLINE) and len(data["ladder"]) == 6
    assert {"name": "NAT", "rung": "elementary", "hand": True} in net["concepts"] and {"name": "DNS", "rung": "", "hand": True} in net["concepts"]
    assert data["chapters"][0]["concepts"][0] == {"name": "CPU", "rung": "", "hand": False}
    notes = "# Chapter 2: Switching\n\nA switch is a device that forwards frames using the MAC address table. A VLAN is a logical broadcast domain on a switch."
    client.post("/api/study/material", headers=h, data={"course": "Net 101", "title": "Week 1", "text": notes})
    mine = client.get("/api/study/concepts?course=net-101", headers=h).json()
    assert not mine["builtin"] and [c["name"] for c in mine["chapters"][0]["concepts"]] == ["switch", "VLAN"]
    assert client.get("/api/study/concepts?course=nope", headers=h).status_code == 400


def test_the_board_route_gives_what_jarvis_last_explained_by_voice(client):
    h = pair(client)
    assert client.get("/api/study/board").status_code == 401
    assert client.get("/api/study/board", headers=h).json() == {"board": None}
    client.post("/api/study/explain", headers=h, json={"topic": "DNS"})          # the page's own explanation is not it
    assert client.get("/api/study/board", headers=h).json() == {"board": None}
    with users.acting_as("owner"):
        run("study_whiteboard", topic="router", level="elementary")
    got = client.get("/api/study/board", headers=h).json()["board"]
    assert got["concept"] == "Router" and got["level"] == "elementary" and got["board"]["steps"] and got["ladder"]
    other = pair(client, users.add("Marie", {"en": "madame"}, "wife"))
    assert client.get("/api/study/board", headers=other).json() == {"board": None}   # hers, not his


def test_the_page_builds_the_board_from_nodes_and_never_from_markup():
    page = (APP / "study.html").read_text(encoding="utf-8")
    start, end = page.index("/* ---------- teach: the ladder and the whiteboard ---------- */"), page.index("/* ---------- end teach ---------- */")
    teach = page[start:end]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser", "eval(", "new Function"):
        assert banned not in teach, banned
    assert "createElementNS" in teach and ".textContent = r" in teach and "textContent = step.say" in teach and "replaceChildren" in teach
    for icon in board.ICONS:
        assert re.search(rf"\b{icon}: \[\[", teach), icon                       # every icon of the vocabulary can be drawn
    for control in ("b-prev", "b-play", "b-next", "b-replay", "b-speed", "b-listen", "t-ladder", "t-simpler", "t-deeper", "t-another", "t-quiz"):
        assert f'id="{control}"' in page, control
    assert '"/api/say"' in teach and "/api/study/explain" in teach and "/api/study/concepts" in teach and "/api/study/board" in teach
    assert "prefers-reduced-motion: reduce" in page and 'matchMedia("(prefers-reduced-motion: reduce)")' in teach
    assert 'data-tab="teach"' in page and "explainHere" not in page             # the old three-level panel is gone
    assert "openTeach({ topic: \"\" }, { session: Q.session, fromQuiz: true })" in page       # "Explain it" in a quiz opens the board
    style = page[page.index("<style>"):page.index("</style>")]
    assert "var(--accent)" in style and "wb-bounce" in style and "wb-draw" in style


# ---- review round 2: everyday nouns that happen to be in his course are not study requests -------------------------------
GENERIC = ("line", "server", "cloud", "key", "lock", "file", "table", "router", "process", "database", "this file", "my password",
           "password", "dns", "subnetting", "tcp handshake", "file system")


def loose(text, context=0):
    def known(topic):                                            # as the Brain's: an article in front does not matter
        return re.sub(r"^(?:the|a|an)\s+", "", topic.lower()) in GENERIC
    return studyintent.call_for(text, None, context, [], known=known)


@pytest.mark.parametrize("said", [
    "draw the line", "draw the server", "draw the cloud", "draw the key", "draw the lock", "draw the file", "draw the table",
    "sketch the router", "put the key on the board", "show me the table on the board",
])
def test_drawing_an_everyday_thing_is_not_a_whiteboard_request_outside_study(said):
    assert loose(said) is None


def test_clear_whiteboard_requests_still_work_with_everyday_words_known():
    assert loose("draw the TCP handshake")[0] == "study_whiteboard"
    assert loose("draw subnetting")[0] == "study_whiteboard"
    assert loose("show me the router on the whiteboard")[1]["topic"] == "router"
    assert loose("put subnetting on the board")[0] == "study_whiteboard"
    assert loose("draw the router", context=1)[0] == "study_whiteboard"          # while studying it is his topic


@pytest.mark.parametrize("said", [
    "explain this file in more detail", "explain my password properly", "explain this error more simply",
    "explain this file like I'm five", "explain my password like I'm in middle school", "explain what happened yesterday",
    "explain why the build failed", "explain yourself", "explain it", "can't explain it simply", "I can explain DNS to you",
    "does that explain the delay", "explain C:/Users/x/notes.txt", "explain 4 plus 4 times 7 divided by 3 please",
])
def test_explain_of_a_thing_on_his_screen_or_day_is_ordinary_talk_outside_study(said):
    assert loose(said) is None


@pytest.mark.parametrize("said, topic, level", [
    ("explain the process properly", "process", "deeper"), ("explain the key in more detail", "key", "deeper"),
    ("explain what a database is in more detail", "database", "deeper"), ("explain how a lock works in simple terms", "lock", "simpler"),
    ("explain the cloud more simply", "cloud", "simpler"), ("explain DNS", "DNS", ""), ("teach me about inflation", "inflation", ""),
    ("help me understand recursion", "recursion", ""), ("Explain RAM from my IT Fundamentals course.", "RAM", ""),
])
def test_explain_teach_and_help_me_understand_start_the_ladder_on_any_plain_subject(said, topic, level):
    got = loose(said)
    assert got and got[0] == "study_explain" and got[1]["topic"] == topic and got[1]["level"] == level, (said, got)


def test_explain_with_a_named_rung_or_while_studying_is_still_a_study_request():
    assert loose("explain subnetting like I'm five") == ("study_explain", {"topic": "subnetting", "level": "prek", "course": ""})
    assert loose("explain DNS like I'm in middle school")[1]["level"] == "middle"
    assert loose("explain DNS for the exam")[1]["level"] == "college"
    assert loose("explain the process properly", context=1) == ("study_explain", {"topic": "process", "level": "deeper", "course": ""})
    assert loose("explain DNS in more detail from my notes")[1]["level"] == "deeper"


def test_the_brain_with_the_real_course_leaves_everyday_draw_and_explain_lines_alone(brain, quiet):
    said = []
    for line in ("draw the line", "draw the server", "draw the cloud", "draw the key", "draw the lock", "draw the file", "draw the table",
                 "draw the screen", "draw the computer", "draw the data", "draw the password", "draw the memory", "draw the process",
                 "draw the system", "sketch the router", "put the key on the board", "explain this file in more detail",
                 "explain my password properly", "explain this error more simply", "explain what happened yesterday"):
        assert brain._study_request(line, said.append) is False, line
    assert said == [] and quiet == []
    assert brain._study_request("explain DNS like I'm five", said.append) is True


@pytest.mark.parametrize("said, rung", [
    ("explain it like I'm five", "prek"), ("like I'm 5", "prek"), ("like a five year old", "prek"), ("eli5", "prek"), ("ELI5 subnetting", "prek"),
    ("like I'm four years old", "prek"), ("like I'm 3", "prek"), ("like I'm two", "one"), ("like I'm 1", "one"), ("like a toddler", "one"),
    ("like a baby", "one"), ("like a kindergartner", "prek"),
    ("like I'm six", "elementary"), ("like I'm 8", "elementary"), ("like a ten year old", "elementary"), ("like I'm 10 years old", "elementary"),
    ("like I'm 11", "middle"), ("like a thirteen year old", "middle"), ("like I'm in middle school", "middle"),
    ("like I'm 14", "high"), ("like I'm sixteen", "high"), ("like an 18 year old", "high"),
    ("like I'm an adult", "college"), ("like an expert", "college"), ("like I'm a college student", "college"),
])
def test_a_named_age_or_level_goes_straight_to_its_rung(said, rung):
    assert ladder.named(said) == rung and ladder.level_in(said) == rung
    assert ladder.move("college", ladder.level_in(said)) == rung and ladder.move("one", ladder.level_in(said)) == rung


def test_unnamed_requests_still_move_one_rung_and_ages_in_other_sentences_name_nothing():
    for said in ("simpler", "dumb it down", "like I'm a kid", "in simple terms"):
        assert ladder.named(said) == "" and ladder.level_in(said) == "simpler", said
    for said in ("more detail", "go deeper"):
        assert ladder.level_in(said) == "deeper", said
    for said in ("like I'm 5 minutes late", "like I'm 15 minutes away", "like I'm twenty", "the five year plan", "I'm 5 and busy"):
        assert ladder.named(said) == "", said
