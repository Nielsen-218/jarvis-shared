"""Movie expert: what's in theaters, what's coming, what's new to stream, and the lowdown on any movie
(rating, length, cast, where to watch), plus whether it's already on your Jellyfin.

Uses TMDB (The Movie Database) when TMDB_API_KEY is in .env (a free key from themoviedb.org: Settings > API);
otherwise it falls back to free web search, which works fine, just less structured.
"""
import json
import os
import urllib.parse
import urllib.request

from tools.registry import tool

TMDB = "https://api.themoviedb.org/3"


def _key() -> str:
    return os.getenv("TMDB_API_KEY", "").strip()


def _tmdb(path: str, **params) -> dict:
    params = {"api_key": _key(), "language": "en-US", **params}
    url = f"{TMDB}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _web(query: str, objective: str) -> str:
    from tools.reach import search_web_free

    return search_web_free(query, objective, 6)


def _line(m: dict) -> str:
    year = (m.get("release_date") or "")[:10]
    score = m.get("vote_average")
    return f"{m.get('title')} ({year})" + (f", rated {score:.1f}/10" if score else "") + \
        (f": {m['overview'][:160]}" if m.get("overview") else "")


@tool(
    "What movies are out: in theaters now, coming soon, or new on streaming. Use for 'what movies are out?', "
    "'anything good in theaters?', 'what's coming out next month?'.",
    {"which": {"type": "string", "enum": ["in theaters", "coming soon", "new on streaming"]}},
)
def movies_out(which: str) -> str:
    if _key() and which != "new on streaming":
        data = _tmdb("/movie/now_playing" if which == "in theaters" else "/movie/upcoming", region="US")
        movies = sorted(data.get("results", []), key=lambda m: -(m.get("popularity") or 0))[:10]
        return f"{which.capitalize()} (TMDB):\n" + "\n".join(f"- {_line(m)}" for m in movies)
    q = {"in theaters": "movies in theaters this week box office", "coming soon": "upcoming movie release dates this month",
         "new on streaming": "new movies on Netflix Max Disney+ Prime Video this week"}[which]
    return _web(q, f"a current list of {which} movies with release dates and reviews")


@tool(
    "Everything about one movie: year, rating, length, genre, director/cast, plot, where to stream or rent it in "
    "the US, and whether it's already on the family's Jellyfin server.",
    {"title": {"type": "string"}},
)
def movie_info(title: str) -> str:
    lines = []
    if _key():
        found = _tmdb("/search/movie", query=title).get("results", [])
        if found:
            m = _tmdb(f"/movie/{found[0]['id']}", append_to_response="credits,watch/providers")
            cast = ", ".join(c["name"] for c in m.get("credits", {}).get("cast", [])[:5])
            director = next((c["name"] for c in m.get("credits", {}).get("crew", []) if c.get("job") == "Director"), "")
            us = m.get("watch/providers", {}).get("results", {}).get("US", {})
            where = ", ".join(sorted({p["provider_name"] for k in ("flatrate", "free", "ads") for p in us.get(k, [])}))
            rent = ", ".join(sorted({p["provider_name"] for k in ("rent", "buy") for p in us.get(k, [])})[:4])
            lines.append(f"{m['title']} ({(m.get('release_date') or '')[:4]}), {m.get('runtime') or '?'} min, "
                         f"{', '.join(g['name'] for g in m.get('genres', []))}, rated {m.get('vote_average', 0):.1f}/10.")
            lines.append(f"Director: {director}. Starring: {cast}.")
            lines.append(m.get("overview", ""))
            lines.append(f"Stream: {where or 'not on subscription services'}" + (f". Rent/buy: {rent}." if rent else "."))
    if not lines:
        lines.append(_web(f"{title} movie review rating runtime where to watch",
                          "the film's year, rating, runtime, plot and US streaming availability"))
    try:
        from tools.homelab import jellyfin_search

        mine = jellyfin_search(title)
        if not mine.startswith("Nothing"):
            lines.append(f"On your Jellyfin: {mine.splitlines()[0]}")
    except Exception:
        pass
    return "\n".join(lines)
