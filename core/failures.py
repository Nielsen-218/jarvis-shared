"""Why did a practice attempt fail? One of five learner-side classes (plus the lab's own faults), each with its own remedy.

  perception    he misread what a capability returned (wrong key, wrong shape, empty list assumed full)  -> teacher can help
  missing_tool  the plan needs a capability that does not exist                                         -> developer request
  planning      every call worked, but the decisions were wrong (judge: wrong end state)                -> teacher can help
  execution     a capability refused or failed, or the procedure ran out of time/calls                  -> local repair first
  verification  his own verify() disagreed with the independent judge                                  -> local repair
  lab           the isolated lab itself broke (no browser, bridge down at start)                         -> not his fault; retry

The remedy, not the class name, is what the learning loop acts on (core/compose). A teacher is asked only where guidance can
change the outcome: concepts (planning, perception). It cannot add a missing tool or fix a broken lab.
"""
from collections import Counter

CLASSES = ("perception", "missing_tool", "planning", "execution", "verification", "lab")
TEACHABLE = ("planning", "perception")
REMEDY = {
    "perception": "re-read the capability documentation and the observed data; teacher if it persists",
    "missing_tool": "record a tool request for a developer; do not work around it with clicks",
    "planning": "change the plan (which items to act on, in what order, what to keep); teacher if it persists",
    "execution": "repair locally with the exact error; make waits/calls fewer and bounded",
    "verification": "rewrite verify() so it checks the expected result through the capabilities",
    "lab": "infrastructure fault: retried, never counted against the learner",
}
_PERCEPTION = ("KeyError", "IndexError", "TypeError", "AttributeError", "ValueError", "ZeroDivisionError")
_EXECUTION = ("BrowserError", "BridgeDown", "Budget", "RuntimeError", "COMError", "ElementNotEnabled")


def classify(row: dict) -> dict:
    """row: one practice result {passed, diff, error, error_type, failed_in, self_check, lab_error}. -> {"class", "also", "why"}."""
    passed, et, err = bool(row.get("passed")), row.get("error_type") or "", row.get("error") or ""
    sc = row.get("self_check")
    if row.get("lab_error"):
        return {"class": "lab", "also": [], "why": row["lab_error"][-200:]}
    if et == "BadMethod" and any(k in err for k in ("is not available", "does not exist", "not in your dependencies")):
        return {"class": "missing_tool", "also": [], "why": err[:200]}
    if passed:
        if sc is False:
            return {"class": "verification", "also": [], "why": "the task was done but his verify() said it was not"}
        return {"class": "", "also": [], "why": ""}
    if et in _PERCEPTION:
        return {"class": "perception", "also": ["verification"] if sc else [], "why": err[:200]}
    if et in _EXECUTION or row.get("failed_in") == "recover":
        return {"class": "execution", "also": [], "why": err[:200]}
    also = ["verification"] if sc else []           # he believed it worked: his own check is blind to this failure
    if et == "MethodFailed":
        return {"class": "planning", "also": also, "why": "gave up although the task was possible: " + err[:150]}
    return {"class": "planning", "also": also, "why": "; ".join(row.get("diff") or [])[:200] or "wrong end state"}


def summary(rows) -> dict:
    """-> {"counts": {class: n}, "dominant": class or "", "teachable": bool, "remedy": text}"""
    counts = Counter(classify(r)["class"] for r in rows)
    counts.pop("", None)
    learner = {k: v for k, v in counts.items() if k != "lab"}
    dominant = max(learner, key=learner.get) if learner else ""
    return {"counts": dict(counts), "dominant": dominant, "teachable": dominant in TEACHABLE, "remedy": REMEDY.get(dominant, "")}
