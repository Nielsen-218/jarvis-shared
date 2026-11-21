"""Where a short chat turn's seconds and prompt tokens go, on the real local model, in the ladder probe's sandbox.

    .venv\\Scripts\\python.exe -m scripts.chat_latency_probe --gateway --lines "Explain RAM from my IT Fundamentals course." "Right."
    .venv\\Scripts\\python.exe -m scripts.chat_latency_probe --gateway --sequence --out runs\\chat-latency.json

Per line: which route answered, then every model call: role, prompt tokens (the model's own count), seconds, and what the
prompt is made of (system text, history, the newest line, tool schemas), in estimated tokens. Synthetic lines only."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "--gateway" not in sys.argv:
    sys.argv.append("--gateway")                      # the deployed app runs with the gateway on
from scripts import ladder_probe as lp  # noqa: E402  (sets up the sandbox, no PC tool may run)
from core import gateway, intelligence  # noqa: E402

SEQUENCE = ["Explain RAM from my IT Fundamentals course.", "Simpler.", "Right.", "This is simpler than I thought.", "Thanks.",
            "what time is it", "More detail.", "Okay.", "That makes sense.", "Explain subnetting.", "Right.", "Thanks."]
OUTSIDE = ["Right.", "Okay.", "Thanks.", "This is simpler than I thought.", "That makes sense.", "Simpler."]

calls: list[dict] = []
decisions: list[str] = []
_complete = intelligence.complete_local
_decide = gateway.decide


def _chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(json.dumps(p)) if not (isinstance(p, dict) and p.get("type") == "text") else len(p.get("text", "")) for p in content)
    return 0


def compose(payload) -> dict:
    msgs = payload.get("messages", [])
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    out = {"system": 0, "history": 0, "latest": 0, "tools": len(json.dumps(payload.get("tools") or []))}
    for i, m in enumerate(msgs):
        n = _chars(m.get("content")) + (len(json.dumps(m.get("tool_calls"))) if m.get("tool_calls") else 0)
        out["system" if m.get("role") == "system" else "latest" if i == last_user else "history"] += n
    return {k: round(v / 3.6) for k, v in out.items()} | {"messages": len(msgs)}   # chars -> rough tokens


def complete(payload, cancelled=lambda: False, on_text=None):
    t = time.time()
    result = _complete(payload, cancelled, on_text)
    usage = (result or {}).get("usage") or {}
    calls.append({"role": payload.get("role"), "profile": payload.get("profile"), "tools": len(payload.get("tools") or []),
                  "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                  "seconds": round(time.time() - t, 2), "parts": compose(payload)})
    return result


def decide(*a, **k):
    out = _decide(*a, **k)
    decisions.append(f"{getattr(out, 'source', '?')}:{[getattr(i, 'kind', '?') for i in getattr(out, 'intents', [])]}" if out else "none")
    return out


intelligence.complete_local = complete
gateway.decide = decide


def run(lines, phone: bool, material: bool) -> list[dict]:
    if material:
        lp.reset_material()
    brain = lp.new_brain()
    rows, previous = [], ""
    for line in lines:
        calls.clear()
        decisions.clear()
        row = lp.turn(brain, line, phone, previous)
        previous = row["reply"]
        row |= {"gateway": list(decisions), "calls": [dict(c) for c in calls],
                "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in calls)}
        rows.append(row)
        print(f"{row['seconds']:>5}s route={row['route']:<12} gateway={row['gateway']} model_calls={len(calls)}  YOU: {line}\n"
              f"      JARVIS: {row['reply'][:160]}", flush=True)
        for c in calls:
            print(f"        {c['role']:<9} prof={c['profile']} prompt={c['prompt_tokens']} out={c['completion_tokens']} {c['seconds']}s "
                  f"parts~{c['parts']}", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", action="store_true")
    ap.add_argument("--phone", action="store_true")
    ap.add_argument("--lines", nargs="*")
    ap.add_argument("--sequence", action="store_true", help="the 12-line conversation with a lesson")
    ap.add_argument("--outside", action="store_true", help="acknowledgements with no lesson open")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if not intelligence.enabled():
        print("local intelligence is not enabled")
        return 2
    report = {}
    if a.lines:
        report["lines"] = run(a.lines, a.phone, True)
    if a.sequence:
        report["sequence"] = run(SEQUENCE, a.phone, True)
    if a.outside:
        report["outside"] = run(OUTSIDE, a.phone, False)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
