"""Replay the owner's REAL spoken lines (the `YOU:` lines of data/jarvis.log, read only) through the web matchers and print
every hit, so a false positive shows up before it reaches him:

  kind        webintent.kind: the line is a search / news / youtube / recall / learn request (and the query that would go out)
  lookup      webintent.lookup_queries: the worldly question that would be searched if the model did not know the answer
  follow-up   webanswer.followup against a stand-in search (at runtime it needs a web answer straight before it)

    python -m scripts.web_replay [path-to-log] [--quiet]

Nothing is searched and no model is called. Household names are not read from his data: the stand-in set below is used."""
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_LOG = Path("C:/Users/you/ECC/jarvis/data/jarvis.log")
STAND_IN = {"owner": {"name": "Alex"}, "maya": {"name": "Maya"}, "mom": {"name": "Mom"}}


def lines(path: Path) -> list[str]:
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"YOU: (.*)$", raw)
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
    return out


def reachable(said: str) -> bool:
    """The Brain only looks a line up when it ASKS something about the world (brain.llm.Brain._unknown_for): a question mark or
    a question's wording, and nothing about him or his household."""
    from core import intelligence, webintent
    said = said.replace("’", "'")
    worldly = re.sub(r"(?i)\b(?:tell|show|give|let|get|find|read|help) me\b", " ", said)
    about_him = (intelligence.about_them(worldly) or bool(intelligence._PERSONAL.search(worldly)) or webintent.household_named(said))
    return ("?" in said or intelligence.just_asking(said)) and not about_him and len(said.split()) >= 3


def run(path: Path, quiet: bool = False) -> dict:
    from core import users, webanswer, webintent
    users.all_users = lambda: STAND_IN                    # never his real household file
    webanswer.today = lambda: date(2026, 11, 15)
    fake = {"question": "Who won the Lakers game November 14, 2026", "results": [
        {"title": "a", "url": "https://espn.com/a", "site": "espn.com", "date": "2026-11-14", "snippet": "x 110-104"},
        {"title": "b", "url": "https://cbssports.com/b", "site": "cbssports.com", "date": "2026-11-14", "snippet": "x 110-104"}]}
    counts = {"lines": 0, "kind": 0, "lookup": 0, "followup": 0}
    for said in lines(path):
        counts["lines"] += 1
        kind = webintent.kind(said)
        if kind:
            counts["kind"] += 1
            try:
                calls = webintent.calls_for(kind, said) if kind not in ("learn", "recall") else []
            except Exception as e:                        # noqa: BLE001
                calls = [("error", str(e))]
            if not quiet:
                print(f"KIND   [{kind}] {said[:150]!r}\n         -> {[(c[0], c[1].get('query') or c[1].get('question') or c[1].get('topic')) for c in calls]}")
        queries = webintent.lookup_queries(said) if reachable(said) else []
        if queries:
            counts["lookup"] += 1
            if not quiet:
                print(f"LOOKUP {said[:150]!r}\n         -> {[webanswer.rewrite(q) for q in queries]}")
        fu = webanswer.followup(said, fake, date(2026, 11, 15), veto=lambda n: webintent.household_named(n) or webintent.private_line(n, strict=False))
        if fu:
            counts["followup"] += 1
            if not quiet:
                print(f"FOLLOW {said[:150]!r}\n         -> {fu}")
    return counts


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    log = Path(args[0]) if args else DEFAULT_LOG
    result = run(log, quiet="--quiet" in sys.argv)
    print(f"\n{result['lines']} spoken lines: {result['kind']} web requests, {result['lookup']} would be looked up if unknown, "
          f"{result['followup']} read as a follow-up (only straight after a web answer)")
