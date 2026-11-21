"""School subjects for a spreadsheet application (blueprint area 17), graded against the file Calc saves.

Each attempt writes a small flat-ODS spreadsheet into the sandbox and launches LibreOffice Calc on it with its OWN throw-away profile
(so nothing of the person's LibreOffice settings or recent files is touched, and the school's windows are the only ones this process
ever acts on). The teacher drives the window with the UI action language; the saved file, parsed by the harness, is the ground truth.
Cleanup ends only the soffice processes that were started with the school's profile folder, and never answers a save prompt.

Only available when LibreOffice Calc is installed and pywinauto works; elsewhere these subjects have no ground truth and are skipped.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

from .curriculum import Context, Subject, Task, HIGH_SCHOOL
from .ui_subjects import available as _ui_available

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
PROFILE_TAG = "jarvis_school_lo_profile"
_CANDIDATES = (r"C:\Program Files\LibreOffice\program\scalc.exe", r"C:\Program Files (x86)\LibreOffice\program\scalc.exe")


def calc_path() -> str:
    for p in _CANDIDATES:
        if os.path.isfile(p):
            return p
    return ""


def available() -> bool:
    return bool(_ui_available() and calc_path())


def _none(ask: str, params: dict) -> Task:
    return Task(ask, params, [], None, 1, kind="ui")


def _cell(kind: str, value: str, text: str = "") -> str:
    p = f"<text:p>{text or value}</text:p>"
    if kind == "float":
        return f'<table:table-cell office:value-type="float" office:value="{value}">{p}</table:table-cell>'
    return f'<table:table-cell office:value-type="string">{p}</table:table-cell>'


def _fods(rows: List[List[Tuple[str, str]]]) -> str:
    body = "".join("<table:table-row>" + "".join(_cell(k, v) for k, v in row) + "</table:table-row>" for row in rows) or "<table:table-row><table:table-cell/></table:table-row>"
    head = " ".join(f'xmlns:{k}="{v}"' for k, v in _NS.items())
    return (f'<?xml version="1.0" encoding="UTF-8"?><office:document {head} office:version="1.3" '
            f'office:mimetype="application/vnd.oasis.opendocument.spreadsheet"><office:body><office:spreadsheet>'
            f'<table:table table:name="Sheet1">{body}</table:table></office:spreadsheet></office:body></office:document>')


def read_grid(path: str) -> List[List[str]]:
    """The saved sheet as rows of text, with numbers as the stored value. Empty on any problem."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    out: List[List[str]] = []
    for row in root.iter(f"{{{_NS['table']}}}table-row"):
        cells: List[str] = []
        for cell in row.findall(f"{{{_NS['table']}}}table-cell"):
            rep = int(cell.get(f"{{{_NS['table']}}}number-columns-repeated", "1"))
            val = cell.get(f"{{{_NS['office']}}}value")
            if val is None:
                val = "".join(("".join(p.itertext())) for p in cell.findall(f"{{{_NS['text']}}}p"))
            cells.extend([val] * min(rep, 50))
        rrep = int(row.get(f"{{{_NS['table']}}}number-rows-repeated", "1"))
        out.extend([cells] * min(rrep, 50))
    return out


def _profile_dir() -> str:
    return os.path.join(tempfile.gettempdir(), PROFILE_TAG)


def _school_processes():
    import psutil
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if PROFILE_TAG in " ".join(p.info["cmdline"] or []):
                yield p
        except Exception:  # noqa: BLE001
            continue


def _stop_school_calc() -> None:
    """End only the LibreOffice processes started with the school's profile. Nothing is saved or answered."""
    for p in list(_school_processes()):
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(1.0)
    for p in list(_school_processes()):
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def _spawn(c: Context, rows: List[List[Tuple[str, str]]]) -> Tuple[str, str]:
    """(window title, file path) of a fresh Calc window the school opened on a new sheet, or ("", path) when it could not."""
    path = os.path.join(c.folder(c.word(6)), c.word(6) + ".fods")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_fods(rows))
    _stop_school_calc()                                   # an earlier attempt's leftover, never anybody else's window
    prof = _profile_dir().replace("\\", "/")
    subprocess.Popen([calc_path(), f"-env:UserInstallation=file:///{prof}", "--norestore", "--nologo", "--nofirststartwizard", path],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c.on_cleanup(_stop_school_calc)                       # registered at launch: a window that opens late must still be ended
    name = os.path.basename(path)
    try:
        from pywinauto import Desktop
        for _ in range(120):
            time.sleep(0.5)
            for w in Desktop(backend="uia").windows():
                t = w.window_text() or ""
                if t.startswith(name) and "Calc" in t:
                    time.sleep(2.5)                       # let the sheet finish loading before the first key
                    return t, path
    except Exception:  # noqa: BLE001
        pass
    return "", path


def _type_save(c: Context) -> Task:
    ask = "type {text} into the first cell of the open spreadsheet, press Enter, and save the file"
    if not available():
        return _none(ask, {"text": "x"})
    title, path = _spawn(c, [])
    text = c.word(6)
    if not title:
        return Task(ask, {"text": text}, [], None, 1, kind="ui")

    def check():
        grid = read_grid(path)
        got = grid[0][0] if grid and grid[0] else ""
        return got == text, "cell A1 holds the typed text" if got == text else f"expected {text!r} in A1, found {got!r}"
    return Task(ask, {"text": text}, [], check, 1, kind="ui", ui_title=title, ui_owned=True)


def _sum_below(c: Context) -> Task:
    ask = "in the open spreadsheet put the total of the two numbers in column A into the cell below them, and save the file"
    if not available():
        return _none(ask, {})
    a, b = c.rng.randint(11, 99), c.rng.randint(11, 99)
    title, path = _spawn(c, [[("float", str(a))], [("float", str(b))]])
    if not title:
        return Task(ask, {}, [], None, 1, kind="ui")

    def check():
        grid = read_grid(path)
        got = grid[2][0] if len(grid) > 2 and grid[2] else ""
        try:
            ok = abs(float(got) - (a + b)) < 1e-9
        except ValueError:
            ok = False
        return ok, "cell A3 holds the total" if ok else f"expected {a + b} in A3, found {got!r}"
    return Task(ask, {}, [], check, 1, kind="ui", ui_title=title, ui_owned=True)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    mk = lambda i, t, f, al, **k: Subject(i, t, hs, f, al, kind="ui", needs_windows=True, **k)  # noqa: E731
    return [
        mk("app_calc_type_save", "type into a spreadsheet cell and save", _type_save, ("spreadsheet entry", "calc cell"), needs=("ui_hotkey_save", "ui_fill_form")),
        mk("app_calc_sum_below", "total a column with a formula and save", _sum_below, ("spreadsheet sum", "calc formula"), needs=("app_calc_type_save",)),
    ]
