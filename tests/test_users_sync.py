"""users.json is shared by Syncthing and written by two machines. These tests simulate the PC and the laptop (two data
folders and a fake sync step that does what Syncthing does: copy the changed file, or keep the newer one and leave a
users.sync-conflict copy when both changed). All names, tokens and endpoints are invented."""
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import pytest

from core import appserver, storage, users


class Machine:
    def __init__(self, root: Path, name: str):
        self.name = name
        self.data = root / name / "data"
        self.data.mkdir(parents=True)
        self.synced = None            # users.json bytes at the last sync

    def __enter__(self):
        self._prev = storage.DATA
        storage.DATA = self.data
        users._conflicts_checked = -1e9
        return self

    def __exit__(self, *exc):
        storage.DATA = self._prev

    @property
    def file(self) -> Path:
        return self.data / "users.json"

    def registry(self) -> dict:
        return json.loads(self.file.read_text(encoding="utf-8"))

    def devices(self, uid="owner") -> dict:
        return {d["name"]: d for d in self.registry()["users"][uid].get("devices", [])}


def sync(a: Machine, b: Machine, tag="ABCDEFG") -> None:
    """What Syncthing does with users.json: one side changed -> copy; both changed -> keep the newer, the loser becomes a
    conflict copy (on both machines). Conflict copies themselves are copied to the other side."""
    fa, fb = a.file, b.file
    da = fa.read_bytes() if fa.exists() else None
    db = fb.read_bytes() if fb.exists() else None
    if da != db:
        ca, cb = da != a.synced, db != b.synced
        if ca and cb:
            winner, loser = (a, b) if fa.stat().st_mtime_ns >= fb.stat().st_mtime_ns else (b, a)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            name = f"users.sync-conflict-{stamp}-{tag}.json"
            for m in (a, b):
                (m.data / name).write_bytes(loser.file.read_bytes())
            shutil.copy2(winner.file, loser.file)
        elif ca:
            shutil.copy2(fa, fb)
        else:
            shutil.copy2(fb, fa)
    a.synced = b.synced = a.file.read_bytes() if a.file.exists() else None
    for src, dst in ((a, b), (b, a)):
        for c in src.data.glob("users.sync-conflict-*"):
            if not (dst.data / c.name).exists() and not (dst.data / users._ARCHIVE_DIR / c.name).exists():
                shutil.copy2(c, dst.data / c.name)


@pytest.fixture
def pc_and_laptop(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_last_good", {})
    monkeypatch.setattr(users, "_conflicts_seen", set())
    monkeypatch.setattr(users, "_unreadable_said", False)
    pc, laptop = Machine(tmp_path, "pc"), Machine(tmp_path, "laptop")
    with laptop:
        users.all_users()
        appserver.add_device("owner", "phone-one")
    sync(pc, laptop)
    return pc, laptop


def _everywhere(pc, laptop, rounds=3):
    """Let both machines read (which merges conflict copies) and sync, until quiet."""
    for _ in range(rounds):
        for m in (pc, laptop):
            with m:
                users.all_users()
        sync(pc, laptop)


def test_a_pairing_on_one_machine_and_a_push_subscription_on_the_other_both_survive(pc_and_laptop):
    pc, laptop = pc_and_laptop
    with laptop:
        appserver.add_device("owner", "phone-two")
    with pc:
        did = pc.devices()["phone-one"]["id"]
        appserver.set_push("owner", did, {"endpoint": "https://push.invalid/abc", "keys": {"p256dh": "x", "auth": "y"}})
    sync(pc, laptop)
    assert list(pc.data.glob("users.sync-conflict-*")), "the simulation must really produce a conflict"
    _everywhere(pc, laptop)
    for m in (pc, laptop):
        devs = m.devices()
        assert set(devs) == {"phone-one", "phone-two"}
        assert devs["phone-one"]["push"]["endpoint"] == "https://push.invalid/abc"
        assert not list(m.data.glob("users.sync-conflict-*"))
        assert list((m.data / users._ARCHIVE_DIR).glob("users.sync-conflict-*"))


def test_a_removal_on_one_machine_during_an_add_on_the_other_keeps_both(pc_and_laptop):
    pc, laptop = pc_and_laptop
    with pc:
        users.update("owner", devices=[d for d in users.get("owner")["devices"] if d["name"] != "phone-one"])
    with laptop:
        appserver.add_device("owner", "phone-two")
    sync(pc, laptop)
    _everywhere(pc, laptop)
    for m in (pc, laptop):
        assert set(m.devices()) == {"phone-two"}, m.name
        assert m.registry()["tombstones"]["devices"]


def test_removed_token_stays_dead_when_the_other_machine_still_has_it(pc_and_laptop):
    pc, laptop = pc_and_laptop
    with laptop:
        token = appserver.add_device("owner", "claude-verify")
    sync(pc, laptop)
    with pc:
        users.update("owner", devices=[d for d in users.get("owner")["devices"] if d["name"] != "claude-verify"])
        assert appserver.find_device(token) is None
    # the laptop never saw the removal and writes something unrelated from its stale view
    with laptop:
        users.update("owner", name="Test Owner")
    sync(pc, laptop)
    _everywhere(pc, laptop)
    for m in (pc, laptop):
        with m:
            assert appserver.find_device(token) is None, m.name
            assert "claude-verify" not in m.devices()


def test_a_conflict_copy_holding_a_revoked_device_does_not_resurrect_it(pc_and_laptop):
    pc, laptop = pc_and_laptop
    with laptop:
        token = appserver.add_device("owner", "temp-device")
        stale_copy = json.loads(laptop.file.read_text(encoding="utf-8"))
        users.update("owner", devices=[d for d in users.get("owner")["devices"] if d["name"] != "temp-device"])
        (laptop.data / "users.sync-conflict-20261113-101500-ABCDEFG.json").write_text(json.dumps(stale_copy), encoding="utf-8")
        users._conflicts_checked = -1e9
        assert appserver.find_device(token) is None
        assert "temp-device" not in laptop.devices()


def test_unpair_stays_unpaired_even_with_a_copy_that_still_has_the_phones(pc_and_laptop):
    pc, laptop = pc_and_laptop
    with laptop:
        before = json.loads(laptop.file.read_text(encoding="utf-8"))
        users.update("owner", devices=[])
        (laptop.data / "users.sync-conflict-20261113-101500-ABCDEFG.json").write_text(json.dumps(before), encoding="utf-8")
        users._conflicts_checked = -1e9
        users.all_users()
    assert laptop.devices() == {}


def test_old_conflict_copies_change_nothing_and_are_archived(pc_and_laptop):
    pc, laptop = pc_and_laptop
    with laptop:
        users.update("owner", name="Test Owner")                       # stamps sync_since
        since = datetime.fromisoformat(users._parse(laptop.file)["sync_since"]).timestamp()
        old = json.loads(laptop.file.read_text(encoding="utf-8"))
        old.pop("sync_since")                                           # written by the deployed code: no stamp
        old["users"]["owner"]["devices"].append({"id": "dead01", "token_hash": "0" * 64, "name": "long-gone",
                                                 "added": "2026-01-01T00:00", "push": None})
        old["users"]["ghost"] = {"name": "Ghost", "role": "member", "devices": []}
        names = []
        for i in range(3):
            c = laptop.data / f"users.sync-conflict-20261030-0{i}0000-ABCDEF{i}.json"
            c.write_text(json.dumps(old), encoding="utf-8")
            os.utime(c, (since - 86400, since - 86400))
            names.append(c.name)
        keep = laptop.file.read_bytes()
        users._conflicts_checked = -1e9
        users.all_users()
    assert laptop.file.read_bytes() == keep
    assert not list(laptop.data.glob("users.sync-conflict-*"))
    assert sorted(p.name for p in (laptop.data / users._ARCHIVE_DIR).iterdir()) == sorted(names)


def test_copies_present_before_the_scheme_existed_are_archived_not_merged(pc_and_laptop):
    pc, laptop = pc_and_laptop
    raw = laptop.registry()
    raw.pop("sync_since", None)
    laptop.file.write_text(json.dumps(raw), encoding="utf-8")           # a file written by the deployed code
    raw["users"]["owner"]["devices"].append({"id": "dead01", "token_hash": "1" * 64, "name": "removed-long-ago",
                                             "added": "x", "push": None})
    c = laptop.data / "users.sync-conflict-20261030-010000-ABCDEFG.json"
    c.write_text(json.dumps(raw), encoding="utf-8")
    with laptop:
        users.all_users()
    assert "removed-long-ago" not in laptop.devices() and not c.exists()
    assert laptop.registry()["sync_since"]


def test_an_unreadable_file_is_never_replaced_by_an_empty_registry(pc_and_laptop, capsys):
    pc, laptop = pc_and_laptop
    with laptop:
        good = laptop.file.read_bytes()
        laptop.file.write_bytes(good[: len(good) // 2])                 # a half-synced file
        assert "owner" in users.all_users()
        assert {d["name"] for d in users.get("owner")["devices"]} == {"phone-one"}
        with pytest.raises(RuntimeError):
            appserver.add_device("owner", "phone-two")
        assert laptop.file.read_bytes() == good[: len(good) // 2]       # nothing written over it
        laptop.file.write_bytes(good)                                   # the sync completes
        appserver.add_device("owner", "phone-two")
    assert set(laptop.devices()) == {"phone-one", "phone-two"}
    assert "unreadable" in capsys.readouterr().err


def test_after_a_restart_an_unreadable_file_falls_back_to_the_saved_copy(pc_and_laptop, monkeypatch):
    pc, laptop = pc_and_laptop
    with laptop:
        laptop.file.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(users, "_last_good", {})                     # a fresh process
        assert {d["name"] for d in users.get("owner")["devices"]} == {"phone-one"}


def test_first_run_creates_the_default_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_last_good", {})
    m = Machine(tmp_path, "fresh")
    with m:
        assert list(users.all_users()) == ["owner"]
    assert "owner" in m.registry()["users"]


def test_write_is_atomic_no_temp_file_is_left(pc_and_laptop):
    pc, laptop = pc_and_laptop
    assert not list(laptop.data.glob("*.tmp"))


def test_notices_and_logs_never_carry_hashes_or_endpoints(pc_and_laptop, capsys):
    pc, laptop = pc_and_laptop
    with pc:
        did = pc.devices()["phone-one"]["id"]
        appserver.set_push("owner", did, {"endpoint": "https://push.invalid/secret-endpoint", "keys": {"p256dh": "k1", "auth": "k2"}})
    with laptop:
        appserver.add_device("owner", "phone-two")
    sync(pc, laptop)
    _everywhere(pc, laptop)
    out = capsys.readouterr()
    text = out.out + out.err
    assert "secret-endpoint" not in text and "token_hash" not in text
    for d in pc.devices().values():
        assert d["token_hash"] not in text
    assert "users.sync-conflict" in out.err


def test_new_reader_old_writer(pc_and_laptop):
    """The deployed code loads the dict, changes it and saves the whole dict back: the new keys survive, and the new
    reader reads what it wrote."""
    pc, laptop = pc_and_laptop
    with laptop:
        users.update("owner", devices=[])                                # tombstones written
    raw = json.loads(laptop.file.read_text(encoding="utf-8"))
    assert raw["tombstones"]["devices"]
    raw["users"]["owner"]["devices"].append({"id": "aaaaaa", "token_hash": "2" * 64, "name": "old-code-pairing",
                                              "added": "2026-11-13T10:00", "push": None})
    laptop.file.write_text(json.dumps(raw, indent=2), encoding="utf-8")  # old writer
    with laptop:
        assert "old-code-pairing" in {d["name"] for d in users.get("owner")["devices"]}
    assert laptop.registry()["tombstones"]["devices"] == raw["tombstones"]["devices"]


def test_old_reader_new_writer(pc_and_laptop):
    """The deployed reader only needs data['users'][uid]['devices'][i]['token_hash'/'id'/'name'/'push'] and the owner
    entry; the new file keeps that shape (extra keys are ignored by it)."""
    pc, laptop = pc_and_laptop
    with laptop:
        appserver.add_device("owner", "phone-two")
        users.update("owner", devices=users.get("owner")["devices"][:1])
    raw = json.loads(laptop.file.read_text(encoding="utf-8"))
    assert set(raw) == {"users", "tombstones", "sync_since"}
    owner = raw["users"]["owner"]
    assert {"name", "titles", "role", "devices"} <= set(owner)
    for d in owner["devices"]:
        assert {"id", "token_hash", "name", "added", "push"} <= set(d)

    def old_find_device(token_hash):                                      # the pre-change reader, same logic
        for uid, person in raw["users"].items():
            for d in person.get("devices", []):
                if d["token_hash"] == token_hash:
                    return uid, d
    assert old_find_device(owner["devices"][0]["token_hash"])[0] == "owner"


def test_add_keeps_an_existing_persons_devices(pc_and_laptop):
    pc, laptop = pc_and_laptop
    with laptop:
        uid = users.add("Mx Invented", {"en": "x"})
        appserver.add_device(uid, "kid-tablet")
        assert users.add("Mx Invented", {"en": "y"}) == uid
        assert {d["name"] for d in users.get(uid)["devices"]} == {"kid-tablet"}


def test_unchanged_push_writes_nothing(pc_and_laptop):
    pc, laptop = pc_and_laptop
    with laptop:
        did = laptop.devices()["phone-one"]["id"]
        before = laptop.file.stat().st_mtime_ns
        time.sleep(0.01)
        appserver.set_push("owner", did, None)
        appserver.set_push("owner", "nosuch", {"endpoint": "e"})
    assert laptop.file.stat().st_mtime_ns == before


def _copy_of(m, reg, name="users.sync-conflict-20261113-000000-ABCDEFG.json"):
    c = m.data / name
    c.write_text(json.dumps(reg), encoding="utf-8")
    return c


def test_a_fresh_machines_default_registry_does_not_swallow_the_real_one_arriving_as_a_conflict_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_last_good", {})
    monkeypatch.setattr(users, "_conflicts_seen", set())
    fresh = Machine(tmp_path, "fresh")
    real = {"users": {"owner": {"name": "Test Owner", "titles": {"en": "madam"}, "role": "owner", "devices": [
        {"id": "aa1111", "token_hash": "a" * 64, "name": "phone-real", "added": "2026-11-01T10:00", "push": {"endpoint": "e1"}}]}},
        "sync_since": "2026-11-10T00:00:00.000000"}
    with fresh:
        users.all_users()                                    # the default owner-only file is written
        _copy_of(fresh, real)
        users._conflicts_checked = -1e9
        owner = users.get("owner")
    assert [d["name"] for d in owner["devices"]] == ["phone-real"] and owner["name"] == "Test Owner"
    assert owner["titles"]["en"] == "madam"
    assert list(fresh.devices()) == ["phone-real"]
    assert not list(fresh.data.glob("users.sync-conflict-*"))


def test_a_conflict_copy_is_merged_even_when_the_other_machines_clock_is_behind(pc_and_laptop):
    pc, laptop = pc_and_laptop
    with laptop:
        users.update("owner", name="Test Owner")
        reg = laptop.registry()
        reg["users"]["owner"]["devices"].append({"id": "bb2222", "token_hash": "b" * 64, "name": "phone-late-clock",
                                                 "added": "2026-11-13T10:00", "push": None})
        c = _copy_of(laptop, reg)
        os.utime(c, (time.time() - 300, time.time() - 300))      # written on a machine whose clock is 5 minutes behind
        users._conflicts_checked = -1e9
        assert "phone-late-clock" in {d["name"] for d in users.get("owner")["devices"]}


def test_a_push_change_after_seeing_the_other_sides_stamp_wins_even_with_a_slow_clock(pc_and_laptop):
    pc, laptop = pc_and_laptop
    far = "2099-01-01T00:00:00.000000"
    with laptop:
        users.mutate_devices("owner", lambda ds: [dict(d, push={"endpoint": "e-far"}) for d in ds])
        raw = laptop.registry()
        raw["users"]["owner"]["devices"][0]["push_at"] = far     # the other machine's clock is far ahead
        laptop.file.write_text(json.dumps(raw), encoding="utf-8")
        users.mutate_devices("owner", lambda ds: [dict(d, push={"endpoint": "e-later"}) for d in ds])
        dev = laptop.registry()["users"]["owner"]["devices"][0]
        assert dev["push"] == {"endpoint": "e-later"} and dev["push_at"] > far


def test_a_copy_without_push_at_never_overwrites_a_stamped_push(pc_and_laptop):
    """An old writer's push carries no stamp: it counts as oldest, so the stamped subscription stays (the documented
    limit: an old machine's unsubscribe is not seen until it runs this code)."""
    pc, laptop = pc_and_laptop
    with laptop:
        users.mutate_devices("owner", lambda ds: [dict(d, push={"endpoint": "e1"}) for d in ds])
        reg = laptop.registry()
        reg["users"]["owner"]["devices"][0]["push"] = {"endpoint": "e2"}
        reg["users"]["owner"]["devices"][0].pop("push_at")
        _copy_of(laptop, reg)
        users._conflicts_checked = -1e9
        assert users.get("owner")["devices"][0]["push"] == {"endpoint": "e1"}
