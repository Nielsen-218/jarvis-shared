"""Skill Development Kit + Installer: format, static review, host judge, installer lifecycle, chat discovery, dry-run, restart persistence.
Package code runs for real inside the AppContainer sandbox throughout; nothing is mocked except where a test asserts that something is NOT called."""
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from core import craftintent, dryrun, skillhost, skillinstall, skilljudge, skillpkg, skillsandbox

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "skillkit" / "examples" / "util.split_bill"
EXAMPLE_PKG = ROOT / "skillkit" / "examples" / "util.split_bill-1.1.0.jskill"
pytestmark = [pytest.mark.skipif(os.name != "nt", reason="installed skills need the Windows AppContainer sandbox"), pytest.mark.serial]   # AppContainer profile setup isn't safe across parallel workers


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SKILLS_DIR", str(tmp_path / "skills"))
    return tmp_path / "skills"


def build(tmp_path, name="pkg", mutate=None, procedure=None, manifest=None, judge=None, version=None):
    """Copy the example to a scratch folder, apply a change, pack it (hashes refreshed) and return the .jskill path."""
    d = tmp_path / name
    shutil.copytree(EXAMPLE, d)
    if procedure is not None:
        (d / "procedure.py").write_text(procedure, encoding="utf-8")
    if judge is not None:
        (d / "judge.py").write_text(judge, encoding="utf-8")
    man = json.loads((d / "skill.json").read_text(encoding="utf-8"))
    if version:
        man["version"] = version
    if manifest:
        manifest(man)
    (d / "skill.json").write_text(json.dumps(man), encoding="utf-8")
    if mutate:
        mutate(d)
    out = tmp_path / f"{name}.jskill"
    skillpkg.pack(d, out)
    return out


def approve(ins, path):
    return ins.install(path, approve=skillpkg.load(skillpkg.read_files(path))["fingerprint"][:24])


PROC = (EXAMPLE / "procedure.py").read_text(encoding="utf-8")
BUGGY = PROC.replace("shares = [base + 1] * extra + [base] * (people - extra)", "shares = [base] * people")


# ---------------------------------------------------------------- package format

def test_example_package_is_structurally_valid():
    pkg = skillpkg.load(EXAMPLE_PKG)
    assert pkg["problems"] == [] and len(pkg["fingerprint"]) == 64


def test_format_names_no_model_vendor():
    text = (EXAMPLE / "skill.json").read_text(encoding="utf-8").lower() + (ROOT / "docs/learning/SKILL-PACKAGE-SPEC.md").read_text(encoding="utf-8").lower()
    for word in ("anthropic", "openai", "chatgpt", "claude"):
        assert word not in text.replace("any ai", "")


def test_tampered_file_is_rejected(tmp_path):
    good = build(tmp_path, "good")
    bad = tmp_path / "bad.jskill"
    with zipfile.ZipFile(good) as zi, zipfile.ZipFile(bad, "w") as zo:
        for i in zi.infolist():
            data = zi.read(i)
            if i.filename == "procedure.py":
                data += b"\n# sneaky\n"
            zo.writestr(i.filename, data)
    assert any("hash does not match" in p for p in skillpkg.load(bad)["problems"])


@pytest.mark.parametrize("name", ["../evil.py", "/abs.py", "C:/x.py", "cases/a b.json", "run.exe", "docs/../../x.md"])
def test_unsafe_paths_and_file_types_rejected(tmp_path, name):
    z = tmp_path / "z.jskill"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("skill.json", "{}")
        zf.writestr(name, "x")
    with pytest.raises(skillpkg.PackageError):
        skillpkg.read_files(z)


def test_missing_holdout_and_empty_invariants_rejected(tmp_path):
    p = build(tmp_path, mutate=lambda d: (d / "cases" / "holdout.json").unlink())
    assert any("holdout" in x for x in skillpkg.load(p)["problems"])
    p2 = build(tmp_path, "p2", manifest=lambda m: m["verify"].update(invariants=[]))
    assert any("invariants" in x for x in skillpkg.load(p2)["problems"])


@pytest.mark.parametrize("perm", ["network", "fs.write", "process", "blender", "pc_control", "paid_api", "made_up"])
def test_unsafe_or_unknown_permissions_rejected(tmp_path, base, perm):
    p = build(tmp_path, manifest=lambda m: m["permissions"].append(perm))
    ins = skillinstall.Installer(base)
    r = ins.review(p)
    assert r["status"] == "rejected" and any("permission" in x for x in r["problems"])
    assert not (base / "quarantine").exists() or not any((base / "quarantine").iterdir())      # rejected packages are not even kept
    assert ins.list() == []


# ---------------------------------------------------------------- static inspection

@pytest.mark.parametrize("code", [
    "import os\ndef run(total, tip_percent, people):\n    return {}\n",
    "import subprocess\ndef run(total, tip_percent, people):\n    return {}\n",
    "def run(total, tip_percent, people):\n    return eval('1')\n",
    "def run(total, tip_percent, people):\n    return open('x').read()\n",
    "def run(total, tip_percent, people):\n    return ().__class__.__bases__\n",
    "def run(total, tip_percent, people):\n    return __import__('os')\n",
    "from os import *\ndef run(total, tip_percent, people):\n    return {}\n",
])
def test_unsafe_code_is_rejected_before_anything_runs(tmp_path, base, code, monkeypatch):
    for fn in ("run", "run_many"):
        monkeypatch.setattr(skillsandbox, fn, lambda *a, **k: pytest.fail("package code must not run for a rejected package"))
    r = skillinstall.Installer(base).install(build(tmp_path, procedure=code), approve="0" * 24)
    assert not r["ok"] and r["stage"] == "review"


def test_undeclared_dependency_rejected(tmp_path, base):
    code = PROC.replace("from decimal", "import math\nfrom decimal")
    r = skillinstall.Installer(base).review(build(tmp_path, procedure=code))
    assert r["status"] == "rejected" and any("does not declare" in p for p in r["problems"])


def test_unknown_third_party_dependency_never_auto_installed(tmp_path, base):
    r = skillinstall.Installer(base).review(build(tmp_path, manifest=lambda m: m["dependencies"]["modules"].append("requests")))
    assert r["status"] == "rejected" and any("never installed automatically" in p for p in r["problems"])


# ---------------------------------------------------------------- sandbox runtime layer (the Python rules; the OS layer is tested adversarially elsewhere)

@pytest.mark.parametrize("body", [
    "open('leak.txt','w').write('x')",
    "import socket; socket.socket().connect(('127.0.0.1', 9))",
    "import subprocess; subprocess.run(['cmd','/c','echo hi'])",
    "__import__('ctypes')",
    "import os; os.remove('x')",
])
def test_sandbox_blocks_dangerous_runtime_actions(body):
    res = skillsandbox.run(f"def run():\n    {body}\n    return {{}}\n", "run", {})
    assert not res["ok"] and res["kind"] in ("PermissionError", "ImportError"), res


def test_sandbox_times_out_and_names_it():
    res = skillsandbox.run("def run():\n    while True:\n        pass\n", "run", {}, timeout=2)
    assert not res["ok"] and res["error"] == "timeout"


def test_sandbox_returns_named_error_and_output():
    src = "class SkillError(Exception):\n    def __init__(self, code, m=''):\n        super().__init__(m)\n        self.code = code\ndef run(x):\n    if x < 0:\n        raise SkillError('neg')\n    return {'y': x * 2}\n"
    ok = skillsandbox.run(src, "run", {"x": 2})
    assert ok["ok"] and ok["output"] == {"y": 4} and ok["boundary"]["app_container"] is True and ok["boundary"]["capability_count"] == 0
    assert skillsandbox.run(src, "run", {"x": -1})["error"] == "neg"
    assert skillsandbox.run("def run():\n    1/0\n", "run", {})["error"] == "crashed"


# ---------------------------------------------------------------- host judge: independent verification is enforced

def test_correct_example_passes_all_independent_checks():
    acc = skilljudge.accept(skillpkg.load(skillpkg.read_files(EXAMPLE_PKG)))
    assert acc["requirements"] == [] and acc["failures"] == [] and acc["passed"] == acc["total"] >= 12


def test_buggy_skill_fails_invariants_even_with_an_always_pass_package_judge(tmp_path, base):
    r = approve(skillinstall.Installer(base), build(tmp_path, procedure=BUGGY, judge="def judge(params, output):\n    return []\n"))
    assert not r["ok"] and r["stage"] == "verify" and r["acceptance"]["failures"]
    assert skillinstall.Installer(base).list() == []


def test_package_judge_can_only_veto_never_approve(tmp_path, base):
    r = approve(skillinstall.Installer(base), build(tmp_path, judge="def judge(params, output):\n    return ['I never like anything']\n"))
    assert not r["ok"] and any("package judge vetoed" in f for f in r["acceptance"]["failures"])


def test_wrong_expectations_in_cases_are_caught(tmp_path, base):
    def bad_cases(d):
        rows = json.loads((d / "cases" / "train.json").read_text(encoding="utf-8"))
        rows[0]["expect"]["output"]["total_cents"] = 1
        (d / "cases" / "train.json").write_text(json.dumps(rows), encoding="utf-8")
    r = approve(skillinstall.Installer(base), build(tmp_path, mutate=bad_cases))
    assert not r["ok"] and r["stage"] == "verify"


def test_host_fuzz_finds_crashes_the_authors_cases_missed(tmp_path, base):
    crashy = PROC.replace("base, extra = divmod(cents, people)", "base, extra = divmod(cents, people)\n    if people == 1000:\n        base = 1 // (people - 1000)")

    def drop_1000(d):
        for split in ("train", "dev", "holdout"):
            rows = [r for r in json.loads((d / "cases" / f"{split}.json").read_text(encoding="utf-8")) if r.get("params", {}).get("people") != 1000]
            if split == "holdout":
                rows += [{"id": f"x{i}", "params": {"total": str(10 + i), "tip_percent": "0", "people": 2}, "expect": {"output": {}}} for i in range(4)]
            (d / "cases" / f"{split}.json").write_text(json.dumps(rows), encoding="utf-8")
    r = approve(skillinstall.Installer(base), build(tmp_path, procedure=crashy, mutate=drop_1000))
    assert not r["ok"] and any(f.startswith("fuzz:") and "unnamed failure" in f for f in r["acceptance"]["failures"])


def test_too_few_cases_is_refused(tmp_path, base):
    def thin(d):
        for split in ("train", "dev", "holdout"):                       # keep only 3 rows per file: fewer than the 12 execution cases required
            rows = json.loads((d / "cases" / f"{split}.json").read_text(encoding="utf-8"))
            (d / "cases" / f"{split}.json").write_text(json.dumps(rows[:3]), encoding="utf-8")
    r = approve(skillinstall.Installer(base), build(tmp_path, mutate=thin))
    assert not r["ok"] and r["stage"] == "verify" and r["acceptance"]["requirements"]


def test_numeric_parameters_must_declare_ranges(tmp_path, base):
    r = approve(skillinstall.Installer(base), build(tmp_path, manifest=lambda m: m["params"]["total"].pop("max")))
    assert not r["ok"] and r["stage"] == "review" and any("min and max" in x for x in r["report"]["problems"])


@pytest.mark.parametrize("expr", ["__import__('os').system('echo x')", "output.__class__", "open('x')", "[c for c in ().__class__.__mro__]", "9 ** 9 ** 9"])
def test_invariant_expressions_cannot_run_code(expr):
    bad = skilljudge.check_invariants([{"name": "evil", "expr": expr}], {}, {})
    assert bad and "could not be evaluated" in bad[0]


# ---------------------------------------------------------------- installer lifecycle

def tree_hash(root):
    return sorted((str(p.relative_to(root)), p.read_bytes()) for p in Path(root).rglob("*") if p.is_file() and p.name != "events.jsonl")


def test_install_requires_the_owner_to_approve_the_exact_fingerprint(tmp_path, base):
    ins = skillinstall.Installer(base)
    p = build(tmp_path)
    r = ins.install(p)
    assert not r["ok"] and r["stage"] == "approval" and r["report"]["fingerprint"][:24] in r["message"]
    assert ins.list() == []
    fp = r["report"]["fingerprint"]
    for wrong in ("0" * 24, fp[:12], fp[:23], fp[::-1][:24]):
        r = ins.install(p, approve=wrong)
        assert not r["ok"] and r["stage"] == "approval" and ins.list() == [], wrong
    assert ins.install(p, approve=fp[:24])["ok"]


def test_valid_install_registers_in_the_existing_procedure_library(tmp_path, base):
    ins = skillinstall.Installer(base)
    r = approve(ins, build(tmp_path))
    assert r["ok"] and r["acceptance"]["passed"] == r["acceptance"]["total"]
    man, _ = ins.lib.get("util.split_bill")
    assert man["evidence"]["judge"] == skilljudge.JUDGE_HASH and man["pkg_version"] == "1.1.0" and man["skill"]["permissions"] == ["compute"]
    assert (base / "archive" / "util.split_bill" / "1.jskill").exists() and ins.verify_installed("util.split_bill")
    assert "install" in [e["event"] for e in ins.events()]


@pytest.mark.serial   # shares the installed-skills folder with other skill tests
def test_failed_install_leaves_existing_capabilities_untouched(tmp_path, base):
    ins = skillinstall.Installer(base)
    assert approve(ins, build(tmp_path, "v1"))["ok"]
    before = tree_hash(base / "library") + tree_hash(base / "archive")
    for bad in (build(tmp_path, "bug", procedure=BUGGY, version="1.2.0"),
                build(tmp_path, "perm", manifest=lambda m: m["permissions"].append("network"), version="1.3.0"),
                build(tmp_path, "old", version="1.0.0")):
        assert not approve(ins, bad)["ok"]
        assert tree_hash(base / "library") + tree_hash(base / "archive") == before
    assert ins.installed("util.split_bill")["pkg_version"] == "1.1.0"
    assert any(e["event"] == "failure" for e in ins.events())


def test_registration_crash_rolls_back_partial_version(tmp_path, base, monkeypatch):
    ins = skillinstall.Installer(base)
    assert approve(ins, build(tmp_path, "v1"))["ok"]
    before = tree_hash(base / "library") + tree_hash(base / "archive")
    monkeypatch.setattr(ins.lib, "set_active", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    r = approve(ins, build(tmp_path, "v2", version="1.2.0"))
    assert not r["ok"] and r["stage"] == "register"
    assert tree_hash(base / "library") + tree_hash(base / "archive") == before


def test_update_disable_enable_and_rollback(tmp_path, base):
    ins = skillinstall.Installer(base)
    assert approve(ins, build(tmp_path, "v1"))["ok"]
    v2 = build(tmp_path, "v2", version="1.2.0", manifest=lambda m: m.update(reply="V2: ${total_with_tip} / {people_text}: {share_text}."))
    assert approve(ins, v2)["ok"] and ins.installed("util.split_bill")["pkg_version"] == "1.2.0"
    assert skillhost.run("split the $100 bill among 2 people").startswith("V2:")
    assert ins.rollback("util.split_bill") == 1
    assert ins.installed("util.split_bill")["pkg_version"] == "1.1.0"
    assert skillhost.run("split the $100 bill among 2 people").startswith("Split $100.00")
    with pytest.raises(ValueError):
        ins.rollback("util.split_bill")
    ins.disable("util.split_bill")
    assert skillhost.plan("split the $100 bill among 2 people")["kind"] == "none"
    ins.enable("util.split_bill")
    assert skillhost.plan("split the $100 bill among 2 people")["kind"] == "run"
    assert {"install", "update", "rollback", "disable", "enable"} <= {e["event"] for e in ins.events()}


def test_reinstalling_the_same_version_is_refused(tmp_path, base):
    ins = skillinstall.Installer(base)
    p = build(tmp_path)
    assert approve(ins, p)["ok"]
    assert not approve(ins, p)["ok"]


# ---------------------------------------------------------------- chat discovery and execution

@pytest.fixture
def installed(tmp_path, base):
    assert approve(skillinstall.Installer(base), build(tmp_path))["ok"]
    return base


def test_chat_front_door_routes_a_matching_request(installed):
    name, args = craftintent.match("jarvis, split a $87.50 dinner bill 3 ways with 18% tip")
    assert name == "run_installed_skill"
    out = skillhost.run(args["request"])
    assert "$103.25" in out and "2 people pay $34.42 and 1 person pays $34.41" in out and "Checked 5 independent rules" in out


def test_missing_essentials_are_asked_not_invented(installed):
    assert skillhost.run("split the bill for $60") == "How many people are splitting it?"
    assert skillhost.run("split the check between 4 people") == "How much is the bill, in dollars?"
    assert skillhost.run("split this three ways") == "How much is the bill, in dollars?"


def test_ordinary_spoken_requests_work(installed):
    assert "each person pays $50.00" in skillhost.run("split the $100 bill two ways")
    assert skillhost.plan("divide the dinner bill evenly between four of us")["kind"] == "ask"
    assert craftintent.match("split this three ways")[0] == "run_installed_skill"


def test_named_error_gets_a_plain_refusal(installed):
    assert "nothing to split" in skillhost.run("split a $0 bill 3 ways")


def test_invalid_precision_is_explained_not_guessed(installed):
    assert "at most 2 decimal places" in skillhost.run("split the $10.005 bill two ways")


@pytest.mark.parametrize("text", ["split the screen into two windows", "split this pdf into pages", "divide 10 by 3", "what is my electric bill", "check my email",
                                  "split the bill with my git commit history", "rig a two bone tube and split the bill", "split the string by commas",
                                  "split the file three ways", "split the difference two ways"])
def test_unsupported_requests_do_not_activate_the_skill(installed, text):
    assert skillhost.plan(text)["kind"] == "none"
    hit = craftintent.match(text)
    assert hit is None or hit[0] != "run_installed_skill"


def test_out_of_range_values_are_not_run(installed):
    assert skillhost.plan("split the $5000000 bill among 4 people")["kind"] == "reject"
    assert skillhost.plan("split the $50 bill among 5000 people")["kind"] == "reject"


def test_existing_craft_requests_still_win(installed):
    assert craftintent.match("rig my character")[0] == "rig_my_character"
    assert craftintent.match("what have you learned lately")[0] == "what_have_you_learned"


def test_skill_failing_its_runtime_invariant_is_not_announced_as_verified(tmp_path, base, monkeypatch):
    assert approve(skillinstall.Installer(base), build(tmp_path))["ok"]
    monkeypatch.setattr(skillsandbox, "run", lambda *a, **k: {"ok": True, "output": {"total_cents": 1000, "shares_cents": [1, 2], "people": 2, "people_text": "2 people",
                                                                                      "tip_percent": "0", "total_with_tip": "10.00", "share_text": "lies"}})
    out = skillhost.run("split the $10 bill among 2 people")
    assert "failed my own verification" in out and "lies" not in out


def test_bounded_recovery_retries_once_with_changed_parameters(tmp_path, base):
    assert approve(skillinstall.Installer(base), build(tmp_path, manifest=lambda m: m["recovery"].update(not_enough_money={"set": {"people": 1}})))["ok"]
    out = skillhost.run("split the $0.02 bill among 5 people")
    assert out.startswith("Split $0.02") and "each person pays $0.02" in out


def test_every_run_is_logged(installed):
    skillhost.run("split the $10 bill among 2 people")
    ins = skillinstall.Installer(installed)
    assert any(e["event"] == "run" and e["ok"] for e in ins.events())
    assert any(u.get("procedure") == "util.split_bill" for u in ins.lib.uses())


# ---------------------------------------------------------------- dry-run: no side effects, including through the real Brain

def test_dry_run_prefix_parsing():
    assert dryrun.parse("[dry-run] rig my dragon") == ("rig my dragon", True)
    assert dryrun.parse("[phone] dry run: rig my dragon") == ("[phone] rig my dragon", True)
    assert dryrun.parse("rig my dragon") == ("rig my dragon", False)


def test_dry_run_describes_without_executing(installed, monkeypatch):
    from tools import registry
    monkeypatch.setattr(registry, "run", lambda *a, **k: pytest.fail("a tool ran during a dry run"))
    for fn in ("run", "run_many"):
        monkeypatch.setattr(skillsandbox, fn, lambda *a, **k: pytest.fail("a skill ran during a dry run"))
    assert "rig_my_character" in dryrun.describe("rig my dragon") and "nothing was executed" in dryrun.describe("rig my dragon")
    assert "util.split_bill" in dryrun.describe("split the $90 bill among 3 people")
    assert "would first ask" in dryrun.describe("split the bill for $60")
    assert "normal conversation" in dryrun.describe("what's two plus two")


def test_dry_run_env_makes_every_tool_inert(installed, monkeypatch):
    from tools import load_all, registry
    load_all()
    monkeypatch.setenv("JARVIS_DRY_RUN", "1")
    for fn in ("run", "run_many"):
        monkeypatch.setattr(skillsandbox, fn, lambda *a, **k: pytest.fail("a skill ran under JARVIS_DRY_RUN"))
    out, err = registry.run("rig_my_character", {"which": "dragon", "kind": "", "profile": "ue5", "use_saved_version": False})
    assert "NOT executed" in out and not err
    out, err = registry.run("run_installed_skill", {"request": "split the $90 bill among 3 people"})
    assert "NOT executed" in out and not err


def test_dry_run_through_the_real_brain_starts_no_job(installed, monkeypatch):
    """The exact dragon-incident phrase, sent through Brain.ask like the live app does: it must describe, not rig."""
    from brain.llm import Brain
    from core import craftjobs, users
    from tools import load_all, registry
    load_all()
    users.set_current("owner")
    monkeypatch.setattr(registry, "run", lambda *a, **k: pytest.fail("a tool ran during a dry run"))
    monkeypatch.setattr(Brain, "_run", lambda *a, **k: pytest.fail("the model ran during a dry run"))
    before = craftjobs.get()
    b = Brain("x", "low", "sys")
    for phrase, expect in (("[dry-run] rig my dragon", "rig_my_character"), ("[dry-run] split the $90 bill among 3 people", "util.split_bill"),
                           ("[dry-run] what is the capital of France", "normal conversation")):
        said = []
        b.ask(phrase, said.append)
        assert expect in " ".join(said) and "nothing was executed" in " ".join(said), said
    assert craftjobs.get() == before


# ---------------------------------------------------------------- persistence across a real process restart

def test_installed_skill_survives_a_process_restart(tmp_path, base):
    assert approve(skillinstall.Installer(base), build(tmp_path))["ok"]
    code = ("from core import skillhost, craftintent\n"
            "print(craftintent.match('split $120 among 4 people with a 20% tip')[0])\n"
            "print(skillhost.run('split $120 among 4 people with a 20% tip'))\n")
    for _ in range(2):                                                      # two separate fresh interpreters
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "JARVIS_SKILLS_DIR": str(base)}, timeout=120)
        assert p.returncode == 0, p.stderr
        assert p.stdout.splitlines()[0] == "run_installed_skill" and "each person pays $36.00" in p.stdout


def test_cli_end_to_end(tmp_path, base):
    pk = build(tmp_path)
    env = {**os.environ, "JARVIS_SKILLS_DIR": str(base)}
    run = lambda *a: subprocess.run([sys.executable, "manage.py", "skill", *a], cwd=ROOT, capture_output=True, text=True, env=env, timeout=300)
    r = run("review", str(pk))
    assert r.returncode == 0 and "fingerprint" in r.stdout
    fp = skillpkg.load(skillpkg.read_files(pk))["fingerprint"]
    assert run("install", str(pk)).returncode == 1                          # no approval -> nothing installed
    assert "util.split_bill" not in run("list").stdout
    assert run("install", str(pk), "--approve", fp[:24]).returncode == 0
    assert "util.split_bill" in run("list").stdout
    assert run("disable", "util.split_bill").returncode == 0 and "DISABLED" in run("list").stdout
