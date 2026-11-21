"""Command-turn latency harness: where the seconds go between "he finished the sentence" and the first sound, measured on the
REAL local model (Ollama) with NOTHING executed.

    .venv\\Scripts\\python.exe scripts\\command_latency.py --runs 3 --out before.json
    .venv\\Scripts\\python.exe scripts\\command_latency.py --runs 3 --out after.json --compare before.json

Each utterance goes through the same path as the voice loop (main.pre_brain_route -> Brain.ask -> core.pcfast -> core.gateway
.decide -> the brain's own model), with a temporary data folder, JARVIS_PC_STATE_DIR in that folder, and every action stubbed:
gateway.execute records the decided steps and says "Done."; registry tools, the PC-control entry points and process launching
are fakes. What is recorded per utterance: the ROUTE (pre-brain verdict, the decided tool + arguments, or the brain's tool call),
and the timing of every stage (first sentence, decide, model, meaning checks), plus one row per Ollama call (role, kind of
prompt, prompt tokens evaluated by the server, tokens generated, load time, wall time).

--compare checks route equality between two result files: for every utterance the majority route over the runs must be
identical (same tool, same arguments); any difference is printed and the exit code is 1.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_LOCAL_ONLY"] = "1"
# The worktree has no .env (never committed, never read here): name the local provider the app runs on explicitly. One
# brain, qwen3.5:9b through Ollama, for both the "fast" and the "advanced" role (core.warmth: one brain since 2026-11-09).
os.environ.setdefault("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
os.environ.setdefault("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
    os.environ.pop(_v, None)

from core import storage  # noqa: E402

_SANDBOX = Path(tempfile.mkdtemp(prefix="jarvis-cmdlat-"))
storage.DATA = _SANDBOX
os.environ["JARVIS_GATEWAY"] = "on"
os.environ["JARVIS_VAULT"] = str(_SANDBOX / "vault")
os.environ["JARVIS_COMPANION_DATA_DIR"] = str(_SANDBOX / "companion")
os.environ["JARVIS_PC_STATE_DIR"] = str(_SANDBOX / "pc-control")
os.environ["JARVIS_PC_ROOT"] = str(_SANDBOX / "jarvis_pc")
os.environ["JARVIS_OUTBOX_DIR"] = str(_SANDBOX / "outbox")
os.environ["JARVIS_MISSIONS_DIR"] = str(_SANDBOX / "missions")

# (label, shape, sentence). Synthetic sentences in the owner's style of command; none is copied from his log.
UTTERANCES = [
    # exact shapes the code recognises today (no model)
    ("open-chrome-like", "exact", "Open Notepad."),
    ("open-polite", "exact", "Please open the calculator."),
    ("close-exact", "exact", "Close Paint."),
    ("close-the", "exact", "Close the calculator."),
    ("focus-exact", "exact", "Focus Notepad."),
    ("minimize", "exact", "Minimize Spotify."),
    ("pause", "exact", "Pause the music."),
    ("scroll", "exact", "Scroll down."),
    ("volume-set", "exact", "Set the volume to 30."),
    ("launch-the", "exact", "Launch the calculator."),
    # one app named, said loosely: the fast lane (pcsemantic) today
    ("bring-up", "loose-app", "Can you bring up Spotify for me?"),
    ("get-going", "loose-app", "Get Discord going."),
    ("done-with", "loose-app", "I'm finished with Paint."),
    ("switch-to", "loose-app", "Switch over to Notepad."),
    ("shut-down", "loose-app", "Shut Notepad down for me."),
    ("let-me-see", "loose-app", "Let me see the calculator."),
    ("would-you-close", "loose-app", "Would you mind closing Discord?"),
    ("put-on-screen", "loose-app", "Put Spotify on my screen."),
    ("get-rid", "loose-app", "Get rid of Paint, I'm done with it."),
    ("app-only-please", "loose-app", "Calculator, please."),
    # short lines with no listed verb: the pre-brain meaning check applies (six words or fewer)
    ("mute", "short", "Mute it."),
    ("volume-down", "short", "Volume down a bit."),
    ("louder", "short", "A bit louder."),
    ("hide-all", "short", "Hide everything."),
    ("settings-now", "short", "Settings now."),
    # volume and system
    ("volume-words", "system", "Turn the volume up to fifty percent."),
    ("quieter", "system", "Make it a little quieter."),
    # the screen
    ("screen-whats", "screen", "What's on my screen?"),
    ("screen-look", "screen", "Take a look at my screen."),
    # inside an app, and compound sentences (the planner)
    ("type-into", "inside-app", "Type hello world into Notepad."),
    ("compound-open-close", "compound", "Open Notepad and then close Paint."),
    ("compound-open-vol", "compound", "Open Spotify and set the volume to 30."),
    ("compound-three", "compound", "Close Discord, open the calculator and then minimize Paint."),
    # talk that must stay talk
    ("chat-weather", "chat", "Do you think it will rain this weekend?"),
    ("chat-why", "chat", "Why is the sky blue?"),
    ("chat-hello", "chat", "How are you doing today?"),
]


class _Guard:
    """Nothing in this harness may start a program: Popen is allowed only for read-only inspection."""
    ALLOWED = ("tasklist", "nvidia-smi", "powershell", "pwsh", "wmic", "reg query", "where", "ollama")
    refused: list = []

    @classmethod
    def install(cls):
        real = subprocess.Popen.__init__

        def init(self, args, *a, **k):
            text = args if isinstance(args, str) else " ".join(map(str, args))
            low = text.lower()
            if not any(low.lstrip('"').split()[0].endswith(x) or x in low.split()[0:2] for x in cls.ALLOWED) or "start-process" in low:
                cls.refused.append(text[:80])
                raise OSError("command launching is stubbed in the latency harness")
            return real(self, args, *a, **k)
        subprocess.Popen.__init__ = init
        if hasattr(os, "startfile"):
            os.startfile = lambda *a, **k: (_Guard.refused.append("startfile"), None)[1]


_Guard.install()

import webbrowser  # noqa: E402

webbrowser.open = lambda *a, **k: True

import main as voice_loop  # noqa: E402  (pre_brain_route: the same filters the voice loop runs)
from brain.llm import Brain  # noqa: E402
from core import gateway, intelligence, pchybrid, pcfast, turntrace, users  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import load_all, memory, registry  # noqa: E402

load_all()
users.set_current("owner")

RECORD: dict = {"steps": [], "tools": [], "calls": []}
_LOCAL = threading.local()


def _fake_execute(decision, said, cancelled=lambda: False, say=None, began=None):
    steps = decision.steps()
    RECORD["steps"].append({"kinds": [i.kind for i in decision.intents], "source": decision.source,
                            "steps": [{"tool": s.get("tool"), "arguments": s.get("arguments", {})} for s in steps],
                            "skills": [i.skill for i in decision.intents if i.kind == "skill"],
                            "questions": [i.question for i in decision.intents if i.question]})
    return "Done."


def _fake_run(name, args):
    RECORD["tools"].append({"tool": name, "arguments": args})
    return "(harness: tool not run)", False


gateway.execute = _fake_execute
registry.run = _fake_run
pchybrid.run = lambda *a, **k: {"detail": "(harness)", "state": "done", "ok": True}
try:
    from tools import pcview
    for _n in ("control_pc", "pc_command", "see_pc_screen", "stop_pc_control"):
        setattr(pcview, _n, lambda *a, **k: [{"type": "text", "text": "(harness: not run)"}])
except Exception:  # noqa: BLE001
    pass
pcfast._run_with_consent = lambda *a, **k: [{"type": "text", "text": "(harness: not run)"}]

# One row per Ollama call: what kind of prompt, how long, and what the server did with it.
_real_reply = intelligence._ollama_reply
_real_complete = intelligence.complete_local
_real_body = intelligence._ollama_body


def _kind_of(system: str) -> str:
    s = system[:60]
    for key, name in (("You turn a person's sentence", "pcsemantic"), ("You are the understanding layer", "planner"),
                      ("You read what a person just said", "meaning"), ("You are JARVIS", "brain"), ("You are Jarvis", "brain")):
        if s.startswith(key):
            return name
    return "other:" + s[:24]


def _body(body):
    out = _real_body(body)
    cur = getattr(_LOCAL, "call", None)
    if cur is not None:
        msgs = out.get("messages") or []
        system = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
        cur.update(kind=_kind_of(system), prompt_chars=sum(len(str(m.get("content") or "")) for m in msgs),
                   system_chars=len(system), num_predict=out.get("options", {}).get("num_predict"),
                   schema=bool(out.get("format")), tools=len(out.get("tools") or []), keep_alive=out.get("keep_alive"),
                   think=out.get("think"), model=out.get("model"))
    return out


def _reply(native):
    cur = getattr(_LOCAL, "call", None)
    if cur is not None and isinstance(native, dict):
        ns = 1e9
        cur.update(prompt_tokens=native.get("prompt_eval_count"), prompt_s=round((native.get("prompt_eval_duration") or 0) / ns, 3),
                   gen_tokens=native.get("eval_count"), gen_s=round((native.get("eval_duration") or 0) / ns, 3),
                   load_s=round((native.get("load_duration") or 0) / ns, 3), server_s=round((native.get("total_duration") or 0) / ns, 3))
    return _real_reply(native)


def _complete(payload, cancelled=lambda: False, on_text=None):
    cur = {"role": payload.get("role"), "t0": time.time()}
    _LOCAL.call = cur
    try:
        return _real_complete(payload, cancelled, on_text=on_text)
    finally:
        cur["wall_s"] = round(time.time() - cur["t0"], 3)
        cur["at_s"] = None
        RECORD["calls"].append(cur)
        _LOCAL.call = None


intelligence._ollama_body = _body
intelligence._ollama_reply = _reply
intelligence.complete_local = _complete

cfg = Config()


def new_brain() -> Brain:
    b = Brain(cfg.model, cfg.effort, load_personality(cfg, "owner"), uid="owner")
    b.context = memory.context_block
    return b


def one_run(brain: Brain, text: str) -> dict:
    RECORD.update(steps=[], tools=[], calls=[])
    turntrace._spans.clear()
    from core import approvals, pcsemantic              # every run is a fresh sentence: no answer cached from an earlier run
    pcsemantic._CACHE.clear()
    getattr(approvals, "_MEANT", {}).clear()
    import re
    norm = re.sub(r"[^\w' ]+", "", text.casefold()).strip()
    t0 = time.time()
    pre = voice_loop.pre_brain_route(norm, [])
    t_pre = time.time() - t0
    sentences: list = []
    stamps: list = []

    def on_sentence(s):
        stamps.append(time.time() - t0)
        sentences.append(s)

    brain.ask(text, on_sentence)
    total = time.time() - t0
    spans = turntrace.breakdown(t0)
    calls = [dict(c, t0=round(c["t0"] - t0, 3)) for c in RECORD["calls"]]
    for c in calls:
        c.pop("at_s", None)
    route = {"pre": pre, "decided": RECORD["steps"], "brain_tools": RECORD["tools"]}
    return {"pre_s": round(t_pre, 3), "first_sentence_s": round(stamps[0], 3) if stamps else None, "total_s": round(total, 3),
            "spans": {k: {"n": v["n"], "s": v["s"]} for k, v in spans.items()}, "calls": calls, "route": route,
            "said": " ".join(sentences)[:120]}


def signature(route: dict) -> str:
    """The actions an utterance causes, nothing about timing or which model chose them."""
    steps = [[(s["tool"], json.dumps(s["arguments"], sort_keys=True)) for s in d["steps"]] for d in route["decided"]]
    skills = [d["skills"] for d in route["decided"]]
    tools = [(t["tool"], json.dumps(t["arguments"], sort_keys=True)) for t in route["brain_tools"]]
    return json.dumps({"pre": route["pre"], "steps": steps, "skills": skills, "brain": tools}, sort_keys=True)


def pct(values, p):
    values = sorted(v for v in values if v is not None)
    return round(values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))], 3) if values else None


def summarize(rows: list) -> dict:
    out = {}
    for group in sorted({r["shape"] for r in rows}) + ["ALL", "MODEL-ROUTED"]:
        sel = [r for r in rows if group in ("ALL", r["shape"]) or (group == "MODEL-ROUTED" and r["spans"].get("model", {}).get("n"))]
        if not sel:
            continue
        out[group] = {"runs": len(sel)}
        for key in ("pre_s", "first_sentence_s", "total_s"):
            vals = [r[key] for r in sel]
            out[group][key] = {"p50": pct(vals, .5), "p90": pct(vals, .9)}
        for span in ("decide", "model", "meaning"):
            vals = [r["spans"].get(span, {}).get("s", 0.0) for r in sel]
            out[group][span] = {"p50": pct(vals, .5), "p90": pct(vals, .9),
                                "calls": round(statistics.mean(r["spans"].get(span, {}).get("n", 0) for r in sel), 2)}
    return out


def compare(before: dict, after: dict) -> int:
    bad = 0
    for label, b in before["by_label"].items():
        a = after["by_label"].get(label)
        if a is None:
            continue
        sb = collections.Counter(signature(r["route"]) for r in b["runs"]).most_common(1)[0][0]
        sa = collections.Counter(signature(r["route"]) for r in a["runs"]).most_common(1)[0][0]
        allb = {signature(r["route"]) for r in b["runs"]}
        alla = {signature(r["route"]) for r in a["runs"]}
        if sb != sa:
            bad += 1
            print(f"ROUTE DIFFERS {label}:\n   before {sb}\n   after  {sa}")
            for name, rows in (("before", b["runs"]), ("after", a["runs"])):
                print(f"   {name} runs:", dict(collections.Counter(signature(r["route"]) for r in rows)))
        elif allb != alla:
            print(f"note (same majority route, different minority runs) {label}: before {len(allb)} variants, after {len(alla)}")
    print(f"route equality: {len(before['by_label']) - bad} of {len(before['by_label'])} utterances identical")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default="")
    ap.add_argument("--compare", default="")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    brain = new_brain()
    print(f"model fast={intelligence.model('fast')} advanced={intelligence.model('advanced')} gateway={gateway.mode()}", flush=True)
    one_run(brain, "How are you doing today?")             # warm-up: the model and the prompt caches are loaded, not measured
    rows, by_label = [], {}
    for label, shape, text in UTTERANCES:
        if args.only and args.only not in label and args.only != shape:
            continue
        runs = []
        for n in range(args.runs):
            brain = new_brain()                             # no conversation carries over between runs
            r = one_run(brain, text)
            r.update(label=label, shape=shape)
            runs.append(r)
            rows.append(r)
            time.sleep(0.2)
        by_label[label] = {"shape": shape, "text": text, "runs": runs}
        med = statistics.median(r["total_s"] for r in runs)
        first = statistics.median((r["first_sentence_s"] or r["total_s"]) for r in runs)
        d = statistics.median(r["spans"].get("decide", {}).get("s", 0.0) for r in runs)
        m = statistics.median(r["spans"].get("model", {}).get("s", 0.0) for r in runs)
        sig = collections.Counter(signature(r["route"]) for r in runs).most_common(1)[0][0]
        print(f"{label:22s} first {first:5.2f}s total {med:5.2f}s decide {d:4.2f} model {m:4.2f} | {sig[:110]}", flush=True)
    result = {"summary": summarize(rows), "by_label": by_label, "refused_launches": _Guard.refused[:20]}
    print(json.dumps(result["summary"], indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    code = 0
    if args.compare:
        code = compare(json.loads(Path(args.compare).read_text(encoding="utf-8")), result)
    print("refused launches:", _Guard.refused[:10])
    return code


if __name__ == "__main__":
    sys.exit(main())
