"""The Wardrobe page: the person's own photo, two closets on equal footing, the try-on queue (never while a game runs),
kept results, looks, the stylist, the week plan, catalogue cut-outs and tags. Every render is a fake: no GPU, no model."""
import base64
import io
from datetime import date

import pytest
from PIL import Image, ImageDraw

from core import pcbridge, users
from tests.test_app import client, pair  # noqa: F401  (shared app-server fixture)
from tools import dressing, fashion, fitting, garment3d, studio

PNG = io.BytesIO()
Image.new("RGBA", (8, 12), (10, 20, 30, 255)).save(PNG, format="PNG")
PNG = PNG.getvalue()
REAL_RENDER = fitting._render          # taken at import, before the suite's guard replaces it


def _jpeg(color=(200, 30, 40), size=(300, 400)) -> bytes:
    im = Image.new("RGB", size, (250, 250, 250))
    ImageDraw.Draw(im).rectangle([60, 70, 240, 340], fill=color)
    buf = io.BytesIO(); im.save(buf, format="JPEG")
    return buf.getvalue()


def _b64(color=(200, 30, 40)) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(_jpeg(color)).decode()


def _garment(iid, name, template, colors, photo=True, uid="owner"):
    with users.acting_as(uid):
        rows = fashion._load("wardrobe.json", [])
        rel = None
        if photo:
            (fashion.fdir() / "photos").mkdir(parents=True, exist_ok=True)
            (fashion.fdir() / "photos" / f"{iid}.jpg").write_bytes(_jpeg())
            rel = f"photos/{iid}.jpg"
        rows.append({"id": iid, "name": name, "category": garment3d.CATEGORY_OF[garment3d.SLOT_OF[template]], "colors": "",
                     "description": "", "photo": rel, "added": "2026-10-01"})
        fashion._save("wardrobe.json", rows)
        garment3d._write_meta(iid, {"template": template, "slot": garment3d.SLOT_OF[template], "colors": colors, "texture": None,
                                    "bbox": [0, 0, 1, 1], "status": "ready"})


def _person_photo(uid="owner"):
    with users.acting_as(uid):
        fashion.save_avatar_photo(_jpeg((90, 60, 40), (300, 600)))


@pytest.fixture
def wife():
    assert users.add("Maya", {"en": "ma'am"}) == "maya"
    return "maya"


@pytest.fixture
def closet():
    _garment("tee1", "black tee", "tee", ["#17171a"])
    _garment("jean", "dark jeans", "jeans", ["#2b3a55"])
    _garment("hi1", "black high-tops", "hightops", ["#17171a"])
    _garment("jkt1", "tan leather jacket", "jacket", ["#c39463"])
    _garment("cap1", "navy cap", "cap", ["#1f2a44"], photo=False)


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1_800_000_000.0}
    monkeypatch.setattr(fitting, "_now", lambda: t["now"])
    monkeypatch.setattr(fitting, "_today", lambda: date(2026, 11, 12))
    return t


@pytest.fixture
def renders(monkeypatch, clock):
    """A stand-in for the GPU: records each job and takes 40 pinned seconds."""
    seen = []

    def fake(job):
        seen.append(dict(job))
        clock["now"] += 40
        return PNG
    monkeypatch.setattr(fitting, "_render", fake)
    return seen


# -- the queue ---------------------------------------------------------------------------------------
def test_tryon_goes_queued_rendering_done_and_is_kept_per_outfit(closet, renders, clock):
    _person_photo()
    job = fitting.enqueue(["tee1", "jean", "hi1"])
    assert job["state"] == "queued" and job["rendered"] == ["tee1", "jean", "hi1"] and job["left_out"] == []
    assert fitting.jobs("owner")[0]["position"] == 1
    done = fitting.step()
    assert done["state"] == "done" and "40 seconds" in done["detail"]
    assert renders[0]["person"] == "owner" and renders[0]["state"] == "rendering"
    fit = fitting.fits()[0]
    assert fit["key"] == "hi1+jean+tee1" and fit["seconds"] == 40 and (fashion.fdir() / fit["image"]).read_bytes() == PNG
    assert fitting.jobs("owner")[0]["fit"] == fit["id"] and fitting.jobs("owner")[0]["elapsed"] == 40


def test_a_render_takes_three_pieces_clothes_first_and_says_what_it_left_out(closet, renders):
    _person_photo()
    job = fitting.enqueue(["cap1", "hi1", "jkt1", "jean", "tee1"])
    assert job["rendered"] == ["tee1", "jean", "jkt1"]          # top, bottom, layer before shoes
    assert set(job["left_out"]) == {"hi1", "cap1"}              # the cap has no photo at all


def test_no_photo_or_no_pieces_is_refused_with_plain_words(closet, renders):
    with pytest.raises(ValueError, match="full-body photo"):
        fitting.enqueue(["tee1"])
    _person_photo()
    with pytest.raises(ValueError, match="has a photo"):
        fitting.enqueue(["cap1"])
    with pytest.raises(ValueError, match="has a photo"):
        fitting.enqueue(["not-mine"])


def test_the_tryon_never_starts_while_a_game_runs_and_starts_when_it_closes(closet, renders, monkeypatch):
    _person_photo()
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(studio, "game_running", lambda: "Apex Legends")
    job = fitting.enqueue(["tee1", "jean"])
    assert job["state"] == "queued" and "Apex Legends is running" in job["detail"]
    for _ in range(3):
        assert fitting.step() is None
    assert renders == [] and fitting.jobs("owner")[0]["state"] == "queued"
    assert "Apex Legends" in fitting.gpu()["note"]
    monkeypatch.setattr(studio, "game_running", lambda: "")
    assert fitting.step()["state"] == "done" and len(renders) == 1


def test_the_local_pipeline_itself_refuses_during_a_game(monkeypatch):
    called = []
    monkeypatch.setattr(studio, "game_running", lambda: "Fortnite")
    monkeypatch.setattr(studio, "_run_child", lambda *a, **k: called.append(a))
    with pytest.raises(RuntimeError, match="busy.*Fortnite"):
        studio.render_wardrobe_tryon("owner", "owner", ["tee1"])
    assert called == []


def test_no_clean_product_shot_is_rendered_during_a_game(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(studio, "game_running", lambda: "Fortnite")
    monkeypatch.setattr(studio, "_run_child", lambda *a, **k: called.append(a))
    assert studio.clean_shot(str(tmp_path / "worn.jpg"), "a shirt") is None and called == []


def test_a_busy_graphics_card_waits_and_retries_then_gives_up_honestly(closet, clock, monkeypatch):
    _person_photo()
    calls = []

    def busy(job):
        calls.append(1)
        raise RuntimeError("The PC's GPU is busy (a game is running). Close it and try again.")
    monkeypatch.setattr(fitting, "_render", busy)
    fitting.enqueue(["tee1"])
    assert fitting.step() is None
    row = fitting.jobs("owner")[0]
    assert row["state"] == "queued" and "busy" in row["detail"]
    assert fitting.step() is None and len(calls) == 1           # not hammered: it waits before asking again
    monkeypatch.setattr(fitting, "MAX_TRIES", 2)
    clock["now"] += fitting.RETRY_S + 1
    failed = fitting.step()
    assert failed["state"] == "failed" and "stayed busy" in failed["detail"] and len(calls) == 2


def test_a_failed_render_says_why_and_the_next_job_still_runs(closet, renders, monkeypatch):
    _person_photo()
    real = fitting._render

    def broken(job):
        raise RuntimeError("avatar render subprocess failed: CUDA exploded")
    monkeypatch.setattr(fitting, "_render", broken)
    fitting.enqueue(["tee1"])
    fitting.enqueue(["jean"])
    first = fitting.step()
    assert first["state"] == "failed" and "CUDA exploded" in first["detail"]
    monkeypatch.setattr(fitting, "_render", real)
    assert fitting.step()["state"] == "done"


def test_same_outfit_is_not_queued_twice_and_a_waiting_job_can_be_cancelled(closet, renders):
    _person_photo()
    a = fitting.enqueue(["tee1", "jean"])
    assert fitting.enqueue(["jean", "tee1"])["id"] == a["id"]
    assert fitting.cancel(a["id"], "owner") and fitting.step() is None and renders == []
    assert not fitting.cancel(a["id"], "owner")


def test_on_the_laptop_the_pc_renders_the_persons_own_garments(closet, clock, wife, monkeypatch):
    _garment("dress", "red dress", "dress", ["#b3202a"], uid="maya")
    _person_photo("maya")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    sent = []

    def relay(path, method="GET", body=None):
        sent.append((path, body, users.current()))
        return PNG, "image/png"
    monkeypatch.setattr(pcbridge, "request", relay)
    monkeypatch.setattr(fitting, "_render", REAL_RENDER)        # the real relay code, with the link to the PC faked above
    with users.acting_as("maya"):
        job = fitting.enqueue(["dress"])
    done = fitting.step()
    assert done["id"] == job["id"] and done["state"] == "done"
    path, body, as_uid = sent[0]
    assert path == "/api/pc/avatar/tryon" and as_uid == "owner"
    assert body["person"] == "maya" and body["garment_owner"] == "maya" and body["garment_ids"] == ["dress"]
    with users.acting_as("maya"):
        assert len(fitting.fits()) == 1
    assert fitting.fits() == []                                  # nothing lands in the owner's closet


# -- looks, fits, plan -----------------------------------------------------------------------------------
def test_look_keeps_its_newest_tryon_and_fits_can_be_deleted(closet, renders, clock):
    _person_photo()
    lk = fitting.save_look("Friday", ["tee1", "jean", "hi1"], "casual")
    assert lk["items"]["top"] == {"src": "closet", "id": "tee1"} and lk["items"]["shoes"]["id"] == "hi1"
    assert fitting.looks()[0]["image"] is None
    fitting.enqueue(["hi1", "tee1", "jean"]); fitting.step()
    clock["now"] += 100
    fitting.enqueue(["hi1", "tee1", "jean"]); fitting.step()
    assert len(fitting.fits()) == 2
    newest = fitting.fits()[0]
    assert fitting.looks()[0]["image"] == newest["image"]
    assert fitting.delete_fit(newest["id"]) and len(fitting.fits()) == 1
    assert not (fashion.fdir() / newest["image"]).exists()
    with pytest.raises(ValueError):
        fitting.save_look("Empty", ["nope"])


def test_week_plan_assigns_and_clears_saved_looks(closet, clock):
    lk = fitting.save_look("Office", ["tee1", "jean"], "work")
    week = fitting.plan("2026-11-14", lk["id"])
    assert [d["date"] for d in week][0] == "2026-11-12" and len(week) == 7 and week[0]["today"]
    sat = next(d for d in week if d["date"] == "2026-11-14")
    assert sat["look_id"] == lk["id"] and sat["name"] == "Office"
    assert fashion._load("calendar.json", {})["2026-11-14"]["item_ids"] == ["jean", "tee1"]   # the voice calendar reads the same file
    assert next(d for d in fitting.plan("2026-11-14", "") if d["date"] == "2026-11-14")["look_id"] == ""
    with pytest.raises(ValueError):
        fitting.plan("2026-11-14", "gone")
    with pytest.raises(ValueError):
        fitting.plan("someday", lk["id"])


# -- the stylist -------------------------------------------------------------------------------------------
def test_stylist_picks_from_the_real_closet_and_the_page_shows_it(closet):
    out = fitting.stylist("casual", "")
    top = out["picks"][0]
    assert {"tee1", "jean", "hi1"} & set(top["items"]) and top["why"]
    shown = fitting.picked()
    assert shown and shown["items"] == sorted(top["items"]) and dressing.current()["by"] == "stylist"


def test_cold_weather_adds_the_jacket_and_hot_weather_leaves_it(closet):
    assert "jkt1" in fitting.stylist("casual", "cold")["picks"][0]["items"]
    assert "jkt1" not in fitting.stylist("casual", "hot")["picks"][0]["items"]
    assert fitting.weather_words("a rainy day") == " with a jacket" and fitting.weather_words("dinner") == ""


def test_empty_closet_gets_a_helpful_message_not_a_crash():
    out = fitting.stylist("date night")
    assert out["picks"] == [] and "empty" in out["message"]
    assert fitting.picked() is None


def test_voice_styling_ends_with_the_outfit_shown(closet, monkeypatch):
    from core import hud
    shown = []
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(hud, "wardrobe", lambda: shown.append(1) or True)
    reply = dressing.style_outfit("make me a casual outfit")
    assert "Wardrobe page" in reply and "on your avatar" in reply and shown == [1]
    assert fitting.picked()["items"]


# -- digitising a garment -------------------------------------------------------------------------------
def test_season_is_tagged_from_the_kind_of_garment():
    assert fitting.season_of("coat") == "cold" and fitting.season_of("tank") == "warm" and fitting.season_of("jeans") == "all"
    assert fitting.season_of("shirt", "linen camp shirt") == "warm"


def test_catalogue_makes_a_trimmed_cutout_and_fills_the_season_once(monkeypatch):
    _garment("coat1", "grey wool coat", "coat", ["#777777"])

    def fake_cut(img):
        out = img.convert("RGBA")
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rectangle([60, 70, 240, 340], fill=255)
        out.putalpha(mask)
        return out
    monkeypatch.setattr(fitting, "cutout_ready", lambda: True)
    monkeypatch.setattr(fitting, "_remove_background", fake_cut)
    assert fitting.catalogue("coat1", "coat") == {"cutout": "cutouts/coat1.png"}
    cut = Image.open(fashion.fdir() / "cutouts" / "coat1.png")
    assert cut.mode == "RGBA" and cut.width < 300 and cut.getpixel((0, 0))[3] == 0
    g = fitting.garments()[0]
    assert g["image"] == "cutouts/coat1.png" and g["cutout"] and g["season"] == "cold" and g["photo"] == "photos/coat1.jpg"
    dressing.closet_op("update", {"id": "coat1", "season": "all"})
    fitting.catalogue("coat1", "coat")
    assert fitting.garments()[0]["season"] == "all"              # a person's own tag is never overwritten


def test_without_the_background_model_the_photo_is_kept(monkeypatch):
    _garment("tee9", "white tee", "tee", ["#f2f1ec"])
    assert fitting.catalogue("tee9", "tee") == {"cutout": None}
    assert fitting.garments()[0]["image"] == "photos/tee9.jpg" and not fitting.garments()[0]["cutout"]
    monkeypatch.setattr(fitting, "cutout_ready", lambda: True)
    monkeypatch.setattr(fitting, "_remove_background", lambda img: img.convert("RGBA"))    # nothing removed: not a cut-out
    assert fitting.make_cutout("tee9") is None


# -- the wife's closet: empty and with a damaged file ------------------------------------------------------
def test_an_empty_closet_with_a_malformed_file_still_opens(wife):
    with users.acting_as("maya"):
        fashion.fdir().mkdir(parents=True, exist_ok=True)
        (fashion.fdir() / "garments.json").write_text('"oops"', encoding="utf-8")
        (fashion.fdir() / "wardrobe.json").write_text("{not json", encoding="utf-8")
        (fashion.fdir() / "calendar.json").write_text("[1, 2]", encoding="utf-8")
        s = fitting.state()
        assert s["closet"] == [] and s["looks"] == [] and s["photo"] is None and len(s["week"]) == 7
        assert s["summary"] == "0 pieces, 0 saved looks"
        assert dressing.data()["discover"] == []
        assert (fashion.fdir() / "garments.json").read_text(encoding="utf-8") == '"oops"'   # reading never rewrites it


# -- the routes ---------------------------------------------------------------------------------------------
def _upload(client, h, person=""):
    return client.post("/api/closet/photo" + (f"?person={person}" if person else ""), headers=h,
                       files={"file": ("me.jpg", io.BytesIO(_jpeg((90, 60, 40), (300, 600))), "image/jpeg")})


def test_page_state_photo_upload_and_two_people(client, wife, closet):
    h = pair(client)
    s = client.get("/api/closet", headers=h).json()
    assert s["person"] == "owner" and s["photo"] is None and len(s["closet"]) == 5
    assert [p["id"] for p in s["people"]] == ["owner", "maya"] and s["people"][0]["me"]
    hers = client.get("/api/closet?person=maya", headers=h).json()
    assert hers["person"] == "maya" and hers["closet"] == [] and hers["photo"] is None
    r = _upload(client, h, "maya")
    assert r.status_code == 200 and r.json()["photo"].startswith("avatar/photo-")
    assert client.get("/api/closet", headers=h).json()["photo"] is None            # her photo is hers alone
    token = h["Authorization"].split()[1]
    img = client.get(f"/api/closet/file/maya/{r.json()['photo']}?t={token}")
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/")
    bad = client.post("/api/closet/photo", headers=h, files={"file": ("x.jpg", io.BytesIO(b"not a picture"), "image/jpeg")})
    assert bad.status_code == 400 and "photo" in bad.json()["detail"]


def test_a_member_sees_only_her_own_closet(client, wife, closet):
    h = pair(client, "maya")
    s = client.get("/api/closet", headers=h).json()
    assert s["person"] == "maya" and [p["id"] for p in s["people"]] == ["maya"] and s["closet"] == []
    assert client.get("/api/closet?person=owner", headers=h).status_code == 403
    assert client.get("/api/closet/file/owner/photos/tee1.jpg", headers=h).status_code == 403
    assert client.post("/api/closet/tryon", headers=h, json={"person": "owner", "items": ["tee1"]}).status_code == 403
    assert _upload(client, h).status_code == 200


def test_file_route_serves_images_only_and_stays_inside_the_folder(client, closet):
    h = pair(client)
    assert client.get("/api/closet/file/owner/photos/tee1.jpg", headers=h).status_code == 200
    assert client.get("/api/closet/file/owner/wardrobe.json", headers=h).status_code == 404
    assert client.get("/api/closet/file/owner/../profile.json", headers=h).status_code == 404
    assert client.get("/api/closet/file/owner/%2e%2e/%2e%2e/%2e%2e/users.json", headers=h).status_code == 404


def test_add_a_garment_for_the_selected_person_and_edit_its_tags(client, wife, monkeypatch):
    started = []
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(garment3d, "process_async", lambda iid, owner, **k: started.append((iid, owner)))
    h = pair(client)
    r = client.post("/api/closet/garment", headers=h, json={"person": "maya", "op": "add", "args": {"photo": _b64(), "name": "red dress"}})
    assert r.status_code == 200 and started == [(r.json()["id"], "maya")]
    state = r.json()["state"]
    assert state["person"] == "maya" and state["closet"][0]["name"] == "red dress" and state["closet"][0]["photo"]
    assert client.get("/api/closet", headers=h).json()["closet"] == []              # not in the owner's closet
    gid = r.json()["id"]
    r = client.post("/api/closet/garment", headers=h, json={"person": "maya", "op": "update",
                                                            "args": {"id": gid, "category": "full-body", "colors": "red", "season": "warm"}})
    g = r.json()["state"]["closet"][0]
    assert g["season"] == "warm" and g["category"] == "full-body" and g["colors_text"] == "red"
    assert client.post("/api/closet/garment", headers=h, json={"op": "add", "args": {}}).status_code == 400
    assert client.post("/api/closet/garment", headers=h, json={"op": "update", "args": {"id": "nope"}}).status_code == 400
    r = client.post("/api/closet/garment", headers=h, json={"person": "maya", "op": "delete", "args": {"id": gid}})
    assert r.json()["state"]["closet"] == []


def test_adding_on_the_laptop_is_kept_until_the_pc_is_back(client, wife, monkeypatch):
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)

    def down(*a, **k):
        raise pcbridge.PCError("The PC is unreachable.", 503)
    monkeypatch.setattr(pcbridge, "json_request", down)
    h = pair(client)
    r = client.post("/api/closet/garment", headers=h, json={"person": "maya", "op": "add", "args": {"photo": _b64()}})
    assert r.status_code == 200 and r.json()["queued"]
    g = r.json()["state"]["closet"][0]
    assert g["pending"] and g["photo"] and g["job"]["state"] == "waiting"
    r = client.post("/api/closet/garment", headers=h, json={"person": "maya", "op": "delete", "args": {"id": g["id"]}})
    assert r.status_code == 503 and "PC" in r.json()["detail"]


def test_tryon_route_queues_polls_and_reports_a_game(client, closet, renders, monkeypatch):
    h = pair(client)
    r = client.post("/api/closet/tryon", headers=h, json={"items": ["tee1", "jean"]})
    assert r.status_code == 400 and "full-body photo" in r.json()["detail"]
    _upload(client, h)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(studio, "game_running", lambda: "Apex Legends")
    r = client.post("/api/closet/tryon", headers=h, json={"items": ["tee1", "jean"]})
    assert r.status_code == 200 and r.json()["job"]["state"] == "queued" and r.json()["gpu"]["game"] == "Apex Legends"
    assert fitting.step() is None
    polled = client.get("/api/closet/jobs", headers=h).json()
    assert polled["jobs"][0]["state"] == "queued" and "Apex Legends" in polled["jobs"][0]["detail"] and polled["fits"] == []
    monkeypatch.setattr(studio, "game_running", lambda: "")
    fitting.step()
    polled = client.get("/api/closet/jobs", headers=h).json()
    assert polled["jobs"][0]["state"] == "done" and polled["fits"][0]["id"] == polled["jobs"][0]["fit"] and polled["gpu"]["game"] == ""
    assert client.post("/api/closet/tryon", headers=h, json={"items": []}).status_code == 400
    r = client.post("/api/closet/fit/delete", headers=h, json={"id": polled["fits"][0]["id"]})
    assert r.json()["fits"] == []


def test_cancel_route(client, closet, renders):
    h = pair(client)
    _upload(client, h)
    job = client.post("/api/closet/tryon", headers=h, json={"items": ["tee1"]}).json()["job"]
    r = client.post("/api/closet/jobs/cancel", headers=h, json={"id": job["id"]})
    assert r.json()["ok"] and r.json()["jobs"][0]["state"] == "failed" and r.json()["jobs"][0]["detail"] == "Cancelled."


def test_look_style_and_plan_routes(client, closet, clock):
    h = pair(client)
    s = client.post("/api/closet/look", headers=h, json={"op": "save", "name": "Date night", "items": ["tee1", "jean", "hi1"]}).json()
    look = s["looks"][0]
    assert look["name"] == "Date night" and look["items"] == ["hi1", "jean", "tee1"] and s["summary"] == "5 pieces, 1 saved look"
    s = client.post("/api/closet/look", headers=h, json={"op": "update", "id": look["id"], "favorite": True, "name": "Dinner"}).json()
    assert s["looks"][0]["favorite"] and s["looks"][0]["name"] == "Dinner"
    week = client.post("/api/closet/plan", headers=h, json={"date": "2026-11-13", "look_id": look["id"]}).json()["week"]
    assert week[1]["name"] == "Dinner"
    assert client.post("/api/closet/plan", headers=h, json={"date": "2026-11-13", "look_id": "nope"}).status_code == 400
    out = client.post("/api/closet/style", headers=h, json={"occasion": "casual", "weather": "cold"}).json()
    assert "jkt1" in out["picks"][0]["items"]
    assert client.get("/api/closet", headers=h).json()["picked"]["items"]
    assert client.post("/api/closet/look", headers=h, json={"op": "save", "name": "x", "items": []}).status_code == 400
    assert client.post("/api/closet/look", headers=h, json={"op": "update", "id": "gone", "name": "y"}).status_code == 400
    s = client.post("/api/closet/look", headers=h, json={"op": "delete", "id": look["id"]}).json()
    assert s["looks"] == [] and s["week"][1]["look_id"] == ""


def test_pc_route_dresses_a_person_in_their_own_closet_and_refuses_during_a_game(client, wife, monkeypatch):
    seen = []
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)

    def fake(person, owner, ids, notes=""):
        seen.append((person, owner, ids))
        raise RuntimeError("The PC's GPU is busy (Apex Legends is running). The try-on starts when the game is closed.")
    monkeypatch.setattr(studio, "render_wardrobe_tryon", fake)
    h = pair(client)
    r = client.post("/api/pc/avatar/tryon", headers=h, json={"person": "maya", "garment_owner": "maya", "garment_ids": ["dress"]})
    assert r.status_code == 503 and "busy" in r.json()["detail"] and seen == [("maya", "maya", ["dress"])]
    r = client.post("/api/pc/avatar/tryon", headers=h, json={"person": "maya", "garment_owner": "stranger", "garment_ids": ["dress"]})
    assert r.status_code == 400


# -- review fixes: weather words are whole words; a damaged file is kept before it is overwritten -----------
@pytest.mark.parametrize("said", ["gym training", "window seat dinner", "a photoshoot", "something warm for the office",
                                  "a hot pink look", "keep me warm", "rewind to casual", "brainstorm an outfit"])
def test_weather_words_ignores_words_that_only_look_like_weather(said):
    assert fitting.weather_words(said) == ""


@pytest.mark.parametrize("said,want", [
    ("cold", " with a jacket"), ("rain", " with a jacket"), ("warm", " without a jacket"), ("hot", " without a jacket"),
    ("a rainy day", " with a jacket"), ("dinner, it's windy", " with a jacket"), ("work and it is freezing", " with a jacket"),
    ("it's really warm out", " without a jacket"), ("a hot day at the park", " without a jacket"),
    ("casual for warm weather", " without a jacket"), ("summer barbecue", " without a jacket")])
def test_weather_words_still_hears_real_weather(said, want):
    assert fitting.weather_words(said) == want


def test_a_damaged_closet_file_is_kept_aside_before_a_save_replaces_it(wife):
    with users.acting_as("maya"):
        fashion.fdir().mkdir(parents=True, exist_ok=True)
        path = fashion.fdir() / "wardrobe.json"
        damaged = '[{"id": "a1", "name": "Blue dress"}, {"id": "b2", "na'          # cut off mid-write
        path.write_text(damaged, encoding="utf-8")
        rows = fashion._load("wardrobe.json", [])
        assert rows == []
        fashion._save("wardrobe.json", rows + [{"id": "c3", "name": "New"}])
        kept = [p for p in fashion.fdir().iterdir() if p.name.startswith("wardrobe.json.damaged-")]
        assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == damaged
        assert fashion._load("wardrobe.json", []) == [{"id": "c3", "name": "New"}]
        fashion._save("wardrobe.json", [{"id": "c3", "name": "New"}, {"id": "d4", "name": "More"}])   # a healthy file: no new copy
        assert len([p for p in fashion.fdir().iterdir() if ".damaged-" in p.name]) == 1


def test_a_file_of_the_wrong_shape_is_kept_aside_too(wife):
    with users.acting_as("maya"):
        fashion.fdir().mkdir(parents=True, exist_ok=True)
        (fashion.fdir() / "calendar.json").write_text("[1, 2]", encoding="utf-8")
        fashion._save("calendar.json", {"2026-11-13": "look1"})
        kept = [p for p in fashion.fdir().iterdir() if p.name.startswith("calendar.json.damaged-")]
        assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "[1, 2]"
