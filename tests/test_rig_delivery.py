"""Owner, 2026-11-01: "i cant see what jarvis had rigged and he does not open it when he is done ... i dont even know where he puts the rigged character".
A finished rig is now copied somewhere obvious, remembered across restarts, opened in a new Blender window, announced first, and 'where is it' answers."""
from pathlib import Path

import pytest

from core import craftintent, craftjobs, storage
from tools import charjobs, rigging


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_JOBS_INLINE", "1")
    monkeypatch.setenv("JARVIS_SKIP_GPU_GUARD", "1")
    monkeypatch.setenv("JARVIS_RIGGED_DIR", str(tmp_path / "Documents" / "JARVIS Rigged Characters"))
    monkeypatch.delenv("JARVIS_AUTO_OPEN_RIG", raising=False)
    monkeypatch.setattr(rigging, "blender_exe", lambda: Path("C:/fake/blender.exe"))
    monkeypatch.setattr(charjobs.time, "sleep", lambda s: None)
    craftjobs._jobs.clear()
    charjobs._PENDING.clear()
    launched = []

    class Proc:
        returncode = None

        def poll(self):
            return None
    monkeypatch.setattr(charjobs, "_spawn", lambda cmd: launched.append(cmd) or Proc())
    from tools import gaming
    monkeypatch.setattr(gaming, "game_mode", lambda: False)
    return launched


@pytest.fixture
def rig_output(tmp_path):
    out = tmp_path / "ws" / "rigged" / "Meshy_AI_Shadow_Vanguard-20261101-120000"
    out.mkdir(parents=True)
    for n in ("char_rigged.glb", "char_rigged.fbx", "rest.png", "report.json", "variants.json"):
        (out / n).write_bytes(b"x")
    text = f"Rigged Meshy AI Shadow Vanguard: humanoid, 36 bones. Everything I measured passed. Files are in {out}: a .glb for Blender and an .fbx with Unreal mannequin bone names. "
    return out, text


def test_finished_rig_is_copied_remembered_and_opened(rig_output, isolated):
    out, text = rig_output
    said = charjobs._deliver(text)
    root = charjobs.delivery_root()
    dest = root / out.name
    assert said.startswith("I opened it in a new Blender window so you can test it.")
    assert "Documents folder, in JARVIS Rigged Characters" in said and out.name in said and said.endswith(text)
    assert {p.name for p in dest.iterdir()} == {"char_rigged.glb", "char_rigged.fbx", "rest.png"}      # the useful files only, not logs
    assert (out / "char_rigged.glb").exists()                                                           # the original is never moved
    cmd = isolated[-1]
    assert "import_scene.gltf" in cmd[2] and repr(str(dest / "char_rigged.glb")) in cmd[2]
    rec = charjobs.last_delivery()
    assert rec["folder"] == str(dest) and rec["original"] == str(out)


def test_it_works_through_the_real_job_path(rig_output, isolated, monkeypatch):
    out, text = rig_output
    monkeypatch.setattr(rigging, "rig_character", lambda *a, **k: text)
    src = out.parent / "Shadow.glb"
    src.write_bytes(b"x")
    charjobs._rig(src, "humanoid", "blender")
    j = craftjobs.get()
    assert j["state"] == "done" and j["result"].startswith("I opened it in a new Blender window") and len(isolated) == 1


def test_game_mode_does_not_pop_a_window(rig_output, isolated, monkeypatch):
    from tools import gaming
    monkeypatch.setattr(gaming, "game_mode", lambda: True)
    said = charjobs._deliver(rig_output[1])
    assert "did not open Blender" in said and isolated == [] and charjobs.last_delivery()


def test_auto_open_can_be_turned_off(rig_output, isolated, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTO_OPEN_RIG", "0")
    said = charjobs._deliver(rig_output[1])
    assert isolated == [] and "opened it" not in said and "saved in your Documents folder" in said


def test_failed_copy_falls_back_to_the_original_and_says_so(rig_output, isolated, monkeypatch):
    out, text = rig_output
    monkeypatch.setattr(charjobs.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    said = charjobs._deliver(text)
    assert "the copy to Documents failed" in said and charjobs.last_delivery()["folder"] == str(out)


def test_blender_that_will_not_start_is_reported_not_claimed(rig_output, isolated, monkeypatch):
    class Dead:
        returncode = 3

        def poll(self):
            return 3
    monkeypatch.setattr(charjobs, "_spawn", lambda cmd: Dead())
    said = charjobs._deliver(rig_output[1])
    assert "I did not open it" in said and "I opened it in a new Blender window" not in said and charjobs.last_delivery()


def test_results_without_files_are_untouched(isolated):
    assert charjobs._deliver("Rigging failed: no mesh.") == "Rigging failed: no mesh." and isolated == []
    assert charjobs._deliver("Files are in C:/definitely/not/here: a .glb") == "Files are in C:/definitely/not/here: a .glb"


# ---- 'where is it' ----------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["where is it", "where did you put the rigged character", "where did you save it", "Where's the rigged model?",
                                  "i dont even know where he puts the rigged character", "where is the rigged character"])
def test_where_questions_are_answered_when_something_was_rigged(rig_output, text):
    charjobs._deliver(rig_output[1])
    assert craftintent.match(text) == ("where_is_rigged_character", {"open_folder": False}), text


def test_where_answer_is_plain_and_complete(rig_output):
    out, text = rig_output
    charjobs._deliver(text)
    said = charjobs.where_is_rigged_character()
    assert "Documents, JARVIS Rigged Characters" in said and out.name in said and ".glb" in said and "open it" in said


def test_show_me_the_folder_opens_explorer(rig_output, isolated):
    charjobs._deliver(rig_output[1])
    assert craftintent.match("show me the folder") == ("where_is_rigged_character", {"open_folder": True})
    charjobs.where_is_rigged_character(open_folder=True)
    assert isolated[-1][0] == "explorer.exe" and Path(isolated[-1][1]).name.startswith("Meshy_AI")


def test_where_is_unrelated_things_is_left_alone(rig_output):
    charjobs._deliver(rig_output[1])
    for text in ("where is the nearest pharmacy", "where is my cat", "where do I park", "how do I find where files are saved"):
        assert craftintent.match(text) is None or craftintent.match(text)[0] != "where_is_rigged_character", text


def test_without_any_rig_the_question_is_not_hijacked():
    assert craftintent.match("where is it") is None
    assert "no finished rigged character" in charjobs.where_is_rigged_character()


def test_while_a_rig_is_running_it_says_so_and_promises_the_location(monkeypatch):
    craftjobs.submit("rigging X", lambda: "done", wait=False) if False else None
    j = {"id": "a", "label": "rigging X", "state": "running", "step": "skinning", "steps": [], "result": "", "created": 1.0, "started": 1.0,
         "finished": None, "uid": None, "need_gpu_mb": 0, "_fn": None, "_cancel": None, "_procs": []}
    craftjobs._jobs["a"] = j
    said = charjobs.where_is_rigged_character()
    assert "not finished yet" in said and "Documents" in said


# ---- 'open it' still works after a restart (no job in memory) ----------------------------------------------------------------------------------

def test_open_it_works_after_a_restart_from_the_remembered_result(rig_output, isolated):
    out, text = rig_output
    charjobs._deliver(text)
    isolated.clear()
    craftjobs._jobs.clear()                                                    # a restarted JARVIS knows no jobs
    assert craftintent.match("can you open it for me?") == ("open_in_blender", {"which": ""})
    said = charjobs.open_in_blender("")
    assert said.startswith("Blender is starting with char_rigged.glb") and len(isolated) == 1
    assert craftintent.match("open the rigged character in blender") == ("open_in_blender", {"which": ""})


def test_a_week_old_result_is_not_assumed_for_a_bare_open_it(rig_output, monkeypatch):
    charjobs._deliver(rig_output[1])
    rec = charjobs.last_delivery()
    storage.save_global(charjobs.LAST, {**rec, "t": rec["t"] - 7 * 86400})
    craftjobs._jobs.clear()
    assert craftintent.match("can you open it for me?") is None
    assert charjobs.last_delivery() is not None and charjobs.last_delivery(3600) is None


@pytest.mark.parametrize("stem,spoken", [("Meshy_AI_Shadow_Vanguard_20261-48bf3cfce-20261101-113515", "Meshy AI Shadow Vanguard"),
                                         ("Meshy_AI_Shadow_Vanguard_20261-48bf3cfce8ac", "Meshy AI Shadow Vanguard"),
                                         ("synthetic_quadruped-20261031-134823", "synthetic quadruped"), ("Neon_Sentinel", "Neon Sentinel")])
def test_names_are_spoken_without_stamps_or_hashes(stem, spoken):
    assert charjobs._spoken(stem) == spoken


def _fake_rig(out, ok):
    return {"ok": ok, "kind": "humanoid", "bones": 36, "skin_method": "heat", "output_folder": str(out),
            "stages": {"skin_valid": "PASS", "pose_tests": "12/12 passed", "ik_fk": "4/4 passed", "export": "PASS",
                       "reimport_animation_roundtrip": "PASS"} if ok else {"skin_valid": "FAIL"},
            "failing_pose_tests": [], "unreal_gate": None}


def test_every_finished_rig_lands_in_documents_once(rig_output, monkeypatch):
    """Owner, 2026-11-09: "when he rigs the characters where do they go i cant find them" -- plain 'rig this' rigs
    stayed in the Blender workspace. Now every finished rig is copied, and the sentence is said once."""
    out, _ = rig_output
    monkeypatch.setattr(rigging, "_rig_once", lambda *a, **k: _fake_rig(out, True))
    res = rigging._rig(str(out / "char_rigged.glb"))
    dest = charjobs.delivery_root() / out.name
    assert res["documents_folder"] == str(dest)
    assert {p.name for p in dest.iterdir()} == {"char_rigged.glb", "char_rigged.fbx", "rest.png"}
    said = rigging._speak(res)
    assert f"A copy is in your Documents folder, in JARVIS Rigged Characters, in a folder called {out.name}. " in said
    delivered = charjobs._deliver(said)               # the job path announces it first and must not say it twice
    assert delivered.count("JARVIS Rigged Characters") == 1


def test_a_failed_rig_is_not_copied(rig_output, monkeypatch):
    out, _ = rig_output
    monkeypatch.setattr(rigging, "_rig_once", lambda *a, **k: _fake_rig(out, False))
    from tools import remedies
    monkeypatch.setattr(remedies, "next_step", lambda *a, **k: None)
    res = rigging._rig(str(out / "char_rigged.glb"))
    assert "documents_folder" not in res and not (charjobs.delivery_root() / out.name).exists()
    assert "Documents" not in rigging._speak(res)


def test_the_fbx_texture_folder_comes_along(rig_output):
    out, _ = rig_output
    (out / "char_rigged.fbm").mkdir()
    (out / "char_rigged.fbm" / "skin.png").write_bytes(b"x")
    dest = charjobs.copy_to_documents(out)
    assert (dest / "char_rigged.fbm" / "skin.png").is_file() and not (dest / "report.json").exists()
