"""Trustworthy answers from the internet (2026-11-15): query rewriting, source ranking, the cross-check (agree / one source /
conflicting reports / out of date / nothing reliable), figures taken verbatim, follow-ups on the last search, and the Brain
wiring. Search results are stubbed (no network, no model); core/webanswer.py is the code under test."""
import re
from datetime import date
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from core import intelligence, webanswer, webintent, websearch

TODAY = date(2026, 11, 15)


@pytest.fixture(autouse=True)
def _pinned_day(monkeypatch):
    monkeypatch.setattr(webanswer, "today", lambda: TODAY)


def row(site, title, snippet, when=None, path="/a", listed=None):
    return {"title": title, "url": f"https://www.{site}{path}", "site": site, "date": when, "listed": listed, "snippet": snippet}


def pack(question, *rows, provider="exa"):
    return {"question": question, "query": question, "provider": provider, "results": list(rows), "failures": [],
            "searched_at": "2026-11-15T12:00:00+00:00", "cached": False}


# --- the question as a search query ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, query", [
    ("um so who won the Lakers game last night please", "who won the Lakers game November 14, 2026"),
    ("What's the weather in Springfield tomorrow", "What's the weather in Springfield November 16, 2026"),
    ("what's the weather in Springfield", "what's the weather in Springfield November 15, 2026"),
    ("what is the latest Blender version", "what is the latest Blender version 2026"),
    ("who is the president of France", "who is the president of France 2026"),
    ("what's the price of bitcoin right now", "what's the price of bitcoin November 15, 2026"),
    ("what are the headlines this week", "what are the headlines this week November 2026"),
    ("who won the first Super Bowl", "who won the first Super Bowl"),               # history: no year added
    ("who won the 2022 world cup", "who won the 2022 world cup"),                    # he named a year
    ("who invented the telephone", "who invented the telephone"),
    ("how tall is Mount Everest", "how tall is Mount Everest"),
    ("hey jarvis, what's the score of the Lakers game tonight, thanks", "what's the score of the Lakers game November 15, 2026"),
    ("and is the DMV open the day after tomorrow", "is the DMV open November 17, 2026"),
])
def test_the_question_becomes_a_dated_search_query(said, query):
    assert webanswer.rewrite(said) == query


def test_a_compound_question_is_two_searches_and_none_is_dropped():
    assert webanswer.split_questions("What's the weather in Springfield and who won the Lakers game last night") == [
        "What's the weather in Springfield", "who won the Lakers game last night"]
    assert webanswer.split_questions("Who is the CEO of Nvidia? And what is the capital of Spain?") == ["Who is the CEO of Nvidia", "what is the capital of Spain"]
    assert len(webanswer.split_questions("Who is the CEO of Nvidia? And what is the stock price?")) == 1          # the second half has no subject of its own
    assert webanswer.split_questions("tell me about salt and pepper") == ["tell me about salt and pepper"]      # not a second question
    calls = webintent.calls_for("search", "google the weather in Springfield and who won the Lakers game last night")
    assert [c[1].get("query") or c[1].get("question") for c in calls] == ["weather in Springfield November 15, 2026",
                                                                          "who won the Lakers game November 14, 2026"]


def test_the_base_topic_comes_back_out_of_a_dated_query():
    assert webanswer.base_of("weather in Springfield November 15, 2026") == "weather in Springfield"
    assert webanswer.base_of("what is the latest Blender version 2026") == "what is the latest Blender version"


# --- sources: primary and recent first, forums last, nothing dropped -----------------------------------------------------------------
def test_the_makers_own_page_and_fresh_results_come_first_and_forums_last():
    rows = [row("reddit.com", "r/nvidia thread", "anyone know the driver version", path="/r/nvidia/x"),
            row("somesite.net", "Driver roundup", "Version 620.55 is out", when="2025-01-02"),
            row("nvidia.com", "GeForce driver 621.01", "Version 621.01 released", when="2026-11-10"),
            row("example.org", "Drivers", "Version 621.01", when=None)]
    ranked = webanswer.rank(rows, "latest NVIDIA driver version 2026")
    assert ranked[0]["site"] == "nvidia.com" and ranked[-1]["site"] in ("reddit.com", "somesite.net") and len(ranked) == 4


def test_a_government_page_beats_a_content_farm_for_a_closing_question():
    rows = [row("answers.com", "DMV hours", "open maybe"), row("dmv.virginia.gov", "Springfield customer service center", "Open Monday-Friday 8:30 a.m.")]
    assert webanswer.rank(rows, "is the DMV open tomorrow")[0]["site"] == "dmv.virginia.gov"


def test_a_date_in_a_news_url_counts_as_a_date_and_the_site_ignores_subdomains():
    r = row("apnews.com", "Storm", "x", path="/article/2026/11/13/storm")
    assert webanswer.row_date(r) == ("2026-11-13", "url")
    assert webanswer.site_of("https://m.bbc.co.uk/news/x") == "bbc.co.uk" and webanswer.site_of("https://scores.espn.com/y") == "espn.com"


# --- the cross-check -------------------------------------------------------------------------------------------------------------------
Q_GAME = "Who won the Lakers game November 14, 2026"
GAME_A = row("espn.com", "Lakers vs Celtics", "Final: Lakers 110-104 Celtics. LeBron scored 31.", when="2026-11-14")
GAME_B = row("cbssports.com", "Lakers beat Celtics", "The Lakers beat the Celtics 110-104 on Saturday night.", when="2026-11-14")
GAME_C = row("nba.com", "Lakers recap", "Lakers fall to the Celtics 108-104 in Boston.", when="2026-11-14")


def test_two_independent_sites_with_the_same_score_agree():
    a = webanswer.assess(pack(Q_GAME, GAME_A, GAME_B), now=TODAY)
    assert a["status"] == "agree" and a["kind"] == "score" and set(a["agree_sites"]) == {"espn.com", "cbssports.com"} and a["agreed"] == "110-104"
    said = webanswer.answer(pack(Q_GAME, GAME_A, GAME_B), now=TODAY)
    assert "espn.com and cbssports.com agree on 110-104" in said and "yesterday" in said


def test_different_scores_are_conflicting_reports_never_a_pick():
    said = webanswer.answer(pack(Q_GAME, GAME_A, GAME_C), now=TODAY)
    assert "conflicting reports" in said and "espn.com says 110-104" in said and "nba.com says 108-104" in said and "can't tell you which is right" in said


def test_one_site_is_one_source_even_with_two_results():
    a = webanswer.assess(pack(Q_GAME, GAME_A, row("espn.com", "Box score", "Lakers 110-104 final", when="2026-11-14", path="/box")), now=TODAY)
    assert a["status"] == "single" and a["sites"] == ["espn.com"]
    assert "unconfirmed" in webanswer.verdict(pack(Q_GAME, GAME_A), now=TODAY)


def test_only_one_site_printing_the_figure_is_a_single_source_even_with_several_results():
    other = row("sportsnet.ca", "Lakers preview", "The Lakers host the Celtics in a Saturday matchup.", when="2026-11-14")
    a = webanswer.assess(pack(Q_GAME, GAME_A, other), now=TODAY)
    assert a["status"] == "single" and a["kind"] == "score" and "Only espn.com gives that figure" in webanswer.verdict(pack(Q_GAME, GAME_A, other), now=TODAY)


def test_old_results_for_a_question_about_last_night_are_out_of_date():
    old = [row("espn.com", "Lakers vs Celtics", "Final: Lakers 98-91 Celtics.", when="2026-10-02")]
    said = webanswer.answer(pack(Q_GAME, *old), now=TODAY)
    assert "old" in said and "October 2" in said and "98-91" not in said        # stale figures are never quoted as the answer


def test_a_search_that_found_nothing_says_so_and_does_not_guess():
    said = webanswer.answer(pack("who won the Lakers game", ), now=TODAY)
    assert "found nothing reliable" in said and "won't guess" in said


def test_version_agreement_and_conflict():
    q = "latest NVIDIA driver version 2026"
    a = row("nvidia.com", "GeForce Game Ready Driver 621.01", "Version 621.01 WHQL released for GeForce RTX 50 series.", when="2026-11-10")
    b = row("techpowerup.com", "NVIDIA GeForce Driver 621.01", "NVIDIA GeForce Game Ready driver version 621.01 download.", when="2026-11-11")
    c = row("neowin.net", "Nvidia driver 620.80", "Nvidia GeForce driver version 620.80 now available.", when="2026-11-09")
    assert webanswer.assess(pack(q, a, b), now=TODAY)["status"] == "agree"
    assert webanswer.assess(pack(q, a, b, c), now=TODAY)["agreed"] == "621.01"      # a third differing site does not undo two that agree
    assert webanswer.assess(pack(q, a, c), now=TODAY)["status"] == "conflict"


def test_prices_that_differ_are_reported_as_conflicting():
    q = "price of bitcoin November 15, 2026"
    a = row("coindesk.com", "Bitcoin price", "Bitcoin is trading at $98,250 on Saturday.", when="2026-11-15")
    b = row("coinmarketcap.com", "BTC", "The live Bitcoin price is $90,400 today.", when="2026-11-15")
    assert "conflicting" in webanswer.verdict(pack(q, a, b), now=TODAY)


def test_a_date_question_compares_dates():
    q = "when is Thanksgiving 2026"
    a = row("timeanddate.com", "Thanksgiving Day 2026", "Thanksgiving Day is on November 26, 2026.")
    b = row("almanac.com", "Thanksgiving 2026", "In 2026, Thanksgiving falls on Nov 26.")
    v = webanswer.assess(pack(q, a, b), now=TODAY)
    assert v["status"] == "agree" and v["kind"] == "date"


def test_a_who_is_question_has_nothing_numeric_to_cross_check_and_says_so():
    q = "who is the president of France 2026"
    a = row("wikipedia.org", "President of France", "The president of France is the head of state of France.")
    b = row("elysee.fr", "Le president", "Le president de la Republique francaise.")
    v = webanswer.verdict(pack(q, a, b), now=TODAY)
    assert "2 sources" in v and "not a confirmed fact" in v


def test_news_with_no_stated_date_says_it_cannot_tell_how_recent():
    q = "top news headlines November 15, 2026"
    a = row("apnews.com", "Top stories", "Storm warnings issued along the coast.")
    b = row("reuters.com", "World news", "Markets steady ahead of the vote.")
    note = webanswer.evidence_note(pack(q, a, b), now=TODAY)
    assert "No result states a date" in note and "Verdict:" in note and "2026-11-15" in note


def test_weather_two_sites_agreeing_on_a_temperature_is_corroborated():
    q = "weather in Springfield November 15, 2026"
    a = row("weather.gov", "Springfield IL", "Today: sunny, with a high near 62°. Tonight: low around 45°.", when="2026-11-15")
    b = row("accuweather.com", "Springfield weather", "High of 62° and sunny this afternoon.", when="2026-11-15")
    assert webanswer.assess(pack(q, a, b), now=TODAY)["status"] == "agree"


# --- numbers are the page's own words ------------------------------------------------------------------------------------------------
def test_every_number_in_a_spoken_answer_is_one_a_result_printed():
    p = pack(Q_GAME, GAME_A, GAME_B)
    said = webanswer.answer(p, now=TODAY)
    printed = " ".join(r["snippet"] + r["title"] for r in p["results"])
    for n in re.findall(r"\d+", said):
        assert n in printed or n in ("14",), (n, said)           # (14: the day, from the page's own date)
    assert "110-104" in said and "31" not in said or "LeBron scored 31" in said


def test_the_quoted_sentence_is_the_one_that_answers_not_the_first():
    r = row("espn.com", "Lakers", "Welcome to ESPN, the worldwide leader in sports coverage. Final: Lakers 110-104 Celtics. Subscribe for more.", when="2026-11-14")
    assert webanswer.best_sentence(r["snippet"], Q_GAME) == "Final: Lakers 110-104 Celtics"


def test_the_spoken_fallback_of_a_search_that_read_no_page_still_quotes_and_cross_checks():
    said = websearch.spoken(pack(Q_GAME, GAME_A, GAME_C))
    assert "espn.com says: Final: Lakers 110-104 Celtics" in said and "conflicting reports" in said


def test_the_evidence_the_model_reads_carries_the_verdict_and_the_rule_to_copy_figures():
    text = websearch.evidence_text(pack(Q_GAME, GAME_A, GAME_C))
    assert "Verdict: I found conflicting reports" in text and "Copy every number and date exactly" in text and "Answer from these results only" in text


# --- follow-ups on the last search ----------------------------------------------------------------------------------------------
LAST = pack("Who won the Lakers game November 14, 2026", GAME_A, GAME_B, GAME_C)


@pytest.mark.parametrize("said, index", [("Tell me more about the second one", 1), ("open the third link", 2), ("What does the first result say?", 0),
                                         ("read the last one", 2), ("tell me more about the ESPN one", 0), ("go into number two", 1),
                                         ("can you read the cbssports article", 1)])
def test_an_ordinal_or_a_site_name_opens_that_result(said, index):
    got = webanswer.followup(said, LAST, TODAY)
    assert got == {"skill": "open_web_page", "args": {"url": LAST["results"][index]["url"]}}, got


def test_a_result_that_does_not_exist_is_said_not_invented():
    assert "only found 3 results" in webanswer.followup("tell me more about the fifth one", LAST, TODAY)["say"]


@pytest.mark.parametrize("said, query", [
    ("What about tomorrow?", "Who won the Lakers game November 16, 2026"),
    ("and yesterday?", "Who won the Lakers game November 14, 2026"),
    ("how about Friday", "Who won the Lakers game November 13, 2026"),               # a question in the past tense: the Friday just gone
])
def test_a_new_day_reuses_the_last_topic(said, query):
    got = webanswer.followup(said, LAST, TODAY)
    assert got["skill"] == "search_internet" and got["args"]["query"] == query, got


def test_a_new_place_reuses_the_last_topic():
    weather = pack("weather in Springfield November 15, 2026", row("weather.gov", "Springfield", "Sunny, high near 62°", when="2026-11-15"))
    got = webanswer.followup("And in Norfolk?", weather, TODAY)
    assert got["args"]["query"] == "weather in Norfolk November 15, 2026"
    assert webanswer.followup("what about Norfolk", weather, TODAY)["args"]["query"] == "weather in Norfolk November 15, 2026"
    assert webanswer.followup("and in Maya?", weather, TODAY, veto=lambda name: True) is None      # a household name is never searched


def test_tell_me_more_reads_the_pages_on_the_same_topic_and_day():
    assert webanswer.followup("tell me more", LAST, TODAY) == {"skill": "research_online", "args": {"question": LAST["question"], "pages": 4}}


def test_where_did_you_get_that_names_the_source_and_its_date_and_claims_nothing_else():
    said = webanswer.followup("Where did you get that?", LAST, TODAY)["say"]
    assert "espn.com" in said and "yesterday" in said and LAST["results"][0]["url"] in said and "haven't checked anything beyond" in said


@pytest.mark.parametrize("said", ["What about my mom?", "That sounds good", "Open the second tab", "Play the first song", "What time is it",
                                  "What about tomorrow's meeting with Dad at noon today, since I'll be at work?",
                                  "How are you doing today my friend?"])
def test_other_lines_are_not_follow_ups(said):
    assert webanswer.followup(said, LAST, TODAY) is None


def test_no_search_no_follow_up():
    assert webanswer.followup("tell me more about the second one", None, TODAY) is None
    assert webanswer.followup("what about tomorrow", {"results": []}, TODAY) is None


# --- the Brain: the whole path with stubbed search and model ---------------------------------------------------------------------------
@pytest.fixture
def brain(monkeypatch):
    from brain import llm
    from core import pcfast, quick
    from tools import registry
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(llm.Brain, "_language_request", lambda self, text, emit: False)
    state = SimpleNamespace(replies=[], calls=[], seen=[])

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        state.seen.append([m for m in history])
        text = state.replies.pop(0) if state.replies else "Okay."
        if on_text:
            on_text(text)
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")

    def run(name, args):
        state.calls.append((name, dict(args)))
        if name in ("search_internet", "research_online"):
            p = pack(args.get("query") or args.get("question"), GAME_A, GAME_B, GAME_C)
            websearch.remember("owner", "search", p)
            return websearch.evidence_text(p), False
        if name == "open_web_page":
            return f"Lakers recap — espn.com (published 2026-11-14; read via direct)\nURL: {args['url']}\nThe Lakers won 110-104.", False
        return "ok", False

    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(registry, "run", run)
    monkeypatch.setattr(websearch, "LAST", {})
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.state = state
    return b


def test_after_a_search_the_second_one_opens_that_result(brain):
    brain.state.replies = ["The Lakers won 110-104, per ESPN yesterday.", "ESPN's recap says the Lakers won 110-104."]
    brain.ask("Google who won the Lakers game last night", [].append)
    assert brain.state.calls[0][0] in ("search_internet", "research_online")
    said = []
    brain.ask("Tell me more about the second one", said.append)
    assert brain.state.calls[-1] == ("open_web_page", {"url": GAME_B["url"]}) and said == ["ESPN's recap says the Lakers won 110-104."]


def test_after_a_search_a_new_day_searches_the_same_topic_again(brain):
    brain.state.replies = ["The Lakers won, per ESPN.", "Tomorrow's game is not played yet."]
    brain.ask("Google who won the Lakers game last night", [].append)
    brain.ask("What about tomorrow?", [].append)
    name, args = brain.state.calls[-1]
    assert name == "search_internet" and "November 16, 2026" in args["query"] and "Lakers" in args["query"]


def test_where_did_you_get_that_is_answered_from_the_search_without_a_model(brain):
    brain.state.replies = ["The Lakers won, per ESPN."]
    brain.ask("Google who won the Lakers game last night", [].append)
    n = len(brain.state.seen)
    said = []
    brain.ask("Where did you get that?", said.append)
    assert len(brain.state.seen) == n and "espn.com" in " ".join(said) and "haven't checked anything beyond" in " ".join(said)


def test_a_follow_up_phrase_after_something_else_is_not_a_follow_up(brain):
    brain.state.replies = ["The Lakers won, per ESPN.", "It is three o'clock.", "Sorry, I don't follow."]
    brain.ask("Google who won the Lakers game last night", [].append)
    brain.ask("What time is it?", [].append)
    before = len(brain.state.calls)
    said = []
    brain.ask("What about tomorrow?", said.append)
    assert len(brain.state.calls) == before, "an unrelated turn came between: no searching"


def test_two_questions_in_one_line_are_both_searched(brain):
    brain.state.replies = ["Sunny in Springfield, and the Lakers won, per ESPN."]
    brain.ask("Google the weather in Springfield and who won the Lakers game last night", [].append)
    queries = [a.get("query") or a.get("question") for n, a in brain.state.calls if n in ("search_internet", "research_online")]
    assert queries == ["weather in Springfield November 15, 2026", "who won the Lakers game November 14, 2026"]


def test_a_failed_search_is_an_honest_line_never_a_made_up_answer(brain, monkeypatch):
    from tools import registry
    monkeypatch.setattr(registry, "run", lambda name, args: (f"{websearch.MARK_OFFLINE}: bing-rss (no connection). I did not search, so I can't "
                                                                "give you live results.", False))
    brain.state.replies = ["The Lakers won by six."]
    said = []
    brain.ask("Google who won the Lakers game last night", said.append)
    assert said and said[0].startswith(websearch.MARK_OFFLINE) and "won by six" not in " ".join(said)


# --- found by replaying his real lines (scripts/web_replay.py) -----------------------------------------------------------------
@pytest.mark.parametrize("said", ["Open Google.", "i need you to go on google", "Look up my screen and describe exactly what you see.",
                                  "Launch Bing"])
def test_opening_a_search_engine_or_looking_at_his_screen_is_not_a_web_search(said):
    assert webintent.kind(said) is None


@pytest.mark.parametrize("said, topic", [
    ("Open YouTube and look for Blender tutorials.", "Blender tutorials"),
    ("Open YouTube, look for Cocomelon.", "Cocomelon"),
    ("Jarvis, I need you to search on YouTube Blender Tutorials.", "Blender Tutorials"),
    ("Look up Cocomelon on YouTube. I have it open.", "Cocomelon"),
])
def test_youtube_asks_keep_only_the_topic(said, topic):
    assert webintent.kind(said) == "youtube"
    assert webintent.call_for("youtube", said)[1]["topic"] == topic


def test_a_browser_prefix_is_not_part_of_the_search():
    assert webintent.call_for("search", "On Microsoft Edge, search for WhatsApp Web.") == ("search_internet", {"query": "WhatsApp Web"})


def test_a_search_with_nothing_to_search_for_is_asked_not_run(brain):
    assert webintent.kind("go on the web and search for, um,") == "search"
    assert webintent.calls_for("search", "go on the web and search for, um,") == []
    said = []
    brain.ask("go on the web and search for, um,", said.append)
    assert said == ["What would you like me to look up?"] and brain.state.calls == []


def test_a_statement_in_the_same_line_is_never_searched_as_a_question():
    assert webintent.lookup_queries("Yeah, that's the problem, Nigel. I don't know why you can't pull up the workout plan. That's something we need to fix, right?") == []
    assert webintent.lookup_queries("And Unreal Engine. Can you make this character do some animation?") == []
    assert webintent.lookup_queries("What's the weather in Springfield? And who won the Lakers game last night?") == [
        "What's the weather in Springfield", "who won the Lakers game last night"]


def test_a_new_question_that_contains_a_date_word_is_not_a_follow_up():
    assert webanswer.followup("What's the weather today?", LAST, TODAY) is None
    assert webanswer.followup("What time is it tomorrow in Tokyo?", LAST, TODAY) is None


def test_providers_that_answered_with_nothing_relevant_are_not_called_offline():
    err = websearch.SearchUnavailable([("exa", "not installed on this machine"), ("bing-rss", "no usable results"), ("duckduckgo", "no usable results")])
    text = websearch.unavailable_text("who won the Lakers game last night", err)
    assert websearch.unavailable(text) and text.startswith(websearch.MARK_NOTHING) and "bing-rss, duckduckgo" in text
    assert "did not search" not in text and "won't guess" in text and "could not reach exa" in text
    offline = websearch.unavailable_text("anything", websearch.SearchUnavailable([("exa", "no connection")]))
    assert "did not search" in offline and not offline.startswith(websearch.MARK_NOTHING)


def test_a_lone_page_from_the_maker_itself_is_called_the_primary_source_not_unconfirmed():
    q = "latest NVIDIA driver version 2026"
    only = row("nvidia.com", "GeForce Game Ready Driver 621.01", "Version 621.01 WHQL released.", when="2026-11-10")
    v = webanswer.verdict(pack(q, only), now=TODAY)
    assert "nvidia.com itself, the primary source" in v and "no second source" in v and "unconfirmed" not in v
    assert "must carry that caveat" in webanswer.evidence_note(pack(q, only), now=TODAY).replace("MUST", "must")
    assert webanswer.is_primary(row("dmv.virginia.gov", "x", "y"), "dmv hours") and not webanswer.is_primary(row("answers.com", "x", "y"), "nvidia driver")


# =====================================================================================================================
# Review round 1 (2026-11-15): the reviewer's own inputs, one test each
# =====================================================================================================================
def R(url, **kw):
    d = {"url": url, "title": kw.pop("title", "t"), "snippet": kw.pop("snippet", ""), "site": None}
    d.update(kw)
    return d


def P(question, *rows, **kw):
    return {"question": question, "query": question, "provider": "exa", "results": list(rows), "failures": [], "searched_at": "2026-11-15T12:00:00+00:00",
            "cached": False, **kw}


# --- 1. dates from providers are parsed, and a bad row never throws -----------------------------------------------------------------
@pytest.mark.parametrize("raw, iso", [("Nov 12, 2026", "2026-11-12"), ("November 12, 2026", "2026-11-12"), ("12 Nov 2026", "2026-11-12"),
                                      ("2 hours ago", "2026-11-15"), ("3 days ago", "2026-11-12"), ("Yesterday", "2026-11-14"), ("today", "2026-11-15"),
                                      ("1 week ago", "2026-11-08"), ("12/25/2026", "2026-12-25"), ("25/12/2026", "2026-12-25"), ("Nov 12", "2026-11-12"),
                                      ("Thu, 12 Nov 2026 10:00:00 GMT", "2026-11-12"), ("20261112", "2026-11-12"), ("2026-11-12T10:00:00Z", "2026-11-12"),
                                      ("12/11/2026", None), ("garbage-xx", None), ("2026-13-45", None), ("N/A", None), ("", None), (None, None)])
def test_a_providers_date_in_any_common_form_becomes_iso_or_nothing(raw, iso):
    assert websearch._date(raw) == iso


@pytest.mark.parametrize("rows", [
    [R("https://a.com/x", date="Nov 12, 2026", snippet="60 degrees")],
    [R("https://a.com/x", date="garbage-xx", snippet="60 degrees")],
    [R("https://a.com/x", date=20261114, snippet="60 degrees")],
    [R("https://a.com/x", date="2027-01-01", snippet="60 degrees today")],
    [R("https://a.com/x", date="2 hours ago", listed="Yesterday", snippet="60 degrees")],
    [None], None, [{"url": None, "title": None, "snippet": None}], [{"title": "x"}],
    [R("https://a.com/x", facts="60 degrees", snippet="x")], [R("https://a.com/x", snippet=None, title=None)], [], [1, 2]])
def test_no_malformed_row_or_date_can_make_the_answer_throw(rows):
    p = {"question": "weather in Springfield today", "query": "weather in Springfield today", "provider": "exa", "results": rows, "failures": [],
         "searched_at": "2026-11-15T12:00:00+00:00"}
    for fn in (lambda: webanswer.assess(p, now=TODAY), lambda: webanswer.verdict(p, now=TODAY), lambda: webanswer.answer(p, now=TODAY),
               lambda: webanswer.evidence_note(p, now=TODAY), lambda: websearch.evidence_text(p), lambda: websearch.spoken(p),
               lambda: webanswer.rank(rows, "weather today", TODAY), lambda: webanswer.followup("where did you get that", p, TODAY),
               lambda: webanswer.followup("tell me more about the second one", p, TODAY)):
        fn()
    assert webanswer.assess([1, 2], now=TODAY)["status"] in ("none", "error") and webanswer.assess(None, now=TODAY)["status"] in ("none", "error")


def test_a_raw_provider_date_counts_as_a_date_and_a_future_one_does_not():
    r = R("https://a.com/x", date="Nov 12, 2026", snippet="60 degrees today")
    assert webanswer.row_date(r) == ("2026-11-12", "published")
    assert webanswer.row_date(R("https://a.com/x", date="2027-06-01")) == (None, "none")
    p = P("weather in Springfield today", r)
    websearch.evidence_text(p)
    assert webanswer.assess(p, now=TODAY)["newest"] == "2026-11-12"


# --- 2. staleness and dates only for questions about NOW -----------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["how many hours of sleep do adults need", "how do I open a PDF in Chrome", "what happened on November 9, 1989",
                               "who won the Super Bowl on February 7, 2021", "what is the price of a Tesla Model 3", "what are the clinic hours",
                               "who won the Civil War", "who won the Battle of Hastings", "how do I stop snow from sticking to my shovel",
                               "what causes rain", "what is a good result for a blood test", "who beat Napoleon at Waterloo"])
def test_timeless_and_historical_questions_get_no_date_no_year_and_no_staleness(q):
    assert webanswer.rewrite(q, TODAY) == q
    assert webanswer.window_days(q, TODAY) is None


def test_old_good_sources_are_not_stale_for_a_timeless_question():
    old = [R("https://cdc.gov/a", date="2024-03-01", snippet="Adults need 7 or more hours of sleep per night."),
           R("https://sleepfoundation.org/a", date="2024-01-01", snippet="Adults need 7-9 hours of sleep.")]
    p = P("how many hours of sleep do adults need", *old)
    assert webanswer.assess(p, now=TODAY)["status"] != "stale" and "old" not in webanswer.verdict(p, now=TODAY)
    assert [r["url"] for r in webanswer.rank(old[::-1], "how many hours of sleep do adults need", TODAY)] == [r["url"] for r in old]
    war = [R("https://en.wikipedia.org/wiki/x", date="2023-05-01", snippet="The Berlin Wall fell on November 9, 1989.")]
    assert webanswer.assess(P("what happened on November 9, 1989", *war), now=TODAY)["status"] != "stale"


@pytest.mark.parametrize("q", ["what's the weather in Springfield", "who won the Lakers game November 14, 2026", "what's the price of bitcoin",
                               "is the DMV open tomorrow", "what's the score of the Lakers game tonight"])
def test_questions_about_now_still_have_a_short_window(q):
    assert webanswer.window_days(q, TODAY) == 4


def test_current_roles_and_latest_things_still_get_the_year():
    assert webanswer.rewrite("who is the CEO of Apple", TODAY) == "who is the CEO of Apple 2026"
    assert webanswer.rewrite("who won the Super Bowl", TODAY) == "who won the Super Bowl 2026"
    assert webanswer.rewrite("what is the latest iPhone", TODAY) == "what is the latest iPhone 2026"


# --- 3. agreement is on the figure that answers the question, and copies are one source ------------------------------------------------
def test_a_shared_change_figure_is_not_agreement_on_the_price():
    p = P("price of bitcoin today",
          R("https://coindesk.com/a", snippet="Bitcoin is trading at $65,200 today. It is up $500 on the day.", date="2026-11-15"),
          R("https://cnbc.com/a", snippet="Bitcoin price is $58,100 today, up $500 on the day.", date="2026-11-15"))
    a = webanswer.assess(p, now=TODAY)
    assert a["status"] == "conflict" and "conflicting reports" in webanswer.verdict(p, now=TODAY) and "$65,200" in webanswer.verdict(p, now=TODAY)


def test_a_team_record_is_not_the_score():
    p = P("who won the Lakers game last night",
          R("https://espn.com/a", snippet="The Lakers won 112-104 last night.", date="2026-11-14"),
          R("https://nba.com/a", snippet="Lakers beat the Suns 118-110 last night, their 5-2 start.", date="2026-11-14"),
          R("https://cbs.com/a", snippet="Lakers 5-2 after win last night.", date="2026-11-14"))
    a = webanswer.assess(p, now=TODAY)
    assert a["status"] == "conflict" and {fig for _, fig in a["conflict"]} == {"112-104", "118-110"}


def test_three_copies_of_one_wire_story_are_one_source():
    p = P("who won the Lakers game last night",
          R("https://apnews.com/a", snippet="Lakers beat Suns 112-104 on Friday.", date="2026-11-14"),
          R("https://www.yahoo.com/news/a", snippet="Lakers beat Suns 112-104 on Friday (AP).", date="2026-11-14"),
          R("https://www.usatoday.com/a", snippet="Lakers beat Suns 112-104 on Friday (AP).", date="2026-11-14"))
    a = webanswer.assess(p, now=TODAY)
    assert a["status"] == "single" and len(a["sites"]) == 1
    assert "agree" not in webanswer.verdict(p, now=TODAY) and "1 site" in webanswer.evidence_note(p, now=TODAY)


def test_two_independent_outlets_with_their_own_words_still_agree():
    assert webanswer.assess(P("who won the Lakers game last night", GAME_A, GAME_B), now=TODAY)["status"] == "agree"


# --- 4. abbreviations never cut a query -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, topic", [
    ("Search for the Lakers vs. Suns score", "Lakers vs. Suns score"),
    ("Search for the best pizza in St. Louis", "best pizza in St. Louis"),
    ("Search for Apple Inc. stock price", "Apple Inc. stock price"),
    ("Look up Tesla vs. Ford sales figures", "Tesla vs. Ford sales figures"),
    ("Search the web for U.S. history", "U.S. history"),
    ("Search for Dr. Smith's clinic hours", "Dr. Smith's clinic hours"),
    ("Search for pizza near me. I'm hungry.", "pizza near me"),
])
def test_an_abbreviation_does_not_end_the_search_topic(said, topic):
    assert webintent.kind(said) == "search"
    assert webintent.query_of(said) == topic


# --- 5. no question is dropped silently -------------------------------------------------------------------------------------------------
def test_the_first_questions_are_kept_in_order_and_the_rest_is_named_as_not_searched():
    said = "Search for pizza and what is the weather in Norfolk and who won the Lakers game last night"
    calls = webintent.calls_for("search", said)
    assert [c[1].get("query") or c[1].get("question") for c in calls] == ["pizza", "what is the weather in Norfolk November 15, 2026"]
    note = webintent.skipped_note("search", said)
    assert "who won the Lakers game last night" in note and "not searched" in note.lower() and "pizza" not in note


def test_a_second_sentence_that_asks_something_is_searched_too():
    said = "Search the web for who won the lakers game. Also what is the weather in Springfield."
    qs = [c[1].get("query") or c[1].get("question") for c in webintent.calls_for("search", said)]
    assert qs == ["who won the lakers game 2026", "what is the weather in Springfield November 15, 2026"]
    assert webintent.skipped_note("search", said) == ""
    assert len(webintent.calls_for("search", "Search for pizza near me. I'm hungry.")) == 1       # a statement is not a question


def test_lookup_keeps_the_first_two_questions_and_reports_the_third():
    said = "Who won the Lakers game? What is the weather in Springfield? Who is the CEO of Tesla?"
    assert webintent.lookup_queries(said) == ["Who won the Lakers game", "What is the weather in Springfield"]
    assert "Who is the CEO of Tesla" in webintent.lookup_skipped(said)
    assert webintent.lookup_queries("Who won the Lakers game last night, what is the weather in Springfield and who is the CEO of Tesla?") == [
        "Who won the Lakers game last night", "what is the weather in Springfield"]


def test_the_brain_tells_the_model_what_was_not_searched(brain):
    brain.state.replies = ["Pizza and weather done."]
    brain.ask("Search for pizza and what is the weather in Norfolk and who won the Lakers game last night", [].append)
    seen = " ".join(str(b.get("content")) for m in brain.state.seen[0] if isinstance(m["content"], list) for b in m["content"] if isinstance(b, dict))
    assert "who won the Lakers game last night" in seen and "NOT searched" in seen


# --- 6. a part with a pronoun keeps its referent: it is not split off -----------------------------------------------------------------------
@pytest.mark.parametrize("q", ["who is the CEO of Tesla and how old is he", "how much is a Tesla and how fast is it",
                               "what is the weather like and is it going to rain"])
def test_a_second_half_that_points_back_is_not_split_off(q):
    assert webanswer.split_questions(q) == [q]
    assert len(webintent.calls_for("search", "Search for " + q)) == 1


# --- 7. date stripping has proper boundaries ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q, base", [("iPhone 16 release date September 2024", "iPhone 16 release date"),
                                     ("iPhone 16 release date September 20, 2024", "iPhone 16 release date"),
                                     ("who won the 2022 world cup", "who won the 2022 world cup"),
                                     ("what happened on November 9, 1989", "what happened"),
                                     ("weather November 16, 2026 in Springfield", "weather in Springfield")])
def test_the_date_a_rewrite_added_comes_off_cleanly(q, base):
    assert webanswer.base_of(q) == base


def test_a_follow_up_after_a_period_phrase_replaces_the_period():
    q = webanswer.rewrite("what movies are coming out next month", TODAY)
    assert webanswer.base_of(q) == "what movies are coming out next month"
    got = webanswer.followup("what about tomorrow?", P(q), TODAY)
    assert "November 16, 2026" in got["args"]["query"] and "next month" not in got["args"]["query"] and "December" not in got["args"]["query"]
    assert webanswer.rewrite("what's the weather this month in Springfield", TODAY).endswith("Springfield November 2026")


# --- 8. a figure at the end of a sentence is still a figure ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind, text, shown", [("score", "The Lakers won 112-104.", "112-104"), ("score", "They won 112-104, sources say.", "112-104"),
                                               ("score", "Final score: 112 to 104.", "112-104"), ("score", "Lakers 112, Suns 104 final", "112-104"),
                                               ("version", "The latest is Python 3.14.1.", "3.14.1"), ("version", "Python 3.14.1 is out.", "3.14.1"),
                                               ("money", "Bitcoin costs $65,200.", "$65,200"), ("percent", "Inflation is 3.2%.", "3.2%"),
                                               ("temp", "It will be 60 degrees.", "60 degrees")])
def test_figures_at_the_end_of_a_sentence_or_before_a_comma(kind, text, shown):
    assert [s for _, s in webanswer.figures(kind, text)][:1] == [shown]


def test_a_date_inside_a_score_is_not_a_score():
    assert webanswer.figures("score", "Published 2026-11-10 at noon") == []


# --- 9. only the topic's own maker is the primary source -----------------------------------------------------------------------------------------
def test_weather_dot_com_is_not_the_primary_source_for_a_weather_question():
    q = "what is the weather in Springfield"
    assert not webanswer.is_primary(R("https://weather.com/a"), q) and webanswer.is_primary(R("https://forecast.weather.gov/a"), q)
    assert webanswer.is_primary(R("https://www.nvidia.com/a"), "latest NVIDIA driver") and not webanswer.is_primary(R("https://news.com/a"), "the news today")
    v = webanswer.verdict(P(q, R("https://weather.com/a", snippet="Springfield 60 degrees today.", date="2026-11-15")), now=TODAY)
    assert "primary source" not in v and "unconfirmed" in v


# --- 10. different things are not conflicting reports --------------------------------------------------------------------------------------------
def test_python_3_14_and_3_14_1_are_the_same_release_line():
    p = P("what is the latest version of Python", R("https://python.org/a", snippet="Python 3.14.1 released.", date="2026-11-10"),
          R("https://wikipedia.org/a", snippet="Python 3.14 is the latest version.", date="2026-11-10"))
    assert webanswer.assess(p, now=TODAY)["status"] == "agree" and "conflicting" not in webanswer.verdict(p, now=TODAY)


def test_fahrenheit_and_celsius_are_the_same_temperature():
    p = P("what is the temperature in Springfield today", R("https://weather.com/a", snippet="Springfield: high of 60 degrees today.", date="2026-11-15"),
          R("https://accuweather.com/a", snippet="Springfield today: high 15 degrees Celsius.", date="2026-11-15"))
    assert webanswer.assess(p, now=TODAY)["status"] == "agree"
    q = P("what is the temperature in Springfield today", R("https://weather.com/a", snippet="Springfield: high of 60 degrees today.", date="2026-11-15"),
          R("https://accuweather.com/a", snippet="Springfield today: high 30 degrees Celsius.", date="2026-11-15"))
    assert webanswer.assess(q, now=TODAY)["status"] == "conflict"


def test_a_quarter_score_is_not_a_conflicting_final():
    p = P("who won the Lakers game last night", R("https://espn.com/a", snippet="Lakers 112-104 Suns final.", date="2026-11-14"),
          R("https://nba.com/a", snippet="Lakers lead 30-28 after the first quarter against the Suns", date="2026-11-14"))
    assert "conflicting" not in webanswer.verdict(p, now=TODAY) and webanswer.assess(p, now=TODAY)["status"] == "single"


# --- 11. the follow-up window closes when the conversation is archived ------------------------------------------------------------------------
def test_open_the_second_one_in_a_fresh_conversation_is_not_a_follow_up(brain):
    brain.state.replies = ["Done.", "Fresh start.", "Fresh again."]
    brain.ask("Google who won the Lakers game last night", [].append)
    brain._archive()
    assert brain._web_at is None
    brain.ask("Hello there my friend", [].append)
    before = len(brain.state.calls)
    brain.ask("Open the second one", [].append)
    assert [c for c in brain.state.calls[before:] if c[0] == "open_web_page"] == []


# --- 12. the source named is the source quoted ------------------------------------------------------------------------------------------------
def test_where_did_you_get_that_names_the_fresh_quoted_source_not_a_stale_first_row():
    p = P("who won the Lakers game November 14, 2026", R("https://old.example.com/a", snippet="Lakers 98-91 Celtics final.", date="2026-09-01"),
          R("https://cbssports.com/a", snippet="Lakers beat the Celtics 110-104 on Saturday night.", date="2026-11-14"))
    said = webanswer.followup("Where did you get that?", p, TODAY)["say"]
    assert said.startswith("That came from cbssports.com")


def test_after_a_compound_turn_the_source_of_each_search_is_named():
    a = P("weather in Springfield November 15, 2026", R("https://weather.gov/a", snippet="Sunny, high near 62 degrees.", date="2026-11-15"))
    b = P("who won the Lakers game November 14, 2026", GAME_A)
    said = webanswer.followup("Where did you get that?", b, TODAY, extra=[a, b])["say"]
    assert "weather.gov" in said and "espn.com" in said and "weather in Springfield" in said


# --- minor: possessives and the day before yesterday ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, out", [("who won today's Lakers game", "who won the Lakers game November 15, 2026"),
                                       ("who won yesterday's Lakers game", "who won the Lakers game November 14, 2026"),
                                       ("who won the game the day before yesterday", "who won the game November 13, 2026"),
                                       ("what happened the day before the election", "what happened the day before the election"),
                                       ("what's Today's Show about", "what's Today's Show about")])
def test_possessive_and_two_day_phrases(said, out):
    assert webanswer.rewrite(said, TODAY) == out


# --- found while re-running the reviewer's probes -------------------------------------------------------------------------------------------
def test_a_part_about_him_or_his_household_is_never_a_separate_search():
    for said in ("Look up the gate code is 4421 and who won the Lakers game", "Search for my wife's address and who won the Lakers game"):
        calls = webintent.calls_for("search", said)
        assert [c[1]["query"] for c in calls] == ["who won the Lakers game 2026"]
        assert "NOT searched" in webintent.skipped_note("search", said) and "never goes to a search engine" in webintent.skipped_note("search", said)
    only_private = "Search for my wife's address and the gate code is 4421"
    assert webintent.nothing_to_search("search", "Search for my wife's address and who is my doctor").startswith("I won't send that")


@pytest.mark.parametrize("said, topic", [("Open Google and search for cats", "cats"), ("find me some videos on YouTube about Blender. Thanks", "Blender"),
                                         ("Search YouTube for cats and open the second one", "cats"), ("search for apple. pie recipes please", "apple. pie recipes")])
def test_asks_with_an_opener_or_a_trailing_action_keep_only_the_topic(said, topic):
    what = "youtube" if "youtube" in said.lower() else "search"
    assert webintent.kind(said) == what
    call = webintent.calls_for(what, said)[0][1]
    assert (call.get("topic") or call.get("query")) == topic


def test_every_relative_day_in_a_line_becomes_a_date():
    assert webanswer.rewrite("weather today and tomorrow", TODAY) == "weather November 15, 2026 and November 16, 2026"


@pytest.mark.parametrize("said", ["Are you sure you want to delete that?", "What's the source of that file?", "Is it Sunday?", "Is it raining?"])
def test_a_new_question_that_looks_like_a_follow_up_is_not_one(said):
    assert webanswer.followup(said, LAST, TODAY) is None


@pytest.mark.parametrize("said", ["Are you sure?", "are you sure about that", "What's the source?", "What's the source of that?"])
def test_a_plain_are_you_sure_asks_for_the_source(said):
    assert "say" in webanswer.followup(said, LAST, TODAY)


def test_two_close_temperatures_agree_about_the_figure():
    p = P("what is the temperature in Springfield today", R("https://weather.com/a", snippet="High of 60 degrees today.", date="2026-11-15"),
          R("https://accuweather.com/a", snippet="Today a high of 61 degrees.", date="2026-11-15"))
    assert "agree on about 60 degrees" in webanswer.verdict(p, now=TODAY)


def test_after_a_compound_turn_the_brain_names_the_source_of_each_search(brain):
    brain.state.replies = ["Sunny, and the Lakers won."]
    brain.ask("Google the weather in Springfield and who won the Lakers game last night", [].append)
    assert len(websearch.LAST["owner"]["packs"]) == 2
    n = len(brain.state.seen)
    said = []
    brain.ask("Where did you get that?", said.append)
    text = " ".join(said)
    assert len(brain.state.seen) == n and "weather in Springfield" in text and "who won the Lakers game" in text and "I haven't checked anything beyond" in text


# =====================================================================================================================
# Review round 2 (2026-11-15): the reviewer's inputs again, one test each
# =====================================================================================================================
def S(url, snippet, d="2026-11-14"):
    return {"url": url, "title": "t", "snippet": snippet, "date": d}


# --- 1. the figure the sources share is the answer, never a year or a retired version --------------------------------------------------------
def test_a_year_is_never_the_headline_count():
    q = "how many people live in Norfolk"
    p = P(q, S("https://census.gov/x", "As of 2020, Norfolk has 238,005 people."), S("https://wikipedia.org/x", "As of 2020, Norfolk has about 235,000 people."))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "conflict" and {f for _, f in a["conflict"]} == {"238,005", "235,000"}
    q = "what is the population of Norfolk"
    p = P(q, S("https://a.com/x", "Norfolk, Virginia population 2024: 232,000."), S("https://b.com/x", "Norfolk population in 2024 was 237,000."))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "conflict" and "2024" not in str(a["conflict"])


def test_a_figure_never_keeps_a_trailing_comma():
    assert webanswer.figures("count", "As of 2020, Norfolk has 238,005, people say.")[0][1] == "238,005"
    assert [s for _, s in webanswer.figures("count", "In 2020, it was 5,000.")] == ["5,000"]
    assert webanswer.figures("money", "It costs $98,123, up a bit.")[0][1] == "$98,123"


def test_a_rounded_count_is_not_a_conflict():
    q = "how tall is Mount Everest"
    p = P(q, S("https://a.com/x", "Mount Everest is 8,849 meters tall."), S("https://b.com/x", "Everest stands 8,848.86 metres (29,031.7 ft)."))
    assert webanswer.assess(p, q, TODAY)["status"] == "agree"


def test_the_latest_version_is_not_a_retired_one():
    q = "what is the latest version of Python"
    p = P(q, S("https://python.org/downloads", "Download Python 3.14.1", "2026-11-01"),
          S("https://endoflife.date/python", "Python 3.9 reached end of life; latest 3.14.1", "2026-11-01"))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "agree" and a["agreed"] == "3.14.1"
    q = "what is the latest version of Blender"
    p = P(q, S("https://blender.org/download", "Blender 4.5 LTS released July 2025. Blender 5.0 is the newest.", "2026-11-01"),
          S("https://wikipedia.org/wiki/Blender", "Blender 5.0 was released in November 2025.", "2026-11-01"))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "agree" and a["agreed"] == "5.0"
    q = "what is the latest version of Python"
    p = P(q, S("https://python.org/downloads", "Python 3.14.1 is the latest release.", "2026-11-01"),
          S("https://realpython.com/a", "Python 3.14 is out. Python 3.13.7 is the last bugfix.", "2026-10-01"))
    assert webanswer.assess(p, q, TODAY)["status"] == "agree" and "3.13" not in webanswer.verdict(p, q, TODAY)


# --- 2. definitions and general knowledge are timeless; only a question about the CURRENT value is time-sensitive -------------------------------------
@pytest.mark.parametrize("q", ["is Python open source", "is Blender open source software", "what is a good credit score", "what is the average SAT score",
                               "what's a stock split", "what are stocks", "what is the boiling temperature of water", "the rain in Spain",
                               "is rain good for tomatoes", "what's the highest score in Tetris", "what's the temperature of the sun",
                               "explain the traffic laws in Virginia", "what is the weather like in March", "what is open AI", "how do I check the weather on Linux",
                               "what is the freezing temperature of water", "what is bitcoin", "what temperature should I cook chicken to"])
def test_timeless_questions_get_no_date_year_or_window(q):
    assert webanswer.rewrite(q, TODAY) == q and webanswer.window_days(q, TODAY) is None


@pytest.mark.parametrize("q", ["is Walmart open", "what's the score of the Lakers game", "how is traffic", "is it going to rain", "how much is bitcoin",
                               "what's the stock price of Apple", "what is the temperature in Springfield", "what's the weather like in Springfield Virginia right now",
                               "what's the exchange rate for euro", "how's the weather", "what are the scores today"])
def test_questions_about_the_current_value_still_have_a_window_and_a_date(q):
    assert webanswer.window_days(q, TODAY) == 4 and "2026" in webanswer.rewrite(q, TODAY)


def test_old_sources_are_not_stale_for_is_python_open_source():
    q = "is Python open source"
    p = P(q, S("https://python.org/about", "Python is open source under the PSF license.", "2024-03-01"),
          S("https://en.wikipedia.org/wiki/Python", "Python is free and open source software.", "2025-01-02"))
    assert webanswer.assess(p, q, TODAY)["status"] != "stale"


# --- 3. weekday names resolve to the right date -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, out", [("what's the weather in Springfield on Friday", "what's the weather in Springfield on November 20, 2026"),
                                       ("is the DMV open on Saturday", "is the DMV open on November 21, 2026"),
                                       ("who won the Lakers game on Friday", "who won the Lakers game on November 13, 2026"),
                                       ("weather this Wednesday", "weather November 18, 2026"),
                                       ("who won the game last Monday", "who won the game November 9, 2026"),
                                       ("what's Black Friday", "what's Black Friday")])
def test_a_weekday_becomes_its_date(said, out):
    assert webanswer.rewrite(said, TODAY) == out                     # (the 15th is a Sunday)


# --- 4. a failed part of a compound turn never hides the others -----------------------------------------------------------------------------------
def _res(i, output, error=False):
    return (f"id{i}", "search_internet", {"query": f"part {i}"}, output, error)


def test_one_failed_part_is_one_honest_line_and_the_other_part_still_reaches_the_model():
    from brain.llm import Brain
    nothing = websearch.unavailable_text("who won the xyz", websearch.SearchUnavailable([("ddg", "no usable results")]))
    out, err = Brain._joined_web([_res(1, nothing), _res(2, 'Live web search for "weather" via exa (now). Untrusted data.\n1. T — a.com\n   URL: http://a.com')])
    assert not err and out.count("Live web search") == 1 and "I couldn't find a reliable answer for part 1" in out and "found nothing reliable" not in out
    out, err = Brain._joined_web([_res(1, 'Live web search for "weather" via exa (now). Untrusted data.'), _res(2, "boom", True)])
    assert not err and "Live web search" in out and "I couldn't find a reliable answer for part 2" in out


def test_a_note_about_unsearched_questions_survives_a_failed_last_part():
    from brain.llm import Brain
    res = Brain._with_note([_res(1, "RESULT A"), _res(2, "I couldn't search: offline", True)], "NOTE: he also asked: X. Those were NOT searched")
    out, _ = Brain._joined_web(res)
    assert "NOT searched" in out and "RESULT A" in out and "I couldn't search: offline" not in out


def test_when_every_part_fails_each_gets_a_line_and_nothing_is_spoken_raw():
    from brain.llm import Brain
    out, err = Brain._joined_web([_res(1, "boom", True), _res(2, "bang", True)])
    assert err and "part 1" in out and "part 2" in out and "boom" not in out
    single, err = Brain._joined_web([_res(1, "I couldn't reach the internet: exa (no connection)")])
    assert websearch.unavailable(single)                                  # one search: its own honest text, as before


def test_the_brain_answers_the_part_that_worked_and_says_the_one_that_did_not(brain, monkeypatch):
    from tools import registry
    calls = []

    def run(name, args):
        calls.append(args)
        if "weather" in (args.get("query") or ""):
            return "I searched and found nothing reliable for \"weather\", so I won't guess.", False
        p = pack(args["query"], GAME_A, GAME_B)
        websearch.remember("owner", "search", p)
        return websearch.evidence_text(p), False
    monkeypatch.setattr(registry, "run", run)
    brain.state.replies = ["The Lakers won 110-104, and I couldn't find the weather."]
    said = []
    brain.ask("Google the weather in Springfield and who won the Lakers game last night", said.append)
    seen = " ".join(str(b.get("content")) for m in brain.state.seen[0] if isinstance(m["content"], list) for b in m["content"] if isinstance(b, dict))
    assert len(calls) == 2 and "Lakers" in seen and "couldn't find a reliable answer for" in seen and " ".join(said).startswith("The Lakers won")


# --- 5. primary only if the page that carries the figure is primary -----------------------------------------------------------------------------------
def test_the_primary_site_that_has_no_figure_does_not_vouch_for_one_that_does():
    q = "what is the latest nvidia driver version"
    p = P(q, S("https://www.nvidia.com/en-us/drivers/", "Download the latest GeForce Game Ready drivers for your GPU."),
          S("https://www.techpowerup.com/download/nvidia/", "NVIDIA GeForce Game Ready driver 581.57 WHQL released."))
    v = webanswer.verdict(p, q, TODAY)
    assert "nvidia.com itself" not in v and "Only techpowerup.com gives that figure" in v
    a = webanswer.answer(p, q, TODAY)
    assert a.startswith("techpowerup.com") and "581.57" in a
    q = "what is the population of Virginia"
    p = P(q, S("https://www.census.gov/quickfacts/va", "QuickFacts Virginia: browse data and statistics."), S("https://worldpopulationreview.com/va", "Virginia population is 8,811,195 in 2026."))
    assert "census.gov itself" not in webanswer.verdict(p, q, TODAY) and "8,811,195" in webanswer.answer(p, q, TODAY)


# --- 6. short snippets of different outlets are not one wire story --------------------------------------------------------------------------------------
def test_two_short_snippets_in_different_words_are_two_sources():
    q = "who won the Lakers game last night"
    p = P(q, S("https://a.com/x", "The Lakers beat the Celtics 112-105 on November 14."), S("https://b.com/x", "Lakers 112, Celtics 105 on November 14."))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "agree" and len(a["sites"]) == 2


# --- 7. a fragment is not a question --------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["Who won the game? And the score?", "Who won, and what was the score?"])
def test_a_fragment_without_a_subject_keeps_the_line_whole(q):
    assert len(webanswer.split_questions(q)) == 1


# --- 8. a place swap needs a place -------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["what about Python", "how about Apple", "and Lakers?", "what about Emma", "what about Tom?"])
def test_a_capitalised_word_that_is_not_a_place_is_a_new_question(said):
    weather = P("weather in Springfield November 15, 2026", S("https://weather.gov/a", "Sunny, high near 62°", "2026-11-15"))
    assert webanswer.followup(said, weather, TODAY, veto=lambda n: n == "Emma") is None


@pytest.mark.parametrize("said, place", [("and Boston?", "Boston"), ("what about Virginia Beach", "Virginia Beach"), ("how about in Smithfield", "Smithfield")])
def test_a_place_swaps_in(said, place):
    weather = P("weather in Springfield November 15, 2026", S("https://weather.gov/a", "Sunny, high near 62°", "2026-11-15"))
    assert webanswer.followup(said, weather, TODAY)["args"]["query"] == f"weather in {place} November 15, 2026"


# --- 9. smaller ------------------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["how do I check the version of Python", "how to install the newest NVIDIA driver", "what is the current time in Tokyo",
                               "how do I update to the latest Blender", "what is the weather like in March"])
def test_how_to_and_clock_questions_get_no_year(q):
    assert webanswer.rewrite(q, TODAY) == q


def test_an_opener_with_a_named_browser_is_stripped():
    assert webintent.calls_for("search", "Open Edge and search for who won the game")[0][1]["query"] == "who won the game 2026"


def test_a_broken_address_never_throws():
    p = P("weather today", {"url": "http://[::1/x", "snippet": "54°F", "title": "t"}, {"url": "http://a.com/x", "snippet": "54°F today", "title": "t"})
    webanswer.verdict(p, now=TODAY), webanswer.answer(p, now=TODAY), websearch.evidence_text(p), websearch.spoken(p)
    assert not webanswer.is_primary({"url": "http://[::1/x"}, "x") and websearch._host("http://[::1/x") == ""


@pytest.mark.parametrize("raw, iso", [("5 mins ago", "2026-11-15"), ("2 hrs ago", "2026-11-15"), ("3h ago", "2026-11-15"), ("2d ago", "2026-11-13"),
                                      ("Updated 3 days ago", "2026-11-12"), ("Today, 3:45 PM", "2026-11-15"), ("Yesterday at 5pm", "2026-11-14"),
                                      ("2026/11/14", "2026-11-14")])
def test_more_relative_and_clock_dates(raw, iso):
    assert webanswer.parse_date(raw, TODAY) == iso


def test_the_private_half_of_a_lookup_is_reported_not_dropped(monkeypatch):
    said = "What's the weather in Springfield and what is Emma's phone number?"
    from core import users
    monkeypatch.setattr(users, "all_users", lambda: {"owner": {"name": "Alex"}, "emma": {"name": "Emma"}})
    q, skipped, private = webintent.lookup_plan(said)
    assert q == ["What's the weather in Springfield"] and private == ["what is Emma's phone number"]
    assert "NOT searched" in webintent.lookup_skipped(said) and "household" in webintent.lookup_skipped(said)


def test_look_it_up_with_no_worldly_question_searches_nothing_and_does_not_search_the_bare_line(brain, monkeypatch):
    monkeypatch.setattr(webintent, "lookup_queries", lambda text, limit=2: [])
    brain.state.replies = ["I don't have current information on that."]
    said = []
    brain.ask("Who won the Lakers game last night?", said.append)
    assert [c for c in brain.state.calls if c[0] in ("search_internet", "research_online")] == []
    assert said and "current information" in " ".join(said)


@pytest.mark.parametrize("q", ["will it snow this winter", "what is the schedule of a Roman day"])
def test_a_season_or_a_roman_schedule_is_not_about_now(q):
    assert webanswer.rewrite(q, TODAY) == q and webanswer.window_days(q, TODAY) is None


def test_his_next_step_is_not_part_of_the_search_and_a_leading_and_is_dropped_from_the_note():
    assert webintent.calls_for("search", "search for blender tutorials and then open the first one")[0][1]["query"] == "blender tutorials"
    assert webintent.calls_for("search", "search the web for the weather and then tell me what I should wear")[0][1]["query"] == "weather November 15, 2026"
    note = webintent.skipped_note("search", "Search the web for the Lakers score. Also what is the weather in Springfield? And who is the president? And who is the CEO of Tesla?")
    assert "he also asked: who is the CEO of Tesla" in note.replace("And ", "") or "who is the CEO of Tesla" in note
    assert "And who" not in note


# =====================================================================================================================
# Review round 3 (2026-11-15): privacy, fragments, like-with-like, names, primary, weekdays, dates, prices
# =====================================================================================================================
@pytest.fixture
def household(monkeypatch):
    from core import users
    monkeypatch.setattr(users, "all_users", lambda: {"owner": {"name": "Alex"}, "sarah": {"name": "Sarah Jones"}, "emma": {"name": "Emma"}})


def _queries(said):
    return [c[1].get("query") or c[1].get("question") for c in webintent.calls_for("search", said)]


# --- 1. a private clause of ANY search is never sent ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["search for the weather in Springfield and Sarah's school schedule", "search for weather in Springfield and my dentist",
                                  "search for weather in Springfield and my wife", "search for weather in Springfield and our house",
                                  "search for the weather in Springfield and Emma's soccer practice", "search for weather in Springfield and the Alex residence"])
def test_a_private_clause_is_dropped_from_a_single_query_and_reported(household, said):
    qs = _queries(said)
    assert qs == ["weather in Springfield November 15, 2026"] or qs == ["the weather in Springfield November 15, 2026"]
    note = webintent.skipped_note("search", said)
    assert "NOT searched" in note and "household" in note
    assert not any(w in " ".join(qs).lower() for w in ("sarah", "emma", "dentist", "wife", "house", "alex"))


@pytest.mark.parametrize("said", ["search for Sarah's school schedule", "search for my wife's address", "search for my dentist", "look up our house"])
def test_a_search_that_is_wholly_private_searches_nothing(household, said):
    assert webintent.calls_for("search", said) == []
    assert webintent.nothing_to_search("search", said).startswith("I won't send that")


@pytest.mark.parametrize("said", ["search for pizza near me", "search for Python code examples", "search for Tommy Hilfiger sale", "google mom jeans"])
def test_public_asks_that_only_look_personal_are_still_searched(household, said):
    assert len(webintent.calls_for("search", said)) == 1


# --- 2. a part that has no subject of its own keeps the line whole -------------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["who won the Lakers game last night and what was the final score", "who won the Lakers game and what was the score",
                               "what's the weather in Springfield and what's the temperature", "who won the Lakers game and what was the result",
                               "what's the weather in Springfield and how long is the drive to Richmond and what is the price"])
def test_the_second_half_without_a_topic_is_not_split_off(q):
    assert webanswer.split_questions(q)[0].startswith(q.split(" and ")[0]) and len(webanswer.split_questions(q)) <= 2
    assert not any(p.startswith(("what was the", "what's the temperature", "what is the price")) for p in webanswer.split_questions(q))


def test_a_self_contained_second_question_is_still_split():
    assert len(webanswer.split_questions("the weather in Springfield and who won the Lakers game last night")) == 2
    assert len(webanswer.split_questions("who is the CEO of Nvidia and what is the capital of Spain")) == 2
    assert webintent.lookup_plan("who won the Lakers game and what was the score?")[0] == ["who won the Lakers game and what was the score"]
    assert len(webintent.calls_for("search", "search for who won the Lakers game last night and what was the final score")) == 1


# --- 3. weather is compared like with like, and the verdict is about the quoted figure ------------------------------------------------------------
def test_current_temperatures_agree_and_the_quote_is_about_that_figure():
    q = "what's the weather in Springfield November 15, 2026"
    p = P(q, S("https://weather.com/x", "Springfield, IL. Currently 54°F. High 62°F, low 45°F tonight. Rain likely.", "2026-11-15"),
          S("https://accuweather.com/y", "Springfield weather: now 55°F. Today's high 63°F, low 44°F.", "2026-11-15"))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "agree" and "about 54°F" in webanswer.verdict(p, q, TODAY)
    said = webanswer.answer(p, q, TODAY)
    assert "Currently 54°F" in said


def test_a_high_and_a_current_reading_are_not_compared():
    q = "what's the weather in Springfield November 15, 2026"
    p = P(q, S("https://weather.com/x", "Springfield, IL weather today: high of 62°F with clouds. Low of 45°F.", "2026-11-15"),
          S("https://accuweather.com/y", "Springfield weather: 45°F now, clearing up.", "2026-11-15"))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "single" and a.get("incomparable")
    v = webanswer.verdict(p, q, TODAY)
    assert "agree" not in v and "can't compare" in v
    said = webanswer.answer(p, q, TODAY)
    assert "62°F" in said


def test_two_highs_that_differ_are_a_conflict():
    q = "what's the weather in Springfield November 15, 2026"
    p = P(q, S("https://weather.com/x", "Today's high of 62°F in Springfield.", "2026-11-15"), S("https://accuweather.com/y", "Springfield high 71°F today.", "2026-11-15"))
    assert webanswer.assess(p, q, TODAY)["status"] == "conflict"


# --- 4. names with a day-word in them stay, possessives work ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["Saturday Night Live cast", "when does Sunday Night Football start", "Friday Night Lights cast", "Monday Night Raw results",
                               "Today Show hosts", "Tomorrow Never Dies plot", "Last Night in Soho streaming", "who hosts the Today show", "who sang Yesterday",
                               "Tomorrowland festival dates", "The Day After Tomorrow cast", "Black Friday deals", "what is Monday Night Raw"])
def test_a_title_with_a_day_word_is_not_rewritten(q):
    assert webanswer.rewrite(q, TODAY) == q


@pytest.mark.parametrize("said, out", [("who won last night's game", "who won the game November 14, 2026"), ("what's tonight's game", "what's the game November 15, 2026"),
                                       ("who won yesterday's game", "who won the game November 14, 2026")])
def test_possessive_days(said, out):
    assert webanswer.rewrite(said, TODAY) == out


# --- 5. "primary" only for .gov/.edu/.mil or a known official site ---------------------------------------------------------------------------------
@pytest.mark.parametrize("url, q", [("https://springfield.com/weather", "what's the weather in Springfield"), ("https://mercury.com/blog", "who is the CEO of Mercury Insurance"),
                                    ("https://norfolk.com/about", "how many people live in Norfolk"), ("https://lakers.com/a", "who won the Lakers game")])
def test_a_site_named_like_the_question_is_not_primary(url, q):
    assert not webanswer.is_primary(R(url), q)
    assert "primary" not in webanswer.verdict(P(q, S(url, "It is 62°F in Springfield. Norfolk has 238,005 people. Lakers beat Suns 112-105. Mercury CEO.")), q, TODAY)


@pytest.mark.parametrize("url, q", [("https://www.nvidia.com/a", "latest NVIDIA driver"), ("https://www.python.org/a", "latest Python version"),
                                    ("https://www.blender.org/a", "latest Blender release"), ("https://www.apple.com/a", "how much is the iPhone 17 from Apple"),
                                    ("https://weather.gov/a", "weather in Springfield"), ("https://www.census.gov/a", "population of Virginia")])
def test_a_known_official_site_is_primary(url, q):
    assert webanswer.is_primary(R(url), q)


def test_a_lookalike_site_gets_no_rank_bonus():
    rows = [R("https://a.com/x", date="2026-11-15"), R("https://springfield.com/weather", date="2026-11-15")]
    assert [r["url"] for r in webanswer.rank(rows, "weather in Springfield", TODAY)] == [r["url"] for r in rows]


# --- 7. a weekday after a capitalised place is a weekday -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, out", [("weather in Springfield Friday", "weather in Springfield November 20, 2026"), ("Springfield weather Monday", "Springfield weather November 16, 2026"),
                                       ("Lakers score Friday night", "Lakers score November 20, 2026"), ("is Costco open Saturday", "is Costco open November 21, 2026"),
                                       ("Black Friday weather", "Black Friday weather November 15, 2026")])
def test_a_weekday_after_a_capitalised_word(said, out):
    assert webanswer.rewrite(said, TODAY) == out


@pytest.mark.parametrize("said, query", [("how about Saturday", "weather in Springfield November 21, 2026"), ("and Monday", "weather in Springfield November 16, 2026"),
                                         ("what about Friday?", "weather in Springfield November 20, 2026")])
def test_a_weekday_follow_up_after_a_springfield_answer(said, query):
    got = webanswer.followup(said, P("weather in Springfield November 15, 2026", S("https://a.com/x", "62°F")), TODAY)
    assert got["args"]["query"] == query


# --- 8. ordinals are dates ------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["weather on November 20th", "weather on March 3rd", "what's the weather on the 4th of December", "weather on the 20th",
                               "what's the weather on 4th December", "weather on the 1st of March"])
def test_an_ordinal_date_gets_nothing_appended(q):
    assert webanswer.rewrite(q, TODAY) == q


# --- 9. the year of an annual event is the latest edition that has happened --------------------------------------------------------------------------
@pytest.mark.parametrize("q, now, year", [("who won the Super Bowl", date(2027, 1, 15), 2026), ("who won the Super Bowl", date(2026, 11, 15), 2026),
                                          ("who won the World Series", date(2027, 1, 15), 2026), ("who won the Masters", date(2027, 2, 1), 2026),
                                          ("who won the Stanley Cup", date(2026, 11, 15), 2026), ("who won the Super Bowl", date(2027, 3, 1), 2027)])
def test_an_annual_event_gets_the_year_of_its_latest_edition(q, now, year):
    assert webanswer.rewrite(q, now) == f"{q} {year}"


# --- 11. live prices a hair apart agree; far apart conflict --------------------------------------------------------------------------------------------
def test_prices_within_one_percent_agree_about_the_figure_and_show_the_range():
    q = "how much is bitcoin November 15, 2026"
    p = P(q, S("https://coindesk.com/a", "Bitcoin is trading at $98,412 today.", "2026-11-15"), S("https://coinmarketcap.com/b", "The live Bitcoin price today is $98,430.", "2026-11-15"))
    assert webanswer.assess(p, q, TODAY)["status"] == "agree"
    v = webanswer.verdict(p, q, TODAY)
    assert "about $98,412 to $98,430" in v and "conflicting" not in v


def test_prices_far_apart_conflict_and_in_between_are_close_not_agreed():
    q = "how much is bitcoin November 15, 2026"
    far = P(q, S("https://coindesk.com/a", "Bitcoin is trading at $98,000 today.", "2026-11-15"), S("https://coinmarketcap.com/b", "Bitcoin price today is $102,000.", "2026-11-15"))
    assert webanswer.assess(far, q, TODAY)["status"] == "conflict"
    mid = P(q, S("https://coindesk.com/a", "Bitcoin is trading at $98,000 today.", "2026-11-15"), S("https://coinmarketcap.com/b", "Bitcoin price today is $100,000.", "2026-11-15"))
    assert webanswer.assess(mid, q, TODAY)["status"] == "close" and "close but not the same" in webanswer.verdict(mid, q, TODAY)


# --- 6. only the sentence that answers the question supplies the figure -------------------------------------------------------------------------------
def test_a_date_in_a_non_answer_sentence_is_not_the_answer():
    q = "when was Tom Hanks born"
    p = P(q, S("https://wikipedia.org/a", "Thomas Jeffrey Hanks (born July 9, 1956) is an American actor.", None),
          S("https://biography.com/b", "Hanks was born in Concord, California in 1956. His first film was released June 10, 1984.", None))
    assert webanswer.assess(p, q, TODAY)["status"] == "single"


def test_a_point_spread_is_not_a_score():
    q = "who won the Lakers game November 14, 2026"
    p = P(q, S("https://espn.com/a", "Lakers vs Suns: tipoff 7:30 PM tonight; Lakers favored by 6-7 points.", "2026-11-14"),
          S("https://cbssports.com/b", "Lakers vs Suns prediction: Lakers 112, Suns 105.", "2026-11-14"))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "single" and a["agree_sites"] == ["cbssports.com"]


# --- 10. gold, gas, oil, currencies and mortgage rates are quotes ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["what's the price of gold", "how much is a gallon of gas", "what are mortgage rates", "how much is oil", "how much is the euro in dollars"])
def test_commodity_and_rate_questions_are_about_now(q):
    assert webanswer.window_days(q, TODAY) == 4 and "2026" in webanswer.rewrite(q, TODAY)


def test_a_two_year_old_gold_price_is_old():
    q = "what's the price of gold November 15, 2026"
    p = P(q, S("https://kitco.com/a", "Gold is trading at $2,310 an ounce.", "2024-05-02"), S("https://bullion.com/b", "Spot gold price: $2,310 per ounce.", "2024-05-02"))
    assert webanswer.assess(p, q, TODAY)["status"] == "stale"
    assert "rate" in webanswer.wanted_kinds("what are mortgage rates") or "percent" in webanswer.wanted_kinds("what are mortgage rates")


# --- 12. a follow-up after a compound turn re-runs both searches ------------------------------------------------------------------------------------------
def test_a_new_day_after_a_compound_turn_searches_both_again():
    a = P("weather in Springfield November 15, 2026", S("https://weather.gov/a", "Sunny.", "2026-11-15"))
    b = P("who won the Lakers game November 14, 2026", GAME_A)
    got = webanswer.followup("what about tomorrow?", b, TODAY, extra=[a, b])
    assert [c[1]["query"] for c in got["calls"]] == ["weather in Springfield November 16, 2026", "who won the Lakers game November 16, 2026"]


# --- 13. the "first two" line is only said when something public was left out ----------------------------------------------------------------------------
def test_over_limit_is_only_true_for_public_questions_left_out(household):
    assert not webintent.over_limit("search", "search for weather in Springfield and my dentist")
    assert webintent.over_limit("search", "search for who won the Lakers game and what is the capital of Spain and who is the CEO of Tesla")


# --- 14. the drive to Richmond is a public question ------------------------------------------------------------------------------------------------------
def test_how_long_is_the_drive_to_richmond_is_not_private():
    assert webintent.lookup_query("how long is the drive to Richmond?")
    assert webintent.lookup_plan("what's the weather in Springfield and how long is the drive to Richmond?")[2] == []


# --- 16. a score is shown winner first ---------------------------------------------------------------------------------------------------------------------
def test_a_score_is_never_shown_loser_first():
    assert [s for _, s in webanswer.figures("score", "Celtics 105, Lakers 112")] == ["112-105"]
    q = "who won the Lakers game November 14, 2026"
    p = P(q, S("https://a.com/x", "Celtics 105, Lakers 112 final.", "2026-11-14"), S("https://b.com/x", "Lakers win 112-105.", "2026-11-14"))
    assert "105-112" not in webanswer.verdict(p, q, TODAY) and "112-105" in webanswer.verdict(p, q, TODAY)


# --- the fuzz: any junk in any field of any row ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("junk", [5, 1.5, float("nan"), ["a", "b"], {"a": 1}, b"bytes", None, True])
def test_junk_in_any_field_never_throws(junk):
    for field in ("url", "title", "site", "date", "listed", "snippet", "excerpt", "facts"):
        row = {"url": "https://a.com/x", "title": "t", "snippet": "Lakers 112-105.", "date": "2026-11-14", field: junk}
        p = {"question": "who won the Lakers game", "provider": "x", "sources": [row, {"url": "https://b.com/x", "snippet": "Lakers 112-105."}]}
        for fn in (webanswer.assess, webanswer.verdict, webanswer.answer, webanswer.evidence_note, websearch.spoken, websearch.evidence_text,
                   lambda p: webanswer.rank(p["sources"], "x", TODAY), lambda p: webanswer.followup("open the second one", p, TODAY),
                   lambda p: webanswer.followup("where did you get that", p, TODAY), webanswer.quoted_row):
            fn(p)


# --- live (2026-11-15): "What's the news?" answered with the SOURCES and no headline ---------------------------------------------------------------------
NEWS_ROWS = [{"title": "Senate passes budget bill after overnight vote", "url": "https://apnews.com/a", "site": "apnews.com", "date": "2026-11-15",
              "snippet": "The Senate approved the federal budget bill early Sunday, sending it to the president."},
             {"title": "Storm warnings issued along the Virginia coast", "url": "https://npr.org/b", "site": "npr.org", "date": "2026-11-15",
              "snippet": "Forecasters expect heavy rain and high winds through Monday morning."},
             {"title": "Top stories - Google News", "url": "https://news.google.com/c", "site": "news.google.com", "date": "2026-11-15", "snippet": ""}]


def _news_history():
    evidence = websearch.evidence_text(pack("top news headlines November 15, 2026", *NEWS_ROWS))
    return [{"role": "user", "content": "What's the news?"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "search_internet", "input": {"query": "top news"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w1", "content": evidence, "is_error": False}]}], evidence


@pytest.mark.parametrize("reply", ["I pulled up today's top headlines from Google News, AP, NPR, CBS and NBC, sir.", "Here is what I found.",
                                   "I've pulled up the latest news from several sources, sir.", "Here are today's headlines:",
                                   "I found the top stories from AP and NPR."])
def test_a_reply_that_only_names_the_sources_is_not_grounded(reply):
    _, evidence = _news_history()
    assert not intelligence._web_content_grounded(reply, evidence)


@pytest.mark.parametrize("reply", ["The Senate passed the federal budget bill after an overnight vote, per AP.",
                                   "Storm warnings are out along the Virginia coast, with heavy rain through Monday, per NPR.",
                                   "The Senate approved the budget early Sunday."])
def test_a_reply_that_says_a_headline_is_grounded(reply):
    _, evidence = _news_history()
    assert intelligence._web_content_grounded(reply, evidence)


def test_a_figure_copied_from_the_results_counts():
    evidence = websearch.evidence_text(pack("latest nvidia driver", S("https://nvidia.com/a", "Game Ready Driver 621.01 released.")))
    assert intelligence._web_content_grounded("It is 621.01, sir.", evidence)
    assert not intelligence._web_content_grounded("It is out now, sir.", evidence)


def test_the_local_model_naming_only_the_sources_raises_the_ungrounded_path(monkeypatch):
    history, _ = _news_history()
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.delenv("JARVIS_PC_URL", raising=False)

    def reply(text):
        return {"model": "local", "choices": [{"finish_reason": "stop", "message": {"content": text, "tool_calls": []}}]}
    schemas = [{"name": "search_internet", "description": "search", "input_schema": {"type": "object", "properties": {}}}]
    monkeypatch.setattr(intelligence, "complete_local", lambda payload, cancelled=lambda: False, on_text=None: reply(
        "I pulled up today's top headlines from Google News, AP, NPR, CBS and NBC, sir."))
    with pytest.raises(intelligence.LocalFailure, match="ungrounded_answer"):
        intelligence.request("fast", "sys", history, schemas)
    monkeypatch.setattr(intelligence, "complete_local", lambda payload, cancelled=lambda: False, on_text=None: reply(
        "The Senate passed the federal budget bill after an overnight vote, per AP."))
    out = intelligence.request("fast", "sys", history, schemas)
    assert "Senate" in out.content[0].text


def test_the_brain_speaks_the_sources_itself_when_no_reply_is_grounded(brain, monkeypatch):
    from tools import registry
    def run(name, args):
        p = pack(args.get("query") or args.get("question"), *NEWS_ROWS)
        websearch.remember("owner", "search", p)
        return websearch.evidence_text(p), False
    monkeypatch.setattr(registry, "run", run)
    monkeypatch.setattr(intelligence, "request", lambda *a, **k: (_ for _ in ()).throw(intelligence.LocalFailure("ungrounded_answer")))
    said = []
    brain.ask("What's the news?", said.append)
    spoken = " ".join(said)
    assert "Senate passes budget bill" in spoken and "apnews.com" in spoken


@pytest.mark.parametrize("q", ["the Day After Tomorrow cast", "what's the weather in Tokyo in the summer"])
def test_a_lowercase_the_before_a_title_and_a_named_season_are_left_alone(q):
    assert webanswer.rewrite(q, TODAY) == q


# =====================================================================================================================
# Review round 4 (2026-11-15): honest caveats pass the grounding check, the past gets no year, units, and the cheap ones
# =====================================================================================================================
TODAY_ISO = TODAY.isoformat()


def _pk(q, *rows):
    """A pack like the live one: rows are (url, title, excerpt, date)."""
    return {"question": q, "query": q, "provider": "exa", "searched_at": "2026-11-15T12:00:00+00:00",
            "sources": [{"url": u, "title": t, "excerpt": x, "date": d, "site": webanswer.site_of(u)} for u, t, x, d in rows]}


PACKS = {
    "stale": _pk("what's the weather in Springfield November 15, 2026", ("https://a.com/w", "Springfield weather forecast", "Springfield forecast: high 58F on June 3.", "2025-06-03"),
                 ("https://b.com/w", "Springfield IL weather", "Sunny with a high of 80F.", "2025-06-04")),
    "nothing": _pk("who is the mayor of Smallville", ("https://a.com/x", "Smallville history", "Smallville was founded in 1850 near the river.", "2024-01-01")),
    "conflict": _pk("who won the Lakers game November 14, 2026", ("https://a.com/x", "Lakers beat Celtics", "The Lakers beat the Celtics 110-102 on Friday.", "2026-11-14"),
                    ("https://b.com/y", "Celtics top Lakers", "Celtics won 98-95 over the Lakers on Friday.", "2026-11-14")),
    "single": _pk("what's the latest nvidia driver", ("https://nvidia.com/d", "GeForce driver 581.57", "Latest GeForce Game Ready Driver 581.57 released.", "2026-11-14")),
    "multi": _pk("top news headlines November 15, 2026", ("https://apnews.com/a", "Senate passes budget bill after overnight session", "Lawmakers voted 52-48.", "2026-11-15"),
                 ("https://npr.org/b", "Storm hits Gulf coast", "Thousands lose power.", "2026-11-15")),
}


@pytest.mark.parametrize("name, reply", [
    ("stale", "The results I found are old, the newest is from June 4, 2025, so I can't confirm the current answer."),
    ("stale", "I can't confirm that: the results are from last June, so they're out of date."),
    ("nothing", "I couldn't find a reliable source for that, sir."),
    ("nothing", "I found nothing reliable on that, so I won't guess."),
    ("nothing", "Sorry sir, nothing reliable came up on that."),
    ("nothing", "I don't have a reliable answer; the results don't say."),
    ("conflict", "I found conflicting reports, so I can't tell you which team won."),
    ("single", "Version 581.57 per nvidia.com, though I found no second source to confirm it."),
    ("single", "The latest is 581.57, but it's only one source, so I can't confirm it."),
])
def test_an_honest_caveat_is_a_grounded_reply(name, reply):
    assert intelligence._web_content_grounded(reply, websearch.evidence_text(PACKS[name]))


@pytest.mark.parametrize("name", list(PACKS))
def test_the_computed_verdict_and_our_own_fallback_always_pass(name):
    pk = PACKS[name]
    ev = websearch.evidence_text(pk)
    for text in (webanswer.verdict(pk, pk["question"]), webanswer.answer(pk, pk["question"]), websearch.spoken(pk)):
        assert intelligence._web_content_grounded(text, ev), text


@pytest.mark.parametrize("reply", ["I pulled up today's top headlines from Google News, AP, NPR, CBS and NBC, sir.", "I pulled up today's headlines from AP and NPR, sir.",
                                   "Here is what I found.", "I've pulled up the latest for you, sir."])
def test_an_announcement_that_states_nothing_is_still_rejected(reply):
    assert not intelligence._web_content_grounded(reply, websearch.evidence_text(PACKS["multi"]))


def test_a_reply_in_another_language_that_copies_the_figure_passes():
    assert intelligence._web_content_grounded("Les Lakers ont battu les Celtics 110-102.", websearch.evidence_text(PACKS["conflict"]))


def test_one_rejection_speaks_the_sources_directly_with_no_second_try(brain, monkeypatch):
    from tools import registry
    calls = []

    def run(name, args):
        p = pack(args.get("query") or args.get("question"), *NEWS_ROWS)
        websearch.remember("owner", "search", p)
        return websearch.evidence_text(p), False

    def request(*a, **k):
        calls.append(1)
        raise intelligence.LocalFailure("ungrounded_answer")
    monkeypatch.setattr(registry, "run", run)
    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    said = []
    brain.ask("What's the news?", said.append)
    assert len(calls) == 1 and "Senate passes budget bill" in " ".join(said)


# --- 2. the past gets no year ------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["who won the World Series last year", "who won last year's Super Bowl", "who won the Masters two years ago", "who won the election last year",
                               "who won the game last season", "who won the Super Bowl last year", "who won the NBA finals last June", "who won the World Series back in 2010",
                               "who won the Super Bowl in the 90s", "who won the game last decade", "who was champion in the previous season"])
def test_a_question_about_the_past_gets_no_year(q):
    assert webanswer.rewrite(q, TODAY) == q and webanswer.window_days(q, TODAY) is None


# --- 3. units -------------------------------------------------------------------------------------------------------------------------------------------
def test_metres_and_feet_are_the_same_height():
    q = "how tall is Mount Everest"
    p = P(q, S("https://a.com/x", "Mount Everest is 8,849 metres tall.", None), S("https://b.com/y", "Mount Everest stands 29,032 feet above sea level.", None))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "agree" and "conflicting" not in webanswer.verdict(p, q, TODAY)
    assert webanswer.assess(P(q, S("https://a.com/x", "Everest is 8,849 m high.", None), S("https://b.com/y", "Everest is 5 miles high.", None)), q, TODAY)["status"] != "agree"


def test_figures_in_units_that_cannot_be_compared_are_not_a_conflict():
    q = "how long is it"
    p = P(q, S("https://a.com/x", "It is 90 miles long.", None), S("https://b.com/y", "It is 90 kg of rope.", None))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "single" and a.get("incomparable") and "different units" in webanswer.verdict(p, q, TODAY)


# --- cheap ones -------------------------------------------------------------------------------------------------------------------------------------------
def test_a_source_agrees_only_on_its_own_headline_figure():
    q = "who won the Lakers game last night"
    p = P(q, S("https://a.com/x", "The Lakers beat the Celtics 110-102 on Friday night.", "2026-11-14"),
          S("https://b.com/y", "Celtics 98, Lakers 95 final. In their earlier meeting the Lakers won 110-102.", "2026-11-14"))
    assert webanswer.assess(p, q, TODAY)["status"] == "conflict"


def test_a_previous_price_is_not_a_figure():
    q = "what is the price of gold today"
    p = P(q, S("https://a.com/gold", "Gold is trading at $2,650 per ounce today.", "2026-11-15"),
          S("https://b.com/gold", "Gold price today is $2,700 per ounce, up from $2,650 last week.", "2026-11-15"))
    a = webanswer.assess(p, q, TODAY)
    assert a["status"] == "close" and "to $2,700" not in str(a.get("agreed"))


@pytest.mark.parametrize("q", ["who plays in the Friday the 13th movie", "Friday the 13th", "who won the Lakers game on Friday the 13th"])
def test_friday_the_13th_is_not_a_weekday(q):
    out = webanswer.rewrite(q, TODAY)
    assert "the 13th" in out and "November 13, 2026 the 13th" not in out and not out.startswith("who plays in the November")


def test_the_youtube_route_drops_private_clauses_too(household):
    call = webintent.calls_for("youtube", "Look up three Blender tutorials on YouTube and my wife's email password")
    assert call[0][1]["topic"] == "Blender tutorials"
    assert webintent.calls_for("youtube", "find YouTube videos about Sarah's piano recital") == []
    assert webintent.nothing_to_search("youtube", "search YouTube for Emma's school play").startswith("I won't send that")
    assert "NOT searched" in webintent.skipped_note("youtube", "Look up three Blender tutorials on YouTube and my wife's email password")


def test_a_private_relative_clause_is_dropped_without_leaving_a_fragment(household):
    qs = _queries("google nearby restaurants where my wife and I can go tonight")
    assert qs == ["nearby restaurants November 15, 2026"]
    for line in ("search for pizza places that my son likes", "search for hotels in Boston who my wife recommended"):
        assert all("my " not in q and " I " not in f" {q} " for q in _queries(line))


@pytest.mark.parametrize("join", [" & ", " / ", " - ", " or ", " as well as ", " along with ", ", plus ", " then "])
def test_every_way_of_joining_a_private_clause_is_split(household, join):
    line = f"search for the weather in Springfield{join}Sarah's school schedule"
    qs = _queries(line)
    assert qs and all("sarah" not in q.lower() and "school" not in q.lower() for q in qs)


# =====================================================================================================================
# Review round 5 (2026-11-15): round figures are compared exactly, now beats the past, caveats are anchored, prices from, YouTube note
# =====================================================================================================================
def _units_status(q, a, b):
    p = P(q, S("https://a.com/x", a, TODAY_ISO), S("https://b.com/y", b, TODAY_ISO))
    return webanswer.assess(p, q, TODAY)["status"]


@pytest.mark.parametrize("q, a, b", [
    ("how far is the airport", "The airport is 20 km away.", "The airport is 30 km away."),
    ("how far is the airport", "The airport is 20 km away.", "The airport is 40 km away."),
    ("how tall is the tower", "The tower is 100 m tall.", "The tower is 150 m tall."),
    ("how far is the lake", "The lake is 3,000 km away.", "The lake is 2,000 km away."),
    ("how big is the install", "Needs 2,000 MB of space.", "Needs 3,000 MB of space."),
    ("how far is the airport", "The airport is 20 miles away.", "The airport is 30 miles away."),
    ("how far is the moon", "The Moon is 384,400 km away.", "The Moon is 250,000 miles away."),
    ("how heavy is the box", "The box weighs 50 lb.", "The box weighs 70 lb."),
    ("how big is the install", "Needs 20 GB of space.", "Needs 60 GB of space."),
    ("how fast is the car", "The car goes 200 mph.", "The car goes 300 mph."),
    ("how many are there", "There are 20 of them.", "There are 24 of them."),
    ("how tall is the Eiffel Tower", "The tower is 330 m tall.", "The tower is 324 m tall."),
])
def test_round_figures_are_compared_exactly_not_to_one_digit(q, a, b):
    assert _units_status(q, a, b) == "conflict"


@pytest.mark.parametrize("q, a, b", [
    ("how tall is Mount Everest", "Mount Everest is 8,849 metres tall.", "Everest stands 29,032 feet high."),
    ("how tall is Mount Everest", "Mount Everest is 8,849 m tall.", "Everest stands 8,848.86 m high."),
    ("how tall is Mount Everest", "Mount Everest is 8,849 tall.", "Everest stands 8,848.86 high."),
    ("how far is the moon", "The Moon is 384,400 km away.", "The Moon is 238,855 miles away."),
    ("how heavy is the ball", "A bowling ball weighs up to 16 pounds.", "A bowling ball weighs 7.26 kg at most."),
    ("how fast is a cheetah", "A cheetah runs 70 mph.", "A cheetah hits 112 km/h."),
    ("how big is the game download", "The game is 120 GB to download.", "Download size: 111.8 GiB."),
    ("how tall is the Eiffel Tower", "The tower is 330 m tall.", "The tower is 1,083 ft tall."),
    ("how long is the Nile", "The Nile is 6,650 km long.", "The Nile is 4,130 miles long."),
    ("how far is the airport", "The airport is 20 miles away.", "It lies 20 miles from the city centre."),
])
def test_the_same_figure_in_another_unit_or_rounding_still_agrees(q, a, b):
    assert _units_status(q, a, b) == "agree"


def test_prices_a_hair_apart_still_agree():
    assert _units_status("how much is bitcoin", "Bitcoin is trading at $98,412 today.", "The live Bitcoin price is $98,430.") == "agree"


# --- 2. now beats the past -------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["what is the price of bitcoin today compared to last year", "what is the current price of gold, up from last year",
                               "what is the price of bitcoin today compared to 2020", "what's the bitcoin price now vs a year ago", "are RTX 5090 back in stock",
                               "is the PS5 back in stock", "what is the latest iPhone, the previous one was 16"])
def test_a_now_marker_keeps_the_question_current(q):
    assert webanswer.window_days(q, TODAY) in (4, 365)


def test_old_sources_are_stale_for_a_price_today_compared_to_last_year():
    q = "what is the price of bitcoin today compared to last year"
    p = P(q, S("https://a.com/x", "Bitcoin is trading at $64,200 today.", "2025-03-01"), S("https://b.com/y", "Bitcoin price: $64,250 right now.", "2025-03-02"))
    assert webanswer.assess(p, q, TODAY)["status"] == "stale"
    assert "2026" in webanswer.rewrite("what is the current price of gold, up from last year", TODAY)


# --- 3. the caveat must be the reply's first sentence and carry no claim of its own --------------------------------------------------------------------
@pytest.mark.parametrize("reply", ["I couldn't confirm it, but I pulled up the headlines from AP, sir.",
                                   "I pulled up the headlines from AP and NPR; I can't tell you more.",
                                   "The president resigned today, but I can't confirm it.",
                                   "I pulled up today's top headlines from AP and NPR, sir, though I can't say more than that.",
                                   "Not confirmed: the president resigned.", "Headlines from AP and NPR are up, though some may be outdated."])
def test_a_caveat_tacked_onto_an_announcement_or_a_claim_is_rejected(reply):
    assert not intelligence._web_content_grounded(reply, websearch.evidence_text(PACKS["multi"]))


@pytest.mark.parametrize("name, reply", [
    ("conflict", "I found conflicting reports, so I can't tell you which team won."),
    ("conflict", "Sorry sir. I found conflicting reports, so I can't tell you which team won."),
    ("nothing", "I couldn't find a reliable source for that, sir."),
    ("stale", "The results I found are old, the newest is from June 4, 2025, so I can't confirm the current answer."),
    ("single", "I can't confirm it: only one source, nvidia.com, has it."),
])
def test_a_caveat_that_is_the_whole_first_sentence_still_passes(name, reply):
    assert intelligence._web_content_grounded(reply, websearch.evidence_text(PACKS[name]))


# --- 4. "from $799" is a price ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, shown", [("The iPhone 17 starts from $799.", ["$799"]), ("Tickets sell from $45 each.", ["$45"]), ("Prices from $799 at Apple.", ["$799"]),
                                         ("The Switch costs $299, was $349.", ["$299"]), ("Gold price today is $2,700, up from $2,650 last week.", ["$2,700"])])
def test_from_is_a_price_lead_in_but_was_than_and_up_from_are_old_prices(text, shown):
    assert [s for _, s in webanswer.figures("money", text)][:len(shown)] == shown


def test_two_sources_saying_from_the_same_price_agree():
    assert _units_status("how much is the iPhone 17", "The iPhone 17 starts from $799.", "Prices from $799 at Apple.") == "agree"


# --- 5. YouTube says what it left out ------------------------------------------------------------------------------------------------------------------------
def test_the_youtube_reply_says_which_clause_was_left_out(brain):
    said = []
    brain.ask("Look up three Blender tutorials on YouTube and my wife's email password", said.append)
    assert [c for c in brain.state.calls if c[0] == "find_youtube_tutorials"][0][1]["topic"] == "Blender tutorials"
    assert "left out" in " ".join(said) and "wife" in " ".join(said)


# --- family words -----------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("line", ["google restaurants where I can go tonight with the wife", "search for movies the kids can watch tonight",
                                  "search for weather in Springfield and the wife's birthday", "search for places to eat with my girlfriend tonight"])
def test_a_clause_naming_the_wife_or_the_kids_is_never_sent(household, line):
    qs = _queries(line)
    assert all(not re.search(r"\b(?:wife|kids|girlfriend|I can|can go)\b", q) for q in qs)


def test_a_restaurant_search_keeps_its_public_part_and_the_time(household):
    assert _queries("google restaurants where I can go tonight with the wife") == ["restaurants November 15, 2026"]
    assert len(_queries("google mom jeans")) == 1 and len(_queries("search for gifts for kids")) == 1


# =====================================================================================================================
# Review round 6 (2026-11-15): from-to prices, public family questions, caveats anywhere in the first sentence, rounded figures
# =====================================================================================================================
@pytest.mark.parametrize("a, b, new", [
    ("Gold increased from $2,300 to $2,350 an ounce.", "Gold went from $2,300 to $2,350 per ounce.", "$2,350"),
    ("Tesla stock increased from $250 to $262.", "Tesla stock went from $250 to $262.", "$262"),
    ("Bitcoin has gone from $95,000 to $98,400.", "Bitcoin moved from $95,000 to $98,400.", "$98,400"),
    ("Bitcoin surged from $95,000 to $98,400 on Friday.", "BTC went from $95,100 to $98,450 this week.", "$98,400"),
    ("Bitcoin went from $95,000 to $98,400.", "Bitcoin trades at $98,400.", "$98,400"),
    ("Bitcoin jumped from $95,000 to $98,400 today.", "Bitcoin is $98,400.", "$98,400"),
    ("Gas rose from $3.10 to $3.25 a gallon.", "Gas costs $3.25 a gallon.", "$3.25"),
])
def test_the_old_price_of_a_from_to_is_not_the_price(a, b, new):
    q = "what is the price of bitcoin"
    p = P(q, S("https://a.com/x", a, TODAY_ISO), S("https://b.com/y", b, TODAY_ISO))
    r = webanswer.assess(p, q, TODAY)
    assert r["status"] == "agree" and new in str(r["agreed"]), r


@pytest.mark.parametrize("text, first", [("Bitcoin surged from $95,000 to $98,400.", "$98,400"), ("Bitcoin went from $95,000 down to $94,000.", "$94,000"),
                                         ("Prices range from $799 to $1,199.", "$799"), ("The iPhone 17 starts from $799.", "$799"),
                                         ("Tickets from $45 to see it.", "$45"), ("Prices are between $799 and $1,199.", "$799")])
def test_the_new_price_after_to_is_the_headline_but_a_range_and_a_starts_from_stay(text, first):
    assert webanswer.figures("money", text)[0][1] == first


@pytest.mark.parametrize("line", ["search for who is the wife of Jeff Bezos", "search the web for who is the father of the internet",
                                  "look up who plays the mother in Frozen", "google the husband of Beyonce", "search for mom jeans", "search for dad bod",
                                  "search for baby shark", "search for kids menu", "search for the kids menu at Chick-fil-A"])
def test_a_public_question_with_a_family_word_is_searched(household, line):
    assert len(_queries(line)) == 1 and not webintent.nothing_to_search("search", line).startswith("I won't")


# --- 3. a caveat anywhere in the first sentence ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name, reply", [
    ("multi", "Sorry sir, I found 2 sources but nothing numeric to cross-check, so this is what they say, not a confirmed fact."),
    ("stale", "Sorry sir, the newest result is from last year, so I can't confirm the current answer."),
    ("stale", "Honestly sir, the newest is from over a year ago, so it may be out of date."),
    ("single", "It's from nvidia.com itself, sir, the primary source, but I found no second source to confirm it."),
])
def test_an_honest_caveat_later_in_its_sentence_passes(name, reply):
    assert intelligence._web_content_grounded(reply, websearch.evidence_text(PACKS[name]))


@pytest.mark.parametrize("reply", ["I couldn't confirm it, but I pulled up the headlines from AP, sir.", "I pulled up the headlines from AP and NPR; I can't tell you more.",
                                   "The president resigned today, but I can't confirm it.", "Headlines from AP and NPR are up, though some may be outdated.",
                                   "The Lakers won 112-98 last night, but I can't confirm it.", "I can't say the president resigned.",
                                   "The president announced he will say goodbye, and I can say that is outdated.", "Nothing reliable? I pulled headlines from AP.",
                                   "I pulled up today's headlines, sir. The sources disagree on nothing."])
def test_the_gaming_replies_are_still_rejected(reply):
    assert not intelligence._web_content_grounded(reply, websearch.evidence_text(PACKS["multi"]))


# --- 4. rounded figures and one-decimal figures -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("a, b", [("The population is about 330 million.", "The population is 331,449,281."), ("The population is 8,300,000.", "The population is 8,336,817."),
                                  ("The population is 330M.", "The population is 331,449,281."), ("The population is 2.1 million.", "The population is 2,100,000."),
                                  ("It is 1.8 m tall.", "It is 1.85 m tall.")])
def test_a_rounded_figure_agrees_with_the_precise_one_it_rounds(a, b):
    assert _units_status("how many people live there" if "population" in a else "how tall is he", a, b) == "agree"


@pytest.mark.parametrize("q, a, b", [("how many people live there", "The population is 20 million.", "The population is 24 million."),
                                     ("how many are there", "There are 20 of them.", "There are 24 of them."),
                                     ("how far is it", "It is 20 km away.", "It is 30 km away."),
                                     ("how tall is he", "He is 1.8 m tall.", "He is 1.9 m tall."), ("how big is the download", "It is 1.0 GB.", "It is 1.1 GB."),
                                     ("how tall is he", "He is 2.1 m tall.", "He is 2.2 m tall."),
                                     ("how many people live there", "The population is 8,000,000.", "The population is 9,336,817.")])
def test_short_round_figures_and_one_decimal_figures_are_compared_exactly(q, a, b):
    assert _units_status(q, a, b) == "conflict"


# =====================================================================================================================
# Review round 7 (2026-11-15): rounded figures within 3%, ranges, conservative family words, caveat-only caveats
# =====================================================================================================================
@pytest.mark.parametrize("a, b", [("1,000", "1,499"), ("1,000,000", "1,400,000"), ("100 million", "140,000,000"), ("3,000", "3,400"), ("10,000", "14,000"),
                                  ("20,000", "24,000"), ("5,000", "5,480"), ("1 million", "1,400,000"), ("1 billion", "1,400,000,000"), ("1,000", "1,049"),
                                  ("100,000", "140,000"), ("500,000", "540,000")])
def test_a_round_figure_is_not_the_same_as_one_far_from_it(a, b):
    assert _units_status("how many people attended", f"About {a} people attended.", f"{b} people attended.") == "conflict"


@pytest.mark.parametrize("a, b", [("330 million", "331,449,281"), ("8,300,000", "8,336,817"), ("2,500", "2,540"), ("1,000,000", "1,000,100"),
                                  ("1 million", "1,020,000")])
def test_a_round_figure_agrees_with_the_longer_one_within_three_percent(a, b):
    assert _units_status("how many people attended", f"About {a} people attended.", f"{b} people attended.") == "agree"


# --- 2. a range is a range; only a change verb before "from" makes the first figure the old price -----------------------------------------------------
@pytest.mark.parametrize("a, b", [
    ("The iPhone 17 costs from $799 to $1,199.", "The iPhone 17 starts at $799."),
    ("Plans start at $10 to $15 per month.", "Plans start at $10 per month."),
    ("Tickets from $30 to $40.", "Tickets are $30."),
    ("It costs $5 to $10 to ship.", "It costs $5 to ship."),
    ("Shipping is free for orders $50 to $100.", "Shipping costs $50."),
    ("Expect to pay $799 up to $1,199.", "The iPhone costs $799."),
    ("Tickets range from $30 to $40.", "Tickets are $40."),
    ("Tickets go for between $30 and $40.", "Tickets are $30."),
    ("The cost is $799, up to $1,199 with more storage.", "The iPhone 17 starts at $799."),
    ("Prices run from $799 up to $1,199.", "The iPhone 17 starts at $799."),
    ("Down from $999 to $799, the iPhone 17 is cheaper.", "The iPhone 17 costs $799."),
    ("Price dropped from $999 to $799.", "The iPhone 17 costs $799."),
    ("Bitcoin went from $95,000 to $98,400.", "Bitcoin trades at $98,400."),
    ("The price has gone from $95,000 to $98,400.", "Bitcoin trades at $98,400."),
])
def test_a_price_range_agrees_with_either_end_and_a_change_is_the_new_price(a, b):
    assert _units_status("how much does it cost", a, b) == "agree"


def test_a_range_still_conflicts_with_a_price_outside_it():
    assert _units_status("how much does it cost", "The iPhone 17 costs from $799 to $1,199.", "The iPhone 17 costs $900.") == "conflict"


# --- 3. family words are private unless they are plainly the public thing -----------------------------------------------------------------------------------
@pytest.mark.parametrize("line", ["search for allergy medicine for the kids", "search for pediatricians for the baby", "search for restaurants near the kids school",
                                  "search for flights for me and the wife", "search for hotels for the wife and me", "search for a birthday present for the wife",
                                  "search for movies to watch with the kids", "search the web for a gift for the husband", "search for the wife needs a dress",
                                  "search for the grandma is in the hospital", "search for the family doctor", "search for tutors for the daughter"])
def test_a_family_member_in_a_search_is_never_sent_and_me_never_stays(line):
    for q in _queries(line):
        assert not re.search(r"\b(?:wife|husband|kids?|baby|son|daughter|grandma|family|me)\b", q, re.I), (line, q)


@pytest.mark.parametrize("line", ["search for who is the wife of Jeff Bezos", "search the web for who is the father of the internet",
                                  "look up who plays the mother in Frozen", "google the husband of Beyonce", "search for mom jeans", "search for dad bod",
                                  "search for baby shark", "search for the baby shark song lyrics", "search for kids menu", "search for the kids menu at Chick-fil-A",
                                  "search for the mother of dragons actress", "search for the sister act cast", "search for pizza near me"])
def test_the_public_things_with_a_family_word_are_still_searched(line):
    assert len(_queries(line)) == 1


# --- the caveat sentence may contain only caveat words -----------------------------------------------------------------------------------------------------
LAKERS = _pk("who won the lakers game last night", ("https://espn.com/a", "Lakers beat Celtics", "The Lakers beat the Celtics 112-98 on Tuesday night.", TODAY_ISO),
             ("https://nba.com/b", "Lakers top Celtics", "Los Angeles won 108-101 over Boston.", TODAY_ISO))


@pytest.mark.parametrize("reply", ["The Lakers won, though I can't confirm it.", "The Lakers won, which I can't confirm.", "I can't confirm it, but the Lakers won 112-98.",
                                   "The sources disagree, but the Lakers won by 14 points.", "I can't confirm it; the Lakers beat the Celtics.", "The Lakers won, the sources disagree.",
                                   "Unconfirmed: Lakers won.", "Sorry sir, I can't tell you, but the Lakers clearly won.", "The sources disagree, so the Lakers probably won.",
                                   "The sources disagree; the Lakers won.", "Honestly the Lakers won and I can't confirm that.", "I can't tell you, sir. The Lakers won.",
                                   "Sources disagree so I can't say, and I'll check again later.", "The reports conflict, and the Warriors won 120-110."])
def test_a_caveat_sentence_that_also_states_a_claim_is_rejected(reply):
    assert not intelligence._web_content_grounded(reply, websearch.evidence_text(LAKERS))


@pytest.mark.parametrize("reply", ["I found conflicting reports, sir, so I can't tell you which team won.", "The sources disagree on the score, sir, so I can't tell you who won.",
                                   "ESPN and NBA.com disagree, so I can't tell you who won.", "I found conflicting reports about the Lakers game, so I can't tell you who won, sir.",
                                   "Sorry sir, I found conflicting reports on the Lakers-Celtics game, so I can't tell you who won.",
                                   "The Lakers-Celtics results conflict, sir, so I can't say who won.",
                                   "Espn.com says the Lakers won 112-98, but nba.com says 108-101, so I can't tell you which is right."])
def test_an_honest_conflict_reply_is_still_accepted(reply):
    assert intelligence._web_content_grounded(reply, websearch.evidence_text(LAKERS))


def test_a_figure_written_two_ways_is_one_figure():
    p = P("how much money", S("https://a.com/x", "They raised $2.5M.", TODAY_ISO), S("https://b.com/y", "They raised $2.5 million.", TODAY_ISO))
    a = webanswer.assess(p, "how much money", TODAY)
    assert a["status"] == "agree" and " to " not in str(a["agreed"])


@pytest.mark.parametrize("reply", ["The results I found are old, sir, so I can't confirm the current price.", "The results I found are all old, so I can't confirm today's price.",
                                   "I couldn't find a current price, sir; the results are out of date."])
def test_a_price_caveat_is_still_accepted(reply):
    p = _pk("what is the price of bitcoin today", ("https://x.com/a", "Bitcoin price", "Bitcoin trades at $98,400.", "2025-01-01"),
            ("https://y.com/a", "BTC", "BTC at $97,000.", "2025-01-01"))
    assert intelligence._web_content_grounded(reply, websearch.evidence_text(p))
