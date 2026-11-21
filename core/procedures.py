"""Verified procedures: stored, parameterized, executable know-how that survives restarts and is reused instead of regenerated.

A procedure is a source file plus a manifest: what it is for (trigger words and exclusions), its typed parameters, a recovery table
(error class -> what to change, or ask the user) and EVIDENCE. Registering without independent-judge evidence is refused. Matching is
deliberately narrow: a task that merely resembles the procedure (more bones, a character, a tail) is NOT matched, so a known skill is
never stretched over work it was not verified for. The library knows no skill: Blender, Python or PC control all register the same way.

Layout: <root>/<procedure id>/v<N>/{manifest.json, source.py}; <root>/uses.jsonl records every match, run and outcome.
This is separate from core/skill_memory.py, which is a non-executable notebook (its docstring says only trusted infrastructure may
promote entries to verified executable skills: this registry is that gate, and it demands judge evidence)."""
import hashlib
import json
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
                 "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}
_NW = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.I)


class NotVerified(ValueError):
    pass


class BadParams(ValueError):
    pass


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Library:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- register / load
    def register(self, pid, title, source, triggers, params, recovery, authored_by, evidence, entry="run", scope="", extra=None):
        """Evidence = {"judge": hash, "passed": n, "total": m, "tasks": [...]}: all of it must pass, on at least 12 tasks."""
        if not evidence or evidence.get("total", 0) < 12 or evidence.get("passed") != evidence.get("total") or not evidence.get("judge"):
            raise NotVerified(f"{pid}: needs independent-judge evidence with every task passed (at least 12), got {evidence and {k: evidence.get(k) for k in ('passed', 'total')}}")
        base = self.root / pid
        n = 1 + max([int(p.name[1:]) for p in base.glob("v*") if p.name[1:].isdigit()] or [0])
        d = base / f"v{n}"
        d.mkdir(parents=True)
        (d / "source.py").write_text(source, encoding="utf-8")
        man = {"id": pid, "version": n, "title": title, "triggers": triggers, "params": params, "recovery": recovery, "entry": entry, "scope": scope,
               "authored_by": authored_by, "source_sha": sha(source), "evidence": evidence, "registered": time.strftime("%Y-%m-%dT%H:%M:%S")}
        for k, v in (extra or {}).items():
            man.setdefault(k, v)                       # installer-supplied facts (permissions, package hash, provenance); never overrides the core fields
        (d / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
        return man

    def versions(self, pid):
        return sorted(int(p.name[1:]) for p in (self.root / pid).glob("v*") if p.name[1:].isdigit())

    def active_version(self, pid):
        """The version in use: the pointer file when present (rollback / update), else the newest."""
        f = self.root / pid / "ACTIVE"
        try:
            v = int(f.read_text(encoding="utf-8").strip())
            if v in self.versions(pid):
                return v
        except (OSError, ValueError):
            pass
        vs = self.versions(pid)
        return vs[-1] if vs else None

    def set_active(self, pid, version):
        if version not in self.versions(pid):
            raise KeyError(f"{pid} has no version {version}")
        (self.root / pid / "ACTIVE").write_text(str(version), encoding="utf-8")

    def set_disabled(self, pid, disabled=True):
        f = self.root / pid / "DISABLED"
        if disabled:
            f.write_text("1", encoding="utf-8")
        elif f.exists():
            f.unlink()

    def is_disabled(self, pid):
        return (self.root / pid / "DISABLED").exists()

    def ids(self):
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def get(self, pid, version=None):
        """-> (manifest, source). The source hash is re-checked on every load: a changed file is refused, not run."""
        base = self.root / pid
        vs = sorted((int(p.name[1:]) for p in base.glob("v*") if p.name[1:].isdigit()))
        if not vs:
            return None
        if version is None:
            if (base / "DISABLED").exists():
                return None                                  # switched off: matched by nothing, run by nobody
            version = self.active_version(pid)
        d = base / f"v{version or vs[-1]}"
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        src = (d / "source.py").read_text(encoding="utf-8")
        if sha(src) != man["source_sha"]:
            raise NotVerified(f"{pid} v{man['version']}: source changed since it was verified")
        return man, src

    # ---- recognise
    def match(self, text, context=None):
        """-> {"id", "manifest", "params"} for the best procedure that applies to this task text, else None.
        Every trigger group needs one of its words; any exclusion word vetoes; parameters must be extractable."""
        low = (text or "").lower()
        best = None
        for pid in self.ids():
            got = self.get(pid)
            if not got:
                continue
            man, _ = got
            if not self.triggered(man, text):
                continue
            t = man["triggers"]
            params = self.extract(man, text, context or {})
            if params is None:
                continue
            score = sum(low.count(w) for g in t["all"] for w in g)
            if best is None or score > best[0]:
                best = (score, {"id": pid, "manifest": man, "params": params})
        return best[1] if best else None

    def triggered(self, man, text):
        """Do the trigger words of this procedure appear in `text` (every group has one) and no exclusion word? The narrow-scope gate."""
        low = (text or "").lower()
        t = man["triggers"]
        if t.get("match") == "word":                         # whole words only ("tip" is not "multiple"); the default stays substring for older procedures
            has = lambda w: re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", low) is not None
        else:
            has = lambda w: w in low
        return not any(has(w) for w in t.get("exclude", [])) and all(any(has(w) for w in group) for group in t["all"])

    def extract(self, man, text, context):
        """Fill the declared parameters from the task: regexes on the text first, then the task's own structured values. None if a required one is missing."""
        out = {}
        text = self.prep(man, text)
        for name, spec in man["params"].items():
            val = None
            if spec.get("from_text"):
                m = re.search(spec["from_text"], text, re.I)
                if m:
                    val = m.group(1) if m.lastindex == 1 else list(m.groups())
            if val is None and spec.get("from_context") in context:
                val = context[spec["from_context"]]
            if val is None and "default" in spec:
                val = spec["default"]
            if val is None:
                return None
            out[name] = self._coerce(spec, val)
        try:
            self.check_params(man, out)
        except BadParams:
            return None
        return out

    @staticmethod
    def _coerce(spec, val):
        """Text pulled out of a sentence -> the declared number type ("12.50" -> 12.5). Anything else is returned untouched."""
        try:
            if spec.get("type") == "number" and isinstance(val, str):
                return float(val.replace(",", ""))
            if spec.get("type") == "integer" and isinstance(val, str):
                return int(float(val.replace(",", "")))
            if spec.get("type") == "decimal" and isinstance(val, str):
                return val.replace(",", "").strip()               # exact decimal text, never a float
        except ValueError:
            pass
        return val

    @staticmethod
    def prep(man, text):
        """If a parameter declares number_words, "three ways" is read as "3 ways" for extraction. Only that; triggers use the owner's own words."""
        text = (text or "")[:400]
        if any(spec.get("number_words") for spec in man["params"].values()):
            text = _NW.sub(lambda m: str(_NUMBER_WORDS[m.group(1).lower()]), text)
        return text

    def diagnose(self, man, text, context=None):
        """-> (params, None) | (None, ("missing", [names])) | (None, ("invalid", reason)). Why a matched request cannot run: ask, or say what is wrong."""
        got = self.extract(man, text, context or {})
        if got is not None:
            return got, None
        miss = self.missing(man, text, context)
        if miss:
            return None, ("missing", miss)
        text = self.prep(man, text)
        partial = {}
        for name, spec in man["params"].items():
            m = re.search(spec["from_text"], text, re.I) if spec.get("from_text") else None
            partial[name] = self._coerce(spec, m.group(1)) if m else spec.get("default")
        try:
            self.check_params(man, partial)
        except BadParams as e:
            return None, ("invalid", str(e))
        return None, ("invalid", "the values could not be read")

    def missing(self, man, text, context=None):
        """Names of declared parameters the text does not supply (and that have no default): what to ASK the owner, never to invent."""
        out = []
        text = self.prep(man, text)
        for name, spec in man["params"].items():
            got = None
            if spec.get("from_text"):
                m = re.search(spec["from_text"], text or "", re.I)
                got = m.group(1) if m else None
            if got is None and spec.get("from_context") in (context or {}):
                got = True
            if got is None and "default" not in spec:
                out.append(name)
        return out

    @staticmethod
    def check_params(man, params):
        for name, spec in man["params"].items():
            if name not in params:
                raise BadParams(f"missing parameter {name}")
            v = params[name]
            kind = spec.get("type")
            if kind == "number" and not isinstance(v, (int, float)):
                raise BadParams(f"{name} must be a number")
            if kind == "integer" and (isinstance(v, bool) or not isinstance(v, int)):
                raise BadParams(f"{name} must be a whole number")
            if kind == "decimal":
                if not (isinstance(v, str) and re.fullmatch(r"\d{1,12}(?:\.\d{1,%d})?" % int(spec.get("places", 2)), v)):
                    raise BadParams(f"{name} must be a plain amount with at most {spec.get('places', 2)} decimal places")
                try:
                    d = Decimal(v)
                except InvalidOperation:
                    raise BadParams(f"{name} is not a number")
                if ("min" in spec and d < Decimal(str(spec["min"]))) or ("max" in spec and d > Decimal(str(spec["max"]))):
                    raise BadParams(f"{name} must be between {spec.get('min', '-inf')} and {spec.get('max', 'inf')}")
            if kind in ("number", "integer") and isinstance(v, (int, float)):
                if "min" in spec and v < spec["min"] or "max" in spec and v > spec["max"]:
                    raise BadParams(f"{name} must be between {spec.get('min', '-inf')} and {spec.get('max', 'inf')}")
            if kind == "string" and not isinstance(v, str):
                raise BadParams(f"{name} must be text")
            if kind == "strings" and not (isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v)):
                raise BadParams(f"{name} must be a list of text")
            if kind == "numbers" and not (isinstance(v, (list, tuple)) and v and all(isinstance(x, (int, float)) for x in v)):
                raise BadParams(f"{name} must be a list of numbers")
            if "enum" in spec and v not in spec["enum"]:
                raise BadParams(f"{name} must be one of {spec['enum']}")
        for k in params:
            if k not in man["params"]:
                raise BadParams(f"unknown parameter {k}")

    # ---- run
    def render(self, pid, params, version=None):
        """The executable script: the verified source plus ONE call line built from validated literals (never from free text)."""
        man, src = self.get(pid, version)
        self.check_params(man, params)
        args = ", ".join(f"{k}={json.dumps(list(v) if isinstance(v, tuple) else v)}" for k, v in sorted(params.items()))
        return src + f"\n{man['entry']}({args})\n"

    def recovery_for(self, man, error):
        """-> {"set": {...}} (retry with changed parameters), {"ask_user": True} or None, from the first error class named in the message."""
        for cls, action in man["recovery"].items():
            if cls in (error or ""):
                return {"class": cls, **action}
        return None

    # ---- usage log (what was matched, run and how it ended)
    def log(self, **row):
        with open(self.root / "uses.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **row}) + "\n")

    def uses(self):
        f = self.root / "uses.jsonl"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []
