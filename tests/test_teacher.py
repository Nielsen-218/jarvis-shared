"""The teacher (core/teacher): asked only when practice is stuck, local first, cloud only behind a credit + budget gate,
guidance never code, and a skill learned with its help is never called independent (owner 2026-11-08)."""
import json
import time
from types import SimpleNamespace

import pytest

from core import acquire, teacher, usage

from tests.test_acquire import GOOD, MISS, WEAK, fake_lab, fake_learner


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LEARNED_DIR", str(tmp_path / "learned"))
    monkeypatch.setenv("JARVIS_ACQUIRE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("JARVIS_USAGE_FILE", str(tmp_path / "usage.json"))
    for k in ("JARVIS_TEACHER_CREDIT_USD", "JARVIS_TEACHER_CREDIT_AS_OF", "JARVIS_TEACHER_CREDIT_EXPIRES", "JARVIS_TEACHER_SKILL_USD",
              "JARVIS_TEACHER_AFTER", "JARVIS_TEACHER_LOCAL_MAX", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def cand():
    return acquire.discover([MISS])[0]


def cycle(store, wid, name, status, passed=5, total=8, started="2026-11-08T01:00:00"):
    d = store / "evidence" / wid / name
    d.mkdir(parents=True)
    rows = [{"seed": 1000 + i, "site": "google", "passed": i < passed, "diff": [] if i < passed else ["wrongly closed: Google - Wikipedia"]}
            for i in range(total)]
    (d / "method-r1-0.py").write_text(WEAK, encoding="utf-8")
    rec = {"status": status, "started": started, "stages": [
        {"stage": "improve", "what": "round 1: learner's own failure analysis", "data": "I matched the word in the title."},
        {"stage": "practice", "what": "round 1", "data": {"kept_sha": acquire._sha(WEAK), "rows": rows}},
        {"stage": "verify", "what": "held-out exam 1", "data": {"rows": [{"seed": 90001, "site": "bing", "passed": False,
                                                                        "diff": ["SEALED-SECRET-WORLD"]}]}}]}
    (d / "cycle.json").write_text(json.dumps(rec), encoding="utf-8")


def test_stuck_means_three_finished_failures_since_the_last_lesson(store):
    c = cand()
    cycle(store, c["id"], "a", "not_learned")
    cycle(store, c["id"], "b", "aborted_by_operator")              # an operator stop or the owner coming back is not a failure
    cycle(store, c["id"], "c", "not_learned")
    assert not teacher.stalled(c["id"])[0]
    cycle(store, c["id"], "d", "not_learned", passed=6)
    st, why = teacher.stalled(c["id"])
    assert st and "5/8, 5/8, 6/8" in why
    teacher._keep({"id": "x", "weakness": c["id"], "tier": "local", "at": time.time(), "status": "given", "lesson": {}})
    assert not teacher.stalled(c["id"])[0]                         # a new lesson resets the count


def test_the_cycle_that_used_a_lesson_counts_as_after_it(store, monkeypatch):
    monkeypatch.setenv("JARVIS_TEACHER_AFTER", "1")
    c = cand()
    at = time.mktime(time.strptime("2026-11-08T10:57:55", "%Y-%m-%dT%H:%M:%S")) + 0.4    # asked at the cycle's start
    teacher._keep({"id": "y", "weakness": c["id"], "tier": "cloud", "at": at, "status": "given", "lesson": {}})
    cycle(store, c["id"], "e", "not_learned", started="2026-11-08T10:57:55")
    rec = json.loads((store / "evidence" / c["id"] / "e" / "cycle.json").read_text(encoding="utf-8"))
    rec["finished"] = "2026-11-08T11:10:44"
    (store / "evidence" / c["id"] / "e" / "cycle.json").write_text(json.dumps(rec), encoding="utf-8")
    assert teacher.stalled(c["id"])[0]


REPLY = json.dumps({
    "failed_assumption": "You assumed a tab belongs to a site when the site's name appears anywhere in its title.",
    "explanation": "Titles of other sites mention the name too. The address bar holds the real address of the selected tab.",
    "hints": ["Select a candidate tab, then read the address bar with ui.value before deciding.",
              "for t in tabs:\n    ui.select(t['id'])",
              "Treat a window whose only tabs are the site's tabs specially: keep the window."],
    "references": [{"title": "ValuePattern", "url": "https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-implementingvalue"},
                   {"title": "random blog", "url": "https://evil.example/hack"}]}) + "\n```python\ndef run(ui, params):\n    pass\n```"


def test_the_teacher_gives_guidance_never_code(store):
    lesson, removed = teacher.clean(REPLY)
    text = json.dumps(lesson)
    assert "def run" not in text and "for t in tabs" not in text and "ui.select(" not in text
    assert "address bar" in lesson["explanation"] and len(lesson["hints"]) == 2           # the code-carrying hint is gone whole
    assert [r["url"] for r in lesson["references"]] == ["https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-implementingvalue"]
    assert any("dropped" in r for r in removed)


def test_the_teacher_never_sees_the_sealed_exam(store):
    c = cand()
    for n in "abc":
        cycle(store, c["id"], n, "not_learned")
    prompt = teacher.brief(c, [x["folder"] for x in teacher.cycles(c["id"])])
    assert "SEALED-SECRET-WORLD" not in prompt and "Google - Wikipedia" in prompt and "matched the word" in prompt


class FakeClient:
    def __init__(self, tokens=2000):
        self.created, self.tokens = [], tokens
        self.messages = self

    def count_tokens(self, **kw):
        return SimpleNamespace(input_tokens=self.tokens)

    def create(self, **kw):
        self.created.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(text=REPLY)],
                               usage=SimpleNamespace(input_tokens=self.tokens, output_tokens=800, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))


def declare(monkeypatch, amount="10", as_of="2026-11-01", expires="2099-01-01"):
    monkeypatch.setenv("JARVIS_TEACHER_CREDIT_USD", amount)
    monkeypatch.setenv("JARVIS_TEACHER_CREDIT_AS_OF", as_of)
    monkeypatch.setenv("JARVIS_TEACHER_CREDIT_EXPIRES", expires)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_local_lessons_come_first_and_the_cloud_stays_shut_without_a_declared_credit(store, monkeypatch):
    c, client = cand(), FakeClient()
    for _ in range(2):
        rec = teacher.consult(c, ask_local=lambda p: (REPLY, 0.0, {}), client=client)
        assert rec["tier"] == "local" and rec["status"] == "given"
    rec = teacher.consult(c, client=client)
    assert rec["tier"] == "cloud" and rec["status"] == "refused" and "not declared" in rec["why"] and client.created == []
    assert teacher.can_help(c["id"]) == (False, rec["why"])


@pytest.mark.parametrize("amount,expires,spent,why", [
    ("10", "2026-01-01", 0, "expired"), ("1", "2099-01-01", 1.5, "used up"), ("abc", "2099-01-01", 0, "not a number")])
def test_an_expired_or_spent_credit_is_never_used(store, monkeypatch, amount, expires, spent, why):
    declare(monkeypatch, amount=amount, expires=expires)
    (store / "usage.json").write_text(json.dumps({"2026-11-05": {"cost": spent}}), encoding="utf-8")
    c = teacher.credit()
    assert not c["ok"] and why in c["why"]


def test_the_cloud_teacher_spends_within_the_per_skill_cap_and_records_it(store, monkeypatch):
    declare(monkeypatch)
    monkeypatch.setenv("JARVIS_TEACHER_LOCAL_MAX", "0")
    monkeypatch.setenv("JARVIS_TEACHER_SKILL_USD", "0.14")     # worst case of one lesson here: $0.128; it really costs $0.024
    monkeypatch.setenv("JARVIS_TEACHER_CLOUD_MAX", "5")        # this test pins the MONEY cap; the one-paid-lesson rule has its own test
    c, client = cand(), FakeClient()
    rec = teacher.consult(c, client=client)
    assert rec["status"] == "given" and len(client.created) == 1 and rec["cost_usd"] == pytest.approx(0.024)
    assert usage.spent_since("2026-01-01") == pytest.approx(rec["cost_usd"])                 # in the same ledger as the rest
    rec2 = teacher.consult(c, client=client)                    # the next lesson could cross the cap: it is not asked for
    assert rec2["status"] == "refused" and "per-skill" in rec2["why"] and len(client.created) == 1
    assert teacher.spent_on(c["id"]) <= 0.14


def test_a_skill_learned_after_a_lesson_is_teacher_assisted_never_independent(store):
    c = cand()
    rec = acquire.learn(c, learner=fake_learner([GOOD]), lab=fake_lab())
    assert rec["status"] == "learned" and acquire.learned_methods()[0]["provenance"] == "independent"

    store2 = store / "second"
    import os
    os.environ["JARVIS_LEARNED_DIR"], os.environ["JARVIS_ACQUIRE_DIR"] = str(store2 / "learned"), str(store2 / "evidence")
    teacher._keep({"id": "l1", "weakness": c["id"], "tier": "local", "model": "qwen3:14b", "at": time.time(), "status": "given",
                   "outcome": "pending", "lesson": {"failed_assumption": "titles lie", "hints": ["read the address bar"]}})
    learner = fake_learner([GOOD])
    rec = acquire.learn(c, learner=learner, lab=fake_lab())
    man = acquire.learned_methods()[0]
    assert rec["status"] == "learned" and man["provenance"] == "teacher_assisted" and man["teacher_lessons"][0]["id"] == "l1"
    assert any("A TEACHER'S GUIDANCE" in p and "read the address bar" in p for p in learner.prompts)
    assert teacher.lessons(c["id"])[0]["outcome"] == "led_to_learned"                        # successful lessons are kept


def test_a_refused_consultation_teaches_nothing_and_changes_no_label(store):
    c = cand()
    teacher._keep({"id": "r1", "weakness": c["id"], "tier": "cloud", "at": time.time(), "status": "refused", "why": "no credit"})
    assert teacher.provenance(c["id"])["provenance"] == "independent" and teacher.guidance(c["id"]) == ""


def test_a_stuck_cycle_asks_for_a_lesson_before_it_practises(store, monkeypatch):
    c = cand()
    for n in "abc":
        cycle(store, c["id"], n, "not_learned")
    monkeypatch.setattr(teacher, "_ask_local", lambda p: (REPLY, 0.0, {}))
    learner = fake_learner([GOOD])
    rec = acquire.learn(c, learner=learner, lab=fake_lab())
    teach = [s for s in rec["stages"] if s["stage"] == "teach"]
    assert teach and teach[0]["autonomous"] is False and teach[0]["assist"].startswith("teacher:")
    assert rec["provenance"]["provenance"] == "teacher_assisted"


def test_ui_value_is_read_only_and_part_of_the_language():
    from core import methodlang
    methodlang.check("def run(ui, params):\n    v = ui.value(3)\n    return v\n")
    assert "ui.value(id)" in methodlang.API_DOC


def test_the_owner_can_order_a_cloud_lesson_now_but_it_still_passes_the_gate(store, monkeypatch):
    c, client = cand(), FakeClient()
    rec = teacher.consult(c, tier="cloud", client=client, requested_by="owner command")
    assert rec["status"] == "refused" and client.created == []                     # no declared credit: still nothing spent
    declare(monkeypatch)
    rec = teacher.consult(c, tier="cloud", client=client, requested_by="owner command")
    assert rec["status"] == "given" and rec["tier"] == "cloud" and rec["requested_by"] == "owner command" and len(client.created) == 1
    assert teacher.provenance(c["id"])["provenance"] == "teacher_assisted"


def test_a_reply_cut_off_mid_way_keeps_only_its_finished_fields(store):
    cut = '{"failed_assumption":"A title is free text; the host is the truth.","explanation":"He matched words in titl'
    lesson, removed = teacher.clean(cut)
    assert lesson["failed_assumption"].startswith("A title is free text") and lesson["explanation"] == "" and lesson["hints"] == []
    assert any("cut off" in r for r in removed)
