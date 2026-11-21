"""Does the REAL local model (qwen3.5:9b through Ollama, no cloud) say what the search results and the computed verdict say?

    python -m scripts.web_pro_eval [scenario ...]      # one at a time; each is a fresh Brain

The search is stubbed (fixed results, never the network); the Brain, the local model, the evidence text and the rules for
answering are the real ones. Each scenario checks that the spoken answer names its source, copies the figure as printed,
says "one source" / "conflicting" / "out of date" / "nothing reliable" when the verdict is that, and invents no number
that is not in the results. Exit code 1 when a check failed. Sandboxed data folder (nothing of his is read or written)."""
import os
import re
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_LOCAL_ONLY"] = "1"
for _k, _val in (("JARVIS_LOCAL_MODEL", "qwen3.5:9b"), ("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1"), ("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434/v1"),
                 ("JARVIS_LOCAL_FAST_PROVIDER", "ollama"), ("JARVIS_LOCAL_FAST_MODEL", "qwen3.5:9b"), ("JARVIS_LOCAL_ADVANCED_MODEL", "qwen3.5:9b")):
    os.environ.setdefault(_k, _val)                        # the production brain (one model, Ollama); a worktree has no .env
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
    os.environ.pop(_v, None)

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-webpro-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
os.environ["JARVIS_COMPANION_DATA_DIR"] = str(storage.DATA / "companion")
os.environ["JARVIS_PC_STATE_DIR"] = str(storage.DATA / "pc-control")
os.environ["JARVIS_PC_ROOT"] = str(storage.DATA / "jarvis_pc")

from brain.llm import Brain  # noqa: E402
from core import users, websearch  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import load_all, memory, registry  # noqa: E402

load_all()
users.set_current("owner")
TODAY = date.today()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
LONG_AGO = (TODAY - timedelta(days=40)).isoformat()


def row(site, title, snippet, when=None, path="/a"):
    return {"title": title, "url": f"https://www.{site}{path}", "site": site, "date": when, "listed": None, "snippet": snippet}


SCENARIOS = {
    "agree": ("Who won the Lakers game last night?",
              [row("espn.com", "Lakers vs Celtics", "Final: Lakers 110-104 Celtics. LeBron James scored 31 points.", YESTERDAY),
               row("cbssports.com", "Lakers beat Celtics", "The Lakers beat the Celtics 110-104 on Saturday night.", YESTERDAY)],
              lambda a: "110" in a and "104" in a and re.search(r"espn|cbs", a, re.I) and not re.search(r"conflict|only one|unconfirmed|couldn't", a, re.I)),
    "conflict": ("Who won the Lakers game last night?",
                 [row("espn.com", "Lakers vs Celtics", "Final: Lakers 110-104 Celtics.", YESTERDAY),
                  row("nba.com", "Lakers recap", "Lakers fall to the Celtics 108-104 in Boston.", YESTERDAY)],
                 lambda a: re.search(r"conflict|disagree|differ|not sure which|can't tell", a, re.I) and "110" in a and "108" in a),
    "single": ("What's the latest NVIDIA driver version?",
               [row("driverbeat.net", "NVIDIA driver 621.01 released", "NVIDIA released GeForce driver version 621.01 WHQL for RTX 50 series.", YESTERDAY)],
               lambda a: "621.01" in a and re.search(r"one source|only|unconfirmed|single|can.t confirm|not confirmed|no second|haven.t confirmed|no other|that source", a, re.I)),
    "primary": ("What's the latest NVIDIA driver version?",
                [row("nvidia.com", "GeForce Game Ready Driver 621.01", "Version 621.01 WHQL, released for GeForce RTX 50 series.", YESTERDAY)],
                lambda a: "621.01" in a and re.search(r"nvidia", a, re.I)),
    "stale": ("Who won the Lakers game last night?",
              [row("espn.com", "Lakers vs Celtics", "Final: Lakers 98-91 Celtics.", LONG_AGO)],
              lambda a: re.search(r"old|out of date|can't confirm|couldn't|not current|outdated", a, re.I) and not re.search(r"\b98\b.{0,40}(?:last night|yesterday)", a, re.I)),
    "who": ("Who is the CEO of Nvidia?",
            [row("wikipedia.org", "Nvidia", "Nvidia Corporation is an American technology company. Jensen Huang is its president and chief executive officer.", YESTERDAY),
             row("nvidia.com", "Leadership", "Jensen Huang founded NVIDIA in 1993 and serves as president and CEO.", None)],
            lambda a: "Jensen Huang" in a and re.search(r"wikipedia|nvidia", a, re.I)),
    "weather": ("What's the weather in Springfield tomorrow?",
                [row("weather.gov", "Springfield IL forecast", "Tomorrow: sunny, with a high near 62°. Low around 45°.", TODAY.isoformat()),
                 row("accuweather.com", "Springfield weather", "Sunny tomorrow with a high of 62° and a low of 44°.", TODAY.isoformat())],
                lambda a: "62" in a and re.search(r"weather\.gov|accuweather|national weather", a, re.I)),
}


def main(names) -> int:
    cfg = Config()
    failed = 0
    for name in names or SCENARIOS:
        question, rows, ok = SCENARIOS[name]
        ran = []

        def run(tool, args, _rows=rows):
            if tool in ("search_internet", "research_online"):
                q = args.get("query") or args.get("question")
                pack = {"question": q, "query": q, "provider": "exa", "results": list(_rows), "failures": [],
                        "searched_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "cached": False}
                ran.append(q)
                websearch.remember("owner", "search", pack)
                return websearch.evidence_text(pack), False
            return real_run(tool, args)

        real_run = registry.run
        registry.run = run
        try:
            brain = Brain(cfg.model, cfg.effort, load_personality(cfg, "owner"), uid="owner")
            brain.context = memory.context_block
            said = []
            t = time.time()
            brain.ask("Jarvis, google " + question[0].lower() + question[1:], said.append)
            answer = " ".join(said)
        finally:
            registry.run = real_run
        evidence = " ".join(r["snippet"] + " " + r["title"] for r in rows)
        invented = [n for n in re.findall(r"\b\d[\d,.]*\b", answer) if n.strip(".,") not in evidence and n.strip(".,") not in str(TODAY)
                    and n.strip(".,") not in YESTERDAY and n.strip(".,") not in LONG_AGO]
        good = bool(ok(answer)) and bool(ran) and not invented
        failed += not good
        print(f"[{'PASS' if good else 'FAIL'}] {name} ({time.time() - t:.1f}s) searched={ran}\n  JARVIS: {answer[:600]}"
              + (f"\n  numbers not in the results: {invented}" if invented else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
