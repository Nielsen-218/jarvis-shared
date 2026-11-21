"""How well does the LOCAL model answer in Haitian Creole? Thirty-two everyday exchanges through the real Brain.

    .venv\\Scripts\\python.exe scripts\\kreyol_eval.py                 (the guide, the phrase bank and the reply check)
    .venv\\Scripts\\python.exe scripts\\kreyol_eval.py --baseline      (as before the bank: the guide only, no check)
    .venv\\Scripts\\python.exe scripts\\kreyol_eval.py --no-guide      (the bare "answer in Haitian Creole" note)

Nonsense words are counted by core.kreyol_bank.check on what was SAID: foreign (French, Spanish or English forms) and
unknown (in no Creole list here: possibly invented, possibly a real rarer word). The same judge in every mode.

The sandbox is scripts/chat_eval.py's (temporary data, no PC action can run, local model only). Every line is
synthetic conversation. Per reply: is it Creole by audio.language.identify (a clear Creole sentence), how many French
or English words slipped in, and French spelling habits (c'est, qu-, je, -tion, silent endings). A last English line
checks that he comes back to English. Writes runs/kreyol-eval-<label>.json and prints a summary. A judge by word
lists is rough: read the replies too.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from scripts import chat_eval  # noqa: E402  (sets up the sandbox on import)

from audio import language  # noqa: E402
from core import intelligence, kreyol_bank  # noqa: E402

AUDITS: list = []
_audit = intelligence.audit


def _counted_audit(role, what, *a, **k):
    AUDITS.append(str(what))
    return _audit(role, what, *a, **k)


intelligence.audit = _counted_audit

CHATS = [
    ["Bonjou Jarvis, kijan ou ye jodi a?", "Mwen fatige anpil, mwen pa t dòmi byen yè swa.",
     "Kisa ou panse mwen ta dwe fè pou m dòmi pi byen?", "Mèsi anpil pou konsèy la."],
    ["Mwen grangou, kisa mwen ka kwit aswè a ak diri epi poul?", "Konbyen tan sa ap pran pou m fè li?",
     "Eske ou konn pale kreyòl byen?", "Rakonte m yon ti blag."],
    ["Mwen ap aprann pwograme, men li difisil pou mwen.", "Poukisa syèl la ble?",
     "Ki kapital peyi Ayiti?", "Dakò, n a pale pita. Bòn nwit."],
    ["Bonswa Jarvis, mwen fèk rantre lakay mwen.", "Jounen an te long anpil nan travay la.",
     "Kisa ou ta fè si ou te nan plas mwen aswè a?", "Ou gen rezon, mwen pral repoze."],
    ["Sak pase Jarvis?", "Mwen tris jodi a, mwen pa konnen poukisa.",
     "Eske ou janm santi ou tris tou?", "Mèsi, sa fè m santi m pi byen."],
    ["Mwen vle aprann jwe gita.", "Konbyen tan sa ka pran pou m jwe byen?",
     "Ki pi bon lè pou m pratike, maten oswa aswè?", "Dakò, m ap kòmanse demen."],
    ["Manman m ap vin wè m dimanch.", "Kisa mwen ta dwe kwit pou li?",
     "Li renmen pwason ak bannann.", "Sa se yon bon lide, mèsi."],
    ["Poukisa lapli tonbe?", "Kijan pou m kenbe kò m an sante?",
     "Ki diferans ki genyen ant yon chat ak yon chen?", "Ou konn anpil bagay. Orevwa."],
]
BACK = "Thanks. Now tell me in one sentence why the sky is blue."
_FRENCH_HABIT = re.compile(r"\b(?:c'est|je|j'ai|vous|nous sommes|est|une|les|des|très|avec|pour|que|qui|quoi|oui|"
                           r"bonjour|merci|\w+tion|\w+eux|\w+ais|\w+ait|\w+ez)\b", re.I)
_CREOLE_SAME = {"pour", "nous"}          # never Creole spellings; kept in the pattern on purpose


def judge(reply: str) -> dict:
    code, sure = language.identify(reply)
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœæñáíóúò'-]+", reply.lower())
    french = sorted({m.group(0).lower() for m in _FRENCH_HABIT.finditer(reply)})
    english = sorted({w for w in words if w in language._ENGLISH and w not in language._SETS["ht"]
                      and w not in language._SETS["fr"] and len(w) > 2})
    found = kreyol_bank.check(reply)
    return {"language": code, "sure": sure, "creole": code == "ht" and sure >= language.CLEAR, "words": len(words),
            "french_habits": french, "english_words": english, "empty": not reply.strip(),
            "foreign": found["foreign"], "unknown": found["unknown"], "nonsense": kreyol_bank.bad(found)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-guide", action="store_true")
    parser.add_argument("--baseline", action="store_true", help="as before the phrase bank: no grounding, no reply check")
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)
    if args.no_guide:
        language.KREYOL_GUIDE = "Write Haitian Creole, not French."
    if args.baseline or args.no_guide:
        kreyol_bank.grounding = lambda said, uid=None: ""
        kreyol_bank.is_note = lambda note: False
    label = args.label or ("bare" if args.no_guide else "baseline" if args.baseline else "bank")
    chat_eval.seed()
    rows = []
    for chat in CHATS:
        brain = chat_eval.new_brain()
        language.reset()
        for said in chat:
            AUDITS.clear()
            row = chat_eval.turn(brain, said, ())
            row.update(judge(row["jarvis"]))
            row["again"], row["fell_back"] = "chat_again_kreyol" in AUDITS or "kreyol_again" in AUDITS, "kreyol_fallback" in AUDITS
            rows.append({k: row[k] for k in ("you", "jarvis", "seconds", "first_words_s", "language", "sure", "creole",
                                             "words", "french_habits", "english_words", "empty", "pc_action", "tools",
                                             "foreign", "unknown", "nonsense", "again", "fell_back")})
        if chat is CHATS[-1]:
            back = chat_eval.turn(brain, BACK, ())
            came_back = language.identify(back["jarvis"])[0] == "en"
    creole = sum(r["creole"] for r in rows)
    clean = sum(r["creole"] and not r["french_habits"] and not r["english_words"] for r in rows)
    summary = {"label": label, "turns": len(rows), "replies_in_creole": creole, "creole_with_no_french_or_english": clean,
               "replies_with_nonsense (a foreign form, or 2+ unknown words)": sum(r["nonsense"] for r in rows),
               "replies_with_any_flagged_word": sum(bool(r["foreign"] or r["unknown"]) for r in rows),
               "foreign_words": sum(len(r["foreign"]) for r in rows), "unknown_words": sum(len(r["unknown"]) for r in rows),
               "flagged_per_100_words": round(100 * sum(len(r["foreign"]) + len(r["unknown"]) for r in rows)
                                              / max(1, sum(r["words"] for r in rows)), 2),
               "median_reply_words": sorted(r["words"] for r in rows)[len(rows) // 2],
               "written_again": sum(r["again"] for r in rows), "fell_back_to_the_bank": sum(r["fell_back"] for r in rows),
               "pc_actions": sum(bool(r["pc_action"]) for r in rows),
               "empty": sum(r["empty"] for r in rows), "back_to_english": came_back, "english_reply": back["jarvis"],
               "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2]}
    out = ROOT / "runs" / f"kreyol-eval-{label}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=1))
    for r in rows:
        if not r["creole"] or r["french_habits"] or r["english_words"] or r["foreign"] or r["unknown"]:
            print(f"  - {r['you'][:40]!r}: lang={r['language']} french={r['french_habits']} english={r['english_words']} "
                  f"foreign={r['foreign']} unknown={r['unknown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
