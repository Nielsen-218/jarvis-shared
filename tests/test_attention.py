"""ONE judge for everything Jarvis says unprompted (core.attention): now when he's here, held while he's away or gaming,
said as one short summary when he's back."""
import pytest

from core import attention as a

HERE = {"present": True, "gaming": "", "idle": 10}
AWAY = {"present": False, "gaming": "", "idle": 3600}
GAME = {"present": True, "gaming": "Apex Legends", "idle": 1}


@pytest.fixture
def voice(monkeypatch, tmp_path):
    said = []
    monkeypatch.setattr(a, "_voice", said.append)
    monkeypatch.setattr(a, "_held", [])
    monkeypatch.setattr(a, "_spoken", [])
    from core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    return said


def test_said_now_when_he_is_here(voice):
    assert a.deliver("A reminder, sir: call mom.", a.HIGH, sit=HERE) == "said" and voice == ["A reminder, sir: call mom."]


def test_nothing_is_said_to_an_empty_room_and_he_hears_it_when_back(voice):
    assert a.deliver("A reminder, sir: call mom.", a.HIGH, sit=AWAY) == "held"
    assert a.deliver("Good news, sir: the jacket dropped to $80.", a.NORMAL, sit=AWAY) == "held"
    assert voice == []
    out = a.welcome_back(HERE)
    assert out.startswith("While you were away: A reminder, sir: call mom.") and "jacket" in out and voice == [out]
    assert a.held() == [] and a.welcome_back(HERE) == ""                       # said once


def test_a_game_is_never_interrupted_except_for_something_critical(voice):
    assert a.deliver("Price drop.", a.NORMAL, sit=GAME) == "held"
    assert a.deliver("A reminder: formation at 0630.", a.HIGH, sit=GAME) == "held"
    assert a.deliver("Your PC is overheating.", a.CRITICAL, sit=GAME) == "said"
    assert a.welcome_back(GAME) == ""                                           # still playing: keep holding
    assert a.welcome_back(HERE, "game").startswith("Now that your game is over: A reminder: formation")


def test_no_more_than_three_interruptions_in_ten_minutes_and_no_repeats(voice):
    for i in range(3):
        assert a.deliver(f"news {i}", a.NORMAL, sit=HERE) == "said"
    assert a.deliver("news 3", a.NORMAL, sit=HERE) == "held"
    assert a.deliver("news 3", a.NORMAL, sit=HERE) == "duplicate"
    assert a.deliver("low thing", a.LOW, sit=HERE) == "held"                   # low news waits for a summary


def test_a_long_list_is_cut_short(voice):
    for i in range(7):
        a.deliver(f"item {i}", a.NORMAL, sit=AWAY)
    out = a.welcome_back(HERE)
    assert out.count("item") == a.MAX_DIGEST and "3 more" in out


def test_other_processes_leave_news_in_the_inbox(voice):
    a.post("I practised 'Close GitHub.' while you were away. Best so far 4 of 9.", a.LOW, key="practice:x")
    a.post("I practised 'Close GitHub.' while you were away. Best so far 4 of 9.", a.LOW, key="practice:x")
    assert a.read_inbox() == 2 and len(a.held()) == 1                          # the same news once
    assert "Close GitHub" in a.welcome_back(HERE)


def test_a_developers_verification_turn_never_lands_in_the_owners_miss_list(tmp_path, monkeypatch):
    """2026-11-08: four test requests from a temporary 'claude-verify' device landed in live-misses, which practice studies."""
    from core import gateway, storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    gateway.TEST_TURN.on = True
    try:
        gateway.record_miss("fast forward the video", "nothing is playing")
    finally:
        gateway.TEST_TURN.on = False
    assert not (tmp_path / gateway.MISSES).exists()
    gateway.record_miss("close github", "no tab identifies itself")
    assert (tmp_path / gateway.MISSES).read_text(encoding="utf-8").count("close github") == 1
