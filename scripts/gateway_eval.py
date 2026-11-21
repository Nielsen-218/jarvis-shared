"""Evaluate the gateway's understanding against independently defined expected intents (scripts/gateway_cases.py), on the
real PC context and the real local models, WITHOUT executing anything. The legacy routers are replayed on the same sentences
(non-executing replicas) so every disagreement is documented with who is right.

Reports: tasks understood correctly, whole-request coverage, safety (would an irreversible step run unasked?), latency per
model, and the legacy comparison. Writes runs/gateway-eval-<stamp>.json and .md. Usage:

    python scripts/gateway_eval.py [--shadow]      (--shadow also summarises what shadow mode recorded on real traffic)
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import core.config  # noqa: E402,F401  (.env: local-first, model names)
from core import authz, gateway, gateway_context, intelligence, pcledger, users  # noqa: E402
from scripts.gateway_cases import CASES  # noqa: E402

users.set_current("owner")
pcledger.usage = lambda *a, **k: None          # this is an evaluation: no ledger rows, no latency rows
pcledger.latency = lambda *a, **k: 0


def resolve(app):
    try:
        from tools import apps
        return apps.resolve_app(app) or app
    except Exception:  # noqa: BLE001
        return app


def tool_match(expected: str, got: str) -> bool:
    return got in expected.rstrip("?").split("|")


def tools_ok(expected: list, got: list) -> bool:
    """Ordered match; a tool written "name?" is optional."""
    i = 0
    for e in expected:
        optional = e.endswith("?")
        if i < len(got) and tool_match(e, got[i]):
            i += 1
        elif not optional:
            return False
    return i == len(got)


def args_ok(expected: dict, steps: list) -> bool:
    for key, want in expected.items():
        found = False
        for s in steps:
            v = s.get("arguments", {}).get(key)
            if v is None:
                continue
            if key == "app":
                found = resolve(str(v)) == resolve(str(want))
            elif isinstance(want, str):
                found = str(want).casefold() in str(v).casefold()
            else:
                found = v == want
            if found:
                break
        if not found:
            return False
    return True


def judge(expect: dict, kind: str, steps: list, skills: list, intent_kinds: list = ()) -> tuple[bool, str]:
    """Correct when the decision is of an expected kind (a mix passes when every working intent is of an expected kind, a
    clarifying question beside the work included), the ordered tools match, the named arguments are there, the skill is right."""
    kinds = expect["kind"].split("|")
    present = [k for k in (intent_kinds or [kind]) if k != "chat"] or list(intent_kinds or [kind])
    if kind == "multi" and "multi" not in kinds:
        if not all(k in kinds or k == "clarify" for k in present):
            return False, f"kinds {present} not all in {expect['kind']}"
    elif kind not in kinds:
        return False, f"kind {kind} not in {expect['kind']}"
    if "tools" in expect and steps:
        got = [s["tool"] for s in steps]
        if not tools_ok(expect["tools"], got):
            return False, f"tools {got} != {expect['tools']}"
        if not args_ok(expect.get("args", {}), steps):
            return False, "arguments differ"
    elif "tools" in expect and not steps and not set(kinds) & {"skill", "clarify", "chat"}:
        return False, "no steps"
    if "skill" in expect and skills:
        if not set(expect["skill"].split("|")) & set(skills):
            return False, f"skill {skills} != {expect['skill']}"
    elif "skill" in expect and not skills and not steps and not set(kinds) & {"clarify", "chat"}:
        return False, "no skill"
    return True, ""


def gateway_decision(text: str):
    d = gateway.decide(text)
    if d is None:
        return "none", [], [], {"source": "none", "model": "", "ms": 0, "attempts": ["local intelligence off"], "covers": False}
    kind = d.kind if d.intents else "none"
    skills = [i.skill for i in d.intents if i.kind == "skill"]
    return kind, d.steps(), skills, {"source": d.source, "model": d.model, "ms": d.latency_ms, "context_ms": d.context_ms,
                                     "attempts": d.attempts, "covers": d.covers, "intents": [i.kind for i in d.intents],
                                     "questions": [i.question for i in d.intents if i.question]}


def legacy_decision(text: str):
    """What today's routers would decide for this sentence (no execution): regex plan, pcsemantic, learned-skill router, planner."""
    from core import pchybrid, pcsemantic, pcstate, pcplanner, skillrouter
    t0 = time.monotonic()
    plan = pchybrid.simple_plan(text)
    if plan:
        return "actions", plan["steps"], [], "regex", (time.monotonic() - t0) * 1000
    plan = pcsemantic.interpret(text)
    if plan and not pcsemantic.mentioned_apps(text) and skillrouter.route(text) is not None:
        plan = None
    if plan:
        return "actions", plan["steps"], [], "pcsemantic", (time.monotonic() - t0) * 1000
    if pcsemantic.compound(text):
        p = pcplanner.local_plan(text, context=pcstate.describe())
        return ("actions", p["steps"], [], "planner(compound)", (time.monotonic() - t0) * 1000) if p else ("none", [], [], "planner failed", (time.monotonic() - t0) * 1000)
    book = intelligence.pc_fact_playbook(text)
    if book is not None and not skillrouter.vet(text, book):
        book = None
    if book is None:
        book = skillrouter.route(text)
    if book is not None:
        return "skill", [], [book.subject or book.title], "skillrouter", (time.monotonic() - t0) * 1000
    if pchybrid.is_request(text):
        p = pcplanner.local_plan(text, context=pcstate.describe())
        return ("actions", p["steps"], [], "planner", (time.monotonic() - t0) * 1000) if p else ("none", [], [], "planner failed", (time.monotonic() - t0) * 1000)
    return "chat", [], [], "chat model", (time.monotonic() - t0) * 1000


def main():
    stamp = time.strftime("%Y%m%d-%H%M")
    print(f"gateway eval {stamp}: fast={intelligence.model('fast')} advanced={intelligence.model('advanced')} cases={len(CASES)}", flush=True)
    rows = []
    only = os.getenv("GW_EVAL_ONLY", "")
    for case in [c for c in CASES if not only or re.search(only, c["text"], re.I)]:
        text, expect = case["text"], case["expect"]
        t0 = time.monotonic()
        g_kind, g_steps, g_skills, meta = gateway_decision(text)
        g_total = round((time.monotonic() - t0) * 1000)
        g_ok, g_why = judge(expect, g_kind, g_steps, g_skills, meta.get("intents", []))
        verdict = authz.decide(g_steps, text) if g_steps else None
        unsafe = bool(verdict and not verdict.ask and any(e in ("irreversible", "external") for e in verdict.effects.values()))
        l_kind, l_steps, l_skills, l_route, l_ms = legacy_decision(text)
        l_ok, l_why = judge(expect, l_kind, l_steps, l_skills)
        agree = (g_kind, [s["tool"] for s in g_steps], g_skills) == (l_kind, [s["tool"] for s in l_steps], l_skills)
        row = {"text": text, "expect": expect, "why": case.get("why", ""),
               "gateway": {"kind": g_kind, "tools": [s["tool"] for s in g_steps], "args": [s.get("arguments") for s in g_steps], "skills": g_skills,
                           "ok": g_ok, "why": g_why, "total_ms": g_total, "unsafe": unsafe, "ask": bool(verdict and verdict.ask), **meta},
               "legacy": {"kind": l_kind, "tools": [s["tool"] for s in l_steps], "skills": l_skills, "route": l_route, "ok": l_ok, "why": l_why, "ms": round(l_ms)},
               "agree": agree}
        rows.append(row)
        print(f"{'PASS' if g_ok else 'FAIL'} gw[{meta['source']:8s} {g_total:5d}ms] {'PASS' if l_ok else 'FAIL'} legacy[{l_route:17s}] "
              f"{'=' if agree else '!'} | {text[:58]:58s} | {g_kind} {[s['tool'] for s in g_steps] or g_skills}"
              + (f"  <- {g_why}" if not g_ok else ""), flush=True)
    n = len(rows)
    g_pass = sum(r["gateway"]["ok"] for r in rows)
    l_pass = sum(r["legacy"]["ok"] for r in rows)
    covered = sum(1 for r in rows if r["gateway"]["covers"])
    unsafe = [r["text"] for r in rows if r["gateway"]["unsafe"]]
    by_model = {}
    for r in rows:
        by_model.setdefault(r["gateway"]["source"], []).append(r["gateway"]["total_ms"])
    lat = {k: {"n": len(v), "p50": statistics.median(v), "max": max(v)} for k, v in by_model.items()}
    disagreements = [r for r in rows if not r["agree"]]
    better = sum(1 for r in disagreements if r["gateway"]["ok"] and not r["legacy"]["ok"])
    worse = sum(1 for r in disagreements if r["legacy"]["ok"] and not r["gateway"]["ok"])
    summary = {"stamp": stamp, "cases": n, "gateway_correct": g_pass, "legacy_correct": l_pass, "whole_request_covered": covered,
               "unsafe_unasked": unsafe, "latency_ms_by_source": lat, "disagreements": len(disagreements),
               "gateway_better": better, "gateway_worse": worse, "both_wrong": sum(1 for r in rows if not r["gateway"]["ok"] and not r["legacy"]["ok"]),
               "models": {"fast": intelligence.model("fast"), "advanced": intelligence.model("advanced")}}
    out = ROOT / "runs"
    out.mkdir(exist_ok=True)
    (out / f"gateway-eval-{stamp}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    md = [f"# Gateway evaluation {stamp}", "",
          f"Cases {n}. Gateway correct {g_pass}/{n}. Legacy correct {l_pass}/{n}. Whole request covered {covered}/{n}. "
          f"Unsafe unasked steps: {len(unsafe)}. Disagreements {len(disagreements)} (gateway better {better}, worse {worse}, both wrong {summary['both_wrong']}).",
          "", f"Latency by source: {json.dumps(lat)}", "", "## Failures (gateway)", ""]
    md += [f"- {r['text']!r}: got {r['gateway']['kind']} {r['gateway']['tools'] or r['gateway']['skills']} ({r['gateway']['why']}; attempts {r['gateway']['attempts']})"
           for r in rows if not r["gateway"]["ok"]] or ["- none"]
    md += ["", "## Disagreements with the legacy routers", ""]
    md += [f"- {r['text']!r}: gateway {r['gateway']['kind']} {r['gateway']['tools'] or r['gateway']['skills']} ({'right' if r['gateway']['ok'] else 'wrong'}) "
           f"vs legacy {r['legacy']['route']} {r['legacy']['kind']} {r['legacy']['tools'] or r['legacy']['skills']} ({'right' if r['legacy']['ok'] else 'wrong'})"
           for r in disagreements] or ["- none"]
    if "--shadow" in sys.argv:
        shadow = gateway.shadow_rows()
        md += ["", f"## Shadow mode on real traffic ({len(shadow)} rows)", ""]
        for s in shadow[:60]:
            d = s.get("decision")
            md.append(f"- {time.strftime('%m-%d %H:%M', time.localtime(s['at']))} {s['said']!r}: legacy {s['legacy']} | gateway "
                      + (f"{d['kind']} {[i.get('steps') or i.get('skill') for i in d['intents']]} {d['source']} {d['ms']} ms" if isinstance(d, dict) else str(d)))
    (out / f"gateway-eval-{stamp}.md").write_text("\n".join(md), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
