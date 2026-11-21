"""A public claim plus "Is that true?" / "What is going on?" is looked up, in code (live 2026-11-15, the PC: "I heard Iran struck a base
in Bahrain. Is that true?" -> "That report isn't true yet" and the bunker-down line -> "it's misinformation", both with no search; the
model looks things up only when it feels like it). Private, personal and machine lines are never searched."""
from types import SimpleNamespace

import pytest

from core import webintent

BUNKER = "People are saying the government wants everyone to bunker down and buy food. What is going on?"
IRAN = "I heard Iran struck a base in Bahrain. Is that true?"


@pytest.mark.parametrize("said, query", [
    (BUNKER, "the government wants everyone to bunker down and buy food news"),
    (IRAN, "Iran struck a base in Bahrain news"),
    ("Reports say the president signed a new tariff bill. Is that true?", None),
])
def test_a_public_claim_with_an_empty_question_is_a_news_search(said, query):
    assert webintent.kind(said) == "news"
    if query:
        assert webintent.news_query(said) == query


@pytest.mark.parametrize("said", [
    "Something is wrong with my PC. What is going on?", "My wife says the gate code is 1234. Is that true?", "Is that true?",
    "What is going on?", "I heard my sister got fired. Is that true?", "Everyone says Jessica from work is quitting. Is that true?",
    "The Lakers game is on TV. What is going on?", "I was thinking about dinner. What is going on?",
])
def test_nothing_private_or_personal_becomes_a_claim_check(said):
    assert webintent.kind(said) is None


@pytest.fixture
def brain(monkeypatch):
    from anthropic.types import TextBlock
    from brain import llm
    from core import intelligence, pcfast, quick, websearch
    from tools import registry
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(llm.Brain, "_language_request", lambda self, text, emit: False)
    state = SimpleNamespace(calls=[])

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        text = "No credible report says so, sir (apnews.com, 2026-11-15); officials call the posts false."
        if on_text:
            on_text(text)
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")

    def run(name, args):
        state.calls.append((name, dict(args)))
        return "1. No government order to bunker down (apnews.com, 2026-11-15); officials call the posts false.", False

    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(registry, "run", run)
    monkeypatch.setattr(websearch, "LAST", {})
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.state = state
    return b


def test_the_brain_searches_the_claim_before_answering_it(brain):
    brain.ask(BUNKER, [].append)
    searched = [c for c in brain.state.calls if c[0] in ("search_internet", "research_online")]
    assert searched, "the claim was answered from memory"
    assert "bunker down" in str(searched[0][1]).lower()


def test_the_brain_searches_nothing_for_a_machine_question(brain):
    brain.ask("Something is wrong with my PC. What is going on?", [].append)
    assert not [c for c in brain.state.calls if c[0] in ("search_internet", "research_online")]


# --- review findings 2026-11-15: private names, streets, local places, machines and games must never reach a search -------------
@pytest.mark.parametrize("said", [
    "People say the police are outside Tyler's place. What is going on?",
    "They say the police arrested Tyler. What is going on?",
    "They say Jessica from work was in a crash. Is that true?",
    "People say Mark is dead and the police are investigating. Is that true?",
    "Reports say the shooting happened at the mall near Tyler's work. Is that true?",
    "People say a police officer named David Ruiz shot someone. Is that true?",
    "People are saying the government is hiding the outbreak, and Tyler has a fever. What is going on?",
    "People say the storm hits tonight and Tyler is stuck at the airport. What is going on?",
    "People are saying the police arrested Sarah Mitchell at 14 Oak Lane. What is going on?",
    "Apparently the police shot a man on Oak Lane. Is that true?",
    "They say a shooting happened at the school on Oak Road. Is that true?",
    "Everyone says there was a shooting at Springfield High School. Is that true?",
    "They said on the radio there is a tornado in Springfield. Is that true?",
    "They say the update crashed. What happened?",
    "People say Blender crashed on launch. What is going on?",
    "Everyone says the attack on the base is op in Apex. What is going on?",
    "People say the strike in Apex Legends is broken. Is that true?",
    "People are saying the attack on titan finale is out. Is that true?",
    "They say who knows what the base is doing. What is going on?",
    "Remind me to call Tyler. They say the police arrested someone. What is going on?",
    "Open Chrome. People say the war is over. Is that true?",
    "People say the launch went well. Really?",
])
def test_the_claim_check_refuses_names_streets_local_places_machines_games_and_extra_sentences(said):
    assert webintent.kind(said) is None, said


@pytest.mark.parametrize("said, query", [
    ("Everyone says the military attacked a border town. Is that true?", "the military attacked a border town news"),
    ("Reports say the president signed a new tariff bill. Is that true?", "the president signed a new tariff bill news"),
])
def test_other_public_claims_are_still_checked_and_the_hearsay_lead_is_not_in_the_query(said, query):
    assert webintent.kind(said) == "news"
    assert webintent.news_query(said) == query


# --- second review 2026-11-15: a closed vocabulary, so nothing outside it can be searched ----------------------------------------
SECOND_REVIEW = ['I saw the police outside. what is going on?', 'I saw the fbi at tylers. what is going on?', 'I read a letter from the fbi. what is going on?', 'I read the police report about the break-in. what is going on?', 'I heard about a flu outbreak at the daycare. what is going on?', 'I heard a bomb go off. what is going on?', 'I see the shutdown is taking forever. what is going on?', 'I see failed login attacks in the logs. what is going on?', 'I see the bomb in the video. what is going on?', 'I read the evacuation notice from the city. what is going on?', 'they say the shutdown takes forever. Is that true?', 'people say the bunker in fallout is safe. Is that true?', 'people say the police in gta are too hard. Is that true?', 'people say the voters on twitch are brigading. Is that true?', 'people say the shooting range is fun. Is that true?', 'people say the police arrested tyler', 'people say the police arrested tyler. is that true?', 'people say the police arrested john smith. is that true?', 'people say the police arrested the cousin. is that true?', 'people say there was a shooting in chatham. is that true?', 'people say there was a shooting in williamsburg. is that true?', 'people say the police arrested usaa. is that true?', 'Everyone in the family says the government is banning cash. Is that true?', 'I heard from tyler that the government is banning cash. Is that true?', 'I heard sarah say the government is banning cash. Is that true?', 'Apparently tyler says the government is banning cash. Is that true?', 'I read in an email that the government is banning cash. Is that true?', 'people say the police are on this block. is that true?', 'They say the police are outside. What is going on here?', 'people say the government is investigating your family. is that true?', 'people say the government is reading your texts. is that true?', 'the CIA is hacking jarvis', 'people say the government is investigating tylersmith.com. is that true?', 'people say the police arrested Émile. is that true?', 'people say the police arrested Jordan. is that true?', 'people say the government is banning cash whereas tyler thinks it is fine. is that true?', 'People say the update crashed. What happened?', 'They say the police arrested Tyler. What is going on?', "Reports say the shooting happened at the mall near Tyler's work. Is that true?", 'Open Chrome. People say the war is over. Is that true?', 'Is that true?']


@pytest.mark.parametrize("said", SECOND_REVIEW)
def test_second_review_lines_never_become_a_search(said):
    assert webintent.kind(said) != "news", said


PRIVATE_WORDS = ("tyler sarah mark jessica john smith cousin uncle aunt nephew niece grandma grandfather roommate coworker teacher "
                 "classmate landlord boss friend girlfriend jacksonville williamsburg chatham chesapeake suffolk portsmouth lakeview lincoln "
                 "westfield riverside richmond memorial kroger usaa daycare gym church pharmacy clinic hospital school mall neighbor patient "
                 "cancer diabetes bank mortgage bankruptcy fallout factorio gta twitch minecraft apex blender python laptop router "
                 "tylersmith.com 555 jordan paris washington india london emile").split()


@pytest.mark.parametrize("word", PRIVATE_WORDS)
def test_no_single_private_word_gets_through_any_part_of_a_claim(word):
    for template in ("People say the police arrested {w}. Is that true?", "I heard the government is investigating {w}. What is going on?",
                     "Reports say {w} was hit by a missile. Is that true?", "Everyone says the president met {w}. Is that true?",
                     "I heard that the military attacked the {w} base. What is going on?"):
        assert webintent.kind(template.format(w=word)) != "news", template.format(w=word)
