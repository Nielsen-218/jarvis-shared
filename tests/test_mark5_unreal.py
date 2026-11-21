"""Mark V (2026-11-09): Unreal Engine provider. Read-only: never starts Unreal, never writes into a game project; a headless
job only on a throwaway project and never in a game, beside an open editor, or with 3+ Unreal processes; characters graded
against Living Shadow World's asset rules (no gate passes just because Unreal imported it)."""
import json
import os

import pytest

from core import unreal


def test_a_utf16_uproject_with_a_bom_is_read(tmp_path):
    # Living Shadow World's .uproject was re-saved as UTF-16 by a plugin (found 2026-11-09)
    (tmp_path / "Game.uproject").write_bytes(json.dumps({"EngineAssociation": "5.8", "Plugins": [
        {"Name": "PythonScriptPlugin", "Enabled": True}, {"Name": "ModelContextProtocol", "Enabled": True},
        {"Name": "Off", "Enabled": False}]}).encode("utf-16"))
    p = unreal.project(tmp_path)
    assert p["engine"] == "5.8" and p["mcp_enabled"] and p["python_enabled"] and "Off" not in p["plugins"]
    (tmp_path / "Game.uproject").write_text(json.dumps({"EngineAssociation": "5.7", "Plugins": []}), encoding="utf-8")
    assert unreal.project(tmp_path)["engine"] == "5.7" and not unreal.project(tmp_path)["mcp_enabled"]
    assert unreal.project(tmp_path / "nothing") is None


def test_engines_come_from_build_version(tmp_path, monkeypatch):
    root = tmp_path / "UE_5.8"
    (root / "Engine" / "Build").mkdir(parents=True)
    (root / "Engine" / "Build" / "Build.version").write_text(json.dumps({"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 4}))
    monkeypatch.setattr(unreal, "ENGINE_ROOTS", (str(tmp_path),))
    monkeypatch.delenv("JARVIS_UE_ROOT", raising=False)
    assert [e["version"] for e in unreal.engines()] == ["5.8.4"]


LSW = os.path.abspath(str(unreal.LSW))


@pytest.mark.parametrize("project, procs, gaming, ok, why", [
    (os.path.join(LSW, "LivingShadowWorld.uproject"), [], False, False, "Living Shadow World working copy"),
    (r"C:\Temp\jarvis_ue_gate\Gate.uproject", [], True, False, "a game is running"),
    (r"C:\Temp\jarvis_ue_gate\Gate.uproject", [{"pid": 1, "exe": "unrealeditor.exe", "project": r"C:\Temp\jarvis_ue_gate\Gate.uproject"}],
     False, False, "has that project open"),
    (r"C:\Temp\jarvis_ue_gate\Gate.uproject", [{"pid": i, "exe": "unrealeditor-cmd.exe", "project": r"C:\x\Other.uproject"} for i in range(3)],
     False, False, "3 Unreal processes"),
    (r"C:\Temp\jarvis_ue_gate\Gate.uproject", [{"pid": 0, "exe": "unknown", "project": ""}], False, False, "couldn't check"),
    (r"C:\Temp\jarvis_ue_gate\Gate.uproject", [{"pid": 1, "exe": "unrealeditor.exe", "project": os.path.join(LSW, "LivingShadowWorld.uproject")}],
     False, True, ""),
])
def test_the_headless_guard(monkeypatch, project, procs, gaming, ok, why):
    from core import warmth
    monkeypatch.setattr(warmth, "gaming", lambda: gaming)
    got, reason = unreal.may_run_headless(project, procs)
    assert got is ok and why in reason


def test_the_rig_check_respects_the_guard(monkeypatch, tmp_path):
    from tools import rigging
    from core import unreal as ue
    (tmp_path / "char_rigged.fbx").write_bytes(b"fbx")
    monkeypatch.delenv("JARVIS_SKIP_UNREAL", raising=False)
    monkeypatch.setattr(ue, "may_run_headless", lambda project: (False, "a game is running"))
    gate = rigging._unreal_gate
    # ue_cmd() must exist for the guard to be reached; point it at a fake engine
    monkeypatch.setenv("JARVIS_UE_ROOT", str(tmp_path / "UE"))
    exe = tmp_path / "UE" / "Engine" / "Binaries" / "Win64"
    exe.mkdir(parents=True)
    (exe / "UnrealEditor-Cmd.exe").write_bytes(b"")
    monkeypatch.setenv("JARVIS_UE_GATE_PROJECT", str(tmp_path / "gate"))
    out = gate(tmp_path, {})
    assert out == {"ok": None, "skipped": "a game is running"}
    assert "didn't run the Unreal import check because a game is running" in rigging.unreal_sentence({"unreal_gate": out})


# ----------------------------------------------------------------------------------------------- the MCP client
class FakeResp:
    def __init__(self, body, sid="s1", sse=False):
        self.body = ("event: message\ndata: " + json.dumps(body) + "\n\n") if sse else json.dumps(body)
        self.headers = {"Mcp-Session-Id": sid}

    def read(self):
        return self.body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_mcp_tools_lists_and_never_calls_a_tool(monkeypatch):
    sent = []

    def urlopen(req, timeout=0):
        body = json.loads(req.data)
        sent.append((body.get("method"), req.headers.get("Origin"), req.headers.get("Mcp-session-id")))
        if body.get("method") == "initialize":
            return FakeResp({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "Unreal"}}})
        if body.get("method") == "tools/list":
            return FakeResp({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "list_toolsets"}, {"name": "call_tool"}]}}, sse=True)
        return FakeResp({})
    monkeypatch.setattr(unreal.urllib.request, "urlopen", urlopen)
    assert unreal.mcp_tools() == ["list_toolsets", "call_tool"]
    assert [m for m, _, _ in sent] == ["initialize", "notifications/initialized", "tools/list"]   # nothing is ever called
    assert all(o == "http://127.0.0.1" for _, o, _ in sent) and sent[-1][2] == "s1"


def test_mcp_not_running_is_none(monkeypatch):
    def refused(req, timeout=0):
        raise ConnectionRefusedError()
    monkeypatch.setattr(unreal.urllib.request, "urlopen", refused)
    assert unreal.mcp_tools() is None


def test_status_sentence():
    s = {"engines": [{"version": "5.8.4"}], "lsw": {"name": "LivingShadowWorld", "mcp_enabled": True}, "processes": [
        {"pid": 1, "exe": "unrealeditor-cmd.exe", "project": "x"}], "lsw_open": False, "mcp_running": False, "mcp_tools": []}
    out = unreal.say_status(s)
    assert out.startswith("Unreal 5.8.4 is installed. LivingShadowWorld is not open.") and "1 background Unreal job running" in out
    s.update(lsw_open=True, mcp_running=True, mcp_tools=["a", "b"], processes=[])
    assert "open in the editor" in unreal.say_status(s) and "answering with 2 tools" in unreal.say_status(s)
    assert unreal.say_status({"engines": []}) == "I can't find Unreal Engine on this PC."


# ----------------------------------------------------------------------------------------------- grading
def _char(tmp_path, *, verts=40000, ue_ok=True, poses_ok=True, prov=None):
    d = tmp_path / "Hero-20261109-120000"
    d.mkdir()
    (d / "report.json").write_text(json.dumps({
        "unreal_gate": {"ok": ue_ok, "verts": verts, "bones": 33, "error": None if ue_ok else "two roots"},
        "bone_name_map": {b.replace("_l", ".L").replace("_r", ".R"): b for b in unreal.MANNEQUIN_CORE},
        "pose_tests": [{"test": "shoulder.L", "pass": poses_ok}], "ik_tests": [{"control": "ik_hand.L", "pass": True}]}))
    if prov is not None:
        (d / "provenance.json").write_text(json.dumps(prov))
    return d


def test_a_good_character_is_still_not_ready_without_the_in_game_check(tmp_path):
    g = unreal.grade_character(_char(tmp_path, prov={"generator": "the owner's own sculpt", "license": "owner's own work"}))
    assert {k: v["state"] for k, v in g["gates"].items()} == {
        "unreal_import": "pass", "mannequin_skeleton": "pass", "deforms": "pass", "optimisation": "pass", "provenance": "pass",
        "in_game": "not_checked"}
    assert g["ready"] is False and "isn't ready for Shadow World yet" in unreal.say_grade(g)


def test_failures_are_named(tmp_path):
    g = unreal.grade_character(_char(tmp_path, verts=86849, ue_ok=False, poses_ok=False,
                                     prov={"license": "Tencent Hunyuan 3D 2.0 Community License", "restrictions": ["territory"]}))
    states = {k: v["state"] for k, v in g["gates"].items()}
    assert states["unreal_import"] == states["deforms"] == states["optimisation"] == states["provenance"] == "fail"
    said = unreal.say_grade(g)
    assert "86,849 vertices" in said and "two roots" in said


def test_no_provenance_fails_and_no_report_is_said(tmp_path):
    assert unreal.grade_character(_char(tmp_path))["gates"]["provenance"]["state"] == "fail"
    empty = tmp_path / "empty"
    empty.mkdir()
    g = unreal.grade_character(empty)
    assert not g["ready"] and g["gates"]["report"]["state"] == "fail"


def test_the_tools_are_registered_read_only_and_plannable():
    from core import authz, capabilities, pcactions
    import tools
    assert "unreal" in tools.ALL
    assert pcactions.SCHEMAS["unreal_status"] == ({}, False) and pcactions.SCHEMAS["shadow_world_check"] == ({"which": str}, False)
    assert authz.effect({"tool": "unreal_status", "arguments": {}}) == authz.OBSERVE
    assert authz.effect({"tool": "shadow_world_check", "arguments": {"which": ""}}) == authz.OBSERVE
    assert capabilities.PROVIDERS["unreal"]["status"] == "available"


def test_shadow_world_check_picks_by_name(monkeypatch, tmp_path):
    from tools import unreal as U
    a, b = tmp_path / "Vanguard-20261109-1", tmp_path / "Barefoot_Man-20261109-2"
    for d in (a, b):
        d.mkdir()
        (d / "report.json").write_text("{}")
    monkeypatch.setattr(U, "_rigged_folders", lambda: [b, a])
    assert U._pick("")[0] == b and U._pick("vanguard")[0] == a
    assert U._pick("dragon") == (None, "I couldn't find a rigged character called dragon.")
    monkeypatch.setattr(U, "_rigged_folders", lambda: [])
    assert "no rigged character" in U.shadow_world_check()



def test_a_skipped_unreal_check_is_not_checked_never_a_failure(tmp_path):
    d = _char(tmp_path, prov={"license": "owner's own work"})
    rep = json.loads((d / "report.json").read_text())
    rep["unreal_gate"] = {"ok": None, "skipped": "a game is running"}
    (d / "report.json").write_text(json.dumps(rep))
    g = unreal.grade_character(d)["gates"]
    assert g["unreal_import"] == {"state": "not_checked", "why": "the Unreal check was skipped because a game is running"}
    assert "in Blender" in g["optimisation"]["why"] or g["optimisation"]["state"] == "not_checked"


def test_the_skeleton_gate_checks_the_real_bone_names(tmp_path):
    d = _char(tmp_path, prov={"license": "own"})
    rep = json.loads((d / "report.json").read_text())
    rep["bone_name_map"] = {"upperarm.L": "upperarm_l"}                      # a partial map is not a mannequin skeleton
    (d / "report.json").write_text(json.dumps(rep))
    gate = unreal.grade_character(d)["gates"]["mannequin_skeleton"]
    assert gate["state"] == "fail" and "pelvis" in gate["why"]


def test_blender_vertex_counts_are_not_called_unreal(tmp_path):
    d = tmp_path / "C-20261109-1"
    d.mkdir()
    (d / "report.json").write_text(json.dumps({"reimport": {"verts_reimported": 70000}, "pose_tests": []}))
    why = unreal.grade_character(d)["gates"]["optimisation"]["why"]
    assert "70,000 vertices in Blender" in why


def test_provenance_needs_a_licence(tmp_path):
    assert unreal.grade_character(_char(tmp_path, prov={"generator": "x"}))["gates"]["provenance"]["state"] == "fail"


def test_the_guard_fails_closed_when_it_cannot_tell_about_games(monkeypatch):
    from core import warmth

    def broken():
        raise OSError("no")
    monkeypatch.setattr(warmth, "gaming", broken)
    assert unreal.may_run_headless(r"C:\Temp\g\Gate.uproject", []) == (False, "I couldn't check whether a game is running")


def test_an_editor_started_with_project_equals_is_seen(monkeypatch):
    import psutil
    from types import SimpleNamespace
    proc = SimpleNamespace(info={"pid": 7, "name": "UnrealEditor.exe",
                                 "cmdline": ["UnrealEditor.exe", '-Project="C:\\Games\\Hero\\Hero.uproject"', "-game"]})
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [proc])
    procs = unreal.unreal_processes()
    assert procs[0]["project"].endswith("Hero.uproject") and unreal.editor_has(r"C:\Games\Hero\Hero.uproject", procs)


def test_the_rig_check_never_creates_the_gate_project_before_the_guard(monkeypatch, tmp_path):
    from tools import rigging
    from core import unreal as ue
    (tmp_path / "char_rigged.fbx").write_bytes(b"fbx")
    monkeypatch.delenv("JARVIS_SKIP_UNREAL", raising=False)
    exe = tmp_path / "UE" / "Engine" / "Binaries" / "Win64"
    exe.mkdir(parents=True)
    (exe / "UnrealEditor-Cmd.exe").write_bytes(b"")
    monkeypatch.setenv("JARVIS_UE_ROOT", str(tmp_path / "UE"))
    gate_dir = tmp_path / "somewhere" / "gate"
    monkeypatch.setenv("JARVIS_UE_GATE_PROJECT", str(gate_dir))
    asked = []
    monkeypatch.setattr(ue, "may_run_headless", lambda project: asked.append(project) or (False, "no"))
    assert rigging._unreal_gate(tmp_path, {}) == {"ok": None, "skipped": "no"}
    assert asked == [str(gate_dir / "Gate.uproject")] and not gate_dir.exists()


def test_rigged_folders_follow_the_rigging_workspace(monkeypatch, tmp_path):
    from tools import unreal as U, charjobs
    ws = tmp_path / "hub-workspace"
    d = ws / "rigged" / "Hero-20261109-1"
    d.mkdir(parents=True)
    (d / "report.json").write_text("{}")
    monkeypatch.setenv("JARVIS_BLENDER_WORKSPACE", str(ws))
    monkeypatch.setattr(charjobs, "delivery_root", lambda: tmp_path / "nothing")
    assert d in U._rigged_folders()


@pytest.mark.parametrize("said, tool, which", [
    ("Is Unreal open?", "unreal_status", None), ("Is the Unreal MCP running?", "unreal_status", None),
    ("What version of Unreal do I have?", "unreal_status", None), ("Unreal status.", "unreal_status", None),
    ("Is my Shadow Vanguard ready for Shadow World?", "shadow_world_check", "Shadow Vanguard"),
    ("Is the main character ready for my game?", "shadow_world_check", "main character"),
    ("Is my character ready for Shadow World?", "shadow_world_check", ""),
    ("Is it ready for Unreal?", "shadow_world_check", ""),
    ("Can I put the vanguard in my game?", "shadow_world_check", "vanguard"),
    ("Open Unreal.", None, None), ("Is Discord open?", None, None), ("Are you ready for the game?", None, None),
])
def test_unreal_requests_are_routed_in_code(said, tool, which):
    # live 2026-11-09: the brain answered "Is Unreal open?" with an Explorer process check and invented a training plan
    from core import gateway
    plan = gateway.unreal_request(said)
    if tool is None:
        assert plan is None
        return
    step = plan["steps"][0]
    assert step["tool"] == tool and (which is None or step["arguments"]["which"] == which)


def test_runs_of_one_character_share_a_spoken_name_and_the_newest_wins(monkeypatch, tmp_path):
    from tools import unreal as U
    new = tmp_path / "Meshy_AI_Shadow_Vanguard_20261-48bf3cfce-20261103-220809"
    old = tmp_path / "Meshy_AI_Shadow_Vanguard_20261-48bf3cfce-20261101-113515"
    other = tmp_path / "Shadow_world_Main-20261103-215523-20261103-215526"
    full = tmp_path / "Shadow_world_main_character-20261101-120-20261101-120832"
    for d in (new, old, other, full):
        d.mkdir()
        (d / "report.json").write_text("{}")
    monkeypatch.setattr(U, "_rigged_folders", lambda: [new, other, full, old])
    assert U.spoken_name(new) == "Meshy AI Shadow Vanguard"
    assert U._pick("shadow vanguard") == (new, "")
    folder, ask = U._pick("main")
    assert folder is None and ask == "More than one rigged character matches main: Shadow world Main, Shadow world main character. Which one?"
    assert U._pick("main character")[0] == full
