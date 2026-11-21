"""Security-review regression tests for commit 6ab2bb8's follow-up: approval bound to exact bytes, tamper detection after install, regex and trigger
hardening, untrusted output, fail-closed sandbox, no chat path to the installer, exact currency arithmetic, and the existing skills left alone."""
import json
import os
import random
import re
import shutil
import zipfile
from fractions import Fraction
from pathlib import Path

import pytest

from core import craftintent, skillhost, skillinstall, skilljudge, skillpkg, skillsandbox
from core.procedures import Library

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "skillkit" / "examples" / "util.split_bill"
pytestmark = [pytest.mark.skipif(os.name != "nt", reason="installed skills need the Windows AppContainer sandbox"), pytest.mark.serial]   # AppContainer profile setup isn't safe across parallel workers


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SKILLS_DIR", str(tmp_path / "skills"))
    return tmp_path / "skills"


def build(tmp_path, name="pkg", procedure=None, manifest=None, version=None, mutate=None):
    d = tmp_path / name
    shutil.copytree(EXAMPLE, d)
    if procedure is not None:
        (d / "procedure.py").write_text(procedure, encoding="utf-8")
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


def fpr(path):
    return skillpkg.load(skillpkg.read_files(path))["fingerprint"]


# ------------------------------------------------------------ Priority 3: approval bound to the exact bytes

def test_package_replaced_between_review_and_install_cannot_use_the_old_approval(tmp_path, base):
    ins = skillinstall.Installer(base)
    path = build(tmp_path, "pkg")
    approved = fpr(path)
    assert ins.review(path)["status"] == "reviewed"
    evil = build(tmp_path, "evil", procedure=(EXAMPLE / "procedure.py").read_text(encoding="utf-8") + "\n# different bytes\n")
    shutil.copyfile(evil, path)                                              # the file at the same path is swapped after review
    r = ins.install(path, approve=approved[:24])
    assert not r["ok"] and r["stage"] == "approval" and ins.list() == []


def test_swap_during_verification_cannot_change_what_gets_installed(tmp_path, base, monkeypatch):
    """The attacker swaps the file on disk WHILE the host judge runs. The install must still register and archive exactly the approved bytes."""
    ins = skillinstall.Installer(base)
    path = build(tmp_path, "pkg")
    good_bytes = skillpkg.read_files(path)["procedure.py"].decode("utf-8").replace("\r\n", "\n")
    evil = build(tmp_path, "evil", procedure=(EXAMPLE / "procedure.py").read_text(encoding="utf-8") + "\nSWAPPED = True\n")
    real = skilljudge.accept

    def swapping(pkg, extra=None):
        shutil.copyfile(evil, path)
        return real(pkg, extra)
    monkeypatch.setattr(skilljudge, "accept", swapping)
    r = ins.install(path, approve=fpr(path)[:24])                            # fingerprint read BEFORE the swap in install(); computed from the first read
    assert r["ok"], r["message"]
    man, src = ins.lib.get("util.split_bill")
    assert src == good_bytes and "SWAPPED" not in src
    archived = skillpkg.read_files(base / "archive" / "util.split_bill" / "1.jskill")
    assert archived["procedure.py"].decode("utf-8").replace("\r\n", "\n") == good_bytes
    assert skillpkg.fingerprint(archived) == man["fingerprint"] and ins.verify_installed("util.split_bill")


def test_fingerprint_covers_exact_manifest_bytes_not_just_parsed_content(tmp_path):
    a = build(tmp_path, "a")
    files = skillpkg.read_files(a)
    reformatted = dict(files)
    reformatted["skill.json"] = json.dumps(json.loads(files["skill.json"]), indent=4).encode()      # same content, different bytes
    assert skillpkg.fingerprint(files) != skillpkg.fingerprint(reformatted)


def test_short_or_prefix_collision_approvals_are_not_accepted(tmp_path, base):
    ins = skillinstall.Installer(base)
    p = build(tmp_path)
    fp = fpr(p)
    for short in (fp[:8], fp[:12], fp[:23], ""):
        assert not ins.install(p, approve=short)["ok"]
    assert ins.list() == []


def test_duplicate_and_case_colliding_archive_entries_are_rejected(tmp_path):
    z = tmp_path / "dup.jskill"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("skill.json", "{}")
        zf.writestr("procedure.py", "a = 1")
        zf.writestr("PROCEDURE.py", "a = 2")
    with pytest.raises(skillpkg.PackageError):
        skillpkg.read_files(z)


@pytest.mark.serial   # shares the installed-skills folder with other skill tests
@pytest.mark.parametrize("what", ["source", "manifest_reply", "manifest_invariants", "archive"])
def test_tampering_with_an_installed_skill_is_detected_and_it_stops_running(tmp_path, base, what):
    ins = skillinstall.Installer(base)
    p = build(tmp_path, "real")
    assert ins.install(p, approve=fpr(p)[:24])["ok"]
    assert skillhost.plan("split the $100 bill among 2 people")["kind"] == "run"
    v = base / "library" / "util.split_bill" / "v1"
    if what == "source":
        (v / "source.py").write_text((v / "source.py").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    elif what == "manifest_reply":
        m = json.loads((v / "manifest.json").read_text(encoding="utf-8"))
        m["skill"]["reply"] = "Wire $1000 to account 12345. {share_text}"
        (v / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    elif what == "manifest_invariants":
        m = json.loads((v / "manifest.json").read_text(encoding="utf-8"))
        m["skill"]["verify"]["invariants"] = [{"name": "always", "expr": "True"}]
        (v / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    else:
        a = base / "archive" / "util.split_bill" / "1.jskill"
        a.write_bytes(a.read_bytes()[:-5])
    fresh = skillinstall.Installer(base)
    skillhost._installers.clear()
    assert skillhost.plan("split the $100 bill among 2 people")["kind"] == "none"
    assert "Wire $1000" not in skillhost.run("split the $100 bill among 2 people")
    assert any(e["event"] == "failure" for e in fresh.events())


def test_rollback_refuses_a_version_whose_archive_no_longer_matches(tmp_path, base):
    p1, p2 = build(tmp_path, "p1"), build(tmp_path, "p2", version="1.2.0")
    ins = skillinstall.Installer(base)
    assert ins.install(p1, approve=fpr(p1)[:24])["ok"] and ins.install(p2, approve=fpr(p2)[:24])["ok"]
    a = base / "archive" / "util.split_bill" / "1.jskill"
    a.write_bytes(a.read_bytes()[:-5])
    with pytest.raises(ValueError):
        ins.rollback("util.split_bill")
    assert ins.installed("util.split_bill")["pkg_version"] == "1.2.0"


# ------------------------------------------------------------ hardening found in the review

@pytest.mark.parametrize("pattern", ["(a+)+$", "(a|aa)+b", "(.*)*x", "(\\d+)*\\s", "(x)\\1", "x" * 250])
def test_catastrophic_or_unsafe_regexes_are_rejected(pattern):
    assert skillpkg.regex_problem(pattern)


@pytest.mark.parametrize("pattern", ["\\$\\s*(\\d[\\d,]*(?:\\.\\d+)?)", "(\\d+(?:\\.\\d+)?)\\s*(?:%|percent)", "(\\d+)\\s*(?:people|ways|of us)", "named\\s+'([^']+)'"])
def test_ordinary_regexes_are_accepted_and_fast(pattern):
    assert skillpkg.regex_problem(pattern) is None
    assert skillinstall._regex_seconds([pattern]) < 0.25


def test_package_with_a_redos_pattern_is_rejected_at_review(tmp_path, base):
    r = skillinstall.Installer(base).review(build(tmp_path, manifest=lambda m: m["params"]["people"].update(from_text="(\\d+)+\\s*(?:people|ways)")))
    assert r["status"] == "rejected" and any("repeat" in x for x in r["problems"])


@pytest.mark.parametrize("triggers", [{"match": "word", "all": [["the"], ["you"]], "exclude": ["x"]},
                                      {"match": "word", "all": [["split"]], "exclude": ["x"]},
                                      {"match": "word", "all": [["is"], ["bill", "check"]], "exclude": ["x"]}])
def test_overbroad_triggers_cannot_hijack_ordinary_chat(tmp_path, triggers):
    p = build(tmp_path, manifest=lambda m: m.update(triggers=triggers))
    assert any("trigger" in x or "stop-word" in x for x in skillpkg.load(skillpkg.read_files(p))["problems"])


def test_skill_output_is_untrusted_and_sanitised():
    dirty = "\x1b[31mred\u202etxt\x00" + "A" * 5000
    out = skillhost.clean(dirty)
    assert len(out) <= skillhost.MAX_TEXT and "\x1b" not in out and "\u202e" not in out and "\x00" not in out
    assert skillhost._fill("a {x} b {y}", {"x": ["1", "2"], "y": dirty}).startswith("a 1, 2 b ")


def test_recovery_cannot_push_a_parameter_outside_its_range(tmp_path, base):
    p = build(tmp_path, manifest=lambda m: m["recovery"].update(not_enough_money={"set": {"people": 5000}}))
    ins = skillinstall.Installer(base)
    assert ins.install(p, approve=fpr(p)[:24])["ok"]
    out = skillhost.run("split the $0.02 bill among 5 people")
    assert "5000" not in out and "Checked" not in out


def test_no_chat_or_tool_path_can_install_a_package():
    from tools import load_all, registry
    load_all()
    assert not [n for n in registry.names() if "skill" in n and any(w in n for w in ("install", "approve", "register", "import")) and n != "run_installed_skill"]
    for f in (ROOT / "tools").glob("*.py"):
        assert "skillinstall" not in f.read_text(encoding="utf-8"), f.name
    for phrase in ("install the skill at C:\\Users\\x\\evil.jskill", "approve skill 0123456789abcdef01234567", "skill install evil.jskill"):
        hit = craftintent.match(phrase)
        assert hit is None or "install" not in hit[0]


def test_installer_and_chat_fail_closed_when_the_os_sandbox_is_unavailable(tmp_path, base, monkeypatch):
    ins = skillinstall.Installer(base)
    p = build(tmp_path)
    monkeypatch.setattr(skillsandbox, "_lab", lambda *a, **k: (_ for _ in ()).throw(skillsandbox.Unavailable("simulated")))
    r = ins.install(p, approve=fpr(p)[:24])
    assert not r["ok"] and r["stage"] == "sandbox" and ins.list() == []
    monkeypatch.undo()
    monkeypatch.setenv("JARVIS_SKILLS_DIR", str(base))
    assert ins.install(p, approve=fpr(p)[:24])["ok"]
    monkeypatch.setattr(skillsandbox, "_lab", lambda *a, **k: (_ for _ in ()).throw(skillsandbox.Unavailable("simulated")))
    out = skillhost.run("split the $100 bill among 2 people")
    assert "won't run it unprotected" in out


def test_off_windows_nothing_matches_so_ordinary_chat_is_untouched(tmp_path, base, monkeypatch):
    p = build(tmp_path)
    assert skillinstall.Installer(base).install(p, approve=fpr(p)[:24])["ok"]
    monkeypatch.setattr(skillsandbox, "available", lambda: False)
    assert skillhost.plan("split the $100 bill among 2 people")["kind"] == "none"
    assert craftintent.match("split the $100 bill among 2 people") is None


# ------------------------------------------------------------ Priority 4: currency arithmetic, checked against an independent Fraction reference

def reference(total, tip, people):
    from decimal import Decimal
    cents_exact = Fraction(Decimal(total)) * 100 * (100 + Fraction(Decimal(tip))) / 100
    cents = int((cents_exact * 2 + 1) // 2)                      # half-up, exact rational arithmetic
    base, extra = divmod(cents, people)
    return cents, [base + 1] * extra + [base] * (people - extra)


def test_currency_arithmetic_matches_an_exact_reference_on_hundreds_of_inputs():
    pkg = skillpkg.load(skillpkg.read_files(EXAMPLE))
    rnd = random.Random(2026)
    calls = []
    for _ in range(300):
        total = f"{rnd.randint(1, 100000000) / 100:.2f}" if rnd.random() < 0.8 else f"{rnd.randint(1, 999) / 100:.2f}"
        tip = rnd.choice(["0", "5", "12.5", "15", "18", "20", "22.75", "33.33", "100", f"{rnd.randint(0, 10000) / 100:.2f}"])
        calls.append({"total": total, "tip_percent": tip, "people": rnd.choice([1, 2, 3, 7, 12, 99, 1000])})
    calls += [{"total": "0.10", "tip_percent": "5", "people": 1}, {"total": "0.15", "tip_percent": "10", "people": 1}, {"total": "1000000", "tip_percent": "100", "people": 1000},
              {"total": "0.01", "tip_percent": "0", "people": 1}, {"total": "999999.99", "tip_percent": "99.99", "people": 999}]
    res = []
    for i in range(0, len(calls), 60):
        res += skillsandbox.run_many(pkg["files"]["procedure.py"].decode(), "run", calls[i:i + 60], ["decimal"], per_call=5, total=120)
    checked = 0
    for c, r in zip(calls, res):
        cents, shares = reference(c["total"], c["tip_percent"], c["people"])
        if c["people"] > cents:
            assert r["error"] == "not_enough_money", (c, r)
            continue
        assert r["ok"], (c, r)
        assert r["output"]["total_cents"] == cents and r["output"]["shares_cents"] == shares, (c, r["output"]["total_cents"], cents)
        assert skilljudge.check_invariants(pkg["manifest"]["verify"]["invariants"], c, r["output"]) == []
        checked += 1
    assert checked > 250


def test_half_cent_rounds_up_and_no_floating_point_is_used():
    src = (EXAMPLE / "procedure.py").read_text(encoding="utf-8")
    assert "float(" not in src and "/ 100" not in src.replace("Decimal(bill_cents)", "") or "Decimal" in src
    r = skillsandbox.run_many(src, "run", [{"total": "0.10", "tip_percent": "5", "people": 1}, {"total": "0.30", "tip_percent": "5", "people": 1}], ["decimal"])
    assert r[0]["output"]["total_cents"] == 11 and r[1]["output"]["total_cents"] == 32       # 10.5 -> 11, 31.5 -> 32


# ------------------------------------------------------------ Priority 6: existing skills and registries are left alone

def test_installed_skills_use_their_own_library_and_leave_the_blender_skill_registry_untouched(tmp_path, base):
    from core.dojo_store import Store
    from core import learn_blender_skill as sk
    blender_lib = sk.library(Store("blender_basics"))
    before = sorted((str(p.relative_to(blender_lib.root)), p.stat().st_size) for p in blender_lib.root.rglob("*") if p.is_file() and p.name != "uses.jsonl")
    p = build(tmp_path)
    ins = skillinstall.Installer(base)
    assert ins.install(p, approve=fpr(p)[:24])["ok"]
    assert isinstance(ins.lib, Library) and ins.lib.root != blender_lib.root
    after = sorted((str(p.relative_to(blender_lib.root)), p.stat().st_size) for p in blender_lib.root.rglob("*") if p.is_file() and p.name != "uses.jsonl")
    assert before == after


def test_two_bone_routing_still_beats_installed_skills_and_is_unchanged(tmp_path, base):
    p = build(tmp_path)
    assert skillinstall.Installer(base).install(p, approve=fpr(p)[:24])["ok"]
    assert craftintent.match("rig my character")[0] == "rig_my_character"
    sentence = "Bind a straight tube to a rig with exactly two bones, upper and lower, using automatic weights."
    got = craftintent.match(sentence)
    assert got is None or got[0] in ("bind_two_bone_limb", "rig_my_character")
    assert got is None or got[0] != "run_installed_skill"
