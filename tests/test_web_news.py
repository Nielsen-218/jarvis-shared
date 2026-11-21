"""The news and "look it up" (2026-11-14). The owner's test that evening: "What's on the news about this? People have to
bunker down. What's going on?" and "Do your research and tell me what the news is saying about that." were answered "I've
checked the major news feeds and there is no credible report... misinformation" and "I'm pulling the latest headlines
right now" with NO search run. His rule: what Jarvis does not know he looks up on the internet, always."""
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from core import intelligence, speech, webanswer, webintent


@pytest.fixture(autouse=True)
def _pinned_day(monkeypatch):
    """The query that goes out carries today's date (core/webanswer.rewrite); these tests pin the day."""
    from datetime import date
    from core import webanswer
    monkeypatch.setattr(webanswer, "today", lambda: date(2026, 11, 15))

BUNKER_1 = "What's on the news about this? People have to bunker down. What's going on?"
BUNKER_2 = ("I see that people say the government is saying to bunker down and buy food and stuff like that. Do your research "
            "and tell me what the news is saying about that.")


# --- the news and research are web requests ----------------------------------------------------------------------------------
@pytest.mark.parametrize("said, query", [
    (BUNKER_1, "People have to bunker down news"),
    (BUNKER_2, "the government is saying to bunker down and buy food news"),
    ("What's the news?", "top news headlines today"),
    ("Check the news for me", "top news headlines today"),
    ("Tell me the latest headlines", "top news headlines today"),
    ("What's happening in the world today?", "top news headlines today"),
    ("Any news on the election?", "the election news"),
])
def test_asking_for_the_news_is_a_live_search(said, query):
    assert webintent.kind(said) == "news"
    name, args = webintent.call_for("news", said)
    assert name == "search_internet" and args["query"] == webanswer.rewrite(query) and "source" in args["objective"]


def test_the_news_about_that_takes_what_he_said_just_before():
    name, args = webintent.call_for("news", "What's the news about that?", "People say the government wants us to bunker down.")
    assert name == "search_internet" and args["query"] == webanswer.rewrite("the government wants us to bunker down news")
    # ...but never another request for the news as its own topic
    assert webintent.call_for("news", "What's the news about that?", "What's the news?")[1]["query"] == webanswer.rewrite("top news headlines today")


@pytest.mark.parametrize("said, question", [("Do some research on electric bikes", "electric bikes"),
                                            ("Do your research on whether the DMV is open tomorrow", "whether the DMV is open tomorrow")])
def test_do_your_research_reads_the_pages(said, question):
    assert webintent.kind(said) == "news"
    assert webintent.call_for("news", said) == ("research_online", {"question": webanswer.rewrite(question), "pages": 3})


@pytest.mark.parametrize("said", ["I heard on the news that gas is going up.", "That's good news.", "No news is good news.",
                                  "Any news for me?", "I got some bad news today.", "Open the news app.", "What's new?",
                                  "What's up?", "Click on the news tab", "The news is depressing lately.",
                                  "What did the news say about my notes?", "I did my research already."])
def test_mentioning_news_is_not_asking_for_it(said):
    assert webintent.kind(said) is None


def test_the_older_search_wordings_are_unchanged():
    assert webintent.kind("Search the web for the latest Blender release") == "search"
    assert webintent.call_for("search", "Search the web for the latest Blender release")[0] == "research_online"
    assert webintent.kind("Look up three good Blender rigging tutorials on YouTube") == "youtube"


# --- a sentence that means he has nothing to answer from -----------------------------------------------------------------
@pytest.mark.parametrize("sentence", [
    "Sir, I've checked the major news feeds and there is no credible report of a government advising people to bunker down.",
    "I'm pulling the latest headlines from trusted news feeds right now, sir.",
    "Give me a moment to read through them and tell you exactly what's being reported.",
    "I've searched and found nothing on that.", "I’ve checked the news feeds and found no credible reports."])
def test_a_claim_of_having_looked_is_recognised(sentence):
    assert intelligence.claims_lookup(sentence)


@pytest.mark.parametrize("sentence", ["I don't have current information on that.", "I'm not sure who won last night.",
                                      "I can't browse the internet from here.", "I have no record of that.", "I don't know.",
                                      "As of my last update, the prime minister was someone else."])
def test_an_admission_of_not_knowing_is_recognised(sentence):
    assert intelligence.admits_unknown(sentence)


@pytest.mark.parametrize("sentence", ["The sky looks blue because air scatters blue light.", "I'm good, thanks for asking.",
                                      "I don't know about you, but I'd pick Python.", "I'm looking forward to it.",
                                      "I'm not sure that's wise, honestly.", "Have you checked the oil?", "Yes, I'm here.",
                                      "I'm reading between the lines here."])
def test_ordinary_sentences_are_neither(sentence):
    assert not intelligence.admits_unknown(sentence) and not intelligence.claims_lookup(sentence)


# --- the draft is held ----------------------------------------------------------------------------------------------------------
def _polish(sentences, unknown, **kw):
    said = []
    p = speech.Polisher(said.append, asked="Who won the game last night?", retry=True, unknown=unknown, **kw)
    for s in sentences:
        p(s)
    return p, said


def test_a_draft_that_opens_on_not_knowing_is_held_whole():
    p, said = _polish(["I don't have current information on that.", "You could check a sports site."], intelligence.admits_unknown)
    assert said == [] and p.stale and p.lookup and p.pending == ["I don't have current information on that.",
                                                                 "You could check a sports site."]


def test_an_answer_is_said_as_before_and_a_later_doubt_does_not_take_it_back():
    p, said = _polish(["The Lakers won by six.", "I'm not sure who scored most."], intelligence.admits_unknown)
    assert said == ["The Lakers won by six.", "I'm not sure who scored most."] and not p.lookup


def test_after_a_tool_ran_nothing_is_held():
    p, said = _polish(["I've checked: the Lakers won by six."], intelligence.claims_lookup, fresh=lambda: True)
    assert said == ["I've checked: the Lakers won by six."] and not p.lookup


def test_without_a_judge_nothing_changes():
    said = []
    p = speech.Polisher(said.append, asked="Who won?", retry=True)
    p("I don't know.")
    assert said == ["I don't know."] and not p.lookup


# --- the Brain looks it up ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def brain(monkeypatch):
    from brain import llm
    from core import pcfast, quick, websearch
    from tools import registry
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(llm.Brain, "_language_request", lambda self, text, emit: False)
    state = SimpleNamespace(replies=[], searches=[], result="1. Lakers beat Celtics 110-104 (espn.com, 2026-11-13)", error=False,
                            seen=[])

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        state.seen.append([m for m in history])
        text = state.replies.pop(0)
        if on_text:
            on_text(text)
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")

    def run(name, args):
        state.searches.append((name, dict(args)))
        return state.result, state.error

    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(registry, "run", run)
    monkeypatch.setattr(websearch, "LAST", {})
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.state = state
    return b


def _internet(brain):
    """The searches that really went to the internet (another tool may answer a question about the machine)."""
    return [c for c in brain.state.searches if c[0] == "search_internet"]


def _text(message):
    content = message["content"]
    return content if isinstance(content, str) else " ".join(getattr(x, "text", "") or (x.get("text", "") if isinstance(x, dict) else "")
                                                             for x in content)


def test_not_knowing_becomes_a_real_search_and_the_answer_comes_from_it(brain):
    brain.state.replies = ["I don't have current information on last night's game.", "The Lakers beat the Celtics 110 to 104, per ESPN."]
    said = []
    brain.ask("Who won the Lakers game last night?", said.append)
    assert " ".join(said) == "The Lakers beat the Celtics 110 to 104, per ESPN."       # (a first clause may be said early)
    assert brain.state.searches == [("search_internet", {"query": "Who won the Lakers game November 14, 2026",
                                                         "objective": "the answer to the question, with the source and its date"})]
    kinds = [(m["role"], m["content"][0].get("type") if isinstance(m["content"], list) and isinstance(m["content"][0], dict) else "text")
             for m in brain.messages]
    assert kinds[-3:] == [("assistant", "tool_use"), ("user", "tool_result"), ("assistant", "text")], kinds
    assert not any("don't have current information" in _text(m) for m in brain.messages), "the draft left the history"


def test_a_pretended_lookup_is_never_said_and_is_looked_up_for_real(brain):
    brain.state.replies = ["Sir, I've checked the major sports feeds and there was no game last night.",
                           "The Lakers beat the Celtics 110 to 104 on the thirteenth."]
    said = []
    brain.ask("Did the Lakers play last night?", said.append)
    assert said == ["The Lakers beat the Celtics 110 to 104 on the thirteenth."] and len(brain.state.searches) == 1


def test_when_nothing_can_search_he_says_so(brain):
    from core import websearch
    brain.state.replies = ["I'm not sure who won."]
    brain.state.result = f"{websearch.MARK_OFFLINE}: bing-rss (timeout). I did not search, so I can't give you live results."
    said = []
    brain.ask("Who won the Lakers game last night?", said.append)
    assert len(brain.state.searches) == 1 and said and said[0].startswith(websearch.MARK_OFFLINE)
    assert "I'm not sure who won" not in " ".join(said)


def test_a_failed_search_gets_one_honest_line(brain):
    brain.state.replies = ["I don't know who won."]
    brain.state.result, brain.state.error = "boom", True
    said = []
    brain.ask("Who won the Lakers game last night?", said.append)
    assert said == ["I don't know that, and I couldn't reach the internet to look it up."]


@pytest.mark.parametrize("question", ["What's my wife's name?", "Where did I park?", "Am I free on Friday?"])
def test_a_question_about_him_is_never_sent_to_the_internet(brain, question):
    brain.state.replies = ["I don't know that."]
    said = []
    brain.ask(question, said.append)
    assert _internet(brain) == [] and said == ["I don't know that."]


def test_a_remark_is_not_looked_up(brain):
    brain.state.replies = ["I'm not sure."]
    said = []
    brain.ask("That was a long day.", said.append)
    assert _internet(brain) == [] and said == ["I'm not sure."]


def test_the_off_switch(brain, monkeypatch):
    monkeypatch.setenv("JARVIS_LOOK_IT_UP", "off")
    brain.state.replies = ["I don't have current information on that."]
    said = []
    brain.ask("Who won the Lakers game last night?", said.append)
    assert _internet(brain) == [] and said == ["I don't have current information on that."]


def test_asking_for_the_news_searches_before_any_model_is_asked(brain):
    brain.state.replies = ["Reports say several agencies issued winter storm guidance, per AP on the fourteenth."]
    said = []
    brain.ask(BUNKER_2, said.append)
    assert brain.state.searches[0] == ("search_internet", {
        "query": webanswer.rewrite("the government is saying to bunker down and buy food news"),
        "objective": "what current news reports say about it, with each source and its date"})
    assert " ".join(said) == "Reports say several agencies issued winter storm guidance, per AP on the fourteenth."
    assert any(isinstance(m["content"], list) and isinstance(m["content"][0], dict) and m["content"][0].get("type") == "tool_result"
               for m in brain.state.seen[0]), "the model saw the search results when it answered"


# --- review round 1 (2026-11-15): confirmed defects ------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["Find out what's using my CPU", "Check the internet speed", "check the web for me later",
                                  "Find out if my battery is charging", "I heard the latest news, thanks.",
                                  "Open the news app", "Read the headlines of this document", "Any updates on my mission?",
                                  "Any updates on my order?", "I saw the top headlines this morning and they were grim.",
                                  "Tell me the news when I get home",
                                  "No, actually, first of all, Claude needs to do a research on Jarvis Ironman."])
def test_wide_research_and_news_patterns_no_longer_catch_other_requests(said):
    assert webintent.kind(said) is None


@pytest.mark.parametrize("previous", ["Remind me to call my doctor tomorrow", "My knee hurts and I'm worried",
                                      "Open Notepad and type my password", "I'm going to the bank at noon",
                                      "Is Maya's flight on time?"])
def test_a_private_previous_line_is_never_the_search_topic(previous, monkeypatch):
    from core import users
    monkeypatch.setattr(users, "all_users", lambda: {"owner": {"name": "Alex"}, "maya": {"name": "Maya"}})
    assert webintent.news_query("What's the news about that?", previous) == "top news headlines today"


def test_a_worldly_previous_line_is_still_the_topic():
    assert webintent.news_query("What's the news about that?", "People say the government wants us to bunker down.")         == "the government wants us to bunker down news"


def test_a_private_sentence_of_the_same_line_is_not_the_topic():
    assert webintent.news_query("My wife is sick. What's the news about that?") == "top news headlines today"


@pytest.mark.parametrize("sentence", ["I've looked at this from both sides.", "I've reviewed the plan with you.",
                                      "I'm looking at your question carefully.", "I've checked the oil."])
def test_looked_at_things_that_are_not_news_is_not_a_claim(sentence):
    assert not intelligence.claims_lookup(sentence)


@pytest.mark.parametrize("sentence", ["I'm not sure, but I think it's Paris.", "I don't know for certain, but it's probably Tuesday.",
                                      "I'm not sure; it's likely Paris."])
def test_a_hedged_answer_is_not_an_admission(sentence):
    assert not intelligence.admits_unknown(sentence)


def test_the_order_tell_me_is_still_about_the_world(brain):
    brain.state.replies = ["I don't have current information on that.", "The Lakers won, per ESPN."]
    said = []
    brain.ask("Tell me who won the Lakers game last night?", said.append)
    assert len(brain.state.searches) == 1


def test_a_family_name_is_never_sent_to_a_search(brain, monkeypatch):
    from core import users
    monkeypatch.setattr(users, "all_users", lambda: {"owner": {"name": "Alex"}, "maya": {"name": "Maya"}})
    brain.state.replies = ["I don't know."]
    said = []
    brain.ask("Is Maya's flight on time today?", said.append)
    assert _internet(brain) == []


def test_the_early_exit_never_says_the_faked_check():
    said = []
    p = speech.Polisher(said.append, asked="Who won the game last night?", retry=True, unknown=intelligence.claims_lookup)
    p("Sir, I've checked the major sports feeds and there was no game.")
    assert said == [] and p.lookup
    p.release(lambda s: "I haven't actually looked that up." if intelligence.claims_lookup(s) else None)
    assert said == ["I haven't actually looked that up."]
    assert p.edits and p.edits[-1][1] == "I haven't actually looked that up."


def test_the_early_exit_still_says_an_honest_admission_as_written():
    said = []
    p = speech.Polisher(said.append, asked="Who won the game last night?", retry=True, unknown=intelligence.admits_unknown)
    p("I don't have current information on that.")
    p.release(lambda s: "I haven't actually looked that up." if intelligence.claims_lookup(s) else None)
    assert said == ["I don't have current information on that."]


# --- review round 2 (2026-11-15) ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["I did some research on laptops and I'm going with the Dell.", "Don't do research on this",
                                  "no need to do research on that", "Stop. Don't do the research.", "I'll do more research on it tonight",
                                  "The research on sleep says eight hours.", "He does research on bees", "Do you do research on the side?"])
def test_a_statement_about_research_is_not_a_request_for_it(said):
    assert webintent.kind(said) is None


@pytest.mark.parametrize("said, query", [("Latest news on AI?", "AI news"), ("What's the news on F1?", "F1 news"),
                                         ("what's going on with the world cup", "the world cup news"),
                                         ("read me the news please", "top news headlines today"),
                                         ("pull up the news", "top news headlines today"),
                                         ("tell me the news about Ukraine and Russia please", "Ukraine and Russia news")])
def test_short_and_awkward_topics_are_kept(said, query):
    assert webintent.kind(said) == "news"
    assert webintent.call_for("news", said)[1]["query"] == webanswer.rewrite(query)


@pytest.mark.parametrize("said", ["what are the headlines on this page", "Are there any new headlines on the page?",
                                  "any news on the PR build status", "any news on the jarvis service?"])
def test_news_about_the_screen_or_the_machine_is_not_web_news(said):
    assert webintent.kind(said) is None


@pytest.mark.parametrize("said", ["Any news on my sister's illness?", "research on my house on Oak Lane"])
def test_a_private_topic_in_the_asking_line_is_not_searched(said):
    assert webintent.kind(said) is None


def test_a_household_name_in_the_asking_line_is_not_searched(monkeypatch):
    from core import users
    monkeypatch.setattr(users, "all_users", lambda: {"owner": {"name": "Alex"}, "sarah": {"name": "Sarah"}})
    assert webintent.kind("Any news on Sarah?") is None


@pytest.mark.parametrize("previous", ["password is hunter2", "The lab results came back positive", "What's the weather in Springfield?",
                                      "Thanks", "How do I fix this error: KeyError token in auth.py?"])
def test_a_sensitive_or_empty_previous_line_is_not_a_topic(previous):
    assert webintent.news_query("What's the news about that?", previous) == "top news headlines today"


@pytest.mark.parametrize("question", ["What's the code for the gate?", "Is Dad home?", "Where's the dog?", "Is the door locked?",
                                      "Did the build pass?", "What's on the calendar tomorrow?", "Who's at the door?",
                                      "How much RAM is free?", "Do you have any pets?", "Can you feel pain?", "Who made you?",
                                      "Do you remember what we talked about yesterday?"])
def test_a_question_about_the_house_the_machine_or_himself_is_never_searched(brain, question):
    brain.state.replies = ["I don't have any information on that."]
    said = []
    brain.ask(question, said.append)
    assert _internet(brain) == []


def test_a_real_tool_run_is_not_called_unlooked(brain):
    said = []
    p = speech.Polisher(said.append, asked="Will it rain in Norfolk tomorrow?", retry=True, unknown=intelligence.claims_lookup)
    p("Give me a second, I'm checking the web now.")
    assert p.lookup
    p.release(None)                                  # the Brain passes no substitute when a tool really ran
    assert said == ["Give me a second, I'm checking the web now."]


# --- review round 3 (2026-11-15) ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, query", [
    ("The gate code is 4421. Who won the Lakers game last night?", "Who won the Lakers game last night"),
    ("Who won the Lakers game last night? We are at 12 Elm Street.", "Who won the Lakers game last night"),
    ("She is sick today. Who is the President of France?", "Who is the President of France"),
    ("Do you know who won the Lakers game last night?", "who won the Lakers game last night"),
    ("Can you tell me who the President of France is?", "who the President of France is"),
    ("Do you happen to know the weather in London?", "the weather in London"),
    ("Could you find out who won the Super Bowl?", "who won the Super Bowl"),
    ("I was wondering who the President of France is.", "who the President of France is"),
    ("how tall is mount everest", "how tall is mount everest"),
    ("who is elon musk", "who is elon musk"),
])
def test_the_lookup_sends_only_the_worldly_question(said, query):
    assert webintent.lookup_query(said) == query


@pytest.mark.parametrize("said", ["Who is Mom's favorite actor?", "What's the code for the gate?", "How much RAM is free?",
                                  "Is Dad home?", "Do you have any pets?", "Who made you?"])
def test_no_lookup_for_the_house_the_machine_or_him(said):
    assert webintent.lookup_query(said) == ""


def test_a_private_sentence_never_rides_along_in_the_search(brain):
    brain.state.replies = ["I don't have current information on that.", "The Lakers won, per ESPN."]
    brain.ask("The gate code is 4421. Do you know who won the Lakers game last night?", [].append)
    assert brain.state.searches[0][1]["query"] == "who won the Lakers game November 14, 2026"


@pytest.mark.parametrize("said", ["Any news on Mom's surgery", "Any news on his health", "what's the news about Dad",
                                  "Do your research on whether I should take out a loan", "Any news on the PR build status",
                                  "any news on the jarvis service?"])
def test_family_and_personal_asks_are_never_searched(said):
    assert webintent.kind(said) is None


@pytest.mark.parametrize("said", ["Any news on the doctor strike", "Any news on password breaches",
                                  "What's the news on build 2026 conference"])
def test_public_topics_with_sensitive_sounding_words_still_work(said):
    assert webintent.kind(said) == "news"


@pytest.mark.parametrize("previous", ["We're buying a house on Elm Street", "Mom is having surgery tomorrow",
                                      "He has been coughing for a week now", "The gate code is 4421", "It's cold in here",
                                      "Thanks Jarvis, that's all", "Good morning Jarvis"])
def test_more_private_previous_lines(previous):
    assert webintent.news_query("What's the news about that?", previous) == "top news headlines today"


@pytest.mark.parametrize("sentence", ["I've checked and the Lakers won 110-102.", "I looked it up and the Lakers won.",
                                      "I double-checked, and the Lakers won.", "I've verified it.", "I checked the sources and the Lakers won.",
                                      "I've checked the forecast and it's sunny."])
def test_more_faked_checks_are_recognised(sentence):
    assert intelligence.claims_lookup(sentence)


# --- live on the laptop (2026-11-15 01:05): "I don't have the result from that search yet, sir. Give me a moment to pull it
# up" was said as written: the first sentence was not recognised as an admission, so nothing was held. ---------------------
@pytest.mark.parametrize("sentence", ["I don't have the result from that search yet, sir.", "I haven't got the score yet.",
                                      "I don't have the latest on that.", "I haven't found any reports on it."])
def test_a_soft_admission_of_not_having_the_result_is_recognised(sentence):
    assert intelligence.admits_unknown(sentence)


def test_the_laptop_draft_is_held_whole_and_looked_up(brain):
    brain.state.replies = ["I don't have the result from that search yet, sir. Give me a moment to pull it up and tell you who won.",
                           "The Lakers beat the Celtics 110 to 104, per ESPN."]
    said = []
    brain.ask("Do you know who won the Lakers game last night?", said.append)
    assert " ".join(said) == "The Lakers beat the Celtics 110 to 104, per ESPN."
    assert len(_internet(brain)) == 1


# --- live on the PC and laptop (2026-11-15): drafts with words between "have" and "result", and an OFFER to look it up -------------------------
@pytest.mark.parametrize("sentence", ["I don\u2019t have the Lakers result handy, sir.", "I don't have the Lakers result right now, sir.",
                                      "I don't have the final score.", "I don't have the game's result yet.", "I don't have those numbers.",
                                      "I don't have it handy.", "I don't have that on hand.", "I can't pull that up.", "I haven't got the Lakers score yet."])
def test_a_soft_admission_with_words_in_between_is_recognised(sentence):
    assert intelligence.admits_unknown(sentence)


@pytest.mark.parametrize("sentence", ["Want me to pull it up?", "Want me to pull it up now?", "Shall I look it up?", "Would you like me to look that up?",
                                      "Do you want me to search for it?"])
def test_an_offer_to_look_it_up_is_recognised(sentence):
    assert intelligence.offers_lookup(sentence)


@pytest.mark.parametrize("sentence", ["I have the Lakers result: they won.", "Want me to open Notepad?", "The Lakers won 110 to 104."])
def test_ordinary_sentences_are_not_soft_admissions_or_offers(sentence):
    assert not intelligence.admits_unknown(sentence) and not intelligence.offers_lookup(sentence)


@pytest.mark.parametrize("draft", [["I don\u2019t have the Lakers result handy, sir.", "Want me to pull it up now?"],
                                   ["I don't have the Lakers result right now, sir.", "Want me to pull it up?"],
                                   ["Want me to pull it up?"]])
def test_the_live_drafts_are_held_and_the_search_is_just_done(brain, draft):
    brain.state.replies = [" ".join(draft), "The Lakers beat the Celtics 110 to 104, per ESPN."]
    said = []
    brain.ask("Do you know who won the Lakers game last night?", said.append)
    assert " ".join(said) == "The Lakers beat the Celtics 110 to 104, per ESPN."
    assert len(_internet(brain)) == 1
    assert not any("pull it up" in _text(m) for m in brain.messages), "the offer left the history"
