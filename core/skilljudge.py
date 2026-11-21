"""The HOST's judge for installed skills: acceptance and runtime verification that do not trust the package's own claims.

Authority rules:
  * Pass/fail is decided here, from DECLARATIVE data in the package (case expectations, invariant expressions), evaluated by this module's
    restricted evaluator. A package's judge.py (code) is untrusted: it runs in the OS sandbox and may only VETO, never approve.
  * Besides the author's cases, the host fuzzes the skill from its declared parameter ranges (seeded by the fingerprint) and, if the owner
    supplies one, runs the owner's own trusted cases. Passing the author's examples alone is not enough: every run must also satisfy the
    invariants, and a crash that is not a declared, named error fails the skill.
  * Package code only ever runs through core/skillsandbox.py (Windows AppContainer). All executions of one acceptance run share ONE sandbox process.
  * The same invariants are re-checked on every real use, so a skill that starts misbehaving is not announced as verified."""
import ast
import hashlib
import math
import re
import random
from decimal import Decimal
from pathlib import Path

from core import skillpkg, skillsandbox
from core.procedures import Library

JUDGE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
MIN_EXEC, MIN_HOLDOUT, MIN_ERRORS, MIN_ROUTE_POS, MIN_ROUTE_NEG, FUZZ_N = 12, 3, 2, 3, 3, 24

_FUNCS = {"len": len, "sum": sum, "min": min, "max": max, "abs": abs, "round": round, "sorted": sorted, "all": all, "any": any, "int": int,
          "float": float, "str": str, "range": range, "isinstance": isinstance, "list": list, "set": set, "tuple": tuple, "zip": zip,
          "enumerate": enumerate, "bool": bool, "dict": dict, "math": math, "Decimal": Decimal}
_OK_NODES = (ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.IfExp, ast.Name, ast.Constant, ast.Subscript, ast.Slice,
             ast.List, ast.Tuple, ast.Dict, ast.Set, ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.comprehension, ast.Call, ast.Load, ast.Store,
             ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.Eq, ast.NotEq,
             ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.keyword, ast.Attribute)


def _compile(expr):
    tree = ast.parse(expr, mode="eval")
    for n in ast.walk(tree):
        if not isinstance(n, _OK_NODES):
            raise ValueError(f"'{type(n).__name__}' is not allowed in an invariant")
        if isinstance(n, ast.Attribute) and not (isinstance(n.value, ast.Name) and n.value.id == "math"):
            raise ValueError("attribute access is only allowed on math")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id not in _FUNCS:
            raise ValueError(f"function {n.func.id} is not allowed in an invariant")
        if isinstance(n, ast.Call) and not isinstance(n.func, (ast.Name, ast.Attribute)):
            raise ValueError("only named functions can be called")
        if isinstance(n, ast.Pow):
            raise ValueError("exponentiation is not allowed (it can exhaust memory)")
    return compile(tree, "<invariant>", "eval")


def check_invariants(invariants, params, output):
    """-> list of failure messages (empty = every invariant holds). A malformed or erroring invariant counts as a failure, never a pass."""
    bad = []
    for inv in invariants:
        try:
            if not eval(_compile(inv["expr"]), {"__builtins__": {}, **_FUNCS}, {"input": params, "output": output}):
                bad.append(f"invariant '{inv['name']}' failed")
        except Exception as e:                               # noqa: BLE001
            bad.append(f"invariant '{inv.get('name')}' could not be evaluated ({type(e).__name__})")
    return bad


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def evaluate(man, case, res, veto=None):
    """-> (passed, why) for one execution case given the sandbox result `res` (already produced) and the package judge's veto for it."""
    exp = case.get("expect", {})
    if res.get("error") == "bad_params":
        return exp.get("error") == "bad_params", res["message"]
    if "error" in exp:
        if res.get("ok"):
            return False, f"expected error {exp['error']} but it returned {res['output']!r}"
        if res["error"] != exp["error"]:
            return False, f"expected error {exp['error']} but got {res['error']} ({res.get('message', '')})"
        return True, "named error as expected"
    if not res.get("ok"):
        return False, f"failed with {res['error']}: {res.get('message', '')}"
    out = res["output"]
    for k, v in (exp.get("output") or {}).items():
        if not isinstance(out, dict) or k not in out or not _same(out[k], v):
            return False, f"output[{k!r}] was {out.get(k) if isinstance(out, dict) else out!r}, expected {v!r}"
    bad = check_invariants(man["verify"]["invariants"], case["params"], out)
    if bad:
        return False, "; ".join(bad)
    if veto:
        return False, f"package judge vetoed: {veto}"
    return True, "ok"


def route_case(man, case):
    """-> (passed, why). Does the owner's wording reach this skill (or correctly NOT), and are the right parameters taken from it?"""
    lib = Library.__new__(Library)
    hit = lib.triggered(man, case["text"])
    if not case["route"]:
        return (not hit), ("correctly not matched" if not hit else "matched a request it must NOT take")
    if not hit:
        return False, "did not match a request it must take"
    if case.get("asks") is not None:                      # a request that is ours but lacks essentials: it must ASK for exactly those, never invent them
        miss = lib.missing(man, case["text"], {})
        if lib.extract(man, case["text"], {}) is not None or set(miss) != set(case["asks"]):
            return False, f"expected to ask for {case['asks']} but would ask for {miss}"
        text = lib.prep(man, case["text"])
        for k, v in (case.get("params") or {}).items():
            spec = man["params"][k]
            m = re.search(spec["from_text"], text, re.I)
            if not m or not _same(lib._coerce(spec, m.group(1)), v):
                return False, f"read {k} as {m.group(1) if m else None!r}, expected {v!r}"
        return True, "asks for the missing essentials"
    got = lib.extract(man, case["text"], {})
    if got is None:
        return False, "matched but could not extract the parameters"
    for k, v in (case.get("params") or {}).items():
        if k not in got or not _same(got[k], v):
            return False, f"extracted {k}={got.get(k)!r}, expected {v!r}"
    return True, "ok"


def _fuzz_params(man, rng):
    out = {}
    for name, spec in man["params"].items():
        t = spec["type"]
        if t in ("number", "integer", "decimal"):
            lo, hi = spec["min"], spec["max"]
            v = rng.choice([lo, hi, (lo + hi) / 2, rng.uniform(lo, hi), rng.uniform(lo, hi)])
            if t == "integer":
                out[name] = int(round(v))
            elif t == "decimal":
                out[name] = f"{Decimal(str(v)).quantize(Decimal(1).scaleb(-int(spec.get('places', 2)))):f}"
            else:
                out[name] = round(float(v), 4)
        elif t == "string":
            out[name] = rng.choice(spec["enum"]) if "enum" in spec else rng.choice(["", "a", "plain words", "ÄÖü 123", "x" * 40])
        elif t == "numbers":
            out[name] = [round(rng.uniform(-10, 10), 3) for _ in range(rng.randint(1, 6))]
        else:
            out[name] = ["a", "b"]
    return out


def accept(pkg, extra_cases=None):
    """Full acceptance of an inspected, owner-approved package. -> {"passed", "total", "failures"[], "tasks"[], "judge", "requirements"[]}.
    `requirements` lists unmet structural minimums (a package that merely passes a few easy cases is refused). Raises skillsandbox.Unavailable when
    the OS sandbox cannot be used: an unverifiable package is never accepted."""
    man, files = pkg["manifest"], pkg["files"]
    source = files["procedure.py"].decode("utf-8").replace("\r\n", "\n")      # the form that is stored and run
    judge_src = files["judge.py"].decode("utf-8").replace("\r\n", "\n") if "judge.py" in files else None
    mods = man["dependencies"].get("modules", [])
    cases, problems = skillpkg.parse_cases(files)
    rows = [(s, r) for s in ("train", "dev", "holdout") for r in cases[s]] + [("owner", r) for r in (extra_cases or [])]
    ex = [(s, r) for s, r in rows if "text" not in r]
    ro = [(s, r) for s, r in rows if "text" in r]
    req = list(problems)
    if len(ex) < MIN_EXEC:
        req.append(f"needs at least {MIN_EXEC} execution cases, has {len(ex)}")
    if sum(1 for s, _ in ex if s == "holdout") < MIN_HOLDOUT:
        req.append(f"needs at least {MIN_HOLDOUT} holdout execution cases")
    if sum(1 for _, r in ex if "error" in r.get("expect", {})) < MIN_ERRORS:
        req.append(f"needs at least {MIN_ERRORS} cases that expect a named error")
    declared = set(man["errors"])
    for _, r in ex:
        if not isinstance(r.get("params"), dict) or r.get("expect", {}).get("error") not in declared | {None, "bad_params"}:
            req.append(f"case {r.get('id')} is malformed or expects an undeclared error")
    if sum(1 for _, r in ro if r["route"]) < MIN_ROUTE_POS or sum(1 for _, r in ro if not r["route"]) < MIN_ROUTE_NEG:
        req.append(f"needs at least {MIN_ROUTE_POS} routing cases that must match and {MIN_ROUTE_NEG} that must NOT match")
    if req:
        return {"passed": 0, "total": 0, "failures": [], "tasks": [], "judge": JUDGE_HASH, "requirements": req}
    # ---- one sandbox process for every execution (author cases first, then seeded fuzz)
    rng = random.Random(pkg["fingerprint"])
    fuzz = [_fuzz_params(man, rng) for _ in range(FUZZ_N)]
    calls, results = [], {}
    for i, (_, r) in enumerate(ex):
        try:
            Library.check_params({"params": man["params"]}, r["params"])
            calls.append((("case", i), r["params"]))
        except Exception as e:                                # noqa: BLE001 - rejected by the host before any package code sees it
            results[("case", i)] = {"ok": False, "error": "bad_params", "message": f"parameters rejected by the host: {e}"}
    calls += [(("fuzz", i), p) for i, p in enumerate(fuzz)]
    ran = skillsandbox.run_many(source, man["entry"], [p for _, p in calls], mods, per_call=5, total=120)
    results.update({k: res for (k, _), res in zip(calls, ran)})
    vetoes = {}
    if judge_src:
        judged = [(k, {"params": dict(p), "output": results[k]["output"]}) for k, p in calls if results[k].get("ok")]
        if judged:
            jr = skillsandbox.run_many(judge_src, "judge", [j for _, j in judged], mods, per_call=5, total=120)
            vetoes = {k: (None if (j.get("ok") and not j["output"]) else (j.get("output") or j.get("message") or "no verdict")) for (k, _), j in zip(judged, jr)}
    failures, tasks = [], []
    for i, (split, r) in enumerate(ex):
        ok, why = evaluate(man, r, results[("case", i)], vetoes.get(("case", i)))
        tasks.append(f"{split}:{r.get('id')}")
        if not ok:
            failures.append(f"{split}:{r.get('id')}: {why}")
    for split, r in ro:
        ok, why = route_case(man, r)
        tasks.append(f"{split}:route:{r.get('id')}")
        if not ok:
            failures.append(f"{split}:route:{r.get('id')}: {why}")
    for i, p in enumerate(fuzz):
        res = results[("fuzz", i)]
        tasks.append(f"fuzz:{i}")
        if res.get("ok"):
            bad = check_invariants(man["verify"]["invariants"], p, res["output"])
            if bad:
                failures.append(f"fuzz:{i} {p}: {'; '.join(bad)}")
            elif vetoes.get(("fuzz", i)):
                failures.append(f"fuzz:{i}: package judge vetoed: {vetoes[('fuzz', i)]}")
        elif res["error"] not in declared:
            failures.append(f"fuzz:{i} {p}: unnamed failure {res['error']} ({res.get('message', '')})")
    return {"passed": len(tasks) - len(failures), "total": len(tasks), "failures": failures, "tasks": tasks, "judge": JUDGE_HASH, "requirements": []}
