"""The test suite can never open the owner's live machine-state (2026-11: tests wrote 386 StepTimeout rows into the live
PC-control ledger and taught the method order that working methods fail)."""
import os
from pathlib import Path

import pytest

from core import missions, pcledger, pcnotes, watchdog
from core.config import ROOT

LIVE = (ROOT / "machine-state").resolve()


def _inside_live(path) -> bool:
    p = Path(path).resolve()
    return p == LIVE or LIVE in p.parents


def test_every_state_folder_resolves_to_a_temp_folder_in_a_test():
    for resolved in (pcledger.state_dir(), missions._dir(), watchdog._dir(), pcnotes._path()):
        assert not _inside_live(resolved), f"a test resolved the live state path: {resolved}"


def test_the_ledger_opens_in_the_temp_folder(tmp_path):
    with pcledger.database() as db:
        path = db.execute("PRAGMA database_list").fetchone()[2]
    assert not _inside_live(path)
    assert Path(path).resolve().is_relative_to(tmp_path.resolve())


@pytest.mark.parametrize("name", ["JARVIS_PC_STATE_DIR", "JARVIS_MISSIONS_DIR", "JARVIS_WATCHDOG_DIR"])
def test_a_test_that_removes_the_override_cannot_reach_the_live_folder(monkeypatch, name):
    """Even if a test (or a fixture ordering mistake) deletes the env override, the default path must be refused."""
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("JARVIS_PC_NOTES_FILE", raising=False)
    opener = {"JARVIS_PC_STATE_DIR": pcledger.state_dir, "JARVIS_MISSIONS_DIR": missions._dir,
              "JARVIS_WATCHDOG_DIR": watchdog._dir}[name]
    with pytest.raises(AssertionError, match="live"):
        opener()


def test_the_ledger_refuses_the_live_default_path(monkeypatch):
    monkeypatch.delenv("JARVIS_PC_STATE_DIR", raising=False)
    with pytest.raises(AssertionError, match="live"):
        with pcledger.database():
            pass
