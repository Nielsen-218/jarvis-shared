"""The owner's corrections (core/corrections.py): queue order, daily cap, provenance, and validation."""
import json
from datetime import datetime

import pytest

from core import corrections, storage


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    (tmp_path / "users" / "owner").mkdir(parents=True)
    (tmp_path / "usage.json").write_text(json.dumps({"2026-11-08": {"cost": 0.39, "requests": 7}}), encoding="utf-8")
    return tmp_path


def _t(ts_day, hhmm, said, kind="open", outcome="done", first_try=True, **kw):
    ts = datetime.fromisoformat(f"{ts_day}T{hhmm}:00").timestamp()
    return {"ts": ts, "host": "pc", "channel": "voice", "said": said, "kind": kind, "outcome": outcome,
            "reply_head": f"re: {said}", "first_try": first_try, **kw}


TURNS = [
    _t("2026-11-15", "10:00", "Open calculator."),
    _t("2026-11-15", "10:01", "Minimize Clot.", kind="minimize", outcome="not_understood", first_try=False, mishear=True),
    _t("2026-11-15", "10:02", "I think Blade Runner is better than Alien.", kind="other", outcome="", first_try=None),
    _t("2026-11-08", "17:30", "What's on my screen?", kind="other", outcome="", first_try=None),
]
NOW = datetime.fromisoformat("2026-11-16T12:00:00").timestamp()


def test_queue_puts_mistakes_first_and_caps_the_day(data):
    q = corrections.queue("owner", n=2, now=NOW, turns=TURNS)
    assert [i["said"] for i in q["items"]] == ["Minimize Clot.", "Open calculator."]
    assert q["left_today"] == 2 and q["waiting"] == 4


def test_reviewed_lines_leave_the_queue_and_count_against_today(data):
    k = corrections.key(TURNS[1])
    corrections.record("owner", {"key": k, "verdict": "wrong", "meant_kind": "minimize", "meant": "minimize Claude"},
                       turns=TURNS, now=NOW)
    q = corrections.queue("owner", n=2, now=NOW, turns=TURNS)
    assert k not in [i["key"] for i in q["items"]]
    assert q["left_today"] == 1


def test_provenance_owner_local_and_unknown(data):
    wrong = corrections.record("owner", {"key": corrections.key(TURNS[1]), "verdict": "wrong", "meant_kind": "minimize",
                                         "meant": "minimize Claude"}, turns=TURNS, now=NOW)
    corrections.record("owner", {"key": corrections.key(TURNS[0]), "verdict": "right"}, turns=TURNS, now=NOW)
    corrections.record("owner", {"key": corrections.key(TURNS[3]), "verdict": "right"}, turns=TURNS, now=NOW)
    rows = {r["said"]: r for r in corrections.reviews("owner")}
    assert rows["Minimize Clot."]["provenance"] == {"input": "owner", "target": "owner"}
    assert rows["Minimize Clot."]["meant"] == {"kind": "minimize", "target": "minimize Claude"}
    assert rows["Open calculator."]["provenance"]["target"] == "jarvis_local"     # no cloud use that day
    assert rows["What's on my screen?"]["provenance"]["target"] == "unknown"      # a cloud model answered that day
    assert wrong["ok"]
    s = corrections.stats("owner")
    assert (s["reviewed"], s["right"], s["wrong"]) == (3, 2, 1)


@pytest.mark.parametrize("body,msg", [
    ({"verdict": "maybe"}, "Pick right"),
    ({"verdict": "wrong", "meant_kind": "teleport", "meant": "x"}, "Pick what you meant"),
    ({"verdict": "wrong", "meant_kind": "open", "meant": "  "}, "Say what you meant"),
])
def test_bad_input_is_refused(data, body, msg):
    with pytest.raises(ValueError, match=msg):
        corrections.record("owner", {"key": corrections.key(TURNS[0]), **body}, turns=TURNS, now=NOW)


def test_no_double_review_and_unknown_line(data):
    k = corrections.key(TURNS[0])
    corrections.record("owner", {"key": k, "verdict": "skip"}, turns=TURNS, now=NOW)
    with pytest.raises(ValueError, match="already"):
        corrections.record("owner", {"key": k, "verdict": "right"}, turns=TURNS, now=NOW)
    with pytest.raises(ValueError, match="isn't in the log"):
        corrections.record("owner", {"key": "nope", "verdict": "right"}, turns=TURNS, now=NOW)


def test_each_machine_writes_its_own_file_and_reads_all(data, monkeypatch):
    monkeypatch.setattr(corrections, "_host", lambda: "pc")
    corrections.record("owner", {"key": corrections.key(TURNS[0]), "verdict": "right"}, turns=TURNS, now=NOW)
    monkeypatch.setattr(corrections, "_host", lambda: "server")
    corrections.record("owner", {"key": corrections.key(TURNS[1]), "verdict": "skip"}, turns=TURNS, now=NOW)
    folder = data / "users" / "owner"
    assert sorted(p.name for p in folder.glob("corrections*.jsonl")) == ["corrections-pc.jsonl", "corrections-server.jsonl"]
    assert len(corrections.reviews("owner")) == 2
    # a line reviewed on one machine is not offered again on the other
    with pytest.raises(ValueError, match="already"):
        corrections.record("owner", {"key": corrections.key(TURNS[0]), "verdict": "wrong", "meant_kind": "open", "meant": "x"},
                           turns=TURNS, now=NOW)
    (folder / "corrections-pc.sync-conflict-20261116-1.jsonl").write_text('{"key": "zzz", "verdict": "right"}\n')
    assert len(corrections.reviews("owner")) == 2


def test_missing_usage_file_is_treated_as_cloud_used(data):
    (data / "usage.json").unlink()
    corrections.record("owner", {"key": corrections.key(TURNS[0]), "verdict": "right"}, turns=TURNS, now=NOW)
    assert corrections.reviews("owner")[0]["provenance"]["target"] == "unknown"
