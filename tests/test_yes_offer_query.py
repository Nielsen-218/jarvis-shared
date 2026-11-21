"""Two defects found live 2026-11-15 (typed + synthetic-voice check of the deployed Jarvis):

1. "People are saying the government wants everyone to bunker down and buy food. What is going on?" searched the internet for
   "What is going on" alone, and the reply said "I checked trusted sources and found no government advisory" about a question
   never put. An empty question (what is going on / is that true) asks about the sentence before it.
2. "Yes, I would like that very much." to "If you want the real headlines, say so and I'll pull them up." was answered with a
   re-ask of a Meshy plan waiting since before ("To be clear, sir: open the Meshy panel...?"): turnstate sees only questions in
   words, so the old plan was the one a yes "landed on". The offer just made owns the yes; the old plan is NOT approved."""
import pytest

from core import pcfast, webintent

BUNKER = "People are saying the government wants everyone to bunker down and buy food. What is going on?"


@pytest.mark.parametrize("said, query", [
    (BUNKER, "the government wants everyone to bunker down and buy food news"),
    ("I heard Iran struck a base in Bahrain. Is that true?", "Iran struck a base in Bahrain news"),
    ("Who won the Lakers game last night?", "Who won the Lakers game last night"),
])
def test_an_empty_question_asks_about_the_sentence_before_it(said, query):
    assert webintent.lookup_query(said) == query


@pytest.mark.parametrize("said", [
    "What is going on?",
    "My wife says the gate code is 1234. What is going on?",
    "Something is wrong with my PC. What is going on?",
    "My mom says she's moving to Texas. Is that true?",
    "I think my neighbour is selling his house. Is that true?",
    "I was thinking about dinner. What is going on?",
])
def test_an_empty_question_never_searches_alone_or_about_his_house(said):
    assert webintent.lookup_query(said) == ""
    assert webintent.lookup_queries(said) == []


OFFER = "That sounds like misinformation, sir. If you want the real headlines, say so and I'll pull them up."


def _history(reply):
    return [{"role": "user", "content": BUNKER}, {"role": "assistant", "content": reply}]


@pytest.mark.parametrize("yes", ["Yes, I would like that very much.", "Yes.", "Yeah, do that.", "Yes please."])
def test_a_yes_to_the_offer_just_made_is_not_a_stale_plans_yes(yes):
    assert pcfast._yes_to_an_offer(yes, _history(OFFER))
    assert pcfast._yes_to_an_offer(yes, _history("Would you like me to open it now?"))


@pytest.mark.parametrize("history", [
    [],
    _history("It is overcast in Springfield, sir."),                       # nothing offered: a yes means the plan from before
    _history("To be clear, sir: open the Meshy panel in the Blender sidebar?"),
])
def test_a_yes_with_no_offer_in_the_last_reply_keeps_the_plans_reask(history):
    # the last case IS a question about the plan; turnstate treats that yes as fresh, so this helper never has to skip anything
    if history and "Meshy" in history[-1]["content"]:
        assert pcfast._yes_to_an_offer("Yes.", history)
    else:
        assert not pcfast._yes_to_an_offer("Yes.", history)


@pytest.mark.parametrize("no", ["No.", "Not now.", "No thanks.", "What?"])
def test_a_no_or_a_question_is_never_a_yes_to_an_offer(no):
    assert not pcfast._yes_to_an_offer(no, _history(OFFER))


def test_pcfast_does_not_re_ask_the_old_plan_for_a_yes_to_an_offer(monkeypatch):
    from core import approvals, turnstate
    monkeypatch.setattr(approvals, "meant", lambda uid, text: "reask")
    monkeypatch.setattr(turnstate, "reask", lambda uid, title="sir": "To be clear, sir: open the Meshy panel?")
    said = pcfast.handle("Yes, I would like that very much.", _history(OFFER))
    assert not said or "To be clear" not in said
    stale = pcfast.handle("Yes.", _history("It is overcast in Springfield, sir."))
    assert stale is None or isinstance(stale, str)          # unchanged path: the re-ask is still turnstate's to say


def test_a_yes_to_the_headlines_offer_becomes_the_news_search_about_the_claim():
    from core import chatturn
    history = _history(OFFER) + [{"role": "user", "content": "Yes, I would like that very much."}]
    said = chatturn.yes_reissues(history, web=True)
    assert "bunker down" in said
    assert webintent.kind(said) == "news"


@pytest.mark.parametrize("first", [
    "What is going on?", "My wife says the gate code is 1234. What is going on?",
    "Something is wrong with my PC. What is going on?"])
def test_a_yes_to_a_headlines_offer_about_nothing_public_searches_nothing(first):
    from core import chatturn
    history = [{"role": "user", "content": first}, {"role": "assistant", "content": OFFER},
               {"role": "user", "content": "Yes."}]
    assert chatturn.yes_reissues(history, web=True) == ""


@pytest.fixture
def brain(monkeypatch):
    from types import SimpleNamespace
    from anthropic.types import TextBlock
    from brain import llm
    from core import intelligence, pcfast as pcfast_, quick, websearch
    from tools import registry
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast_, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(llm.Brain, "_language_request", lambda self, text, emit: False)
    state = SimpleNamespace(replies=[OFFER], searches=[], model_calls=0)

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        state.model_calls += 1
        text = state.replies.pop(0) if state.replies else "Here is what the sources say, sir."
        if on_text:
            on_text(text)
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")

    def run(name, args):
        state.searches.append((name, dict(args)))
        return "1. No government order to bunker down (apnews.com, 2026-11-15); officials call the posts false.", False

    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(registry, "run", run)
    monkeypatch.setattr(websearch, "LAST", {})
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.state = state
    return b


def test_brain_yes_to_the_headlines_offer_searches_the_claim(brain):
    brain.ask(BUNKER, [].append)
    said = []
    brain.ask("Yes, I would like that very much.", said.append)
    searches = [c for c in brain.state.searches if c[0] in ("search_internet", "research_online")]
    assert searches, "no search ran"
    assert "bunker down" in str(searches[-1][1]).lower()
    assert "To be clear" not in " ".join(said)


# --- review findings 2026-11-15 --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", [
    "Reports say Sarah Johnson's phone number 757-555-1234 leaked. Is that true?",
    "They are saying 192.168.1.5 is compromised. Is that true?",
    "People are saying the owner's PC got a virus. What is going on?",
    "People are saying the neighbors are moving. What is going on?",
    "The Microsoft Teams call dropped. What happened?",
    "There are sirens outside on Mercury Boulevard. What is going on?",
    "The Lakers game is on TV. What is going on?",
    "Everyone says Jessica from work is quitting. Is that true?",
    "People are saying Mark is dead. What is going on?",
    "Someone posted that Zephyr Quill got arrested in Springfield. Is that true?",
    "Do you know who won the Lakers game last night? Is that true?",
    "I heard my sister got fired. Is that true?",
])
def test_the_context_search_never_takes_numbers_neighbours_individuals_or_observations(said):
    assert webintent.lookup_query(said) == "", said


def test_a_public_claim_with_a_cue_is_still_searched():
    assert webintent.lookup_query("Reports say the president signed a new tariff bill. Is that true?").endswith("news")
    assert webintent.lookup_query("I heard Iran struck a base in Bahrain. Is that true?") == "Iran struck a base in Bahrain news"


def test_a_stale_yes_is_not_taken_for_the_offer_after_two_minutes_and_no_pc_planner_runs_for_it(monkeypatch):
    from core import approvals, gateway, turnstate
    calls = []
    monkeypatch.setattr(approvals, "meant", lambda uid, text: "reask")
    monkeypatch.setattr(turnstate, "reask", lambda uid, title="sir": "To be clear, sir: open the Meshy panel?")
    monkeypatch.setattr(gateway, "decide", lambda *a, **k: calls.append("decide"))
    monkeypatch.setattr(gateway, "execute", lambda *a, **k: calls.append("execute"))
    for yes in ("Yes please.", "Yes.", "Yes, I would like that very much."):
        assert pcfast.handle(yes, _history(OFFER)) in (None, "")       # the brain answers it: nothing planned, nothing run
    assert calls == []
    token = pcfast.OFFER_FRESH.set(False)               # his yes came after YES_MAX_AGE: the stale plan's re-ask stays
    try:
        assert pcfast.handle("Yes.", _history(OFFER)) == "To be clear, sir: open the Meshy panel?"
    finally:
        pcfast.OFFER_FRESH.reset(token)


@pytest.mark.parametrize("reply", [
    "If you want to leave, just say so and I can't stop you.",
    "If you want me to delete the old files, let me know and I'll do it.",
])
def test_an_if_you_want_line_that_is_no_offer_binds_no_yes(reply):
    from core import chatturn
    assert chatturn._offer_in(reply) == ""
