"""Mark V (2026-11-09, owner: "yes do it"): a rigged character goes into Living Shadow World through JARVIS's own lane - its
worktree (C:/Users/you/LSW_wt/jarvis) and branch (feat/jarvis-characters), content only under /Game/JARVIS, verified from
disk in a second Unreal process, textured, committed there, never pushed, the owner's working copy untouched."""
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import unreal
from tools import unreal as U


def _glb(path: Path, images=(("normal", b"\x89PNGnormal"), ("texture_0", b"\x89PNGcolour"))):
    binary = b"".join(data for _, data in images)
    views, offset = [], 0
    for _, data in images:
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
        offset += len(data)
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}], "bufferViews": views,
           "images": [{"name": n, "mimeType": "image/png", "bufferView": i} for i, (n, _) in enumerate(images)],
           "textures": [{"source": i} for i in range(len(images))],
           "materials": [{"name": "BakedMaterial", "normalTexture": {"index": 0}, "pbrMetallicRoughness": {"baseColorTexture": {"index": 1}}}]}
    j = json.dumps(doc).encode()
    j += b" " * (-len(j) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = struct.pack("<I", len(j)) + b"JSON" + j + struct.pack("<I", len(binary)) + b"BIN\0" + binary
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)


def test_textures_come_out_of_the_glb_with_their_slots(tmp_path):
    _glb(tmp_path / "char_rigged.glb")
    out = unreal.glb_textures(str(tmp_path / "char_rigged.glb"), tmp_path / "tex", "Hero")
    assert [(t["asset"], t["slot"], t["kind"]) for t in out] == [("T_Hero_BaseColor", "DiffuseColorMap", "color"),
                                                                  ("T_Hero_Normal", "NormalMap", "normal")]
    assert Path(out[0]["file"]).read_bytes() == b"\x89PNGcolour" and Path(out[1]["file"]).read_bytes() == b"\x89PNGnormal"
    assert unreal.glb_textures(str(tmp_path / "missing.glb"), tmp_path / "t2", "Hero") == []


def test_asset_names_follow_ue_naming():
    assert unreal.asset_name("Shadow world main character") == "ShadowWorldMainCharacter"
    assert unreal.asset_name("") == "Character"


@pytest.fixture
def lane(tmp_path, monkeypatch):
    wt = tmp_path / "LSW_wt" / "jarvis"
    (wt / "Binaries" / "Win64").mkdir(parents=True)
    (wt / "LivingShadowWorld.uproject").write_text("{}")
    (wt / "Binaries" / "Win64" / "UnrealEditor-LivingShadowWorld.dll").write_bytes(b"")
    monkeypatch.setattr(unreal, "JARVIS_WT", wt)
    monkeypatch.setattr(unreal, "_git", lambda *a: unreal.JARVIS_BRANCH if a[:1] == ("rev-parse",) else "")
    monkeypatch.setattr(unreal, "may_run_headless", lambda project: (True, ""))
    monkeypatch.setattr(unreal, "engines", lambda: [{"version": "5.8.4", "editor_cmd": "UnrealEditor-Cmd.exe"}])
    char = tmp_path / "rig"
    char.mkdir()
    (char / "char_rigged.fbx").write_bytes(b"fbx")
    _glb(char / "char_rigged.glb")
    return SimpleNamespace(wt=wt, fbx=char / "char_rigged.fbx")


def _fake_unreal(results):
    """A runner standing in for UnrealEditor-Cmd: reads the cfg, writes the scripted result for that mode."""
    seen = []

    def run(cmd, env=None, **kw):
        cfg = json.loads(Path(env["LSW_IMPORT_CFG"]).read_text())
        seen.append((cmd, cfg))
        Path(env["LSW_IMPORT_OUT"]).write_text(json.dumps(results[cfg["mode"]]))
        return SimpleNamespace(stdout="", returncode=0)
    return run, seen


GOOD = {"ok": True, "verts": 51327, "bones": 40, "has_pelvis": True, "textured": True,
        "textures_bound": {"DiffuseColorMap": "/Game/JARVIS/Characters/ShadowWorldMainCharacter/T_ShadowWorldMainCharacter_BaseColor.T"}}


def test_import_then_verify_from_disk_in_jarvis_worktree(lane):
    run, seen = _fake_unreal({"import": GOOD, "verify": GOOD})
    out = unreal.import_character(str(lane.fbx), "Shadow world main character", runner=run)
    assert out["ok"] and out["textured"] and out["dest"] == "/Game/JARVIS/Characters/ShadowWorldMainCharacter"
    (imp_cmd, imp_cfg), (ver_cmd, ver_cfg) = seen
    assert imp_cfg["mode"] == "import" and ver_cfg["mode"] == "verify"
    assert imp_cmd[1] == str(lane.wt / "LivingShadowWorld.uproject") and "-nullrhi" in imp_cmd and "-unattended" in imp_cmd
    assert [t["slot"] for t in imp_cfg["textures"]] == ["DiffuseColorMap", "NormalMap"]


def test_a_grey_import_is_not_ok(lane):
    run, _ = _fake_unreal({"import": GOOD, "verify": {**GOOD, "textured": False}})
    out = unreal.import_character(str(lane.fbx), "Hero", runner=run)
    assert not out["ok"] and "look grey" in out["why"]


def test_never_overwrites_and_never_outside_the_lane(lane, monkeypatch):
    existing = lane.wt / "Content" / "JARVIS" / "Characters" / "Hero"
    existing.mkdir(parents=True)
    (existing / "SK_Hero.uasset").write_bytes(b"x")
    run, seen = _fake_unreal({"import": GOOD, "verify": GOOD})
    out = unreal.import_character(str(lane.fbx), "Hero", runner=run)
    assert not out["ok"] and "never overwrite" in out["why"] and seen == []
    monkeypatch.setattr(unreal, "_git", lambda *a: "main")
    assert unreal.import_character(str(lane.fbx), "Other", runner=run)["why"].startswith("the worktree is on main")
    monkeypatch.setattr(unreal, "_git", lambda *a: unreal.JARVIS_BRANCH)
    monkeypatch.setattr(unreal, "may_run_headless", lambda project: (False, "a game is running"))
    assert unreal.import_character(str(lane.fbx), "Other", runner=run) == {"ok": False, "why": "a game is running"}
    assert seen == []


def test_the_voice_tool_refuses_a_character_that_fails_the_gates(monkeypatch, tmp_path):
    d = tmp_path / "Hero-20261109-1"
    d.mkdir()
    (d / "report.json").write_text(json.dumps({"unreal_gate": {"ok": True, "verts": 86849, "bones": 33},
                                               "bone_name_map": {b: b for b in unreal.MANNEQUIN_CORE},
                                               "pose_tests": [{"test": "a", "pass": True}]}))
    monkeypatch.setattr(U, "_rigged_folders", lambda: [d])
    monkeypatch.setattr(unreal, "import_character", lambda *a, **k: pytest.fail("must not import"))
    out = U.shadow_world_import("hero")
    assert out.startswith("I won't put Hero into Shadow World yet") and "86,849" in out


def test_the_voice_tool_imports_and_commits(monkeypatch, tmp_path):
    d = tmp_path / "Hero-20261109-1"
    d.mkdir()
    (d / "report.json").write_text(json.dumps({"unreal_gate": {"ok": True, "verts": 51327, "bones": 40},
                                               "bone_name_map": {b: b for b in unreal.MANNEQUIN_CORE},
                                               "pose_tests": [{"test": "a", "pass": True}]}))
    monkeypatch.setattr(U, "_rigged_folders", lambda: [d])
    monkeypatch.setattr(unreal, "import_character", lambda fbx, name: {"ok": True, "textured": True, "verify": {"verts": 51327, "bones": 40}})
    monkeypatch.setattr(unreal, "commit_character", lambda name, **k: "f934347")
    monkeypatch.setattr(unreal, "retarget_character", lambda name: {"ok": True, "anims": ["a"] * 9})
    monkeypatch.setattr(unreal, "game_test", lambda name: {"ok": True, "tests": [{"name": "Hero", "state": "Success", "info": [], "errors": []}]})
    out = U.shadow_world_import("hero")
    assert out.startswith("Hero is in my Shadow World branch, /Game/JARVIS/Characters/Hero, 51,327 vertices, 40 bones, textured, commit f934347.")
    assert "Your own copy of the game is untouched." in out and "It has 9 of the game's animations." in out
    assert out.endswith("Hero passed the in-game test.")


def test_registered_routed_and_bounded():
    from core import authz, gateway, pcactions, pcmethods
    assert "shadow_world_import" in pcactions.UNREAL_TOOLS and pcactions.SCHEMAS["shadow_world_import"] == ({"which": str}, False)
    assert authz.effect({"tool": "shadow_world_import", "arguments": {"which": ""}}) == authz.REVERSIBLE
    assert pcmethods.step_timeout("shadow_world_import") <= 600
    assert gateway.unreal_request("Put the main character in Shadow World.")["steps"] == [
        {"tool": "shadow_world_import", "arguments": {"which": "main character"}}]
    assert gateway.unreal_request("Put the milk in the fridge.") is None


def test_the_rigger_passes_the_game_budget(monkeypatch, tmp_path):
    from tools import rigging
    monkeypatch.setenv("JARVIS_BLENDER_WORKSPACE", str(tmp_path / "ws"))      # never the owner's real workspace
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return SimpleNamespace(stdout="", stderr="", returncode=1)
    monkeypatch.setattr(rigging.craftjobs, "run", run)
    monkeypatch.setattr(rigging, "blender_exe", lambda: "blender.exe")
    model = tmp_path / "m.glb"
    model.write_bytes(b"glTF")
    rigging._rig_once(str(model), "humanoid", "ue5", 40000, max_faces=70000)
    cmd = next(c for c in seen if "--faces" in c)
    assert cmd[cmd.index("--max-faces") + 1] == "70000"



def test_every_ue5_rig_gets_the_game_budget(monkeypatch):
    from tools import rigging
    got = []
    monkeypatch.setattr(rigging, "_rig_once", lambda *a, **k: got.append(k) or "stop")
    rigging._rig("m.glb", "humanoid", "ue5")
    rigging._rig("m.glb", "humanoid", "blender")
    assert got[0] == {"max_faces": rigging.GAME_MAX_FACES} and got[1] == {}


def test_commit_only_the_folder_and_never_push(monkeypatch):
    calls = []

    def git(*a):
        calls.append(a)
        return unreal.JARVIS_BRANCH if a[:1] == ("rev-parse",) and "--abbrev-ref" in a else "abc1234"
    monkeypatch.setattr(unreal, "_git", git)
    assert unreal.commit_character("Hero") == "abc1234"
    commit = next(c for c in calls if "commit" in c)
    assert commit[commit.index("--") + 1] == "Content/JARVIS/Characters/Hero"        # a pathspec: nothing else staged rides along
    assert not any("push" in c for c in calls)
    monkeypatch.setattr(unreal, "_git", lambda *a: "main")
    with pytest.raises(RuntimeError, match="no longer on"):
        unreal.commit_character("Hero")


def test_a_failed_attempt_leaves_nothing_behind(lane):
    made = lane.wt / "Content" / "JARVIS" / "Characters" / "Hero"

    def run(cmd, env=None, **kw):
        cfg = json.loads(Path(env["LSW_IMPORT_CFG"]).read_text())
        made.mkdir(parents=True, exist_ok=True)
        (made / "SK_Hero.uasset").write_bytes(b"x")                               # Unreal saved before verify failed
        out = GOOD if cfg["mode"] == "import" else {**GOOD, "has_pelvis": False}
        Path(env["LSW_IMPORT_OUT"]).write_text(json.dumps(out))
    out = unreal.import_character(str(lane.fbx), "Hero", runner=run)
    assert not out["ok"] and not made.exists()


def test_a_parent_default_texture_does_not_count(lane):
    bound = {"DiffuseColorMap": "/InterchangeAssets/Textures/Default.Default"}
    run, _ = _fake_unreal({"import": GOOD, "verify": {**GOOD, "textured": True, "textures_bound": bound}})
    assert not unreal.import_character(str(lane.fbx), "Hero", runner=run)["ok"]


def test_perform_reports_only_a_committed_import_as_done(monkeypatch):
    from core import pcactions
    from tools import unreal as U
    for said, ok in [("Hero is in my Shadow World branch, /Game/JARVIS/Characters/Hero, 1 vertices, 1 bones, textured, commit a.", True),
                     ("Hero is imported into my Shadow World branch and checked, but committing it failed (x); it is there uncommitted.", False),
                     ("I couldn't find a rigged character called dragon.", False),
                     ("More than one rigged character matches x: a, b. Which one?", False)]:
        monkeypatch.setattr(U, "shadow_world_import", lambda which="", s=said: s)
        assert pcactions.perform("shadow_world_import", {"which": ""})["verified"] is ok, said


def test_the_newest_passing_run_is_used(monkeypatch, tmp_path):
    def run_folder(stamp, verts):
        d = tmp_path / f"Hero-20261109-{stamp}"
        d.mkdir()
        (d / "report.json").write_text(json.dumps({"unreal_gate": {"ok": True, "verts": verts, "bones": 40},
                                                   "bone_name_map": {b: b for b in unreal.MANNEQUIN_CORE},
                                                   "pose_tests": [{"test": "a", "pass": True}]}))
        return d
    failing_new, passing_old = run_folder("2", 86849), run_folder("1", 51327)
    monkeypatch.setattr(U, "_rigged_folders", lambda: [failing_new, passing_old])
    used = []
    monkeypatch.setattr(unreal, "import_character", lambda fbx, name: used.append(fbx) or {"ok": True, "textured": True, "verify": {}})
    monkeypatch.setattr(unreal, "commit_character", lambda name, **k: "c")
    monkeypatch.setattr(unreal, "retarget_character", lambda name: {"ok": True, "anims": []})
    monkeypatch.setattr(unreal, "game_test", lambda name: {"ok": True, "tests": []})
    U.shadow_world_import("hero")
    assert used == [str(passing_old / "char_rigged.fbx")]


def test_retarget_is_never_redone_and_needs_the_character_first(lane):
    assert unreal.retarget_character("Hero")["why"].startswith("Hero isn't in my Shadow World branch yet")
    folder = lane.wt / "Content" / "JARVIS" / "Characters" / "Hero"
    (folder / "Anims").mkdir(parents=True)
    (folder / "SK_Hero.uasset").write_bytes(b"x")
    (folder / "Anims" / "MM_Idle_Hero.uasset").write_bytes(b"x")
    out = unreal.retarget_character("Hero", runner=lambda *a, **k: pytest.fail("never redone"))
    assert out["ok"] and out["already"] and out["anims"] == ["MM_Idle_Hero"]


def test_a_failed_retarget_removes_only_what_it_made(lane):
    folder = lane.wt / "Content" / "JARVIS" / "Characters" / "Hero"
    folder.mkdir(parents=True)
    (folder / "SK_Hero.uasset").write_bytes(b"x")

    def run(cmd, env=None, timeout=0):
        (folder / "Rig").mkdir()
        (folder / "Anims").mkdir()
        Path(env["LSW_RT_OUT"]).write_text(json.dumps({"ok": False, "error": "no chains"}))
        return True
    out = unreal.retarget_character("Hero", runner=run)
    assert not out["ok"] and "no chains" in out["why"]
    assert (folder / "SK_Hero.uasset").exists() and not (folder / "Rig").exists() and not (folder / "Anims").exists()


def test_the_game_test_report_is_read(lane):
    def run(cmd, cwd=None, timeout=0):
        tag = cmd[cmd.index("test") + 1]
        rep = lane.wt / "Documentation" / "BuildEvidence" / f"{tag}_x_report"
        rep.mkdir(parents=True)
        (rep / "index.json").write_text(json.dumps({"tests": [{"fullTestPath": "LivingShadowWorld.JARVIS.InGame.Hero", "state": "Success",
            "entries": [{"event": {"type": "Info", "message": "JARVIS: Hero body height 183.9 cm"}}]}]}), encoding="utf-8")
        assert cmd[-1] == "LivingShadowWorld.JARVIS.InGame.Hero" and cwd == str(lane.wt)
        return True
    out = unreal.game_test("Hero", runner=run)
    assert out["ok"] and out["tests"][0]["info"] == ["JARVIS: Hero body height 183.9 cm"]
    assert U._say_game_test(out) == "Hero passed the in-game test: it's 184 cm tall."
    full = {"tests": [{"name": "Hero", "state": "Success", "errors": [], "info": [
        "JARVIS: Hero rested at Z=90.1 (capsule half height 88.0), on ground=1", "JARVIS: Hero body height 183.9 cm",
        "JARVIS: Hero foot_l moved up to 68.4 cm during the walk"]}]}
    assert U._say_game_test(full) == ("Hero passed the in-game test: it lands on the floor and stands, it's 184 cm tall, "
                                      "its walk really moves its feet.")
    fail = {"tests": [{"name": "Hero", "state": "Fail", "info": [], "errors": ["Expected 'collides with the floor: stands on it' to be true."]}]}
    assert U._say_game_test(fail) == "Hero FAILED the in-game test: collides with the floor: stands on it."


@pytest.mark.parametrize("said, tool", [
    ("Retarget the main character for Shadow World.", "shadow_world_retarget"),
    ("Give my character the game's animations.", "shadow_world_retarget"),
    ("Test the main character in Shadow World.", "shadow_world_game_test"),
    ("Does my character work in the game?", "shadow_world_game_test"),
    ("Test the microphone.", None),
])
def test_ask_routing(said, tool):
    from core import gateway
    plan = gateway.unreal_request(said)
    assert (plan["steps"][0]["tool"] if plan else None) == tool


def test_perform_verifies_only_real_successes(monkeypatch):
    from core import authz, pcactions, pcmethods
    from tools import unreal as Ut
    cases = [("shadow_world_retarget", "Hero now has 9 of the game's animations, ...; Committed abc.", True),
             ("shadow_world_retarget", "I didn't retarget Hero: a game is running.", False),
             ("shadow_world_game_test", "Hero passed the in-game test (x).", True),
             ("shadow_world_game_test", "Hero FAILED the in-game test: y.", False)]
    for tool, said, ok in cases:
        monkeypatch.setattr(Ut, tool, lambda which="", s=said: s)
        assert pcactions.perform(tool, {"which": ""})["verified"] is ok, said
    assert authz.effect({"tool": "shadow_world_game_test", "arguments": {"which": ""}}) == authz.OBSERVE
    assert all(pcmethods.step_timeout(t) <= 600 for t in ("shadow_world_retarget", "shadow_world_game_test", "shadow_world_import"))
