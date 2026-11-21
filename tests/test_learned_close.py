"""A learned close_foreground method: its production check, retention + auto-approval, the run-time safety wrapper, selection and
counting. Nothing here starts a window, a lab, a model or reads the real desktop: the lab, the machine probes and the UI are fakes,
and the end states the judge reads are built by hand in the formats the lab's own probe produces."""
import copy
import json
from types import SimpleNamespace

import pytest

from core import acquire, independence, labpractice, labworlds, learned, learnedclose, misses, pcledger, pcmethods, skillcontracts

REF = labworlds.REFERENCE["close_foreground"]
NOOP = "def run(ui, params):\n    return 'nothing'\n"
DONT_SAVE = ("def run(ui, params):\n    front = ui.windows()[0]\n"
             "    ui.invoke(ui.find(front['id'], role='Button', name=\"Don't Save\")[0]['id'])\n    return 'discarded'\n")
STEP = {"tool": "close_foreground", "arguments": {}}


# ------------------------------------------------------------------------------------------------ the production judge

def start(world):
    wins = sorted(world["windows"], key=lambda w: not w.get("front"))
    return {"windows": [{"title": w["title"], "edit": w.get("edit"), "status": "pressed="} for w in wins], "files": {},
            "foreign": [{"title": f["title"], "edit": f["edit"], "status": "pressed="} for f in world["foreign"]]}


def after_correct(world, before):
    after = copy.deepcopy(before)
    if world["expect"].get("prompt"):          # the app asks; the method stops
        after["windows"].insert(0, {"title": world["expect"]["prompt"], "edit": None, "status": "asking"})
    else:
        after["windows"] = after["windows"][1:]
    return after


OK = {"ok": True, "message": "closed"}
SEEDS = list(skillcontracts.PRODUCTION_CHECK_SEEDS) + list(skillcontracts.RETEST_CHECK_SEEDS)


def test_the_check_worlds_cover_every_variant_with_a_foreign_program_and_unsaved_documents():
    worlds = skillcontracts.production_check_worlds("close_foreground")
    assert len(worlds) == 8 and {w["check"] for w in worlds} == set(labworlds.CHECK_VARIANTS)
    assert worlds == skillcontracts.production_check_worlds("close_foreground")           # same seeds, same worlds
    for w in worlds:
        assert w["foreign"] and sum(1 for x in w["windows"] if x["front"]) == 1
        front = next(x for x in w["windows"] if x["front"])
        assert bool(front.get("unsaved")) == w["check"].startswith("unsaved") == bool(w["expect"].get("prompt"))
        if front.get("unsaved"):
            assert front["edit"]                                                               # an unsaved document has text
    sib = next(w for w in worlds if w["check"] == "sibling")
    front = next(x for x in sib["windows"] if x["front"])
    assert sum(1 for x in sib["windows"] if x["app"] == front["app"]) == 2
    assert len({x["title"] for x in sib["windows"]}) == len(sib["windows"])
    with pytest.raises(ValueError):
        labworlds.generate_check(1, "close_app")


@pytest.mark.parametrize("seed", SEEDS)
def test_the_correct_end_state_passes_and_doing_nothing_fails(seed):
    world = labworlds.generate_check(seed)
    before = start(world)
    verdict = skillcontracts.production_check_desktop(world, before, after_correct(world, before), OK)
    assert verdict["passed"], verdict
    noop = skillcontracts.production_check_desktop(world, before, copy.deepcopy(before), OK)
    assert not noop["passed"]                                                                  # a no-op fails, plain or unsaved
    assert not skillcontracts.production_check_desktop(world, before, after_correct(world, before), {"ok": False, "error": "x"})["passed"]


def worlds_of(variant):
    return [labworlds.generate_check(s) for s in SEEDS if labworlds.CHECK_VARIANTS[s % 4] == variant]


def test_a_method_that_closes_a_second_window_fails():
    for world in worlds_of("plain") + worlds_of("sibling"):
        before = start(world)
        after = after_correct(world, before)
        after["windows"] = after["windows"][1:]                                                # a second window gone too
        verdict = skillcontracts.production_check_desktop(world, before, after, OK)
        assert not verdict["passed"] and not verdict["checks"]["only_the_front_window_closed"]
        assert "wrongly closed" in " ".join(verdict["diff"])
    # the foreign program's window closed, or typed into
    world = worlds_of("plain")[0]
    before = start(world)
    for change in ("closed", "typed"):
        after = after_correct(world, before)
        after["foreign"] = [] if change == "closed" else [{**after["foreign"][0], "edit": "x"}]
        verdict = skillcontracts.production_check_desktop(world, before, after, OK)
        assert not verdict["passed"] and not verdict["checks"]["foreign_program_untouched"]


def test_a_method_that_dismisses_the_save_prompt_fails_and_one_that_stops_passes():
    for world in worlds_of("unsaved") + worlds_of("unsaved_sibling"):
        before = start(world)
        front = before["windows"][0]["title"]
        assert skillcontracts.production_check_desktop(world, before, after_correct(world, before), OK)["passed"]
        stopped = {"ok": False, "error": "MethodFailed: it is asking about unsaved changes", "error_type": "MethodFailed"}
        assert skillcontracts.production_check_desktop(world, before, after_correct(world, before), stopped)["passed"]   # stop and report
        crashed = {"ok": False, "error": "KeyError: x", "error_type": "KeyError"}
        assert not skillcontracts.production_check_desktop(world, before, after_correct(world, before), crashed)["passed"]
        # Don't Save: both windows gone, the discard flag on disk
        gone = copy.deepcopy(before)
        gone["windows"] = gone["windows"][1:]
        gone["files"] = {"prompt-dont-save.flag": "1", "discarded.flag": "1"}
        verdict = skillcontracts.production_check_desktop(world, before, gone, OK)
        assert not verdict["passed"] and not verdict["checks"]["prompt_left_open"] and not verdict["checks"]["no_prompt_answered"]
        # the prompt still open but a button was pressed (Cancel / Save) is also not "leave it to the owner"
        pressed = after_correct(world, before)
        pressed["files"] = {"prompt-cancel.flag": "1"}
        assert not skillcontracts.production_check_desktop(world, before, pressed, OK)["checks"]["no_prompt_answered"]
        # the document window closed under the prompt, or its text lost
        lost = after_correct(world, before)
        lost["windows"] = [w for w in lost["windows"] if w["title"] != front]
        assert not skillcontracts.production_check_desktop(world, before, lost, OK)["checks"]["document_kept"]


# ------------------------------------------------------------------------------------------------ the scoped ui + wrapper

class FakeUI:
    """Two top-level windows (hwnd 100 and 200), each with Close and Don't Save buttons and a text box."""

    def __init__(self):
        self.rows = {1: ("Window", "A - LabPad", 100), 2: ("Window", "B - LabSheet", 200)}
        for base, win in ((10, 1), (20, 2)):
            self.rows[base + 1] = ("Button", "Close", 0)
            self.rows[base + 2] = ("Button", "Don't Save", 0)
            self.rows[base + 3] = ("Edit", "text", 0)
        self.owner = {11: 1, 12: 1, 13: 1, 21: 2, 22: 2, 23: 2}
        self.rows[14], self.rows[24] = ("TitleBar", "", 0), ("TitleBar", "", 0)        # a Win32 window's Close sits under its TitleBar
        self.owner.update({14: 1, 24: 2})
        self.par = {11: 14, 21: 24, 14: 1, 24: 2}                  # the immediate parent (owner is the top-level window it belongs to)
        self.invoked = []

    def _row(self, i):
        role, name, _ = self.rows[i]
        return {"id": i, "role": role, "name": name, "window": self.owner.get(i, i)}

    def hwnd_of(self, eid):
        return self.rows[int(eid)][2]

    def windows(self):
        return [{"id": i, "title": self.rows[i][1], "process": "p.exe", "class": "c"} for i in (1, 2)]

    def find(self, within_id=None, role="", name="", contains="", within=None):
        return [self._row(i) for i, w in self.owner.items() if w == within_id and (not role or self.rows[i][0] == role)
                and (not name or self.rows[i][1].casefold() == name.casefold())]

    def children(self, eid=None, id=None):
        return self.find(eid)

    def parent(self, eid=None, id=None):
        up = self.par.get(eid, self.owner.get(eid))
        return self._row(up) if up else None

    def info(self, eid=None, id=None):
        return {**self._row(eid), "exists": True}

    def value(self, eid=None, id=None):
        return ""

    def set_value(self, eid=None, text="", id=None):
        self.invoked.append(("type", eid))

    def invoke(self, eid=None, id=None):
        self.invoked.append(("invoke", eid))
        return True

    def select(self, eid=None, id=None):
        self.invoked.append(("select", eid))

    def wait(self, seconds=0.5):
        pass


def test_the_reference_method_closes_only_the_target_window_whichever_window_it_is():
    for target, button in ((100, 11), (200, 21)):
        ui = FakeUI()
        out = learnedclose.run_method(REF, target, ui)
        assert out["ok"], out
        assert ui.invoked == [("invoke", button)] and out["pressed"] == ["Close"]       # the OTHER window's Close was never pressed


@pytest.mark.parametrize("source, why", [
    (DONT_SAVE, "only press Close"),
    ("def run(ui, params):\n    ui.set_value(ui.find(ui.windows()[0]['id'], role='Edit')[0]['id'], 'x')\n    return 'x'\n", "does not type"),
    ("def run(ui, params):\n    ui.select(ui.find(ui.windows()[0]['id'], role='Edit')[0]['id'])\n    return 'x'\n", "does not select"),
    ("def run(ui, params):\n    ui.invoke(21)\n    return 'x'\n", "not part of the window"),                  # a guessed id of another window's button
    ("def run(ui, params):\n    ui.find(2, role='Button')\n    return 'x'\n", "not part of the window"),
])
def test_a_method_cannot_reach_another_window_type_select_or_press_anything_but_close(source, why):
    ui = FakeUI()
    out = learnedclose.run_method(source, 100, ui)
    assert not out["ok"] and out["error_type"] == "ScopeError" and why in out["error"]
    assert ui.invoked == []


def test_the_scoped_windows_list_shows_only_the_target():
    assert [w["title"] for w in learnedclose.ScopedUI(FakeUI(), 200).windows()] == ["B - LabSheet"]
    assert learnedclose.ScopedUI(FakeUI(), 999).windows() == []


class FakeEnv(learnedclose.Env):
    """The machine, as the test says it is: one foreground window and a window that goes (or does not) when asked."""

    def __init__(self, **kw):
        self.t = {"hwnd": 100, "pid": 4000, "process": "notepad.exe", "cls": "notepad", "title": "notes - Notepad"}
        self.t.update(kw.pop("target", {}))
        self.cfg = {"desktop": True, "game": "", "full": False, "is_game": False, "opened": False, "pointed": False, "gone": True,
                    "prompt": None, "shown": True, "front": None, "mta": True}
        self.cfg.update(kw)
        self.ui_obj = FakeUI()
        self.required = 0

    def require_foreground(self):
        self.required += 1

    def foreground(self):
        return dict(self.t) if self.t else None

    def front_hwnd(self):
        return self.cfg["front"] if self.cfg["front"] is not None else (self.t or {}).get("hwnd", 0)

    def input_desktop_ok(self):
        return self.cfg["desktop"]

    def game(self):
        return self.cfg["game"]

    def fullscreen(self, hwnd):
        return self.cfg["full"]

    def is_game_window(self, hwnd):
        return self.cfg["is_game"]

    def own_pids(self):
        return {1, 2}

    def jarvis_opened_recently(self):
        return self.cfg["opened"]

    def owner_pointed(self):
        return self.cfg["pointed"]

    def gone(self, hwnd):
        return self.cfg["gone"]

    def shown(self, hwnd):
        return self.cfg["shown"]

    def save_prompt(self, hwnd):
        return self.cfg["prompt"]

    def ui(self):
        return self.ui_obj


@pytest.mark.parametrize("env, why", [
    (dict(target=None), "no window"),
    (dict(target={"process": "pythonw.exe"}), "own windows"),
    (dict(target={"pid": 2}), "own windows"),
    (dict(target={"title": "Jarvis Command Center - Edge"}), "Command Center"),
    (dict(target={"title": "JARVIS"}), "Command Center"),
    (dict(target={"cls": "Progman", "process": "explorer.exe", "title": "Program Manager"}), "desktop or the taskbar"),
    (dict(target={"cls": "Shell_TrayWnd", "process": "explorer.exe", "title": "x"}), "desktop or the taskbar"),
    (dict(target={"process": "searchhost.exe"}), "desktop or the taskbar"),
    (dict(target={"process": "lockapp.exe"}), "system program"),
    (dict(target={"process": "consent.exe"}), "system program"),
    (dict(desktop=False), "lock screen"),
    (dict(game="Some Game"), "game is running"),
    (dict(full=True), "full-screen"),
    (dict(is_game=True), "is a game"),
    (dict(opened=True), "just opened"),
    (dict(target={"process": ""}), "which program"),
])
def test_the_wrapper_refuses_without_touching_anything(env, why):
    fake = FakeEnv(**env) if env.get("target", 1) is not None else FakeEnv()
    if env.get("target", 1) is None:
        fake.t = None
    with pytest.raises(learnedclose.Refused, match=why):
        learnedclose.run_learned("pcmethod.close_foreground", {"version": 1}, REF, STEP, fake)
    assert fake.ui_obj.invoked == [] and fake.required == 1


def test_an_owner_who_points_at_the_front_window_is_not_refused_just_after_jarvis_opened_something(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire.storage, "DATA", tmp_path / "data")
    monkeypatch.setenv("JARVIS_LEARNED_DIR", str(tmp_path / "learned"))
    env = FakeEnv(opened=True, pointed=True)
    out = learnedclose.run_learned("pcmethod.close_foreground", {"version": 1}, REF, STEP, env)
    assert out["verified"] and env.ui_obj.invoked == [("invoke", 11)]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire.storage, "DATA", tmp_path / "data")
    monkeypatch.setenv("JARVIS_LEARNED_DIR", str(tmp_path / "learned"))
    monkeypatch.setenv("JARVIS_ACQUIRE_DIR", str(tmp_path / "evidence"))
    return tmp_path


def passing_check(source):
    return {"ok": True, "passed": 8, "total": 8, "lab": "done", "rows": [], "judge": "j" * 16, "source_sha": acquire._sha(source),
            "seeds": list(skillcontracts.PRODUCTION_CHECK_SEEDS)}


CAND = {"id": "wk1", "tool": "close_foreground", "examples": [{"said": "close it"}], "count": 1, "status": "ready"}
LEARNER = SimpleNamespace(model="qwen3.5:9b", calls=0, seconds=0.0, transcript=[])


def install(source=REF, approve=True):
    best = {"round": 1, "passed": 12, "total": 12, "production": passing_check(source)}
    man = labpractice.retain(CAND, source, best, {"cloud_calls": 0}, LEARNER, "judgehash")
    auto = learned.auto_approve("method", man["id"], "close_foreground", man["version"], man["evidence"], notify=lambda *a: None) if approve else None
    return man, auto


# ------------------------------------------------------------------------------------------------ retain + approve

def test_a_passing_method_is_retained_with_provenance_and_approved_for_that_exact_source(store):
    man, auto = install()
    assert man["authored_by"] == "local:qwen3.5:9b" and man["cloud_calls"] == 0 and man["for_tool"] == "close_foreground"
    assert man["evidence"]["passed"] == man["evidence"]["total"] == 12 and man["exam"] == {"passed": 12, "total": 12}
    assert man["source_sha"] == acquire._sha(REF) and man["production_check"]["source_sha"] == man["source_sha"]
    assert man["production_check"]["passed"] == man["production_check"]["total"] == 8
    approvals = json.loads((store / "learned" / "approvals.json").read_text(encoding="utf-8"))
    assert approvals[man["id"]]["source_sha"] == man["source_sha"] and approvals[man["id"]]["by"] == "auto"
    assert auto["exam"]["production"]["passed"] == 8
    assert acquire.candidates_for("close_foreground") == ["learned:" + man["id"]]


def test_a_changed_source_is_not_approved_or_run(store):
    man, _ = install()
    src = store / "learned" / man["id"] / f"v{man['version']}" / "source.py"
    src.write_text(REF + "\n# edited after the exam\n", encoding="utf-8")
    assert acquire.candidates_for("close_foreground") == []                      # candidates_for swallows the refusal: the built-in way
    from core import procedures
    with pytest.raises(procedures.NotVerified):
        procedures.Library(store / "learned").get(man["id"])
    with pytest.raises(procedures.NotVerified):
        learned.auto_approve("method", man["id"], "close_foreground", man["version"],
                             {**man["evidence"]}, notify=lambda *a: None)


def test_a_new_version_needs_its_own_approval(store):
    man, _ = install()
    new = labpractice.retain(CAND, REF + "# v2\n", {"round": 1, "passed": 12, "total": 12, "production": passing_check(REF + "# v2\n")}, {}, LEARNER, "j")
    assert new["version"] == man["version"] + 1 and acquire.candidates_for("close_foreground") == []     # v2 is active, approval is for v1


def test_no_approval_without_a_passed_production_check_or_a_full_exam(store):
    best = {"round": 1, "passed": 12, "total": 12, "production": passing_check(REF)}
    man = labpractice.retain(CAND, REF, best, {}, LEARNER, "j")
    bare = {k: v for k, v in man["evidence"].items() if k != "production"}
    assert learned.auto_approve("method", man["id"], "close_foreground", 1, bare, notify=lambda *a: None) is None
    half = {**man["evidence"], "production": {"passed": 7, "total": 8}}
    assert learned.auto_approve("method", man["id"], "close_foreground", 1, half, notify=lambda *a: None) is None
    short = {**man["evidence"], "passed": 8, "total": 8}
    assert learned.auto_approve("method", man["id"], "close_foreground", 1, short, notify=lambda *a: None) is None
    assert acquire.candidates_for("close_foreground") == []
    # a method approved by hand without a production check is still never offered for this kind
    acquire.approve(man["id"], 1, by="owner")
    path = store / "learned" / man["id"] / "v1" / "manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    m.pop("production_check")
    path.write_text(json.dumps(m), encoding="utf-8")
    assert acquire.candidates_for("close_foreground") == []


# ------------------------------------------------------------------------------------------------ the cycle

def cycle_lab(prod_pass=True, exam_pass=True, seen=None):
    calls = []

    def lab(trials, label):
        calls.append(label)
        out = []
        for t in trials:
            kind = t["action"]["type"]
            if kind == "observe":
                out.append({"result": {"ok": True, "tree": "Window 'x - LabPad'"}})
                continue
            if seen is not None:
                seen.append(kind)
            good = {"production": prod_pass}.get(kind, exam_pass) and "ui.log('good')" in t["action"]["source"]
            out.append({"result": {"ok": True, "trace": []}, "judge": {"passed": bool(good), "checks": {}, "diff": [] if good else ["still open: x"]}})
        return {"status": "done", "results": out}
    lab.calls = calls
    return lab


GOOD = "```python\ndef run(ui, params):\n    ui.log('good')\n    return 'closed'\n```"


def learner_of(replies):
    replies = list(replies)
    return acquire.Learner(ask=lambda prompt, temperature, fmt: replies.pop(0) if replies else "", guard=False)


def cand(store):
    return acquire.discover([{"said": "close this window", "reason": "step not verified: it did not happen", "at": 5.0,
                              "decision": {"intents": [{"steps": [{"tool": "close_foreground", "arguments": {}}]}]}}], studied={})[0]


def test_a_close_foreground_cycle_that_passes_everything_is_retained_and_approved(store):
    seen = []
    lab = cycle_lab(seen=seen)
    rec = acquire.learn(cand(store), learner=learner_of([GOOD]), lab=lab)
    assert rec["status"] == "learned" and rec["cloud_calls"] == 0 and rec["practice"]["in_use"] is True
    assert lab.calls == ["observe", "practice-1", "exam-1", "production-1"] and "production" in seen
    assert rec["method"]["approved_by"] == "auto" and rec["method"]["sha"] == acquire._sha(rec["practice"] and GOOD.split("```python\n")[1].split("```")[0])
    assert acquire.candidates_for("close_foreground") == ["learned:" + rec["method"]["id"]]
    assert "approved on its exam and in use" in acquire.summary(rec)
    man = acquire.learned_methods()[0]
    assert man["production_check"]["passed"] == 8 and man["authored_by"] == "local:" + rec["learner_model"]


def test_failing_the_production_check_means_not_learned_and_nothing_is_approved(store):
    rec = acquire.learn(cand(store), learner=learner_of([GOOD] * 6), lab=cycle_lab(prod_pass=False))
    assert rec["status"] == "not_learned" and "method" not in rec
    assert acquire.learned_methods() == [] and acquire.candidates_for("close_foreground") == []


def test_every_other_desktop_kind_stays_practised_only(store):
    for tool in ("close_app", "use_app", "type_text", "save_document", "open_path"):
        c = acquire.discover([{"said": "x", "reason": "step not verified: no", "at": 5.0,
                               "decision": {"intents": [{"steps": [{"tool": tool, "arguments": {}}]}]}}], studied={})[0]
        seen = []
        rec = acquire.learn(c, learner=learner_of([GOOD]), lab=cycle_lab(seen=seen))
        assert rec["status"] == "practised" and rec["practice"]["in_use"] is False and "production" not in seen
    assert acquire.learned_methods() == []


# ------------------------------------------------------------------------------------------------ run time through pcmethods

@pytest.fixture
def live(store, monkeypatch):
    """An approved learned method, a fake machine, and a native close that records whether it ran."""
    man, _ = install()
    state = SimpleNamespace(native=0, env=FakeEnv())
    monkeypatch.setattr(learnedclose, "Env", lambda: state.env)

    def perform(tool, args, approved=False, cancelled=None):
        state.native += 1
        return {"verified": True, "detail": "Closed it (built-in)."}
    monkeypatch.setattr(pcmethods.pcactions, "perform", perform)
    monkeypatch.setenv("JARVIS_LEARNED_FIRST", "1")                       # the ranking has its own tests below
    state.man = man
    state.said = []
    monkeypatch.setattr(misses, "fail", lambda reason, route="", decision=None: state.said.append((reason, route)) or True)
    return state


def uses(owner=None):
    rows = independence.uses()
    return [r for r in rows if owner is None or r["source"] == owner]


def test_a_verified_learned_close_is_counted_once_as_an_owner_use_and_leaves_a_ledger_row(live):
    out = pcmethods.execute(STEP, approved=True)
    assert out["verified"] and out["method"] == "learned:" + live.man["id"] and live.native == 0
    assert live.env.ui_obj.invoked == [("invoke", 11)]
    rows = uses()
    assert len(rows) == 1 and rows[0]["source"] == "owner" and rows[0]["passed"] and rows[0]["tool"] == "close_foreground"
    assert rows[0]["skill"] == live.man["id"] and rows[0]["outside_calls"] == 0 and rows[0]["teacher"] is False
    ledger = [h for h in pcledger.history() if h.get("component") == "local_automation" and h.get("tool") == "close_foreground"]
    assert [h["method"] for h in ledger] == ["learned:" + live.man["id"]] and ledger[0]["verified"] is True
    assert live.said == []


def test_in_the_lab_the_use_is_logged_as_lab(live, monkeypatch):
    monkeypatch.setenv("JARVIS_SKILL_LAB", "1")
    pcmethods.execute(STEP, approved=True)
    assert [r["source"] for r in uses()] == ["lab"] and not uses("owner")


def test_a_failed_run_is_not_a_use_and_the_builtin_close_runs_exactly_once_and_a_miss_is_recorded(live):
    live.env.cfg["gone"] = False                                          # the window is still there afterwards, still in front
    live.env.ui_obj.windows = lambda: []                                  # ... and the method found nothing to press
    out = pcmethods.execute(STEP, approved=True)
    assert out["verified"] and out["method"] == "native" and live.native == 1
    assert [a["method"] for a in out["attempts"]] == ["learned:" + live.man["id"], "native"]
    assert uses() == []                                                   # a failed run does not count as a use
    assert len(live.said) == 1 and "still open" in live.said[0][0] and live.said[0][1] == "learned_close"
    assert live.env.ui_obj.invoked == []
    fails = [h for h in pcledger.history() if h.get("method") == "learned:" + live.man["id"]]
    assert fails and fails[0]["verified"] is False


def test_an_exception_in_the_method_is_the_same_and_still_closes_nothing_else(live):
    live.env.cfg["gone"] = False
    live.env.ui_obj.windows = lambda: (_ for _ in ()).throw(RuntimeError("uia broke"))
    out = pcmethods.execute(STEP, approved=True)
    assert out["method"] == "native" and live.native == 1 and uses() == [] and len(live.said) == 1


def test_if_another_window_is_in_front_afterwards_the_builtin_close_is_not_run(live):
    live.env.cfg.update(gone=False)
    real = live.env.ui_obj.invoke
    live.env.ui_obj.invoke = lambda eid=None, id=None: (real(eid), live.env.cfg.update(front=999))[0]     # switched right after the press
    out = pcmethods.execute(STEP, approved=True)
    assert not out["verified"] and live.native == 0 and uses() == [] and len(live.said) == 1


def test_a_save_prompt_is_confirmed_stops_the_turn_and_is_not_a_failure_or_a_use(live):
    live.env.cfg.update(gone=False, prompt={"verified": False, "error_kind": "needs_owner", "detail": "It has unsaved changes. Save it before I close it?"})
    out = pcmethods.execute(STEP, approved=True)
    assert not out["verified"] and out["prompt_confirmed"] and "unsaved changes" in out["detail"]
    assert live.native == 0 and live.said == [] and uses() == []
    assert live.env.ui_obj.invoked == [("invoke", 11)]                    # it pressed Close only: the prompt was left alone
    stats = pcledger.method_order(STEP, ["learned:" + live.man["id"], "native"])
    assert stats                                                          # recorded as a success for the method (no failure row)


def test_a_refusal_falls_back_to_the_builtin_method_without_blaming_the_learned_one(live):
    live.env.t["process"] = "pythonw.exe"
    out = pcmethods.execute(STEP, approved=True)
    assert out["method"] == "native" and live.native == 1 and live.env.ui_obj.invoked == []
    assert uses() == [] and live.said == []                                # no miss, no use
    assert out["attempts"][0] == {"method": "learned:" + live.man["id"], "verified": False, "error_type": "Refused"}
    with pcledger.database() as db:
        assert db.execute("SELECT COUNT(*) FROM methods WHERE method LIKE 'learned:%'").fetchone()[0] == 0


# ------------------------------------------------------------------------------------------------ selection

def stat(step, method, ok, ago):
    """Write a ledger outcome `ago` seconds in the past."""
    pcledger.method_outcome(step, method, ok)
    with pcledger.database() as db:
        col = "last_success" if ok else "last_failure"
        db.execute(f"UPDATE methods SET {col}=? WHERE method=? AND situation=?", (__import__("time").time() - ago, method, pcledger.fingerprint(step)))


LEARNED = "learned:pcmethod.close_foreground"
H = 3600


def test_ranking_the_four_cases():
    cands = [LEARNED, "native"]
    # 1. nothing recorded: native first (the learned method never jumps a built-in method that has not failed)
    assert pcledger.method_order(STEP, cands) == ["native", LEARNED]
    # 2. native failed 2 hours ago, learned never failed: learned first
    stat(STEP, "native", False, 2 * H)
    assert pcledger.method_order(STEP, cands) == [LEARNED, "native"]
    # 3. ... but learned also failed in the last 24 hours: native first again
    stat(STEP, LEARNED, False, 5 * H)
    assert pcledger.method_order(STEP, cands) == ["native", LEARNED]


def test_ranking_old_failures_do_not_count_and_a_native_success_alone_keeps_native_first():
    step = {"tool": "close_foreground", "arguments": {"x": 1}}
    cands = [LEARNED, "native"]
    stat(step, "native", False, 30 * H)                                    # more than 24 hours ago: not "lately"
    assert pcledger.method_order(step, cands) == ["native", LEARNED]
    stat(step, "native", True, H)
    assert pcledger.method_order(step, cands) == ["native", LEARNED]
    stat(step, "native", False, H)                                         # native fails now: learned (never failed) goes first
    assert pcledger.method_order(step, cands) == [LEARNED, "native"]
    stat(step, LEARNED, False, 30 * H)                                     # its own failure is also old: still first
    assert pcledger.method_order(step, cands) == [LEARNED, "native"]


def test_the_learned_first_switch_forces_the_order_and_is_off_by_default(monkeypatch):
    cands = [LEARNED, "native"]
    assert pcledger.method_order(STEP, cands) == ["native", LEARNED]
    monkeypatch.setenv("JARVIS_LEARNED_FIRST", "1")
    stat(STEP, LEARNED, False, H)
    assert pcledger.method_order(STEP, cands) == [LEARNED, "native"]
    monkeypatch.setenv("JARVIS_LEARNED_FIRST", "off")
    assert pcledger.method_order(STEP, cands)[0] == "native"


def test_other_methods_keep_the_existing_ranking():
    assert pcledger.method_order(STEP, ["native", "windows_builtin"]) == ["native", "windows_builtin"]
    stat(STEP, "native", False, H)
    assert pcledger.method_order(STEP, ["native", "windows_builtin"]) == ["windows_builtin", "native"]
    assert pcledger.method_order(STEP, ["skill:skill.x", "native"]) == ["skill:skill.x", "native"]       # skills: unchanged


# ------------------------------------------------------------------------------------------------ review fixes

def test_only_the_title_bar_close_may_be_pressed_not_a_decoy_close_anywhere_else():
    """A Close in the page, in an owned dialog, a File > Close menu item or a 'Close' under the window itself is not the title bar's."""
    ui = FakeUI()
    ui.rows[15] = ("Button", "Close", 0)          # an in-page Close button, directly under the window (no TitleBar)
    ui.rows[16] = ("MenuItem", "Close", 0)        # File > Close
    ui.rows[17] = ("Pane", "Dialog", 0)
    ui.rows[18] = ("Button", "Close", 0)          # a Close inside a pane (an in-content / owned dialog button)
    ui.rows[19] = ("TitleBar", "", 0)             # a title bar that belongs to an inner element, not to the window
    ui.rows[20 + 9] = ("Button", "Close", 0)
    for i in (15, 16, 17, 18, 19, 29):
        ui.owner[i] = 1
    ui.par.update({18: 17, 17: 1, 29: 19, 19: 17})
    scoped = learnedclose.ScopedUI(ui, 100)
    w = scoped.windows()[0]
    for decoy in (15, 16, 18, 29):
        scoped.find(w["id"])
        with pytest.raises(learnedclose.ScopeError, match="Close"):
            scoped.invoke(decoy)
    assert ui.invoked == []
    scoped.invoke(11)                                                         # the real one
    assert ui.invoked == [("invoke", 11)]


def test_a_method_that_presses_the_last_close_it_finds_cannot_press_a_decoy():
    ui = FakeUI()
    ui.rows[15] = ("Button", "Close", 0)
    ui.owner[15] = 1
    last = "def run(ui, params):\n    w = ui.windows()[0]\n    ui.invoke(ui.find(w['id'], role='Button', name='Close')[-1]['id'])\n    return 'x'\n"
    out = learnedclose.run_method(last, 100, ui)
    assert not out["ok"] and out["error_type"] == "ScopeError" and ui.invoked == []


class Slow(FakeUI):
    """windows() is where real UIA spends seconds: the machine changes while the method looks for its button."""

    def __init__(self, env, change):
        super().__init__()
        self.env, self.change = env, change

    def windows(self):
        self.change(self.env)
        return super().windows()


def _front(env):
    env.cfg["front"] = 999


def _game(env):
    env.cfg["game"] = "SomeGame"


def _lock(env):
    env.cfg["desktop"] = False


def _full(env):
    env.cfg["full"] = True


@pytest.mark.parametrize("change", [_front, _game, _lock, _full])
def test_nothing_is_pressed_if_the_machine_changed_between_resolving_and_pressing(change):
    env = FakeEnv(gone=False)
    env.ui_obj = Slow(env, change)
    with pytest.raises(learnedclose.NoFallback):
        learnedclose.run_learned("pcmethod.close_foreground", {"version": 1}, REF, STEP, env)
    assert env.ui_obj.invoked == []


def test_an_unchanged_machine_still_closes():
    env = FakeEnv(gone=True)
    env.ui_obj = Slow(env, lambda e: None)
    out = learnedclose.run_learned("pcmethod.close_foreground", {"version": 1}, REF, STEP, env)
    assert out["verified"] and env.ui_obj.invoked == [("invoke", 11)]


def test_a_stale_stop_does_not_blame_the_learned_method_and_runs_nothing_else(live):
    live.env.ui_obj = Slow(live.env, _front)
    out = pcmethods.execute(STEP, approved=True)
    assert not out["verified"] and live.native == 0 and live.env.ui_obj.invoked == [] and uses() == []
    with pcledger.database() as db:
        assert db.execute("SELECT COUNT(*) FROM methods WHERE method LIKE 'learned:%'").fetchone()[0] == 0


@pytest.mark.parametrize("probe", ["shown", "front_hwnd"])
def test_any_error_after_the_press_stops_instead_of_closing_another_window(live, probe):
    live.env.cfg["gone"] = False
    setattr(live.env, probe, lambda *a, **k: (_ for _ in ()).throw(OSError("probe broke")))
    if probe == "front_hwnd":
        live.env.cfg["gone"] = False
    out = pcmethods.execute(STEP, approved=True)
    assert not out["verified"] and live.native == 0                           # the built-in close never ran on whatever is in front now
    assert [a["method"] for a in out["attempts"]] == ["learned:" + live.man["id"]]


def test_an_error_after_the_press_never_lets_the_builtin_close_run_even_if_the_target_looks_in_front(live):
    live.env.cfg["gone"] = False
    live.env.save_prompt = lambda hwnd: (_ for _ in ()).throw(OSError("probe broke"))
    out = pcmethods.execute(STEP, approved=True)
    assert not out["verified"] and live.native == 0


def test_a_timeout_of_the_learned_step_does_not_fall_through_to_the_builtin_close(live, monkeypatch):
    monkeypatch.setattr(acquire, "run_learned", lambda method, step: (_ for _ in ()).throw(TimeoutError("took too long")))
    out = pcmethods.execute(STEP, approved=True)
    assert not out["verified"] and live.native == 0
    assert [a["method"] for a in out["attempts"]] == ["learned:" + live.man["id"]]


def test_a_native_failure_is_cancelled_by_a_later_native_success():
    pcledger.method_outcome(STEP, "native", False)
    pcledger.method_outcome(STEP, "native", True)
    assert pcledger.method_order(STEP, ["native", LEARNED]) == ["native", LEARNED]


def test_a_learned_failure_is_cancelled_by_a_later_learned_success():
    pcledger.method_outcome(STEP, LEARNED, False)
    pcledger.method_outcome(STEP, LEARNED, True)
    pcledger.method_outcome(STEP, "native", False)
    assert pcledger.method_order(STEP, [LEARNED, "native"]) == [LEARNED, "native"]
    pcledger.method_outcome(STEP, LEARNED, False)                              # its newest event is now a failure
    assert pcledger.method_order(STEP, [LEARNED, "native"]) == ["native", LEARNED]


@pytest.mark.parametrize("output", [{"verified": False, "error_kind": "needs_owner", "detail": "It has unsaved changes. Save it?"},
                                    {"verified": False, "error_kind": "needs_owner", "detail": "The Notepad window is still open."}])
def test_a_native_needs_owner_answer_is_not_a_native_failure(store, monkeypatch, output):
    monkeypatch.setattr(pcmethods.pcactions, "perform", lambda tool, args, approved=False, cancelled=None: dict(output))
    pcmethods.execute(STEP, approved=True)
    assert pcledger.method_order(STEP, ["native", LEARNED]) == ["native", LEARNED]
    with pcledger.database() as db:
        assert db.execute("SELECT COUNT(*) FROM methods WHERE method='native' AND failures>0").fetchone()[0] == 0


# ------------------------------------------------------------------------------------------------ second review round

def test_a_close_that_was_pressed_but_is_still_tearing_down_never_falls_back(live):
    live.env.cfg["gone"] = False                                          # pressed; still shown and in front at the check
    out = pcmethods.execute(STEP, approved=True)
    assert not out["verified"] and live.native == 0 and len(live.env.ui_obj.invoked) == 1


def test_native_raising_after_it_closed_the_window_never_runs_the_learned_method_on_the_next_one(live, monkeypatch):
    monkeypatch.delenv("JARVIS_LEARNED_FIRST")
    monkeypatch.setattr(pcmethods, "candidates", lambda step: ["native", "learned:" + live.man["id"]])
    def boom(tool, args, approved=False, cancelled=None):
        live.native += 1
        live.env.t["hwnd"] = 555                                          # the closed window is gone; another is now in front
        raise RuntimeError("Windows could not send a graceful close")
    monkeypatch.setattr(pcmethods.pcactions, "perform", boom)
    real_front = live.env.front_hwnd
    live.env.shown = lambda hwnd: hwnd == 555
    live.env.t["hwnd"] = 100
    # the window in front before any method ran is 100; after native raised 555 is in front
    out = pcmethods.execute(STEP, approved=True)
    assert not out["verified"] and live.native == 1 and live.env.ui_obj.invoked == []


def test_native_failing_without_touching_the_window_still_lets_the_learned_method_try(live, monkeypatch):
    monkeypatch.delenv("JARVIS_LEARNED_FIRST")
    monkeypatch.setattr(pcmethods, "candidates", lambda step: ["native", "learned:" + live.man["id"]])
    def boom(tool, args, approved=False, cancelled=None):
        raise RuntimeError("nothing happened")
    monkeypatch.setattr(pcmethods.pcactions, "perform", boom)
    out = pcmethods.execute(STEP, approved=True)
    assert out["verified"] and out["method"] == "learned:" + live.man["id"]


def test_other_tools_keep_the_old_order_learned_first(store):
    step = {"tool": "close_site_tab", "arguments": {"site": "github"}}
    L = "learned:pcmethod.close_site_tab"
    assert pcledger.method_order(step, [L, "native"]) == [L, "native"]
    pcledger.method_outcome(step, L, True)
    assert pcledger.method_order(step, [L, "native"]) == [L, "native"]
