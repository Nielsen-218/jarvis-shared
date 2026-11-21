"""The brain-facing PC skills: registered, and the approval flow cannot be bypassed by the AI."""
import pytest

from core import approvals, users
from jarvis_pc.brain import Prepared
from tools import ALL, registry
from tools import pcskills
from jarvis_pc.testing import module_guard

setUpModule, tearDownModule = module_guard()  # no real winget/uninstall/delete from tests

TOOLS = ["pc_diagnose", "pc_find_files", "pc_find_duplicates", "pc_backup", "pc_restore_test", "pc_install",
         "pc_uninstall", "pc_cleanup", "pc_purge", "pc_undo", "pc_learned_skill"]


class Stub:
    """Stands in for jarvis_pc.brain.PCSkills; records what actually ran."""

    def __init__(self):
        self.ran = []
        self.blocked = None
        self.winget_flags = []

    def _p(self, name, direct=False):
        if self.blocked:
            return Prepared("", "", self.blocked, lambda: "")
        return Prepared(f"key-{name}", f"plan for {name}", None, lambda: self.ran.append(name) or f"ran {name}", allows_direct=direct)

    def prepare_install(self, source): return self._p("install:" + source)
    def prepare_uninstall(self, target, winget=True):
        self.winget_flags.append(winget)
        return self._p("uninstall:" + target, direct=True)
    def prepare_cleanup(self): return self._p("cleanup", direct=True)
    def prepare_purge(self): return self._p("purge")
    def prepare_undo(self): return self._p("undo")
    def solve(self, task, teacher, budget, params=None): return f"solve {task} teacher={teacher is not None} budget={budget} params={params}"
    def diagnose(self, symptom, everything): return f"diag {symptom} {everything}"


@pytest.fixture
def stub(monkeypatch):
    s = Stub()
    monkeypatch.setattr(pcskills, "_skills", s)
    users.set_current("owner")
    approvals._pending.clear()
    approvals._direct.clear()
    yield s
    approvals._pending.clear()
    approvals._direct.clear()


def test_every_pc_skill_is_registered_and_desk_only():
    registry_names = set(registry.names())
    from tools import load_all, DESK_ONLY
    load_all()
    assert "pcskills" in ALL and "pcskills" in DESK_ONLY  # needs this PC (installs, files); not for the laptop server
    assert set(TOOLS) <= set(registry.names()), set(TOOLS) - set(registry.names())
    assert registry_names is not None


def test_install_asks_first_and_the_ai_cannot_self_approve(stub):
    out = pcskills.pc_install("https://github.com/o/tool", False)
    assert out.startswith("NEEDS_CONFIRMATION") and "plan for install" in out
    assert stub.ran == []
    # the model sets confirmed=true on its own, but the person never said yes:
    assert pcskills.pc_install("https://github.com/o/tool", True).startswith(("NEEDS_CONFIRMATION", "NOT_ASKED"))
    assert stub.ran == []


def test_a_real_yes_runs_it_once_and_the_approval_is_used_up(stub):
    pcskills.pc_install("https://github.com/o/tool", False)
    approvals.on_user_message("owner", "yes")
    assert pcskills.pc_install("https://github.com/o/tool", True) == "ran install:https://github.com/o/tool"
    assert len(stub.ran) == 1
    assert pcskills.pc_install("https://github.com/o/tool", True).startswith("NEEDS_CONFIRMATION")  # single use
    assert len(stub.ran) == 1


def test_a_yes_for_one_plan_does_not_approve_a_different_one(stub):
    pcskills.pc_install("https://github.com/o/tool", False)
    approvals.on_user_message("owner", "yes")
    assert pcskills.pc_install("https://github.com/o/OTHER", True).startswith(("NEEDS_CONFIRMATION", "NOT_ASKED"))
    assert stub.ran == []


def test_a_no_cancels(stub):
    pcskills.pc_purge(False)
    approvals.on_user_message("owner", "no, cancel")
    assert pcskills.pc_purge(True).startswith("NEEDS_CONFIRMATION")
    assert stub.ran == []


def test_for_the_owner_his_request_is_the_yes_for_uninstall_purge_and_install(stub):
    approvals.on_user_message("owner", "uninstall tool")
    assert pcskills.pc_uninstall("tool", False) == "ran uninstall:tool"
    approvals.on_user_message("owner", "delete everything in quarantine")
    assert pcskills.pc_purge(False) == "ran purge"           # irreversible, but he said it (owner 2026-11-07: no rules for his commands)
    approvals.on_user_message("owner", "install tool")
    assert pcskills.pc_install("https://github.com/o/tool", False) == "ran install:https://github.com/o/tool"
    approvals.on_user_message("owner", "install something else")
    assert pcskills.pc_install("https://github.com/o/other", False).startswith("NEEDS_CONFIRMATION")   # not what he named
    assert stub.ran == ["uninstall:tool", "purge", "install:https://github.com/o/tool"]


def test_blocked_plans_say_why_and_do_not_ask(stub):
    stub.blocked = "I won't install that: trust check says STOP"
    assert pcskills.pc_install("https://github.com/o/tool", False) == "I won't install that: trust check says STOP"
    assert approvals.pending("owner") is None


def test_learned_skill_uses_the_teacher_only_if_one_is_registered(stub):
    pcskills.set_teacher(None)
    assert "teacher=False" in pcskills.pc_learned_skill("check git")
    pcskills.set_teacher(lambda task, ctx: "git --version", 2)
    try:
        assert "teacher=True budget=2" in pcskills.pc_learned_skill("check git")
    finally:
        pcskills.set_teacher(None)


def test_read_only_tools_do_not_ask(stub):
    assert pcskills.pc_diagnose("slow", False) == "diag slow False"
    assert approvals.pending("owner") is None


# ---- coexistence with the already-merged PC control (tools/software.py, the pchybrid front door, brain/llm.py) ----------
def test_pc_install_is_for_github_links_only_and_points_to_the_existing_tool(stub):
    out = pcskills.pc_install("Discord", False)
    assert "install_program" in out
    assert approvals.pending("owner") is None and stub.ran == []


def test_pc_uninstall_only_handles_jarvis_installs_and_says_where_else_to_go(stub):
    pcskills.pc_uninstall("tool", False)
    assert stub.winget_flags == [False]  # winget packages and normal programs belong to uninstall_program
    stub.blocked = "I won't do that: nothing named 'discord' under apps"
    assert pcskills.pc_uninstall("discord", False).endswith("use uninstall_program.")


def test_has_install_sees_jarvis_installs_only_and_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_PC_ROOT", str(tmp_path / "pc"))
    assert pcskills.has_install("ripgrep") is False and not (tmp_path / "pc").exists()
    (tmp_path / "pc" / "apps" / "ripgrep-15.2.0").mkdir(parents=True)
    (tmp_path / "pc" / "apps" / "_downloads").mkdir()
    assert pcskills.has_install("ripgrep") and pcskills.has_install("RipGrep-15.2.0")
    assert not pcskills.has_install("rip") and not pcskills.has_install("_downloads")
    assert not pcskills.has_install("../apps") and not pcskills.has_install("")


def test_the_existing_uninstall_flow_hands_a_jarvis_install_to_the_reversible_flow(stub, monkeypatch):
    from tools import software
    monkeypatch.setattr(software, "find_installed", lambda n: [])
    monkeypatch.setattr(software, "_registry_programs", lambda n: [])
    monkeypatch.setattr(software, "_appx", lambda n, remove=False: "")
    monkeypatch.setattr(pcskills, "has_install", lambda n: n == "ripgrep")
    approvals.on_user_message("owner", "uninstall ripgrep")
    assert software.uninstall_program("ripgrep") == "ran uninstall:ripgrep"  # reversible, so the request itself is the yes
    assert software.uninstall_program("nothing") == "Nothing installed matches 'nothing'."


def test_a_normally_installed_program_never_touches_the_pc_skills(stub, monkeypatch):
    from tools import software
    called = []
    monkeypatch.setattr(software, "find_installed", lambda n: [{"Name": "Discord", "Id": "Discord.Discord"}])
    monkeypatch.setattr(software, "_registry_programs", lambda n: [])
    monkeypatch.setattr(pcskills, "has_install", lambda n: called.append(n) or True)
    out = software.uninstall_program("Discord")
    assert called == [] and out.startswith("NEEDS_CONFIRMATION")  # the existing ask-first flow, untouched


def test_every_slow_pc_tool_has_a_spoken_progress_line():
    from brain import llm
    from tools import load_all
    load_all()
    slow = [n for n in TOOLS if registry.is_slow(n)]
    assert slow
    missing = [n for n in slow if n not in llm._DOING]
    assert not missing, f"no 'I'm ...' line for: {missing}"


def test_dry_run_mode_refuses_every_pc_tool(monkeypatch):
    monkeypatch.setenv("JARVIS_DRY_RUN", "1")  # the repo-wide switch: tools.registry.run refuses to run any tool
    for name in TOOLS:
        result, _ = registry.run(name, {"symptom": "x", "folder": "x", "pattern": "x", "source": "https://github.com/o/r",
                                        "target": "x", "backup_path": "x", "task": "x"})
        assert "dry" in str(result).lower() or "not run" in str(result).lower() or "refus" in str(result).lower(), (name, result)



def test_the_owners_own_command_runs_the_two_phase_skills_at_once(stub):
    """Owner 2026-11-07: "when I say do something there are no rules" — his command is the approval."""
    approvals.on_user_message("owner", "install https://github.com/BurntSushi/ripgrep")
    assert pcskills.pc_install("https://github.com/BurntSushi/ripgrep") == "ran install:https://github.com/BurntSushi/ripgrep"
    approvals.on_user_message("owner", "empty the quarantine")
    assert "ran purge" in pcskills.pc_purge()
    approvals.on_user_message("owner", "undo that")
    assert "ran undo" in pcskills.pc_undo()
    approvals._direct.clear()
    approvals.on_user_message("owner", "what's the weather")                    # he never asked: it only shows the plan
    assert "ran" not in pcskills.pc_install("https://github.com/x/tool") and stub.ran == [
        "install:https://github.com/BurntSushi/ripgrep", "purge", "undo"]


def test_someone_else_in_the_house_still_gets_the_plan_first(stub, monkeypatch):
    from core import authz
    monkeypatch.setattr(authz, "is_owner", lambda uid=None: False)
    approvals.on_user_message("owner", "install https://github.com/BurntSushi/ripgrep")
    assert "ran" not in pcskills.pc_install("https://github.com/BurntSushi/ripgrep") and stub.ran == []
    approvals.on_user_message("owner", "uninstall vlc")                        # reversible-by-design: unchanged for anyone
    assert pcskills.pc_uninstall("vlc") == "ran uninstall:vlc"
