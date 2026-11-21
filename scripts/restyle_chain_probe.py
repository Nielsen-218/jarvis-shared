r"""A chain of six restyle follow-ups after a question, on the real local model (chat_latency_probe's sandbox, synthetic lines).

    .venv\Scripts\python.exe -m scripts.restyle_chain_probe --out runs\restyle-chain.json

For every reply: seconds, prompt tokens, model calls, the reply, and whether it is still about the subject. Criterion: the reply
uses a content word (stop words out, stemmed) of the question or two of the first answer's; meta flags commentary about the request."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import chat_latency_probe as probe  # noqa: E402
import re  # noqa: E402
from core import intelligence  # noqa: E402


def words(text: str) -> set:
    return {intelligence._stem(w) for w in re.findall(r"[a-z]{3,}", text.casefold())
            if w not in intelligence._STOP and intelligence._stem(w) not in intelligence._STOP_STEMS}

QUESTIONS = ["Why is the sky blue?", "How does a vaccine work?", "What is inflation?"]
RESTYLES = ["Shorter.", "Simpler.", "Give me an example.", "Explain that differently.", "More detail.", "Say that again."]


META = re.compile(r"(?:you asked|you wanted|you want|the previous|the first explanation|earlier attempt|as i said|to rephrase|i will rephrase|here is that)", re.I)
# Measured on the reply as it comes out of the brain, with patterns of its own (not the code under test).
OPENER = re.compile(r"^\W*(?:you\s+(?:asked|wanted|requested)|as\s+requested|here(?:'s|\s+is|\s+are)|let\s+me|"
                    r"(?:the|my|your)\s+(?:previous|earlier|last|first|original)\s+\w+|to\s+(?:rephrase|restate)|in\s+other\s+words|"
                    r"(?:sure|certainly|of\s+course|absolutely))", re.I)
ECHO = re.compile(r"^\W*(?:shorter|simpler|more\s+detail|longer|an?\s+example|example|again|differently)\s*[.:!\-—]", re.I)


def on_subject(question: str, answer: str, reply: str) -> bool:
    """True when the reply uses a content word of the question, or at least two of the first answer's content words."""
    r = words(reply)
    return bool(words(question) & r) or len(words(answer) & r) >= 2


def wordcount(text: str) -> int:
    return len(text.split())


def length_ok(line: str, rows: list, i: int) -> str:
    """'ok' / 'broken' / '-' for the two length rules: Shorter is strictly shorter than the latest reply, Simpler is not longer
    than the original answer and at most 60 words unless the original is shorter."""
    now = wordcount(rows[i]["reply"])
    if line == "Shorter.":
        return "ok" if now < wordcount(rows[i - 1]["reply"]) else "broken"
    if line == "Simpler.":
        return "ok" if now <= min(60, wordcount(rows[0]["reply"])) else "broken"
    return "-"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--questions", nargs="*", default=QUESTIONS)
    a, _ = ap.parse_known_args()
    report = []
    for question in a.questions:
        rows = probe.run([question] + RESTYLES, False, True)
        first = rows[0]["reply"]
        for row in rows[1:]:
            row["on_subject"] = on_subject(question, first, row["reply"])
            row["meta"] = bool(META.search(row["reply"]))
            row["opener"] = bool(OPENER.search(row["reply"]))
            row["echo"] = bool(ECHO.search(row["reply"]))
            row["words"] = wordcount(row["reply"])
            row["length_rule"] = length_ok(row["line"] if "line" in row else RESTYLES[rows.index(row) - 1], rows, rows.index(row))
            row["ends_clean"] = row["reply"].rstrip()[-1:] in ".!?\"'"
        report.append({"question": question, "rows": rows})
        print(f"CHAIN {question}: " + " ".join(f"{r['on_subject']}" for r in rows[1:]), flush=True)
        for line, r in zip(RESTYLES, rows[1:]):
            print(f"  {line:<22} {r['seconds']:>5}s {r['words']:>4}w opener={r['opener']!s:<5} echo={r['echo']!s:<5} length={r['length_rule']:<6} clean_end={r['ends_clean']}", flush=True)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
