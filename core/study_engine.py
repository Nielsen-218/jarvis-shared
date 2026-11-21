"""The study engine: questions grounded in a passage, grading, explanations on a six-rung ladder with a whiteboard scene, flashcards with spaced
repetition, and a per-concept mastery record that drives "what should I review".

Pure Python. Every function that needs wording takes `model`: a callable (prompt) -> text, or None. The model only
writes wording; code decides what is kept:
  * a generated question is kept only if the sentence it quotes as proof really is in the passage (grounding gate);
  * a multiple-choice answer is graded in code, never by the model;
  * when the model is missing, fails, or returns something unusable, a deterministic version built from the passage
    itself is used instead, so a quiz always works.
The local model drops JSON schemas when thinking is off, so each prompt spells out the shape and parse_json reads the
first JSON value it finds in the reply.

Progress is an event log, one file per machine and person (data/users/<uid>/study/events-<machine>.json). Each machine
appends only to its own file; the state (mastery, cards, due dates) is recomputed by replaying every machine's events
in time order. Two machines can therefore both record study without ever writing the same file.

Scheduler: SM-2 (Wozniak 1990), the documented classic. quality 0-5; below 3 the card starts over tomorrow; otherwise
intervals go 1 day, 6 days, then previous interval x ease; ease moves by 0.1 - (5-q)(0.08 + (5-q)0.02), floor 1.3.
Mastery: Bayesian knowledge tracing per concept (prior 0.2, learn 0.15, slip 0.1, guess 0.25 for multiple choice and
0.1 for a written answer)."""
import hashlib
import json
import random
import re
import threading
import time
from datetime import date, datetime, timedelta

from core import storage, study_board, study_calc, study_check, study_ladder, study_store

LEVELS = study_ladder.RULES          # the six rungs of the explanation ladder (core/study_ladder)
KINDS = ("mcq", "short", "scenario")
clock = datetime.now                 # tests pin this
mono = time.monotonic                # the explanation's time budget runs on this; tests pin it too
BUDGET_S = 60                        # no new model call is started for one explanation once this many seconds are spent


# ---- reading what the model wrote -------------------------------------------------------------------------------
def parse_json(text):
    """The first JSON object or array in a model reply (after any <think> block, inside or outside a code fence).
    -> the value, or None."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S)
    decoder = json.JSONDecoder()
    for m in re.finditer(r"[{\[]", text):
        try:
            return decoder.raw_decode(text, m.start())[0]
        except ValueError:
            continue
    return None


def _salvage(text):
    """A reply whose JSON is broken somewhere (a missing comma in the board, a cut-off end) but whose explanation is intact
    -> {"explanation", "analogy", "limit", "check"} read from the fields that are whole, else None. parse_json would
    otherwise hand back a board's inner object, and a good explanation was lost as "unreadable" (live probe, 2026-11-13)."""
    if not isinstance(text, str) or '"explanation"' not in text:
        return None
    out = {}
    for key in ("explanation", "analogy", "limit", "check"):
        m = re.search(r'"' + key + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.S)
        if m:
            try:
                out[key] = json.loads('"' + m.group(1).replace("\n", " ") + '"')
            except ValueError:
                out[key] = m.group(1)
    return out if out.get("explanation") else None


def _try(model, prompt):
    """-> (the JSON the model wrote or None, whether the call itself failed). A call that raised (a timeout, a server error)
    is not the same as a reply that could not be read: asking a dead model again only doubles the wait."""
    if model is None:
        return None, False
    try:
        reply = model(prompt)
        found = parse_json(reply)
        if '"explanation"' in str(reply) and not (isinstance(found, dict) and "explanation" in found):
            found = _salvage(reply) or found
        return found, False
    except Exception:                 # noqa: BLE001 - a model that is down means the deterministic path, never a crash
        return None, True


def _ask(model, prompt):
    return _try(model, prompt)[0]


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def grounded(quote, source) -> bool:
    """Is this quote really in the source? (case, spacing and punctuation aside; at least three words)"""
    q = _norm(quote)
    return len(q.split()) >= 3 and q in _norm(source)


def _text(value, limit=400):
    return " ".join(str(value or "").split())[:limit]


def concept_key(course, chapter, label):
    return f"{course}|{study_store.slug(chapter)}|{study_store.slug(label)}"


# ---- questions --------------------------------------------------------------------------------------------------
_SHAPES = {
    "mcq": ('{"concept": "2-4 word name of the idea tested", "question": "...", "answer": "the correct option", '
            '"distractors": ["wrong option", "wrong option", "wrong option"], '
            '"quote": "one sentence copied word for word from SOURCE that proves the answer", "why": "one sentence"}'),
    "short": ('{"concept": "2-4 word name of the idea tested", "question": "...", "answer": "a model answer in 1-2 sentences", '
              '"points": ["key point the answer must contain", "key point"], '
              '"quote": "one sentence copied word for word from SOURCE that proves the answer"}'),
}
_TASKS = {
    "mcq": "Write ONE multiple-choice question that tests understanding, not word-matching. Each wrong option must be "
           "plausible: the mistake a student who only half-learned this would make. All four options the same style and length.",
    "short": "Write ONE short-answer question the student answers in a sentence or two in their own words.",
    "scenario": "Write ONE scenario question: describe a realistic situation at work (a symptom, a ticket, a request) "
                "and ask what the student would do or conclude. It must be answerable from SOURCE.",
}


def _question_prompt(passage, kind, focus=""):
    return (f"You are a professor writing an exam question for an IT student, using ONLY the SOURCE below.\n\n"
            f"SOURCE ({study_store.cite(passage)}):\n\"\"\"\n{passage['text']}\n\"\"\"\n\n{_TASKS[kind]}\n"
            f"Do not mention 'the source' or 'the passage' in the question.\n"
            + (f"Ask about this part of it: {focus}.\n" if focus else "") +
            f"Reply with JSON only, exactly this shape:\n{_SHAPES['mcq' if kind == 'mcq' else 'short']}")


_DEFINES = re.compile(r"^(?:(?:An?|The) )?(?P<term>[A-Za-z0-9][A-Za-z0-9/+.-]*(?: [A-Za-z0-9/+.-]+){0,3}?),? "
                      r"(?:is (?=an? |the |one of )|are (?=the |\w+ (?:that|which|used|for) )|means |refers to |stands for |describes )"
                      r"(?P<rest>.{20,})$")    # "X is a ...": a definition. "X is flooded to ...": a statement, not one.
_NOT_TERMS = set("it this that there these those he she they we you what which here one each some most many".split())


def definitions(passage) -> list[tuple[str, str]]:
    """[(term, the sentence that defines it)] found in a passage ("A switch is ...", "DNS stands for ...")."""
    if passage.get("concept"):
        return [(passage["concept"], passage["text"])]
    out = []
    for sentence in study_store._SENTENCE.split(passage["text"]):
        m = _DEFINES.match(sentence.strip())
        if m and m.group("term").split()[0].lower() not in _NOT_TERMS:
            out.append((m.group("term"), sentence.strip()))
    return out


def _mask(text, term):
    for part in sorted({term, *term.split(" and ")}, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(part)}(?:s|es)?\b", "_____", text, flags=re.I)
    return text


def _base(passage, kind, course, concept, source):
    concept = _text(concept, 60) or passage.get("section") or passage.get("chapter") or "this passage"
    return {"kind": kind, "course": course, "chapter": passage.get("chapter") or "", "concept": concept,
            "key": concept_key(course, passage.get("chapter") or "", concept), "cite": study_store.cite(passage),
            "page": passage.get("page"), "passage_id": passage.get("id"), "source": source}


def _finish(q, rng):
    if q["kind"] == "mcq":
        options = [q["answer"]] + q.pop("distractors")
        rng.shuffle(options)
        q["options"], q["answer_index"] = options, options.index(q["answer"])
    q["id"] = hashlib.sha256(json.dumps([q["stem"], q["answer"], q["passage_id"]]).encode()).hexdigest()[:12]
    return q


def fallback_question(passage, kind, course, siblings=(), rng=None):
    """A question built from the passage's own words, no model. Multiple choice when the passage defines a term and
    three other terms from the course can stand as wrong options; otherwise a written question."""
    rng = rng or random.Random()
    found = definitions(passage)
    if found:
        term, sentence = found[passage.get("variant", 0) % len(found)]      # asked again: its next defined term
        if kind == "mcq":
            others = [t for t, _ in found if _norm(t) != _norm(term)]       # the passage's other terms are the closest
            for sib in sorted(siblings, key=lambda s: s.get("chapter") != passage.get("chapter")):   # then its chapter's
                for other, _ in definitions(sib):
                    if _norm(other) != _norm(term) and _norm(other) not in map(_norm, others):
                        others.append(other)
            if len(others) >= 3:
                near = others[:5]
                picks = rng.sample(near, 3)
                q = _base(passage, "mcq", course, term, "passage")
                q.update(stem=f"Which term fits this description? \"{_mask(sentence, term)}\"", answer=term,
                         distractors=picks, quote=sentence, why=sentence)
                return _finish(q, rng)
        q = _base(passage, "short", course, term, "passage")
        q.update(stem=f"In your own words: what is {term}, and what does it do?" if passage.get("concept")
                 else f"Explain what your material says about {term}.", answer=sentence, points=[], quote=sentence)
        return _finish(q, rng)
    sentences = study_store._SENTENCE.split(passage["text"])
    q = _base(passage, "short", course, passage.get("section") or passage.get("chapter"), "passage")
    q.update(stem=f"From {q['cite']}: what is the main point about {q['concept']}?", answer=" ".join(sentences[:2]),
             points=[], quote=sentences[0])
    return _finish(q, rng)


def make_question(passage, kind="mcq", course="", model=None, siblings=(), rng=None):
    """One question from one passage. The model's draft is kept only when it is complete and its proof quote is in the
    passage; anything else gives the deterministic question. Never returns None."""
    rng = rng or random.Random()
    kind = kind if kind in KINDS else "mcq"
    built_in = bool(passage.get("concept"))
    if built_in and kind == "mcq":
        return fallback_question(passage, kind, course, siblings, rng)     # the outline's own terms: instant, exact
    found = definitions(passage)
    focus = found[passage["variant"] % len(found)][0] if passage.get("variant") and found else ""
    draft = _ask(model, _question_prompt(passage, kind, focus))
    if isinstance(draft, dict):
        stem, answer, quote = _text(draft.get("question"), 600), _text(draft.get("answer"), 500), _text(draft.get("quote"), 500)
        if stem and answer and grounded(quote, passage["text"]):
            q = _base(passage, kind, course, passage["concept"] if built_in else draft.get("concept"), "model")
            q.update(stem=stem, answer=answer, quote=quote, why=_text(draft.get("why"), 300))
            if kind == "mcq":
                wrong = []
                for d in draft.get("distractors") if isinstance(draft.get("distractors"), list) else []:
                    d = _text(d, 300)
                    if d and _norm(d) != _norm(answer) and _norm(d) not in map(_norm, wrong):
                        wrong.append(d)
                if len(wrong) >= 3:
                    q["distractors"] = wrong[:3]
                    return _finish(q, rng)
            else:
                points = draft.get("points") if isinstance(draft.get("points"), list) else []
                q["points"] = [_text(p, 200) for p in points if _text(p)][:4]
                return _finish(q, rng)
    return fallback_question(passage, kind, course, siblings, rng)


# ---- grading ----------------------------------------------------------------------------------------------------
_GIVE_UP = re.compile(r"^\W*(?:i (?:do not|don'?t|dont) know|no idea|not sure|idk|skip(?: it| this)?|pass|next(?: question| one)?|"
                      r"i give up|tell me(?: the answer)?|just tell me|show me the answer)\W*$", re.I)
_ORDINALS = {"first": 0, "second": 1, "third": 2, "fourth": 3, "last": -1, "1": 0, "2": 1, "3": 2, "4": 3,
             "one": 0, "two": 1, "three": 2, "four": 3}


def gave_up(response) -> bool:
    return not _text(response) or bool(_GIVE_UP.match(_text(response)))


def choice(question, response):
    """Which option he meant: "B", "option b", "the second one", or the option's own words. -> index or None."""
    said = _norm(response)
    options = question["options"]
    m = re.fullmatch(r"(?:(?:i think|i say|maybe|probably|is it|its|it s|my answer is|i ll go with|i choose|i pick|going with) )*"
                     r"(?:(?:the )?(?:option|answer|letter|number) )?([a-d])(?: (?:i think|please|final answer))?", said)
    if m:
        index = "abcd".index(m.group(1))
        return index if index < len(options) else None
    m = re.fullmatch(r"(?:the )?(?:option |answer |number )?(first|second|third|fourth|last|[1-4]|one|two|three|four)(?: one| option)?", said)
    if m:
        index = _ORDINALS[m.group(1)] % len(options)
        return index if index < len(options) else None
    exact = [n for n, o in enumerate(options) if _norm(o) and (_norm(o) == said or _norm(o) in said)]
    if len(exact) == 1:
        return exact[0]
    words = set(study_store.tokens(response))
    scores = [len(words & set(study_store.tokens(o))) / (len(set(study_store.tokens(o))) or 1) for o in options]
    best = max(scores)
    return scores.index(best) if best >= 0.6 and scores.count(best) == 1 else None


def _coverage(reference, response):
    want = set(study_store.tokens(reference))
    return len(want & set(study_store.tokens(response))) / len(want) if want else 0.0


def grade(question, response, model=None) -> dict:
    """-> {correct, score (0, 0.5 or 1), feedback, answer, quote, cite, missing, understood}. `understood` is False
    when a multiple-choice reply named no option (ask again rather than mark it wrong)."""
    out = {"correct": False, "score": 0.0, "feedback": "", "answer": question["answer"], "quote": question.get("quote", ""),
           "cite": question["cite"], "missing": [], "understood": True, "why": question.get("why", "")}
    if gave_up(response):
        out["feedback"] = "No problem. Here is the answer."
        return out
    if question["kind"] == "mcq":
        picked = choice(question, response)
        if picked is None:
            return dict(out, understood=False, feedback="Which option: A, B, C or D?")
        out["picked"] = picked
        if picked == question["answer_index"]:
            return dict(out, correct=True, score=1.0, feedback="Correct.")
        return dict(out, feedback=f"Not quite. It is {'ABCD'[question['answer_index']]}: {question['answer']}.")
    points = question.get("points") or []
    verdict = _ask(model, (
        "You are a fair professor grading a student's answer. Use ONLY the reference; do not add outside facts.\n"
        f"QUESTION: {question['stem']}\nREFERENCE ANSWER: {question['answer']}\n"
        + (f"KEY POINTS: {json.dumps(points)}\n" if points else "")
        + f"SOURCE SENTENCE: {question.get('quote', '')}\nSTUDENT ANSWER: {_text(response, 1200)}\n\n"
        "Wording does not matter, meaning does. score 2 = right in substance, 1 = partly right or incomplete, 0 = wrong or off topic.\n"
        'Reply with JSON only, exactly this shape:\n{"score": 0, "missing": ["what the answer left out, if anything"], '
        '"feedback": "one or two sentences spoken to the student: what was right first, then what to fix"}'))
    if isinstance(verdict, dict) and verdict.get("score") in (0, 1, 2, "0", "1", "2"):
        score = int(verdict["score"])
        missing = [_text(m, 200) for m in verdict.get("missing", []) if _text(m)][:3] if isinstance(verdict.get("missing"), list) else []
        return dict(out, correct=score == 2, score=score / 2, missing=missing,
                    feedback=_text(verdict.get("feedback"), 500) or ("Correct." if score == 2 else "Not quite."))
    reference = " ".join(points) or question["answer"]
    cover = _coverage(reference, response)                # no model: how much of the reference he covered...
    said, want = len(study_store.tokens(response)), len(set(study_store.tokens(reference)))
    focused = said <= max(12, 3 * want)                   # ...in an answer about it: a long ramble that happens to contain the words is not "correct"
    if cover >= 0.5 and focused:
        return dict(out, correct=True, score=1.0, feedback="That covers it.")
    if cover >= 0.25:
        return dict(out, score=0.5, feedback="Partly. Compare yours with the reference answer.")
    return dict(out, feedback="That does not match the material.")


# ---- explanations -----------------------------------------------------------------------------------------------
def _same_analogy(a, b):
    x, y = set(study_store.tokens(a)), set(study_store.tokens(b))
    return bool(x and y) and len(x & y) / len(x | y) >= 0.5


_OWN_WORDS = {"college": 4, "high": 3, "middle": 2, "elementary": 2, "prek": 1, "one": 1}     # sentences of his material, no model


def explain(topic, passages, level="college", model=None, avoid=(), turn=0, previous=None, another=False) -> dict:
    """An explanation of `topic` from the passages given, at one rung of the ladder (core/study_ladder), with the
    whiteboard scene that draws it (core/study_board).
    previous: {"level", "text"} of the explanation he just had for this topic: the prompt forbids reusing its
    comparison. avoid: comparisons already used for this topic. another: he asked for another way at the same rung.
    -> {text, analogy, limit, check, cite, level, label, source, templated, board}. source: "model"; "builtin" (the
    built-in course's own rung: hand-written for the networking chapter, otherwise templated=True); "passage" (his
    material's own words); "none". A hand-written rung is given as it is, without a model call, unless he asked for
    another way."""
    rung = level if level in study_ladder.RUNGS else study_ladder.START
    out = {"text": "", "analogy": "", "limit": "", "check": "", "cite": study_store.cite(passages[0]) if passages else "",
           "level": rung, "label": study_ladder.LABELS[rung], "source": "none", "templated": False, "board": None}
    if not passages:
        return out
    seed = passages[0] if passages[0].get("concept") else None
    concept = seed["concept"] if seed else ""
    rungs, written = study_ladder.builtin(concept, seed["text"], seed.get("chapter") or "") if seed else ({}, False)

    def built_in():
        text = study_check.finalize(rung, rungs[rung])
        if not study_check.names(text, concept):          # the hand-written small rungs tell a story: they still say what it is about
            text = study_check.finalize(rung, (f"{concept}! " if rung == "one" else f"This is {concept}. ") + text)
        return dict(out, text=text, source="builtin", templated=not written,
                    board=study_board.for_explanation(topic, text, rung, concept))

    if written and not another:
        return built_in()
    general = bool(passages[0].get("general"))         # his library has nothing on it: the model's own knowledge, said so
    source = ("There is no course material on this topic. Use your own general knowledge, and be accurate and plain." if general
              else "\n\n".join(f"[{study_store.cite(p)}]\n{p['text']}" for p in passages[:3]))
    plain = "" if general else "\n".join(p["text"] for p in passages[:3])
    ex = study_calc.example(topic, plain)                 # the numbers of a worked example are computed here, not by the model
    facts = ("Use EXACTLY these figures in your worked example, in this order (they were computed; do not recalculate or change them): "
             + " ".join(ex["facts"]) + "\n") if ex and rung in ("college", "high") else ""
    before, down = "", False
    if previous and previous.get("text"):
        was = previous.get("level")
        down = was in study_ladder.RUNGS and study_ladder.RUNGS.index(was) < study_ladder.RUNGS.index(rung)
        before = (f"The explanation the student just had ({study_ladder.LABELS.get(was, 'another')} level) was:\n"
                  f"\"\"\"\n{_text(previous['text'], 600)}\n\"\"\"\n")
        before += ("This one is exactly ONE step simpler: the same idea, said with fewer terms, shorter sentences and a more concrete picture. "
                   "Do not jump to baby talk, and do not copy its sentences.\n" if down else
                   "Do NOT reuse its comparison, its example or its sentences: this one must be a genuinely different explanation, "
                   "not the same text with shorter or longer words.\n")
    reason, kept, spare = "", None, None
    started = mono()

    def spent():
        return mono() - started >= BUDGET_S
    out["refused"] = refused = []                 # the codes of the drafts the referee turned down (the eval reads them)
    for attempt in range(2):
        if attempt and spent():                       # the first call used the turn's time: no second wait, the fallbacks below
            break
        banned = (f"The student did NOT understand these comparisons, so do not use them or anything like them: "
                  f"{json.dumps(list(avoid))}. Choose a completely different comparison and a different angle.\n") if avoid else ""
        draft, failed = _try(model, (
            f"You are a patient, knowledgeable professor. Explain \"{topic}\" to your student "
            + ("from your own general knowledge, because the student has no course material on it. " if general
               else "using ONLY the SOURCE below. ") +
            "Everything you say must be true of the topic: a comparison is only good if it works the way the real thing works.\n"
            f"{study_ladder.example_block()}\n"
            f"Explain it at this level of the ladder. {study_ladder.RULES[rung]}\n{before}{banned}{facts}"
            + (f"Your last try was refused: {reason} Write it again and fix exactly that.\n" if reason else "")
            + f"\nSOURCE:\n\"\"\"\n{source}\n\"\"\"\n\n{study_board.prompt(rung, topic)}\n\nReply with JSON only, exactly this shape:\n"
            '{"explanation": "the explanation", "analogy": "the everyday thing you compared it to in 2-5 words, or empty if none", '
            '"limit": "one way the comparison breaks down, or empty", "check": "one question that shows whether they understood", '
            f'"board": {study_board.SHAPE}}}'))
        if not isinstance(draft, dict) or not _text(draft.get("explanation")):
            if attempt == 0 and model is not None and not failed:         # not readable: one more try, told so
                refused.append("unreadable")
                reason = "Your reply was not the JSON object asked for. Reply with ONLY that JSON object and nothing else."
                continue
            break
        analogy = _text(draft.get("analogy"), 80) if rung not in ("college", "high") else ""     # no comparison on the upper rungs
        if analogy and any(_same_analogy(analogy, old) for old in avoid):
            reason = "You repeated a banned comparison. Pick another."
            refused.append("banned")
            continue
        text = _text(draft["explanation"], 1500)
        verdict = study_check.judge(rung, text, topic, plain + " " + " ".join(ex["facts"] if ex else []), analogy, previous,
                                    ex["anchors"] if facts and ex else ())
        verdict = study_check.unnamed(rung, text, topic) or verdict      # every rung says what it is about
        if verdict:
            refused.append(verdict[0])
        if text and not (verdict and verdict[0] in ("empty", "repeat", "no_topic")):
            spare = (draft, text, analogy)                # the last draft that said something new (the general fallback below)
        close = bool(verdict and attempt == 1 and ((verdict[0] == "repeat" and study_check.echo(text, previous["text"]) < 0.9)
                                                   or (verdict[0] == "copy" and rung == "college")))     # a college rung that is the source's own text is the source
        # a second draft that still lacks the exact name but is plainly about the material (another word form, a synonym) is kept:
        # throwing a good answer away costs a template reply and another model call
        soft = bool(verdict and verdict[0] == "no_topic" and attempt >= 1 and study_check.related(text, topic, plain))
        if soft:
            verdict = None
        if verdict and verdict[0] in study_check.HARD and not close:
            reason = verdict[1]                           # off the topic, a copy or an echo: nothing here to keep
            continue                                      # (a near cousin of the last rung that is not a duplicate beats the source's own words)
        if verdict and attempt == 0:
            reason, kept = verdict[1], (draft, text, analogy)       # worth a second try; this one is the fallback
            continue
        return _model_explanation(out, topic, text, analogy, draft, rung, concept, ex, model, spent, previous)
    if kept:
        return _model_explanation(out, topic, kept[1], kept[2], kept[0], rung, concept, ex, model, spent, previous)
    if general:
        # No material to fall back on. The last draft that said something new is better than silence (the referee's
        # complaint was about its style, not about the idea); else the rung above in fewer words, which is what one step
        # simpler means; else nothing, and the caller says so.
        if spare:
            return _model_explanation(out, topic, spare[1], spare[2], spare[0], rung, concept, ex, model, spent, previous)
        was = (previous or {}).get("level")
        if previous and previous.get("text") and was in study_ladder.RUNGS and study_ladder.RUNGS.index(was) < study_ladder.RUNGS.index(rung):
            text = study_check.finalize(rung, " ".join(study_check.sentences(previous["text"])[:{"middle": 3}.get(rung, 2)]))
            if not study_check.names(text, topic):
                return out
            return dict(out, text=text, source="model", board=study_board.for_explanation(topic, text, rung))
        return out
    if seed:
        return built_in()
    near = [p for p in passages if p.get("chapter") == passages[0].get("chapter")]       # a passage of another chapter is another topic
    passage = near[turn % len(near)]                      # no model: the next passage about it each time he asks again
    if rung in ("prek", "one") and previous and previous.get("text") and previous.get("level") in study_ladder.RUNGS             and study_ladder.RUNGS.index(previous["level"]) < study_ladder.RUNGS.index(rung):
        # the model gave nothing usable for this step: say the rung above in fewer words, which is what one step simpler means
        text = study_check.finalize(rung, " ".join(study_check.sentences(previous["text"])[:2]))
        if study_check.names(text, topic):
            return dict(out, text=text, cite=study_store.cite(passage), source="model", templated=False,
                        board=study_board.for_explanation(topic, text, rung))
    if rung in ("prek", "one"):                           # his material's own sentence is no pre-K explanation: the frame is
        text = study_ladder.templated(topic, passage["text"])[rung]
        return dict(out, text=text, cite=study_store.cite(passage), source="passage", templated=True,
                    board=study_board.for_explanation(topic, text, rung))
    text = " ".join(study_store._SENTENCE.split(passage["text"])[:_OWN_WORDS[rung]])
    return dict(out, text=text, cite=study_store.cite(passage), source="passage",
                board=study_board.for_explanation(topic, text, rung))


def _model_explanation(out, topic, text, analogy, draft, rung, concept, ex, model, spent=lambda: False, previous=None):
    """The accepted draft with its board: the worked example's, else the model's own (read leniently, rebuilt when badly
    shaped), else, when that is unusable, ONE more call that asks for the board alone (not once the turn's time is spent)."""
    text = study_check.shorter(rung, study_check.lead_with_topic(rung, text, topic), previous)
    if ex and rung in ("college", "high") and not study_check.has_figures(text, ex["anchors"]):
        text = f"{text} Worked example: {ex['facts'][0]}"          # the model would not use the computed figures: the code's own sentence says them
    board_draft = draft.get("board")
    if isinstance(board_draft, str):
        board_draft = parse_json(board_draft)
    board = study_board.for_explanation(topic, text, rung, concept, board_draft, ex)
    if board.get("source") == "fallback" and model is not None and not spent():
        again = _ask(model, study_board.only_prompt(rung, text, topic))
        if isinstance(again, dict) and isinstance(again.get("board"), (dict, list)):
            again = again["board"]
        if again is not None:
            board = study_board.for_explanation(topic, text, rung, concept, again, ex)
    return dict(out, text=text, analogy=analogy, limit=_text(draft.get("limit"), 300), check=_text(draft.get("check"), 300),
                source="model", board=board)


# ---- flashcards -------------------------------------------------------------------------------------------------
MAX_CARD_CALLS = 4         # model calls for one "make flashcards" (each can give three cards); the rest come from definitions


def make_cards(passages, course, count=8, model=None) -> list[dict]:
    """Up to `count` cards (front, back) from these passages: the model's when grounded, else one per defined term.
    At most MAX_CARD_CALLS model calls, however many passages there are."""
    cards, seen, calls = [], set(), 0

    def add(passage, front, back, concept):
        front, back = _text(front, 300), _text(back, 600)
        if front and back and _norm(front) not in seen and len(cards) < count:
            seen.add(_norm(front))
            cid = hashlib.sha256(f"{course}|{_norm(front)}".encode()).hexdigest()[:12]
            cards.append({"id": cid, "course": course, "chapter": passage.get("chapter") or "", "front": front, "back": back,
                          "key": concept_key(course, passage.get("chapter") or "", concept or front),
                          "concept": _text(concept or front, 60), "cite": study_store.cite(passage)})

    for passage in passages:
        if len(cards) >= count:
            break
        if passage.get("concept"):
            add(passage, f"What does '{passage['concept']}' mean?", passage["text"], passage["concept"])
            continue
        calls += 1
        drafts = _ask(model if calls <= MAX_CARD_CALLS else None, (
            "Make flashcards for an IT student from the SOURCE only. One fact per card; the front is a question, the back "
            f"is the short answer.\n\nSOURCE:\n\"\"\"\n{passage['text']}\n\"\"\"\n\nReply with JSON only, exactly this shape:\n"
            '[{"front": "question", "back": "short answer", "concept": "2-4 word name", '
            '"quote": "a sentence copied word for word from SOURCE that proves the back"}]'))
        kept = 0
        for d in drafts if isinstance(drafts, list) else []:
            if isinstance(d, dict) and grounded(d.get("quote"), passage["text"]) and kept < 3:
                before = len(cards)
                add(passage, d.get("front"), d.get("back"), d.get("concept"))
                kept += len(cards) - before
        if not kept:
            for term, sentence in definitions(passage)[:2]:
                add(passage, f"What does '{term}' mean?", sentence, term)
    return cards


QUALITY = {"again": 1, "hard": 3, "good": 4, "easy": 5}


def sm2(card: dict, quality: int, today: date) -> dict:
    """One SM-2 review. card: {ease, interval, reps} (a new card: 2.5, 0, 0). -> the card with its new `due` date."""
    quality = max(0, min(5, int(quality)))
    ease, interval, reps = card.get("ease", 2.5), card.get("interval", 0), card.get("reps", 0)
    if quality < 3:
        reps, interval = 0, 1
    else:
        reps += 1
        interval = 1 if reps == 1 else 6 if reps == 2 else max(1, round(interval * ease))
    ease = max(1.3, round(ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02), 4))
    return dict(card, ease=ease, interval=interval, reps=reps, due=(today + timedelta(days=interval)).isoformat(),
                lapses=card.get("lapses", 0) + (quality < 3))


# ---- mastery ----------------------------------------------------------------------------------------------------
PRIOR, LEARN, SLIP = 0.2, 0.15, 0.1


def bkt(p: float, correct: bool, kind: str = "short") -> float:
    """Bayesian knowledge tracing: the chance he knows the concept after one more graded answer."""
    guess = 0.25 if kind == "mcq" else 0.1
    known = p * (1 - SLIP) / (p * (1 - SLIP) + (1 - p) * guess) if correct else p * SLIP / (p * SLIP + (1 - p) * (1 - guess))
    return round(known + (1 - known) * LEARN, 4)


def status(m: dict) -> str:
    if m["p"] >= 0.9 and m["right"] >= 2:
        return "mastered"
    return "weak" if (not m["last_correct"] or m["p"] < 0.45) else "learning"


# ---- the event log ----------------------------------------------------------------------------------------------
def _machine():
    from core import thread
    return thread.machine()


_record_lock = threading.Lock()


def record(event: dict) -> dict:
    """Append one event to THIS machine's file (the only file this machine ever writes for progress). One writer at
    a time in this process; a file that cannot be read whole is left alone (the event is not written over it)."""
    name = f"study/events-{_machine()}.json"
    event = dict(event, t=clock().isoformat(timespec="seconds"))
    with _record_lock:
        path = storage.path(name)
        rows = []
        if path.exists():
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return event
        rows = rows if isinstance(rows, list) else []
        rows.append(event)
        storage.save(name, rows)
    return event


def events() -> list[dict]:
    folder = storage.user_dir() / "study"
    rows = []
    for path in sorted(folder.glob("events-*.json")) if folder.is_dir() else []:
        if ".sync-conflict" in path.name or ".corrupt" in path.name:
            continue
        data = study_store.read_json(path)
        rows += [r for r in data if isinstance(r, dict) and r.get("t") and r.get("type")] if isinstance(data, list) else []
    return sorted(rows, key=lambda r: r["t"])


def fold(rows: list[dict]) -> dict:
    """Replay events -> {"mastery": {key: {...}}, "cards": {id: {...}}, "answers": n, "rungs": {concept: rung}}."""
    mastery, cards, answers, rungs = {}, {}, 0, {}

    def learn(key, row, correct, kind, at):
        m = mastery.setdefault(key, {"key": key, "course": row.get("course", ""), "chapter": row.get("chapter", ""),
                                     "concept": row.get("concept", ""), "p": PRIOR, "n": 0, "right": 0,
                                     "last": "", "last_correct": True})
        m["p"] = bkt(m["p"], correct, kind)
        m["n"] += 1
        m["right"] += bool(correct)
        m["last"], m["last_correct"] = at, bool(correct)

    for row in rows:
        kind = row["type"]
        if kind == "answer" and row.get("key"):
            answers += 1
            learn(row["key"], row, row.get("correct"), row.get("kind", "short"), row["t"])
        elif kind == "card" and row.get("id"):
            if row["id"] not in cards:
                cards[row["id"]] = {**{k: row.get(k, "") for k in ("id", "course", "chapter", "front", "back", "key", "concept", "cite")},
                                    "ease": 2.5, "interval": 0, "reps": 0, "lapses": 0, "due": row["t"][:10], "added": row["t"]}
        elif kind == "review" and row.get("id") in cards:
            card = cards[row["id"]]
            cards[row["id"]] = sm2(card, row.get("quality", 0), date.fromisoformat(row["t"][:10]))
            if card.get("key"):
                learn(card["key"], card, row.get("quality", 0) >= 3, "short", row["t"])
        elif kind == "card_removed":
            cards.pop(row.get("id"), None)
        elif kind == "rung" and row.get("key") and row.get("rung") in study_ladder.RUNGS:
            rungs[str(row["key"])] = row["rung"]          # where he stands on the ladder for this concept (latest wins)
    return {"mastery": mastery, "cards": cards, "answers": answers, "rungs": rungs}


def state() -> dict:
    return fold(events())


def due_cards(st: dict, course_id: str | None = None, today: date | None = None) -> list[dict]:
    today = (today or clock().date()).isoformat()
    return sorted((c for c in st["cards"].values() if c["due"] <= today and (not course_id or c["course"] == course_id)),
                  key=lambda c: (c["due"], c["added"]))


def progress(course_id: str, st: dict | None = None, today: date | None = None) -> dict:
    """What the progress view and "how am I doing" show: per chapter the concepts met, a mastery percentage and the
    weak ones; the weakest concepts overall; cards due. A chapter's percentage is the average knowledge of the concepts
    met, scaled by how much of the chapter has been met (all of the built-in outline's concepts, or 8 per chapter of
    his own material), so one right answer never reads as a mastered chapter."""
    st = st or state()
    rows = [m for m in st["mastery"].values() if m["course"] == course_id]
    chapters = []
    for ch in study_store.chapters(course_id):
        mine = [m for m in rows if study_store.slug(m["chapter"]) == study_store.slug(ch["chapter"])]
        target = ch["passages"] if course_id == study_store.study_seed.ID else min(ch["passages"], 8)
        cover = min(1.0, len(mine) / target) if target else 0
        pct = round(100 * cover * sum(m["p"] for m in mine) / len(mine)) if mine else 0
        chapters.append({"chapter": ch["chapter"], "seen": len(mine), "of": target, "mastery": pct,
                         "weak": [m["concept"] for m in sorted(mine, key=lambda m: m["p"]) if status(m) == "weak"],
                         "mastered": sum(status(m) == "mastered" for m in mine)})
    weak = [{"concept": m["concept"], "chapter": m["chapter"], "p": m["p"], "n": m["n"], "right": m["right"]}
            for m in sorted(rows, key=lambda m: (m["p"], m["last"])) if status(m) == "weak"]
    cards = [c for c in st["cards"].values() if c["course"] == course_id]
    return {"course": course_id, "chapters": chapters, "weak": weak[:12], "answers": sum(m["n"] for m in rows),
            "right": sum(m["right"] for m in rows), "cards": len(cards), "due": len(due_cards(st, course_id, today)),
            "mastery": round(sum(c["mastery"] for c in chapters) / len(chapters)) if chapters else 0,
            "last": max((m["last"] for m in rows), default="")}


def plan(course_id: str, exam_date: str | None, st: dict | None = None, today: date | None = None) -> dict:
    """A day-by-day plan up to the exam: weakest and unseen chapters first, each chapter met again at least two days
    later when there is room, the last day before the exam for weak concepts and a practice exam. Without a date: the
    order to study in."""
    today = today or clock().date()
    prog = progress(course_id, st, today)
    order = sorted(prog["chapters"], key=lambda c: c["mastery"])           # stable: unseen chapters stay in book order
    out = {"course": course_id, "exam_date": exam_date, "order": [c["chapter"] for c in order], "days": [], "days_left": None}
    if not exam_date or not order:
        return out
    try:
        left = (date.fromisoformat(exam_date) - today).days
    except ValueError:
        return dict(out, exam_date=None)
    out["days_left"] = left
    if left <= 0:
        return out
    study_days = max(1, left - 1) if left > 1 else 1
    days = [{"date": (today + timedelta(days=n)).isoformat(), "tasks": []} for n in range(study_days)]
    for n, ch in enumerate(order):
        first = min(study_days - 1, n * study_days // len(order))
        days[first]["tasks"].append(f"Learn {ch['chapter']}: have it explained, then a 5-question quiz, then make flashcards")
        again = first + max(2, study_days // 3)
        if again < study_days:
            days[again]["tasks"].append(f"Review {ch['chapter']}: quiz the weak concepts")
    for day in days:
        day["tasks"].append("Review the flashcards that are due")
    if left > 1:
        days.append({"date": (today + timedelta(days=left - 1)).isoformat(),
                     "tasks": ["Practice exam across every chapter", "Go over each miss and have it explained another way", "Stop early and sleep"]})
    out["days"] = days
    return out
