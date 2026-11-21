"""Kitchen v2 engine (core/nutri.py) and chef (core/chef.py) on a small fixture library."""
import io
import json
from datetime import date

import pytest

from core import chef, nutri, storage


def _dish(i, name, cat="main", meals=("lunch", "dinner"), keys=("chicken thighs", "long-grain rice"), tags=("high-protein",),
          cuisine="Haitian", kcal=600, p=45, c=60, f=18):
    return {"id": i, "name": name, "english": name, "cuisine": cuisine, "category": cat, "meals": list(meals), "servings": 5,
            "ingredients": [{"item": k, "qty": "1 lb", "grams": 450, "key": True, "aisle": "meat"} for k in keys]
            + [{"item": "epis", "qty": "2 tbsp", "grams": 30, "key": False, "aisle": "pantry"}],
            "steps": ["Cook it."], "macros": {"kcal": kcal, "protein_g": p, "carbs_g": c, "fat_g": f},
            "tags": list(tags), "toddler": "No pepper.", "photo_prompt": "x"}


LIB = [
    _dish("poul-nan-sos", "Poul nan Sòs", tags=("high-protein", "batch", "training-day")),
    _dish("griot", "Griot", keys=("pork shoulder", "sour orange"), tags=("high-protein",)),
    _dish("legim", "Legim", keys=("eggplant", "cabbage", "beef"), tags=("one-pot", "rest-day")),
    _dish("pwason", "Pwason Gwo Sèl", keys=("snapper", "lime"), tags=("high-protein", "rest-day")),
    _dish("mayi", "Mayi Moulen ak Sòs Pwa", keys=("cornmeal", "red kidney beans"), tags=("budget",)),
    _dish("jerk", "Jerk Chicken", keys=("chicken thighs", "scotch bonnet"), cuisine="Jamaican"),
    _dish("akasan", "Akasan", cat="breakfast", meals=("breakfast",), keys=("cornmeal", "evaporated milk"), tags=("budget",)),
    _dish("ze", "Bannann Bouyi ak Ze", cat="breakfast", meals=("breakfast",), keys=("green plantains", "eggs")),
    _dish("oats", "Coconut Overnight Oats", cat="breakfast", meals=("breakfast",), keys=("oats", "coconut milk")),
]


@pytest.fixture
def lib(tmp_path, monkeypatch):
    d = tmp_path / "dishes"
    d.mkdir()
    (d / "test.json").write_text(json.dumps(LIB), encoding="utf-8")
    monkeypatch.setattr(nutri, "DISH_DIR", d)
    nutri.forget_cache()
    monkeypatch.setattr(nutri, "day_type", lambda day=None: ("heavy", "Lower body"))
    yield nutri.library()
    nutri.forget_cache()


def test_library_loads_and_skips_bad_files(lib, tmp_path):
    assert len(lib) == len(LIB)
    (nutri.DISH_DIR / "broken.json").write_text("{not json", encoding="utf-8")
    nutri.forget_cache()
    assert len(nutri.library()) == len(LIB)


def test_like_dislike_and_clear(lib):
    nutri.vote("griot", "like")
    nutri.vote("legim", "dislike")
    assert nutri.taste_of("griot") == "like" and nutri.taste_of("legim") == "dislike"
    nutri.vote("griot", "dislike")
    assert nutri.taste_of("griot") == "dislike" and "griot" not in nutri.taste()["likes"]
    nutri.vote("griot", "clear")
    assert nutri.taste_of("griot") is None
    with pytest.raises(KeyError):
        nutri.vote("nope", "like")
    with pytest.raises(ValueError):
        nutri.vote("griot", "love")


def test_disliked_dishes_never_planned(lib):
    nutri.vote("poul-nan-sos", "dislike")
    week = nutri.build_week(start=date(2026, 11, 23), pantry=[])
    assert len(week) == 7
    assert all("poul-nan-sos" not in d["meals"].values() for d in week)
    assert all(set(d["meals"]) == {"breakfast", "lunch", "dinner"} for d in week)


def test_batch_dinner_becomes_next_lunch(lib):
    week = nutri.build_week(start=date(2026, 11, 23), pantry=[])
    for a, b in zip(week, week[1:]):
        dinner = nutri.dish(a["meals"]["dinner"])
        if {"batch", "one-pot"} & set(dinner["tags"]):
            assert b["meals"]["lunch"] == a["meals"]["dinner"]


def test_targets_need_a_weight_then_follow_the_day(lib, monkeypatch):
    assert nutri.targets() is None
    nutri.log_weight(190)
    t = nutri.targets()
    assert t["protein_g"] == 190 and t["kcal"] > 2900
    monkeypatch.setattr(nutri, "day_type", lambda day=None: ("rest", "Rest"))
    assert nutri.targets()["kcal"] < t["kcal"] and nutri.targets()["protein_g"] == 190
    with pytest.raises(ValueError):
        nutri.log_weight(20)


def test_pantry_match_and_cook_now(lib):
    pantry = [{"name": "Chicken thighs 5 lb"}, {"name": "rice"}, {"name": "Scotch bonnet peppers"}]
    share, have, missing = nutri.pantry_match(nutri.dish("poul-nan-sos"), pantry)
    assert share == 1.0 and not missing
    now = [c["dish"] for c in nutri.cook_now(pantry)]
    assert now[0] == "poul-nan-sos" and "jerk" in now and "griot" not in now


def test_eat_logs_full_macros(lib):
    nutri.eat("griot", "dinner")
    eaten = nutri.eaten_today()
    assert eaten["kcal"] == 600 and eaten["protein_g"] == 45 and eaten["carbs_g"] == 60 and eaten["fat_g"] == 18
    row = storage.load("nutrition.json", {})["log"][-1]
    assert row["food"] == "Griot" and row["dish"] == "griot"


def test_shopping_lists_only_what_is_missing(lib):
    week = [{"date": "2026-11-23", "day_type": "heavy", "meals": {"breakfast": "akasan", "lunch": "griot", "dinner": "jerk"}}]
    items = [r["item"] for r in nutri.shopping(week, [{"name": "cornmeal"}, {"name": "chicken thighs"}])]
    assert "pork shoulder" in items and "scotch bonnet" in items and "evaporated milk" in items
    assert "cornmeal" not in items and "chicken thighs" not in items and "epis" not in items


def test_page_payload_shape(lib):
    nutri.log_weight(185)
    p = nutri.page()
    assert set(p) >= {"me", "household", "today", "week", "dishes", "cook_now", "pantry", "shopping", "snaps"}
    assert p["me"]["set_up"] and p["household"]["size"] == 5
    for d in p["week"]:
        for m in d["meals"].values():
            assert m in p["dishes"] and "photo" in p["dishes"][m] and "taste" in p["dishes"][m]


def _jpeg():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(buf, "JPEG")
    return buf.getvalue()


def test_snap_groceries_suggests_meals(lib, monkeypatch):
    monkeypatch.setattr(chef, "see", lambda url, q="": json.dumps(
        {"kind": "groceries", "items": [{"name": "chicken thighs", "qty": "5 lb"}, {"name": "long-grain rice"},
                                        {"name": "scotch bonnet"}]}))
    monkeypatch.setattr(chef, "make_scraps", lambda on_hand: None)
    r = chef.snap(_jpeg(), "groceries")
    assert [i["name"] for i in r["items"]] == ["chicken thighs", "long-grain rice", "scotch bonnet"]
    assert r["meals"][0]["id"] == "poul-nan-sos" and r["meals"][0]["dish"]["name"] == "Poul nan Sòs" and r["thumb"].endswith(".jpg")
    assert nutri.recent_snaps()[0]["id"] == r["id"]
    from tools import pantry
    assert pantry.list_items() == []          # nothing is stocked until he confirms


def test_snap_with_eyes_offline_keeps_the_photo(lib, monkeypatch):
    def offline(url, q=""):
        raise RuntimeError("vision unavailable")
    monkeypatch.setattr(chef, "see", offline)
    r = chef.snap(_jpeg(), "groceries")
    assert r["items"] == [] and "offline" in r["note"] and nutri.recent_snaps()[0]["id"] == r["id"]


def test_scraps_recipe_is_saved_and_likeable(lib, monkeypatch):
    from tools import pantry
    monkeypatch.setattr(pantry, "_local_chat", lambda *a, **k: json.dumps({
        "name": "Diri Kole Fritay", "english": "Leftover rice fry", "ingredients": [{"item": "leftover rice", "qty": "4 cups"},
                                                                                     {"item": "hot dogs", "qty": "6"}],
        "steps": ["Fry it.", "Serve."], "total_min": 20, "toddler": "Plain portion.",
        "macros": {"kcal": 520, "protein_g": 18, "carbs_g": 70, "fat_g": 18}}))
    d = chef.make_scraps(["leftover rice", "hot dogs", "eggs"])
    assert d["chef_scraps"] and d["id"].startswith("chef-diri-kole-fritay")
    assert nutri.dish(d["id"])["name"] == "Diri Kole Fritay"
    nutri.vote(d["id"], "like")
    assert nutri.taste_of(d["id"]) == "like"
    assert all(d["id"] not in day["meals"].values() for day in nutri.build_week(start=date(2026, 11, 23), pantry=[]))


def test_scraps_with_model_down_is_none(lib, monkeypatch):
    from tools import pantry
    monkeypatch.setattr(pantry, "_local_chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert chef.make_scraps(["rice", "eggs"]) is None


def test_plate_matches_a_dish(lib):
    assert chef.match_dish("poul nan sos with rice") == "poul-nan-sos"
    assert chef.match_dish("sushi") is None


def test_uneat_takes_it_back(lib):
    nutri.eat("griot", "dinner")
    nutri.eat("legim", "lunch")
    assert nutri.uneat("griot", "dinner")["kcal"] == 600
    assert [r["dish"] for r in storage.load("nutrition.json", {})["log"]] == ["legim"]


def test_shopping_notes_are_shared_and_deduped(lib):
    assert nutri.add_to_shopping([{"item": "chicken thighs", "qty": "5 lb"}, {"item": "Chicken thighs"}, {"item": " "}]) == 1
    assert nutri.add_to_shopping([{"item": "chicken thighs"}]) == 0
    nutri.log_weight(180)
    shop = nutri.page()["shopping"]
    assert any(r.get("note") and "chicken thighs" in r["item"] for r in shop)


def test_pantry_dates_are_days(lib):
    from tools import pantry
    pantry.add_items([{"name": "rice"}])
    v = nutri.pantry_view(pantry.list_items())
    assert v[0]["name"] == "rice" and v[0]["added"] == date.today().isoformat()


# -- review round 1 ------------------------------------------------------------------------------------------------------
def test_carried_lunch_is_not_bought_twice(lib):
    week = [{"date": "2026-11-23", "day_type": "heavy", "meals": {"breakfast": "akasan", "lunch": "griot", "dinner": "legim"}},
            {"date": "2026-11-24", "day_type": "heavy", "lunch_is_leftovers": True,
             "meals": {"breakfast": "ze", "lunch": "legim", "dinner": "pwason"}},
            {"date": "2026-11-25", "day_type": "rest", "lunch_is_leftovers": True,   # a stale flag: lunch is not yesterday's dinner
             "meals": {"breakfast": "oats", "lunch": "mayi", "dinner": "jerk"}}]
    rows = {r["item"]: r for r in nutri.shopping(week, [])}
    assert rows["eggplant"]["qty"] == "1 lb" and rows["eggplant"]["for"] == ["legim"]
    assert "cornmeal" in rows                                   # the stale-flagged lunch is still bought


def test_build_week_marks_leftover_lunches(lib):
    week = nutri.build_week(start=date(2026, 11, 23), pantry=[])
    for a, b in zip(week, week[1:]):
        assert b["lunch_is_leftovers"] == (b["meals"]["lunch"] == a["meals"]["dinner"])


def test_dislike_takes_it_off_this_weeks_plan(lib):
    plan = nutri.week()
    victim = next(m for d in plan for s, m in d["meals"].items() if s == "dinner")
    nutri.vote(victim, "dislike")
    assert all(victim not in d["meals"].values() for d in nutri.week())


def test_unknown_dish_is_its_own_error(lib):
    with pytest.raises(nutri.UnknownDish):
        nutri.eat("nope")
    d = dict(LIB[0], id="odd", ingredients=[{"qty": "1"}, {"item": "rice", "key": True}])
    assert nutri.pantry_match(d, [{"name": "rice"}])[0] == 1.0


def test_saved_maintenance_is_tdee(lib):
    nutri.log_weight(190)
    storage.save("nutrition.json", {"targets": {"kcal": 2500, "tdee": 2800}, "log": []})
    assert nutri.targets()["kcal"] == round((2800 + 250) * 1.06 / 10) * 10


def test_household_size_does_not_touch_members_text(lib):
    from tools import kitchen
    data = kitchen._data()
    data["household"] = {"members": "2 adults, 2 teens, 18-month-old son", "household_size": 5}
    storage.save(kitchen.FILE, data)
    nutri.log_weight(180)
    assert nutri.page()["household"]["size"] == 5


def test_scraps_without_steps_is_rejected(lib, monkeypatch):
    from tools import pantry
    monkeypatch.setattr(pantry, "_local_chat", lambda *a, **k: json.dumps(
        {"name": "Nothing", "ingredients": [{"item": "rice"}], "steps": [], "macros": {}}))
    assert chef.make_scraps(["rice", "eggs"]) is None


def test_shopping_dedupe_is_order_independent(lib):
    assert nutri.add_to_shopping([{"item": "red kidney beans"}]) == 1
    assert nutri.add_to_shopping([{"item": "kidney beans red"}]) == 0


def test_swapping_a_batch_dinner_keeps_tomorrows_lunch_honest(lib):
    plan = nutri.week()
    i = next(i for i, (a, b) in enumerate(zip(plan, plan[1:])) if b.get("lunch_is_leftovers"))
    after = nutri.swap(plan[i]["date"], "dinner")
    a, b = after[i], after[i + 1]
    assert (b["meals"]["lunch"] == a["meals"]["dinner"]) == bool(b["lunch_is_leftovers"])


def test_plate_match_needs_real_overlap(lib):
    assert chef.match_dish("Griot") == "griot"                                   # exact family name
    assert chef.match_dish("garden salad", ["lettuce", "croutons"]) is None             # nothing shared: no match
    assert chef.match_dish("fried fish", ["snapper", "rice"]) != "griot"                   # fish on the plate is never pork
    assert chef.match_dish("fried pork belly", ["orange slices", "pickled vegetables"]) == "griot"   # live: it WAS griot


# -- family favourites and the daily drink (owner, 2026-11-20) -----------------------------------------------------------
@pytest.fixture
def famlib(tmp_path, monkeypatch, lib):
    extra = [dict(_dish("ji-kowosol", "Ji Kowosòl", cat="drink", meals=("snack",), keys=("soursop",), tags=()),
                  family_favorite=["family"]),
             dict(_dish("ji-zoranj", "Ji Zoranj", cat="drink", meals=("breakfast",), keys=("oranges",), tags=()),
                  family_favorite=["family", "son"]),
             dict(_dish("labouyi-avwan", "Labouyi Avwan", cat="breakfast", meals=("breakfast",), keys=("oats",), tags=(), p=24),
                  family_favorite=["wife", "owner"]),
             dict(_dish("steak-pire", "Steak ak Pire Pòmdetè", keys=("steak", "potatoes"), tags=("high-protein",)),
                  family_favorite=["owner", "family"])]
    (nutri.DISH_DIR / "family.json").write_text(json.dumps(extra), encoding="utf-8")
    nutri.forget_cache()
    return nutri.library()


def test_every_day_gets_a_drink(famlib):
    week = nutri.build_week(start=date(2026, 11, 23), pantry=[])
    assert all(nutri.dish(d["meals"]["drink"])["category"] == "drink" for d in week)


def test_family_favourites_come_up_more(famlib):
    week = nutri.build_week(start=date(2026, 11, 23), pantry=[])
    picked = [m for d in week for m in d["meals"].values()]
    assert "steak-pire" in picked and "labouyi-avwan" in picked


def test_other_family_members_votes_count(famlib, monkeypatch):
    from core import users
    monkeypatch.setattr(users, "all_users", lambda: {"owner": {}, "wife": {}})
    storage._write(storage.user_dir("wife") / nutri.TASTE, {"likes": {"pwason": "x"}, "dislikes": {"jerk": "x"}})
    fam = nutri.household_taste()
    assert fam == {"likes": {"pwason": 1}, "dislikes": {"jerk": 1}}


def test_old_plans_without_a_drink_are_rebuilt(famlib):
    from datetime import date as d_
    key = nutri._monday().isoformat()
    n = nutri._record()
    n["plans"][key] = [{"date": d_.today().isoformat(), "day_type": "train", "meals": {"breakfast": "akasan", "lunch": "griot",
                                                                                    "dinner": "legim"}}]
    nutri._save(n)
    assert all("drink" in d["meals"] for d in nutri.week())


def test_all_drinks_disliked_does_not_rebuild_forever(famlib, monkeypatch):
    for i in ("ji-kowosol", "ji-zoranj"):
        nutri.vote(i, "dislike")
    nutri.week(refresh=True)
    calls = []
    real = nutri.build_week
    monkeypatch.setattr(nutri, "build_week", lambda *a, **k: calls.append(1) or real(*a, **k))
    nutri.week()
    nutri.week()
    assert calls == []


def test_a_broken_family_taste_file_is_skipped(famlib, monkeypatch):
    from core import users
    monkeypatch.setattr(users, "all_users", lambda: {"owner": {}, "wife": {}})
    storage._write(storage.user_dir("wife") / nutri.TASTE, [])
    assert nutri.household_taste() == {"likes": {}, "dislikes": {}}
