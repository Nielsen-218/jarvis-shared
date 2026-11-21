import pytest


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    from core import vault as v

    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    monkeypatch.setattr(v, "_label", lambda kind, text, note, created: {
        "title": "Brakes quote" if "brake" in text else "Memory", "summary": text[:80],
        "tags": "car, vehicle, brakes, mechanic" if "brake" in text else "", "people": ""})
    monkeypatch.setattr(v, "_transcribe", lambda p: ("We need new brakes, the mechanic quoted 340 dollars.", 31.0))
    return v


def test_record_transcribe_search_and_evidence(vault):
    item = vault.add("owner", "audio", data=b"fake-audio", ext=".webm", mime="audio/webm", background=False)
    assert item["status"] == "ready" and item["seconds"] == 31.0 and vault.file_path(item).read_bytes() == b"fake-audio"
    vault.add("owner", "note", text="Parked on level 3, row F.", background=False)
    hits = vault.search("owner", "what did we say about the vehicle")  # found through its tags, not exact words
    assert [h["id"] for h in hits] == [item["id"]]
    today = item["created"][:10]
    assert len(vault.search("owner", "", start=today)) == 2 and vault.search("owner", "", end="2000-01-01") == []
    assert vault.search("wife", "brakes") == []  # each person only sees their own memories

    from tools import vault as tools

    out = tools.recall_memories("car brakes mechanic")
    assert f"[{item['id']}] audio today at" in out and "0:31 long" in out and "340 dollars" in out
    assert "Parked on level 3" in tools.recall_memories("parked level")
    assert "Nothing in the memory vault" in tools.recall_memories("passport")
    assert vault.delete(item["id"], "owner") and vault.file_path(item) is None and vault.get(item["id"]) is None


def test_phone_upload_note_and_file_routes(vault, monkeypatch):
    from fastapi.testclient import TestClient

    from core import appserver

    token = appserver.add_device("owner", "phone")
    h = {"Authorization": f"Bearer {token}"}
    c = TestClient(appserver.AppServer(lambda uid: None).app)
    real_add = vault.add
    monkeypatch.setattr(vault, "add", lambda *a, **k: real_add(*a, **{**k, "background": False}))
    r = c.post("/api/memory/upload", headers=h, data={"kind": "audio", "note": "car talk"},
               files={"file": ("memory.webm", b"x" * 3000, "audio/webm")})
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["title"] == "Brakes quote" and item["note"] == "car talk"
    assert c.post("/api/memory/note", headers=h, json={"text": "Gate code 4512"}).status_code == 200
    assert [i["kind"] for i in c.get("/api/memory/search", headers=h).json()["items"]] == ["note", "audio"]
    f = c.get(f"/api/memory/file/{item['id']}?t={token}")
    assert f.status_code == 200 and f.content == b"x" * 3000
    assert c.get(f"/api/memory/file/{item['id']}").status_code in (401, 403)  # no token, no recording
    assert c.post("/api/memory/upload", headers=h, data={"kind": "exe"},
                  files={"file": ("a.exe", b"x", "application/octet-stream")}).status_code == 400
    assert c.get("/api/memory/stats", headers=h).json()["items"] == {"audio": 1, "note": 1}
    assert c.delete(f"/api/memory/item/{item['id']}", headers=h).json()["ok"] is True
