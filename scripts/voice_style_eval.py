"""How JARVIS sounds on the REAL local model: the owner's own chat lines replayed through Brain.ask, scored for style.

    .venv\\Scripts\\python.exe scripts\\voice_style_eval.py            # 40 recent chat lines + the fixed probes
    .venv\\Scripts\\python.exe scripts\\voice_style_eval.py --lines 20

Upgrade 4 (speak like JARVIS) is measured here, before and after: the rate of replies that END on a closing offer
("Anything else?"), mean words per reply, exact repeats of the previous reply, "the owner" said to his face, and the old
template lines. Targets: offer endings <= 10%, no exact repeats, zero "the owner".

Safe by construction, like scripts/convo_local.py but stricter: a temporary data folder (memories, lists, the vault, the
PC plan ledger and the learned skills are never touched), the cloud is off, and NOTHING acts: quick answers and the PC
layer (pcfast / gateway) are switched off and every tool call is refused, so only the conversation model speaks. Lines
that read as PC commands are not replayed at all. Reads data/jarvis.log (legacy-encoded: decoded leniently, never written).
"""
import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_LOCAL_ONLY"] = "1"                      # a paid call here would be a bug, not a fallback
os.environ["JARVIS_GATEWAY"] = "off"                       # no PC understanding, no PC execution
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
    os.environ.pop(_v, None)

from core import storage  # noqa: E402

LOG = storage.DATA / "jarvis.log"                          # the owner's real log: read only, before the sandbox below
storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-voice-eval-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
os.environ["JARVIS_COMPANION_DATA_DIR"] = str(storage.DATA / "companion")
os.environ["JARVIS_PC_STATE_DIR"] = str(storage.DATA / "pc-control")
os.environ["JARVIS_PC_ROOT"] = str(storage.DATA / "jarvis_pc")
os.environ["JARVIS_MISSIONS_DIR"] = str(storage.DATA / "missions")

import webbrowser  # noqa: E402

from brain.llm import Brain  # noqa: E402
from core import intelligence, pcfast, quick, users  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import load_all, memory, registry  # noqa: E402

load_all()
users.set_current("owner")
webbrowser.open = lambda url, *a, **k: True
quick.handle = lambda text: None                           # quick answers include volume and timers: they ACT
pcfast.handle = lambda text, history=None, **kw: None      # never the PC
registry.run = lambda name, args: ("Not run: tools are off during the voice-style evaluation.", True)
for _shortcut in ("_learned_skill_request", "_craft_request", "_web_request", "_local_note_request"):
    setattr(Brain, _shortcut, lambda self, text, emit: False)   # the deterministic shortcuts run skills: off

# A PC command, a pasted block or anything that would act: not replayed (the model is asked to talk, not to do).
_ACTS = re.compile(r"\b(?:open|close|launch|start|quit|kill|minimi[sz]e|maximi[sz]e|type|click|press|scroll|install|uninstall|"
                   r"delete|remove|rename|move|copy|paste|save|download|upload|send|post|buy|pay|shut ?down|restart|reboot|"
                   r"sleep|lock|sign out|log off|turn (?:on|off)|set (?:the )?volume|mute|play|pause|skip|rig|import|"
                   r"run|execute|watch me|follow (?:the|this|these)|screenshot|remind me|timer|alarm|add .* to)\b", re.I)
_YOU = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?YOU:\s*(.+?)\s*$")
TEMPLATES = ("I caught a short phrase", "I'm not sure that covers everything", "tell me what I missed", "Plan: 1)")
PROBES = [["How are you doing?", "Why are you here?", "Why are you here?", "Thanks."],
          # his own back-to-back repeats (not the persona's few-shot inputs, which the model can copy): the repeat guard
          # must shorten a copy to "As I said", never swallow the answer after a lead-in (review 2026-11-10)
          ["How are you?", "How are you?", "What local AI are you using?", "What local AI are you using?"]]
# Nothing said, or only a lead-in ("Checking, sir.") with the answer swallowed after it.
_LEAD_IN_ONLY = re.compile(r"^(?:(?:checking|one (?:sec|second|moment)|let me (?:check|see|look)|pulling (?:that|it) up|"
                           r"looking (?:that|it) up|give me a (?:second|moment)|bear with me|on it)[^.?!]{0,15}[.!]\s*)?$", re.I)


# The character, scored (wave 3, 2026-11-14; his choices: "sir" in about one reply in three, light humor, a person to
# talk to and not a service desk, never a remark about how he talks). Counted per reply; a rate per run in the summary.
CHARACTER = {
    "service_desk": re.compile(r"\b(?:ready to (?:listen|help|assist)|standing by|here to (?:listen|help|assist)|what would you like|"
                               r"what do you (?:need|want)|how (?:can|may) i (?:help|assist)|at your service|let me know|"
                               r"i am here and|awaiting your|your next (?:instruction|command|request))\b", re.I),
    "machine_talk": re.compile(r"\b(?:my circuits|my processors?|my servers?|my algorithms?|my subroutines|my code|my systems|"
                               r"for a machine|as a machine|as an ai|polishing my)\b", re.I),
    "about_his_manner": re.compile(r"\b(?:casual (?:mood|greeting|exchange|tone|chatter)|your (?:greeting|tone|phrasing|casual)|"
                                   r"you seem to be in|unnecessary chatter)\b", re.I),
}


def character(reply: str, title: str = "sir") -> dict:
    """One reply against the character sheet: the title said, a question at the end, and the three faults above."""
    text = (reply or "").strip()
    row = {"title": bool(re.search(rf"\b{re.escape(title)}\b", text, re.I)), "question_ending": text.endswith("?")}
    row.update({name: bool(pattern.search(text)) for name, pattern in CHARACTER.items()})
    return row


def chat_lines(limit: int, log: Path = LOG) -> list[str]:
    """The owner's most recent distinct chat lines from his log (newest last), PC commands left out."""
    try:
        text = log.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return []
    seen, out = set(), []
    for line in text.splitlines():
        m = _YOU.match(line)
        said = m.group(1).split("  (")[0].strip() if m else ""
        key = said.casefold()
        if len(said) < 3 or len(said) > 200 or key in seen or _ACTS.search(said) or said.startswith(("[", "{")):
            continue
        seen.add(key)
        out.append(said)
    return out[-limit:]


def ask(brain: Brain, text: str) -> tuple[str, float]:
    said: list[str] = []
    started = time.time()
    brain.ask(text, said.append)
    return " ".join(s.strip() for s in said if s.strip()), time.time() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lines", type=int, default=40, help="how many of his recent chat lines to replay")
    parser.add_argument("--log", type=Path, default=LOG, help="the log to read YOU: lines from (default: data/jarvis.log)")
    parser.add_argument("--per-conversation", type=int, default=5, help="lines per conversation (repeats are within one)")
    parser.add_argument("--label", default="", help="a name for this run in the output file (before, after)")
    args = parser.parse_args()
    cfg = Config()
    lines = chat_lines(args.lines, args.log)
    groups = [lines[i:i + args.per_conversation] for i in range(0, len(lines), args.per_conversation)] + PROBES
    rows = []
    for group in groups:
        b = Brain(cfg.model, cfg.effort, load_personality(cfg, "owner"), uid="owner")
        b.context = memory.context_block
        previous = ""
        for text in group:
            reply, took = ask(b, text)
            row = {"you": text, "jarvis": reply, "seconds": round(took, 1), "words": len(reply.split()),
                   "offer_ending": intelligence.generic_offer(reply), "repeat": bool(reply) and reply == previous,
                   "as_i_said": reply.startswith("As I said"), "no_answer": bool(_LEAD_IN_ONLY.match(reply.strip())),
                   "the_owner": len(re.findall(r"\bthe owner\b", reply, re.I)),
                   "template": any(t.casefold() in reply.casefold() for t in TEMPLATES)} | character(reply, cfg.user_title)
            rows.append(row)
            previous = reply
            print(f"\nYOU: {text}\n  JARVIS ({took:.1f}s, {row['words']} words): {reply[:400]}")
    if not rows:
        print("No lines to replay.")
        return 1
    n = len(rows)
    summary = {"replies": n, "offer_ending_rate": round(sum(r["offer_ending"] for r in rows) / n, 3),
               "mean_words": round(statistics.mean(r["words"] for r in rows), 1),
               "exact_repeats": sum(r["repeat"] for r in rows), "the_owner": sum(r["the_owner"] for r in rows),
               "as_i_said": sum(r["as_i_said"] for r in rows), "no_answer": sum(r["no_answer"] for r in rows),
               "template_lines": sum(r["template"] for r in rows), "model": intelligence.model("fast"),
               "title_rate": round(sum(r["title"] for r in rows) / n, 3),
               "question_ending_rate": round(sum(r["question_ending"] for r in rows) / n, 3),
               **{name: sum(r[name] for r in rows) for name in CHARACTER},
               "median_words": statistics.median(r["words"] for r in rows)}
    print("\n" + json.dumps(summary, indent=2))
    out = ROOT / "runs" / f"voice-style-{time.strftime('%Y%m%d-%H%M')}{'-' + args.label if args.label else ''}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written: {out}")
    ok = (summary["offer_ending_rate"] <= 0.10 and summary["exact_repeats"] == 0 and summary["the_owner"] == 0
          and summary["template_lines"] == 0 and summary["no_answer"] == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
