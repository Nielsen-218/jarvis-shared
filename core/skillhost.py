"""Chat side of installed skills: owner's words -> a skill whose verified scope matches -> parameters (ask when essential ones are missing,
never invent) -> OS-sandboxed execution -> the host's invariants on the REAL result -> bounded recovery -> a plain-language answer.

Reuses core/procedures.Library for matching, extraction and recovery lookup. Installing a skill adds no operating-system capability: package code runs
only in the AppContainer sandbox (core/skillsandbox.py) with no files, network, processes or secrets. Before a skill is used, the stored copy is
re-checked against the package the owner approved. Everything a skill returns is UNTRUSTED text: it is clipped and stripped of control characters
before it reaches the owner or the conversation, and success is spoken only when the invariants hold on the actual output."""
import re

from core import skilljudge, skillsandbox
from core.procedures import BadParams, Library, NotVerified

_installers = {}
MAX_TEXT = 400
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def _installer():
    from core import skillinstall
    base = skillinstall.root()
    if base not in _installers:
        _installers[base] = skillinstall.Installer(base)
    return _installers[base]


def _lib():
    return _installer().lib


def usable():
    """Installed skills run only where the OS sandbox exists (Windows desk machine). Elsewhere nothing is matched, so ordinary chat is untouched."""
    return skillsandbox.available()


def _candidates(text):
    ins, lib = _installer(), _lib()
    out = []
    for pid in lib.ids():
        try:
            got = lib.get(pid)                                   # None when disabled; raises when the stored source was altered
        except NotVerified:
            ins.event("failure", id=pid, stage="load", error="stored source changed since verification")
            continue
        if got and "skill" in got[0] and lib.triggered(got[0], text) and ins.verify_installed(pid):
            out.append(got)
    return out


def plan(text):
    """Pure routing, no execution. -> {"kind": "none"} | {"kind": "ask", id, missing, question} | {"kind": "reject", id, reason} | {"kind": "run", id, params}."""
    if not usable():
        return {"kind": "none"}
    text = (text or "")[:MAX_TEXT]
    try:
        cands = _candidates(text)
    except Exception:                                            # a broken store must never swallow the turn
        return {"kind": "none"}
    if not cands:
        return {"kind": "none"}
    lib = _lib()
    man = max(cands, key=lambda g: sum(text.lower().count(w) for grp in g[0]["triggers"]["all"] for w in grp))[0]
    params, why = lib.diagnose(man, text, {})
    if params is not None:
        return {"kind": "run", "id": man["id"], "params": params}
    if why[0] == "missing":
        return {"kind": "ask", "id": man["id"], "missing": why[1], "question": man["params"][why[1][0]].get("ask") or f"What is {why[1][0]}?"}
    return {"kind": "reject", "id": man["id"], "reason": why[1]}


def wants(text):
    return plan(text)["kind"] != "none"


def clean(value):
    """Skill output is untrusted: strings are stripped of control/bidi characters and clipped; lists are joined."""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(x) for x in value[:20])
    return _CTRL.sub("", str(value))[:MAX_TEXT]


def _fill(template, out):
    return re.sub(r"\{(\w+)\}", lambda m: clean(out.get(m.group(1), "")), template)


def run(text):
    """The spoken answer for an installed-skill request."""
    p = plan(text)
    if p["kind"] == "none":
        return "None of my installed skills covers that, so I won't improvise."
    if p["kind"] == "ask":
        return p["question"]
    if p["kind"] == "reject":
        return f"I can't use that as given: {p['reason']}."
    ins, lib = _installer(), _lib()
    man, src = lib.get(p["id"])
    sk, params = man["skill"], dict(p["params"])
    title = f"{man['title']} (version {man['pkg_version']})"
    for attempt in (0, 1):                                       # one bounded recovery
        try:
            res = skillsandbox.run(src, man["entry"], params, sk["dependencies"].get("modules", []))
        except skillsandbox.Unavailable as e:
            ins.event("run", id=p["id"], version=man["pkg_version"], ok=False, error="sandbox_unavailable")
            return f"I can't run {title} right now because my OS safety sandbox is unavailable ({clean(e)}). I won't run it unprotected."
        lib.log(procedure=p["id"], version=man["version"], params=params, attempt=attempt, ok=res.get("ok"), error=res.get("error"))
        if res.get("ok") and isinstance(res.get("output"), dict):
            bad = skilljudge.check_invariants(sk["verify"]["invariants"], params, res["output"])
            ins.event("run", id=p["id"], version=man["pkg_version"], ok=not bad, params=params, problems=bad)
            if bad:
                return f"I ran {title} but its result failed my own verification ({'; '.join(bad)}), so I'm not giving you that answer."
            return f"{_fill(sk['reply'], res['output'])} (Checked {len(sk['verify']['invariants'])} independent rules on the result; skill: {title}.)"
        rec = lib.recovery_for(man, res.get("error"))
        if rec and rec.get("set") and attempt == 0:
            trial = {**params, **rec["set"]}
            try:
                Library.check_params(man, trial)                 # a recovery may not push a value outside the declared range
            except BadParams:
                rec = None
            else:
                params = trial
                continue
        ins.event("run", id=p["id"], version=man["pkg_version"], ok=False, params=params, error=res.get("error"))
        if rec and rec.get("ask_user"):
            return f"I can't do that one as asked: {clean(rec.get('why') or sk['errors'].get(res['error'], res['error']))}."
        return f"{title} failed ({clean(res.get('error'))}). I'm not guessing an answer."
    return "That did not work."
