"""The file/project/terminal verbs, with the PC faked: paths stay in the owner's space, a tie is a choice, a T2 command needs the yes."""
import os

import pytest

from core import pcverbs, projects


def test_find_project_reports_a_choice_on_a_tie_and_a_single_best_with_its_method(monkeypatch, tmp_path):
    a = tmp_path / "A" / "Shadowy.uproject"; a.parent.mkdir(); a.write_text("x")
    b = tmp_path / "B" / "Shadowy.uproject"; b.parent.mkdir(); b.write_text("x")
    monkeypatch.setattr(projects, "roots", lambda: [str(tmp_path)])
    out = pcverbs.find_project({"name": "my shadowy game"})
    assert out["verified"] is False and out["error_kind"] == "needs_choice" and len(out["choices"]) == 2
    b.unlink(); b.parent.rmdir()
    out = pcverbs.find_project({"name": "my shadowy game"})
    assert out["verified"] and out["found"] == str(a) and "Unreal" in out["method"]
    with pytest.raises(RuntimeError, match="did not find"):
        pcverbs.find_project({"name": "nothing like this"})


def test_open_path_refuses_the_unknown_the_system_and_the_unlaunchable(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="no file or folder"):
        pcverbs.open_path({"path": str(tmp_path / "missing.txt")})
    with pytest.raises(RuntimeError, match="not opened|No path"):
        pcverbs.open_path({"path": "<found>"})
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    if os.path.isfile(os.path.join(sysroot, "notepad.exe")):
        with pytest.raises(RuntimeError, match="outside your own folders"):
            pcverbs.open_path({"path": os.path.join(sysroot, "notepad.exe")})
    odd = tmp_path / "x.dll"; odd.write_text("x")
    monkeypatch.setattr(pcverbs, "_in_user_space", lambda p: True)
    with pytest.raises(RuntimeError, match="don't open"):
        pcverbs.open_path({"path": str(odd)})


def test_open_path_verifies_by_a_new_window_named_after_the_file(monkeypatch, tmp_path):
    doc = tmp_path / "Notes.txt"; doc.write_text("x")
    monkeypatch.setattr(pcverbs, "_in_user_space", lambda p: True)
    launched = []
    import tools.software as software
    monkeypatch.setattr(software, "launch_as_user", lambda p: launched.append(p))
    shown = [[], [{"hwnd": 1, "title": "Notes.txt - Notepad", "process": "notepad.exe", "minimized": False}]]
    monkeypatch.setattr(pcverbs, "_windows_with", lambda words: shown.pop(0) if shown else shown[-1] if shown else [{"hwnd": 1, "title": "Notes.txt - Notepad", "process": "notepad.exe", "minimized": False}])
    out = pcverbs.open_path({"path": str(doc)})
    assert launched == [str(doc)] and out["verified"] and "Notes.txt - Notepad" in out["detail"]


def test_run_terminal_tiers_decide_who_may_run_what(monkeypatch):
    with pytest.raises(RuntimeError, match="needs your yes"):
        pcverbs.run_terminal({"command": "Remove-Item C:\\x -Recurse"})
    with pytest.raises(RuntimeError, match="never run"):
        pcverbs.run_terminal({"command": "Set-MpPreference -DisableRealtimeMonitoring $true"})
    out = pcverbs.run_terminal({"command": "echo hello-from-jarvis"})
    assert out["verified"] and "hello-from-jarvis" in out["detail"]
    from core import authz
    home = os.path.expanduser("~")
    assert pcverbs.expand_home("mkdir ~/Downloads/x") == f"mkdir {home}/Downloads/x"
    assert authz.effect({"tool": "run_terminal", "arguments": {"command": "mkdir ~/Downloads/jarvis-test"}}) == "reversible", \
        "a folder inside the owner's own space is a reversible change"
    assert authz.effect({"tool": "run_terminal", "arguments": {"command": "mkdir C:\\Windows\\x"}}) == "irreversible"


def test_mkdir_in_the_owners_folder_is_in_scope_and_reversible_but_the_gate_is_unchanged():
    """2026-11-06: 'create a folder called jarvis-test ... from the terminal' asked for review because the scope check wanted
    the word 'mkdir' in the sentence. The target is what must be named; the tier still decides what may run."""
    from core import authz
    home = os.path.expanduser("~")
    said = "Open my Downloads folder, create a folder called jarvis-test in it from the terminal, then open Notepad and type that folder's path."
    for cmd in ("mkdir jarvis-test", "mkdir ~/Downloads/jarvis-test", f"mkdir {home}\\Downloads\\jarvis-test",
                f'New-Item -ItemType Directory -Path "{home}\\Downloads\\jarvis-test"'):
        step = {"tool": "run_terminal", "arguments": {"command": cmd}}
        assert authz.effect(step) == "reversible", cmd
        assert not authz.decide([step], said).ask, cmd
    other = {"tool": "run_terminal", "arguments": {"command": f"mkdir {home}\\Downloads\\something-else"}}
    assert authz.decide([other], said).ask, "a folder the owner never named still asks"
    for cmd in ("mkdir C:\\Windows\\jarvis-test", "Remove-Item ~/Downloads/jarvis-test -Recurse"):
        assert authz.decide([{"tool": "run_terminal", "arguments": {"command": cmd}}], said).ask, cmd
    assert authz.command_targets("Get-ChildItem C:\\x\\Logs | Sort-Object Length -Descending") == ["Logs"]
    assert authz.relative_targets("mkdir reports") == ["reports"]
    assert authz.relative_targets(f'mkdir "{home}\\Documents\\reports"') == [] and authz.relative_targets("mkdir ~/x") == []


def test_a_terminal_change_with_a_relative_target_is_sent_back_for_a_full_path():
    """2026-11-06: 'open JarvisAcceptance, then mkdir reports' created C:\\Users\\me\\reports (the terminal starts in home)."""
    from core import gateway
    with pytest.raises(ValueError, match="full paths"):
        gateway._check_step({"tool": "run_terminal", "arguments": {"command": "mkdir reports"}}, None)
    home = os.path.expanduser("~")
    ok = gateway._check_step({"tool": "run_terminal", "arguments": {"command": f"mkdir {home}\\Documents\\reports"}}, None)
    assert ok["arguments"]["command"].endswith("reports")
    assert gateway._check_step({"tool": "run_terminal", "arguments": {"command": "dir"}}, None), "a read-only command may be relative"


def test_a_relative_change_right_after_opening_a_folder_lands_in_that_folder():
    from core import gateway
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    steps = gateway._anchor_terminal_steps([{"tool": "open_path", "arguments": {"path": docs}},
                                            {"tool": "run_terminal", "arguments": {"command": "mkdir reports"}}])
    assert steps[1]["arguments"]["command"] == f'mkdir "{docs}\\reports"'
    alone = gateway._anchor_terminal_steps([{"tool": "run_terminal", "arguments": {"command": "mkdir reports"}}])
    assert alone[0]["arguments"]["command"] == "mkdir reports", "no opened folder: left for _check_step to refuse"
    q = gateway._anchor_terminal_steps([{"tool": "open_path", "arguments": {"path": docs}},
                                        {"tool": "run_terminal", "arguments": {"command": "New-Item -ItemType Directory -Name 'my stuff'"}}])
    assert q[1]["arguments"]["command"] == f'New-Item -ItemType Directory -Path "{docs}\\my stuff"'


def test_run_terminal_really_runs_its_gate_and_is_not_a_dry_run(monkeypatch):
    """2026-11-06: the Guardrails default is dry-run, so every T1 change was audited and silently not executed."""
    import jarvis_pc.guardrails.gate as gate
    seen = {}
    real = gate.Guardrails

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)
    monkeypatch.setattr(gate, "Guardrails", spy)
    pcverbs.run_terminal({"command": "echo gate-check"})
    assert seen.get("dry_run") is False and seen.get("approver") is None and seen.get("pre_change") is not None


def test_typing_never_goes_into_a_document_the_owner_did_not_name(monkeypatch):
    from core import pcbridge
    with pcbridge.user_message("owner", "Open Notepad, type hello world, and save it as hello.txt."):
        assert pcverbs._foreign_document("notepad", "README.TXT - Notepad")
        assert pcverbs._foreign_document("notepad", "*Friday 2026-11-06 - Notepad")
        assert not pcverbs._foreign_document("notepad", "Untitled - Notepad")
        assert not pcverbs._foreign_document("notepad", "*Untitled - Notepad")
        assert not pcverbs._foreign_document("paint", "README.TXT - Paint")
    with pcbridge.user_message("owner", "add milk to my shopping list in notepad"):
        assert not pcverbs._foreign_document("notepad", "shopping list.txt - Notepad")


class _FakeWin:
    def __init__(self, titles):
        self.titles = list(titles)

    def window_text(self):
        return self.titles.pop(0) if len(self.titles) > 1 else self.titles[0]


def _fake_notepad(monkeypatch, titles, typed):
    from jarvis_pc.uiact import uia
    import pyautogui
    import tools.computer as computer
    win = _FakeWin(titles)
    monkeypatch.setattr(pcverbs, "_one_window", lambda app, cancelled=None: ("notepad", {"title": titles[0], "hwnd": 1}))
    monkeypatch.setattr(uia, "window", lambda title: win)
    monkeypatch.setattr(uia, "focus", lambda w: None)
    keys = []
    monkeypatch.setattr(pyautogui, "hotkey", lambda *k: keys.append(k))
    monkeypatch.setattr(computer, "_type_text", lambda t: typed.append(t))
    monkeypatch.setattr(pcverbs.time, "sleep", lambda s: None)
    return keys


def test_save_never_presses_ctrl_s_on_a_document_the_owner_did_not_name(monkeypatch, tmp_path):
    """2026-11-06: the text landed in a restored README tab; Ctrl+S there saves over that file without any dialog."""
    keys = _fake_notepad(monkeypatch, ["*README.TXT - Notepad"], [])
    monkeypatch.setattr(pcverbs, "_in_user_space", lambda p: True)
    with pytest.raises(RuntimeError, match="did not press Save"):
        pcverbs.save_document({"app": "notepad", "path": str(tmp_path / "hello.txt")})
    assert keys == [], "no Ctrl+S was sent"


def test_save_is_allowed_on_the_new_document_jarvis_typed_into(monkeypatch, tmp_path):
    """Notepad renamed the new tab to 'hello world' (its first line): that is our document, so Save may proceed."""
    keys = _fake_notepad(monkeypatch, ["*hello world - Notepad"], [])
    monkeypatch.setattr(pcverbs, "_in_user_space", lambda p: True)
    pcverbs._started_here(1, "*hello world - Notepad")
    from jarvis_pc.uiact import uia
    monkeypatch.setattr(uia, "_desktop", lambda: type("D", (), {"windows": lambda s: []})())
    with pytest.raises(RuntimeError, match="did not open a Save dialog"):
        pcverbs.save_document({"app": "notepad", "path": str(tmp_path / "hello.txt")})
    assert keys == [("ctrl", "s")], "Save was pressed on our own new document"
    pcverbs._OURS.clear()


def test_typing_refuses_when_no_new_tab_opens_over_a_foreign_document(monkeypatch):
    from core import pcbridge
    typed = []
    keys = _fake_notepad(monkeypatch, ["README.TXT - Notepad"], typed)        # the title never becomes Untitled
    with pcbridge.user_message("owner", "open notepad and type hello world"), pytest.raises(RuntimeError, match="typed nothing"):
        pcverbs.type_text({"app": "notepad", "text": "hello world"})
    assert keys == [("ctrl", "n")] and typed == []


def test_typing_stops_when_the_document_in_front_changes_under_it(monkeypatch):
    from core import pcbridge
    typed = []
    monkeypatch.setattr(pcverbs, "_editable", lambda win: type("C", (), {"element_info": None, "set_focus": lambda s: None})())
    # the front document is Untitled when typing starts, README after: the text went to the wrong tab
    _fake_notepad(monkeypatch, ["Untitled - Notepad", "Untitled - Notepad", "*README.TXT - Notepad"], typed)
    with pcbridge.user_message("owner", "type hello world into notepad"), pytest.raises(RuntimeError, match="somewhere unexpected"):
        pcverbs.type_text({"app": "notepad", "text": "hello world"})


def test_notepad_naming_a_new_tab_after_its_first_line_is_the_same_document():
    assert pcverbs._same_document("Untitled - Notepad", "*hello world - Notepad", "hello world")
    assert pcverbs._same_document("Untitled - Notepad", "*A long first line that Notepad cuts… - Notepad", "A long first line that Notepad cuts short here")
    assert pcverbs._same_document("*notes.txt - Notepad", "*notes.txt - Notepad", "x")
    assert pcverbs._same_document("Untitled - Notepad", "*CUsersyouDownloadsjarvis-test - Notepad", r"C:\Users\you\Downloads\jarvis-test")
    assert not pcverbs._same_document("Untitled - Notepad", "*README.TXT - Notepad", "hello world")
    assert not pcverbs._same_document("notes.txt - Notepad", "*README.TXT - Notepad", "hello world")


def test_a_notepad_still_loading_or_named_only_by_the_app_counts_as_foreign():
    """2026-11-06 (second rerun): the title read at launch was 'Notepad', and 'notepad' is in every such request, so the
    restored README tab was taken for the owner's own document."""
    from core import pcbridge
    with pcbridge.user_message("owner", "Open Notepad, type hello world, and save it as hello.txt."):
        assert pcverbs._foreign_document("notepad", "Notepad")
        assert pcverbs._foreign_document("notepad", "Notepad - Notepad")
        assert not pcverbs._foreign_document("notepad", "hello.txt - Notepad")


def test_a_big_tree_in_one_place_cannot_hide_a_project_in_another(monkeypatch, tmp_path):
    """2026-11-06: a throwaway project under Documents was never reached after the profile's big trees used the budget."""
    big, docs = tmp_path / "big", tmp_path / "docs"
    for i in range(40):
        (big / f"d{i}").mkdir(parents=True)
        (big / f"d{i}" / "x.txt").write_text("x")
    proj = docs / "JarvisAcceptance" / "Quillmere Atlas"
    proj.mkdir(parents=True)
    (proj / "QuillmereAtlas.blend").write_text("x")
    monkeypatch.setattr(projects, "MAX_ENTRIES", 30)
    found = projects.find("my Quillmere Atlas project", [str(big), str(docs)])
    assert found and found[0].path.endswith("QuillmereAtlas.blend")


def test_what_speech_recognition_wrote_is_made_to_mean_what_exists(tmp_path):
    """Voice acceptance 2026-11-06: 'JarvisAcceptance' was heard 'Jarvis acceptance', 'hello.txt' as 'Hello TXT'."""
    real = tmp_path / "JarvisAcceptance"
    real.mkdir()
    assert pcverbs.spoken_path(str(tmp_path / "Jarvis acceptance")) == str(real)
    assert pcverbs.spoken_path(str(tmp_path / "Jarvis acceptance" / "reports")) == str(real / "reports"), "the last part may be new"
    assert pcverbs.spoken_path(str(tmp_path / "Nothing like it" / "x")) == str(tmp_path / "Nothing like it" / "x")
    cmd = f'mkdir "{tmp_path}\\Jarvis acceptance\\reports"'
    assert pcverbs.heard("command", cmd) == f'mkdir "{real}\\reports"'
    assert pcverbs.spoken_file_name("Hello TXT") == "Hello.txt" and pcverbs.spoken_file_name("notes dot md") == "notes.md"
    assert pcverbs.spoken_file_name("report.txt") == "report.txt" and pcverbs.spoken_file_name("txt") == "txt"
    (tmp_path / "Jarvis-Acceptance").mkdir()
    assert pcverbs.spoken_path(str(tmp_path / "jarvis acceptance")) == str(tmp_path / "jarvis acceptance"), "two look-alikes: no guess"


def test_a_close_spelling_finds_the_project_and_says_so(tmp_path):
    proj = tmp_path / "Quillmere Atlas"
    proj.mkdir()
    (proj / "QuillmereAtlas.blend").write_text("x")
    (tmp_path / "Other").mkdir()
    (tmp_path / "Other" / "Harbor.blend").write_text("x")
    found = projects.find("my Quilmere Atlas project", [str(tmp_path), str(tmp_path)])      # reached from two places
    assert len(found) == 1 and found[0].path.endswith("QuillmereAtlas.blend") and "close spelling" in found[0].why[0]
    assert projects.find("my Zebra Canyon project", [str(tmp_path)]) == [], "a different name is not a close spelling"


def test_the_context_names_existing_folders_the_request_says(monkeypatch, tmp_path):
    from core import gateway_context
    (tmp_path / "Documents" / "JarvisAcceptance").mkdir(parents=True)
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Projects").mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)
    got = gateway_context.folders_mentioned("create a folder called reports inside my Jarvis acceptance folder in Documents")
    assert got == [str(tmp_path / "Documents" / "JarvisAcceptance")], "the place itself (Documents) is not listed again"
    assert gateway_context.folders_mentioned("open paint") == []


def test_a_directory_listing_is_spoken_as_names():
    from tools import pcskills
    table = ("\n    Directory: C:\\x\n\n\nMode                 LastWriteTime         Length Name\n"
             "----                 -------------         ------ ----\n"
             "d-----         11/6/2026   4:51 PM                Quillmere Atlas\n"
             "-a----         11/6/2026   4:51 PM              5 notes-alpha.txt\n")
    assert pcskills._spoken("list the files in C:\\x", table) == "List the files in C:\\x: Quillmere Atlas, notes-alpha.txt."
    assert pcskills._spoken("what is the date", "Friday") == "What is the date: Friday."


def test_focus_app_takes_the_program_with_or_without_exe():
    from core import pcactions
    assert pcactions.exe_name("notepad") == pcactions.exe_name("Notepad.exe") == pcactions.exe_name(" NOTEPAD ") == "notepad.exe"
    assert pcactions.exe_name("") == ""


def test_a_folder_in_the_profile_is_named_by_its_part_below_home():
    """'Open my Downloads folder' planned as open_path C:\\Users\\me\\Downloads asked, because 'users' and the account name
    are never in the sentence. The part below home is what the owner names; the profile itself still asks."""
    from core import authz
    home = os.path.expanduser("~")
    said = "Open my Downloads folder and create a folder called jarvis-test in it"
    assert not authz.decide([{"tool": "open_path", "arguments": {"path": home + "\\Downloads"}}], said).ask
    assert authz.decide([{"tool": "open_path", "arguments": {"path": home + "\\Pictures"}}], said).ask
    assert authz.decide([{"tool": "open_path", "arguments": {"path": home}}], said).ask
    assert authz.below_home(home + "\\Documents\\A b") == "Documents\\A b" and authz.below_home(r"D:\x") == r"D:\x"
    heard = "create a folder called reports inside my Jarvis acceptance folder in Documents"
    assert not authz.decide([{"tool": "open_path", "arguments": {"path": home + "\\Documents\\JarvisAcceptance"}}], heard).ask


def test_a_stored_skill_is_judged_by_the_command_that_would_really_run():
    from types import SimpleNamespace
    from core import gateway
    step = SimpleNamespace(command="Get-ChildItem -Path {dir} -Recurse -File | Sort-Object Length -Descending | Select-Object -First 1")
    book = SimpleNamespace(params=["dir"], steps=[step])
    home = os.path.expanduser("~")
    assert gateway._filled_tier(book, {"dir": home + "\\Downloads"}) == 0
    assert gateway._filled_tier(book, {}) == 2, "a missing value asks"
    writes = SimpleNamespace(params=["dir"], steps=[SimpleNamespace(command="Remove-Item -Path {dir} -Recurse")])
    assert gateway._filled_tier(writes, {"dir": home + "\\Downloads\\x"}) >= 1, "anything above read-only still asks"


def test_a_skill_that_changes_the_pc_must_do_only_what_was_asked():
    from types import SimpleNamespace
    from core import gateway
    home = os.path.expanduser("~")
    note = SimpleNamespace(id="job_make_note", title="create the folder {dir} and put a file note.txt in it that says {word}",
                           book=SimpleNamespace(params=["dir", "word"], steps=[
                               SimpleNamespace(command="New-Item -Path {dir} -ItemType Directory"),
                               SimpleNamespace(command='Set-Content -Path "{dir}\\note.txt" -Value {word}')]))
    said = "create a folder called reports inside my JarvisAcceptance folder in Documents"
    extra = gateway._changes_more_than_asked(note, {"dir": home + "\\Documents\\x", "word": "reports"}, said)
    assert "note" in extra and "put" in extra
    assert gateway._changes_more_than_asked(note, {"dir": home + "\\x", "word": "hi"},
                                            "create the folder x and put a file note.txt in it that says hi") == []
    biggest = SimpleNamespace(id="job_biggest_folder", title="which folder directly inside {dir} holds the biggest file",
                              book=SimpleNamespace(params=["dir"], steps=[SimpleNamespace(command="Get-ChildItem -Path {dir} -Recurse -File")]))
    assert gateway._changes_more_than_asked(biggest, {"dir": home + "\\Downloads"}, "what's hogging space in my downloads") == [], \
        "a read-only skill may be a paraphrase"


def test_an_invented_profile_becomes_the_real_home_and_a_real_other_profile_is_left_alone(tmp_path):
    home = os.path.expanduser("~")
    assert pcverbs.real_home_path(r"mkdir C:\Users\Owner\Downloads\jarvis-test") == f"mkdir {home}\\Downloads\\jarvis-test"
    assert pcverbs.real_home_path(r"C:\Users\Public\Documents\x") == r"C:\Users\Public\Documents\x"
    assert pcverbs.real_home_path(home + r"\Documents\a.txt") == home + r"\Documents\a.txt"
    assert pcverbs.real_home_path("type hello world") == "type hello world"


def test_find_files_knows_the_usual_folders_and_stays_in_user_space(monkeypatch, tmp_path):
    (tmp_path / "a.pdf").write_text("x")
    monkeypatch.setattr(pcverbs, "_in_user_space", lambda p: True)
    out = pcverbs.find_files({"folder": str(tmp_path), "pattern": "*.pdf"})
    assert out["verified"] and "a.pdf" in out["detail"]
    with pytest.raises(RuntimeError, match="no folder"):
        pcverbs.find_files({"folder": str(tmp_path / "nope"), "pattern": "*"})


def test_user_space_means_the_profile_minus_appdata_and_the_other_drives():
    home = os.path.expanduser("~")
    assert pcverbs._in_user_space(os.path.join(home, "Documents", "x.txt"))
    assert not pcverbs._in_user_space(os.path.join(home, "AppData", "Local", "x.txt"))
    assert not pcverbs._in_user_space(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "notepad.exe"))
    # another drive counts only when it is one of the owner's FIXED drives (explicit roots; PR #216 review)
    assert pcverbs._in_user_space(r"D:\Projects\thing.sln") == ("D:\\" in pcverbs.user_roots())
