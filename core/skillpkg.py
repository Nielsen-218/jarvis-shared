"""The JARVIS skill package format (jarvis-skill/1): a model-independent, self-describing bundle that any developer or AI can produce.

A package is a directory or a .jskill zip holding skill.json (the manifest), procedure.py (the executable), cases/*.json (train, dev and
holdout acceptance cases), an optional judge.py (UNTRUSTED extra checks) and docs. Nothing here names any AI vendor or model.
This module only READS and INSPECTS: it never imports or runs package code (that is core/skillsandbox.py, inside the OS sandbox, after the owner approves).

A package is read from disk exactly ONCE (snapshot). Review, approval, verification, registration and the archive copy all use those same bytes.
The fingerprint is the sha256 of the exact bytes of skill.json, which lists the sha256 of every other file, so one value pins every byte.
Hashes detect corruption, edits and substitution. They do not prove WHO wrote a package: the owner approves a fingerprint, and a fingerprint
passed along separately from the package is what detects a swapped package."""
import ast
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from re import _constants as _rc, _parser as _rp

FORMAT = "jarvis-skill/1"
ALLOWED_FILES = re.compile(r"^(?:skill\.json|procedure\.py|judge\.py|cases/[a-z0-9_.-]+\.json|docs/[A-Za-z0-9_.-]+\.(?:md|txt)|[A-Z]+\.(?:md|txt))$")
MAX_FILES, MAX_BYTES, MAX_FILE = 40, 2_000_000, 400_000

# Permissions this installer UNDERSTANDS. Only "compute" can be granted: installing a skill never grants an operating-system capability.
GRANTABLE = {"compute"}
KNOWN_UNGRANTABLE = {"fs.read", "fs.write", "network", "process", "blender", "pc_control", "secrets", "gpu", "paid_api", "clipboard", "screen"}

# Standard-library modules a pure-compute skill may import. Anything else (os, sys, subprocess, socket, ctypes, requests, ...) is rejected.
SAFE_MODULES = {"math", "cmath", "decimal", "fractions", "statistics", "json", "re", "itertools", "functools", "collections", "string",
                "datetime", "textwrap", "heapq", "bisect", "operator", "typing", "dataclasses", "enum", "unicodedata", "numbers", "copy", "__future__"}
BANNED_CALLS = {"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr", "input", "breakpoint",
                "memoryview", "help", "exit", "quit"}
BANNED_ATTRS = {"__class__", "__subclasses__", "__globals__", "__builtins__", "__dict__", "__bases__", "__mro__", "__code__", "__closure__",
                "__import__", "__loader__", "__spec__", "__getattribute__", "__reduce__", "__reduce_ex__", "f_globals", "f_locals", "gi_frame", "cr_frame"}
STOPWORDS = {"the", "and", "you", "for", "are", "can", "get", "how", "what", "this", "that", "with", "have", "will", "your", "jarvis", "please", "thanks",
             "make", "want", "need", "tell", "show", "give", "let", "now", "yes", "not", "but", "all", "any", "out", "who", "why", "when", "then", "than"}
PARAM_TYPES = ("string", "number", "integer", "decimal", "strings", "numbers")

NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class PackageError(ValueError):
    """A package that must not be installed; the message says exactly why."""


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def semver(v):
    return tuple(int(x) for x in v.split("."))


# ---- reading a package into memory (directory or zip), safely, ONCE ------------------------------------------------------------------------------

def _zip_files(blob):
    out = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise PackageError(f"too many files ({len(infos)} > {MAX_FILES})")
        names = [i.filename for i in infos]
        if len({n.lower() for n in names}) != len(names):
            raise PackageError("duplicate file names in the archive")
        if sum(i.file_size for i in infos) > MAX_BYTES:
            raise PackageError("package is too large when unpacked")
        for i in infos:
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise PackageError(f"{i.filename}: links are not allowed in a package")
            out[i.filename] = z.read(i)
    return out


def build_zip(files):
    """Deterministic zip of the verified files (fixed timestamps, sorted): the form that is archived."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in sorted(files.items()):
            z.writestr(zipfile.ZipInfo(n, (2020, 1, 1, 0, 0, 0)), d)
    return buf.getvalue()


def snapshot(src):
    """-> (files {name: bytes}, canonical_zip_bytes). The package is read exactly once; everything after (review, approval, verification,
    registration, archive) works on THESE bytes, never on the path again, so swapping the file on disk mid-install changes nothing.
    `src` is a path to a folder or a .jskill file, or the raw bytes of a .jskill."""
    out = {}
    if isinstance(src, (bytes, bytearray)):
        try:
            out = _zip_files(bytes(src))
        except zipfile.BadZipFile:
            raise PackageError("not a valid .jskill archive")
    else:
        p = Path(src)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_symlink():
                    raise PackageError(f"{f.name}: links are not allowed in a package")
                if f.is_file():
                    out[f.relative_to(p).as_posix()] = f.read_bytes()
        elif p.is_file():
            try:
                out = _zip_files(p.read_bytes())
            except zipfile.BadZipFile:
                raise PackageError(f"{p.name}: not a package directory or a .jskill file")
        else:
            raise PackageError(f"{p.name}: not found")
    for name, data in out.items():
        pp = PurePosixPath(name)
        if pp.is_absolute() or ".." in pp.parts or ":" in name or "\\" in name:
            raise PackageError(f"{name}: unsafe path")
        if not ALLOWED_FILES.match(name):
            raise PackageError(f"{name}: file type or location is not allowed in a skill package")
        if len(data) > MAX_FILE:
            raise PackageError(f"{name}: file is too large")
    if len(out) > MAX_FILES or sum(map(len, out.values())) > MAX_BYTES:
        raise PackageError("package is too large")
    return out, build_zip(out)


def read_files(src):
    return snapshot(src)[0]


def fingerprint(files):
    """sha256 of the EXACT bytes of skill.json (which lists the sha256 of every other file): one value pins every byte of the package."""
    return sha256(files["skill.json"])


def pack(src_dir, out_file):
    """Author tool: directory -> .jskill with the manifest's `files` hashes refreshed. The installer never trusts the result blindly."""
    src = Path(src_dir)
    man = json.loads((src / "skill.json").read_text(encoding="utf-8"))
    files = read_files(src)
    man["files"] = {n: sha256(d) for n, d in sorted(files.items()) if n != "skill.json"}
    (src / "skill.json").write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    files["skill.json"] = (src / "skill.json").read_bytes()
    Path(out_file).write_bytes(build_zip(files))
    return man


# ---- validation (no code is run) -----------------------------------------------------------------------------------------------------------------

def regex_problem(pattern):
    """Package-supplied regexes run in the HOST process on the owner's sentences, so a catastrophic-backtracking pattern would freeze JARVIS.
    Reject the shapes that cause it: back-references, a repeat inside a repeat, a repeated alternation, over-long patterns. -> reason or None."""
    if not isinstance(pattern, str) or len(pattern) > 200:
        return "must be text of at most 200 characters"
    try:
        tree = _rp.parse(pattern)
    except re.error as e:
        return f"is not a valid pattern ({e})"
    if tree.state.groups - 1 > 3:
        return "has too many groups"
    repeats = {_rc.MAX_REPEAT, _rc.MIN_REPEAT}
    if hasattr(_rc, "POSSESSIVE_REPEAT"):
        repeats.add(_rc.POSSESSIVE_REPEAT)

    def has_branch(sub):
        for op, av in sub:
            if op is _rc.BRANCH:
                return True
            if op is _rc.SUBPATTERN and has_branch(av[3]):
                return True
            if op in repeats and has_branch(av[2]):
                return True
        return False

    def walk(sub, in_repeat):
        for op, av in sub:
            if op in (_rc.GROUPREF, _rc.GROUPREF_EXISTS):
                return "uses a back-reference"
            if op in repeats:
                lo, hi, body = av
                again = hi > 1
                if again and in_repeat:
                    return "has a repeat inside a repeat (catastrophic backtracking risk)"
                if again and has_branch(body):
                    return "repeats an alternation (catastrophic backtracking risk)"
                bad = walk(body, in_repeat or again)
            elif op is _rc.BRANCH:
                bad = next((b for b in (walk(br, in_repeat) for br in av[1]) if b), None)
            elif op is _rc.SUBPATTERN:
                bad = walk(av[3], in_repeat)
            elif op in (_rc.ASSERT, _rc.ASSERT_NOT):
                bad = walk(av[1], in_repeat)
            else:
                bad = None
            if bad:
                return bad
        return None
    return walk(tree, False)


def load(src):
    """-> {"manifest", "files", "fingerprint", "problems"[], "warnings"[]}. `problems` empty means structurally sound; it says nothing about safety yet.
    `src` may be an already-read files dict (from snapshot) so the caller keeps working on the same bytes."""
    files = src if isinstance(src, dict) else read_files(src)
    problems, warnings = [], []
    if "skill.json" not in files:
        raise PackageError("skill.json is missing")
    try:
        man = json.loads(files["skill.json"].decode("utf-8"))
    except ValueError as e:
        raise PackageError(f"skill.json is not valid JSON ({e})")
    if not isinstance(man, dict):
        raise PackageError("skill.json must be an object")
    out = {"manifest": man, "files": files, "fingerprint": fingerprint(files), "problems": problems, "warnings": warnings}
    need = {"format": str, "id": str, "version": str, "title": str, "description": str, "author": dict, "scope": str, "permissions": list, "dependencies": dict,
            "entry": str, "params": dict, "triggers": dict, "recovery": dict, "errors": dict, "verify": dict, "provenance": dict, "files": dict, "reply": str}
    for k, t in need.items():
        if k not in man:
            problems.append(f"manifest is missing '{k}'")
        elif not isinstance(man[k], t):
            problems.append(f"manifest '{k}' must be {t.__name__}")
    if problems:
        return out
    if man["format"] != FORMAT:
        problems.append(f"unsupported format {man['format']!r} (this installer reads {FORMAT})")
    if not NAME.match(man["id"]):
        problems.append("id must look like 'group.skill_name' (lowercase, dots and underscores)")
    if not SEMVER.match(man["version"]):
        problems.append("version must be major.minor.patch")
    if not man["author"].get("name"):
        problems.append("author.name is required")
    if not man["scope"].strip():
        problems.append("scope must state exactly what this skill was verified for")
    if "procedure.py" not in files:
        problems.append("procedure.py is missing")
    for need_file in ("cases/train.json", "cases/dev.json", "cases/holdout.json"):
        if need_file not in files:
            problems.append(f"{need_file} is missing (training, development and holdout cases are all required)")
    if not man["verify"].get("invariants"):
        problems.append("verify.invariants is empty: a skill needs at least one independent, measurable acceptance check")
    declared = man["files"]
    for n, d in files.items():
        if n == "skill.json":
            continue
        if n not in declared:
            problems.append(f"{n} is in the package but not listed in the manifest hashes")
        elif declared[n] != sha256(d):
            problems.append(f"{n}: hash does not match the manifest (tampered or corrupted)")
    for n in declared:
        if n not in files:
            problems.append(f"{n} is listed in the manifest but missing from the package")
    for perm in man["permissions"]:
        if perm in GRANTABLE:
            continue
        if perm in KNOWN_UNGRANTABLE:
            problems.append(f"permission '{perm}' requested: this installer never grants operating-system capabilities, so the package is refused")
        else:
            problems.append(f"permission {perm!r} is unknown, so the package is refused")
    if "compute" not in man["permissions"]:
        problems.append("permissions must declare 'compute'")
    t = man["triggers"]
    if not (isinstance(t.get("all"), list) and t["all"] and all(isinstance(g, list) and g and all(isinstance(w, str) and w for w in g) for g in t["all"])):
        problems.append("triggers.all must be a non-empty list of non-empty word groups")
    else:
        if len(t["all"]) < 2:
            problems.append("triggers.all needs at least two word groups (a verb and an object) so the skill cannot hijack ordinary chat")
        words = [w for g in t["all"] for w in g] + list(t.get("exclude") or [])
        if any(len(w) > 40 for w in words) or any(len(g) > 40 for g in t["all"]):
            problems.append("trigger words must be at most 40 characters and groups at most 40 words")
        if any(w.lower() in STOPWORDS or len(w) < 3 for g in t["all"] for w in g):
            problems.append("a trigger group contains a stop-word or a word under 3 letters: too broad, it could hijack ordinary chat")
    if t.get("match") != "word":
        problems.append("triggers.match must be 'word' (whole-word matching) for installed skills")
    if not t.get("exclude"):
        warnings.append("triggers.exclude is empty: narrow exclusions are strongly advised")
    for name, spec in man["params"].items():
        if not isinstance(spec, dict) or spec.get("type") not in PARAM_TYPES:
            problems.append(f"param {name}: unsupported type")
            continue
        if spec["type"] in ("number", "integer", "decimal") and not ("min" in spec and "max" in spec):
            problems.append(f"param {name}: numeric parameters need min and max (the host rejects values outside and fuzzes inside)")
        if spec.get("from_text"):
            bad = regex_problem(spec["from_text"])
            if bad:
                problems.append(f"param {name}: from_text {bad}")
        if "default" not in spec and not spec.get("ask"):
            problems.append(f"param {name}: a required parameter needs an 'ask' question so JARVIS can ask for it instead of inventing it")
    for cls, action in man["recovery"].items():
        if cls not in man["errors"]:
            problems.append(f"recovery names an undeclared error '{cls}'")
        if not (isinstance(action, dict) and (action.get("ask_user") or isinstance(action.get("set"), dict))):
            problems.append(f"recovery for '{cls}' must either set parameters or ask the user")
    for cls, desc in man["errors"].items():
        if not (isinstance(desc, str) and desc):
            problems.append(f"error '{cls}' needs a description")
    if not set(re.findall(r"\{(\w+)\}", man["reply"])):
        warnings.append("reply template has no {placeholders}: the result will not appear in the answer")
    return out


# ---- static inspection of the code (parses, never runs) ------------------------------------------------------------------------------------------

def inspect_code(source, label="procedure.py"):
    """-> (problems, facts). Pure-compute rules: only SAFE_MODULES, no dynamic code, no file/process/network names, no dunder tricks.
    This is the FIRST layer; the OS sandbox is the one that does not depend on it being complete."""
    problems, facts = [], {"imports": [], "functions": []}
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"{label}: syntax error on line {e.lineno}"], facts
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
            if node.level:
                problems.append(f"{label}:{node.lineno}: relative imports are not allowed")
            if any(a.name == "*" for a in node.names):
                problems.append(f"{label}:{node.lineno}: 'import *' is not allowed")
        else:
            mods = []
        for m in mods:
            facts["imports"].append(m)
            if m.split(".")[0] not in SAFE_MODULES:
                problems.append(f"{label}:{node.lineno}: imports '{m}', which is outside the pure-compute allowlist")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
            problems.append(f"{label}:{node.lineno}: calls {node.func.id}(), which is not allowed")
        if isinstance(node, ast.Attribute) and (node.attr in BANNED_ATTRS or (node.attr.startswith("__") and node.attr.endswith("__") and node.attr not in ("__init__", "__str__", "__repr__", "__name__"))):
            problems.append(f"{label}:{node.lineno}: touches '{node.attr}', which is not allowed")
        if isinstance(node, ast.Name) and node.id in BANNED_ATTRS:
            problems.append(f"{label}:{node.lineno}: uses '{node.id}', which is not allowed")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append(f"{label}:{node.lineno}: global/nonlocal is not allowed")
        if isinstance(node, ast.FunctionDef):
            facts["functions"].append(node.name)
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await)):
            problems.append(f"{label}:{node.lineno}: async code is not allowed")
    return sorted(set(problems)), facts


def parse_cases(files):
    """-> ({"train": [...], "dev": [...], "holdout": [...]}, problems)."""
    out, problems = {}, []
    for split in ("train", "dev", "holdout"):
        try:
            rows = json.loads(files[f"cases/{split}.json"].decode("utf-8"))
            if not (isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
                raise ValueError("must be a list of case objects")
        except (KeyError, ValueError) as e:
            problems.append(f"cases/{split}.json: {e}")
            rows = []
        out[split] = rows
    return out, problems
