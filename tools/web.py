"""Live internet research skills: search with sources, bounded multi-page research, page/PDF reading, YouTube
tutorial discovery, and keeping what was found (core/websearch.py does the work; core/webintent.py routes the
clear spoken requests to these deterministically, before any model)."""
from core import users, websearch
from tools.registry import tool


@tool(
    "Search the live internet when they ask to google something, look it up, search online, check the web or find "
    "the latest version/news/driver. Returns numbered results with title, site, publication date and the exact URL. "
    "Answer from the results and name the source; never answer from memory when they asked for a search.",
    {"query": {"type": "string", "description": "what to search for, e.g. 'latest NVIDIA driver RTX 5070 Ti'"},
     "objective": {"type": "string", "description": "what they need from the results, e.g. 'the version number and date'"}},
    required=["query"], slow=True,
)
def search_internet(query: str, objective: str = "") -> str:
    try:
        pack = websearch.search(query, 5, objective)
    except websearch.SearchUnavailable as e:
        return websearch.unavailable_text(query, e)
    except InterruptedError:
        return websearch.paused_text(query)
    websearch.remember(users.current(), "search", pack)
    return websearch.evidence_text(pack)


@tool(
    "Research a question across several web pages: searches, opens the best few results, and returns excerpts with "
    "each source's title, site, date and URL. For questions that need more than a headline (an error's fix, a "
    "comparison, how something works). Bounded: one search, at most four pages.",
    {"question": {"type": "string"}, "objective": {"type": "string", "description": "what a good answer must contain"},
     "pages": {"type": "integer", "description": "pages to read, 1-4 (default 3)"}},
    required=["question"], slow=True,
)
def research_online(question: str, objective: str = "", pages: int = 3) -> str:
    try:
        pack = websearch.research_question(question, pages, objective)
    except websearch.SearchUnavailable as e:
        return websearch.unavailable_text(question, e)
    except InterruptedError:
        return websearch.paused_text(question)
    websearch.remember(users.current(), "research", pack)
    return websearch.evidence_text(pack)


@tool(
    "Open one public web page or PDF link and read its text (title, date when stated, content). Reports pages that "
    "can't be read (login walls, blocked bots, redirects) instead of guessing their content.",
    {"url": {"type": "string"}}, required=["url"], slow=True,
)
def open_web_page(url: str) -> str:
    try:
        page = websearch.open_page(url)
    except websearch.PageUnavailable as e:
        return (f"I couldn't read {e.url}: " + "; ".join(f"{p} ({r})" for p, r in e.failures)
                + ". I haven't inferred what it says.")
    except (ValueError, OSError) as e:
        return f"I couldn't open that link: {e}"
    head = f"{page['title']} — {websearch._host(page['url'])} ({'published ' + page['date'] if page.get('date') else 'date not stated'}; read via {page['provider']})\nURL: {page['url']}\n"
    return head + page["text"][:12000] + (" [cut short]" if len(page["text"]) > 12000 else "")


@tool(
    "Find YouTube tutorials on a topic independently (no URL needed): searches YouTube, returns each video's title, "
    "channel, upload date, length, exact link and whether English captions exist, and saves the references for "
    "later study. Does not read or watch the videos: use youtube_transcript for a transcript.",
    {"topic": {"type": "string", "description": "e.g. 'Blender rigging'"},
     "count": {"type": "integer", "description": "how many, 2-5 (default 3)"}},
    required=["topic"], slow=True,
)
def find_youtube_tutorials(topic: str, count: int = 3) -> str:
    try:
        pack = websearch.youtube_tutorials(topic, count)
    except InterruptedError:
        return websearch.paused_text(topic)
    except Exception as e:                                    # noqa: BLE001 - the reason is the answer
        reason = websearch._reason(e)
        why = ("yt-dlp is not installed on this machine" if "not installed" in reason else reason)
        return (f"{websearch.MARK_NOSEARCH} YouTube: {why}. I did not search, so I have no tutorials to show for "
                f"\"{topic}\". " + websearch.offline_recall(topic))
    websearch.remember(users.current(), "youtube", pack)
    return websearch.evidence_text(pack)


@tool(
    "Keep what was just found online: 'learn this', 'save that finding', 'add it to your library'. Saves the last "
    "search, research or tutorial results into the Learning Library as RETRIEVED, UNVERIFIED reference material "
    "(found again offline later). Reading is not mastery; practice happens through the sandbox skills.",
    {"title": {"type": "string", "description": "optional title for the saved finding"}}, direct=True,
)
def save_research(title: str = "") -> str:
    return websearch.save_finding(users.current(), title)


@tool(
    "Recall saved research offline, without searching the internet: what Jarvis found or saved earlier about a "
    "topic (research store and Learning Library), with its source URLs and the date it was saved.",
    {"query": {"type": "string"}}, required=["query"], direct=True,
)
def recall_research(query: str) -> str:
    return websearch.offline_recall(query)
