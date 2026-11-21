"""Command Center polish: the profile photo and tagline, app icons and pinned apps, and the Workout / Focus / Play views
inside the window. No real window, model, network or wall clock."""
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def _png(w=600, h=400, color=(200, 30, 30), fmt="PNG"):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, fmt)
    return out.getvalue()


class _Brain:
    def __init__(self, uid):
        self.uid = uid

    def ask(self, content, on_sentence):
        on_sentence("ok")


@pytest.fixture
def server(monkeypatch):
    from core import appserver
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    return TestClient(appserver.AppServer(lambda uid: _Brain(uid)).app)


def _pair(client, uid="owner", code="515151"):
    from tools import voice
    voice.pair_codes[code] = uid
    return {"Authorization": f"Bearer {client.post('/api/pair', json={'code': code, 'device': 'Desk'}).json()['token']}"}


def _upload(client, headers, data, name="me.png", ctype="image/png"):
    return client.post("/api/me/photo", files={"file": (name, data, ctype)}, headers=headers)


# ------------------------------------------------------------------ step 1: profile
def test_me_has_default_tagline_and_no_photo(server):
    me = server.get("/api/me", headers=_pair(server)).json()
    assert me["photo"] == "" and me["tagline"] == "Your tagline here"


def test_upload_is_cropped_square_stored_per_user_and_returned(server):
    from PIL import Image
    from core import profile, storage
    h = _pair(server)
    r = _upload(server, h, _png(600, 400))
    assert r.status_code == 200 and r.json()["photo"].startswith("data:image/jpeg;base64,")
    img = Image.open(profile.photo_path("owner"))
    assert img.size == (profile.SIDE, profile.SIDE) and img.format == "JPEG"
    assert profile.photo_path("owner").is_relative_to(storage.user_dir("owner"))
    assert server.get("/api/me", headers=h).json()["photo"] == r.json()["photo"]


def test_upload_limits_and_types(server, monkeypatch):
    from core import profile
    h = _pair(server)
    assert _upload(server, h, b"not an image").status_code == 400
    assert _upload(server, h, b"").status_code == 400
    assert _upload(server, h, _png(40, 40, fmt="GIF"), "a.gif", "image/gif").status_code == 415
    monkeypatch.setattr(profile, "MAX_UPLOAD", 2000)
    assert _upload(server, h, _png(900, 900)).status_code == 413
    monkeypatch.setattr(profile, "MAX_UPLOAD", 8 * 1024 * 1024)
    monkeypatch.setattr(profile, "MAX_PIXELS", 100)
    assert _upload(server, h, _png(50, 50)).status_code == 413
    assert not profile.photo_path("owner").exists()          # nothing half-stored by the refusals


def test_profile_routes_need_a_token_and_the_owner(server):
    from core import users
    assert server.post("/api/me/photo", files={"file": ("a.png", _png(), "image/png")}).status_code == 401
    assert server.post("/api/me/tagline", json={"tagline": "x"}).status_code == 401
    wife = users.add("Marie", {"en": "madame"}, "wife")
    h = _pair(server, wife, "626262")
    assert _upload(server, h, _png()).status_code == 403
    assert server.post("/api/me/tagline", json={"tagline": "x"}, headers=h).status_code == 403


def test_tagline_is_per_user_cleaned_and_resets(server):
    from core import profile, users
    h = _pair(server)
    assert server.post("/api/me/tagline", json={"tagline": "  Builder \n of   things  " + "z" * 200}, headers=h).json()["tagline"].startswith("Builder of things")
    assert len(server.get("/api/me", headers=h).json()["tagline"]) <= profile.MAX_TAGLINE
    wife = users.add("Marie", {"en": "madame"}, "wife")
    assert profile.tagline(wife) == profile.DEFAULT_TAGLINE      # his words are not hers
    assert server.post("/api/me/tagline", json={"tagline": ""}, headers=h).json()["tagline"] == profile.DEFAULT_TAGLINE


def test_set_profile_photo_script(tmp_path, capsys):
    import importlib.util
    from core import profile
    spec = importlib.util.spec_from_file_location("set_profile_photo", ROOT / "scripts" / "set_profile_photo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    img = tmp_path / "p.jpg"
    img.write_bytes(_png(500, 700, fmt="JPEG"))
    assert mod.main(["owner", str(img)]) == 0 and profile.photo_path("owner").is_file()
    assert mod.main(["nobody-here", str(img)]) == 1
    assert mod.main(["owner", str(tmp_path / "missing.jpg")]) == 1
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"nope")
    assert mod.main(["owner", str(bad)]) == 1


# ------------------------------------------------------------------ step 2: app logos and pinned apps
@pytest.fixture
def catalogue(monkeypatch, tmp_path):
    """A stand-in Start Menu: two apps with real files, one pointing nowhere. The extractor is a stand-in too."""
    from core import applogos
    exe = tmp_path / "Blender.lnk"
    exe.write_bytes(b"x")
    other = tmp_path / "Notepad Plus.lnk"
    other.write_bytes(b"x")
    cat = {"blender 5.2": exe, "notepad plus": other, "ghost app": tmp_path / "missing.lnk"}
    monkeypatch.setattr(applogos, "installed", lambda: dict(cat))
    calls = []

    def fake_extract(src, dest):
        calls.append(src.name)
        dest.write_bytes(_png(32, 32))
        return True
    monkeypatch.setattr(applogos, "EXTRACT", fake_extract)
    return calls


def test_icon_is_extracted_once_then_served_from_the_cache(catalogue):
    from core import applogos
    first = applogos.icon_path("blender")
    assert first is not None and first.suffix == ".png" and first.is_relative_to(applogos.cache_dir())
    assert applogos.icon_path("blender") == first and catalogue == ["Blender.lnk"]     # one extraction, however often asked
    assert applogos.icon_path("Notepad Plus") is not None                           # a pinned app: exact catalogue name


def test_icon_falls_back_when_the_app_or_the_extraction_is_missing(catalogue, monkeypatch):
    from core import applogos
    assert applogos.icon_path("vscode") is None and applogos.icon_path("not installed") is None
    assert applogos.icon_path("ghost app") is None                      # in the catalogue, but the file is gone
    monkeypatch.setattr(applogos, "EXTRACT", lambda src, dest: False)
    assert applogos.icon_path("notepad plus") is None                  # the program gives up no icon: letter mark
    monkeypatch.setattr(applogos, "EXTRACT", lambda src, dest: (_ for _ in ()).throw(OSError("no")))
    assert applogos.icon_path("steam") is None and applogos.icon_path("") is None


def test_a_failed_icon_is_retried_after_ten_minutes_and_unknown_names_leave_nothing(catalogue, monkeypatch):
    from core import applogos
    monkeypatch.setattr(applogos, "EXTRACT", lambda src, dest: False)
    assert applogos.icon_path("notepad plus", now=1000.0) is None
    monkeypatch.setattr(applogos, "EXTRACT", lambda src, dest: (dest.write_bytes(_png(32, 32)) or True))
    assert applogos.icon_path("notepad plus", now=1000.0 + 60) is None            # still in the quiet period
    assert applogos.RETRY_AFTER == 600
    assert applogos.icon_path("notepad plus", now=1000.0 + applogos.RETRY_AFTER + 5) is not None


def test_icon_route_serves_png_or_404_and_is_owner_only(server, catalogue):
    from core import users
    h = _pair(server)
    r = server.get("/api/app-icon", params={"name": "blender"}, headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert server.get("/api/app-icon", params={"name": "vscode"}, headers=h).status_code == 404
    token = h["Authorization"].split()[1]
    assert server.get("/api/app-icon", params={"name": "blender", "t": token}).status_code == 200      # <img> carries ?t=
    assert server.get("/api/app-icon", params={"name": "blender"}).status_code == 401
    wife = users.add("Marie", {"en": "madame"}, "wife")
    assert server.get("/api/app-icon", params={"name": "blender"}, headers=_pair(server, wife, "737373")).status_code == 403


def test_pinned_apps_persist_per_user_and_are_validated(server, catalogue):
    from core import applogos, users
    h = _pair(server)
    assert server.get("/api/launcher", headers=h).json()["pinned"] == []
    assert server.post("/api/launcher/pin", json={"name": "Notepad Plus"}, headers=h).json()["pinned"] == ["notepad plus"]
    assert server.post("/api/launcher/pin", json={"name": "notepad plus"}, headers=h).json()["pinned"] == ["notepad plus"]   # no duplicate
    assert server.post("/api/launcher/pin", json={"name": "format c:"}, headers=h).status_code == 400   # only installed apps
    got = server.get("/api/launcher", headers=h).json()
    assert got["pinned"] == ["notepad plus"] and "notepad plus" not in got["apps"] and "blender 5.2" in got["apps"]
    assert applogos.pinned("owner") == ["notepad plus"]                                      # read from disk, not memory
    wife = users.add("Marie", {"en": "madame"}, "wife")
    assert applogos.pinned(wife) == []
    assert server.post("/api/launcher/unpin", json={"name": "notepad plus"}, headers=h).json()["pinned"] == []
    assert server.post("/api/launcher/pin", json={"name": "x"}).status_code == 401


def test_pinned_row_has_a_limit(server, catalogue, monkeypatch):
    from core import applogos
    monkeypatch.setattr(applogos, "MAX_PINNED", 1)
    h = _pair(server)
    assert server.post("/api/launcher/pin", json={"name": "blender 5.2"}, headers=h).status_code == 200
    assert server.post("/api/launcher/pin", json={"name": "notepad plus"}, headers=h).status_code == 400


def test_page_wires_logos_and_the_plus_tile():
    page = (APP / "command-center.html").read_text(encoding="utf-8")
    js = (APP / "command-center.js").read_text(encoding="utf-8")
    for key in ("blender", "unreal", "vscode", "github", "steam"):
        assert f'data-app="{key}"' in page
    assert 'id="pick"' in page and "/api/launcher/pin" in js and "/api/app-icon" in js and 'typing(true, "Open ")' not in js.split("launchMore")[1][:200]
    assert page.count("<img") == 1                           # letter marks stay in the HTML; icons are swapped in by script


# ------------------------------------------------------------------ review fixes
def test_unknown_apps_touch_no_cache_and_extraction_runs_outside_the_global_lock(catalogue, monkeypatch):
    from core import applogos
    assert applogos.icon_path("no such program") is None and applogos.icon_path("vscode") is None
    assert not applogos.cache_dir().exists() or not list(applogos.cache_dir().glob("*.none"))
    seen = []

    def extract(src, dest):
        got = applogos._lock.acquire(blocking=False)
        seen.append(got)
        if got:
            applogos._lock.release()
        dest.write_bytes(b"png")
        return True
    monkeypatch.setattr(applogos, "EXTRACT", extract)
    assert applogos.icon_path("blender") is not None and seen == [True]    # the global lock was free during the extraction


def test_a_timeout_does_not_block_the_next_try_for_long(catalogue, monkeypatch):
    import subprocess
    from core import applogos
    monkeypatch.setattr(applogos, "EXTRACT", lambda src, dest: (_ for _ in ()).throw(subprocess.TimeoutExpired("powershell", 20)))
    assert applogos.icon_path("blender", now=5000.0) is None
    monkeypatch.setattr(applogos, "EXTRACT", lambda src, dest: (dest.write_bytes(_png(32, 32)) or True))
    assert applogos.icon_path("blender", now=5000.0 + 30) is None
    assert applogos.icon_path("blender", now=5000.0 + 601) is not None


def test_pinning_from_many_threads_never_loses_a_pin(catalogue, monkeypatch):
    import threading
    from core import applogos
    cat = {f"app{i}": Path(__file__) for i in range(6)}
    monkeypatch.setattr(applogos, "installed", lambda: dict(cat))
    threads = [threading.Thread(target=applogos.pin, args=("owner", name)) for name in cat]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(applogos.pinned("owner")) == sorted(cat)


def test_photo_refused_by_content_length_before_reading(server, monkeypatch):
    from core import profile
    h = _pair(server)
    big = _png(64, 64)
    monkeypatch.setattr(profile, "MAX_UPLOAD", 10)           # the whole request is bigger than the limit plus its slack
    monkeypatch.setattr(profile, "save_photo", lambda *a: (_ for _ in ()).throw(AssertionError("body was read")))
    r = server.post("/api/me/photo", files={"file": ("a.png", big + b"0" * 70000, "image/png")}, headers=h)
    assert r.status_code == 413


def test_a_malformed_picture_gives_the_friendly_400(server, monkeypatch):
    from PIL import ImageOps
    h = _pair(server)
    monkeypatch.setattr(ImageOps, "exif_transpose", lambda im: (_ for _ in ()).throw(ValueError("bad exif")))
    assert _upload(server, h, _png()).status_code == 400


def test_embedded_pages_check_who_sent_the_message_and_post_to_their_own_origin():
    fit = (APP / "fitness.html").read_text(encoding="utf-8")
    play = (APP / "play.html").read_text(encoding="utf-8")
    js = (APP / "command-center.js").read_text(encoding="utf-8")
    assert "e.source !== parent" in fit and 'wantTab' in fit and "/^[a-z]+$/.test(wantTab)" in fit
    assert "e.source !== parent || e.origin !== location.origin" in play and 'postMessage({ jarvisPlay: msg }, location.origin)' in play and '"*"' not in play.split("const up =")[1][:200]
    assert 'e.source === $("#frame").contentWindow' in js and "location.origin" in js
    icon = js.split("function appIcon")[1].split("function paintPins")[0]
    assert "Authorization" in icon and "createObjectURL" in icon and "&t=" not in icon     # the token never goes in an image address
