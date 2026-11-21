"""Live location (owner, 2026-11-15: "I need him to have access to live location on phone and PC"): a local question is searched
by TOWN with a closed phrase; nothing else he said leaves the machine."""
import pytest

from core import localask, place, quick, webintent


@pytest.mark.parametrize("text,key", [
    ("Are there any weather advisories near me?", "alerts"),
    ("Any flood warnings today?", "alerts"),
    ("How's the traffic?", "traffic"),
    ("Is the power out around here?", "outage"),
    ("What are gas prices like?", "gas"),
    ("What's going on around here?", "news"),
    ("What is going on around here?", "news"),
    ("What is happening in my area?", "news"),
    ("Anything new in town?", "news"),
    ("Any events near me this weekend?", "events"),
    ("Where's good pizza near me?", "food"),
    ("What are the sirens about around here?", "safety")])
def test_local_questions_are_recognised(text, key):
    assert localask.subject(text)[0] == key


@pytest.mark.parametrize("text", [
    "What's the news?", "Tell me a joke", "Open the local files", "How's the weather in Richmond?", "Is there traffic in Boston?",
    "Check my local model", "I had an accident this morning.", "The construction near me is loud.", "Who won the game?", "Where's the nearest pizza in Blender?"])
def test_other_lines_are_not_local_questions(text):
    assert localask.subject(text) is None


@pytest.mark.parametrize("text", [
    "Give me the local news.", "What are the headlines near me?", "Tell me what's happening in my neighborhood.",
    "Show me the news around my area.", "Can you tell me the news around my area?"])
def test_the_phone_prefix_and_other_wordings_do_not_hide_a_local_news_question(text):
    prefixed = "[Sent from their Jarvis phone app. Reply for the phone.] " + text
    for line in (text, prefixed):
        assert localask.subject(line)[0] == "news"
        assert "Springfield, Illinois" in localask.query(line, "Springfield, Illinois")


def test_the_query_is_the_closed_phrase_and_the_town_only():
    q = localask.query("Tyler at 123 Oak Street keeps calling, so what's going on around here?", "Springfield, Illinois")
    assert "Springfield, Illinois" in q
    for leaked in ("Tyler", "123", "Oak", "calling"):
        assert leaked not in q


def test_no_place_no_query():
    assert localask.query("Any weather advisories near me?", "") == ""


def test_kind_and_calls_use_the_town(monkeypatch):
    monkeypatch.setattr(place, "label", lambda allow_home=True: "Springfield, Illinois")
    assert webintent.kind("Any weather advisories near me?") == "local"
    (skill, args), = webintent.calls_for("local", "Any weather advisories near me?")
    assert skill == "search_internet" and "Springfield, Illinois" in args["query"] and "advisories" in args["query"]


def test_without_a_known_place_it_says_so_not_guesses(monkeypatch):
    monkeypatch.setattr(place, "label", lambda allow_home=True: "")
    assert webintent.calls_for("local", "Any weather advisories near me?") == []
    monkeypatch.setattr(place, "current", lambda allow_home=True: None)
    assert "don't know where you are" in webintent.nothing_to_search("local", "x")


@pytest.mark.parametrize("text", ["Where am I?", "where am I right now", "What town am I in?", "Do you know where I am?"])
def test_where_am_i_is_answered_from_the_position(text, monkeypatch):
    assert localask.asks_where_am_i(text)
    monkeypatch.setattr(place, "current", lambda allow_home=True: {"city": "Springfield", "state": "Illinois", "source": "phone"})
    assert quick.handle(text) == "You're in Springfield, Illinois, sir, from your phone."


def test_a_position_is_reduced_to_a_town_and_kept_per_source(monkeypatch, tmp_path):
    saved = {}
    monkeypatch.setattr(place.storage, "load_global", lambda name, default: saved.get(name, default))
    monkeypatch.setattr(place.storage, "save_global", lambda name, value: saved.__setitem__(name, value))
    monkeypatch.setattr(place, "town_of", lambda lat, lon: {"city": "Springfield", "state": "Illinois"})
    record = place.update("phone", 39.8012345, -89.6412345, 40)
    assert record["lat"] == 39.80 and record["lon"] == -89.64          # a kilometre, never the exact spot
    assert saved["place-phone.json"]["city"] == "Springfield" and "place-pc.json" not in saved
    assert place.update("phone", 999, 0) is None and place.update("nowhere", 37, -76) is None
    monkeypatch.setattr(place, "refresh_pc", lambda force=False: None)
    assert place.label() == "Springfield, Illinois"


def test_an_old_position_is_not_where_he_is(monkeypatch):
    old = {"lat": 1.0, "lon": 1.0, "city": "Faraway", "state": "X", "source": "phone", "at": 1.0}
    monkeypatch.setattr(place.storage, "load_global", lambda name, default: old if name == "place-phone.json" else default)
    monkeypatch.setattr(place, "refresh_pc", lambda force=False: None)
    assert place.current(allow_home=False) is None
