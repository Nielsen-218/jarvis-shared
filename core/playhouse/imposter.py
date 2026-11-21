"""Authoritative, expiring LAN party rooms for Imposter — Trust Nobody.

The public snapshot is built per seat. Never return the stored room dict to a client.
SQLite's immediate transaction serializes simultaneous phone actions and deadlines.
"""
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import random
import secrets
import sqlite3
import time
import unicodedata

from .rooms import RoomError

TTL = 4 * 3600
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEFAULTS = {"discussion": 60, "vote": 20, "night": 45, "confirm": 12,
            "tie": "no_elimination", "evidence": True, "detective": False,
            "guardian": False, "sabotage": False, "audio": True}
BOUNDS = {"discussion": (30, 180), "vote": (10, 60), "night": (25, 90), "confirm": (8, 30)}


def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def valid_name(name):
    return isinstance(name, str) and 1 <= len(name.strip()) <= 20 and name == name.strip() \
        and not any(unicodedata.category(ch) in {"Cc", "Cf"} or ch in "<>" for ch in name)


def validate_settings(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise RoomError(400, "invalid_settings")
    result = {**DEFAULTS, **values}
    for key, (low, high) in BOUNDS.items():
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise RoomError(400, "invalid_settings")
    if result["tie"] not in {"no_elimination", "runoff", "random"}:
        raise RoomError(400, "invalid_settings")
    if any(type(result[key]) is not bool for key in ("evidence", "detective", "guardian", "sabotage", "audio")):
        raise RoomError(400, "invalid_settings")
    return result


def count_imposters(size):
    if not 5 <= size <= 12:
        raise RoomError(409, "need_five_players")
    return 1 if size <= 6 else 2 if size <= 9 else 3


class ImposterGames:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS imposter_rooms (id TEXT PRIMARY KEY, code TEXT UNIQUE, expires REAL, data TEXT)")
            con.execute("CREATE TABLE IF NOT EXISTS imposter_attempts (actor TEXT PRIMARY KEY, at REAL, count INTEGER)")
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM imposter_rooms WHERE expires <= ?", (self.clock(),))
            con.execute("DELETE FROM imposter_attempts WHERE at < ?", (self.clock() - 60,))
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def save(con, room):
        con.execute("UPDATE imposter_rooms SET data=? WHERE id=?", (json.dumps(room, separators=(",", ":")), room["id"]))

    @staticmethod
    def seat(room, token):
        if not isinstance(token, str) or len(token) > 128:
            raise RoomError(404, "room_unavailable")
        hashed = digest(token)
        for index, player in enumerate(room["players"]):
            if secrets.compare_digest(player["token"], hashed):
                return index
        raise RoomError(404, "room_unavailable")

    def create(self, name, actor="unknown"):
        if not valid_name(name):
            raise RoomError(400, "invalid_name")
        token = secrets.token_urlsafe(32)
        with self.db() as con:
            row = con.execute("SELECT count FROM imposter_attempts WHERE actor=?", ("create:"+actor,)).fetchone()
            if row and row[0] >= 5:
                raise RoomError(429, "too_many_attempts")
            con.execute("INSERT INTO imposter_attempts VALUES(?,?,1) ON CONFLICT(actor) DO UPDATE SET count=count+1,at=excluded.at",
                        ("create:"+actor, self.clock()))
            if con.execute("SELECT count(*) FROM imposter_rooms").fetchone()[0] >= 128:
                raise RoomError(429, "room_limit")
            for _ in range(10):
                code = "".join(secrets.choice(ALPHABET) for _ in range(6))
                if not con.execute("SELECT 1 FROM imposter_rooms WHERE code=?", (code,)).fetchone():
                    break
            else:
                raise RoomError(503, "try_again")
            room = {"id": secrets.token_hex(16), "code": code, "expires": self.clock()+TTL,
                    "host": 0, "phase": "lobby", "round": 0, "match": 0, "revision": 0,
                    "settings": DEFAULTS.copy(), "players": [self.new_player(name, token)],
                    "deadline": None, "night": {}, "votes": {}, "event": None,
                    "history": [], "role_seed": secrets.randbits(64), "runoff": [], "public_roles": {}}
            con.execute("INSERT INTO imposter_rooms VALUES(?,?,?,?)",
                        (room["id"], code, room["expires"], json.dumps(room, separators=(",", ":"))))
            return {"token": token, "room": self.view(room, 0)}

    @staticmethod
    def new_player(name, token):
        return {"name": name, "token": digest(token), "alive": True, "role": None,
                "score": 0, "imposter_count": 0, "last_imposter": False,
                "ready": False, "used_power": False, "prediction": None,
                "clue": None, "vote_hits": 0, "suspicion_hits": 0, "night_token": False}

    def join(self, code, name, actor="unknown"):
        if not isinstance(code, str) or len(code) != 6 or any(ch not in ALPHABET for ch in code):
            raise RoomError(400, "invalid_code")
        if not valid_name(name):
            raise RoomError(400, "invalid_name")
        with self.db() as con:
            row = con.execute("SELECT count FROM imposter_attempts WHERE actor=?", (actor,)).fetchone()
            if row and row[0] >= 10:
                raise RoomError(429, "too_many_attempts")
            con.execute("INSERT INTO imposter_attempts VALUES(?,?,1) ON CONFLICT(actor) DO UPDATE SET count=count+1,at=excluded.at", (actor, self.clock()))
            row = con.execute("SELECT data FROM imposter_rooms WHERE code=? AND expires>?", (code, self.clock())).fetchone()
            if not row:
                raise RoomError(404, "room_unavailable")
            room = json.loads(row[0])
            if room["phase"] != "lobby":
                raise RoomError(409, "already_started")
            if len(room["players"]) >= 12:
                raise RoomError(409, "room_full")
            if any(p["name"].casefold() == name.casefold() for p in room["players"]):
                raise RoomError(409, "name_taken")
            token = secrets.token_urlsafe(32)
            room["players"].append(self.new_player(name, token))
            room["revision"] += 1
            self.save(con, room)
            return {"token": token, "room": self.view(room, len(room["players"])-1)}

    def load(self, con, rid, token):
        if not isinstance(rid, str) or len(rid) != 32:
            raise RoomError(404, "room_unavailable")
        row = con.execute("SELECT data FROM imposter_rooms WHERE id=? AND expires>?", (rid, self.clock())).fetchone()
        if not row:
            raise RoomError(404, "room_unavailable")
        room = json.loads(row[0])
        return room, self.seat(room, token)

    def state(self, rid, token):
        with self.db() as con:
            room, seat = self.load(con, rid, token)
            self.resolve(room)
            self.save(con, room)
            return self.view(room, seat)

    def act(self, rid, token, action, revision, *, target=None, use=False, confirm=None,
            prediction=None, settings=None):
        with self.db() as con:
            room, seat = self.load(con, rid, token)
            self.resolve(room)
            if type(revision) is not int or revision != room["revision"]:
                raise RoomError(409, "stale_phase")
            phase = room["phase"]
            player = room["players"][seat]
            if action == "close":
                self.host_only(room, seat)
                con.execute("DELETE FROM imposter_rooms WHERE id=?", (rid,))
                return {"closed": True}
            if action == "settings":
                self.host_only(room, seat)
                if phase != "lobby":
                    raise RoomError(409, "game_started")
                room["settings"] = validate_settings(settings)
                room["revision"] += 1
            elif action == "start":
                self.host_only(room, seat)
                if phase != "lobby":
                    raise RoomError(409, "game_started")
                count_imposters(len(room["players"]))
                if len(room["players"]) < 7 and (room["settings"]["detective"] or room["settings"]["guardian"]):
                    raise RoomError(409, "unbalanced_roles")
                self.assign_roles(room)
                self.phase(room, "reveal", 45)
            elif action == "ready":
                if phase != "reveal":
                    raise RoomError(409, "wrong_phase")
                player["ready"] = True
                if all(p["ready"] for p in room["players"]):
                    self.begin_night(room)
            elif action == "night":
                if phase != "night" or not player["alive"]:
                    raise RoomError(409, "wrong_phase")
                if str(seat) in room["night"]["picks"]:
                    raise RoomError(409, "choice_locked")
                self.validate_target(room, seat, target)
                if type(use) is not bool or (use and player["used_power"]):
                    raise RoomError(400, "invalid_token")
                room["night"]["picks"][str(seat)] = target
                room["night"]["tokens"][str(seat)] = use
                if use:
                    player["used_power"] = True
                if self.all_living_submitted(room, room["night"]["picks"]):
                    self.begin_confirm(room)
            elif action == "confirm":
                if phase != "confirm" or not player["alive"]:
                    raise RoomError(409, "wrong_phase")
                if type(confirm) is not bool:
                    raise RoomError(400, "invalid_confirmation")
                if str(seat) in room["night"]["confirms"]:
                    raise RoomError(409, "choice_locked")
                room["night"]["confirms"][str(seat)] = confirm
                if self.all_living_submitted(room, room["night"]["confirms"]):
                    self.finish_night(room)
            elif action == "vote":
                if phase not in {"vote", "runoff"} or not player["alive"]:
                    raise RoomError(409, "wrong_phase")
                if str(seat) in room["votes"]:
                    raise RoomError(409, "vote_locked")
                self.validate_target(room, seat, target, allowed=room["runoff"] if phase == "runoff" else None)
                room["votes"][str(seat)] = target
                if self.all_living_submitted(room, room["votes"]):
                    self.finish_vote(room)
            elif action == "predict":
                if player["alive"] or phase in {"lobby", "reveal", "end"}:
                    raise RoomError(409, "wrong_phase")
                if prediction not in {"innocents", "imposters"} or player["prediction"] is not None:
                    raise RoomError(400, "invalid_prediction")
                player["prediction"] = prediction
            elif action == "rematch":
                self.host_only(room, seat)
                if phase != "end":
                    raise RoomError(409, "wrong_phase")
                room["match"] += 1
                room["round"] = 0
                room["event"] = None
                room["history"] = []
                room["public_roles"] = {}
                self.assign_roles(room)
                self.phase(room, "reveal", 45)
            else:
                raise RoomError(400, "invalid_action")
            self.save(con, room)
            return self.view(room, seat)

    @staticmethod
    def host_only(room, seat):
        if seat != room["host"]:
            raise RoomError(403, "host_only")

    @staticmethod
    def phase(room, name, seconds=None):
        room["phase"] = name
        room["revision"] += 1
        room["deadline"] = None if seconds is None else room["now"] + seconds

    def assign_roles(self, room):
        players = room["players"]
        count = count_imposters(len(players))
        rng = random.Random(room["role_seed"] + room["match"] * 1000003)
        order = list(range(len(players)))
        rng.shuffle(order)
        order.sort(key=lambda i: (players[i]["last_imposter"], players[i]["imposter_count"]))
        chosen = set(order[:count])
        special = [i for i in order if i not in chosen]
        rng.shuffle(special)
        detective = special.pop() if room["settings"]["detective"] else None
        guardian = special.pop() if room["settings"]["guardian"] else None
        for i, player in enumerate(players):
            player.update(alive=True, role="imposter" if i in chosen else "detective" if i == detective else
                          "guardian" if i == guardian else "innocent", ready=False, used_power=False,
                          prediction=None, clue=None, vote_hits=0, suspicion_hits=0, night_token=False,
                          last_imposter=i in chosen)
            if i in chosen:
                player["imposter_count"] += 1
        room["night"] = {}
        room["votes"] = {}
        room["runoff"] = []
        room["now"] = self.clock()

    def begin_night(self, room):
        room["round"] += 1
        room["night"] = {"picks": {}, "tokens": {}, "confirms": {}, "candidate": None}
        room["votes"] = {}
        room["event"] = None
        room["talk_prompt"] = random.Random(room["role_seed"] + room["round"] * 7919 + room["match"] * 101).randrange(12)
        room["now"] = self.clock()
        self.phase(room, "night", room["settings"]["night"])

    @staticmethod
    def living(room):
        return [i for i, player in enumerate(room["players"]) if player["alive"]]

    @classmethod
    def all_living_submitted(cls, room, values):
        return all(str(i) in values for i in cls.living(room))

    @staticmethod
    def validate_target(room, seat, target, *, allowed=None):
        if target is None:
            return
        if type(target) is not int or target == seat or target < 0 or target >= len(room["players"]) \
                or not room["players"][target]["alive"]:
            raise RoomError(400, "invalid_target")
        if allowed is not None and target not in allowed:
            raise RoomError(400, "invalid_target")

    def begin_confirm(self, room):
        picks = [target for seat, target in room["night"]["picks"].items()
                 if room["players"][int(seat)]["role"] == "imposter" and target is not None]
        counts = Counter(picks)
        if counts:
            maximum = max(counts.values())
            leaders = sorted(target for target, count in counts.items() if count == maximum)
            room["night"]["candidate"] = (leaders[0] if len(leaders) == 1 else
                random.Random(room["role_seed"] + room["round"] * 31 + room["match"] * 1009).choice(leaders))
        room["now"] = self.clock()
        self.phase(room, "confirm", room["settings"]["confirm"])

    def finish_night(self, room):
        night = room["night"]
        imposters = [i for i in self.living(room) if room["players"][i]["role"] == "imposter"]
        approvals = sum(night["confirms"].get(str(i), False) is True for i in imposters)
        candidate = night["candidate"] if approvals > len(imposters) / 2 else None
        guardians = [i for i in self.living(room) if room["players"][i]["role"] == "guardian"]
        protected = any(night["tokens"].get(str(i)) and night["confirms"].get(str(i))
                        and night["picks"].get(str(i)) == candidate for i in guardians)
        victim = None if protected else candidate
        if victim is not None:
            room["players"][victim]["alive"] = False
        for i in range(len(room["players"])):
            if str(i) not in night["picks"]:
                continue
            player = room["players"][i]
            target = night["picks"].get(str(i))
            if player["role"] == "detective" and night["tokens"].get(str(i)) and night["confirms"].get(str(i)) and target is not None:
                player["clue"] = {"round": room["round"], "target": target,
                                  "alignment": "imposter" if room["players"][target]["role"] == "imposter" else "innocent"}
            if player["role"] == "innocent" and night["confirms"].get(str(i)) and target is not None \
                    and room["players"][target]["role"] == "imposter":
                player["suspicion_hits"] += 1 + int(bool(night["tokens"].get(str(i))))
        if victim is not None:
            for i in imposters:
                if night["tokens"].get(str(i)) and night["confirms"].get(str(i)):
                    room["players"][i]["night_token"] = True
        saboteur = room["settings"]["sabotage"] and any(room["players"][i]["role"] == "imposter" and night["tokens"].get(str(i))
                        and night["confirms"].get(str(i)) for i in imposters)
        suspicions = Counter(night["picks"].get(str(i)) for i, p in enumerate(room["players"])
                             if p["role"] == "innocent" and night["confirms"].get(str(i))
                             and night["picks"].get(str(i)) is not None)
        evidence = None
        if room["settings"]["evidence"] and not saboteur and suspicions:
            most = max(suspicions.values())
            leaders = [i for i, count in suspicions.items() if count == most]
            if most >= 2 and len(leaders) == 1:
                # Agreement strength is truthful; the suspect and exact count stay private.
                evidence = {"strength": min(most, 3)}
        room["event"] = {"kind": "morning", "victim": victim, "evidence": evidence,
                         "silence": saboteur, "night": room["round"]}
        room["history"].append(room["event"])
        if self.winner(room):
            self.end(room)
        else:
            room["now"] = self.clock()
            self.phase(room, "morning", 8)

    @staticmethod
    def winner(room):
        alive = [p for p in room["players"] if p["alive"]]
        imposters = sum(p["role"] == "imposter" for p in alive)
        innocents = len(alive) - imposters
        return "innocents" if imposters == 0 else "imposters" if imposters >= innocents else None

    def finish_vote(self, room):
        counts = Counter(value for value in room["votes"].values() if value is not None)
        leaders = []
        if counts:
            maximum = max(counts.values())
            leaders = sorted(i for i, count in counts.items() if count == maximum)
        tie = len(leaders) > 1
        target = leaders[0] if len(leaders) == 1 else None
        if tie and room["phase"] == "vote" and room["settings"]["tie"] == "runoff":
            room["runoff"] = leaders
            room["votes"] = {}
            room["now"] = self.clock()
            self.phase(room, "runoff", room["settings"]["vote"])
            return
        if tie and room["settings"]["tie"] == "random":
            target = random.Random(room["role_seed"] + room["round"] * 97 + room["match"] * 1009).choice(leaders)
        if target is not None:
            room["players"][target]["alive"] = False
            room["public_roles"][str(target)] = room["players"][target]["role"]
        for seat, voted in room["votes"].items():
            if voted is not None and room["players"][voted]["role"] == "imposter":
                room["players"][int(seat)]["vote_hits"] += 1
        event = {"kind": "verdict", "ejected": target, "tie": tie,
                 "votes": len(room["votes"]), "tally": {str(i): count for i, count in counts.items()},
                 "alignment": None if target is None else
                 "imposter" if room["players"][target]["role"] == "imposter" else "innocent"}
        room["event"] = event
        room["history"].append(event)
        if self.winner(room):
            self.end(room)
        else:
            room["now"] = self.clock()
            self.phase(room, "verdict", 8)

    def end(self, room):
        winner = self.winner(room)
        for player in room["players"]:
            own = "imposters" if player["role"] == "imposter" else "innocents"
            player["score"] += (3 if player["alive"] else 2) if own == winner else 0
            player["score"] += (player["vote_hits"] + player["suspicion_hits"] if own == "innocents" else int(player["night_token"]))
            player["score"] += int(player["prediction"] == winner)
        room["event"] = {"kind": "end", "winner": winner, "last": room["event"]}
        awards = []
        if max(p["suspicion_hits"] for p in room["players"]) > 0:
            awards.append({"kind": "eagle_eyes", "seat": max(range(len(room["players"])), key=lambda i: room["players"][i]["suspicion_hits"])})
        if max(p["vote_hits"] for p in room["players"]) > 0:
            awards.append({"kind": "jury_star", "seat": max(range(len(room["players"])), key=lambda i: room["players"][i]["vote_hits"])})
        survivors = [i for i, p in enumerate(room["players"]) if p["alive"]]
        if survivors:
            awards.append({"kind": "plot_armor", "seat": survivors[0]})
        ghost = next((i for i, p in enumerate(room["players"]) if not p["alive"] and p["prediction"] == winner), None)
        if ghost is not None:
            awards.append({"kind": "ghost_whisperer", "seat": ghost})
        room["event"]["awards"] = awards
        room["now"] = self.clock()
        self.phase(room, "end")

    def resolve(self, room):
        # A sleeping server can skip multiple expired phases, but only while
        # the game can progress without fabricating a player's choice.
        for _ in range(8):
            if room["deadline"] is None or self.clock() < room["deadline"]:
                return
            phase = room["phase"]
            if phase == "reveal":
                self.begin_night(room)
            elif phase == "night":
                self.begin_confirm(room)
            elif phase == "confirm":
                self.finish_night(room)
            elif phase == "morning":
                room["now"] = self.clock()
                self.phase(room, "discussion", room["settings"]["discussion"])
            elif phase == "discussion":
                room["votes"] = {}
                room["now"] = self.clock()
                self.phase(room, "vote", room["settings"]["vote"])
            elif phase in {"vote", "runoff"}:
                self.finish_vote(room)
            elif phase == "verdict":
                self.begin_night(room)
            else:
                return

    def view(self, room, seat):
        player = room["players"][seat]
        phase = room["phase"]
        alive = [{"name": p["name"], "alive": p["alive"],
                  "role": p["role"] if phase == "end" else None,
                  "score": p["score"] if phase == "end" else None,
                  "alignment": ("imposter" if p["role"] == "imposter" else "innocent")
                  if phase == "end" else room["public_roles"].get(str(i))}
                 for i, p in enumerate(room["players"])]
        night = room.get("night", {})
        own_pick = night.get("picks", {}).get(str(seat))
        confirmed = night.get("confirms", {}).get(str(seat))
        confirmation_target = (night.get("candidate") if player["role"] == "imposter" else own_pick) if phase == "confirm" else None
        event = room["event"] if phase in {"morning", "discussion", "vote", "runoff", "verdict", "end"} else None
        return {"id": room["id"], "code": room["code"], "phase": phase,
                "revision": room["revision"], "round": room["round"], "match": room["match"],
                "host": seat == room["host"], "seat": seat, "name": player["name"],
                "players": alive, "settings": room["settings"], "deadline": room["deadline"],
                "seconds_left": max(0, int((room["deadline"] or 0) - self.clock())) if room["deadline"] else None,
                "ready": player["ready"], "role": player["role"] if phase != "lobby" else None,
                "teammates": [i for i, p in enumerate(room["players"]) if i != seat and p["role"] == "imposter"]
                             if player["role"] == "imposter" and phase != "lobby" else [],
                "alive": player["alive"], "used_power": player["used_power"],
                "submitted": str(seat) in (night.get("picks", {}) if phase == "night" else
                                            night.get("confirms", {}) if phase == "confirm" else room["votes"]),
                "my_pick": own_pick if phase == "night" else None,
                "confirmation_target": confirmation_target,
                "my_confirmation": confirmed if phase == "confirm" else None,
                "my_vote": room["votes"].get(str(seat)) if phase in {"vote", "runoff"} else None,
                "runoff": room["runoff"] if phase == "runoff" else None,
                "talk_prompt": room.get("talk_prompt") if phase == "discussion" else None,
                "event": event, "clue": player["clue"] if phase not in {"lobby", "reveal", "night", "confirm"} else None,
                "prediction": player["prediction"], "score": player["score"] if phase == "end" else None,
                "history": room["history"] if phase == "end" else None}
