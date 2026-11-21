"""The planner's view of the PC: read-only, bounded, honest when it cannot look."""
from core import pcstate


def rows():
    return [{"hwnd": 1, "title": "Untitled - Paint", "process": "mspaint.exe", "pid": 1, "foreground": False, "minimized": True},
            {"hwnd": 2, "title": "Apex Legends", "process": "r5apex.exe", "pid": 2, "foreground": True, "minimized": False},
            {"hwnd": 3, "title": "Spotify  Premium", "process": "Spotify.exe", "pid": 3, "foreground": False, "minimized": False},
            {"hwnd": 4, "title": "hidden host", "process": "TextInputHost.exe", "pid": 4, "foreground": False, "minimized": False}]


def test_describe_names_the_front_window_the_open_ones_and_the_running_catalogue_apps(monkeypatch):
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda visible_only=True: rows())
    text = pcstate.describe()
    assert text.startswith("PC STATE (read just now):")
    assert "in front: r5apex (Apex Legends)" in text
    assert "mspaint: Untitled - Paint [minimized]" in text and "Spotify: Spotify Premium" in text
    assert "known apps running: paint, spotify" in text
    assert "TextInputHost" not in text, "shell helper windows are noise"


def test_a_desktop_that_cannot_be_read_is_reported_not_invented(monkeypatch):
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda visible_only=True: (_ for _ in ()).throw(OSError("no desktop")))
    assert pcstate.describe() == "PC STATE: no visible windows (or the desktop could not be read)."


def test_the_window_list_is_bounded(monkeypatch):
    from tools import pcops
    many = [{"hwnd": n, "title": f"Window {n}", "process": "app.exe", "pid": n, "foreground": False, "minimized": False} for n in range(40)]
    monkeypatch.setattr(pcops, "windows", lambda visible_only=True: many)
    assert len(pcstate.snapshot(limit=12)["windows"]) == 12
