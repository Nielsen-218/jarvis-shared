"""The resolver that finds the character the owner means. It must ask instead of guessing and never touch the
open Blender session (a wrong guess on 2026-10-31 rigged a file whose Blender title showed unsaved edits)."""
import os
import time
from pathlib import Path

import pytest

from tools import charjobs, rigging


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Roots = tmp_path; rigging stubbed to record which file it was asked to rig; no Blender windows by default."""
    for name in ("Shadow_Vanguard.blend", "Shadow_Vanguard_notes.glb", "Neon_Sentinel.glb", "Red_Sweater.blend"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "rigged").mkdir()
    (tmp_path / "rigged" / "Old_Result.glb").write_bytes(b"x")
    rigged = []
    monkeypatch.setattr(rigging, "_allowed_roots", lambda: [tmp_path])
    monkeypatch.setattr(charjobs, "_rig", lambda path, kind, profile: rigged.append(str(path)) or f"RIGGED {Path(path).name}")
    monkeypatch.setattr(charjobs.pcops, "windows", lambda: [])
    monkeypatch.setenv("JARVIS_BLENDER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    (tmp_path / "temp").mkdir()
    return tmp_path, rigged


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setenv("JARVIS_JOBS_INLINE", "1")      # the tests want the result, not a background job
    monkeypatch.setenv("JARVIS_SKIP_GPU_GUARD", "1")   # and must never unload the owner's real language model


def blender_window(path, unsaved, pid=4242):
    title = ("* " if unsaved else "") + f"{Path(path).stem} [{path}]"
    return {"title": title, "process": "blender.exe", "pid": pid, "foreground": False, "minimized": False, "rect": [0, 0, 1, 1]}


def test_title_parsing_and_unsaved_flag(world, monkeypatch):
    root, _ = world
    f = str(root / "Shadow_Vanguard.blend")
    monkeypatch.setattr(charjobs.pcops, "windows", lambda: [blender_window(f, True), {"title": "x", "process": "notepad.exe", "pid": 1}])
    got = charjobs.open_blender_models()
    assert len(got) == 1 and got[0]["path"] == f and got[0]["unsaved"] is True


def test_named_unique_character_is_rigged(world):
    root, rigged = world
    out = charjobs.rig_my_character("the neon sentinel")
    assert out == "RIGGED Neon_Sentinel.glb" and rigged == [str(root / "Neon_Sentinel.glb")]


def test_output_folders_and_autosaves_are_never_candidates(world):
    root, _ = world
    (root / "Shadow_Vanguard_123_autosave.blend").write_bytes(b"x")
    names = [h["name"] for h in charjobs.find_files("", 20)]
    assert "Old_Result" not in names and not any("autosave" in n for n in names)


def test_ambiguous_name_asks_and_rigs_nothing(world):
    root, rigged = world
    out = charjobs.rig_my_character("shadow vanguard")
    # two different files share the words; the closest wins only if unambiguous - here the notes file is a different stem
    assert rigged and out.startswith("RIGGED") or out.startswith("Which one do you mean?")
    rigged.clear()
    (root / "Shadow_Dancer.glb").write_bytes(b"x")
    out = charjobs.rig_my_character("shadow")
    assert out.startswith("Which one do you mean?") and rigged == []


def test_no_name_lists_recent_and_rigs_nothing(world):
    _, rigged = world
    out = charjobs.rig_my_character("")
    assert out.startswith("Which character should I rig?") and rigged == []


def test_unknown_name_offers_picture_or_path(world):
    out = charjobs.rig_my_character("zorblax the destroyer")
    assert "couldn't find" in out and "picture" in out


def test_open_saved_model_is_used_directly(world, monkeypatch):
    root, rigged = world
    f = str(root / "Shadow_Vanguard.blend")
    monkeypatch.setattr(charjobs.pcops, "windows", lambda: [blender_window(f, False)])
    out = charjobs.rig_my_character("")
    assert "Using your open Blender file Shadow_Vanguard" in out and rigged == [f]


def test_open_model_with_unsaved_edits_uses_a_copy_of_the_autosave(world, monkeypatch):
    root, rigged = world
    f = str(root / "Shadow_Vanguard.blend")
    auto = root / "temp" / "Shadow_Vanguard_4242_autosave.blend"
    auto.write_bytes(b"autosave-bytes")
    monkeypatch.setattr(charjobs.pcops, "windows", lambda: [blender_window(f, True)])
    out = charjobs.rig_my_character("")
    assert "unsaved edits" in out and "autosave" in out and "untouched" in out
    assert len(rigged) == 1 and rigged[0] != f and "-live-" in rigged[0]
    assert Path(rigged[0]).read_bytes() == b"autosave-bytes" and auto.read_bytes() == b"autosave-bytes"   # original not modified


def test_unsaved_without_autosave_asks_the_owner_to_save(world, monkeypatch):
    root, rigged = world
    f = str(root / "Shadow_Vanguard.blend")
    monkeypatch.setattr(charjobs.pcops, "windows", lambda: [blender_window(f, True)])
    out = charjobs.rig_my_character("")
    assert "Ctrl+S" in out and rigged == []
    out = charjobs.rig_my_character("", use_saved_version=True)
    assert rigged == [f] and out.startswith("Using your open Blender file")


def test_two_open_scenes_ask_which(world, monkeypatch):
    root, rigged = world
    a, b = str(root / "Shadow_Vanguard.blend"), str(root / "Red_Sweater.blend")
    monkeypatch.setattr(charjobs.pcops, "windows", lambda: [blender_window(a, False, 1), blender_window(b, False, 2)])
    assert "Which one should I rig?" in charjobs.rig_my_character("") and rigged == []


def test_a_path_and_a_picture_are_used_as_given(world, monkeypatch):
    root, rigged = world
    assert charjobs.rig_my_character(str(root / "Red_Sweater.blend")) == "RIGGED Red_Sweater.blend"
    img = root / "ref.png"
    img.write_bytes(b"png")
    seen = []
    monkeypatch.setattr(rigging, "image_to_character", lambda p, k, pr: seen.append(p) or "GENERATED")
    assert charjobs.rig_my_character(str(img)) == "GENERATED" and seen == [str(img)]


def test_find_character_reports_open_and_matches(world, monkeypatch):
    root, _ = world
    f = str(root / "Shadow_Vanguard.blend")
    monkeypatch.setattr(charjobs.pcops, "windows", lambda: [blender_window(f, True)])
    text = charjobs.find_character("vanguard")
    assert "Open in Blender: Shadow_Vanguard (unsaved edits)" in text and "Files matching that" in text


def test_made_up_open_phrase_with_nothing_open_asks_instead_of_searching(world):
    _, rigged = world
    for phrase in ("the model I have open", "the one I have open in blender", "my current scene"):
        out = charjobs.rig_my_character(phrase)
        assert out.startswith("Nothing is open in Blender right now. Which character should I rig?") and rigged == []


def test_tidy_keeps_the_newest_good_run_and_only_deletes_after_confirm(tmp_path, monkeypatch):
    import json, os, time
    ws = tmp_path / "ws"
    monkeypatch.setenv("JARVIS_BLENDER_WORKSPACE", str(ws))
    old = time.time() - 40 * 86400
    names = []
    for i, ok in enumerate([True, True, False, False, False]):
        d = ws / "rigged" / f"hero-2026010{i + 1}-000000"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"ok": ok}))
        (d / "a.glb").write_bytes(b"x" * 100)
        os.utime(d, (old + i * 100, old + i * 100))
        names.append(d)
    doomed, kept = charjobs.cleanup_plan()
    assert {names[2], names[3], names[4], names[0]} == set(doomed)              # old failures + the older duplicate
    assert names[1] not in doomed                                               # newest good run is always kept
    assert "Nothing is deleted yet" in charjobs.tidy_character_outputs()
    assert all(d.exists() for d in names)
    assert "Removed" in charjobs.tidy_character_outputs(confirm=True)
    assert names[1].exists()
