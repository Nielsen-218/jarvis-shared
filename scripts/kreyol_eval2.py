"""Fallback rate of the local model's Haitian Creole replies over the fixed set in scripts/kreyol_evalset.py.

    .venv\\Scripts\\python.exe scripts\\kreyol_eval2.py --label before
    .venv\\Scripts\\python.exe scripts\\kreyol_eval2.py --label after

Sandboxed like scripts/chat_eval.py (temporary data, no PC action runs, local model only; never the live server).
One fresh Brain per item (a follow-up's prime line runs first and is not scored). Per scored reply: whether the canned
line "Mwen pa fin konnen..." (or the short honest last-resort line) was said, whether a retry was made, and an
independent judge of the wrong language: a reply accepted as Creole that language.identify calls French/English/Spanish,
or that holds a French/English habit. Writes runs/kreyol-eval2-<label>.json and prints per-category numbers.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from scripts import chat_eval, kreyol_evalset  # noqa: E402  (sets up the sandbox on import)

from audio import language  # noqa: E402
from core import intelligence, kreyol_bank  # noqa: E402

AUDITS: list = []
_audit = intelligence.audit


def _counted_audit(role, what, *a, **k):
    AUDITS.append(str(what))
    return _audit(role, what, *a, **k)


intelligence.audit = _counted_audit
FLAGGED: list = []
_again_note = kreyol_bank.again_note


def _recording_again_note(found, *a, **k):
    FLAGGED.append({"foreign": found["foreign"], "unknown": found["unknown"]})
    return _again_note(found, *a, **k)


kreyol_bank.again_note = _recording_again_note
CANNED = "Mwen pa fin konnen kijan pou m di sa byen an kreyòl"
_FRENCH = re.compile(r"\b(?:c'est|j'ai|vous|nous sommes|est|une|les|des|très|avec|que|qui|quoi|oui|bonjour|merci|"
                     r"\w+tion|\w+eux|\w+ais|\w+ait)\b", re.I)


def wrong_language(reply: str, said: str = "") -> list:
    """Reasons this reply is not Creole, by judges that are not the reply check itself."""
    why = []
    code, sure = language.identify(reply)
    if code in ("fr", "en", "es") and sure >= language.SOME:
        why.append(f"identify={code}:{sure:.2f}")
    if _FRENCH.search(reply):
        why.append("french-habit:" + ",".join(sorted({m.group(0).lower() for m in _FRENCH.finditer(reply)})))
    found = kreyol_bank.check(reply, said=said)          # a word he used himself is not the model's French
    if found["foreign"]:
        why.append("foreign:" + ",".join(found["foreign"]))
    return why


def rejudge(label: str) -> int:
    data = json.loads((ROOT / "runs" / f"kreyol-eval2-{label}.json").read_text(encoding="utf-8"))
    bad = [(r["you"], r["jarvis"], wrong_language(r["jarvis"], r["you"])) for r in data["rows"] if not r["canned"]]
    bad = [b for b in bad if b[2]]
    print(f"{label}: {len(bad)} replies accepted that are not Creole by the independent judge")
    for you, jarvis, why in bad:
        print("  -", you[:40], "|", jarvis[:100], why)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--presence", type=float, default=None, help="override the chat profiles' presence penalty")
    parser.add_argument("--rejudge", default="", help="re-judge an earlier run's replies offline (no model) and print")
    args = parser.parse_args(argv)
    if args.rejudge:
        return rejudge(args.rejudge)
    if args.presence is not None:
        for profile in intelligence.PROFILES.values():
            profile["presence_penalty"] = args.presence
    chat_eval.seed()
    rows = []
    items = kreyol_evalset.ITEMS[:args.limit] if args.limit else kreyol_evalset.ITEMS
    for category, line, prime in items:
        brain = chat_eval.new_brain()
        language.reset()
        if prime:
            chat_eval.turn(brain, prime, ())
        AUDITS.clear()
        FLAGGED.clear()
        row = chat_eval.turn(brain, line, ())
        reply = row["jarvis"]
        canned = CANNED in reply or reply.strip() in getattr(kreyol_bank, "HONEST", ())
        rows.append({"category": category, "you": line, "jarvis": reply, "seconds": row["seconds"],
                     "canned": canned, "again": "kreyol_again" in AUDITS, "fell_back": "kreyol_fallback" in AUDITS,
                     "last_resort": "kreyol_honest" in AUDITS, "empty": not reply.strip(),
                     "pc_action": bool(row["pc_action"]), "flagged_first": list(FLAGGED), "wrong_language": [] if canned else wrong_language(reply, line)})
    by: dict = {}
    for r in rows:
        c = by.setdefault(r["category"], {"n": 0, "canned": 0, "fell_back": 0, "again": 0, "wrong_language": 0})
        c["n"] += 1
        c["canned"] += r["canned"]
        c["fell_back"] += r["fell_back"]
        c["again"] += r["again"]
        c["wrong_language"] += bool(r["wrong_language"])
    n = len(rows)
    summary = {"label": args.label, "n": n, "canned_line": sum(r["canned"] for r in rows),
               "canned_rate": round(sum(r["canned"] for r in rows) / n, 3),
               "any_fallback_path": sum(r["fell_back"] for r in rows),
               "retried": sum(r["again"] for r in rows), "last_resort": sum(r["last_resort"] for r in rows),
               "wrong_language_accepted": sum(bool(r["wrong_language"]) for r in rows),
               "empty": sum(r["empty"] for r in rows), "pc_actions": sum(r["pc_action"] for r in rows),
               "by_category": by}
    out = ROOT / "runs" / f"kreyol-eval2-{args.label}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
