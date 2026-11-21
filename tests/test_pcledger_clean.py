"""scripts/pcledger_clean.py: dry run by default, counts only; --apply backs up and removes only the suite's rows."""
import json
import sqlite3
import time

from core import pcledger
from scripts import pcledger_clean as clean

STEP = clean.TEST_STEPS["focus_app"]


def _row(db, component, at, **d):
    db.execute("INSERT INTO usage VALUES(?,?,?,?,?)", (f"id{at}{d.get('error_type')}{d.get('tool')}", "owner", component, json.dumps(d), at))


def _seed(tmp_path):
    path = tmp_path / "pc-control" / "ledger.sqlite"
    now = 1_700_000_000.0
    with pcledger.database() as db:
        # a test timeout (frames name the test file), its follow-on RuntimeError, and the methods failure it taught
        _row(db, "local_automation", now, tool="focus_app", method="timeout", verified=False, error_type="StepTimeout",
             where="test_antistuck.py:133 <lambda> <- pcmethods.py:155 <lambda>")
        _row(db, "local_automation", now + 0.1, tool="focus_app", method="native", verified=False, error_type="RuntimeError")
        # the run_terminal test: no frames, same run
        _row(db, "local_automation", now + 30, tool="run_terminal", method="timeout", verified=False, error_type="StepTimeout",
             where="ended after the stop")
        # a REAL timeout, far from any test run
        _row(db, "local_automation", now + 90000, tool="focus_app", method="timeout", verified=False, error_type="StepTimeout",
             where="uia.py:88 find <- pcactions.py:900 focus")
        _row(db, "local_automation", now + 90000.1, tool="focus_app", method="native", verified=False, error_type="RuntimeError")
        _row(db, "local_automation", now + 100, tool="open_app", method="native", verified=True)
        fp = pcledger.fingerprint(STEP)
        db.execute("INSERT INTO methods VALUES('owner',?,?,0,2,0,?)", (fp, "native", now + 90000))   # one test, one real failure
    return path, now


def _counts(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT count(*) FROM usage").fetchone()[0], db.execute("SELECT failures,last_failure FROM methods").fetchall()
    finally:
        db.close()


def test_dry_run_is_the_default_and_changes_nothing(tmp_path, capsys):
    path, _ = _seed(tmp_path)
    before = path.read_bytes()
    assert clean.main(["--ledger", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run" and out["wrote_nothing"] is True
    assert out["attributed_timeouts"] == 1 and out["inferred_timeouts"] == 1 and out["follow_on_rows"] == 1
    assert out["rows_to_remove"] == 2          # inferred ones need --include-inferred
    assert path.read_bytes() == before and not list(path.parent.glob("*.bak-*"))
    assert "test_antistuck" not in json.dumps(out)       # counts only


def test_apply_backs_up_then_removes_only_the_test_rows_and_fixes_methods(tmp_path, capsys):
    path, now = _seed(tmp_path)
    assert clean.main(["--ledger", str(path), "--apply", "--include-inferred"]) == 0
    out = json.loads(capsys.readouterr().out)
    backups = list(path.parent.glob("ledger.sqlite.bak-*"))
    assert len(backups) == 1 and out["backup"] == str(backups[0])
    assert _counts(backups[0])[0] == 6                    # the backup is the file as it was
    rows, methods = _counts(path)
    assert rows == 3                                      # real timeout + its RuntimeError + the open_app row stay
    assert methods == [(1, now + 90000)]                  # the one test failure is gone, the real one and its cool-down stay


def test_a_methods_row_made_only_of_test_failures_is_deleted(tmp_path):
    path, now = _seed(tmp_path)
    db = sqlite3.connect(path)
    db.execute("UPDATE methods SET failures=1, last_failure=?", (now,))
    db.commit()
    db.close()
    clean.apply(path, 120.0, False)
    assert _counts(path)[1] == []


def test_without_include_inferred_the_frameless_timeout_stays(tmp_path):
    path, _ = _seed(tmp_path)
    clean.apply(path, 120.0, False)
    db = sqlite3.connect(path)
    kinds = db.execute("SELECT json_extract(data,'$.tool') FROM usage WHERE json_extract(data,'$.error_type')='StepTimeout'").fetchall()
    db.close()
    assert ("run_terminal",) in kinds
