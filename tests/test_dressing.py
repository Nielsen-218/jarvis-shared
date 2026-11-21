"""The 3D dressing room: closet edits, the stylist, saved looks, the look on the avatar, photo cut-outs and the APIs."""
import base64
import io
import time

import pytest
from PIL import Image, ImageDraw

from core import pcbridge, users
from tests.test_app import client, pair  # noqa: F401  (shared app-server fixture)
from tools import dressing, fashion, garment3d


def _photo(color=(200, 30, 40), size=(400, 500)) -> str:
    im = Image.new("RGB", size, (250, 250, 250))
    ImageDraw.Draw(im).rectangle([80, 90, 320, 430], fill=color)
    buf = io.BytesIO(); im.save(buf, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _item(iid, name, template, colors, status="ready"):
    """A closet item that is already 3D (meta written as the PC pipeline would)."""
    rows = fashion._load("wardrobe.json", [])
    rows.append({"id": iid, "name": name, "category": garment3d.CATEGORY_OF[garment3d.SLOT_OF[template]], "colors": "",
                 "description": "", "photo": None, "added": "2026-10-01"})
    fashion._save("wardrobe.json", rows)
    garment3d._write_meta(iid, {"template": template, "slot": garment3d.SLOT_OF[template], "colors": colors, "texture": None,
                                "bbox": [0, 0, 1, 1], "status": status})


@pytest.fixture
def closet():
    _item("tee1", "black tee with tan trim", "tee", ["#17171a", "#c39463"])
    _item("hood", "cream hoodie", "hoodie", ["#ece3cf", "#f2f1ec"])
    _item("jog1", "beige joggers", "joggers", ["#d6c3a0"])
    _item("jean", "dark jeans", "jeans", ["#2b3a55"])
    _item("hi1", "black and tan high-tops", "hightops", ["#17171a", "#c39463"])
    _item("jkt1", "tan leather jacket", "jacket", ["#c39463", "#9a5a2a"])


# -- the stylist -------------------------------------------------------------------------------------
def test_parse_keeps_adjustments_apart_from_occasions():
    assert dressing.parse_request("make it more formal")["occasion"] is None
    assert dressing.parse_request("make it more formal")["more_formal"]
    assert dressing.parse_request("an outfit for church")["occasion"] == "church"
    r = dressing.parse_request("something classy with my white sneakers")
    assert r["occasion"] == "formal" and r["want"]["shoes"] == {"color": "white", "word": "sneakers"}
    assert dressing.parse_request("change the shoes")["change"] == "shoes"
    assert dressing.parse_request("give me five combinations")["n"] == 5


def test_casual_outfit_comes_from_the_real_closet(closet):
    look = dressing.suggest("make me a casual outfit")[0]
    assert {"top", "bottom", "shoes"} <= set(look["items"])
    assert all(r["src"] == "closet" for r in look["items"].values())


def test_missing_piece_uses_a_labelled_stand_in(closet):
    look = dressing.suggest("something classy with my white sneakers")[0]
    assert look["items"]["shoes"] == {"src": "basic", "id": "b-sneakers"}
    assert "don't have white sneakers" in look["why"]


def test_what_goes_with_jeans_keeps_the_jeans(closet):
    look = dressing.suggest("what can I wear with these jeans")[0]
    assert look["items"]["bottom"]["id"] == "jean"


def test_five_combinations_are_different(closet):
    looks = dressing.suggest("give me five combinations")
    pairs = {(l["items"]["top"]["id"], l["items"].get("bottom", {}).get("id")) for l in looks}
    assert len(looks) == 5 and len(pairs) == 5


def test_adjustments_change_only_what_was_asked(closet):
    first = dressing.suggest("casual")[0]
    base = {"items": first["items"], "_target": first["_target"]}
    shoes = dressing.suggest("change the shoes", base=base)[0]
    assert shoes["items"]["shoes"] != first["items"]["shoes"]
    assert shoes["items"]["top"] == first["items"]["top"] and shoes["items"].get("bottom") == first["items"].get("bottom")
    jacket = dressing.suggest("try a black jacket", base=base)[0]
    assert jacket["items"]["outer"]["id"] == "b-jacket" and jacket["items"]["top"] == first["items"]["top"]
    better = dressing.suggest("show me something better", base=base, avoid=[first["items"]])[0]
    assert better["items"] != first["items"]


def test_more_formal_raises_formality(closet):
    first = dressing.suggest("casual")[0]
    formal = dressing.suggest("make it more formal", base={"items": first["items"], "_target": first["_target"]})[0]
    f = lambda look: sum(dressing.FORMALITY[dressing.find(r)["model"]["template"]] for r in look["items"].values())
    assert f(formal) > f(first)


def test_style_tool_dresses_the_avatar(closet):
    reply = dressing.style_outfit("an outfit for church")
    cur = dressing.current()
    assert "on your avatar" in reply and cur["by"] == "stylist" and cur["items"]["shoes"]
    assert cur["_target"][0] >= 3


# -- closet edits ------------------------------------------------------------------------------------
def test_add_photo_saves_and_starts_the_3d_job(monkeypatch):
    started = []
    monkeypatch.setattr(garment3d, "process_async", lambda iid, owner, force=False, correction=None: started.append((iid, correction)))
    r = dressing.closet_op("add", {"photo": _photo(), "template": "tee"})
    row = fashion._load("wardrobe.json", [])[0]
    assert row["id"] == r["id"] and row["photo"] and (fashion.fdir() / row["photo"]).exists() and row["auto_name"]
    assert started == [(r["id"], {"template": "tee"})]


def test_correcting_the_type_moves_the_slot_and_rescans(monkeypatch, closet):
    started = []
    monkeypatch.setattr(garment3d, "process_async", lambda iid, owner, force=False, correction=None: started.append(iid))
    rows = fashion._load("wardrobe.json", []); rows[0]["photo"] = "photos/tee1.jpg"; fashion._save("wardrobe.json", rows)
    dressing.closet_op("update", {"id": "tee1", "template": "sneakers", "name": "white sneakers", "favorite": True})
    m = garment3d.meta("tee1")
    assert m["template"] == "sneakers" and m["slot"] == "shoes" and started == ["tee1"]
    item = next(i for i in dressing.closet() if i["id"] == "tee1")
    assert item["name"] == "white sneakers" and item["favorite"]


def test_delete_removes_item_and_its_3d_model(closet):
    dressing.closet_op("delete", {"id": "jkt1"})
    assert "jkt1" not in {i["id"] for i in dressing.closet()} and garment3d.meta("jkt1") is None


def test_server_queues_adds_until_the_pc_adopts_them(monkeypatch):
    q = dressing.queue_add({"photo": _photo(), "name": "red tee"})
    item = next(i for i in dressing.closet() if i["id"] == q["id"])
    assert item["job"]["state"] == "waiting" and item["name"] == "red tee"
    monkeypatch.setattr(garment3d, "process_async", lambda *a, **k: None)
    assert dressing.import_pending() == 1 and dressing.import_pending() == 0
    assert q["id"] in {i["id"] for i in fashion._load("wardrobe.json", [])}


# -- looks, the current look and the avatar (per machine, merged) -----------------------------------------
def test_looks_save_update_delete_and_merge_across_machines(monkeypatch, closet):
    items = {"top": {"src": "closet", "id": "tee1"}}
    a = dressing.save_look("Sunday", items, "Church")
    dressing.update_look(a["id"], favorite=True)
    monkeypatch.setattr(dressing, "_host", lambda: "server")
    b = dressing.save_look("Gym", items, "Gym")
    looks = dressing.looks()
    assert {l["name"] for l in looks} == {"Sunday", "Gym"} and next(l for l in looks if l["id"] == a["id"])["favorite"]
    dressing.delete_look(a["id"])   # deleted on the server, written on the PC: still gone
    assert [l["id"] for l in dressing.looks()] == [b["id"]]


def test_current_look_is_the_newest_from_either_machine(monkeypatch):
    dressing.set_current({"top": {"src": "closet", "id": "x"}}, "PC look")
    time.sleep(.05)
    monkeypatch.setattr(dressing, "_host", lambda: "server")
    dressing.set_current({"top": {"src": "closet", "id": "y"}}, "Phone look")
    assert dressing.current()["name"] == "Phone look"
    assert dressing.set_current({"nope": {"src": "closet", "id": "z"}})["items"] == {}


def test_avatar_settings_are_clamped_and_remembered():
    cfg = dressing.set_avatar_cfg({"sex": "f", "shape": {"height": 3, "jaw": -1, "evil": 1}, "hack": 1})
    assert cfg["sex"] == "f" and cfg["shape"]["height"] == 1 and cfg["shape"]["jaw"] == 0 and "evil" not in cfg["shape"] and "hack" not in cfg
    assert dressing.avatar_cfg()["sex"] == "f"


# -- photo cut-out ------------------------------------------------------------------------------------
def test_prepare_texture_finds_the_garment_and_its_colour(tmp_path):
    p = tmp_path / "shirt.jpg"
    Image.open(io.BytesIO(base64.b64decode(_photo().split(",")[1]))).save(p)
    tex, bbox, palette, mask = garment3d.prepare_texture(p, None, white_bg=True)
    assert abs(bbox[0] - .2) < .05 and abs(bbox[2] - .8) < .05 and abs(bbox[1] - .18) < .05
    r, g, b = (int(palette[0][i:i + 2], 16) for i in (1, 3, 5))
    assert r > 150 and g < 90 and b < 90
    corner = tex.convert("RGB").getpixel((3, 3))
    assert corner[0] > 120 and corner[1] < 120   # background filled with garment colour, not white


# -- APIs ---------------------------------------------------------------------------------------------
def test_dress_api_style_and_current(client, closet):
    h = pair(client)
    d = client.get("/api/dress", headers=h).json()
    assert {i["id"] for i in d["closet"]} >= {"tee1", "jkt1"} and d["avatar"]["sex"] == "m" and d["basics"]
    r = client.post("/api/dress/style", headers=h, json={"request": "make me a casual outfit"}).json()
    assert r["looks"] and r["current"]["items"] == r["looks"][0]["items"]
    assert client.get("/api/dress", headers=h).json()["current"]["by"] == "stylist"
    saved = client.post("/api/dress/looks", headers=h, json={"op": "save", "name": "Mine", "items": r["looks"][0]["items"]}).json()
    assert saved["looks"][0]["name"] == "Mine"
    assert client.post("/api/dress/closet", headers=h, json={"op": "explode"}).status_code == 400


def test_dress_closet_api_queues_on_the_server_when_the_pc_is_off(client, monkeypatch):
    h = pair(client)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    def down(*a, **k):
        raise pcbridge.PCError("The PC is unreachable.", 503)
    monkeypatch.setattr(pcbridge, "json_request", down)
    r = client.post("/api/dress/closet", headers=h, json={"op": "add", "args": {"photo": _photo()}}).json()
    assert r["queued"]
    assert client.post("/api/dress/closet", headers=h, json={"op": "delete", "args": {"id": r["id"]}}).status_code == 503
