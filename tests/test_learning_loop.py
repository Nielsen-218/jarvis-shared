"""The self-learning chain end to end: fails -> lesson requested -> teacher shows -> verified -> stored -> next time local.
Everything here is fake: learner, teacher, lab, clock. No model, no network, no real app."""
import io
import json
import os
import urllib.error

import pytest

from core import acquire, skillspec, teacher

MISS = {"at": 1794030713.2, "said": "Close GitHub.",
        "reason": "step not verified: No visible browser tab identifies itself as Github; nothing was closed. Stopped without paid escalation.",
        "decision": {"intents": [{"kind": "actions", "steps": [{"tool": "close_site_tab", "arguments": {"site": "github"}}]}]}}
SKILL_SOURCE = "def run(browser, util, params):\n    return {'closed': 1}\n"
EVIDENCE = {"judge": "j", "passed": 12, "total": 12, "tasks": []}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LEARNED_DIR", str(tmp_path / "learned"))
    monkeypatch.setenv("JARVIS_ACQUIRE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("JARVIS_SKILLS_DIR", str(tmp_path / "learned-skills"))
    monkeypatch.setenv("JARVIS_LESSONS_DIR", str(tmp_path / "lessons"))
    monkeypatch.delenv("JARVIS_SKILL_LAB", raising=False)
    monkeypatch.setattr(acquire, "idle_ready", lambda: "")
    return tmp_path


def keep_skill(provenance=None, approve=True, name="close_site_tab"):
    spec = {"name": name, "goal": "close the site's tabs", "inputs": ["site"], "recovery": [],
            "dependencies": {"capabilities": ["browser"], "skills": []}}
    man = skillspec.retain(spec, SKILL_SOURCE, EVIDENCE, provenance or {"provenance": "independent", "teacher_lessons": []}, "fake-model")
    if approve:
        skillspec.approve(man["id"])
    return man


def http_404(*_a, **_k):
    raise urllib.error.HTTPError("http://127.0.0.1:11434/v1/api/chat", 404, "Not Found", {}, io.BytesIO(b""))


# ------------------------------------------------------------------------------------------------ 1. crash-proof cycle

def test_the_learner_talks_to_ollamas_native_api_even_when_the_setting_is_the_openai_base(monkeypatch):
    """The real cause of the nightly 404: JARVIS_OLLAMA_URL is ".../v1" for every other caller, and /v1/api/chat does not exist."""
    seen = []

    class Reply(list):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"models": [{"name": "fake:1"}]}).encode()

    def urlopen(req, timeout):
        seen.append(req if isinstance(req, str) else req.full_url)
        return Reply([json.dumps({"message": {"content": "hi"}, "done": True}).encode()])
    monkeypatch.setattr(acquire, "OLLAMA", "http://127.0.0.1:11434/v1/")
    monkeypatch.setattr(acquire.urllib.request, "urlopen", urlopen)
    assert acquire.learner_ready("fake:1") == ""
    assert acquire.Learner(model="fake:1", guard=False)("say hi") == "hi"
    assert seen == ["http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/api/chat"]


def test_a_404_from_the_model_server_ends_the_cycle_as_a_recorded_failure(store, monkeypatch):
    monkeypatch.setattr(acquire.urllib.request, "urlopen", http_404)
    cand = acquire.discover([MISS])[0]
    rec = acquire.learn(cand, learner=acquire.Learner(model="fake", guard=False), lab=lambda trials, label: {"status": "done", "results": []})
    assert rec["status"] == "error" and rec["cause"] == "model_unavailable" and "404" in rec["error"]
    on_disk = json.loads((next((store / "evidence" / cand["id"]).iterdir()) / "cycle.json").read_text(encoding="utf-8"))
    assert on_disk["status"] == "error" and on_disk["finished"] and on_disk["stages"][-1]["stage"] == "error"
    assert "error" in acquire.summary(rec)
    assert teacher.cycles(cand["id"]) == []                     # an outage is nobody's failed lesson


def test_a_crash_inside_a_cycle_counts_as_a_failed_cycle_for_the_teacher(store):
    cand = acquire.discover([MISS])[0]

    def broken_lab(trials, label):
        raise KeyError("results")
    ask = lambda prompt, temperature, fmt: json.dumps({"exercises": []})   # noqa: E731
    rec = acquire.learn(cand, learner=acquire.Learner(model="fake", ask=ask), lab=broken_lab)
    assert rec["status"] == "error" and rec["cause"] == "crash" and rec["error"].startswith("KeyError")
    assert [c["status"] for c in teacher.cycles(cand["id"])] == ["not_learned"]


def test_practice_checks_the_model_first_and_never_raises(store, monkeypatch):
    f = store / "live-misses.jsonl"
    f.write_text(json.dumps(MISS) + "\n", encoding="utf-8")
    monkeypatch.setattr(acquire, "_misses_path", lambda: f)
    started = []
    assert acquire.practice_once(ready_check=lambda: "the local model server is not reachable (HTTPError)",
                                 learn_one=started.append) is None
    assert started == [] and not (store / "evidence").exists()          # no cycle, no failure counted
    monkeypatch.setattr(acquire.urllib.request, "urlopen", http_404)    # the real check, the real 404: still no exception
    assert acquire.practice_once() is None


def test_a_record_left_at_running_by_a_dead_process_is_closed_with_its_reason(store):
    d = store / "evidence" / "abc" / "20261112-030003"
    d.mkdir(parents=True)
    (d / "cycle.json").write_text(json.dumps({"status": "running", "stages": [{"stage": "investigate", "what": "existing skills"}]}), encoding="utf-8")
    at = (d / "cycle.json").stat().st_mtime
    assert acquire.close_stale(now=at + 60) == 0                         # a cycle that may still be alive is left alone
    assert acquire.close_stale(now=at + acquire.STALE_RUNNING_SECONDS + 1) == 1
    rec = json.loads((d / "cycle.json").read_text(encoding="utf-8"))
    assert rec["status"] == "error" and rec["cause"] == "model_unavailable" and rec["finished"] and "existing skills" in rec["error"]


# ------------------------------------------------------------------------------------------------ 2. discover

def test_a_skill_in_the_skills_store_counts_as_learned_so_practice_moves_on(store, monkeypatch):
    other = {**MISS, "said": "Shut the YouTube tab.", "decision": {"intents": [{"steps": [{"tool": "close_site_tab", "arguments": {"site": "youtube"}}]}]},
             "reason": "step not verified: the tab strip could not be read"}
    assert [r["status"] for r in acquire.discover([MISS, other])] == ["ready", "ready"]
    keep_skill()
    rows = acquire.discover([MISS, other])
    assert {r["status"] for r in rows} == {"learned"} and "skill.close_site_tab" in rows[0]["note"]
    f = store / "live-misses.jsonl"
    f.write_text(json.dumps(MISS) + "\n", encoding="utf-8")
    monkeypatch.setattr(acquire, "_misses_path", lambda: f)
    started = []
    assert acquire.practice_once(ready_check=lambda: "", learn_one=started.append) is None and started == []


def test_practice_moves_to_the_next_weakness_when_the_first_was_tried_today(store, monkeypatch):
    monkeypatch.setattr(acquire.skillcontracts if hasattr(acquire, "skillcontracts") else __import__("core.skillcontracts").skillcontracts,
                        "KINDS", {"close_site_tab": None, "focus_site_tab": None}, raising=False)
    from core import authz
    monkeypatch.setitem(authz.EFFECTS, "focus_site_tab", "reversible")
    second = {**MISS, "said": "Show me the wiki tab.", "reason": "step not verified: the tab did not come to the front",
              "decision": {"intents": [{"steps": [{"tool": "focus_site_tab", "arguments": {"site": "wikipedia"}}]}]}}
    f = store / "live-misses.jsonl"
    f.write_text("\n".join(json.dumps(m) for m in (MISS, MISS, second)) + "\n", encoding="utf-8")
    monkeypatch.setattr(acquire, "_misses_path", lambda: f)
    rows = acquire.discover()
    assert [r["tool"] for r in rows[:2]] == ["close_site_tab", "focus_site_tab"]          # the more frequent miss first
    done = store / "evidence" / rows[0]["id"] / "20261112-030000"
    done.mkdir(parents=True)
    (done / "cycle.json").write_text(json.dumps({"status": "not_learned"}), encoding="utf-8")
    started = []
    acquire.practice_once(ready_check=lambda: "", learn_one=lambda c: started.append(c["tool"]) or {"status": "x"},
                          now=(done / "cycle.json").stat().st_mtime + 3600)
    assert started == ["focus_site_tab"]


def test_an_understanding_gap_restudied_twice_asks_for_a_lesson_and_frequent_misses_rank_first(store):
    vague = {**MISS, "said": "tidy the thing up for me", "reason": "part of the request was not understood", "decision": {}}
    rare = {**MISS, "said": "do the usual", "reason": "part of the request was not understood and unclear", "decision": {}}
    studied = {"tidy the thing up for me": {"said": vague["said"], "outcome": "unclear", "tries": 2}}
    rows = acquire.discover([vague, vague, vague, rare], studied=studied)
    assert rows[0]["examples"][0]["said"] == vague["said"] and rows[0]["count"] == 3 and rows[0]["status"] == "needs_lesson"
    assert rows[1]["status"] == "elsewhere"


# ------------------------------------------------------------------------------------------------ 3. teacher order

LESSON = json.dumps({"failed_assumption": "He read tidying up as nothing at all.", "explanation": "The owner means the GitHub tab.",
                     "hints": ["Close the GitHub tab."], "references": []})


class Plan:
    """A stand-in for gateway.Decision: a complete plan of the given steps."""
    covers = True

    def __init__(self, steps):
        from types import SimpleNamespace
        self.intents = [SimpleNamespace(kind="actions", question="")]
        self._steps = steps

    def steps(self):
        return self._steps

    def brief(self):
        return {"steps": self._steps}


def stuck_miss(said="tidy up the github page for me", tries=2):
    from core import misslessons
    misslessons._write({"said": said, "studied_at": 1.0, "outcome": "unclear", "why": "still needs: which thing?", "tries": tries,
                        "status": "FAILED", "plan": None})
    return said


def declare_credit(monkeypatch):
    for k, v in (("JARVIS_TEACHER_CREDIT_USD", "10"), ("JARVIS_TEACHER_CREDIT_AS_OF", "2026-11-01"),
                 ("JARVIS_TEACHER_CREDIT_EXPIRES", "2099-01-01"), ("ANTHROPIC_API_KEY", "test-key")):
        monkeypatch.setenv(k, v)


class PaidClient:
    def __init__(self):
        from types import SimpleNamespace as NS
        self.created = []
        self.messages = NS(count_tokens=lambda **kw: NS(input_tokens=1000), create=self._create)

    def _create(self, **kw):
        from types import SimpleNamespace as NS
        self.created.append(kw)
        return NS(content=[NS(text=LESSON)], usage=NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def test_a_miss_is_taught_by_the_local_teacher_verified_and_then_understood_with_no_teacher(store, monkeypatch):
    from core import gateway, independence, misslessons
    stuck_miss()
    asked, replayed = [], []

    def decide(text, cancelled=None, **kw):
        replayed.append(text)
        return Plan([{"tool": "close_site_tab", "arguments": {"site": "github"}}])
    paid = PaidClient()
    rows = misslessons.teach(decide=decide, ask_local=lambda p: (asked.append(p) or LESSON, 0.0, {}), client=paid)
    assert len(asked) == 1 and paid.created == [] and replayed == ["Close the GitHub tab."]      # local first; verified by replay
    assert rows[0]["outcome"] == "understood" and rows[0]["teacher"] == "local" and rows[0]["taught_as"] == "Close the GitHub tab."
    assert misslessons.taught_as("Tidy up the GitHub page for me!") == "Close the GitHub tab."
    assert misslessons.teach(decide=decide, ask_local=lambda p: asked.append(p), client=paid) == [] and len(asked) == 1   # not asked twice
    # next time he says it: the stored wording is what the understanding layer reads; no teacher is involved
    seen = []
    monkeypatch.setattr(gateway, "_decide", lambda text, history, cancelled, ask: seen.append(text))
    gateway.decide("tidy up the github page for me")
    assert seen == ["Close the GitHub tab."]
    use = independence.uses()[-1]
    assert use["kind"] == "taught_phrase" and use["source"] == "owner" and not use["teacher"] and use["outside_calls"] == 0
    monkeypatch.setenv("JARVIS_TAUGHT_PHRASES", "off")
    assert misslessons.taught_as("tidy up the github page for me") is None


def test_a_taught_wording_that_plans_a_risky_step_or_is_not_understood_is_never_stored(store, monkeypatch):
    from core import authz, misslessons
    said = stuck_miss()
    monkeypatch.setitem(authz.EFFECTS, "close_site_tab", "destructive")
    rows = misslessons.teach(decide=lambda text, cancelled=None: Plan([{"tool": "close_site_tab", "arguments": {"site": "github"}}]),
                             ask_local=lambda p: (LESSON, 0.0, {}))
    assert rows[0]["outcome"] == "unclear" and "taught_as" not in rows[0] and misslessons.taught_as(said) is None
    assert teacher.usable(misslessons.lesson_id(said))[0]["outcome"] == "not_enough_yet"


def test_the_paid_teacher_is_asked_once_per_skill_and_only_after_the_local_lessons(store, monkeypatch):
    from core import misslessons
    declare_credit(monkeypatch)
    said = stuck_miss()
    wid, paid, local = misslessons.lesson_id(said), PaidClient(), []
    never = lambda text, cancelled=None: None                     # noqa: E731 - no lesson ever verifies in this test
    for _ in range(5):
        misslessons.teach(decide=never, ask_local=lambda p: (local.append(p) or LESSON, 0.0, {}), client=paid)
    assert len(local) == teacher.local_max() == 2 and len(paid.created) == 1       # 2 local lessons, then ONE paid lesson, then nothing
    assert teacher.can_help(wid)[0] is False and "one lesson" in teacher.can_help(wid)[1]
    refused = teacher.consult({"id": wid}, tier="cloud", client=paid, prompt="x")
    assert refused["status"] == "refused" and len(paid.created) == 1
    assert teacher.consult({"id": wid}, tier="cloud", client=paid, prompt="x", requested_by="owner command")["status"] == "given"


def test_the_local_teacher_gets_a_thinking_budget(monkeypatch):
    bodies = []

    def stream(self, body):
        bodies.append(body)
        return "" if body.get("think") else LESSON
    from core import warmth
    monkeypatch.setattr(warmth, "ollama_fields", lambda name, keep="10m": {"keep_alive": keep, "options": {"num_ctx": 8192}, "think": False})
    monkeypatch.setattr(acquire.Learner, "_stream", stream)
    reply, cost, meta = teacher._ask_local("teach me")
    assert bodies[0]["think"] is True and bodies[0]["options"]["num_predict"] == teacher.MAX_TOKENS * 4
    assert bodies[1]["think"] is False and reply == LESSON and cost == 0.0 and meta["calls"] == 2    # empty after thinking: asked plainly


# ------------------------------------------------------------------------------------------------ 4. retest and demotion

def test_a_skill_that_fails_its_retest_is_demoted_and_one_that_passes_stays(store):
    man = keep_skill()
    ran = []
    ok = acquire.retest_learned(run=lambda item: ran.append(item["id"]) or {"status": "done", "passed": True, "detail": "3/3"}, now=1000.0)
    assert ok["passed"] and not ok["demoted"] and skillspec.approved_skill(man["id"])
    assert acquire.retest_learned(run=lambda item: ran.append(item["id"]), now=1000.0 + 3600) is None       # not due yet
    lab_down = acquire.retest_learned(run=lambda item: {"status": "lab_unavailable", "passed": False}, now=1000.0 + acquire.RETEST_EVERY)
    assert lab_down["status"] == "lab_unavailable" and skillspec.approved_skill(man["id"])               # a broken lab is no verdict
    bad = acquire.retest_learned(run=lambda item: {"status": "done", "passed": False, "detail": "1/3"}, now=1000.0 + acquire.RETEST_EVERY)
    assert bad["demoted"] and skillspec.approved_skill(man["id"]) is None and skillspec.candidates_for("close_site_tab") == []
    assert acquire.demoted()[man["id"]]["why"].startswith("failed its retest")
    assert acquire.discover([MISS])[0]["status"] == "ready"          # demoted, not kept: it is learned again
    assert acquire.retest_learned(run=lambda item: ran.append("again"), now=9e9) is None and "again" not in ran


# ------------------------------------------------------------------------------------------------ 5. use and proof

class Connected:
    def connected(self):
        return True


def test_a_proven_learned_skill_is_offered_before_the_native_method_and_never_after_demotion(store, monkeypatch):
    from core import browserbridge, pcledger, pcmethods
    step = {"tool": "close_site_tab", "arguments": {"site": "github"}}
    assert pcmethods.candidates(step) == ["native"]
    man = keep_skill(approve=False)
    assert pcmethods.candidates(step) == ["native"]                   # verified but not approved: never offered
    skillspec.approve(man["id"])
    monkeypatch.setattr(browserbridge, "live", lambda: Connected())
    assert pcmethods.candidates(step) == ["skill:skill.close_site_tab", "native"]
    assert pcledger.method_order(step, pcmethods.candidates(step))[0] == "skill:skill.close_site_tab"
    pcledger.method_outcome(step, "native", True)                     # native proven in this exact situation: it keeps its place
    assert pcledger.method_order(step, pcmethods.candidates(step))[0] == "native"
    acquire.demote(acquire._active()[0], "failed its retest (0/3)")
    assert pcmethods.candidates(step) == ["native"]


def test_every_use_is_logged_with_who_asked_and_whether_anything_outside_was_involved(store, monkeypatch):
    from core import independence
    independence.log_use("skill.close_site_tab", "skill", True, tool="close_site_tab", at=100.0)
    monkeypatch.setenv("JARVIS_SKILL_LAB", "1")
    independence.log_use("skill.close_site_tab", "skill", False, tool="close_site_tab", at=101.0)
    rows = independence.uses()
    assert [(r["source"], r["passed"], r["teacher"], r["outside_calls"]) for r in rows] == [("owner", True, False, 0), ("lab", False, False, 0)]


# ------------------------------------------------------------------------------------------------ 6. the report

def test_the_independence_report_counts_skills_tasks_lessons_and_paid_teacher_cost(store, monkeypatch):
    import time as _time
    from core import independence, pcledger
    from tools import selfinfo
    now = _time.mktime((2026, 11, 12, 12, 0, 0, 0, 0, -1))
    keep_skill()                                                                              # learned on his own
    keep_skill({"provenance": "teacher_assisted", "teacher_lessons": [{"tier": "local"}]}, name="focus_site_tab")
    keep_skill({"provenance": "teacher_assisted", "teacher_lessons": [{"tier": "local"}, {"tier": "cloud"}]}, approve=False, name="mute_site_tab")
    monkeypatch.setattr(_time, "time", lambda: now)
    for _ in range(3):                                                                        # tasks are run records, not step rows
        pcledger.create("run", {"state": "completed", "route": "gateway", "cloud_usd": 0}, "completed")
        pcledger.usage("local_automation", tool="close_site_tab", billable_usd=0)
    pcledger.create("run", {"state": "completed", "route": "cloud_ai", "cloud_usd": 0.1}, "completed")
    independence.log_use("skill.close_site_tab", "skill", True, at=now - 60)
    independence.log_use("skill.close_site_tab", "skill", True, source="lab", at=now - 50)
    teacher._keep({"id": "a", "weakness": "w1", "tier": "local", "at": now - 500, "status": "given", "outcome": "led_to_learned"})
    teacher._keep({"id": "b", "weakness": "w2", "tier": "cloud", "at": now - 400, "status": "given", "outcome": "not_enough_yet", "cost_usd": 0.25})
    teacher._keep({"id": "c", "weakness": "w3", "tier": "cloud", "at": now - 40 * 86400, "status": "given", "outcome": "pending", "cost_usd": 9.0})
    d = independence.save(now)
    assert d["skills"] == {"total": 3, "active": 2, "by_himself": 1, "local_teacher": 1, "paid_teacher": 1}
    assert d["pc_tasks"]["local"] == 3 and d["pc_tasks"]["outside"] == 1 and d["pc_tasks"]["local_share"] == 0.75
    assert d["learned_uses"]["owner"] == 1 and d["learned_uses"]["lab"] == 1 and d["learned_uses"]["with_outside_calls"] == 0
    assert d["lessons"] == {"taught": 3, "worked": 1, "worked_after_one": 1, "second_attempt_success": 0.333}
    assert d["paid_teacher"] == {"month": "2026-11", "calls": 1, "cost_usd": 0.25}           # last month's lesson is not this month's
    assert json.loads((store / "learning" / "independence.json").read_text(encoding="utf-8"))["skills"]["total"] == 3
    said = selfinfo.independence_report()
    assert "3 skills" in said and "75 percent of 4 PC tasks" in said and "called 1 time this month, costing $0.25" in said
    assert "1 on my own" in said and "1 with the paid teacher" in said


def test_the_report_is_a_registered_tool_and_honest_when_there_is_nothing_yet(store):
    import tools
    from core import independence
    from tools import selfinfo
    assert "selfinfo" in tools.ALL and callable(selfinfo.independence_report)
    said = independence.spoken(independence.report(now=1.8e9))
    assert "not learned a skill in my lab yet" in said and "no PC tasks on record" in said and "called 0 times this month" in said


# ------------------------------------------------------------------------------------------------ review fixes (2026-11-12)

class EmptyPaidClient(PaidClient):
    def _create(self, **kw):
        from types import SimpleNamespace as NS
        self.created.append(kw)
        text = json.dumps({"failed_assumption": "", "explanation": "", "hints": [], "references": []})
        return NS(content=[NS(text=text)], usage=NS(input_tokens=1000, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def test_a_paid_lesson_that_came_back_empty_or_broke_still_uses_up_the_one_paid_lesson(store, monkeypatch):
    from core import misslessons
    declare_credit(monkeypatch)
    said = stuck_miss()
    wid, paid, local = misslessons.lesson_id(said), EmptyPaidClient(), []
    never = lambda text, cancelled=None: None                     # noqa: E731
    for _ in range(6):
        misslessons.teach(decide=never, ask_local=lambda p: (local.append(p) or LESSON, 0.0, {}), client=paid)
    assert len(paid.created) == 1                                  # paid for once, never again
    assert [l["status"] for l in teacher.lessons(wid) if l["tier"] == "cloud"] == ["empty"]
    assert teacher.cloud_given(wid) == 1 and teacher.can_help(wid)[0] is False
    assert teacher.consult({"id": wid}, tier="cloud", client=paid, prompt="x")["status"] == "refused" and len(paid.created) == 1


def test_a_paid_call_that_failed_after_it_was_sent_counts_but_a_refusal_at_the_gate_does_not(store, monkeypatch):
    declare_credit(monkeypatch)
    sent = []

    def broken(prompt):
        sent.append(prompt)
        raise TimeoutError("no answer")
    monkeypatch.setattr(teacher, "cloud_gate", lambda wid, prompt, client=None: {"ok": False, "why": "closed", "estimate": 0, "credit": {}})
    assert teacher.consult({"id": "w1"}, tier="cloud", ask_cloud=broken, prompt="x")["status"] == "refused"
    assert teacher.cloud_given("w1") == 0 and sent == []          # nothing was sent: the one lesson is still available
    monkeypatch.setattr(teacher, "cloud_gate", lambda wid, prompt, client=None: {"ok": True, "why": "", "estimate": 0, "credit": {}})
    assert teacher.consult({"id": "w1"}, tier="cloud", ask_cloud=broken, prompt="x")["status"] == "failed"
    assert teacher.cloud_given("w1") == 1 and len(sent) == 1
    assert teacher.consult({"id": "w1"}, tier="cloud", ask_cloud=broken, prompt="x")["status"] == "refused" and len(sent) == 1


def test_a_learned_method_demoted_by_its_retest_is_ready_to_be_learned_again(store, monkeypatch):
    man = {"id": "pcmethod.close_site_tab", "for_tool": "close_site_tab", "version": 1, "source_sha": "abc"}
    monkeypatch.setattr(acquire, "learned_methods", lambda: [man])
    (store / "learned").mkdir()
    assert acquire.discover([MISS])[0]["status"] == "learned"
    acquire.demote({"id": man["id"], "kind": "method", "version": 1, "source_sha": "abc"}, "failed its retest (0/3)")
    assert "close_site_tab" not in acquire.known_skills() and acquire.discover([MISS])[0]["status"] == "ready"
    man["source_sha"] = "def"                                         # learned again (new code): known again
    assert acquire.discover([MISS])[0]["status"] == "learned"


def test_the_native_method_is_always_among_the_methods_tried(store, monkeypatch):
    from core import pcledger, pcmethods
    step = {"tool": "close_site_tab", "arguments": {"site": "github"}}
    monkeypatch.setattr(pcmethods, "candidates", lambda s: ["learned:pcmethod.x", "skill:skill.close_site_tab", "native"])
    chosen = pcmethods.methods_for(step)
    assert len(chosen) == pcmethods.MAX_ATTEMPTS and chosen == ["learned:pcmethod.x", "native"]
    pcledger.method_outcome(step, "native", True)                     # proven native goes first, as before
    assert pcmethods.methods_for(step)[0] == "native"
    monkeypatch.setattr(pcmethods, "candidates", lambda s: ["native", "windows_builtin"])
    assert pcmethods.methods_for(step) == ["native", "windows_builtin"]


@pytest.mark.parametrize("said, hint, steps", [
    ("turn it off", "Close Spotify.", [{"tool": "close_app", "arguments": {"app": "spotify"}}]),          # a pronoun: depends on the moment
    ("close that for me", "Close the GitHub tab.", [{"tool": "close_site_tab", "arguments": {"site": "github"}}]),
    ("do the same again", "Open Notepad.", [{"tool": "open_app", "arguments": {"app": "notepad"}}]),
    ("tidy the thing up for me", "Close the GitHub tab.", [{"tool": "close_site_tab", "arguments": {"site": "github"}}]),  # invented target
    ("shut the music player", "Close Spotify.", [{"tool": "close_app", "arguments": {"app": "spotify"}}]),               # invented target
])
def test_a_sentence_whose_meaning_depends_on_the_moment_is_never_bound_to_one_wording(store, monkeypatch, said, hint, steps):
    from core import authz, gateway, misslessons
    for s in steps:
        monkeypatch.setitem(authz.EFFECTS, s["tool"], "reversible")
    stuck_miss(said)
    lesson = json.dumps({"failed_assumption": "x", "explanation": "y", "hints": [hint], "references": []})
    rows = misslessons.teach(decide=lambda text, cancelled=None: Plan(steps), ask_local=lambda p: (lesson, 0.0, {}))
    assert rows[0]["outcome"] != "understood" and "taught_as" not in rows[0] and misslessons.taught_as(said) is None
    seen = []
    monkeypatch.setattr(gateway, "_decide", lambda text, history, cancelled, ask: seen.append(text))
    gateway.decide(said)
    assert [misslessons._key(s) for s in seen] == [misslessons._key(said)]          # his own words reach the understanding layer


def test_a_wording_stored_earlier_for_a_moment_bound_sentence_is_not_used(store):
    from core import misslessons
    misslessons._write({"said": "close it", "studied_at": 2.0, "outcome": "understood", "why": "", "tries": 3, "status": "EXPERIMENTAL",
                        "plan": None, "taught_as": "Close Spotify."})
    assert misslessons.taught_as("Close it.") is None
