"""Bounded, expiring family-story rooms. SQLite serializes concurrent votes."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time

SCENES = json.loads((Path(__file__).resolve().parents[2] / "app/games-story.json").read_text(encoding="utf-8"))
TTL = 7200
VOTE_SECONDS = 60


class RoomError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def identity(who):
    return hashlib.sha256((who[0] + ":" + who[1]["id"]).encode()).hexdigest()


class Rooms:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS rooms (id TEXT PRIMARY KEY, code TEXT UNIQUE, expires REAL, host TEXT, data TEXT)")
            con.execute("CREATE TABLE IF NOT EXISTS attempts (actor TEXT PRIMARY KEY, at REAL, count INTEGER)")
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM rooms WHERE expires <= ?", (self.clock(),))
            con.execute("DELETE FROM attempts WHERE at < ?", (self.clock() - 60,))
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def save(con, room):
        con.execute("UPDATE rooms SET data=? WHERE id=?", (json.dumps(room), room["id"]))

    def throttle(self, actor):
        # Commit failed join attempts too; rejecting an invalid code must not reset this.
        with self.db() as con:
            row = con.execute("SELECT count FROM attempts WHERE actor=?", (actor,)).fetchone()
            if row and row[0] >= 10:
                raise RoomError(429, "too_many_attempts")
            con.execute("INSERT INTO attempts VALUES(?,?,1) ON CONFLICT(actor) DO UPDATE SET count=count+1",
                        (actor, self.clock()))

    def create(self, actor):
        with self.db() as con:
            if con.execute("SELECT count(*) FROM rooms WHERE host=?", (actor,)).fetchone()[0] >= 2:
                raise RoomError(429, "room_limit")
            if con.execute("SELECT count(*) FROM rooms").fetchone()[0] >= 128:
                raise RoomError(429, "room_limit")
            for _ in range(10):
                code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
                if not con.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone():
                    break
            else:
                raise RoomError(503, "try_again")
            room = dict(id=secrets.token_hex(16), code=code, host=actor, players=[],
                        phase="lobby", scene="kitchen", round=0, votes={}, counts=None,
                        selected=None, deadline=None, expires=self.clock()+TTL, completed=0)
            con.execute("INSERT INTO rooms VALUES(?,?,?,?,?)",
                        (room["id"], code, room["expires"], actor, json.dumps(room)))
            return self.view(room, actor)

    def load(self, con, rid, actor):
        row = con.execute("SELECT data FROM rooms WHERE id=? AND expires>?", (rid, self.clock())).fetchone()
        if not row:
            raise RoomError(404, "room_unavailable")
        room = json.loads(row[0])
        if actor != room["host"] and actor not in room["players"]:
            raise RoomError(404, "room_unavailable")
        self.resolve(room)
        return room

    def join(self, code, actor):
        self.throttle(actor)
        with self.db() as con:
            row = con.execute("SELECT data FROM rooms WHERE code=? AND expires>?", (code, self.clock())).fetchone()
            if not row:
                raise RoomError(404, "room_unavailable")
            room = json.loads(row[0])
            if actor not in room["players"]:
                if room["phase"] != "lobby":
                    raise RoomError(409, "already_started")
                if len(room["players"]) >= 8:
                    raise RoomError(409, "room_full")
                room["players"].append(actor)
            self.resolve(room)
            self.save(con, room)
            return self.view(room, actor)

    def resolve(self, room):
        if room["phase"] != "voting":
            return
        if not room["votes"]:
            if self.clock() >= room["deadline"]:
                room["phase"] = "waiting"
            return
        if len(room["votes"]) < len(room["players"]) and self.clock() < room["deadline"]:
            return
        counts = [list(room["votes"].values()).count(i) for i in range(len(SCENES[room["scene"]]["choices"]))]
        # Fixed visible tie policy: the first listed tied choice wins.
        selected = counts.index(max(counts))
        room.update(phase="result", counts=counts, selected=selected)

    def state(self, rid, actor):
        with self.db() as con:
            room = self.load(con, rid, actor)
            self.save(con, room)
            return self.view(room, actor)

    def act(self, rid, actor, action, round_number, choice=None):
        with self.db() as con:
            room = self.load(con, rid, actor)
            if type(round_number) is not int or round_number != room["round"]:
                raise RoomError(409, "stale_round")
            if action == "vote":
                if actor not in room["players"]:
                    raise RoomError(403, "join_to_vote")
                options = SCENES[room["scene"]]["choices"]
                if type(choice) is not int or choice < 0 or choice >= len(options):
                    raise RoomError(400, "invalid_choice")
                if actor in room["votes"]:
                    if room["votes"][actor] != choice:
                        raise RoomError(409, "vote_locked")
                    return self.view(room, actor)  # identical retries are safe
                if room["phase"] != "voting":
                    raise RoomError(409, "voting_closed")
                room["votes"][actor] = choice
                self.resolve(room)
            else:
                if actor != room["host"]:
                    raise RoomError(403, "host_only")
                if action == "close":
                    con.execute("DELETE FROM rooms WHERE id=?", (rid,))
                    return {"closed": True}
                if action == "start":
                    if room["phase"] != "lobby" or len(room["players"]) < 2:
                        raise RoomError(409, "need_two_players")
                    room.update(phase="voting", deadline=self.clock()+VOTE_SECONDS)
                elif action == "retry":
                    if room["phase"] != "waiting":
                        raise RoomError(409, "wait_for_votes")
                    # A new round identity rejects delayed votes from the timed-out round.
                    room.update(phase="voting", round=room["round"]+1, deadline=self.clock()+VOTE_SECONDS)
                elif action == "next":
                    if room["phase"] != "result":
                        raise RoomError(409, "wait_for_votes")
                    scene = SCENES[room["scene"]]["choices"][room["selected"]]["next"]
                    complete = not SCENES[scene]["choices"]
                    room.update(scene=scene, round=room["round"]+1, votes={}, counts=None,
                                selected=None, completed=SCENES[scene]["chapter"] if complete else SCENES[scene]["chapter"]-1,
                                phase="complete" if complete else "voting",
                                deadline=None if complete else self.clock()+VOTE_SECONDS)
                else:
                    raise RoomError(400, "invalid_action")
            self.save(con, room)
            return self.view(room, actor)

    def view(self, room, actor):
        scene = SCENES[room["scene"]]
        # No device identities or other players' individual votes leave the store.
        return dict(id=room["id"], code=room["code"], phase=room["phase"], round=room["round"],
                    title=scene["title"], body=scene["body"],
                    choices=[c["label"] for c in scene["choices"]],
                    players=len(room["players"]), player=room["players"].index(actor)+1 if actor in room["players"] else None,
                    host=actor==room["host"], voted=len(room["votes"]), my_vote=room["votes"].get(actor),
                    counts=room["counts"], selected=room["selected"], expires=room["expires"],
                    deadline=room["deadline"], seconds_left=max(0, int((room["deadline"] or self.clock())-self.clock())),
                    completed=room["completed"], tie_rule="first_listed")
