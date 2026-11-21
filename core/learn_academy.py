"""The academy: Research -> Study -> Watch -> Practice -> Test -> Fix -> Remember -> Optimize, for ANY skill that supplies topics.

A topic (from `adapter.topics()`) names official sources, drill tasks and sealed exam tasks, and two hooks the skill implements:
  draft(passages, feedback)  -> a lesson dict from the quoted passages (the model writes it; sources are DATA, never instructions)
  verify(lesson)             -> {"ok", "detail", "teach_back": {...}}: the lesson's worked example is EXECUTED by the real judge, and the
                               prediction the student made BEFORE running it is compared with what happened (teach-back)
The academy owns the generic parts: remember-before-research (a verified lesson is reused offline), trusted-host fetching with version and
date provenance, prompt-injection filtering, the repair loop, placement, guided drills, the sealed A/B exam (without vs with the retained
lesson, same tasks, paired counts and an exact sign test) and remedial study from DRILL failures only (an exam is never read back).
Sources text is stored only as short passages with hashes; nothing from the web is ever executed."""
import hashlib
import math
import time
from urllib.parse import urlparse

from core import learn


def passages(text, keywords, width=700, limit=3600):
    """Windows of `text` around the keywords, merged, injection lines removed (the same filter as the anatomy research)."""
    from core.anatomy_knowledge import INJECTION
    spans = []
    low = text.lower()
    for kw in keywords:
        start = 0
        for _ in range(3):
            i = low.find(kw.lower(), start)
            if i < 0:
                break
            spans.append((max(0, i - width // 3), min(len(text), i + width)))
            start = i + len(kw)
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    out = " ... ".join(text[a:b] for a, b in merged)
    out = "\n".join(l for l in out.splitlines() if l.strip() and not INJECTION.search(l))
    return out[:limit]


def sign_test(helped, hurt):
    """Exact two-sided sign test on the discordant pairs (helped vs hurt)."""
    n = helped + hurt
    if n == 0:
        return 1.0
    k = min(helped, hurt)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def default_fetch(url):
    from core import teaching_import
    data, ctype = teaching_import.fetch(url)
    return teaching_import.extract("page.html", data, ctype)


class Academy:
    def __init__(self, engine, fetch=default_fetch, pass_threshold=0.7):
        self.engine, self.fetch, self.threshold = engine, fetch, pass_threshold
        self.store = engine.store
        engine.adapter._study_store = engine.store               # lets an adapter cache its study prompts next to the lessons

    def topic(self, topic_id):
        return next(t for t in self.engine.adapter.topics() if t["id"] == topic_id)

    # --- remember before research
    def recall(self, topic_id):
        rows = [l for l in self.store.lessons() if l.get("kind") == "academy" and l.get("topic") == topic_id and l.get("verified")]
        return rows[-1] if rows else None

    # --- research + study
    def sources(self, topic):
        from core.anatomy_knowledge import trusted
        got, notes = [], []
        found = []
        if topic.get("queries"):                                # research: look up how others already solved it (trusted hosts only)
            try:
                from core import websearch
                for q in topic["queries"]:
                    res = websearch.search(q, 8)
                    for r in ((res.get("results") if isinstance(res, dict) else None) or []):
                        if r.get("url") and trusted(r["url"]) and all(r["url"] != x["url"] for x in found + topic["sources"]):
                            found.append({"url": r["url"], "keywords": topic.get("keywords", []), "version_note": "community answer found by search (unverified)", "width": 1200, "limit": 2600})
            except Exception as e:
                notes.append({"search": f"{type(e).__name__}"})
        for s in topic["sources"] + found[: topic.get("max_found", 3)]:
            if not trusted(s["url"]):
                notes.append({"url": s["url"], "skipped": "host is not trusted"})
                continue
            try:
                text = self.fetch(s["url"])
            except Exception as e:
                notes.append({"url": s["url"], "skipped": f"{type(e).__name__}"})
                continue
            got.append({"url": s["url"], "host": urlparse(s["url"]).hostname, "fetched": time.strftime("%Y-%m-%d"), "modality": "text",
                        "version_note": s.get("version_note", ""), "sha": hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:12],
                        "passages": passages(text, s["keywords"], s.get("width", 700), s.get("limit", 3600))})
        return got, notes

    def study(self, topic_id, max_attempts=6, feedback=""):
        """Reuse a verified lesson if one is stored; otherwise research the official sources, draft a lesson, and keep it only if its
        worked example passes the real judge. Failed attempts are recorded, never hidden."""
        have = self.recall(topic_id)
        if have and not feedback:
            self.store.event("lesson_recalled", topic=topic_id, lesson=have["id"])
            return have
        topic = self.topic(topic_id)
        srcs, notes = self.sources(topic)
        if not srcs:
            self.store.event("lesson_failed", topic=topic_id, why="no readable trusted source", notes=notes)
            return None
        quoted = "\n\n".join(f"[{i + 1}] {s['url']} ({s['version_note']})\n{s['passages']}" for i, s in enumerate(srcs))
        attempts, fb = [], feedback
        for n in range(max_attempts):
            try:
                lesson = topic["draft"](quoted, fb, n)
            except RuntimeError as e:
                attempts.append({"n": n, "ok": False, "detail": f"model unavailable: {e}"})
                continue
            res = topic["verify"](lesson)
            attempts.append({"n": n, "ok": res["ok"], "detail": str(res.get("detail"))[:300], "teach_back": res.get("teach_back")})
            if res["ok"]:
                row = self.store.add_lesson(kind="academy", topic=topic_id, verified=True, signature=["academy", topic_id],
                                            sources=[{k: v for k, v in s.items() if k != "passages"} for s in srcs], attempts=attempts,
                                            teach_back=res.get("teach_back"), **lesson)
                self.store.event("lesson", topic=topic_id, lesson=row["id"], attempts=len(attempts))
                return row
            extra = self.diagnose(topic, res.get("error_hint"))
            if extra:                                   # investigate: search for the actual error, add what trusted pages say about it
                quoted += chr(10) * 2 + extra
            code = str(lesson.get("example_code", ""))[:1500]
            fb = "Your previous attempt failed the real judge: " + str(res.get("detail")) + ". Your previous code was:" + chr(10) + code + chr(10) + "Fix exactly what failed."
        self.store.event("lesson_failed", topic=topic_id, attempts=attempts)
        return None

    def diagnose(self, topic, error):
        """Failure-driven research: look the real error up on trusted hosts and quote the best passages (data, not instructions)."""
        if not error or not topic.get("error_query_prefix"):
            return ""
        from core.anatomy_knowledge import trusted
        try:
            from core import websearch
            res = websearch.search(topic["error_query_prefix"] + " " + str(error)[:140], 6)
        except Exception:
            return ""
        out = []
        for r in ((res.get("results") if isinstance(res, dict) else None) or []):
            if len(out) >= 2:
                break
            if r.get("url") and trusted(r["url"]):
                try:
                    out.append(f"[diagnostic] {r['url']} (found by searching the error)" + chr(10) + passages(self.fetch(r["url"]), topic.get("keywords", []), 1200, 2200))
                except Exception:
                    continue
        if out:
            self.store.event("diagnostic_research", topic=topic["id"], error=str(error)[:140], sources=len(out))
        return (chr(10) * 2).join(out)

    # --- practice and tests
    def _run(self, task_ids, action, purpose):
        out = []
        for tid in task_ids:
            r = self.engine.run(tid, action, purpose=purpose)
            out.append(r["outcome"]["full"])
        return out

    def placement(self, topic_id):
        """The baseline BEFORE any lesson: the untaught action on the drill tasks."""
        t = self.topic(topic_id)
        res = self._run(t["drill_tasks"], t["baseline_action"], "drill")
        self.store.event("placement", topic=topic_id, solved=sum(res), n=len(res))
        return {"solved": sum(res), "n": len(res)}

    def drills(self, topic_id):
        t = self.topic(topic_id)
        res = self._run(t["drill_tasks"], t["taught_action"], "drill")
        self.store.event("drills", topic=topic_id, solved=sum(res), n=len(res))
        return {"solved": sum(res), "n": len(res)}

    def exam(self, topic_id):
        """Sealed, unseen tasks, SAME tasks with and without the retained lesson. Passing needs the threshold on the taught side."""
        t = self.topic(topic_id)
        base = self._run(t["exam_tasks"], t["baseline_action"], "exam")
        taught = self._run(t["exam_tasks"], t["taught_action"], "exam")
        helped = sum(1 for b, a in zip(base, taught) if a and not b)
        hurt = sum(1 for b, a in zip(base, taught) if b and not a)
        n = len(base)
        rep = {"topic": topic_id, "n": n, "baseline_solved": sum(base), "taught_solved": sum(taught), "helped": helped, "hurt": hurt,
               "sign_test_p": round(sign_test(helped, hurt), 5), "threshold": self.threshold, "passed": sum(taught) / n >= self.threshold,
               "lesson_effect_shown": bool(helped > hurt and sign_test(helped, hurt) < 0.05), "judge": self.engine.judge_hash(), "session": learn.SESSION}
        self.store.event("exam_report", **rep)
        return rep

    def remediate(self, topic_id):
        """After a failed exam: restudy using ONLY what the drills got wrong (never the exam's tasks or failures)."""
        bad = [e for e in self.store.episodes(purpose="drill") if not e["outcome"]["full"] and e["action"] == self.topic(topic_id)["taught_action"]][-3:]
        fb = "Drills still failing: " + "; ".join(str(e["outcome"].get("detail") or e["outcome"].get("error") or e["task"]) for e in bad)
        return self.study(topic_id, feedback=fb or "the drills failed")

    def run_topic(self, topic_id):
        """The whole cycle, in order. Returns what happened at each step (nothing is skipped silently)."""
        report = {"topic": topic_id}
        have = self.recall(topic_id)
        report["recalled"] = bool(have)
        if not have:
            report["placement"] = self.placement(topic_id)
        lesson = self.study(topic_id)
        report["lesson"] = lesson["id"] if lesson else None
        if not lesson:
            report["state"] = "study_failed"
            return report
        report["drills"] = self.drills(topic_id)
        report["exam"] = self.exam(topic_id)
        if not report["exam"]["passed"]:
            self.remediate(topic_id)
            report["remedial"] = "restudied from drill failures; the exam is not re-run on the same tasks"
        report["state"] = "exam_passed" if report["exam"]["passed"] else "exam_failed"
        return report
