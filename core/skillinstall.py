"""The Skill Installer: package in -> inspected -> approved by the owner -> verified by the host's own judge -> registered in the existing
procedure library (core/procedures.py) -> discoverable by chat (core/skillhost.py).

Nothing is registered, and no package code is ever executed, until: (1) the package parses and every hash matches, (2) static inspection finds no
unsafe code, (3) every requested permission is one this installer can grant (only 'compute'), and (4) the OWNER has approved this exact fingerprint.
Only then does the host judge run the package, inside the OS-enforced AppContainer sandbox (core/skillsandbox.py). A failure at any stage changes
nothing that is installed. Every step is appended to events.jsonl.

BYTE BINDING. The package is read from disk once (skillpkg.snapshot). The fingerprint the owner approves is computed from those bytes, the
verification runs on those bytes, the library entry is written from those bytes and the archive copy is those bytes. Replacing the file on disk
between review and install cannot change what is installed: the fingerprint would differ and the approval would not match. Before every use of an
installed skill, `verify_installed` re-checks the stored copy against its archived package.

Layout under the skills root (JARVIS_SKILLS_DIR, else data/skills):
    quarantine/<fingerprint24>/   package/ (the files) + report.json      (an inspection copy: never imported, never on any path)
    library/                      core.procedures.Library: <id>/v<N>/{manifest.json, source.py}, ACTIVE / DISABLED markers, uses.jsonl
    archive/<id>/<N>.jskill       the verified package of each installed version (canonical zip of the approved bytes)
    lab/                          the AppContainer lab the sandbox uses (core/skillsandbox.py)
    events.jsonl                  audit log: review, approve, verify, install, update, rollback, disable, run, failure"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from core import skilljudge, skillpkg, skillsandbox
from core.procedures import Library, NotVerified

MIN_APPROVAL = 24            # hex characters of the fingerprint the owner must quote (96 bits)

_REGEX_PROBE = r'''
import json, re, sys, time
req = json.load(sys.stdin)
worst = 0.0
for pat in req["patterns"]:
    rx = re.compile(pat, re.I)
    for s in req["strings"]:
        t = time.perf_counter(); rx.search(s); worst = max(worst, time.perf_counter() - t)
print(json.dumps(worst))
'''


def root():
    env = os.environ.get("JARVIS_SKILLS_DIR")
    if env:
        return Path(env)
    from core import storage
    return storage.DATA / "skills"


def _regex_seconds(patterns):
    """Worst time any package regex takes on hostile 400-character inputs, measured in a throwaway process (so a hang cannot freeze JARVIS)."""
    strings = ["a" * 400, "1" * 400, " " * 400, "$" + "9" * 398, "9." * 199, "people " * 57, "ab" * 200, "$1," + "1," * 190]
    try:
        p = subprocess.run([sys.executable, "-I", "-c", _REGEX_PROBE], input=json.dumps({"patterns": patterns, "strings": strings}), capture_output=True,
                           text=True, timeout=4)
        return float(json.loads(p.stdout)) if p.returncode == 0 else 99.0
    except (subprocess.TimeoutExpired, ValueError):
        return 99.0


class Installer:
    def __init__(self, base=None):
        self.base = Path(base) if base else root()
        self.base.mkdir(parents=True, exist_ok=True)
        self.lib = Library(self.base / "library")
        self._seen = {}

    # ---- audit
    def event(self, kind, **row):
        with open(self.base / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind, **row}) + "\n")

    def events(self):
        f = self.base / "events.jsonl"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    # ---- stage 1-3: static review of ONE snapshot (runs NO package code)
    def _review(self, files, archive):
        pkg = skillpkg.load(files)
        man, problems, warnings = pkg["manifest"], list(pkg["problems"]), list(pkg["warnings"])
        fp = pkg["fingerprint"]
        if problems and not (isinstance(man.get("id"), str) and isinstance(man.get("params"), dict) and isinstance(man.get("dependencies"), dict)
                             and isinstance(man.get("title"), str) and isinstance(man.get("author"), dict)):
            return pkg, {"status": "rejected", "fingerprint": fp, "problems": problems, "warnings": warnings, "summary": "Rejected: " + "; ".join(problems[:4])}
        facts = {}
        if "procedure.py" in files:
            p, facts = skillpkg.inspect_code(files["procedure.py"].decode("utf-8", "replace"))
            problems += p
            undeclared = sorted({m.split(".")[0] for m in facts["imports"]} - set(man["dependencies"].get("modules", [])) - {"__future__"})
            if undeclared:
                problems.append(f"imports {undeclared} but dependencies.modules does not declare them")
            if man["entry"] not in facts["functions"]:
                problems.append(f"entry function '{man['entry']}' is not defined in procedure.py")
        if "judge.py" in files:
            p, _ = skillpkg.inspect_code(files["judge.py"].decode("utf-8", "replace"), "judge.py")
            problems += p
        for m in man["dependencies"].get("modules", []):
            if m not in skillpkg.SAFE_MODULES:
                problems.append(f"dependency '{m}' is not in the allowed standard-library list; unknown software is never installed automatically")
        pats = [s["from_text"] for s in man["params"].values() if isinstance(s, dict) and s.get("from_text") and not skillpkg.regex_problem(s["from_text"])]
        if pats and not problems and _regex_seconds(pats) > 0.25:
            problems.append("a from_text pattern is too slow on adversarial input (it could freeze the chat)")
        installed = self.installed(man["id"]) if skillpkg.NAME.match(man["id"]) else None
        if installed and skillpkg.SEMVER.match(man["version"]):
            if skillpkg.semver(man["version"]) <= skillpkg.semver(installed["pkg_version"]):
                problems.append(f"version {man['version']} is not newer than the installed {installed['pkg_version']}")
        status = "rejected" if problems else "reviewed"
        report = {"status": status, "fingerprint": fp, "id": man["id"], "version": man["version"], "title": man["title"], "author": man["author"],
                  "scope": man["scope"], "permissions": man["permissions"], "dependencies": man["dependencies"], "imports": sorted(set(facts.get("imports", []))),
                  "claimed_provenance": man["provenance"], "problems": problems, "warnings": warnings, "update_of": installed and installed["pkg_version"],
                  "cases": {s: len(v) for s, v in skillpkg.parse_cases(files)[0].items()}, "has_package_judge": "judge.py" in files}
        report["summary"] = self.summary(report)
        if status == "reviewed":
            q = self.base / "quarantine" / fp[:MIN_APPROVAL]
            if q.exists():
                shutil.rmtree(q)
            for n, d in files.items():
                (q / "package" / n).parent.mkdir(parents=True, exist_ok=True)
                (q / "package" / n).write_bytes(d)
            (q / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        self.event("review", id=man["id"], version=man["version"], fingerprint=fp, status=status, problems=problems)
        return pkg, report

    def review(self, src):
        """-> report dict. status: 'rejected' (do not install) or 'reviewed' (safe to ask the owner). Runs nothing from the package."""
        try:
            files, archive = skillpkg.snapshot(src)
            return self._review(files, archive)[1]
        except skillpkg.PackageError as e:
            self.event("review", package=str(src)[:200], status="rejected", problems=[str(e)])
            return {"status": "rejected", "fingerprint": None, "problems": [str(e)], "warnings": [], "summary": f"Rejected: {e}"}

    @staticmethod
    def summary(r):
        lines = [f"{'REJECTED' if r['status'] == 'rejected' else 'Reviewed'}: {r['title']} ({r['id']} {r['version']}) by {r['author'].get('name')}",
                 f"  scope: {r['scope']}", f"  permissions requested: {', '.join(r['permissions']) or 'none'}  (installing grants nothing beyond pure computation)",
                 f"  imports: {', '.join(r['imports']) or 'none'}", f"  fingerprint: {r['fingerprint']}"]
        lines += [f"  PROBLEM: {p}" for p in r["problems"]] + [f"  note: {w}" for w in r["warnings"]]
        return "\n".join(lines)

    # ---- stage 4-7: approval, host verification, registration (all on the SAME snapshot)
    def install(self, src, approve=None, extra_cases=None):
        """-> {"ok", "stage", "message", ...}. `approve` is the fingerprint (at least its first 24 characters) the owner has read and accepts."""
        try:
            files, archive = skillpkg.snapshot(src)
        except skillpkg.PackageError as e:
            self.event("review", package=str(src)[:200], status="rejected", problems=[str(e)])
            return {"ok": False, "stage": "review", "message": f"Rejected: {e}", "report": {"status": "rejected", "problems": [str(e)]}}
        pkg, report = self._review(files, archive)
        if report["status"] == "rejected":
            return {"ok": False, "stage": "review", "message": report["summary"], "report": report}
        fp = report["fingerprint"]
        if not approve or len(approve) < MIN_APPROVAL or not fp.startswith(approve.lower()):
            self.event("awaiting_approval", id=report["id"], fingerprint=fp)
            return {"ok": False, "stage": "approval", "report": report,
                    "message": report["summary"] + f"\n\nNothing has been run or installed. To install exactly this package, approve its fingerprint:\n  install --approve {fp[:MIN_APPROVAL]}"}
        man = pkg["manifest"]
        self.event("approved", id=man["id"], fingerprint=fp)
        try:
            acc = skilljudge.accept(pkg, extra_cases)
        except skillsandbox.Unavailable as e:
            self.event("failure", id=man["id"], stage="sandbox", fingerprint=fp, error=str(e))
            return {"ok": False, "stage": "sandbox", "report": report, "message": f"Cannot verify: the OS sandbox is unavailable ({e}). Nothing was run or installed."}
        if acc["requirements"] or acc["failures"]:
            self.event("failure", id=man["id"], stage="verify", fingerprint=fp, requirements=acc["requirements"], failures=acc["failures"][:10])
            why = acc["requirements"] + acc["failures"]
            return {"ok": False, "stage": "verify", "report": report, "acceptance": acc,
                    "message": f"Verification FAILED ({acc['passed']}/{acc['total']} passed); nothing installed.\n  " + "\n  ".join(why[:12])}
        evidence = {"judge": acc["judge"], "passed": acc["passed"], "total": acc["total"], "tasks": acc["tasks"]}
        before = set(self.lib.versions(man["id"]))
        arc = self.base / "archive" / man["id"]
        lib_version = None
        try:
            lm = self.lib.register(man["id"], man["title"], files["procedure.py"].decode("utf-8").replace("\r\n", "\n"), man["triggers"], man["params"], man["recovery"],
                                   f"{man['author'].get('name')} (third-party package; verified by the host judge)", evidence, entry=man["entry"], scope=man["scope"],
                                   extra={"skill": {k: man[k] for k in ("errors", "verify", "reply", "permissions", "dependencies", "provenance", "author", "description")},
                                          "pkg_version": man["version"], "fingerprint": fp})
            lib_version = lm["version"]
            arc.mkdir(parents=True, exist_ok=True)
            (arc / f"{lib_version}.jskill").write_bytes(archive)                  # the approved bytes, canonical zip
            self.lib.set_active(man["id"], lib_version)
            self.lib.set_disabled(man["id"], False)
        except Exception as e:                                # noqa: BLE001 - leave the library exactly as it was
            for v in set(self.lib.versions(man["id"])) - before:
                shutil.rmtree(self.lib.root / man["id"] / f"v{v}", ignore_errors=True)
            if lib_version is not None:
                (arc / f"{lib_version}.jskill").unlink(missing_ok=True)
            self.event("failure", id=man["id"], stage="register", error=f"{type(e).__name__}: {e}")
            return {"ok": False, "stage": "register", "message": f"Registration failed ({e}); nothing installed.", "report": report}
        self.event("update" if report["update_of"] else "install", id=man["id"], version=man["version"], lib_version=lib_version, fingerprint=fp,
                   passed=acc["passed"], total=acc["total"])
        return {"ok": True, "stage": "installed", "report": report, "acceptance": acc, "lib_version": lib_version,
                "message": f"Installed {man['title']} {man['version']}: {acc['passed']}/{acc['total']} independent checks passed. It is now active and chat can use it."}

    # ---- integrity of what is installed
    def verify_installed(self, pid, version=None):
        """True only if the stored library entry is byte-for-byte what the owner approved: the archived package still hashes to the approved
        fingerprint, and the stored source and every field used to route, run and verify equal the archive's. Cached until a file changes."""
        v = version or self.lib.active_version(pid)
        try:
            a = self.base / "archive" / pid / f"{v}.jskill"
            m = self.lib.root / pid / f"v{v}" / "manifest.json"
            key = (a.stat().st_mtime_ns, a.stat().st_size, m.stat().st_mtime_ns, (m.parent / "source.py").stat().st_mtime_ns)
            if self._seen.get((pid, v)) == key:
                return True
            man, src = self.lib.get(pid, v)                    # also re-checks the source hash
            files = skillpkg.read_files(a)
            pkg = skillpkg.load(files)
            am = pkg["manifest"]
            ok = (not pkg["problems"] and pkg["fingerprint"] == man.get("fingerprint")
                  and src == files["procedure.py"].decode("utf-8").replace("\r\n", "\n")
                  and all(man[k] == am[k] for k in ("triggers", "params", "recovery", "entry", "scope", "title"))
                  and all(man["skill"][k] == am[k] for k in ("errors", "verify", "reply", "permissions", "dependencies")))
        except (OSError, NotVerified, KeyError, ValueError, TypeError, skillpkg.PackageError):
            ok = False
        if ok:
            self._seen[(pid, v)] = key
        else:
            self._seen.pop((pid, v), None)
            self.event("failure", id=pid, stage="integrity", error="installed copy does not match the approved package")
        return ok

    # ---- management
    def installed(self, pid):
        v = self.lib.active_version(pid) if pid in self.lib.ids() else None
        if v is None:
            return None
        try:
            man, _ = self.lib.get(pid, v)
        except (NotVerified, OSError, ValueError):
            return {"id": pid, "lib_version": v, "pkg_version": "0.0.0", "title": "(stored source changed: refused)", "broken": True, "disabled": self.lib.is_disabled(pid)}
        return {"id": pid, "lib_version": v, "pkg_version": man.get("pkg_version", "0.0.0"), "title": man["title"], "disabled": self.lib.is_disabled(pid),
                "fingerprint": man.get("fingerprint"), "versions": self.lib.versions(pid)}

    def list(self):
        return [i for i in (self.installed(p) for p in self.lib.ids()) if i]

    def disable(self, pid):
        self._need(pid)
        self.lib.set_disabled(pid, True)
        self.event("disable", id=pid)

    def enable(self, pid):
        self._need(pid)
        self.lib.set_disabled(pid, False)
        self.event("enable", id=pid)

    def rollback(self, pid):
        """Re-activate the version installed before the active one. Newer versions stay on disk for the record."""
        self._need(pid)
        cur = self.lib.active_version(pid)
        older = [v for v in self.lib.versions(pid) if v < cur]
        if not older:
            raise ValueError(f"{pid} has no earlier version to roll back to")
        if not self.verify_installed(pid, older[-1]):
            raise ValueError(f"{pid} version {older[-1]} no longer matches its approved package; not rolling back to it")
        self.lib.set_active(pid, older[-1])
        self.event("rollback", id=pid, from_version=cur, to_version=older[-1])
        return older[-1]

    def _need(self, pid):
        if pid not in self.lib.ids():
            raise KeyError(f"no installed skill called {pid}")


def cli(argv):
    """python manage.py skill <review|install|list|disable|enable|rollback|history|pack> ..."""
    usage = ("skill review <package>                    inspect only; runs nothing, shows the fingerprint\n"
             "skill install <package> --approve <fp>   verify with the host judge and install exactly that fingerprint (at least 24 characters)\n"
             "skill list | history [id] | disable <id> | enable <id> | rollback <id>\n"
             "skill pack <folder> <file.jskill>         author tool: refresh hashes and zip a package folder\n"
             "skill selftest                            prove the OS sandbox holds from THIS shell (run it from the same kind of shell JARVIS runs in)")
    if not argv:
        print(usage)
        return 0
    cmd, a = argv[0], argv[1:]
    ins = Installer()
    if cmd == "review" and a:
        r = ins.review(a[0])
        print(r["summary"])
        return 0 if r["status"] == "reviewed" else 1
    if cmd == "install" and a:
        appr = a[a.index("--approve") + 1] if "--approve" in a and a.index("--approve") + 1 < len(a) else None
        r = ins.install(a[0], approve=appr)
        print(r["message"])
        return 0 if r["ok"] else 1
    if cmd == "list":
        for i in ins.list():
            print(f"{i['id']}  {i['pkg_version']}  {'DISABLED' if i.get('disabled') else 'active'}  {i['title']}")
        return 0
    if cmd == "history":
        for e in ins.events():
            if not a or e.get("id") == a[0]:
                print(json.dumps(e))
        return 0
    if cmd in ("disable", "enable", "rollback") and a:
        try:
            r = getattr(ins, cmd)(a[0])
        except (KeyError, ValueError) as e:
            print(e)
            return 1
        print(f"{cmd}: ok" + (f" (now version {r})" if cmd == "rollback" else ""))
        return 0
    if cmd == "selftest":
        root_dir = Path(__file__).resolve().parent.parent
        r = skillsandbox.selftest([p for p in (root_dir / ".env", root_dir / "data" / "users.json", root_dir / "data" / "settings.json") if p.exists()])
        for name, ok in r["checks"].items():
            print(("PASS " if ok else "FAIL ") + name)
        print(f"shell elevated (administrator): {'YES' if r['elevated'] else 'no' if r['elevated'] is False else 'unknown'}")
        print("SANDBOX HOLDS" if r["ok"] else f"SANDBOX FAILED {r['error'] or ''}")
        return 0 if r["ok"] else 1
    if cmd == "pack" and len(a) == 2:
        skillpkg.pack(a[0], a[1])
        print(f"packed {a[1]}")
        return 0
    print(usage)
    return 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
