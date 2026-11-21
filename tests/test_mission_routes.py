"""Mark VI phase A (2026-11-10): the laptop's worker routes for missions (C2), the missions push (C3). The worker device is
the PC's pairing, named by JARVIS_MISSION_WORKER_DEVICE; phones never claim or finish runs, and the worker never answers
its own approval. Fakes only: no real push is sent."""
import pytest

from core import hud, missions, users
from tests.test_app import client, pair  # noqa: F401


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MISSIONS_DIR", str(tmp_path / "missions"))
    monkeypatch.setattr(hud, "_send", lambda m: None)
    users.set_current("owner")


def _device(client, uid="owner"):
    """Pair a device and return (headers, its device id)."""
    h = pair(client, uid)
    return h, users.get(uid)["devices"][-1]["id"]


@pytest.fixture
def worker(client, monkeypatch):
    h, did = _device(client)
    monkeypatch.setenv("JARVIS_MISSION_WORKER_DEVICE", did)
    return h


def test_a_phone_cannot_use_the_worker_routes(client, worker):
    phone, _ = _device(client)
    assert client.post("/api/missions/worker/claim", json={"machine": "pc"}, headers=phone).status_code == 403
    assert client.post("/api/missions/worker/abc/finish", json={"outcome": "done"}, headers=phone).status_code == 403


def test_with_no_worker_device_set_nobody_may_claim(client, monkeypatch):
    h, _ = _device(client)
    monkeypatch.delenv("JARVIS_MISSION_WORKER_DEVICE", raising=False)
    assert client.post("/api/missions/worker/claim", json={}, headers=h).status_code == 403


def test_a_household_member_gets_403(client, monkeypatch):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    h, did = _device(client, wife)
    monkeypatch.setenv("JARVIS_MISSION_WORKER_DEVICE", did)              # even if her phone were named the worker
    assert client.post("/api/missions/worker/claim", json={}, headers=h).status_code == 403


def test_the_worker_cannot_answer_its_own_approval(client, worker):
    m = missions.add("update the Nvidia driver")
    got = client.post("/api/missions/worker/claim", json={"machine": "pc"}, headers=worker).json()
    assert got["mission"]["id"] == m["id"] and got["paused"] is False
    run = got["mission"]["run"]
    r = client.post(f"/api/missions/worker/{run}/park", json={"step": {"tool": "install"}, "preview": "Install?",
                                                              "checkpoint": {"done_clauses": [0]}}, headers=worker)
    assert r.status_code == 200 and r.json()["mission"]["state"] == "waiting_approval"
    assert client.post(f"/api/missions/{m['id']}/answer", json={"approved": True, "fp": missions.get(m["id"])["approval"]["fp"]}, headers=worker).status_code == 403
    phone, _ = _device(client)
    assert client.post(f"/api/missions/{m['id']}/answer", json={"approved": True, "fp": missions.get(m["id"])["approval"]["fp"]}, headers=phone).json()["mission"]["state"] == "queued"


def test_the_worker_round_trip_and_bad_input(client, worker):
    missions.add("report disk space")
    run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    assert client.post(f"/api/missions/worker/{run}/heartbeat", json={"progress": "disk"}, headers=worker).json() == \
        {"state": "running", "paused": False}
    assert client.post(f"/api/missions/worker/{run}/park", json={"step": "nope"}, headers=worker).status_code == 400
    assert client.post(f"/api/missions/worker/{run}/finish", json={"outcome": "cancelled"}, headers=worker).status_code == 400
    assert client.post("/api/missions/worker/nope/finish", json={"outcome": "done"}, headers=worker).status_code == 400
    done = client.post(f"/api/missions/worker/{run}/finish", json={"outcome": "done", "detail": "C: 120 GB free"}, headers=worker)
    assert done.json()["mission"]["state"] == "done" and done.json()["mission"]["changed"] is True
    assert client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"] is None


def test_release_puts_it_back_with_its_checkpoint(client, worker):
    m = missions.add("rig the new character")
    run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    out = client.post(f"/api/missions/worker/{run}/release", json={"checkpoint": {"done_clauses": [0]}}, headers=worker).json()
    assert out["mission"]["state"] == "queued" and out["mission"]["retries"] == 0
    assert missions.get(m["id"])["checkpoint"] == {"done_clauses": [0]}


def test_review_f1_a_heartbeat_can_save_the_checkpoint(client, worker):
    """Review findings 1/18: right after an approved step ran, the worker saves that in the checkpoint at once."""
    m = missions.add("tidy up")
    run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    cp = {"done_clauses": [], "clause": 0, "rest": [], "approved_ran": True}
    assert client.post(f"/api/missions/worker/{run}/heartbeat", json={"progress": "saved", "checkpoint": cp},
                       headers=worker).json()["state"] == "running"
    assert missions.get(m["id"])["checkpoint"] == cp
    assert client.post(f"/api/missions/worker/{run}/heartbeat", json={"checkpoint": "nope"}, headers=worker).status_code == 400


def test_review_f0_the_answer_route_needs_the_fingerprint_he_saw(client, worker):
    m = missions.add("update the Nvidia driver")
    run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    client.post(f"/api/missions/worker/{run}/park", json={"step": {"tool": "install"}, "preview": "Install?"}, headers=worker)
    phone, _ = _device(client)
    assert client.post(f"/api/missions/{m['id']}/answer", json={"approved": True}, headers=phone).status_code == 400
    assert client.post(f"/api/missions/{m['id']}/answer", json={"approved": True, "fp": "stale"}, headers=phone).status_code == 400
    assert missions.get(m["id"])["state"] == "waiting_approval"
    ok = client.post(f"/api/missions/{m['id']}/answer", json={"approved": True, "fp": missions.get(m["id"])["approval"]["fp"]},
                     headers=phone)
    assert ok.json()["mission"]["state"] == "queued"


def test_review_f4_the_pcs_owner_token_acts_on_missions_only_when_relaying_the_owner(client, worker):
    """Review finding 4: the PC's (worker device's) token is the owner's; whoever talks to the PC must not queue, read,
    cancel or pause his missions. The PC marks a relay for the owner himself with for_uid."""
    assert client.post("/api/missions", json={"goal": "uninstall X"}, headers=worker).status_code == 403
    assert client.get("/api/missions", headers=worker).status_code == 403
    assert client.post("/api/missions/pause", json={"paused": True}, headers=worker).status_code == 403
    assert client.post("/api/missions", json={"goal": "x", "for_uid": "someone"}, headers=worker).status_code == 403
    assert missions.listing() == [] and missions.paused() is False
    m = client.post("/api/missions", json={"goal": "tidy downloads", "for_uid": "owner"}, headers=worker).json()["mission"]
    assert client.post(f"/api/missions/{m['id']}/cancel", headers=worker).status_code == 403
    assert client.post(f"/api/missions/{m['id']}/cancel", json={"for_uid": "owner"}, headers=worker).json()["mission"]["state"] == "cancelled"
    assert client.get("/api/missions", params={"for_uid": "owner"}, headers=worker).status_code == 200
    phone, _ = _device(client)                                           # his phone needs no relay marker
    assert client.post("/api/missions", json={"goal": "report disk space"}, headers=phone).status_code == 200


# ---- C3: the missions push ---------------------------------------------------------------------------------------------
@pytest.fixture
def pushes():
    from core import notify
    got = []

    def channel(title, body, uid, category="jarvis", urgent=False, extra=None):
        got.append({"title": title, "body": body, "uid": uid, "category": category, "extra": extra})
    channel.wants_category = channel.wants_extra = True
    notify._channels.append(channel)
    yield got
    notify._channels.remove(channel)


def test_park_pushes_needs_your_ok_with_the_mission_link(client, worker, pushes):
    m = missions.add("update the Nvidia driver")
    run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    client.post(f"/api/missions/worker/{run}/park", json={"step": {"tool": "install"}, "preview": "Install driver 621.14?"}, headers=worker)
    mid = m["id"]
    assert pushes == [{"title": "Needs your OK", "body": "update the Nvidia driver: Install driver 621.14?", "uid": "owner",
                       "category": "missions",
                       "extra": {"kind": "approval", "mission": mid, "tag": "mission-" + mid, "url": "/#mission=" + mid}}]


def test_a_double_finish_pushes_once(client, worker, pushes):
    missions.add("report disk space")
    run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    for _ in range(2):
        client.post(f"/api/missions/worker/{run}/finish", json={"outcome": "done", "detail": "C: 120 GB free"}, headers=worker)
    assert [p["title"] for p in pushes] == ["Mission done"]


def test_the_same_failure_again_within_the_hour_is_not_pushed_twice(client, worker, pushes):
    m = missions.add("clean temp files")
    run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    client.post(f"/api/missions/worker/{run}/finish", json={"outcome": "failed", "detail": "disk locked"}, headers=worker)
    with missions.database() as db:                                     # (a retry of the same mission)
        db.execute("UPDATE missions SET state='queued' WHERE id=?", (m["id"],))
    run2 = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    client.post(f"/api/missions/worker/{run2}/finish", json={"outcome": "failed", "detail": "disk locked"}, headers=worker)
    assert [p["title"] for p in pushes] == ["Mission failed"]


def test_quiet_hours_keep_the_report_in_the_bell_without_pushing(client, worker, monkeypatch):
    import pywebpush

    from core import appserver, notify
    sent = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **k: sent.append(k))
    monkeypatch.setattr(notify, "in_quiet_hours", lambda p, now=None: True)
    devices = users.get("owner").get("devices", []) + [{"id": "p1", "token_hash": "x", "name": "phone", "push": {"endpoint": "https://e"}}]
    users.update("owner", devices=devices)
    notify._channels.append(appserver._push_channel)
    try:
        missions.add("report disk space")
        run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
        client.post(f"/api/missions/worker/{run}/finish", json={"outcome": "done", "detail": "C: 120 GB free"}, headers=worker)
    finally:
        notify._channels.remove(appserver._push_channel)
    assert sent == []
    row = notify.history("owner")[0]
    assert row["title"] == "Mission done" and row["category"] == "missions" and row["pushed"] is False


def test_the_push_payload_carries_the_mission_tag_and_url(monkeypatch):
    import json

    import pywebpush

    from core import appserver
    sent = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **k: sent.append(json.loads(k["data"])))
    monkeypatch.setattr(appserver.notify, "allowed", lambda *a, **k: True)
    users.update("owner", devices=[{"id": "p1", "token_hash": "x", "name": "phone", "push": {"endpoint": "https://e"}}])
    appserver.push("Needs your OK", "x", "owner", "missions", extra={"kind": "approval", "mission": "0123456789ab",
                   "tag": "mission-0123456789ab", "url": "/#mission=0123456789ab", "image": "https://evil"})
    appserver.push("Timer done", "y", "owner", "reminders")
    assert sent[0] == {"title": "Needs your OK", "body": "x", "category": "missions", "kind": "approval",
                       "mission": "0123456789ab", "tag": "mission-0123456789ab", "url": "/#mission=0123456789ab"}
    assert sent[1]["tag"] == "jarvis-reminders" and sent[1]["url"] == "/#notifications"


def test_the_relay_drops_a_bad_extra_url(client, monkeypatch):
    from core import notify
    relayed = []
    monkeypatch.setattr(notify, "notify", lambda *a, **k: relayed.append((a, k)))
    h = pair(client)
    for url in ("https://evil.example/#mission=0123456789ab", "/#mission=0123456789ab&do=delete", "javascript:alert(1)"):
        client.post("/api/notify", json={"title": "t", "body": "b", "extra": {"url": url, "kind": "done"}}, headers=h)
    client.post("/api/notify", json={"title": "t", "body": "b", "extra": {"url": "/#mission=0123456789ab&do=approve"}}, headers=h)
    assert [k.get("extra") for _, k in relayed] == [{"kind": "done"}] * 3 + [{"url": "/#mission=0123456789ab&do=approve"}]


def test_the_pc_forwards_extra_and_old_channels_never_see_it(monkeypatch):
    from core import notify
    from tools import vault
    calls, old = [], []
    monkeypatch.setattr(vault, "_remote", lambda: ("https://laptop", "t"))
    monkeypatch.setattr(vault, "_call", lambda m, p, b=None, q=None: calls.append(b))
    notify.forward_to_server("Mission done", "x", "owner", "missions", False, extra={"kind": "done"})
    assert calls[0]["extra"] == {"kind": "done"}

    def legacy(title, msg, uid, category="jarvis", urgent=False):
        old.append(category)
    legacy.wants_category = True
    notify._channels.append(legacy)
    try:
        notify.notify("Mission done", "x", "owner", "missions", extra={"kind": "done"})
    finally:
        notify._channels.remove(legacy)
    assert old == ["missions"]


def test_evidence_is_saved_for_the_mission_sheet(client, worker):
    import base64

    from core import storage
    m = missions.add("check the render")
    run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"
    out = client.post(f"/api/missions/worker/{run}/finish", json={"outcome": "done", "detail": "ok",
                                                                 "evidence": {"jpeg_b64": base64.b64encode(jpeg).decode()}},
                      headers=worker).json()["mission"]
    name = f"{m['id']}-{run}.jpg"
    assert out["result_image"] == name and missions.get(m["id"])["result_image"] == name
    assert (storage.DATA / "users" / "owner" / "missions" / name).read_bytes() == jpeg
    assert client.get(f"/api/file/u/missions/{name}", headers=worker).content == jpeg


# ---- C4: the phone (static checks; the real tap is the LIVE step) --------------------------------------------------------
def test_the_phone_offers_approve_deny_but_never_answers_without_the_lock():
    from core.config import ROOT
    sw = (ROOT / "app" / "sw.js").read_text(encoding="utf-8")
    page = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    assert 'data.kind === "approval"' in sw and '{ action: "approve", title: "Approve" }' in sw and '"&do=" + e.action' in sw
    assert "/api/missions/" not in sw                                  # the service worker never answers by itself
    body = page[page.index("async function openMission"):]
    body = body[:body.index("\n}\n")]
    assert body.index("await gateOpen()") < body.index("/answer")      # the fingerprint lock before every answer
    assert "/answer" not in body[:body.index("const answer")]          # a preselect only highlights
    assert "missionLink(location.hash)" in page and "Waiting for your OK" in page


def test_the_worker_gets_the_approved_step_once(client, worker):
    """C11: the approved step reaches the worker exactly once, and only for the mission's current run."""
    m = missions.add("clean temp files")
    run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    client.post(f"/api/missions/worker/{run}/park", json={"step": {"tool": "pc_cleanup"}, "preview": "Delete?"}, headers=worker)
    missions.answer_approval(m["id"], True, missions.get(m["id"])["approval"]["fp"])
    run2 = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
    assert client.post(f"/api/missions/worker/{run}/approval", json={}, headers=worker).json() == {"approval": None}
    got = client.post(f"/api/missions/worker/{run2}/approval", json={}, headers=worker).json()["approval"]
    assert got["step"] == {"tool": "pc_cleanup"} and got["used"]
    assert client.post(f"/api/missions/worker/{run2}/approval", json={}, headers=worker).json() == {"approval": None}
    phone, _ = _device(client)
    assert client.post(f"/api/missions/worker/{run2}/approval", json={}, headers=phone).status_code == 403


def test_his_words_hold_and_release_missions_over_the_link(client, worker):
    """Owner 2026-11-10: 'I'm gonna game' said at the PC reaches the laptop's queue (the PC relays for him); the worker then
    sees paused and claims nothing; 'I'm done gaming' lets it claim again. A household member cannot hold his missions."""
    missions.add("report my disk space")
    r = client.post("/api/missions/hold", json={"busy": True, "reason": "you're gaming", "for_uid": "owner"}, headers=worker)
    assert r.status_code == 200 and r.json()["hold"] == "you're gaming"
    assert client.get("/api/missions", params={"for_uid": "owner"}, headers=worker).json()["hold"] == "you're gaming"
    got = client.post("/api/missions/worker/claim", json={"machine": "pc"}, headers=worker).json()
    assert got["mission"] is None and got["paused"] is True
    client.post("/api/missions/hold", json={"busy": False, "for_uid": "owner"}, headers=worker)
    assert client.post("/api/missions/worker/claim", json={"machine": "pc"}, headers=worker).json()["mission"]
    wife = users.add("Marie", {"en": "madame"}, "wife")
    h, _ = _device(client, wife)
    assert client.post("/api/missions/hold", json={"busy": True}, headers=h).status_code == 403


def test_busy_must_be_a_real_yes(client, worker):
    """'false' sent as text is not busy (review 2026-11-10)."""
    r = client.post("/api/missions/hold", json={"busy": "false", "for_uid": "owner"}, headers=worker).json()
    assert r["hold"] == "" and not missions.paused()


# ---- cross-device audit A (2026-11-13): an approval request is urgent; an interrupted mission tells the owner once ------------
def test_a_needs_your_ok_push_gets_through_quiet_hours(client, worker, monkeypatch):
    import pywebpush

    from core import appserver, notify
    sent = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **k: sent.append(k))
    monkeypatch.setattr(notify, "in_quiet_hours", lambda p, now=None: True)
    devices = users.get("owner").get("devices", []) + [{"id": "p1", "token_hash": "x", "name": "phone", "push": {"endpoint": "https://e"}}]
    users.update("owner", devices=devices)
    notify._channels.append(appserver._push_channel)
    try:
        missions.add("update the Nvidia driver")
        run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
        client.post(f"/api/missions/worker/{run}/park", json={"step": {"tool": "install"}, "preview": "Install?"}, headers=worker)
    finally:
        notify._channels.remove(appserver._push_channel)
    assert len(sent) == 1 and notify.history("owner")[0]["pushed"] is True


def test_a_done_report_stays_quiet_but_an_approval_is_urgent(client, worker):
    from core import notify
    got = []

    def channel(title, body, uid, category="jarvis", urgent=False, extra=None):
        got.append((title, urgent))
    channel.wants_category = channel.wants_extra = True
    notify._channels.append(channel)
    try:
        missions.add("a")
        run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
        client.post(f"/api/missions/worker/{run}/park", json={"step": {"tool": "install"}, "preview": "Install?"}, headers=worker)
        m = missions.listing()[0]
        client.post(f"/api/missions/{m['id']}/answer", json={"approved": True, "fp": m["approval"]["fp"]},
                    headers=_device(client)[0])
        run = client.post("/api/missions/worker/claim", json={}, headers=worker).json()["mission"]["run"]
        client.post(f"/api/missions/worker/{run}/finish", json={"outcome": "done", "detail": "ok"}, headers=worker)
    finally:
        notify._channels.remove(channel)
    assert got == [("Needs your OK", True), ("Mission done", False)]


def _interrupt_twice():
    m = missions.add("report disk space")
    for _ in range(2):
        missions.claim_next("pc")
        missions.recover_and_report(stale_s=-1)
    return m


def test_a_mission_that_lands_in_unknown_notifies_the_owner_once(pushes):
    m = _interrupt_twice()
    assert missions.get(m["id"])["state"] == "unknown"
    assert [(p["title"], p["uid"]) for p in pushes] == [("Mission interrupted", "owner")]
    assert pushes[0]["extra"]["mission"] == m["id"] and "report disk space" in pushes[0]["body"]
    missions.recover_and_report(stale_s=-1)                                         # later passes find nothing running: no second notice
    missions.recover_and_report(stale_s=-1)
    assert len(pushes) == 1


def test_the_first_interruption_is_retried_silently(pushes):
    missions.add("report disk space")
    missions.claim_next("pc")
    missions.recover_and_report(stale_s=-1)
    assert pushes == []
