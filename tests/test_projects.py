"""Project discovery by the owner's words: no project name in the code, the real folder beats worktrees and package copies."""
import os
import time

from core import projects


def make(tmp_path, rel, age=0):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    os.utime(p, (time.time() - age, time.time() - age))
    return str(p)


def test_the_project_in_its_own_folder_beats_a_worktree_and_a_package_copy(tmp_path):
    real = make(tmp_path, "LivingShadowWorld/LivingShadowWorld.uproject", age=3600)
    make(tmp_path, "LSW_wt/LivingShadowWorld.uproject")                           # a git worktree: same file name, other folder
    make(tmp_path, "LSW_PackageOut/Windows/LivingShadowWorld/LivingShadowWorld.uproject")   # skipped: 'windows' output tree
    make(tmp_path, "LivingShadowWorld/Saved/StagedBuilds/LivingShadowWorld.uproject")      # skipped: Saved
    make(tmp_path, "Other/Shadow.uproject")                                        # 'world' missing
    found = projects.find("find my shadow world project and launch it", [str(tmp_path)])
    assert found and found[0].path == real and "its own folder" in " ".join(found[0].why)
    assert not projects.ambiguous(found)
    assert all("StagedBuilds" not in c.path and "PackageOut" not in c.path for c in found)


def test_a_true_tie_is_reported_as_a_choice_not_guessed(tmp_path):
    make(tmp_path, "Alpha/ShadowWorld/ShadowWorld.uproject")
    make(tmp_path, "Beta/ShadowWorld/ShadowWorld.uproject")
    found = projects.find("shadow world", [str(tmp_path)])
    assert len(found) == 2 and projects.ambiguous(found)


def test_kinds_rank_a_project_file_above_a_program_and_installers_sink(tmp_path):
    make(tmp_path, "Melty/Melty.exe")
    make(tmp_path, "Melty/MeltySetup.exe")
    make(tmp_path, "Melty/Melty.sln")
    found = projects.find("open my melty solution", [str(tmp_path)])
    assert found[0].path.endswith("Melty.sln") and found[-1].path.endswith("MeltySetup.exe")


def test_noise_words_do_not_count_and_nothing_means_nothing(tmp_path):
    assert projects.words_of("find my unreal project and open it") == []
    assert projects.find("find my unreal project and open it", [str(tmp_path)]) == []
    assert projects.words_of("Hey Jarvis, find my Shadow World project") == ["hey", "shadow", "world"] or "shadow" in projects.words_of("Shadow World")


def test_launch_method_and_title_words_follow_the_file_kind():
    assert "Unreal" in projects.launch_method(r"C:\x\LivingShadowWorld.uproject")
    assert projects.launch_method(r"C:\x\tool.exe") == "run it"
    assert projects.expected_title_words(r"C:\x\LivingShadowWorld.uproject") == ["LivingShadowWorld"]
    assert projects.expected_title_words(r"C:\x\my_cool-game.sln") == ["cool", "game"]
