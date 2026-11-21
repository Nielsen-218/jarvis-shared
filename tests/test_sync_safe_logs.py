"""Files both machines append to must have one writer each, or Syncthing silently drops one side's rows."""
from core import intelligence, journal, pcbridge, storage


def test_each_machine_writes_its_own_journal_and_reads_both(monkeypatch):
    storage.save(journal.JOURNAL, [{"id": "old1", "t": "2026-10-01T09:00", "summary": "legacy entry", "open": []}])
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    journal.archive_local("owner", [{"role": "user", "content": "plan the garage paint job"}])
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    journal.archive_local("owner", [{"role": "user", "content": "order printer ink"}])
    assert "garage" in storage.load("journal-pc.json", [])[-1]["summary"]
    assert "printer ink" in storage.load("journal-server.json", [])[-1]["summary"]
    assert len(storage.load(journal.JOURNAL, [])) == 1                        # the shared file is never written
    texts = " | ".join(e["summary"] for e in journal.entries())
    assert "legacy entry" in texts and "garage" in texts and "printer ink" in texts
    assert journal.search("garage")[0]["summary"].endswith("plan the garage paint job")


def test_tier_audit_is_per_machine_and_merged(monkeypatch):
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    intelligence.audit("fast", "received", 1.0)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    intelligence.audit("fast", "local_request_failed", 0.1)
    assert storage.load("intelligence_usage-pc.json", [])[-1]["status"] == "received"
    assert storage.load("intelligence_usage-server.json", [])[-1]["status"] == "local_request_failed"
    assert [r["status"] for r in intelligence.usage_rows()][-2:] == ["received", "local_request_failed"] or \
        {r["status"] for r in intelligence.usage_rows()} >= {"received", "local_request_failed"}
