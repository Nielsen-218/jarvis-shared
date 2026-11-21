"""Scores the saved output of scripts/study_eval.py with the rubric's mechanical criteria, and prints a compact reading
copy of every ladder for the human judge (factual correctness, true analogy, "genuinely simpler" are judged by reading).

    .venv\\Scripts\\python.exe -m scripts.study_score DIR [--read] [--topic NAME]

Mechanical criteria (pass = True):
  model     every rung came from the local model (not the material's own words)
  length    sentence and word counts within what the rung is for (speech)
  simpler   not harder to read than the rung above (Flesch-Kincaid grade, +1.5 grace), and not an echo of it
  on_topic  names the topic or shares its vocabulary (the lowest rungs may use an analogy)
  analogy   the analogy field is filled from middle school down to pre-K
  board     a board built up in steps (3+ at college/high, 2+ at middle..pre-K), every part labelled, arrows between parts;
            from the model, repaired or worked (not the generic fallback)
  order     the board's step captions follow the spoken explanation, in order (silent beats ignored; a worked board narrates its own script)
  numbers   (topics with a worked example) the college and high-school boards carry real numbers"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import study_board, study_ladder, study_store  # noqa: E402
from scripts import study_eval_set as S  # noqa: E402

LIMITS = {"college": (6, 150), "high": (6, 150), "middle": (5, 110), "elementary": (5, 90), "prek": (3, 45), "one": (2, 14)}


def syllables(word: str) -> int:
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    n = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and not word.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def grade(text: str) -> float:
    words = re.findall(r"[A-Za-z']+", text)
    sents = max(1, len(study_board.sentences(text)))
    if not words:
        return 0.0
    return 0.39 * len(words) / sents + 11.8 * sum(syllables(w) for w in words) / len(words) - 15.59


def words_of(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", re.sub(r"\([^)]*\)", " ", text or ""))


def echo(a: str, b: str) -> float:
    x, y = set(study_store.tokens(a)), set(study_store.tokens(b))
    return len(x & y) / len(x | y) if x and y else 0.0


def on_topic(topic: str, text: str, analogy: str, rung: str) -> bool:
    keys = set(study_store.tokens(topic)) | set(study_store.tokens(S.NOTES.get(topic, "")))
    hit = len(set(study_store.tokens(text)) & keys)
    if rung in ("prek", "one"):
        return hit >= 1 or bool(analogy) or any(w in (text or "").lower() for w in study_store.tokens(topic))
    return hit >= 2


def board_checks(topic: str, row: dict) -> dict:
    b = row.get("board") or {}
    steps = b.get("steps") or []
    ops = [op for s in steps for op in s.get("ops", [])]
    solid = [o for o in ops if o["op"] in ("box", "circle", "icon")]
    labelled = [o for o in solid if o.get("text")]
    low = row["rung"] in ("prek", "one")
    ok_model = b.get("source") in ("model", "hand", "repaired", "worked")
    need = {"college": 3, "high": 3, "middle": 2, "elementary": 2, "prek": 2, "one": 1}.get(row["rung"], 2)
    ok_struct = (len(steps) >= need and len(labelled) >= max(1, len(solid)) and
                 (low or any(o["op"] in ("arrow", "move") for o in ops) or len(solid) < 2))
    lines = study_board.sentences(row.get("text") or "")
    pos = []
    for s in steps:
        say = s.get("say") or ""
        if not say:                                   # a beat drawn in silence while the sentence before it is still being said
            continue
        pos.append(max(range(len(lines)), key=lambda i: echo(say, lines[i]), default=0) if lines else 0)
    ok_order = b.get("source") == "worked" or (all(a <= b2 for a, b2 in zip(pos, pos[1:])) and (not steps or len(steps) >= min(2, len(lines)) or low))
    nums = any(re.search(r"\d", o.get("text") or "") for o in solid + [o for o in ops if o["op"] == "label"])
    return {"board": ok_model and ok_struct, "board_model": ok_model, "order": ok_order, "numbers": nums}


def score_row(topic: str, row: dict, prev: dict | None) -> dict:
    rung, text = row.get("rung") or "", row.get("text") or ""
    sents, nwords = len(study_board.sentences(re.sub(r"\([^)]*\)", " ", text) or text)), len(words_of(text))
    lim = LIMITS.get(rung, (6, 150))
    out = {"model": row.get("source") == "model", "length": 0 < sents <= lim[0] and nwords <= lim[1]}
    out["simpler"] = True if not prev else (grade(text) <= grade(prev["text"]) + 1.5 and echo(text, prev["text"]) < 0.55)
    out["on_topic"] = on_topic(topic, text, row.get("analogy") or "", rung)
    out["analogy"] = bool(row.get("analogy")) if rung in ("middle", "elementary", "prek") else None
    out.update(board_checks(topic, row))
    if topic not in S.WORKED or rung not in ("college", "high"):
        out["numbers"] = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--read", action="store_true")
    ap.add_argument("--topic", default="")
    args = ap.parse_args()
    files = sorted(Path(args.dir).glob("*.json"))
    tally, per_topic = {}, {}
    extra = {"rows": 0, "calls": 0, "seconds": 0.0, "one_step": 0, "upper": 0, "srcs": {}, "boards": {}}
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if args.topic and d["topic"] != args.topic:
            continue
        prev = None
        fails = 0
        if args.read:
            print(f"\n######## {d['topic']}")
        for row in d["rows"]:
            r = score_row(d["topic"], row, prev)
            extra["rows"] += 1
            extra["calls"] += row.get("model_calls") or 0
            extra["seconds"] += row.get("seconds") or 0
            extra["srcs"][row.get("source")] = extra["srcs"].get(row.get("source"), 0) + 1
            bsrc = (row.get("board") or {}).get("source")
            extra["boards"][bsrc] = extra["boards"].get(bsrc, 0) + 1
            if row.get("rung") in ("college", "high", "middle", "elementary"):
                extra["upper"] += 1
                extra["one_step"] += len((row.get("board") or {}).get("steps", [])) <= 1
            for k, v in r.items():
                if v is not None:
                    t = tally.setdefault(k, [0, 0])
                    t[1] += 1
                    t[0] += bool(v)
                    fails += (not v) and k not in ("board_model",)
            if args.read:
                b = row.get("board") or {}
                shapes = [f"{o['op']}:{o.get('text', '')}" for s in b.get("steps", []) for o in s["ops"] if o["op"] in ("box", "circle", "icon", "label")]
                print(f"[{row['rung']}] ({row['source']}, {row['seconds']}s) {row.get('error', '')} fails={[k for k, v in r.items() if v is False]}\n  {row['text']}\n  analogy={row.get('analogy')!r} limit={row.get('limit')!r}"
                      f"\n  board({b.get('source')}) steps={len(b.get('steps', []))} shapes={shapes[:12]}")
            prev = row if row.get("text") else prev
        per_topic[d["topic"]] = fails
    print("\ncriterion pass rates")
    for k, (ok, n) in sorted(tally.items()):
        print(f"  {k:12s} {ok}/{n}  {100 * ok / n:.0f}%")
    n = max(1, extra["rows"])
    print(f"explanation sources {extra['srcs']}; board sources {extra['boards']}; one-step boards at college..elementary {extra['one_step']}/{extra['upper']}")
    print(f"model calls per rung {extra['calls'] / n:.2f}, seconds per rung {extra['seconds'] / n:.1f}")
    print("worst topics (mechanical failures):", sorted(per_topic.items(), key=lambda kv: -kv[1])[:8])


if __name__ == "__main__":
    main()
