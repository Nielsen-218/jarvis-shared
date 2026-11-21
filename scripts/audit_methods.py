"""Audit every method the learner wrote for one weakness: which low-level UI routines it re-derives again and again
(candidates for developer-built primitives), and how near-identical its candidates are.

    python scripts/audit_methods.py [weakness_id]  -> prints a report, writes data/learning/audit-<id>.json
"""
import ast
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Routines the learner had to rebuild from raw UI calls. Each regex marks a method that re-derives it.
ROUTINES = {
    "find the browser windows": r'process"\]\.lower\(\)|msedge',
    "list tabs (TabItem search)": r'role\s*=\s*"TabItem"',
    "find the address bar": r'Address and search bar|role\s*=\s*"Edit"',
    "read the selected tab's URL": r'ui\.value\(',
    "parse a host out of a URL": r'split\("/"\)|"://"|\.split\(":"\)|startswith\("www\."\)|removeprefix',
    "switch to a tab": r'ui\.select\(',
    "wait for the UI to settle": r'ui\.wait\(',
    "find a tab's close button": r'Close|close',
    "open a blank tab to keep a window": r'New tab|new tab|ctrl\+t',
    "verify a tab is gone": r'ui\.info\(',
}


def _shape(src: str) -> str:
    """The method with names, strings and numbers erased: two methods of the same shape differ only in constants."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            n.id = "_"
        elif isinstance(n, ast.Constant):
            n.value = 0
        elif isinstance(n, ast.arg):
            n.arg = "_"
    return ast.dump(tree, annotate_fields=False)


def audit(wid: str) -> dict:
    base = ROOT / "data" / "learning" / "acquire" / wid
    cycles = sorted(p for p in base.iterdir() if p.is_dir())
    methods = []
    for c in cycles:
        for f in sorted(c.glob("method-*.py")):
            methods.append((c.name, f.name, f.read_text(encoding="utf-8", errors="replace")))
    routine_counts = Counter()
    for _, _, src in methods:
        for name, rx in ROUTINES.items():
            if re.search(rx, src):
                routine_counts[name] += 1
    # Near-duplicates: within each cycle, a candidate whose text is >= 85% like an EARLIER candidate of the same cycle.
    near, same_shape, total_pairs = 0, 0, 0
    for c in cycles:
        srcs = [m[2] for m in methods if m[0] == c.name]
        shapes = set()
        for i, s in enumerate(srcs):
            sh = _shape(s)
            if sh in shapes:
                same_shape += 1
            shapes.add(sh)
            if any(difflib.SequenceMatcher(None, s, p).quick_ratio() >= 0.85 and difflib.SequenceMatcher(None, s, p).ratio() >= 0.85
                   for p in srcs[max(0, i - 6):i]):
                near += 1
            total_pairs += 1
    lines = [len(m[2].splitlines()) for m in methods]
    out = {"weakness": wid, "cycles": len(cycles), "methods": len(methods),
           "median_lines": sorted(lines)[len(lines) // 2] if lines else 0,
           "near_identical_to_a_recent_candidate": near, "same_shape_as_an_earlier_candidate": same_shape,
           "routines_rederived": dict(routine_counts.most_common())}
    (ROOT / "data" / "learning" / f"audit-{wid}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(audit(sys.argv[1] if len(sys.argv) > 1 else "21bfdeaa4e946c7a"), indent=1))
