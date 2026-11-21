"""Authoritative multi-phone social deduction, privacy and deadline checks."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.playhouse.imposter import ImposterGames
from core.playhouse.rooms import RoomError
from core.playhouse.api import install


def group(tmp_path, size, settings=None):
    now = [1000.0]
    game = ImposterGames(tmp_path / "imposter.sqlite", lambda: now[0])
    joined = [game.create("Player 0")]
    code = joined[0]["room"]["code"]
    for index in range(1, size):
        joined.append(game.join(code, f"Player {index}", actor=f"device-{index}"))
    rid = joined[0]["room"]["id"]
    tokens = [j["token"] for j in joined]
    if settings:
        game.act(rid, tokens[0], "settings", game.state(rid, tokens[0])["revision"], settings=settings)
    game.act(rid, tokens[0], "start", game.state(rid, tokens[0])["revision"])
    roles = [game.state(rid, token)["role"] for token in tokens]
    for token in tokens:
        view = game.state(rid, token)
        assert view["phase"] == "reveal"
        assert all(p["alignment"] is None for p in view["players"])
        game.act(rid, token, "ready", view["revision"])
    return game, rid, tokens, roles, now


@pytest.mark.parametrize("size", range(5, 13))
def test_full_matches_every_supported_size(tmp_path, size):
    game, rid, tokens, roles, now = group(tmp_path, size)
    imposters = [i for i, role in enumerate(roles) if role == "imposter"]
    assert len(imposters) == (1 if size <= 6 else 2 if size <= 9 else 3)
    assert len(set(imposters)) == len(imposters)
    assert game.state(rid, tokens[0])["phase"] == "night"
    first_victim = None
    while game.state(rid, tokens[0])["phase"] != "end":
        view = game.state(rid, tokens[0])
        if view["phase"] == "night":
            living = [i for i, p in enumerate(view["players"]) if p["alive"]]
            innocent = next(i for i in living if i not in imposters)
            target_imposter = next(i for i in living if i in imposters)
            for seat in living:
                state = game.state(rid, tokens[seat])
                target = innocent if seat in imposters else target_imposter
                if target == seat:
                    target = next(i for i in living if i != seat and i not in imposters)
                game.act(rid, tokens[seat], "night", state["revision"], target=target)
            assert game.state(rid, tokens[0])["phase"] == "confirm"
            for seat in living:
                state = game.state(rid, tokens[seat])
                assert state["confirmation_target"] is not None
                game.act(rid, tokens[seat], "confirm", state["revision"], confirm=True)
            after = game.state(rid, tokens[0])
            assert after["phase"] in {"morning", "end"}
            if after["phase"] == "morning":
                victims = [i for i, p in enumerate(after["players"]) if not p["alive"] and i not in imposters]
                if first_victim is None:
                    first_victim = victims[0]
                    dead = game.state(rid, tokens[first_victim])
                    assert dead["role"] != "imposter"
                    with pytest.raises(RoomError):
                        game.act(rid, tokens[first_victim], "night", dead["revision"], target=target_imposter)
                    game.act(rid, tokens[first_victim], "predict", dead["revision"], prediction="innocents")
                    assert game.state(rid, tokens[first_victim])["prediction"] == "innocents"
                    assert game.state(rid, tokens[next(i for i in range(size) if i != first_victim)])["prediction"] is None
        elif view["phase"] == "morning":
            now[0] += 9
            assert game.state(rid, tokens[0])["phase"] == "discussion"
        elif view["phase"] == "discussion":
            assert 0 <= view["talk_prompt"] < 12
            now[0] += 61
            assert game.state(rid, tokens[0])["phase"] == "vote"
        elif view["phase"] == "vote":
            living = [i for i, p in enumerate(view["players"]) if p["alive"]]
            accused = next(i for i in living if i in imposters)
            for seat in living:
                target = accused if seat not in imposters else next(i for i in living if i not in imposters)
                current = game.state(rid, tokens[seat])
                game.act(rid, tokens[seat], "vote", current["revision"], target=target)
            result = game.state(rid, tokens[0])
            assert result["phase"] in {"verdict", "end"}
        elif view["phase"] == "verdict":
            now[0] += 9
            assert game.state(rid, tokens[0])["phase"] == "night"
        else:
            pytest.fail(f"unexpected phase {view['phase']}")
        assert game.state(rid, tokens[0])["round"] <= size
    end = game.state(rid, tokens[0])
    assert end["event"]["winner"] == "innocents"
    assert all(p["alignment"] in {"innocent", "imposter"} for p in end["players"])
    assert game.state(rid, tokens[first_victim])["score"] >= 3  # team reward plus prediction
    assert ImposterGames(game.path, lambda: now[0]).state(rid, tokens[0])["phase"] == "end"


def test_confirm_guardian_detective_sabotage_and_private_info(tmp_path):
    settings = {"detective": True, "guardian": True, "sabotage": True}
    game, rid, tokens, roles, now = group(tmp_path, 7, settings)
    imposters = [i for i, r in enumerate(roles) if r == "imposter"]
    detective, guardian = roles.index("detective"), roles.index("guardian")
    victim = next(i for i, r in enumerate(roles) if r == "innocent")
    for seat, role in enumerate(roles):
        state = game.state(rid, tokens[seat])
        target = victim if role in {"imposter", "guardian"} else imposters[0]
        if seat == target:
            target = imposters[0]
        game.act(rid, tokens[seat], "night", state["revision"], target=target,
                 use=seat in {imposters[0], detective, guardian})
    assert game.state(rid, tokens[0])["phase"] == "confirm"
    for seat in range(7):
        state = game.state(rid, tokens[seat])
        game.act(rid, tokens[seat], "confirm", state["revision"], confirm=True)
    state = game.state(rid, tokens[0])
    assert state["phase"] == "morning" and state["event"]["victim"] is None
    assert state["event"]["silence"] and state["event"]["evidence"] is None
    assert game.state(rid, tokens[detective])["clue"]["alignment"] == "imposter"
    assert game.state(rid, tokens[guardian])["clue"] is None
    assert game.state(rid, tokens[imposters[0]])["clue"] is None
    assert all(p["alignment"] is None for p in state["players"])


def test_tie_no_elimination_and_runoff(tmp_path):
    for rule in ("no_elimination", "runoff", "random"):
        game, rid, tokens, roles, now = group(tmp_path / rule, 5, {"tie": rule})
        # Everyone passes at night; the confirmation still appears on all phones.
        for token in tokens:
            game.act(rid, token, "night", game.state(rid, token)["revision"], target=None)
        for token in tokens:
            game.act(rid, token, "confirm", game.state(rid, token)["revision"], confirm=False)
        now[0] += 9; game.state(rid, tokens[0]); now[0] += 61
        state = game.state(rid, tokens[0]); assert state["phase"] == "vote"
        for seat, target in enumerate([1, 0, 0, 1, None]):
            game.act(rid, tokens[seat], "vote", state["revision"], target=target)
        state = game.state(rid, tokens[0])
        if rule == "runoff":
            assert state["phase"] == "runoff" and state["runoff"] == [0, 1]
            with pytest.raises(RoomError):
                game.act(rid, tokens[2], "vote", state["revision"], target=3)
            now[0] += 21
            assert game.state(rid, tokens[0])["event"]["ejected"] is None
        elif rule == "no_elimination":
            assert state["event"]["ejected"] is None
        else:
            verdict = state["event"]["last"] if state["phase"] == "end" else state["event"]
            assert verdict["ejected"] in {0, 1}


def test_guest_tokens_host_settings_reconnect_and_rotation(tmp_path):
    now = [1000.0]
    game = ImposterGames(tmp_path / "room.sqlite", lambda: now[0])
    host = game.create("Host")
    rid, code = host["room"]["id"], host["room"]["code"]
    with pytest.raises(RoomError): game.state(rid, "wrong")
    with pytest.raises(RoomError): game.join(code, "Host", "new-device")
    with pytest.raises(RoomError): game.join(code, "Bad\u202ename", "new-device")
    guest = game.join(code, "Second", "device")
    with pytest.raises(RoomError): game.act(rid, guest["token"], "settings", game.state(rid, guest["token"])["revision"], settings={"vote": 30})
    with pytest.raises(RoomError): game.act(rid, host["token"], "settings", game.state(rid, host["token"])["revision"], settings={"vote": True})
    game.act(rid, host["token"], "settings", game.state(rid, host["token"])["revision"], settings={"vote": 30, "tie": "runoff"})
    assert ImposterGames(game.path, lambda: now[0]).state(rid, host["token"])["settings"]["vote"] == 30
    with pytest.raises(RoomError): game.act(rid, host["token"], "start", 0)
    game.act(rid, host["token"], "close", game.state(rid, host["token"])["revision"])
    with pytest.raises(RoomError): game.state(rid, guest["token"])


def test_imposters_win_at_parity_without_invented_votes(tmp_path):
    game, rid, tokens, roles, now = group(tmp_path, 5)
    imposter = roles.index("imposter")
    while True:
        view = game.state(rid, tokens[imposter])
        if view["phase"] == "end":
            assert view["event"]["winner"] == "imposters"
            assert sum(p["alive"] for p in view["players"]) == 2
            break
        if view["phase"] == "night":
            living = [i for i, p in enumerate(view["players"]) if p["alive"]]
            victim = next(i for i in living if i != imposter)
            for seat in living:
                game.act(rid, tokens[seat], "night", view["revision"], target=victim if seat == imposter else imposter)
            for seat in living:
                game.act(rid, tokens[seat], "confirm", game.state(rid, tokens[seat])["revision"], confirm=True)
            assert sum(not p["alive"] for p in game.state(rid, tokens[0])["players"]) <= view["round"]
        elif view["phase"] == "morning":
            now[0] += 9; game.state(rid, tokens[0])
        elif view["phase"] == "discussion":
            now[0] += 61; game.state(rid, tokens[0])
        elif view["phase"] == "vote":
            living = [i for i, p in enumerate(view["players"]) if p["alive"]]
            for seat in living:
                game.act(rid, tokens[seat], "vote", view["revision"], target=None)
            assert game.state(rid, tokens[0])["event"]["ejected"] is None
        elif view["phase"] == "verdict":
            now[0] += 9; game.state(rid, tokens[0])
        else:
            pytest.fail(view["phase"])


def test_missing_phones_timeout_without_forged_actions(tmp_path):
    now = [1000.0]
    game = ImposterGames(tmp_path / "timeout.sqlite", lambda: now[0])
    joined = [game.create("P0")]
    for i in range(1, 5): joined.append(game.join(joined[0]["room"]["code"], f"P{i}", f"device-{i}"))
    rid, tokens = joined[0]["room"]["id"], [entry["token"] for entry in joined]
    game.act(rid, tokens[0], "start", game.state(rid, tokens[0])["revision"])
    now[0] += 46  # One phone never acknowledged its role.
    view = game.state(rid, tokens[0]); assert view["phase"] == "night"
    roles = [game.state(rid, token)["role"] for token in tokens]
    imposter = roles.index("imposter")
    victim = next(i for i in range(5) if i != imposter)
    game.act(rid, tokens[imposter], "night", view["revision"], target=victim)
    now[0] += 46
    view = game.state(rid, tokens[0]); assert view["phase"] == "confirm"
    assert view["confirmation_target"] == (victim if imposter == 0 else None)
    game.act(rid, tokens[imposter], "confirm", view["revision"], confirm=True)
    now[0] += 13
    view = game.state(rid, tokens[0]); assert view["phase"] == "morning"
    assert view["event"]["victim"] == victim
    assert sum(not p["alive"] for p in view["players"]) == 1
    now[0] += 9; game.state(rid, tokens[0]); now[0] += 61
    vote = game.state(rid, tokens[0]); assert vote["phase"] == "vote"
    voter = next(i for i, p in enumerate(vote["players"]) if p["alive"] and i != imposter)
    game.act(rid, tokens[voter], "vote", vote["revision"], target=imposter)
    now[0] += 21
    result = game.state(rid, tokens[0])
    assert result["phase"] == "end" and result["event"]["winner"] == "innocents"
    assert result["event"]["last"]["votes"] == 1


def test_public_evidence_is_truthful_but_does_not_name_suspect(tmp_path):
    game, rid, tokens, roles, now = group(tmp_path, 7)
    imposter = next(i for i, role in enumerate(roles) if role == "imposter")
    innocent = next(i for i, role in enumerate(roles) if role != "imposter")
    for seat, role in enumerate(roles):
        game.act(rid, tokens[seat], "night", game.state(rid, tokens[seat])["revision"],
                 target=innocent if role == "imposter" else imposter)
    for seat in range(7):
        view = game.state(rid, tokens[seat])
        assert view["event"] is None and all(p["alignment"] is None for p in view["players"])
        game.act(rid, tokens[seat], "confirm", view["revision"], confirm=True)
    evidence = game.state(rid, tokens[0])["event"]["evidence"]
    assert evidence == {"strength": 3}  # capped; five ordinary innocents agreed
    assert "target" not in evidence


def test_split_imposter_nominations_still_offer_one_confirmable_target(tmp_path):
    game, rid, tokens, roles, now = group(tmp_path, 8)
    imposters = [i for i, role in enumerate(roles) if role == "imposter"]
    innocents = [i for i, role in enumerate(roles) if role != "imposter"]
    for seat, role in enumerate(roles):
        target = innocents[imposters.index(seat)] if role == "imposter" else imposters[0]
        game.act(rid, tokens[seat], "night", game.state(rid, tokens[seat])["revision"], target=target)
    candidates = [game.state(rid, tokens[seat])["confirmation_target"] for seat in imposters]
    assert candidates[0] == candidates[1] and candidates[0] in innocents[:2]
    # One of two imposters cannot force a disappearance alone.
    for seat in range(8):
        game.act(rid, tokens[seat], "confirm", game.state(rid, tokens[seat])["revision"],
                 confirm=seat != imposters[1])
    morning = game.state(rid, tokens[0])
    assert morning["event"]["victim"] is None
    assert sum(not p["alive"] for p in morning["players"]) == 0


def test_special_roles_are_not_enabled_for_tiny_group(tmp_path):
    game = ImposterGames(tmp_path / "balance.sqlite")
    host = game.create("P0")
    tokens = [host["token"]]
    for i in range(1, 5): tokens.append(game.join(host["room"]["code"], f"P{i}", f"d{i}")["token"])
    rid = host["room"]["id"]
    game.act(rid, tokens[0], "settings", game.state(rid, tokens[0])["revision"], settings={"detective": True})
    with pytest.raises(RoomError, match="unbalanced_roles"):
        game.act(rid, tokens[0], "start", game.state(rid, tokens[0])["revision"])
    assert game.state(rid, tokens[0])["phase"] == "lobby"


def test_api_guest_auth_and_no_cache(tmp_path, monkeypatch):
    from core.playhouse import api
    monkeypatch.setattr(api.storage, "DATA", tmp_path)
    app = FastAPI()
    def old_auth():
        raise AssertionError("Imposter guest routes must not require JARVIS pairing")
    install(app, old_auth)
    client = TestClient(app)
    created = client.post("/api/playhouse/imposter/rooms", json={"name": "Ada"})
    assert created.status_code == 200 and created.headers["cache-control"] == "no-store"
    room = created.json()["room"]
    assert client.get(f"/api/playhouse/imposter/rooms/{room['id']}").status_code == 401
    assert client.get(f"/api/playhouse/imposter/rooms/{room['id']}", headers={"Authorization": "Bearer wrong"}).status_code == 404
    joined = client.post("/api/playhouse/imposter/join", json={"code": room["code"], "name": "Ben"})
    assert joined.status_code == 200
    token = joined.json()["token"]
    response = client.get(f"/api/playhouse/imposter/rooms/{room['id']}", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200 and response.json()["seat"] == 1
    assert "token" not in response.text and "password" not in response.text
    assert client.post("/api/playhouse/imposter/join", json={"code": "111111", "name": "C"}).status_code == 400


def test_standalone_lan_host_serves_game_without_jarvis_pairing(tmp_path, monkeypatch):
    from core.playhouse import api
    from scripts.playhouse_imposter_host import build_app
    monkeypatch.setattr(api.storage, "DATA", tmp_path)
    client = TestClient(build_app(tmp_path))
    assert client.get("/imposter.html").status_code == 200
    assert "javascript" in client.get("/imposter-ui.mjs").headers["content-type"]
    assert client.get("/games-assets/room.webp").status_code == 200
    host = client.post("/api/playhouse/imposter/rooms", json={"name": "Host"})
    assert host.status_code == 200
    room = host.json()["room"]
    assert client.get("/api/playhouse/imposter/rooms/"+room["id"], headers={"Authorization": "Bearer "+host.json()["token"]}).status_code == 200
    assert client.post("/api/playhouse/rooms").status_code == 401
