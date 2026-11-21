"""Where does a voice turn's wait go? p50 / p90 per stage from the turn-timing ledger.

Every voice turn appends one row to data/learning/turn-timing-<pc|server>.jsonl (core/turntrace.record). Rows written
since the latency ledger carry "stages": endpoint (his last word -> the turn committed), stt, prefilter (the local
meaning checks before the brain), decide (the gateway), model (all model calls), first_token (commit -> the first
streamed token), first_sentence (commit -> the first sentence handed to the voice), tts_first_audio (that sentence ->
first sound) and total (commit -> first sound). Older rows only have total and thinking; they are counted for those.
A phone turn's row (the laptop, --channel phone) carries stt (a voice note's transcription), relay (every hop to the PC:
the thinking is one), tool (each skill that ran), voice (making the reply's audio) and total (request -> reply sent).

With --usage, the local model's own audit (intelligence_usage-<pc|server>.json) adds each call's prompt size
(prompt_tokens: the whole prompt on Ollama 0.40, cached or not) and how long reading it took (prompt_eval_ms: short
when the cache held). A 12,000-token prompt read from its first token takes 2.2-2.9 s; picked up from the cache, 0.1-0.2 s.

Run:  python scripts/latency_report.py [--file PATH] [--channel voice] [--last N] [--usage]
Report only: nothing here gates a merge (targets: chat first words p50 <= 2.5 s; PC acknowledgment <= 1.5 s).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ORDER = ["endpoint", "stt", "prefilter", "decide", "model", "relay", "tool", "first_token", "first_sentence",
         "tts_first_audio", "voice", "total", "thinking"]


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 100))          # ceil(n * q / 100), at least the first value
    return ordered[int(min(rank, len(ordered))) - 1]


def rows(path: Path, channel: str | None = None, last: int | None = None, real_only: bool = False) -> list[dict]:
    """The ledger's rows for one channel. real_only: without the rows marked "test" (verification devices, harnesses)."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (isinstance(row, dict) and (channel is None or row.get("channel") == channel)
                and not (real_only and row.get("test"))):
            out.append(row)
    return out[-last:] if last else out


def table(data: list[dict]) -> dict:
    """{stage: {"n", "p50", "p90", "max"}} over the rows."""
    values: dict[str, list[float]] = {}
    for row in data:
        stages = dict(row.get("stages") or {})
        for key in ("total", "thinking"):
            if key not in stages and isinstance(row.get(key), (int, float)):
                stages[key] = row[key]
        for stage, seconds in stages.items():
            if isinstance(seconds, (int, float)) and seconds >= 0:
                values.setdefault(stage, []).append(float(seconds))
    names = [s for s in ORDER if s in values] + sorted(s for s in values if s not in ORDER)
    return {s: {"n": len(values[s]), "p50": percentile(values[s], 50), "p90": percentile(values[s], 90),
                "max": max(values[s])} for s in names}


def usage_table(path: Path, last: int | None = None) -> dict:
    """{role: {"n", "read_p50", "read_p90", "read_ms_p50"}} from the local model's audit (received calls only)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    data = [r for r in data if isinstance(r, dict) and r.get("status") == "received"]
    data = data[-last:] if last else data
    by: dict[str, dict[str, list[float]]] = {}
    for r in data:
        tokens = r.get("tokens") or {}
        if isinstance(tokens.get("prompt_tokens"), int):
            by.setdefault(r.get("role", "?"), {}).setdefault("read", []).append(tokens["prompt_tokens"])
        if isinstance(tokens.get("prompt_eval_ms"), int):
            by.setdefault(r.get("role", "?"), {}).setdefault("ms", []).append(tokens["prompt_eval_ms"])
    out = {}
    for role, v in sorted(by.items()):
        read, ms = v.get("read", []), v.get("ms", [])
        out[role] = {"n": len(read), "read_p50": percentile(read, 50) if read else None,
                     "read_p90": percentile(read, 90) if read else None, "read_ms_p50": percentile(ms, 50) if ms else None}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--file", type=Path, help="a turn-timing .jsonl (default: this machine's under data/learning)")
    parser.add_argument("--channel", default="voice", help="voice / phone, or 'all'")
    parser.add_argument("--last", type=int, default=0, help="only the last N turns")
    parser.add_argument("--real", action="store_true", help="leave out test traffic (rows marked test)")
    parser.add_argument("--usage", action="store_true", help="also the local model's prompt-cache reads")
    parser.add_argument("--usage-file", type=Path, help="an intelligence_usage-*.json (default: this machine's)")
    args = parser.parse_args(argv)
    host = "pc"
    if args.file is None or (args.usage and args.usage_file is None):
        try:
            from core import pcbridge
            host = "pc" if pcbridge.local_pc() else "server"
        except Exception:  # noqa: BLE001
            pass
    path = args.file or ROOT / "data" / "learning" / f"turn-timing-{host}.jsonl"
    if not path.exists():
        print(f"no turn-timing file at {path}")
        return 1
    data = rows(path, None if args.channel == "all" else args.channel, args.last or None, args.real)
    print(f"{len(data)} {args.channel} turns from {path.name}")
    print(f"{'stage':<16}{'n':>5}{'p50 s':>9}{'p90 s':>9}{'max s':>9}")
    for stage, s in table(data).items():
        print(f"{stage:<16}{s['n']:>5}{s['p50']:>9.2f}{s['p90']:>9.2f}{s['max']:>9.2f}")
    if args.usage:
        usage = args.usage_file or ROOT / "data" / f"intelligence_usage-{host}.json"
        print(f"\nprompt size per local call, in tokens ({usage.name}); a short read time = the cache held")
        for role, u in usage_table(usage, args.last or None).items():
            ms = "-" if u["read_ms_p50"] is None else f"{u['read_ms_p50']:.0f} ms"
            print(f"  {role:<10} n={u['n']:<4} p50 {u['read_p50']} p90 {u['read_p90']} read time p50 {ms}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
