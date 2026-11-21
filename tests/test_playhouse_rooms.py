import concurrent.futures
import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from core.playhouse.rooms import Rooms, RoomError
from core.playhouse.api import install


@pytest.fixture
def rooms(tmp_path):
    return Rooms(tmp_path / "rooms.sqlite")


def ready(rooms):
    room = rooms.create("host")
    for player in ("one", "two"):
        rooms.join(room["code"], player)
    return rooms.act(room["id"], "host", "start", 0)


def test_persist_reconnect_complete_and_no_identity_leak(rooms):
    room = ready(rooms)
    for round_number in (0, 1):
        first = rooms.act(room["id"], "one", "vote", round_number, 0)
        assert first["counts"] is None and first["my_vote"] == 0
        second = Rooms(rooms.path).state(room["id"], "two")
        assert second["my_vote"] is None and second["voted"] == 1
        result = rooms.act(room["id"], "two", "vote", round_number, 1)
        assert result["counts"] == [1, 1] and result["selected"] == 0
        assert "votes" not in result and "host" in result and result["host"] is False
        room = rooms.act(room["id"], "host", "next", round_number)
    assert room["phase"] == "complete"
    assert room["completed"] == 3
    assert room["title"]["en"] == "A surprise for Grandma!"


def test_concurrent_votes_and_retries(rooms):
    room = ready(rooms)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda p: rooms.act(room["id"], p, "vote", 0, 1), ["one", "two"]))
    final = rooms.state(room["id"], "host")
    assert final["counts"] == [0, 2] and final["voted"] == 2
    assert rooms.act(room["id"], "one", "vote", 0, 1)["voted"] == 2
    with pytest.raises(RoomError, match="vote_locked"):
        rooms.act(room["id"], "one", "vote", 0, 0)
    rooms.act(room["id"], "host", "next", 0)
    with pytest.raises(RoomError, match="stale_round"):
        rooms.act(room["id"], "one", "vote", 0, 1)


def test_permissions_lifecycle_and_capacity(rooms):
    room = rooms.create("host")
    with pytest.raises(RoomError, match="need_two_players"):
        rooms.act(room["id"], "host", "start", 0)
    with pytest.raises(RoomError, match="room_unavailable"):
        rooms.state(room["id"], "outsider")
    for i in range(8):
        rooms.join(room["code"], str(i))
    with pytest.raises(RoomError, match="room_full"):
        rooms.join(room["code"], "ninth")
    with pytest.raises(RoomError, match="host_only"):
        rooms.act(room["id"], "0", "start", 0)
    rooms.act(room["id"], "host", "start", 0)
    with pytest.raises(RoomError, match="already_started"):
        rooms.join(room["code"], "ninth")
    with pytest.raises(RoomError, match="join_to_vote"):
        rooms.act(room["id"], "host", "vote", 0, 0)
    assert rooms.join(room["code"], "0")["players"] == 8
    rooms.act(room["id"], "host", "close", 0)
    with pytest.raises(RoomError, match="room_unavailable"):
        rooms.state(room["id"], "0")


def test_expiry_timeout_and_rate_limit(tmp_path):
    now = [1000.]
    rooms = Rooms(tmp_path / "rooms.sqlite", lambda: now[0])
    room = ready(rooms)
    rooms.act(room["id"], "one", "vote", 0, 1)
    now[0] += 61
    assert rooms.state(room["id"], "two")["counts"] == [0, 1]
    with pytest.raises(RoomError, match="voting_closed"):
        rooms.act(room["id"], "two", "vote", 0, 0)
    for _ in range(10):
        with pytest.raises(RoomError, match="room_unavailable"):
            rooms.join("BADBAD", "guesser")
    with pytest.raises(RoomError, match="too_many_attempts"):
        rooms.join(room["code"], "guesser")
    now[0] += 7200
    with pytest.raises(RoomError, match="room_unavailable"):
        rooms.state(room["id"], "host")


def test_room_creation_bounded(rooms):
    rooms.create("host"); rooms.create("host")
    with pytest.raises(RoomError, match="room_limit"):
        rooms.create("host")


def test_empty_timeout_does_not_invent_a_choice(tmp_path):
    now = [1000.]
    rooms = Rooms(tmp_path / "empty.sqlite", lambda: now[0])
    room = ready(rooms)
    now[0] += 61
    assert rooms.state(room["id"], "host")["phase"] == "waiting"
    with pytest.raises(RoomError, match="voting_closed"):
        rooms.act(room["id"], "one", "vote", 0, 0)
    with pytest.raises(RoomError, match="host_only"):
        rooms.act(room["id"], "one", "retry", 0)
    reopened = rooms.act(room["id"], "host", "retry", 0)
    assert reopened["phase"] == "voting" and reopened["round"] == 1
    with pytest.raises(RoomError, match="stale_round"):
        rooms.act(room["id"], "one", "vote", 0, 0)
    assert rooms.act(room["id"], "one", "vote", 1, 0)["voted"] == 1


@pytest.mark.parametrize("bad", [True, -1, 2, "0", None])
def test_invalid_vote_never_changes_state(rooms, bad):
    room = ready(rooms)
    with pytest.raises(RoomError):
        rooms.act(room["id"], "one", "vote", 0, bad)
    assert rooms.state(room["id"], "one")["voted"] == 0


def test_api_auth_validation_and_persistence(tmp_path):
    app = FastAPI()
    def auth(authorization: str = Header(default="")):
        if authorization not in {"Bearer host", "Bearer one", "Bearer two", "Bearer outsider"}:
            raise HTTPException(401, "Not paired")
        return ("owner", {"id": authorization})
    install(app, auth)
    client = TestClient(app)
    h = lambda name: {"Authorization": "Bearer " + name}
    assert client.post("/api/playhouse/rooms").status_code == 401
    assert client.post("/api/playhouse/join", content=b"x"*3000, headers=h("one")).status_code == 413
    assert client.post("/api/playhouse/join", content=iter([b"x"*1000]*3), headers=h("one")).status_code == 413
    room = client.post("/api/playhouse/rooms", headers=h("host")).json()
    for actor in ("one", "two"):
        assert client.post("/api/playhouse/join", json={"code":room["code"]}, headers=h(actor)).status_code == 200
    path = "/api/playhouse/rooms/" + room["id"]
    assert client.get(path, headers=h("outsider")).status_code == 404
    assert client.post(path+"/start", headers=h("one"), json={"round":0}).status_code == 403
    assert client.post(path+"/start", headers=h("host"), json={"round":0}).status_code == 200
    assert client.post(path+"/vote", headers=h("one"), json={"round":0,"choice":True}).status_code == 422
    assert client.post(path+"/vote", headers=h("one"), json={"round":0,"choice":0}).json()["voted"] == 1
    response = client.get(path, headers=h("two"))
    assert response.json()["my_vote"] is None
    assert response.headers["cache-control"] == "no-store"


def test_existing_appserver_device_auth_and_static_modules():
    from core import appserver, users
    client = TestClient(appserver.AppServer(lambda uid: None).app)
    headers = [{"Authorization": "Bearer " + appserver.add_device("owner", name)}
               for name in ("playhouse-test-host", "playhouse-test-one", "playhouse-test-two")]
    room = client.post("/api/playhouse/rooms", headers=headers[0]).json()
    for h in headers[1:]:
        assert client.post("/api/playhouse/join", json={"code":room["code"]}, headers=h).status_code == 200
    path = "/api/playhouse/rooms/" + room["id"]
    assert client.get(path, headers=headers[0]).json()["players"] == 2
    assert client.get("/games-story.json").json()["kitchen"]["chapter"] == 1
    assert "javascript" in client.get("/games-rooms.mjs").headers["content-type"]
    assert client.get("/games-assets/family.webp").headers["content-type"] == "image/webp"
    # Revocation is effective immediately; the room adds no independent bearer.
    users.update("owner", devices=[])
    assert client.get(path, headers=headers[1]).status_code == 401
