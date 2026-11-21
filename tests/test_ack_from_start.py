"""A command turn is acknowledged counted from the turn's start (gateway.Ack began=), without a real clock or sleep."""
from core import gateway


class FakeTimer:
    made = []

    def __init__(self, seconds, fn):
        self.seconds, self.fn, self.cancelled = seconds, fn, False
        self.daemon = True
        FakeTimer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


def setup(monkeypatch, now):
    FakeTimer.made = []
    monkeypatch.setattr(gateway.threading, "Timer", FakeTimer)
    monkeypatch.setattr(gateway.time, "monotonic", lambda: now[0])


def test_ack_is_due_1_2_s_after_the_turn_began(monkeypatch):
    now = [100.0]
    setup(monkeypatch, now)
    ack = gateway.Ack(lambda t: None, "On it, sir.", began=100.0)
    now[0] = 100.2                          # the work is underway 0.2 s into the turn
    ack.go()
    assert abs(FakeTimer.made[0].seconds - 1.0) < 1e-6


def test_a_slow_decision_still_leaves_a_grace_period(monkeypatch):
    now = [100.0]
    setup(monkeypatch, now)
    ack = gateway.Ack(lambda t: None, "On it, sir.", began=100.0)
    now[0] = 101.6                          # the gateway's decision alone took 1.6 s
    ack.go()
    assert FakeTimer.made[0].seconds == gateway.ACK_GRACE, "a quick action still says only its result when it is quick"


def test_no_ack_when_the_answer_comes_first(monkeypatch):
    now, said = [0.0], []
    setup(monkeypatch, now)
    ack = gateway.Ack(said.append, "On it, sir.", began=0.0)
    ack.go()
    ack.finish()
    FakeTimer.made[0].fn()                  # the timer fires late, after the result
    assert said == [] and FakeTimer.made[0].cancelled


def test_no_ack_before_go_and_none_after_cancel(monkeypatch):
    now, said = [0.0], []
    setup(monkeypatch, now)
    ack = gateway.Ack(said.append, "On it, sir.", cancelled=lambda: True, began=0.0)
    ack.go()
    FakeTimer.made[0].fn()
    assert said == []
    before = len(FakeTimer.made)
    ack2 = gateway.Ack(said.append, "On it, sir.", began=0.0)
    assert len(FakeTimer.made) == before and not ack2._underway, "nothing is scheduled until the review question passed"


def test_chat_and_questions_get_no_ack():
    G, I = gateway.Decision, gateway.Intent
    assert not gateway.wants_ack(G([I("chat", "a joke")], "fast"))
    assert not gateway.wants_ack(G([I("clarify", "which one?"), I("actions", steps=[])], "fast"))


def test_openings_never_repeat_in_a_row():
    seen = [gateway.ack_opening("sir") for _ in range(30)]
    assert all(a != b for a, b in zip(seen, seen[1:])) and len(set(seen)) > 3


def test_execute_hands_the_turn_start_to_its_ack(monkeypatch):
    got = {}
    real = gateway.Ack

    class Spy(real):
        def __init__(self, *a, **k):
            got.update(k)
            super().__init__(*a, **k)
    monkeypatch.setattr(gateway, "Ack", Spy)
    d = gateway.Decision([gateway.Intent("chat", "x")], "fast")
    monkeypatch.setattr(gateway, "wants_ack", lambda decision: True)
    monkeypatch.setattr(gateway, "_execute", lambda *a, **k: "ok")
    assert gateway.execute(d, "hello", say=lambda t: None, began=42.0) == "ok"
    assert got.get("began") == 42.0


def test_a_quick_action_after_a_slow_decision_says_only_its_result(monkeypatch):
    """The decision took the 1.2 s budget: a 0.8 s action (close_app's median) must still finish before the ack is due."""
    now, said = [100.0], []
    setup(monkeypatch, now)
    ack = gateway.Ack(said.append, "On it, sir.", began=100.0)
    now[0] = 101.2                          # the decision alone used the whole budget
    ack.go()
    due = FakeTimer.made[0].seconds
    assert due > 0.8, "the ack would queue ahead of a 0.8 s action's own result"
    now[0] = 101.2 + 0.8
    ack.finish()                            # the result is in at 0.8 s, before the timer could fire
    assert said == [] and FakeTimer.made[0].cancelled
