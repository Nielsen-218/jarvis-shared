"""Isolated persistence + actual vendored Three renderer test invoker."""
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import companion_home as home


def test_default_is_fresh_and_does_not_write(tmp_path):
    path = tmp_path / "home.json"
    a = home.load_home(path)
    a["pet"]["name"] = "Changed"
    assert home.load_home(path)["pet"]["name"] == "Pip"
    assert not path.exists()
    assert home.validate_home(home.default_home()) == home.default_home()


def test_roundtrip_all_capabilities(tmp_path):
    path = tmp_path / "nested" / "home.json"
    patch = {"room": "reading", "activity": "read", "lamp": False, "accent": "#ab12ef",
             "furniture": {"reading": {"chair": {"position": [.06, 0, .03], "rotation": .5}}},
             "inventory": [{"id": "first", "kind": "star", "color": "#ffaa00"}],
             "pet": {"name": "Pixel", "enabled": False},
             "scrapbook": [{"title": "First room", "text": "A quiet corner.", "color": "#ffeecc"}],
             "routines": {"enabled": True, "entries": [
                 {"time": "22:00", "room": "bedroom", "activity": "sleep"},
                 {"time": "07:00", "room": "kitchen", "activity": "cook"}]}}
    saved = home.update_home(patch, path)
    assert home.load_home(path) == saved
    assert saved["furniture"]["reading"]["chair"]["rotation"] == .5
    assert saved["pet"]["color"] == "#e9b76c"
    assert not list(path.parent.glob("*.tmp"))
    patch["scrapbook"][0]["text"] = "mutated"
    assert home.load_home(path) == saved
    home.update_home({"room": "bedroom", "activity": "sleep"}, path)
    assert home.load_home(path)["activity"] == "sleep"
    assert home.update_home({"activity": "wake"}, path)["activity"] == "wake"


@pytest.mark.parametrize("patch", [
    {"schema": True}, {"schema": 2}, {"room": "outside"}, {"room": []},
    {"activity": "cook"}, {"lamp": 1}, {"accent": "url(https://example.com)"},
    {"unexpected": True}, {"pet": {"enabled": "yes"}}, {"pet": {"name": "\x00"}},
    {"inventory": [{}]}, {"inventory": [{"id": "x", "kind": "unknown", "color": "#ffffff"}]},
    {"scrapbook": [{"title": "a", "text": "b", "color": "red"}]},
    {"routines": {"entries": [{"time": "24:00", "room": "bedroom", "activity": "sleep"}]}},
    {"routines": {"entries": [{"time": "07:00", "room": "bedroom", "activity": "cook"}]}},
    {"furniture": {"bedroom": {"bed": {"position": [1, 0, 0]}}}},
    {"furniture": {"bedroom": {"bed": {"position": [0, .1, 0]}}}},
    {"furniture": {"bedroom": {"bed": {"rotation": math.nan}}}},
    {"furniture": {"bedroom": {"bed": {"rotation": math.inf}}}},
    {"furniture": {"bedroom": {"unknown": {}}}},
])
def test_invalid_update_preserves_file(tmp_path, patch):
    path = tmp_path / "home.json"
    home.save_home(home.default_home(), path)
    before = path.read_bytes()
    with pytest.raises(home.HomeError):
        home.update_home(patch, path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,limit,item", [
    ("inventory", 12, {"id": "x", "kind": "badge", "color": "#aabbcc"}),
    ("scrapbook", 24, {"title": "a", "text": "b", "color": "#aabbcc"}),
])
def test_collection_bounds(tmp_path, field, limit, item):
    with pytest.raises(home.HomeError):
        home.update_home({field: [copy.deepcopy(item) for _ in range(limit + 1)]}, tmp_path / "home.json")


def test_duplicate_ids_and_times(tmp_path):
    item = {"id": "same", "kind": "badge", "color": "#aabbcc"}
    routine = {"time": "01:00", "room": "bedroom", "activity": "sleep"}
    for patch in ({"inventory": [item, item]}, {"routines": {"entries": [routine, routine]}}):
        with pytest.raises(home.HomeError):
            home.update_home(patch, tmp_path / "home.json")


@pytest.mark.parametrize("payload", ["{broken", "null", "[]", '{"schema":99}', "x" * (home.MAX_HOME_BYTES + 1)],
                         ids=["broken", "null", "array", "future-schema", "oversized"])
def test_corrupt_file_is_not_overwritten(tmp_path, payload):
    path = tmp_path / "home.json"
    path.write_text(payload, encoding="utf-8")
    assert home.load_home(path) == home.default_home()
    assert path.read_text(encoding="utf-8") == payload


def test_environment_is_evaluated_per_call_and_explicit_path_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_COMPANION_DATA_DIR", str(tmp_path / "isolated"))
    home.update_home({"lamp": False})
    assert (tmp_path / "isolated" / "companion-home.json").exists()
    explicit = tmp_path / "explicit.json"
    assert home.load_home(explicit)["lamp"] is True
    home.update_home({"accent": "#112233"}, explicit)
    assert home.load_home()["accent"] != "#112233"
    monkeypatch.setenv("JARVIS_COMPANION_DATA_DIR", str(tmp_path / "second"))
    assert home.load_home()["lamp"] is True


def test_atomic_failure_preserves_previous_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "home.json"
    home.save_home(home.default_home(), path)
    before = path.read_bytes()
    def fail(*args):
        raise OSError("simulated replacement failure")
    monkeypatch.setattr(home.os, "replace", fail)
    with pytest.raises(OSError):
        home.update_home({"lamp": False}, path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_threaded_updates_do_not_lose_disjoint_fields(tmp_path):
    path = tmp_path / "home.json"
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda p: home.update_home(p, path), [
            {"lamp": False}, {"accent": "#112233"}, {"pet": {"name": "Pixel"}}]))
    saved = home.load_home(path)
    assert (saved["lamp"], saved["accent"], saved["pet"]["name"]) == (False, "#112233", "Pixel")


def test_maximum_unicode_scrapbook_roundtrips(tmp_path):
    path = tmp_path / "home.json"
    page = {"title": "🌟" * 80, "text": "🌟" * 500, "color": "#aabbcc"}
    saved = home.update_home({"scrapbook": [page] * 24}, path)
    assert home.load_home(path) == saved


def test_real_three_node_suite(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for actual vendored Three home geometry tests")
    snapshot = home.update_home({"room": "workshop", "activity": "tinker"}, tmp_path / "home.json")
    env = {**os.environ, "HOME_TEST_SNAPSHOT": json.dumps(snapshot),
           "HOME_TEST_DEFAULT": json.dumps(home.default_home())}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / "tests/node/test_home.mjs")], cwd=root,
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["failed"] == 0
    assert report["passed"] >= 60
