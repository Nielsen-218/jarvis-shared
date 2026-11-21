"""The live-research layer: provider fallback order, honest failure when nothing can search, evidence with sources,
retention in the research store and the Learning Library, offline recall, and the brain's deterministic path.
Providers are stubbed here (no internet in the suite); scripts/convo_local.py research exercises the real ones."""
import json
from datetime import datetime, timezone

import pytest

import tools  # noqa: F401
from core import intelligence, research, storage, users, webintent, websearch


@pytest.fixture(autouse=True)
def _pinned_day(monkeypatch):
    """The query that goes out carries today's date (core/webanswer.rewrite); these tests pin the day."""
    from datetime import date
    from core import webanswer
    monkeypatch.setattr(webanswer, "today", lambda: date(2026, 11, 15))
from core.intelligence import LocalFailure, parse_reply

ROWS = [{"title": "GeForce Game Ready Driver 621.01 | NVIDIA", "url": "https://www.nvidia.com/en-us/drivers/details/282412/",
         "snippet": "Game Ready Driver 621.01 for GeForce RTX 50 series", "date": "2026-10-14"},
        {"title": "Drivers | NVIDIA", "url": "https://www.nvidia.com/en-us/drivers/", "snippet": "Download drivers", "date": None},
        {"title": "private box", "url": "http://192.168.1.5/admin", "snippet": "nope", "date": None},
        {"title": "dupe", "url": "https://www.nvidia.com/en-us/drivers/details/282412", "snippet": "same page again", "date": None}]


@pytest.fixture(autouse=True)
def _no_live_desktop(monkeypatch):
    """These tests are about web search. Without this, what happens to be in the foreground on the machine running them
    (for example a browser on YouTube) changes the route: the PC front door answers "Searching YouTube." instead."""
    from core import pcactions
    monkeypatch.setattr(pcactions, "foreground_browser", lambda: None)
    # The PC front door really opens tabs. A test must never do that on the machine running it.
    monkeypatch.setattr(pcactions, "perform", lambda *a, **k: {"verified": False, "detail": "stubbed in tests: no real browser"})


@pytest.fixture(autouse=True)
def _owner():
    users.set_current("owner")
    websearch.LAST.clear()


def _providers(monkeypatch, **behaviour):
    """Replace the chain: each name -> rows, or an exception to raise."""
    def make(name):
        def provider(query, limit, objective=""):
            value = behaviour.get(name, OSError("no connection"))
            if isinstance(value, BaseException):
                raise value
            return value
        return provider
    monkeypatch.setattr(websearch, "PROVIDERS", tuple((n, make(n)) for n, _ in websearch.PROVIDERS))


def test_the_first_provider_that_answers_wins_and_results_are_public_and_deduplicated(monkeypatch):
    _providers(monkeypatch, exa=FileNotFoundError("mcporter not installed"), **{"exa-direct": ROWS})
    pack = websearch.search("latest NVIDIA driver RTX 5070 Ti", 5)
    assert pack["provider"] == "exa-direct" and pack["failures"] == [("exa", "not installed on this machine")]
    assert [r["url"] for r in pack["results"]] == ["https://www.nvidia.com/en-us/drivers/details/282412/",
                                                   "https://www.nvidia.com/en-us/drivers/"]
    assert pack["results"][0]["site"] == "nvidia.com" and pack["results"][0]["date"] == "2026-10-14"
    saved = research.sources("NVIDIA driver")
    assert saved and saved[0]["modality"] == "search_results" and saved[0]["provider"] == "exa-direct"


def test_the_whole_chain_is_tried_in_order_and_every_reason_is_reported(monkeypatch):
    _providers(monkeypatch, **{"bing-rss": [], "duckduckgo": ROWS[:1]})
    pack = websearch.search("nvidia game ready driver", 3)
    assert pack["provider"] == "duckduckgo"
    assert [p for p, _ in pack["failures"]] == ["exa", "exa-direct", "bing-rss"]


def test_off_topic_results_are_not_usable_and_the_next_provider_is_tried(monkeypatch):
    """Live from the laptop: Bing RSS answered 'heat pump efficiency in cold winters' with the 1995 film Heat and the
    Miami HEAT. Such results are no answer; the chain moves on."""
    junk = [{"title": "Heat (1995 film) - Wikipedia", "url": "https://en.wikipedia.org/wiki/Heat_(1995_film)", "snippet": "A crime film", "date": None},
            {"title": "Official Site Of The Miami HEAT", "url": "https://www.nba.com/heat/", "snippet": "basketball", "date": None}]
    good = [{"title": "Heat pump efficiency in cold climates", "url": "https://example.org/heat-pumps-cold", "snippet": "COP in winter", "date": None}]
    _providers(monkeypatch, **{"bing-rss": junk, "duckduckgo": good})
    pack = websearch.search("heat pump efficiency in cold winters", 3)
    assert pack["provider"] == "duckduckgo" and ("bing-rss", "no usable results") in pack["failures"]
    assert websearch._relevant(junk + good, "heat pump efficiency in cold winters") == good
    assert websearch._relevant(ROWS[:2], "latest NVIDIA driver RTX 5070 Ti") == ROWS[:2] or len(websearch._relevant(ROWS[:2], "latest NVIDIA driver RTX 5070 Ti")) >= 1
    assert websearch._relevant(junk, "Heat") == junk                       # a one-word query keeps its matches


def test_a_link_the_evidence_never_gave_makes_the_answer_ungrounded():
    evidence = websearch.MARK_SEARCH + ' for "latest driver" via exa.\n1. GeForce 621.01 — nvidia.com (published 2026-10-21)\n   URL: https://www.nvidia.com/en-us/drivers/details/282412/'
    assert intelligence._unsourced_urls("Download it at https://www.nvidia.com/en-us/drivers/details/282412/.", evidence) == []
    assert intelligence._unsourced_urls("It is at https://nvidia.com/en-us/drivers/details/282412", evidence) == []
    assert intelligence._unsourced_urls("See https://www.nvidia.com/en-us/drivers/details/621.01/", evidence) == ["https://www.nvidia.com/en-us/drivers/details/621.01/"]
    history = [{"role": "user", "content": "search the internet for the latest driver"},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "research_online", "input": {"question": "latest driver"}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w1", "content": evidence, "is_error": False}]}]
    assert intelligence._web_evidence(history) == evidence


def test_a_figure_the_evidence_never_gave_makes_the_answer_ungrounded():
    evidence = websearch.MARK_SEARCH + ' for "latest driver" via exa.\n1. GeForce 621.01 — nvidia.com (published 2026-10-21)\n   States: Version: 621.01; Release Date: Wed Oct 21, 2026; File Size: 1.04 GB\n   URL: https://www.nvidia.com/en-us/drivers/details/282412/'
    assert intelligence._unsourced_numbers("The latest driver is 621.01, released October 21, 2026, 1.04 GB.", evidence) == []
    assert intelligence._unsourced_numbers("The latest driver is version R537.", evidence) == ["R537"]
    assert intelligence._unsourced_numbers("Driver 622.15 is out.", evidence) == ["622.15"]
    assert intelligence._unsourced_numbers("It is 8 minutes long and costs 15 percent less.", evidence) == []   # small numbers aren't claims here


def test_found_videos_are_listed_exactly_as_found(monkeypatch):
    pack = {"topic": "Blender rigging", "cached": True, "problems": [{"url": "https://www.youtube.com/watch?v=m-Obo_nC3SM", "reason": "not installed on this machine"}],
            "videos": [{"title": "Rigging for impatient people", "channel": "Joey Carlino", "date": "2023-05-05", "duration_seconds": 602,
                        "captions_manual": [], "captions_auto_en": True, "url": "https://www.youtube.com/watch?v=DDeB4tDVCGY"},
                       {"title": "Rigging for beginners", "channel": None, "date": None, "duration_seconds": None,
                        "captions_manual": None, "captions_auto_en": None, "url": "https://www.youtube.com/watch?v=m-Obo_nC3SM"}]}
    said = websearch.listed(pack)
    assert said.startswith("I found 2 YouTube tutorials on Blender rigging (from my search earlier this hour):")
    assert "1. Rigging for impatient people by Joey Carlino, uploaded 2023-05-05, 10 minutes, auto-generated English captions. https://www.youtube.com/watch?v=DDeB4tDVCGY" in said
    assert "2. Rigging for beginners by an unknown channel, uploaded date unknown, length unknown, captions not checked. https://www.youtube.com/watch?v=m-Obo_nC3SM" in said
    assert "Details I couldn't fetch" in said and "haven't watched them" in said


def test_youtube_discovery_without_ytdlp_says_the_tool_is_missing(monkeypatch):
    from core import tutorial_learning
    from tools import web
    monkeypatch.setattr(tutorial_learning, "discover", lambda topic, limit=3: (_ for _ in ()).throw(FileNotFoundError(2, "No such file or directory", "yt-dlp")))
    out = web.find_youtube_tutorials("python decorators")
    assert websearch.unavailable(out) and "yt-dlp is not installed on this machine" in out and "did not search" in out


def test_offline_means_saying_so_not_answering_from_memory(monkeypatch):
    _providers(monkeypatch)
    with pytest.raises(websearch.SearchUnavailable) as e:
        websearch.search("anything at all", 3)
    assert [p for p, _ in e.value.failures] == ["exa", "exa-direct", "bing-rss", "duckduckgo"]
    text = websearch.unavailable_text("anything at all", e.value)
    assert websearch.unavailable(text) and "did not search" in text and "Nothing is saved locally" in text
    from tools import web
    out = web.search_internet("anything at all")
    assert websearch.unavailable(out) and "exa (no connection)" in out


def test_a_repeated_query_within_the_hour_is_served_from_the_saved_results(monkeypatch):
    calls = []
    def provider(query, limit, objective=""):
        calls.append(query)
        return ROWS
    monkeypatch.setattr(websearch, "PROVIDERS", (("exa", provider),))
    websearch.search("nvidia drivers download", 3)
    again = websearch.search("nvidia drivers download", 3)
    assert len(calls) == 1 and again["cached"] is True and len(again["results"]) == 2


def test_evidence_names_source_site_date_and_url_and_the_spoken_fallback_cites_sources(monkeypatch):
    _providers(monkeypatch, exa=ROWS)
    pack = websearch.search("latest NVIDIA driver", 5, "version and date")
    text = websearch.evidence_text(pack)
    assert text.startswith(websearch.MARK_SEARCH) and "via exa" in text
    assert "1. GeForce Game Ready Driver 621.01 | NVIDIA — nvidia.com (published 2026-10-14)" in text
    assert "URL: https://www.nvidia.com/en-us/drivers/details/282412/" in text and "Answer from these results only" in text
    said = websearch.spoken(pack)
    assert "nvidia.com" in said and "2026-10-14" in said and "exa" in said


def test_learn_this_keeps_the_last_finding_in_the_library_as_unverified_and_recall_works_offline(monkeypatch):
    _providers(monkeypatch, exa=ROWS)
    from tools import engineering, web
    assert "Nothing was found online recently" in web.save_research()
    web.search_internet("latest NVIDIA driver RTX 5070 Ti", "version")
    said = web.save_research()
    assert said.startswith("Saved \"Research: latest NVIDIA driver RTX 5070 Ti\"") and "unverified" in said
    entry = engineering.visible_index()[-1]
    assert "retrieved_unverified" in entry["tags"] and entry["source"] == "research:exa"
    doc = storage.load(f"{engineering.BASE}/docs/{entry['id']}.json", {})
    assert "STATUS: retrieved, unverified" in doc["chunks"][0]["text"] and "mastered" not in doc["chunks"][0]["text"].lower()
    # now offline: nothing is searched, the saved finding comes back with its source
    _providers(monkeypatch)
    recalled = websearch.offline_recall("NVIDIA driver")
    assert recalled.startswith("From my saved research, without searching the internet")
    assert "nvidia.com/en-us/drivers/details/282412" in recalled and "Learning Library" in recalled
    assert web.recall_research("NVIDIA driver") == recalled


def test_page_reading_falls_back_and_reports_unreadable_pages(monkeypatch):
    monkeypatch.setattr(research, "open_page", lambda url: (_ for _ in ()).throw(ValueError("Source must respond directly with HTTP 200; redirects are not followed")))
    monkeypatch.setattr(websearch, "_exa_fetch_cli", lambda url: (_ for _ in ()).throw(FileNotFoundError("mcporter not installed")))
    monkeypatch.setattr(websearch, "_exa_fetch_direct", lambda url: "Title: Fixing ModuleNotFoundError\nURL: https://example.org/fix\nPublished: 2025-03-02\nAuthor: N/A\nHighlights:\n"
                        + "Install the package into the same interpreter that runs the script: python -m pip install pyyaml. " * 3)
    page = websearch.open_page("https://example.org/fix")
    assert page["provider"] == "exa-direct" and page["date"] == "2025-03-02" and "pip install pyyaml" in page["text"]
    assert research.sources("ModuleNotFoundError")[0]["modality"] == "remote_page_extraction"
    monkeypatch.setattr(websearch, "_exa_fetch_direct", lambda url: (_ for _ in ()).throw(OSError("no connection")))
    with pytest.raises(websearch.PageUnavailable) as e:
        websearch.open_page("https://example.org/other")
    assert [p for p, _ in e.value.failures] == ["direct", "exa", "exa-direct"]
    from tools import web
    assert "I couldn't read https://example.org/other" in web.open_web_page("https://example.org/other")
    with pytest.raises(websearch.PageUnavailable):
        websearch.open_page("http://127.0.0.1:8765/")            # never a private destination


def test_multi_source_research_opens_a_bounded_number_of_pages_and_skips_video_sites(monkeypatch):
    rows = ROWS[:2] + [{"title": "RTX 5070 Ti driver video", "url": "https://www.youtube.com/watch?v=abcdefghijk", "snippet": "", "date": None},
                       {"title": "Forum: RTX 5070 Ti driver thread", "url": "https://forum.example.org/t/1", "snippet": "", "date": None}]
    _providers(monkeypatch, exa=rows)
    opened = []
    def fake_open(url):
        opened.append(url)
        if "forum" in url:
            raise websearch.PageUnavailable(url, [("direct", "HTTP 403")])
        return {"title": "T " + url, "url": url, "text": "driver 621.01 released 2026-10-14 for RTX 5070 Ti " * 20, "date": "2026-10-14", "provider": "direct"}
    monkeypatch.setattr(websearch, "open_page", fake_open)
    pack = websearch.research_question("latest driver RTX 5070 Ti", max_pages=3)
    assert len(pack["sources"]) == 1 and pack["unopened"] == [{"url": "https://forum.example.org/t/1", "reason": "PageUnavailable: https://forum.example.org/t/1: direct: HTTP 403"}]
    assert [r["url"] for r in pack["results"]] == [ROWS[0]["url"], "https://www.youtube.com/watch?v=abcdefghijk", "https://forum.example.org/t/1"]  # ("Drivers | NVIDIA" shares one word only)
    assert not any("youtube" in u for u in opened)
    text = websearch.evidence_text(pack)
    assert "Not readable: https://forum.example.org/t/1" in text and "621.01" in text


def test_youtube_discovery_reports_real_metadata_and_caption_availability_without_reading_videos(monkeypatch):
    from core import tutorial_learning
    monkeypatch.setattr(tutorial_learning, "discover", lambda topic, limit=3: {"candidates": [
        {"url": "https://www.youtube.com/watch?v=DDeB4tDVCGY", "title": "Rigging for impatient people", "creator": "Joey Carlino", "duration_seconds": 602},
        {"url": "https://www.youtube.com/watch?v=m-Obo_nC3SM", "title": "Rigging for beginners", "creator": "Joey Carlino", "duration_seconds": 3567}],
        "retrieved_at": "2026-10-30T22:00:00+00:00"})
    infos = {"DDeB4tDVCGY": "20230415\tJoey Carlino\t602\t1234567\tNA\tvtt\tRigging for impatient people - Blender Tutorial"}
    def fake_run(args, timeout=35):
        vid = args[-1].rsplit("=", 1)[1]
        if vid not in infos:
            return 1, "", "HTTP Error 429"
        return 0, infos[vid] + "\n", ""
    monkeypatch.setattr(tutorial_learning, "run_ytdlp", fake_run)
    pack = websearch.youtube_tutorials("blender rigging", 2)
    text = websearch.evidence_text(pack)
    assert text.startswith(websearch.MARK_VIDEO)
    assert "1. Rigging for impatient people - Blender Tutorial — Joey Carlino (uploaded 2023-04-15, 10 min)" in text
    assert "English captions: auto-generated" in text and "URL: https://www.youtube.com/watch?v=DDeB4tDVCGY" in text
    assert "Metadata unavailable: https://www.youtube.com/watch?v=m-Obo_nC3SM" in text and "429" in text
    assert "transcript has not been read" in text
    assert research.sources("blender rigging")[0]["modality"] == "video_metadata_only"
    assert "Joey Carlino" in websearch.spoken(pack)
    # no internet: the skill says so and invents no videos
    from tools import web
    monkeypatch.setattr(tutorial_learning, "discover", lambda topic, limit=3: (_ for _ in ()).throw(ValueError("YouTube search unavailable; no videos were studied")))
    out = web.find_youtube_tutorials("blender rigging")
    assert websearch.unavailable(out) and "did not search" in out and "youtube.com/watch" not in out.split("From my saved")[0]


def test_a_bare_phrase_never_grounds_a_web_search_but_asking_does():
    def call(name, args):
        return {"model": "local", "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "c1", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}
    for name, args in (("search_internet", {"query": "frozen pencil"}), ("search_web_free", {"query": "frozen pencil"}),
                       ("research_online", {"question": "frozen pencil"}), ("find_youtube_tutorials", {"topic": "frozen pencil"})):
        with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
            parse_reply(call(name, args), {name}, said="frozen pencil")
    assert parse_reply(call("search_internet", {"query": "latest nvidia driver"}), {"search_internet"},
                       said="what's the latest nvidia driver?").stop_reason == "tool_use"
    assert parse_reply(call("search_internet", {"query": "yaml error"}), {"search_internet"},
                       said="google the yaml module not found error").stop_reason == "tool_use"
    assert parse_reply(call("find_youtube_tutorials", {"topic": "blender rigging"}), {"find_youtube_tutorials"},
                       said="find me a blender rigging tutorial on youtube").stop_reason == "tool_use"


def test_a_clear_web_request_is_offered_only_the_web_skills():
    from tools import registry
    schemas = registry.schemas()
    offered = {t["name"] for t in intelligence.select_tools([{"role": "user", "content": "Look up three good Blender rigging tutorials on YouTube."}], schemas)}
    assert offered and offered <= intelligence.WEB_TOOLS and "blender_reference" not in offered
    offered = {t["name"] for t in intelligence.select_tools([{"role": "user", "content": "Google this: ModuleNotFoundError yaml"}], schemas)}
    assert "search_internet" in offered and offered <= intelligence.WEB_TOOLS


def test_an_answer_that_ignores_the_evidence_is_refused(monkeypatch):
    evidence = websearch.MARK_SEARCH + ' for "latest driver" via exa. 1. GeForce 621.01 — nvidia.com (published 2026-10-14)\n   URL: https://www.nvidia.com/x'
    history = [{"role": "user", "content": "search the internet for the latest driver"},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "search_internet", "input": {"query": "latest driver"}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w1", "content": evidence, "is_error": False}]}]
    assert intelligence._web_evidence(history) == evidence
    reply = lambda text: {"model": "local", "choices": [{"finish_reason": "stop", "message": {"content": text, "tool_calls": None}}]}
    monkeypatch.setattr(intelligence, "call_local_model", lambda *a, **k: None, raising=False)
    grounded = parse_reply(reply("The latest driver is 621.01, from nvidia.com, published 2026-10-14."), set(), said="search the internet for the latest driver", answering=True)
    assert "621.01" in grounded.content[0].text
    anchor = intelligence._anchor([evidence], "search the internet for the latest driver")
    assert intelligence._shares("The latest driver is 621.01, from nvidia.com.", anchor)
    assert not intelligence._shares("Your balance is roughly two thousand dollars, sir.", anchor - intelligence._anchor([], "search the internet for the latest driver"))


def test_the_brain_runs_a_clear_search_itself_and_speaks_the_sources_when_no_model_can(monkeypatch):
    """The deterministic path: 'google this' runs search_internet before any model; with every local tier failing
    and the cloud off, the sources are spoken as they are instead of 'Local intelligence is unavailable'."""
    from brain.llm import Brain
    _providers(monkeypatch, exa=ROWS)
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    monkeypatch.setattr(intelligence, "local_only", lambda: True)
    monkeypatch.setattr(intelligence, "uses_ollama", lambda role: True)
    monkeypatch.setattr(intelligence, "request", lambda *a, **k: (_ for _ in ()).throw(LocalFailure("invalid_response")))
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(websearch, "open_page", lambda url: {"title": "GeForce Game Ready Driver 621.01", "url": url, "provider": "exa",
                                                            "text": "Version: 621.01 Release Date: Wed Oct 21, 2026 for GeForce RTX 50 series", "date": None})
    brain = Brain("claude-x", "low", "You are Jarvis.", uid="owner")
    said = []
    brain.ask("Jarvis, google the latest NVIDIA driver for the RTX 5070 Ti", said.append)
    spoken = " ".join(said)
    assert "I searched the web for latest NVIDIA driver for the RTX 5070 Ti 2026 through exa" in spoken and "nvidia.com" in spoken
    uses = [b for m in brain.messages for b in (m["content"] if isinstance(m["content"], list) else []) if isinstance(b, dict) and b.get("type") == "tool_use"]
    assert uses and uses[0]["name"] == "research_online" and uses[0]["input"]["question"] == "latest NVIDIA driver for the RTX 5070 Ti 2026"
    evidence = brain.messages[-2]["content"][0]["content"]
    assert "Release Date: Wed Oct 21, 2026" in evidence and "URL: https://www.nvidia.com/en-us/drivers/details/282412/" in evidence
    said.clear()
    _providers(monkeypatch)                                       # offline now: say so, offer what is saved
    brain.ask("Look up the Blender 5.0 release date online.", said.append)
    assert websearch.unavailable(" ".join(said)) and "did not search" in " ".join(said)
    said.clear()
    brain.ask("What did you find about the NVIDIA driver?", said.append)
    assert "without searching the internet" in " ".join(said) and "nvidia.com" in " ".join(said)
    said.clear()
    brain.ask("Learn this.", said.append)
    assert " ".join(said).startswith("Saved \"Research: latest NVIDIA driver")


def test_the_spoken_fallback_quotes_what_the_best_page_says_and_adds_nothing():
    pack = {"question": "latest driver", "provider": "bing-rss", "results": [], "sources": [
        {"title": "Drivers Details", "url": "https://www.nvidia.com/d/1/", "site": "nvidia.com", "date": None,
         "facts": ["Version: 621.01", "Release Date: Wed Oct 21, 2026"], "excerpt": "ignored when facts exist"}]}
    said = websearch.spoken(pack)
    assert "nvidia.com says: Version: 621.01; Release Date: Wed Oct 21, 2026." in said and "ignored" not in said
    pack["sources"][0].update(facts=[], excerpt="pip install pyyaml " * 40)
    said = websearch.spoken(pack)
    assert "nvidia.com says: pip install pyyaml" in said and "...  " not in said and len(said) < 600
    pack["sources"][0].update(excerpt="")
    assert " says: " not in websearch.spoken(pack)


def test_sponsored_redirect_links_are_never_results():
    rows = [{"title": "Cheap Soldering Iron - We Reviewed Every Model",
             "url": "https://duckduckgo.com/y.js?ad_domain=bestreviews.com&ad_provider=bingv7aa&ad_type=txad"},
            {"title": "Ad", "url": "https://www.bing.com/aclk?ld=abc"},
            {"title": "Best soldering irons for beginners", "url": "https://example.org/soldering"}]
    assert [r["url"] for r in websearch._usable(rows)] == ["https://example.org/soldering"]


def test_a_cached_search_saved_with_an_ad_link_drops_it(monkeypatch):
    ad = {"title": "Cheap Soldering Iron", "url": "https://duckduckgo.com/y.js?ad_domain=x.com", "site": "duckduckgo.com"}
    good = {"title": "Best soldering irons", "url": "https://example.org/s", "site": "example.org"}
    monkeypatch.setattr(websearch.research, "sources", lambda q: [{
        "modality": "search_results", "query": "soldering iron", "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "results": [ad, good], "provider": "duckduckgo", "id": "r1"}])
    hit = websearch._cached("soldering iron", 5)
    assert [r["url"] for r in hit["results"]] == ["https://example.org/s"] and hit["cached"] is True
