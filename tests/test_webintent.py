"""Ordinary spoken requests for the live internet are recognised deterministically (and nothing else is)."""
import random

import pytest

from core import webanswer, webintent


@pytest.fixture(autouse=True)
def _pinned_day(monkeypatch):
    """The query that goes out carries today's date (core/webanswer.rewrite); these tests pin the day."""
    from datetime import date
    from core import webanswer
    monkeypatch.setattr(webanswer, "today", lambda: date(2026, 11, 15))

WORDS_A = ["frozen", "amber", "velvet", "copper", "quiet", "hollow", "paper", "crimson", "marble", "golden"]
WORDS_B = ["pencil", "lantern", "meadow", "anchor", "falcon", "ledger", "window", "garden", "button", "mirror"]
NOTE = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "


@pytest.mark.parametrize("said, query", [
    ("Jarvis, search the internet for the latest NVIDIA driver for the RTX 5070 Ti.", "latest NVIDIA driver for the RTX 5070 Ti"),
    ("Google this: ModuleNotFoundError no module named yaml in Python.", "ModuleNotFoundError no module named yaml in Python"),
    ("Can you look up the Blender 5.0 release date online?", "Blender 5.0 release date"),
    ("Look it up: how long do RTX cards last", "how long do RTX cards last"),
    ("Find the latest news about Blender 5.0.", "latest news about Blender 5.0"),
    ("What's the latest Nvidia driver version?", "What's the latest Nvidia driver version"),
    ("hey jarvis search online for cheap flights to miami please", "cheap flights to miami"),
    ("Do a web search on heat pump efficiency in winter.", "heat pump efficiency in winter"),
    (NOTE + "google the Python 3.13 release notes", "Python 3.13 release notes"),
    ("Research how to fix a ModuleNotFoundError in Python online.", "how to fix a ModuleNotFoundError in Python"),
])
def test_search_requests_in_everyday_words_are_recognised_and_the_topic_extracted(said, query):
    assert webintent.kind(said) == "search", said
    assert webintent.query_of(said).lower() == query.lower(), webintent.query_of(said)
    name, args = webintent.call_for("search", said)
    assert name in ("search_internet", "research_online")
    assert (args.get("query") or args.get("question")).lower() == webanswer.rewrite(query).lower()


def test_requests_for_facts_inside_pages_read_the_pages_and_plain_searches_list_results():
    assert webintent.call_for("search", "Find the latest NVIDIA driver for the RTX 5070 Ti")[0] == "research_online"
    assert webintent.call_for("search", "Google this: ModuleNotFoundError no module named yaml, how do I fix it")[0] == "research_online"
    assert webintent.call_for("search", "search online for Blender 5.0 release date")[0] == "research_online"
    assert webintent.call_for("search", "google cheap flights to miami") == ("search_internet", {"query": webanswer.rewrite("cheap flights to miami")})
    assert webintent.call_for("search", "look up Joey Carlino") == ("search_internet", {"query": webanswer.rewrite("Joey Carlino")})


@pytest.mark.parametrize("said, topic, count", [
    ("Look up three good Blender rigging tutorials on YouTube.", "Blender rigging tutorials", 3),
    ("Find me a couple of YouTube videos on Python decorators", "Python decorators", 2),
    ("Jarvis, search YouTube for a beginner guitar tutorial", "a beginner guitar tutorial", 3),
    ("can you find video tutorials about soldering", "about soldering", 3),
])
def test_youtube_requests_route_to_tutorial_discovery(said, topic, count):
    assert webintent.kind(said) == "youtube", said
    name, args = webintent.call_for("youtube", said)
    assert name == "find_youtube_tutorials" and args["count"] == count
    assert args["topic"].lower().strip("a ") in topic.lower() or topic.lower() in args["topic"].lower(), args


@pytest.mark.parametrize("said", [
    "What did you find about the NVIDIA driver?", "What did we learn about Blender rigging?",
    "From your saved research, what's the fix for that Python error?", "Without searching, what do you have on heat pumps?",
    "Tell me about the driver from your earlier research.",
])
def test_recall_of_saved_research_never_goes_online(said):
    assert webintent.kind(said) == "recall", said


@pytest.mark.parametrize("said", [
    "Learn this.", "learn that", "Save that finding.", "Save this research for later", "Keep that for later.",
    "Remember what you just found.", "Add it to your library.", "OK Jarvis, save what you found",
])
def test_learn_this_keeps_the_last_finding(said):
    assert webintent.kind(said) == "learn", said


@pytest.mark.parametrize("said", [
    "Search my notes for the dentist appointment", "look up my reminders", "find my shopping list",
    "what's on my screen", "search the library for the ESP32 pinout", "what did we talk about yesterday",
    "When I say blue notebook, reply only with noted", "learn this: when I say frozen pencil say noted",
    "Remember that my sister lives in Miami.", "set a timer for ten minutes", "how are you doing today?",
    "I was online all day yesterday.", "Please make corrections too.", "we have no request to stop.",
    "Juris.", "Open YouTube", "Play some music", "The research paper is on my desk.",
])
def test_local_requests_teaching_and_ordinary_talk_are_not_web_requests(said):
    assert webintent.kind(said) is None, said


def test_bare_random_phrases_never_launch_a_search():
    rng = random.Random()
    for _ in range(40):
        phrase = f"{rng.choice(WORDS_A)} {rng.choice(WORDS_B)}"
        assert webintent.kind(phrase) is None, phrase
        assert webintent.kind(NOTE + phrase) is None, phrase
        assert webintent.kind(f"hey jarvis {phrase}") is None, phrase


def test_phone_note_and_wake_word_are_stripped_from_the_query():
    said = NOTE + "Hey Jarvis, google this: best budget soldering iron 2026"
    assert webintent.kind(said) == "search"
    assert webintent.query_of(said) == "best budget soldering iron 2026"
    assert webintent.count_of("find me four youtube videos") == 4 and webintent.count_of("find videos") == 3
    assert webintent.count_of("find 9 videos") == 5


@pytest.mark.parametrize("said", [
    "What did you learn?", "what have you learned lately", "What have you learned recently?", "what have you learned so far",
    "what skills did you learn", "list your learned skills",
])
def test_asking_what_he_learned_is_not_a_library_recall(said):
    assert webintent.kind(said) is None


@pytest.mark.parametrize("said", ["what did you learn about rigging", "what did you learn from the research"])
def test_what_did_you_learn_about_a_topic_still_goes_to_the_library(said):
    assert webintent.kind(said) == "recall"
