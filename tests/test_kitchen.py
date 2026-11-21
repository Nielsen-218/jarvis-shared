"""Chef, nutritionist and grocery deals (network faked)."""
from fastapi.testclient import TestClient

from tools import kitchen


def test_nutrition_targets_from_trainer_profile():
    from tools import fitness

    assert "Need weight, height, age" in kitchen.nutrition_targets("lean muscle")
    for f, v in [("weight_lbs", "215"), ("height", "6'2.5\""), ("age", "27"), ("sex", "male")]:
        fitness.set_fitness_profile(f, v)
    out = kitchen.nutrition_targets("lean muscle")
    assert "215 g protein" in out and "kcal" in out
    t = kitchen.storage.load(kitchen.NUTRITION, {})["targets"]
    assert 2800 <= t["kcal"] <= 3500 and t["protein_g"] == 215
    out = kitchen.log_food("4 eggs and toast", 450, 30)
    assert out.startswith("Today: 450 of") and "185 g protein" in out


def test_recipe_plan_shopping_list():
    kitchen.save_recipe("Diri ak pwa with chicken", [
        {"item": "rice", "qty": "3 cups", "aisle": "Pantry"}, {"item": "red beans", "qty": "2 cans", "aisle": "Pantry"},
        {"item": "chicken thighs", "qty": "3 lb", "aisle": "Meat"}], ["Cook beans", "Cook rice in bean water", "Roast chicken"],
        5, 60, 14.5, {"kcal": 650, "protein_g": 48}, "Mash the beans and rice; shred chicken small; no added salt.")
    kitchen.save_recipe("Egg scramble", [{"item": "eggs", "qty": "12", "aisle": "Dairy"},
                                         {"item": "rice", "qty": "1 cup", "aisle": "Pantry"}], ["Scramble"])
    assert "Recipes not saved yet" in kitchen.save_meal_plan("2026-10-12", [
        {"day": "Mon", "meals": [{"meal": "breakfast", "recipe": "Egg scramble"},
                                 {"meal": "dinner", "recipe": "Diri ak pwa with chicken"},
                                 {"meal": "lunch", "recipe": "Leftover soup"}]}])
    assert kitchen.todays_meals("Mon").startswith("Mon: breakfast: Egg scramble")
    items = kitchen._shopping_items(kitchen._data())
    assert [i["item"] for i in items] == ["eggs", "chicken thighs", "red beans", "rice"]  # sorted by aisle
    assert sorted(next(i for i in items if i["item"] == "rice")["qty"]) == ["1 cup", "3 cups"]
    assert kitchen.build_shopping_list().startswith("Added 4 items")
    from tools.notes import read_list
    assert "chicken thighs (3 lb)" in read_list("shopping", False)
    assert "For the baby: Mash the beans" in kitchen.get_recipe("diri ak pwa")


def test_grocery_deals(monkeypatch):
    assert "ZIP" in kitchen.grocery_deals("eggs")
    kitchen.set_household_food("zip", "28307")
    fake = {"eggs": [{"store": "ALDI", "name": "Large eggs 18ct", "price": 2.99, "unit": "", "until": "2026-10-14"},
                     {"store": "Food Lion", "name": "Eggs", "price": 3.49, "unit": "", "until": "2026-10-14"}],
            "rice": []}
    monkeypatch.setattr(kitchen, "_flipp", lambda q, z: fake.get(q, []))
    out = kitchen.grocery_deals("eggs, rice")
    assert "- eggs: ALDI $2.99" in out and "Also: Food Lion $3.49" in out and "- rice: no flyer deals" in out
    assert "Most savings at: ALDI (1 item)" in out


def test_kitchen_api():
    from core import appserver
    from tools import voice

    c = TestClient(appserver.AppServer(lambda uid: None).app)
    voice.pair_codes["444444"] = "owner"
    h = {"Authorization": "Bearer " + c.post("/api/pair", json={"code": "444444"}).json()["token"]}
    d = c.get("/api/kitchen", headers=h).json()
    assert {"plan", "recipes", "shopping", "targets", "week", "toddler_rules"} <= set(d) and len(d["week"]) == 7


def test_store_assignment_and_route(monkeypatch):
    from tools import kitchen, travel

    kitchen.set_household_food("zip", "62701")
    kitchen.save_recipe("Diri ak pwa", [{"item": "Rice", "qty": "2 cups"}, {"item": "Plantains", "qty": "4"}], ["Cook."])
    kitchen.save_meal_plan("2026-10-12", [{"day": "Mon", "meals": [{"meal": "dinner", "recipe": "Diri ak pwa"}]}])
    assert "Costco 1 item" in kitchen.assign_stores({"rice": "Costco", "Plantains": "Commissary"})
    assert {i["item"]: i["store"] for i in kitchen.app_data()["shopping"]} == {"Plantains": "Commissary", "Rice": "Costco"}

    places = {"Costco": ("39.84", "-88.95", "Costco, 100, Main Street, Lakeside, Decatur, Illinois"),
              "Lincoln Commissary": ("39.80", "-89.64", "Lincoln Commissary, 51, Base Road, Springfield, Illinois")}
    monkeypatch.setattr(travel, "geocode", lambda q, limit=1: [{"lat": "39.78", "lon": "-89.65", "display_name": q}])
    monkeypatch.setattr(kitchen, "_osm_near", lambda q, lat, lon: [
        {"lat": places[q][0], "lon": places[q][1], "name": q, "display_name": places[q][2]}] if q in places else [])

    def fake_get(url, timeout=15):
        if "/table/" in url:
            return {"durations": [[0, 900]]}
        if "/trip/" in url:
            n = url.split("driving/")[1].split("?")[0].count(";")
            return {"waypoints": [{"waypoint_index": i} for i in range(n + 1)]}
        return {"routes": [{"duration": 1800, "distance": 32000}]}
    monkeypatch.setattr(travel, "_get", fake_get)
    out = kitchen.shopping_route(["Costco", "Commissary"], cold_last="Commissary")
    assert "1. Costco: Costco, 100 Main Street, Decatur" in out and "2. Commissary: Lincoln Commissary" in out
    assert "about 30 min" in out and "waypoints=" in out
    assert kitchen.app_data()["route"]["stops"][-1]["store"] == "Commissary"


def test_price_book_unit_prices_and_deals_comparison(monkeypatch):
    from tools import kitchen

    assert kitchen._unit_price(16.72, "60 ct") == (16.72 / 60, "each")
    assert kitchen._unit_price(6.0, "48 oz") == (2.0, "lb") and round(kitchen._unit_price(4.8, "2 dozen")[0], 6) == 0.2
    kitchen.log_price("Large eggs", "Costco", 16.72, "60 ct", "estimate")
    out = kitchen.log_price("large eggs", "Sam's Club", 4.82, "24 ct")
    assert "Cheapest known: Sam's Club $4.82 for 24 ct ($0.20/each)" in out
    kitchen.log_price("large eggs", "Costco", 15.99, "60 ct", "receipt")  # newest price per store replaces the old one
    assert kitchen.price_book("eggs").count("Costco") == 1 and "receipt" in kitchen.price_book("eggs")
    kitchen.set_household_food("zip", "62701")
    monkeypatch.setattr(kitchen, "_flipp", lambda q, z: [])
    deals = kitchen.grocery_deals("eggs, rice")
    assert "Warehouse / saved prices" in deals and "- eggs: Sam's Club $4.82" in deals
