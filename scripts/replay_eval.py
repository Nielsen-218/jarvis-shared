"""Conversation replay + scripted multi-turn chats on the REAL local model, sandboxed (same sandbox as scripts/chat_eval.py).

    .venv\\Scripts\\python.exe scripts\\replay_eval.py --set scripted --out DIR [--label before]
    .venv\\Scripts\\python.exe scripts\\replay_eval.py --set real --log PATH --limit 150 --out DIR [--label before]

Two sets:
  scripted  15 multi-turn conversations written for this eval (synthetic, safe to keep in the repository).
  real      the most recent N conversational `YOU:` lines of a Jarvis log, in their original order with context
            (consecutive lines form one chat; a gap of more than GAP command lines starts a new one). The owner's words
            are PRIVATE: they are read from the log on this machine only; the output goes to --out (keep it outside the
            repository) and nothing from it belongs in code, tests, commits or docs.

Nothing can act: chat_eval's stand-ins refuse every PC action and skill; a line that would have acted is marked `cmd`
(not a conversation line, left out of the conversation numbers). The automatic detectors are chat_eval's plus the
ones below; the replies are then classified by reading them (labels file, scripts do not decide by themselves).
Writes OUT/<set>-<label>.json and OUT/<set>-<label>.txt (a compact transcript to read).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import chat_eval as ce  # noqa: E402  (sandbox setup happens on import)

GAP = 3
MAX_CHAT = 25

# ---------------------------------------------------------------------------------------------- scripted set
# (line, tags). Tags as in chat_eval, plus: cmd = a PC command (not judged as chat), memo:<words> = reply must use one
# of these words, no:<words> = reply must NOT use any of them, prev = must follow the previous line (a follow-up).
SCRIPTED: dict[str, list[tuple[str, tuple]]] = {
    "s_small_talk": [
        ("Hey, how are you doing tonight?", ("noact",)),
        ("Not bad. I finally finished painting the spare room.", ()),
        ("Yeah, it took all weekend. Grey, in the end.", ()),
        ("Do you think grey is boring?", ("depth",)),
        ("Ha, fair. I'm going to hang some pictures next.", ()),
        ("Any tips on hanging them straight?", ("depth",)),
        ("Right. Thanks, that helps.", ("short", "ack")),
    ],
    "s_howto": [
        ("How do I change a flat tyre?", ("depth",)),
        ("What if I don't have a jack?", ("depth", "prev")),
        ("How tight should the wheel nuts be?", ("depth", "prev")),
        ("And should I drive on the spare for long?", ("prev",)),
        ("Okay, got it.", ("short", "ack")),
    ],
    "s_opinion": [
        ("Is working from home better than going to an office?", ("depth",)),
        ("Even for creative work?", ("prev",)),
        ("Hmm, I'm not sure I agree with you.", ("short",)),
        ("Which would you pick if you had to?", ()),
        ("Fair enough.", ("short", "ack")),
        ("Right, different subject: do you like jazz?", ("fresh:home|office|commut|remote|creative",)),
    ],
    "s_correction": [
        ("What's the capital of Canada?", ("recall:ottawa",)),
        ("Sorry, no, I meant the capital of Australia.", ("recall:canberra",)),
        ("And its biggest city?", ("recall:sydney", "prev")),
        ("No wait, I said the biggest city, not the capital.", ("recall:sydney",)),
        ("How far apart are those two?", ("prev",)),
    ],
    "s_topic_switch": [
        ("I've been learning guitar for about a month.", ()),
        ("My fingertips are killing me, though.", ()),
        ("How long until the soreness stops?", ("depth", "prev")),
        ("Random question, how many ounces are in a cup?", ("fresh:guitar|finger|callus|string",)),
        ("Back to the guitar. Should I practise every day?", ("depth", "recall:guitar|practi|day|minutes|short|session")),
    ],
    "s_emotional": [
        ("My grandmother is in hospital and I can't get there until Friday.", ()),
        ("I just feel useless sitting here.", ()),
        ("She's the one who taught me to cook.", ()),
        ("I don't know what to say when I call her.", ("depth",)),
        ("Thanks. That actually helps.", ("short", "ack")),
    ],
    "s_joke": [
        ("Tell me a joke.", ()),
        ("That was terrible.", ("short",)),
        ("Okay, one about computers then.", ()),
        ("Ha, that one worked.", ("short", "ack")),
        ("Do you ever get tired of jokes?", ("noact",)),
        ("Another one, but a clean one about cats.", ()),
    ],
    "s_fact_chain": [
        ("Who painted the Mona Lisa?", ("recall:leonardo|da vinci",)),
        ("When did he paint it?", ("prev", "recall:15|1503|1506|sixteenth|renaissance|early|century")),
        ("Where is it kept now?", ("recall:louvre|paris",)),
        ("How big is it?", ("prev", "recall:77|53|inch|centimet|small|cm|smaller|surpris")),
        ("Wasn't it stolen once?", ("recall:1911|stolen|theft|stole|peruggia|yes")),
        ("When did it come back?", ("prev", "recall:1913|two years|returned|recovered")),
    ],
    "s_memory": [
        ("Remember this: my meeting with Priya about the budget is at three tomorrow.", ()),
        ("Also I bought a blue bicycle today, a second-hand one.", ()),
        ("And I need to call the dentist before Friday.", ()),
        ("Okay, what did I say about tomorrow?", ("recall:priya|three|3|budget|meeting",)),
        ("What colour was the bicycle?", ("recall:blue",)),
        ("What was the very first thing I told you?", ("recall:priya|meeting|budget|three",)),
        ("And what do I have to do before Friday?", ("recall:dentist",)),
    ],
    "s_mixed": [
        ("I'm trying to decide what to cook for my sister on Sunday.", ()),
        ("Open Notepad and type the shopping list.", ("cmd",)),
        ("Anyway, she's vegetarian. Any ideas?", ("recall:veg|lentil|mushroom|bean|pasta|curry|chickpea|tofu|cheese|eggplant|aubergine|risotto|stuffed|dal|soup|salad|halloumi|spinach|pepper|squash|pumpkin|vegetable|paneer|gnocchi|lasagn",)),
        ("The second one sounds good.", ("prev",)),
        ("Close Notepad.", ("cmd",)),
        ("What were we talking about before that?", ("recall:sister|sunday|cook|dinner|vegetarian|food|meal",)),
    ],
    "s_mishear": [
        ("Wait, what's the Stephen Hill of a tree called?", ()),
        ("No, I said, what's the age of a tree, how do you tell?", ("depth",)),
        ("Sous-titrage Société Radio-Canada.", ()),
        ("Did you catch what I just said?", ()),
        ("Never mind. How are you finding tonight?", ("noact",)),
        ("Mm.", ("short",)),
        ("I said, I think I'll go for a walk.", ("short",)),
    ],
    "s_pushback": [
        ("You're pretty slow sometimes, you know that?", ("short",)),
        ("Why can't you just get it right the first time?", ()),
        ("That's not an answer.", ("short",)),
        ("Say something actually useful.", ()),
        ("Okay, okay. Sorry. Long day.", ("short",)),
        ("What would you do if you were me tonight?", ()),
    ],
    "s_ramble": [
        ("So here is the thing. I want to start a small business selling handmade candles, but I have no idea about pricing, "
         "and I work full time, and my sister thinks I should just do markets on weekends first, and honestly I'm scared of "
         "putting money in before I know anyone will buy them.", ("depth",)),
        ("So what do you think?", ("prev", "depth")),
        ("What about the pricing part?", ("depth", "prev")),
        ("Okay. And if nobody buys them?", ("prev",)),
        ("Right.", ("short", "ack")),
    ],
    "s_about_you": [
        ("What are you, exactly?", ()),
        ("Can you remember things between days?", ()),
        ("What did we talk about yesterday?", ("no:yesterday we discussed|yesterday you told|last night you",)),
        ("Do you get tired?", ()),
        ("What do you actually like doing?", ()),
        ("Alright. Thanks for being honest.", ("short", "ack")),
    ],
    "s_repeat_q": [
        ("How far is Mars from Earth?", ("depth",)),
        ("Sorry, I zoned out. How far did you say?", ("prev", "recall:million|mile|kilomet|distance|far|\\d")),
        ("And how long would a trip take?", ("prev", "depth")),
        ("Wait, what did I ask you first?", ("recall:mars|far|distance|how")),
        ("Cheers.", ("short", "ack")),
    ],
}

# ---------------------------------------------------------------------------------------------- extra detectors
_BRUSH = re.compile(r"^(?:ok(?:ay)?|sure|indeed|noted|very good|right|quite|of course|understood|got it)[.!,]?\s*$", re.I)
_SERVILE = re.compile(r"\b(?:at your service|as you wish|certainly,? sir|right away,? sir|my pleasure|i live to serve|"
                      r"how may i (?:assist|be of)|i do apologi[sz]e|my deepest apolog)", re.I)
_MARKDOWN = re.compile(r"(?:^|\n)\s*(?:[-*•]\s|\d+\.\s|#{1,4}\s)|\*\*[^*]+\*\*")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def extra_flags(said: str, row: dict, prev_replies: list[str], tags: tuple) -> dict:
    reply = row["jarvis"]
    words = len(reply.split())
    question = ce.chatturn.open_question(said) or said.strip().endswith("?")
    depth = "depth" in tags
    return {
        "lecture": bool(reply) and words > 70 and not depth and len(said.split()) < 12,
        "brush_off": bool(reply) and question and (words < 4 or bool(_BRUSH.match(reply.strip()))) and "ack" not in tags,
        "servile": bool(_SERVILE.search(reply)),
        "markdown_or_emoji": bool(_MARKDOWN.search(reply)) or bool(_EMOJI.search(reply)),
        "no_word": any(re.search(rf"(?:{t.split(':', 1)[1]})", reply, re.I) for t in tags if t.startswith("no:")),
        "echo_prev": bool(reply) and bool(prev_replies) and ce.chatturn.same_reply(reply, prev_replies[-1]),
        "cmd": bool(row["pc_action"]),
    }


XFLAGS = ("lecture", "brush_off", "servile", "markdown_or_emoji", "no_word", "echo_prev")


def run_chat(name: str, lines: list[tuple[str, tuple]], rows: list[dict]) -> None:
    brain = ce.new_brain()
    prev: list[str] = []
    for said, tags in lines:
        row = ce.turn(brain, said, tags, prev)
        row.update(extra_flags(said, row, prev, tags))
        row["chat"] = name
        row["fresh_old_topic"] = row["old_topic"]
        rows.append(row)
        prev.append(row["jarvis"])


def real_lines(log: Path, limit: int) -> dict[str, list[tuple[str, tuple]]]:
    text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    turns = [ln[5:].strip() for ln in text if ln.startswith("YOU: ")]
    cmd = re.compile(
        r"^(?:(?:hey|hi|ok(?:ay)?|yes|yeah|no)[ ,.]*)?(?:jarvis[ ,.]*|jervis[ ,.]*)?(?:please )?"
        r"(open|closed?|minimi[sz]e|stop|type|click|play|pause|pull up|bring|get rid|scroll|rig|save|use|launch|go to|switch|"
        r"take a look|look for|search|find|show me|set|turn|mute|unmute|volume|run|start|quit|exit|maximi[sz]e|follow|watch|"
        r"learn|put|copy|paste|press|drag|read my screen|what's on my screen|how many apps|what apps|scan|make sure|go and)\b",
        re.I)
    app = re.compile(r"\b(open|close|minimi[sz]e|launch)\b.{0,40}\b(chatgpt|claude|edge|youtube|blender|steam|epic|notepad|"
                     r"apex|settings|files|dashboard|word|excel|outlook|calculator|github|discord)\b", re.I)
    keep = [i for i, s in enumerate(turns) if s and not cmd.search(s) and not app.search(s)][-limit:]
    chats: dict[str, list[tuple[str, tuple]]] = {}
    current: list[tuple[str, tuple]] = []
    last = None
    n = 0
    for i in keep:
        if last is not None and (i - last > GAP + 1 or len(current) >= MAX_CHAT):
            chats[f"r{n:02d}"] = current
            n += 1
            current = []
        current.append((turns[i], ()))
        last = i
    if current:
        chats[f"r{n:02d}"] = current
    return chats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=("scripted", "real"), required=True)
    ap.add_argument("--log", default=str(ROOT.parent.parent / "data" / "jarvis.log"))
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="run")
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--no-seed", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chats = SCRIPTED if args.set == "scripted" else real_lines(Path(args.log), args.limit)
    if args.only:
        chats = {k: v for k, v in chats.items() if k in args.only}
    if not args.no_seed:
        ce.seed_real()
    rows: list[dict] = []
    for name, lines in chats.items():
        print(f"\n===== {name} =====")
        run_chat(name, lines, rows)
    stem = out / f"{args.set}-{args.label}"
    stem.with_suffix(".json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    txt = []
    for k, r in enumerate(rows):
        flags = [f for f in ce.FLAGS + XFLAGS + ("cmd",) if r.get(f)] + (["forgot"] if r["recall_needed"] and not r["recall_ok"] else [])
        txt.append(f"#{k} [{r['chat']}] {'/'.join(flags)}\n  Y: {r['you'][:300]}\n  J: {r['jarvis'][:700] or '(nothing)'}")
    stem.with_suffix(".txt").write_text("\n".join(txt), encoding="utf-8")
    counts = {f: sum(1 for r in rows if r.get(f)) for f in ce.FLAGS + XFLAGS + ("cmd",)}
    counts["forgot"] = sum(1 for r in rows if r["recall_needed"] and not r["recall_ok"])
    print(f"\n{len(rows)} turns: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
