"""Tests for the simple-yes / do-not-disturb PC approval UX (approvals + pcfast)."""
import time

from core import approvals, pcfast


def _reset():
    approvals._autonomy.clear()
    approvals._pending.clear()
    approvals._direct.clear()


# --------------------------------------------------------------- do-not-disturb (autonomy)
def test_just_do_it_phrases_enable_autonomy():
    for phrase in ["just get it done", "just do it", "don't bother me", "don't ask me",
                   "be quick", "handle it", "do whatever it takes", "take care of it"]:
        _reset()
        approvals.on_user_message("owner", phrase)
        assert approvals.autonomous("owner"), phrase


def test_ask_me_first_turns_autonomy_off():
    _reset()
    approvals.on_user_message("owner", "just do it, don't bother me")
    assert approvals.autonomous("owner")
    approvals.on_user_message("owner", "actually, ask me first from now on")
    assert not approvals.autonomous("owner")


def test_dont_bother_me_is_not_a_cancellation():
    # "don't bother me" contains "don't" but must NOT drop a pending approval.
    _reset()
    approvals.request("owner", "somekey", "do something")
    approvals.on_user_message("owner", "just handle it, don't bother me")
    assert approvals.pending("owner") is not None
    assert approvals.autonomous("owner")


def test_autonomy_expires(monkeypatch):
    _reset()
    approvals.on_user_message("owner", "just do it")
    real = time.monotonic
    monkeypatch.setattr(approvals.time, "monotonic",
                        lambda: real() + approvals._AUTONOMY_FOR + 10)
    assert not approvals.autonomous("owner")


def test_plain_no_does_not_enable_autonomy():
    _reset()
    approvals.on_user_message("owner", "no, stop")
    assert not approvals.autonomous("owner")


def test_pc_autonomous_is_on_by_default():
    _reset()
    approvals._ask_first.clear()
    assert approvals.pc_autonomous("owner") is True          # autonomous by default
    approvals.on_user_message("owner", "ask me first from now on")
    assert approvals.pc_autonomous("owner") is False         # opt out
    approvals.on_user_message("owner", "ok just do it, go autonomous")
    assert approvals.pc_autonomous("owner") is True          # back on


def test_plain_no_does_not_disable_pc_autonomy():
    _reset()
    approvals._ask_first.clear()
    approvals.on_user_message("owner", "no")                 # one-off cancel, not a standing pref
    assert approvals.pc_autonomous("owner") is True


# --------------------------------------------------------------- pcfast helpers
def test_plan_safe_flags_sensitive_steps():
    assert pcfast._plan_safe({"steps": [{"tool": "set_volume"}, {"tool": "pc_status"}]}) is True
    assert pcfast._plan_safe({"steps": [{"tool": "uninstall_program"}]}) is False
    assert pcfast._plan_safe({"steps": []}) is False
    assert pcfast._plan_safe(None) is False


def test_auto_usd_capped_and_budget_bounded(monkeypatch):
    from core import usage
    monkeypatch.setattr(usage, "budget", lambda: 20.0)
    monkeypatch.setattr(usage, "spent", lambda: 5.0)
    assert pcfast._auto_usd() == 1.0           # capped at $1/call
    monkeypatch.setattr(usage, "spent", lambda: 19.5)
    assert pcfast._auto_usd() == 0.5           # bounded by remaining monthly
    monkeypatch.setattr(usage, "spent", lambda: 20.0)
    assert pcfast._auto_usd() == 0             # exhausted -> gate stands


# --------------------------------------------------------------- consent loop
def _staged(states):
    it = iter(states)
    last = {"v": states[-1]}
    def last_result():
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]
    return last_result


def test_consent_auto_approves_cost_then_runs(monkeypatch):
    calls = {"n": 0}
    def control_pc(task):
        calls["n"] += 1
        return [{"type": "text", "text": "ran"}]
    monkeypatch.setattr(pcfast.pchybrid, "last_result",
                        _staged([{"state": "needs_budget", "approval_id": "a" * 32},
                                 {"state": "completed"}]))
    monkeypatch.setattr(pcfast, "_grant", lambda rid, kind: True)
    pcfast._run_with_consent("do it", control_pc, allow=True)
    assert calls["n"] == 2  # ran, hit gate, approved, ran again


def test_consent_never_auto_approves_sensitive_plan(monkeypatch):
    granted = {"n": 0}
    monkeypatch.setattr(pcfast.pchybrid, "last_result",
                        lambda: {"state": "needs_review", "approval_id": "b" * 32,
                                 "plan": {"steps": [{"tool": "uninstall_program"}]}})
    monkeypatch.setattr(pcfast, "_grant", lambda rid, kind: granted.__setitem__("n", granted["n"] + 1) or True)
    pcfast._run_with_consent("remove app", lambda t: [{"type": "text", "text": "x"}], allow=True)
    assert granted["n"] == 0  # a sensitive plan is shown for review, never auto-run


def test_consent_does_nothing_without_allow(monkeypatch):
    calls = {"n": 0}
    def control_pc(task):
        calls["n"] += 1
        return [{"type": "text", "text": "x"}]
    monkeypatch.setattr(pcfast.pchybrid, "last_result",
                        lambda: {"state": "needs_budget", "approval_id": "c" * 32})
    pcfast._run_with_consent("t", control_pc, allow=False)
    assert calls["n"] == 1  # not authorized -> runs once, gate stands


def test_consent_stops_when_budget_exhausted(monkeypatch):
    calls = {"n": 0}
    def control_pc(task):
        calls["n"] += 1
        return [{"type": "text", "text": "x"}]
    monkeypatch.setattr(pcfast.pchybrid, "last_result",
                        lambda: {"state": "needs_budget", "approval_id": "d" * 32})
    monkeypatch.setattr(pcfast, "_grant", lambda rid, kind: False)  # budget spent
    pcfast._run_with_consent("t", control_pc, allow=True)
    assert calls["n"] == 1  # gate can't be granted -> stops
