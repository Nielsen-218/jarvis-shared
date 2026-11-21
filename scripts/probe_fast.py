"""Why does the fast local tier reject its own replies? Run real request lines through it and keep every raw reply
that parse_reply refused, so the guard that refused it can be judged (and fixed) on evidence, not guesses.

    .venv\\Scripts\\python.exe scripts\\probe_fast.py                 # built-in lines (the owner's real ones, sanitized)
    .venv\\Scripts\\python.exe scripts\\probe_fast.py lines.txt       # one request per line
    JARVIS_PROBE_REPEAT=3 ...                                        # each line that many times

Sandboxed data folder, the owner's personality and every tool, the real local model, no cloud. Prints a tally of
outcomes and, for each refusal, the request, the failure code and the raw reply the model wrote.
"""
import collections
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_LOCAL_ONLY"] = "1"
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
    os.environ.pop(_v, None)

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-probe-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")

import tools  # noqa: E402
from core import intelligence, users  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import memory, registry  # noqa: E402

tools.load_all()
users.set_current("owner")
SYSTEM = load_personality(Config(), "owner")
TOOLS = registry.schemas()

LINES = [
    "How are you?", "What's up?", "Are you here?", "What are you up to?", "Thank you.", "Yes.", "Okay.", "Bah!",
    "pair my phone.", "What's the weather?", "What local AI are you using?", "Can you see my screen?",
    "do some tricks.", "Go to sleep.", "Start Gaming Coach.", "debrief me", "status on your investigation.",
    "uninstall Red Dead Redemption.", "open wardrobe", "try on some outfits from the wardrobe",
    "What's the status of the home lab?", "Turn off eyes.", "What's the basic of C++?", "play rock, paper, scissors.",
    "YouTube.", "the cloud budget how do i do that", "can you tell me how to turn on pc control",
    "what grocery deals is there around this week that i can use so you can go shopping",
    "did you add my son's powdered milk because i need to get him that",
    "what's the news on the haitian gangs right now", "Explain how a computer processor works in five sentences.",
    "um jarvis can you make sure that my pc is ready for playing", "do something cool with the little robot.",
    "Remind me in 20 minutes to take the laundry out.", "Add eggs and milk to my shopping list.",
    "How much have you cost me this month?", "What did we talk about yesterday?", "Where did I park?",
    "I need you to build a meal plan for the family of six, high protein, I'm getting paid on the 15th.",
    "my folders keep opening up by themselves what is causing that can you investigate that for me",
]


def probe(said, raw_out):
    """One fresh-conversation turn: what the fast tier returns, or which guard refused what the model wrote."""
    history = [{"role": "user", "content": [
        {"type": "text", "text": "<background>\n" + memory.context_block() + "\n</background>"},
        {"type": "text", "text": said}]}]
    seen = []
    real = intelligence.complete_local

    def capture(payload, cancelled=lambda: False, on_text=None):
        reply = real(payload, cancelled, on_text)
        seen.append({"tools": [t["function"]["name"] for t in payload["tools"]],
                     "reply": reply["choices"][0]["message"]})
        return reply
    intelligence.complete_local = capture
    t = time.time()
    try:
        msg = intelligence.request("fast", SYSTEM, history, TOOLS)
        kinds = [b.type if b.type == "text" else f"call:{b.name}" for b in msg.content]
        return "ok", round(time.time() - t, 1), kinds, seen
    except intelligence.LocalFailure as error:
        raw_out.append({"said": said, "failure": str(error), "requests": seen})
        return str(error), round(time.time() - t, 1), [], seen
    finally:
        intelligence.complete_local = real


def main(argv):
    lines = Path(argv[0]).read_text(encoding="utf-8").splitlines() if argv else LINES
    lines = [x.strip() for x in lines if x.strip()]
    repeat = int(os.getenv("JARVIS_PROBE_REPEAT", "1"))
    tally, refused, times = collections.Counter(), [], []
    print(f"fast model: {intelligence.model('fast')}   {len(lines)} lines x {repeat}")
    for said in lines:
        for _ in range(repeat):
            outcome, took, kinds, seen = probe(said, refused)
            tally[outcome] += 1
            times.append(took)
            print(f"{outcome:>22} {took:5.1f}s  {said[:70]!r}  -> {kinds} ({len(seen)} model call{'s' if len(seen) != 1 else ''})")
    print("\n== outcomes ==")
    for k, v in tally.most_common():
        print(f"{v:4}  {k}")
    times.sort()
    print(f"median {times[len(times) // 2]:.1f}s  p90 {times[int(len(times) * .9)]:.1f}s  max {times[-1]:.1f}s")
    if refused:
        print("\n== refused replies (raw) ==")
        for r in refused:
            print(f"\n{r['said']!r}  ->  {r['failure']}")
            for n, req in enumerate(r["requests"]):
                reply = req["reply"]
                calls = [(c["function"]["name"], c["function"]["arguments"]) for c in reply.get("tool_calls") or []]
                print(f"  call {n + 1} ({len(req['tools'])} tools): text={str(reply.get('content'))[:300]!r} calls={calls}")
    out = storage.DATA / "probe.json"
    out.write_text(json.dumps({"tally": tally, "refused": refused}, indent=1, default=str), encoding="utf-8")
    print(f"\nraw record: {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
