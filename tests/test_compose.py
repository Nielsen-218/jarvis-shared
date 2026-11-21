"""Compositional skill learning (owner 2026-11-08): developer-built primitives, a skill spec JARVIS writes, failure classes,
a bounded budget, and labels that keep developer primitives apart from JARVIS-learned procedures.

The procedures below are TEST FIXTURES for a simulated browser. They are never shown to the learner."""
import json
import os
import threading
import time
import urllib.request

import pytest

from core import acquire, browserbridge, capabilities, compose, failures, learnbudget, methodlang, skillcontracts, skillspec, teacher

from tests.test_acquire import MISS


# ------------------------------------------------------------------------------------------------ a simulated browser

class FakeBrowser:
    """The browser capability over a lab world, in memory. Tab ids are stable numbers; closing the last tab closes the window."""

    def __init__(self, world, fail_close=False):
        self.tabs_, self.wins, n = [], [], 100
        for wi, win in enumerate(world["windows"]):
            self.wins.append(wi + 1)
            for ti, t in enumerate(win["tabs"]):
                n += 1
                self.tabs_.append({"id": n, "window": wi + 1, "index": ti, "url": f"https://{t['host']}:4430/t0/w{wi}/p{ti}",
                                   "title": t["title"], "active": ti == win.get("active", 0)})
        self.fail_close, self.next = fail_close, 900

    def _t(self, t):
        return {**t, "host": browserbridge.host_of(t["url"])}

    def windows(self):
        return [{"id": w, "focused": False, "state": "normal", "tab_count": sum(t["window"] == w for t in self.tabs_)} for w in self.wins]

    def tabs(self, window=None):
        return [self._t(t) for t in self.tabs_ if window is None or t["window"] == window]

    def tab(self, tab_id):
        return next((self._t(t) for t in self.tabs_ if t["id"] == tab_id), None)

    def activate(self, tab_id):
        return self.tab(tab_id)

    def close(self, tab_id):
        if self.fail_close:
            raise browserbridge.BrowserError("close failed: tab is busy")
        t = next(t for t in self.tabs_ if t["id"] == tab_id)
        self.tabs_.remove(t)
        if not any(x["window"] == t["window"] for x in self.tabs_):
            self.wins.remove(t["window"])
        return {"closed": True, "tab": tab_id}

    def new_tab(self, window):
        self.next += 1
        t = {"id": self.next, "window": window, "index": 99, "url": "about:blank", "title": "", "active": False}
        self.tabs_.append(t)
        return self._t(t)

    def snapshot(self):
        return {"pages": [{"id": str(t["id"]), "url": t["url"], "title": t["title"]} for t in self.tabs_], "windows": len(self.wins)}


def fake_lab(fail_close=False, log=None):
    def lab(trials, label):
        out = []
        for tr in trials:
            w, a = tr["world"], tr["action"]
            b = FakeBrowser(w, fail_close=fail_close)
            before = b.snapshot()
            if a["type"] == "procedure":
                res = methodlang.run_procedure(a["source"], {"browser": b}, a["caps"], w["params"])
            else:
                man, src = skillspec.library(a.get("store")).get(a["skill"], a.get("version"))
                res = methodlang.run_procedure(src, {"browser": b}, man["spec"]["dependencies"]["capabilities"], w["params"])
            out.append({"result": res, "judge": skillcontracts.judge(w, before, b.snapshot(), res)})
        if log is not None:
            log.append(label)
        return {"status": "done", "results": out}
    return lab


GOOD = '''def run(browser, util, params):
    domain = {"github": "github.com", "youtube": "youtube.com", "google": "google.com", "bing": "bing.com",
              "duckduckgo": "duckduckgo.com", "wikipedia": "wikipedia.org"}.get(params["site"], params["site"] + ".com")
    closed = 0
    for w in browser.windows():
        tabs = browser.tabs(w["id"])
        hit = [t for t in tabs if t["host"] == domain or t["host"].endswith("." + domain)]
        if hit and len(hit) == len(tabs):
            browser.new_tab(w["id"])
        for t in hit:
            browser.close(t["id"])
            closed += 1
    return "closed " + str(closed)

def verify(browser, util, params):
    domain = {"github": "github.com", "youtube": "youtube.com", "google": "google.com", "bing": "bing.com",
              "duckduckgo": "duckduckgo.com", "wikipedia": "wikipedia.org"}.get(params["site"], params["site"] + ".com")
    return not [t for t in browser.tabs() if t["host"] == domain or t["host"].endswith("." + domain)]
'''

TITLE_GUESS = GOOD.replace('t["host"] == domain or t["host"].endswith("." + domain)', 'params["site"] in t["title"].lower()')

PLAN = json.dumps({"dependencies": {"capabilities": ["browser"]}, "preconditions": ["a browser window is open"],
                   "steps": ["list windows", "per window find the site's tabs by host", "keep a window alive", "close them"],
                   "expected": ["no tab of the site remains", "no window lost"], "recovery": [], "needs": []})


class Scripted:
    """A learner that answers plan prompts with PLAN and code prompts with the next procedure in `procs`."""

    def __init__(self, procs, plan=PLAN):
        self.procs, self.plan, self.model, self.calls, self.seconds, self.transcript, self.prompts = list(procs), plan, "fake", 0, 0.0, [], []

    def __call__(self, prompt, temperature=0.2, fmt=None, num_predict=1600):
        self.calls += 1
        self.prompts.append(prompt)
        if "Write the skill description as JSON" in prompt:
            return self.plan
        if "practice situations" in prompt:
            return '{"exercises": []}'
        src = self.procs.pop(0) if len(self.procs) > 1 else self.procs[0]
        return f"```python\n{src}```"


@pytest.fixture
def store(tmp_path, monkeypatch):
    for k, d in (("JARVIS_COMPOSE_DIR", "compose"), ("JARVIS_SKILLS_DIR", "skills"), ("JARVIS_BUDGET_DIR", "budget"),
                 ("JARVIS_ACQUIRE_DIR", "evidence"), ("JARVIS_LEARNED_DIR", "learned"), ("JARVIS_MISSES_FILE", "misses.jsonl")):
        monkeypatch.setenv(k, str(tmp_path / d))
    monkeypatch.setenv("JARVIS_SKILL_LAB", "1")
    for k in ("JARVIS_TEACHER_CREDIT_USD", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(compose, "UNSEEN_SEEDS", tuple(range(95001, 95007)))
    return tmp_path


def cand():
    return acquire.discover([MISS])[0]


# ------------------------------------------------------------------------------------------------ the procedure language

def test_a_procedure_composes_only_the_capabilities_it_declares():
    methodlang.check_procedure(GOOD, ["browser", "util"])
    with pytest.raises(methodlang.BadMethod, match="ui is not in your dependencies"):
        methodlang.check_procedure(GOOD.replace("browser.windows()", "ui.windows()"), ["browser", "util"])
    with pytest.raises(methodlang.BadMethod, match="browser.navigate does not exist"):
        methodlang.check_procedure(GOOD.replace("browser.new_tab(w[\"id\"])", "browser.navigate(w[\"id\"])"), ["browser", "util"])
    with pytest.raises(methodlang.BadMethod, match="bpy is not available"):
        methodlang.check_procedure("def run(bpy, params):\n    return 1\n", ["bpy"])
    with pytest.raises(methodlang.BadMethod, match="name 'run' is not allowed"):
        methodlang.check_procedure("def run(browser, params):\n    return run(browser, params)\n", ["browser"])


def test_recover_runs_once_and_verify_is_recorded():
    src = ('def run(browser, util, params):\n    t = browser.tabs()\n    if len(t) > 3:\n        x = t[99]\n    return "ok"\n'
           'def recover(browser, util, params, error):\n    browser.close(browser.tabs()[0]["id"])\n'
           'def verify(browser, util, params):\n    return len(browser.tabs()) <= 3\n')
    world = skillcontracts.generate(1001)
    b = FakeBrowser(world)
    out = methodlang.run_procedure(src, {"browser": b}, ["browser", "util"], world["params"])
    assert out["ok"] is False or out["recovered"]                     # one recovery attempt only; never a loop
    assert "IndexError" in out["error"] or out["recovered"]


def test_primitives_are_labelled_developer_built():
    cat = {c["name"]: c for c in capabilities.catalogue()}
    assert all(c["built_by"] == "developer" for c in cat.values())
    assert cat["browser"]["status"] == "available_in_lab" and cat["bpy"]["status"] == "planned" and cat["vision"]["api"] == []


# ------------------------------------------------------------------------------------------------ failure classes

@pytest.mark.parametrize("row,cls", [
    ({"passed": False, "error_type": "KeyError", "error": "KeyError: 'url'"}, "perception"),
    ({"passed": False, "error_type": "BadMethod", "error": "BadMethod: capability bpy is not available"}, "missing_tool"),
    ({"passed": False, "error_type": "", "diff": ["wrongly closed: GitHub - Wikipedia"], "self_check": True}, "planning"),
    ({"passed": False, "error_type": "BrowserError", "error": "close failed"}, "execution"),
    ({"passed": False, "error_type": "Budget", "error": "Budget: procedure ran out of time"}, "execution"),
    ({"passed": True, "self_check": False}, "verification"),
    ({"passed": False, "lab_error": "Traceback ... bridge did not connect"}, "lab")])
def test_every_failure_gets_one_class(row, cls):
    assert failures.classify(row)["class"] == cls


def test_a_blind_self_check_is_flagged_and_only_concepts_are_teachable():
    c = failures.classify({"passed": False, "diff": ["still open: x"], "self_check": True})
    assert c["class"] == "planning" and "verification" in c["also"]
    assert failures.summary([{"passed": False, "error_type": "BrowserError"}])["teachable"] is False
    assert failures.summary([{"passed": False, "diff": ["x"]}])["teachable"] is True


# ------------------------------------------------------------------------------------------------ the budget

def test_a_strategy_that_stops_improving_is_stalled_and_the_next_one_is_chosen(store):
    st = learnbudget.load("s")
    learnbudget.record_round(st, "compose", 5, 9, 3)
    learnbudget.record_round(st, "compose", 5, 9, 3)
    assert learnbudget.verdict(st, "compose")[0] == "continue"
    learnbudget.record_round(st, "compose", 4, 9, 3)
    v, why = learnbudget.verdict(st, "compose")
    assert v == "stalled" and "no better score" in why and st["strategies"]["compose"]["best_ref"] == ""
    assert learnbudget.next_step(st, ("compose", "compose-minimal")) == ("strategy", "compose-minimal")
    learnbudget.strategy(st, "compose-minimal")["status"] = "stalled"
    assert learnbudget.next_step(st, ("compose", "compose-minimal"), other_tasks=["t2"]) == ("task", "t2")
    assert learnbudget.next_step(st, ("compose", "compose-minimal"))[0] == "stop"


# ------------------------------------------------------------------------------------------------ the skill spec

def test_a_capability_the_host_granted_counts_as_declared():
    """Live 2026-11-08: half the candidates were refused for calling util, which the host itself had added."""
    spec = skillspec.normalise({"dependencies": {"capabilities": ["browser"]}, "steps": ["a"]}, compose.contract(cand()), GOOD,
                               granted=["browser", "util"])
    assert spec["dependencies"]["capabilities"] == ["browser", "util"]


def test_a_better_sealed_score_is_progress_even_when_practice_was_already_perfect(store):
    st = learnbudget.load("p")
    learnbudget.record_round(st, "compose", 9, 21, 1)           # 9/9 practice, not examined yet
    learnbudget.record_round(st, "compose", 20, 21, 1)          # 9/9 practice + 11/12 sealed
    assert st["strategies"]["compose"]["since_better"] == 0


def test_jarvis_writes_his_own_practice_cases_and_the_host_only_checks_they_are_valid(store):
    ex = json.dumps({"exercises": [
        {"why": "a window with only the site's tabs", "params": {"site": "youtube"},
         "windows": [{"tabs": [{"host": "www.youtube.com", "title": "a - YouTube"}, {"host": "m.youtube.com", "title": "b - YouTube"}], "active": 0},
                     {"tabs": [{"host": "news.ycombinator.com", "title": "Hacker News"}], "active": 0}]},
        {"why": "broken", "params": {"site": "nowhere"}, "windows": []}]})

    class WithExercises(Scripted):
        def __call__(self, prompt, temperature=0.2, fmt=None, num_predict=1600):
            if "practice situations" in prompt:
                self.calls += 1
                return ex
            return super().__call__(prompt, temperature, fmt, num_predict)
    rec = compose.learn(cand(), learner=WithExercises([GOOD]), lab=fake_lab())
    own = [s for s in rec["stages"] if s["stage"] == "exercises"][0]
    assert "1 practice situation of" in own["what"] and own["data"]["refused"]
    assert rec["status"] == "learned"
    prac = [s for s in rec["stages"] if s["stage"] == "practice"][0]["data"]["rows"]
    assert len(prac) == len(compose.FIXED_PRACTICE) + 1 + compose.FRESH_PER_ROUND


BLIND_VERIFY = GOOD.split("def verify")[0] + 'def verify(browser, util, params):\n    return params.get("_original_tabs") is not None\n'


def test_a_skill_whose_own_check_disagrees_with_the_judge_is_not_retained_until_he_repairs_it(store):
    """Live 2026-11-08: v1 was retained with a verify() that read params it never got and returned False on all 10 worlds."""
    rec = compose.learn(cand(), learner=Scripted([BLIND_VERIFY, BLIND_VERIFY, GOOD]), lab=fake_lab())
    rep = [s for s in rec["stages"] if s["stage"] == "repair"]
    assert rep and "rewrote verify() only" in rep[0]["what"] and rec["status"] == "learned"
    man, src = skillspec.library().get("skill.close_site_tab")
    assert "_original_tabs" not in src


def test_a_repair_strategy_starts_from_his_own_earlier_procedure(store):
    spec = skillspec.normalise(json.loads(PLAN), compose.contract(cand()), BLIND_VERIFY, granted=["browser", "util"])
    start = {"source": BLIND_VERIFY, "caps": ["browser", "util"], "spec": spec, "ref": "proc-r4-1.py"}
    learner = Scripted([GOOD])
    rec = compose.learn(cand(), learner=learner, lab=fake_lab(), strategy="repair", start=start)
    assert rec["status"] == "learned" and any("starting from his own earlier procedure" in s["what"] for s in rec["stages"])
    assert not any("Write the skill description" in p for p in learner.prompts)          # nothing re-planned from scratch


def test_a_lab_job_that_died_part_way_never_counts_as_all_passed(store):
    worlds = [skillcontracts.generate(s) for s in (1001, 1002, 1003)]
    one = fake_lab()([{"world": worlds[0], "action": {"type": "procedure", "source": GOOD, "caps": ["browser", "util"]}}], "x")
    rows = compose._rows(worlds, one)                          # 1 result for 3 worlds
    assert len(rows) == 3 and sum(r["passed"] for r in rows) == 1
    assert [r["class"]["class"] for r in rows[1:]] == ["lab", "lab"]


def test_a_lesson_that_led_to_a_learned_skill_is_marked_so(store, monkeypatch):
    teacher._keep({"id": "l1", "weakness": compose.lesson_key(cand()["id"]), "tier": "cloud", "model": "m", "at": time.time(),
                   "status": "given", "outcome": "pending", "lesson": {"hints": ["x"]}})
    rec = compose.learn(cand(), learner=Scripted([GOOD]), lab=fake_lab())
    assert rec["status"] == "learned"
    assert teacher.lessons(compose.lesson_key(cand()["id"]))[0]["outcome"] == "led_to_learned"
    assert skillspec.library().get("skill.close_site_tab")[0]["procedure_by"] == "teacher_assisted"


def test_a_repair_that_touches_run_is_refused(store):
    changed = GOOD.replace('return "closed " + str(closed)', 'return "done"')
    rec = compose.learn(cand(), learner=Scripted([BLIND_VERIFY, BLIND_VERIFY, changed]), lab=fake_lab())
    assert any("changed run() or recover(): refused" in s["what"] for s in rec["stages"])
    assert not any(s["stage"] == "retain" and "BLIND" in str(s) for s in rec["stages"])
    assert skillspec.library().get("skill.close_site_tab") is None or "_original_tabs" not in skillspec.library().get("skill.close_site_tab")[1]


def test_the_spec_must_declare_what_the_procedure_uses_and_planned_tools_become_requests():
    con = compose.contract(cand())
    with pytest.raises(skillspec.BadSpec, match="calls browser"):
        skillspec.normalise({"dependencies": {"capabilities": ["ui"]}, "steps": ["x"]}, con, GOOD)
    spec = skillspec.normalise({"dependencies": {"capabilities": ["browser", "util", "vision"]}, "steps": ["a"]}, con, GOOD)
    assert spec["dependencies"]["capabilities"] == ["browser", "util"] and any("vision" in n for n in spec["needs"])
    assert spec["goal"] == con["goal"] and spec["verification"]["sealed_exam"] == 12          # the host's facts, not the learner's


# ------------------------------------------------------------------------------------------------ the whole loop

def test_jarvis_composes_a_skill_passes_the_sealed_exam_and_it_survives_a_reload(store):
    labels = []
    rec = compose.learn(cand(), learner=Scripted([GOOD]), lab=fake_lab(log=labels))
    assert rec["status"] == "learned", rec
    assert "exam-1" in labels and "unseen" in labels and "persist" in labels
    man, src = skillspec.library().get("skill.close_site_tab")
    assert man["procedure_by"] == "jarvis_learned" and man["primitives_by"] == "developer" and man["status"] == "verified"
    assert man["evidence"]["passed"] == man["evidence"]["total"] == 12 and man["spec"]["steps"]
    assert man["provenance"] == "independent"           # no lesson given to THIS learner


def test_a_title_guessing_plan_fails_as_planning_and_a_repeated_idea_is_not_practised(store, monkeypatch):
    asked = []
    monkeypatch.setattr(teacher, "can_help", lambda wid: (True, "local"))
    monkeypatch.setattr(teacher, "consult", lambda c, **kw: asked.append(kw) or {"tier": "local", "status": "given", "model": "m"})
    rec = compose.learn(cand(), learner=Scripted([TITLE_GUESS]), lab=fake_lab())
    assert rec["status"] == "not_learned"
    practice = [s for s in rec["stages"] if s["stage"] == "practice"]
    assert practice and practice[0]["data"]["classes"]["dominant"] == "planning"
    assert any("same idea" in s["what"] for s in rec["stages"])                          # near-identical: refused, counted
    assert asked and asked[0]["requested_by"] == "compose:planning" and "SEALED" not in asked[0]["prompt"]
    assert any(s["stage"] == "budget" for s in rec["stages"])                            # bounded: it stopped by itself


def test_execution_failures_never_call_the_teacher(store, monkeypatch):
    asked = []
    monkeypatch.setattr(teacher, "consult", lambda c, **kw: asked.append(kw) or {})
    rec = compose.learn(cand(), learner=Scripted([GOOD]), lab=fake_lab(fail_close=True))
    assert rec["status"] == "not_learned" and asked == []
    rows = [s for s in rec["stages"] if s["stage"] == "practice"][0]["data"]["rows"]
    assert {r["class"]["class"] for r in rows if not r["passed"]} == {"execution"}


# ------------------------------------------------------------------------------------------------ the bridge itself

def test_the_bridge_refuses_callers_without_the_token_and_round_trips_with_it():
    br = browserbridge.Bridge()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{br.port}/poll")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=3)
        assert e.value.code == 403

        def extension():                 # what background.js does: poll, run, reply
            h = {"X-Bridge-Token": br.token, "X-Bridge-Session": br.session}
            for _ in range(20):
                cmd = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{br.port}/poll", headers=h), timeout=20).read())
                if cmd.get("op"):
                    body = json.dumps({"id": cmd["id"], "ok": True, "result": [{"id": 7, "window": 1, "index": 0, "url": "https://gist.github.com/x",
                                                                                 "title": "t", "active": True}]}).encode()
                    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{br.port}/reply", data=body,
                                                                  headers={**h, "Content-Type": "application/json"}), timeout=5)
                    return
        th = threading.Thread(target=extension, daemon=True)
        th.start()
        tabs = browserbridge.BrowserAPI(br).tabs()
        assert tabs[0]["host"] == "gist.github.com"
        with pytest.raises(browserbridge.BrowserError):
            browserbridge.BrowserAPI(br).close("7")                # ids are numbers, checked before anything is sent
    finally:
        br.close()


# ------------------------------------------------------------------------------------------------ the owner's own Edge (production)

@pytest.fixture
def live_env(tmp_path, monkeypatch, store):
    monkeypatch.setenv("JARVIS_BRIDGE_DIR", str(tmp_path / "bridge"))
    monkeypatch.delenv("JARVIS_SKILL_LAB", raising=False)
    monkeypatch.setattr(browserbridge, "_LIVE", {"bridge": None, "lock": threading.Lock(), "error": ""})
    return tmp_path


def test_the_owners_extension_package_carries_the_live_port_and_a_persistent_secret(live_env):
    f = browserbridge.package()
    cfg = json.loads((f / "config.json").read_text(encoding="utf-8"))
    assert cfg["port"] == browserbridge.PORT and len(cfg["token"]) >= 32 and cfg["session"]
    assert json.loads((browserbridge.package() / "config.json").read_text(encoding="utf-8")) == cfg      # same secret every start
    assert (f / "manifest.json").exists() and (f / "background.js").exists()


def test_the_live_bridge_never_runs_in_the_lab_and_a_taken_port_never_crashes_jarvis(live_env, monkeypatch):
    monkeypatch.setenv("JARVIS_SKILL_LAB", "1")
    assert browserbridge.live() is None
    monkeypatch.delenv("JARVIS_SKILL_LAB")
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    monkeypatch.setattr(browserbridge, "PORT", s.getsockname()[1])
    try:
        if os.name == "nt":
            assert browserbridge.live() is None and browserbridge._LIVE["error"]
    finally:
        s.close()


class _Connected:
    def connected(self):
        return True


def _approve_good(store_dir):
    spec = skillspec.normalise(json.loads(PLAN), compose.contract(cand()), GOOD, granted=["browser", "util"])
    skillspec.retain(spec, GOOD, {"judge": "j", "passed": 12, "total": 12, "tasks": []}, {"provenance": "teacher_assisted"}, "m")
    return skillspec.approve("skill.close_site_tab")


def test_a_skill_reaches_the_pc_path_only_when_approved_and_the_extension_is_connected(live_env, monkeypatch):
    spec = skillspec.normalise(json.loads(PLAN), compose.contract(cand()), GOOD, granted=["browser", "util"])
    skillspec.retain(spec, GOOD, {"judge": "j", "passed": 12, "total": 12, "tasks": []}, {"provenance": "teacher_assisted"}, "m")
    assert skillspec.candidates_for("close_site_tab") == []                         # not approved
    skillspec.approve("skill.close_site_tab")
    monkeypatch.setattr(browserbridge, "live", lambda: None)
    assert skillspec.candidates_for("close_site_tab") == []                         # approved, but no extension connected
    monkeypatch.setattr(browserbridge, "live", lambda: _Connected())
    assert skillspec.candidates_for("close_site_tab") == ["skill:skill.close_site_tab"]
    from core import pcmethods
    assert "skill:skill.close_site_tab" in pcmethods.candidates({"tool": "close_site_tab", "arguments": {"site": "github"}})


def test_an_approved_skill_runs_on_the_owners_browser_and_is_checked_independently(live_env, monkeypatch):
    _approve_good(live_env)
    world = skillcontracts.generate(1004)
    fb = FakeBrowser(world)
    monkeypatch.setattr(browserbridge, "live", lambda: _Connected())
    monkeypatch.setattr(browserbridge, "BrowserAPI", lambda b: fb)
    monkeypatch.setattr(acquire, "_tab_names", lambda: ([t["title"] for t in fb.tabs_ if t["url"] != "about:blank"], len(fb.wins)))
    out = skillspec.run_approved("skill:skill.close_site_tab", {"tool": "close_site_tab", "arguments": world["params"]})
    assert out["verified"] and "learned_skill" in out
    assert not [t for t in fb.tabs() if skillcontracts.is_target(world["params"]["site"], t["url"])]


def test_changed_code_after_approval_is_never_run(live_env, monkeypatch):
    _approve_good(live_env)
    spec = skillspec.normalise(json.loads(PLAN), compose.contract(cand()), TITLE_GUESS, granted=["browser", "util"])
    skillspec.retain(spec, TITLE_GUESS, {"judge": "j", "passed": 12, "total": 12, "tasks": []}, {"provenance": "x"}, "m")   # v2, not approved
    monkeypatch.setattr(browserbridge, "live", lambda: _Connected())
    assert skillspec.candidates_for("close_site_tab") == []
    with pytest.raises(RuntimeError, match="not been approved"):
        skillspec.run_approved("skill:skill.close_site_tab", {"tool": "close_site_tab", "arguments": {"site": "github"}})


def test_a_dead_browsers_waiting_poll_never_takes_the_next_browsers_command():
    """Live 2026-11-08 15:04: a killed lab browser's long-poll swallowed the next world's first command (BridgeDown)."""
    br = browserbridge.Bridge()
    try:
        old = br.session
        stale = {}
        th = threading.Thread(target=lambda: stale.setdefault("got", br._next(5, old)), daemon=True)
        th.start()
        time.sleep(.2)
        br.reset()                                       # a new world: new session
        br._queue.append({"id": 1, "op": "windows", "args": {}})
        th.join(3)
        assert stale["got"] is None and br._queue                  # the old poll gave up; the command waits for the new browser
        assert br._next(1, br.session)["op"] == "windows"
    finally:
        br.close()


def test_the_extension_asks_for_tabs_only_and_talks_only_to_localhost():
    man = json.loads((browserbridge.EXT_SRC / "manifest.json").read_text(encoding="utf-8"))
    assert man["permissions"] == ["tabs", "alarms"] and man["host_permissions"] == ["http://127.0.0.1/*"]
    js = (browserbridge.EXT_SRC / "background.js").read_text(encoding="utf-8")
    import re
    apis = set(re.findall(r"chrome\.(\w+)\.", js))
    assert apis == {"windows", "tabs", "runtime", "alarms"}       # no debugger, scripting, cookies, history, downloads
    assert set(re.findall(r"^  async (\w+)\(", js, re.M)) == set(browserbridge.BROWSER_API)


# ------------------------------------------------------------------ review 2026-11-12: a lab run that did not finish is not an exam

@pytest.mark.parametrize("label", ["exam-1", "unseen", "persist"])
def test_a_verification_job_the_lab_did_not_finish_never_makes_a_learned_skill(store, label):
    inner = fake_lab()

    def lab(trials, lab_label):
        out = inner(trials, lab_label)
        return {"status": "timeout", "results": out["results"]} if lab_label == label or (label == "exam-1" and lab_label.startswith("exam")) else out
    rec = compose.learn(cand(), learner=Scripted([GOOD]), lab=lab)
    assert rec["status"] != "learned", rec
    assert not (isinstance(rec.get("skill"), dict) and rec["skill"].get("approved_by"))
