"""Autonomous skill acquisition (core/acquire): the rules that keep it honest, with a fake learner and a fake lab.
The real lab (hidden desktop + throwaway Edge) and the real local learner are exercised by scripts/acquire.py; evidence in
docs/evidence/skill-acquisition-20261107.md."""
import json

import pytest

from core import acquire, methodlang, procedures, skillcontracts

MISS = {"at": 1794030713.2, "said": "Close GitHub.",
        "reason": "step not verified: No visible browser tab identifies itself as Github; nothing was closed. Stopped without paid escalation.",
        "decision": {"intents": [{"kind": "actions", "steps": [{"tool": "close_site_tab", "arguments": {"site": "github"}}]}]}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LEARNED_DIR", str(tmp_path / "learned"))
    monkeypatch.setenv("JARVIS_ACQUIRE_DIR", str(tmp_path / "evidence"))
    return tmp_path


def test_discover_ranks_real_execution_misses_and_never_fakes_a_verifier(store):
    other = {**MISS, "said": "close it", "reason": "step not verified: ChatGPT.exe is still in front",
             "decision": {"intents": [{"steps": [{"tool": "close_foreground", "arguments": {}}]}]}}
    nolab = {**MISS, "said": "hide it", "reason": "step not verified: it is still showing",
             "decision": {"intents": [{"steps": [{"tool": "minimize_foreground", "arguments": {}}]}]}}
    vague = {**MISS, "said": "find the most demanding memory", "reason": "part of the request was not understood"}
    brief = {**MISS, "said": "[Automatic morning briefing: ...]"}
    rows = acquire.discover([MISS, MISS, other, nolab, vague, brief])
    assert rows[0]["tool"] == "close_site_tab" and rows[0]["status"] == "ready" and rows[0]["count"] == 2    # deduplicated
    by = {r["tool"]: r for r in rows}
    assert by["minimize_foreground"]["status"] == "UNVERIFIED" and "verifier" in by["minimize_foreground"]["note"]
    assert by["close_foreground"]["status"] == "ready" and by["close_foreground"]["lab"]     # 2026-11-12: core/labworlds
    assert by[""]["status"] == "elsewhere"
    assert all(not r["examples"][0]["said"].startswith("[Automatic") for r in rows)


def test_method_language_refuses_everything_but_ui_primitives():
    for bad in ("import os\ndef run(ui, params):\n    return 1", "def run(ui, params):\n    while True:\n        pass",
                "def run(ui, params):\n    return ui._ui", "def run(ui, params):\n    return open('x')",
                "def run(ui, params):\n    return ui.keys(1, 'x')", "def run(ui, params):\n    return ''.__class__",
                "def run(ui, params):\n    f = lambda: 1\n    return f()", "def helper():\n    pass\ndef run(ui, params):\n    pass"):
        with pytest.raises(methodlang.BadMethod):
            methodlang.check(bad)


class FakeUI:
    def __init__(self):
        self.closed = []

    def windows(self):
        return [{"id": 1, "title": "x", "process": "msedge.exe", "class": ""}]

    def find(self, within, role="", name="", contains=""):
        return [{"id": 2, "role": "Button", "name": "Close tab", "window": 1}]

    def invoke(self, eid):
        self.closed.append(eid)
        return True


def test_a_method_runs_counted_and_bounded():
    ui = FakeUI()
    src = "def run(ui, params):\n    for b in ui.find(1, role='Button'):\n        ui.invoke(b['id'])\n    return 'closed ' + params['site']\n"
    out = methodlang.run(src, ui, {"site": "github"})
    assert out["ok"] and out["message"] == "closed github" and ui.closed == [2] and out["calls"] == 2
    loop = "def run(ui, params):\n    for i in range(150):\n        for j in range(150):\n            ui.windows()\n"
    out = methodlang.run(loop, ui, {})
    assert not out["ok"] and out["error_type"] == "Budget"
    out = methodlang.run("def run(ui, params):\n    ui.fail('no edge window')\n", ui, {})
    assert not out["ok"] and out["error_type"] == "MethodFailed"


def _snap(pages, windows=1):
    return {"pages": [{"id": str(i), "url": u, "title": t} for i, (u, t) in pages.items()], "windows": windows}


def test_the_judge_reads_ground_truth_not_the_methods_claims():
    world = {"params": {"site": "github"}, "windows": [{"tabs": [{"host": "github.com", "title": "a · GitHub"},
                                                                {"host": "www.google.com", "title": "github - Google Search"}]}]}
    before = _snap({1: ("https://github.com:9/p0", "a · GitHub"), 2: ("https://www.google.com:9/p1", "github - Google Search")})
    good = _snap({2: ("https://www.google.com:9/p1", "github - Google Search")})
    assert skillcontracts.judge(world, before, good, {"ok": True})["passed"]
    assert not skillcontracts.judge(world, before, before, {"ok": True})["passed"]                 # said done, nothing closed
    decoy = _snap({})
    j = skillcontracts.judge(world, before, decoy, {"ok": True})
    assert not j["passed"] and "wrongly closed" in j["diff"][0]                                     # the Google search was not GitHub
    assert not skillcontracts.judge(world, before, _snap({2: ("https://www.google.com:9/p1", "x")}, windows=0), {"ok": True})["passed"]


def test_a_blank_tab_that_keeps_a_one_tab_window_alive_is_allowed_but_only_one():
    """2026-11-08: the lab Edge is offline, so its new tab is chrome-search://local-ntp; two practice worlds failed a
    correct method for opening exactly that."""
    world = {"params": {"site": "github"}, "windows": [{"tabs": [{"host": "github.com", "title": "a · GitHub"}]}]}
    before = _snap({1: ("https://github.com:9/p0", "a · GitHub")})
    ntp = "chrome-search://local-ntp/local-ntp.html"
    assert skillcontracts.judge(world, before, _snap({5: (ntp, "New tab")}), {"ok": True})["passed"]
    assert not skillcontracts.judge(world, before, _snap({5: (ntp, "New tab"), 6: (ntp, "New tab")}), {"ok": True})["passed"]
    assert not skillcontracts.judge(world, before, _snap({5: ("https://evil.example/", "x")}), {"ok": True})["passed"]


def test_worlds_are_deterministic_and_learner_exercises_are_validated():
    assert skillcontracts.generate(7) == skillcontracts.generate(7) and skillcontracts.generate(7) != skillcontracts.generate(8)
    for seed in range(40):
        w = skillcontracts.generate(seed)
        assert any(skillcontracts.is_target(w["params"]["site"], "https://" + t["host"] + "/") for x in w["windows"] for t in x["tabs"])
    with pytest.raises(ValueError):
        skillcontracts.validate_world({"params": {"site": "evil"}, "windows": [{"tabs": [{"host": "a.com", "title": "x"}]}]})
    with pytest.raises(ValueError):
        skillcontracts.validate_world({"params": {"site": "github"}, "windows": [{"tabs": [{"host": "file:///c:", "title": "x"}]}]})
    ok = skillcontracts.validate_world({"params": {"site": "github"}, "windows": [{"tabs": [{"host": "github.com", "title": "<b>x</b> · GitHub"}], "active": 9}]})
    assert ok["windows"][0]["active"] == 0 and "<b>" not in ok["windows"][0]["tabs"][0]["body"]


GOOD = "def run(ui, params):\n    ui.log('GOOD')\n    return 'closed'\n"
WEAK = "def run(ui, params):\n    ui.log('WEAK')\n    return 'closed'\n"


def fake_lab(exam_passes=True):
    calls = []

    def lab(trials, label):
        calls.append(label)
        results = []
        for t in trials:
            a = t["action"]
            if a["type"] == "native":
                results.append({"result": {"ok": False, "error": "RuntimeError: No visible browser tab identifies itself as "
                                           + t["world"]["params"]["site"].title() + "; nothing was closed."}, "judge": {"passed": False, "diff": []}})
            elif a["type"] == "observe":
                results.append({"result": {"ok": True, "tree": "TabItem 'a · GitHub'\n  Button 'Close tab'"}})
            else:
                good = "GOOD" in a["source"] and (exam_passes or not label.startswith("exam"))
                results.append({"result": {"ok": True, "trace": []}, "judge": {"passed": good, "checks": {"ok": good}, "diff": [] if good else ["still open: x"]}})
        return {"status": "done", "results": results}
    lab.calls = calls
    return lab


def fake_learner(sources):
    prompts = []

    def ask(prompt, temperature, fmt):
        prompts.append(prompt)
        if "Propose 4 concrete" in prompt:
            return json.dumps({"exercises": [{"why": "background tab", "params": {"site": "github"},
                                              "windows": [{"tabs": [{"host": "www.bing.com", "title": "Bing"}, {"host": "github.com", "title": "x · GitHub"}], "active": 0}]}]})
        if "search queries" in prompt:
            return json.dumps({"queries": []})
        if "what capability" in prompt:
            return "It only reads window titles, which show the front tab."
        if "what exactly went wrong" in prompt:
            return "It matched the wrong names."
        return "```python\n" + (sources.pop(0) if sources else GOOD) + f"    # attempt {len(prompts)}\n```"
    learner = acquire.Learner(model="fake", ask=ask)
    learner.prompts = prompts
    return learner


def test_promoted_only_after_the_sealed_exam_and_then_awaits_review(store, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTO_APPROVE", "off")       # the review gate itself; auto-approval is tests/test_learned.py
    cand = acquire.discover([MISS])[0]
    learner, lab = fake_learner([WEAK, WEAK, WEAK, GOOD]), fake_lab()
    rec = acquire.learn(cand, learner=learner, lab=lab)
    assert rec["status"] == "learned" and "exam-1" in lab.calls and lab.calls.index("practice-r1") < lab.calls.index("exam-1")
    stages = [s["stage"] for s in rec["stages"]]
    for st in ("discover", "investigate", "design", "practice", "improve", "verify", "retain"):
        assert st in stages
    man = acquire.learned_methods()[0]
    assert man["status"] == "verified" and man["evidence"]["total"] == 12 and man["for_tool"] == "close_site_tab"
    assert acquire.candidates_for("close_site_tab") == []                     # verified is NOT active: the owner reviews first
    acquire.approve(man["id"])
    assert acquire.candidates_for("close_site_tab") == ["learned:" + man["id"]]
    from core import pcmethods
    assert "learned:" + man["id"] in pcmethods.candidates({"tool": "close_site_tab", "arguments": {"site": "github"}})
    assert pcmethods.candidates({"tool": "close_app", "arguments": {"app": "paint"}}) == ["native"]


def test_a_failed_exam_is_not_promoted_and_sealed_worlds_never_leak(store):
    cand = acquire.discover([MISS])[0]
    learner, lab = fake_learner([GOOD.replace("GOOD", f"GOOD {i}") for i in range(8)]), fake_lab(exam_passes=False)
    rec = acquire.learn(cand, learner=learner, lab=lab)
    assert rec["status"] == "not_learned" and acquire.learned_methods() == []
    assert lab.calls.count("exam-1") == 1 and lab.calls.count("exam-2") == 1 and "exam-3" not in lab.calls     # bounded exams
    sealed = [acquire._world_text(skillcontracts.generate(s))[:300] for s in acquire.HELD_OUT_SEEDS]
    for p in learner.prompts:
        assert not any(t in p for t in sealed)                                # only a count of exam passes went back
    assert any("only 0 passed" in p for p in learner.prompts)


def test_an_approval_is_bound_to_the_exact_code(store):
    cand = acquire.discover([MISS])[0]
    acquire.learn(cand, learner=fake_learner([GOOD]), lab=fake_lab())
    man = acquire.learned_methods()[0]
    acquire.approve(man["id"])
    src = store / "learned" / man["id"] / "v1" / "source.py"
    src.write_text(src.read_text() + "\n# tampered\n")
    assert acquire.candidates_for("close_site_tab") == []                     # changed code: refused, not run


def test_budget_stops_an_endless_learner(store):
    cand = acquire.discover([MISS])[0]
    learner = fake_learner([WEAK] * 20)
    learner.max_calls = 5
    rec = acquire.learn(cand, learner=learner, lab=fake_lab())
    assert rec["status"] == "not_learned" and learner.calls == 5
    with pytest.raises(procedures.NotVerified):                               # and the library itself refuses unjudged code
        procedures.Library(store / "x").register("p", "t", "x", [], {}, {}, "me", {"judge": "j", "passed": 3, "total": 3})


def test_a_learner_exercise_must_be_solvable_from_what_is_on_screen():
    with pytest.raises(ValueError, match="never names the site"):
        skillcontracts.validate_world({"params": {"site": "bing"}, "windows": [{"tabs": [{"host": "www.bing.com", "title": "Incognito - News"}]}]})
    for seed in range(60):                 # the host's own worlds obey the same rule
        w = skillcontracts.generate(seed)
        site = w["params"]["site"]
        for x in w["windows"]:
            for t in x["tabs"]:
                if skillcontracts.is_target(site, "https://" + t["host"] + "/"):
                    assert site in t["title"].casefold().replace(" ", "")


def test_an_unfinished_attempt_warm_starts_the_next_but_must_still_pass_everything(store):
    cand = acquire.discover([MISS])[0]
    rec = acquire.learn(cand, learner=fake_learner([WEAK] * 30), lab=fake_lab())
    assert rec["status"] == "not_learned"
    assert acquire._warm_start(cand, None) is None                      # nothing ever passed: nothing to keep
    half = "def run(ui, params):\n    ui.log('HALF')\n    return 'x'\n"

    def lab(trials, label):
        out = fake_lab()(trials, label)
        for t, r in zip(trials, out["results"]):
            if "HALF" in t["action"].get("source", ""):
                r["judge"] = {"passed": t["world"]["params"]["site"] == "github", "checks": {}, "diff": []}
        return out
    acquire.learn(cand, learner=fake_learner([half] + [WEAK] * 30), lab=lab)
    warm = acquire._warm_start(cand, None)
    assert warm and "HALF" in warm["source"]
    rec = acquire.learn(cand, learner=fake_learner([WEAK] * 30), lab=lab)
    assert any(s["what"].startswith("warm start") for s in rec["stages"]) and rec["status"] == "not_learned"


def test_a_stopped_attempt_keeps_its_work_and_the_next_one_goes_straight_to_practice(store):
    from core import skilllab
    cand = acquire.discover([MISS])[0]
    half = "def run(ui, params):\n    ui.log('HALF')\n    return 'x'\n"

    def stopping_lab(trials, label):                    # the owner comes back the moment practice would start
        if label.startswith("practice"):
            raise skilllab.Suspended("the owner started using the PC")
        return fake_lab()(trials, label)
    rec = acquire.learn(cand, learner=fake_learner([half, WEAK, GOOD]), lab=stopping_lab)
    assert rec["status"] == "suspended" and "3 untested methods" in rec["stages"][-1]["data"]["kept_for_next_time"]
    learner, lab = fake_learner([WEAK] * 30), fake_lab()
    rec = acquire.learn(cand, learner=learner, lab=lab)
    assert any(s["what"].startswith("reused the investigation") for s in rec["stages"])
    assert lab.calls[0] == "practice-r1"                # no second reproduction, no rewriting before practice
    assert not any("Propose 4 concrete" in p for p in learner.prompts)
    assert rec["status"] == "learned"                   # the GOOD method written before the stop still had to pass the sealed exam
    assert "exam-1" in lab.calls
    rec = acquire.learn(cand, learner=fake_learner([WEAK] * 30), lab=fake_lab())
    assert not any(s["what"].startswith("reused") for s in rec["stages"])     # a saved state is used once only


class _FakeStream:
    def __init__(self, lines):
        self.lines, self.closed = lines, False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True

    def __iter__(self):
        return iter(self.lines)


def test_the_owner_coming_back_stops_the_model_mid_reply_not_only_the_lab(monkeypatch):
    from core import skilllab
    chunks = [json.dumps({"message": {"content": w}, "done": False}).encode() + b"\n" for w in ("def ", "run", "(ui")]
    stream = _FakeStream(chunks)
    monkeypatch.setattr(acquire.urllib.request, "urlopen", lambda req, timeout: stream)
    inputs = iter([100, 100, 100, 250])                     # the 3rd chunk arrives after the owner touched the mouse
    monkeypatch.setattr(skilllab, "_last_input", lambda: next(inputs))
    with pytest.raises(skilllab.Suspended):
        acquire.Learner(model="m", guard=True)("write a method")
    assert stream.closed                                   # connection dropped: Ollama stops generating
    stream = _FakeStream(chunks + [json.dumps({"message": {"content": ")"}, "done": True}).encode()])
    monkeypatch.setattr(acquire.urllib.request, "urlopen", lambda req, timeout: stream)
    assert acquire.Learner(model="m", guard=False)("write a method") == "def run(ui)"


def test_a_stop_while_writing_keeps_the_methods_already_written(store):
    from core import skilllab
    cand = acquire.discover([MISS])[0]
    learner = fake_learner([WEAK, GOOD])
    ask = learner._ask

    def stopping(prompt, temperature, fmt):
        if "```python" in prompt and "Write a NEW method" in prompt and sum("NEW method" in p for p in learner.prompts) >= 2:
            raise skilllab.Suspended("owner back while writing")
        return ask(prompt, temperature, fmt)
    learner._ask = stopping
    rec = acquire.learn(cand, learner=learner, lab=fake_lab())
    assert rec["status"] == "suspended" and "2 untested methods" in rec["stages"][-1]["data"]["kept_for_next_time"]
    lab = fake_lab()
    rec = acquire.learn(cand, learner=fake_learner([WEAK] * 30), lab=lab)
    assert lab.calls[0] == "practice-r1" and rec["status"] == "learned"


def test_a_missing_local_model_is_waited_for_not_counted_as_a_failure(monkeypatch):
    def down(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr(acquire.urllib.request, "urlopen", down)
    assert "not reachable" in acquire.learner_ready("gpt-oss:20b")


def test_a_rejected_method_gets_one_rewrite_with_the_checkers_words_and_the_rules_stay(store):
    bad = "def run(ui, params):\n    def helper():\n        return 1\n    return 'x'\n"
    cand = acquire.discover([MISS])[0]
    learner = fake_learner([bad, bad, bad])                     # every first draft breaks the rules; rewrites come back GOOD
    rec = acquire.learn(cand, learner=learner, lab=fake_lab(), escalate=False)
    assert rec["status"] == "learned"
    assert any("REJECTED by the checker" in p and "only the one run() function" in p for p in learner.prompts)
    assert any(s["what"].endswith("rewritten within the rules") for s in rec["stages"])
    with pytest.raises(methodlang.BadMethod):
        methodlang.check(bad)                                    # the language did not get looser
    methodlang.check("def run(ui, params):\n    for _ in range(2):\n        ui.wait(0.1)\n")
    with pytest.raises(methodlang.BadMethod):
        methodlang.check("def run(ui, params):\n    x = ui.__class__\n")
