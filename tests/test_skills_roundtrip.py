"""Round-trip correctness for skills that had no tests before the 2026-10-23 audit.

skill_smoke.py proves every skill runs and fails gracefully; these prove the data skills actually WORK: what is
saved can be read back through the skill a person would use, and what is removed is really gone.
"""
import re
import threading

import pytest


# -- pantry ------------------------------------------------------------------------------------------
def test_pantry_add_list_and_receipt(monkeypatch):
    from tools import pantry
    out = pantry.add_to_pantry("2 lb rice, black beans\neggs")
    assert out == "Added 3 items to the pantry. You now have 3 items."
    have = pantry.whats_in_pantry().lower()
    assert all(food in have for food in ("black beans", "eggs")) and "rice 2 lb" in have   # quantity kept with its item
    # A receipt is read by the local model; here it answers deterministically.
    monkeypatch.setattr(pantry, "_local_chat", lambda *a, **k:
                        '{"items":[{"name":"whole milk","qty":"1 gal"},{"name":"bananas","qty":""}]}')
    out = pantry.add_receipt("GV WHL MILK 1GAL 3.48\nBANANAS 0.99\nTOTAL 4.47")
    assert out.startswith("Added 2 items from the receipt: ") and "Pantry now has 5 items." in out
    assert "whole milk" in pantry.whats_in_pantry().lower()


def test_receipt_survives_the_local_model_being_down(monkeypatch):
    from tools import pantry
    def down(*a, **k):
        raise ConnectionError("local model offline")
    monkeypatch.setattr(pantry, "_local_chat", down)
    out = pantry.add_receipt("EGGS LARGE 18CT 4.29\nSUBTOTAL 4.29\nTAX 0.30")
    assert "eggs" in out.lower() and "subtotal" not in out.lower() and "tax" not in pantry.whats_in_pantry().lower()


# -- wardrobe ----------------------------------------------------------------------------------------
def test_wardrobe_add_list_show_remove(no_desktop_popups):
    from tools import fashion
    out = fashion.add_to_wardrobe("Navy blazer", "top", "navy", "wool blazer", False)
    item_id = re.search(r"as item (\w+)", out).group(1)
    assert out.endswith("Wardrobe now has 1 item.")
    assert f"[{item_id}] top: Navy blazer (navy)" in fashion.list_wardrobe("all")
    assert fashion.show_wardrobe() == "Opened 'Wardrobe' in the browser." and no_desktop_popups   # recorded, not opened
    fashion.remove_from_wardrobe(item_id)
    assert "Navy blazer" not in fashion.list_wardrobe("all")


COAT = {"name": "Camel overcoat", "why": "the season's key layer", "price": "$120",
        "url": "https://shop.example/coat", "pairs_with": "dark jeans and white sneakers"}


def test_lookbook_page_lists_its_pieces(no_desktop_popups):
    from pathlib import Path
    from urllib.parse import unquote, urlparse

    from tools import fashion
    out = fashion.create_lookbook("Fall layers", "Warm, simple pieces.", [COAT])
    assert out == "Opened 'Fall layers' in the browser." and no_desktop_popups
    page = Path(unquote(urlparse(no_desktop_popups[-1]).path).lstrip("/"))
    html = page.read_text(encoding="utf-8")
    assert "Camel overcoat" in html and "https://shop.example/coat" in html and "dark jeans" in html


def test_lookbook_call_missing_a_required_field_is_rejected_not_crashed():
    # Before the deep validator this exact call passed validation and crashed with KeyError: 'pairs_with'.
    from tools import registry
    partial = {k: v for k, v in COAT.items() if k != "pairs_with"}
    result, is_error = registry.run("create_lookbook", {"title": "Fall", "intro": "x", "items": [partial]})
    assert is_error and "items[0]" in result and "pairs_with" in result and "KeyError" not in result


# -- the memory vault --------------------------------------------------------------------------------
@pytest.fixture()
def vault_labels(monkeypatch):
    """The labelling model is the PAID Claude API — never call it from tests."""
    from core import vault
    monkeypatch.setattr(vault, "_label", lambda kind, text, note, created: {
        "title": "Generated title", "summary": text[:60], "tags": "parking, car, garage", "people": ""})
    return vault


def _wait_for_vault_threads():
    for t in threading.enumerate():
        if t.name.startswith("vault-"):
            t.join(10)


def test_vault_note_round_trip_with_delete_approval(vault_labels):
    from core import approvals, users
    from tools import vault
    vault.remember_note("Car is on level 3, row F", "parking")
    _wait_for_vault_threads()
    found = vault.recall_memories("parking")
    mid = re.search(r"\[([\w-]+)\] note", found).group(1)
    assert "Car is on level 3, row F" in found
    assert "level 3, row F" in vault.memory_details(mid)
    # Deleting needs the person's own "yes" — the AI can't approve it.
    asked = vault.delete_memory(mid)
    assert "Car is on level 3" in vault.recall_memories("parking")          # nothing deleted on the request
    approvals.on_user_message(users.current(), "yes")
    assert vault.delete_memory(mid, confirmed=True) == "Deleted from the vault."
    assert "level 3" not in vault.recall_memories("parking") and asked


def test_a_title_the_person_gives_is_kept(vault_labels):
    from tools import vault
    vault.remember_note("Car is on level 3, row F", "parking")
    _wait_for_vault_threads()
    out = vault.recall_memories("parking")
    assert ": parking" in out and "Generated title" not in out and "(untitled)" not in out


def test_a_note_is_never_untitled_or_mislabelled_when_labelling_fails(monkeypatch):
    from core import vault as core_vault
    from tools import vault
    def offline(*a, **k):
        raise ConnectionError("labelling model unreachable")
    monkeypatch.setattr(core_vault, "_label", offline)
    vault.remember_note("Spare key is in the blue toolbox in the garage")
    _wait_for_vault_threads()
    out = vault.recall_memories("spare key")
    assert ": Spare key is in the blue toolbox in the" in out                 # named by its own words
    assert "(untitled)" not in out and "transcribed" not in out              # a typed note isn't "being transcribed"
    assert "still saved and searchable" in out


def test_vault_status_counts_notes(vault_labels):
    from tools import vault
    vault.remember_note("Wifi password is on the router label", "wifi")
    _wait_for_vault_threads()
    assert "1 note" in vault.vault_status()


# -- price watches ------------------------------------------------------------------------------------
def test_price_watch_round_trip(monkeypatch):
    from tools import proactive
    monkeypatch.setattr(proactive, "read_price", lambda url: 24.99)
    assert proactive.watch_price("https://shop.example/item/123", "Desk lamp", 19.99) == \
        "Watching Desk lamp: currently 24.99. I'll tell you at or below 19.99."
    listed = proactive.list_price_watches()
    wid = re.search(r"\[(\w+)\] Desk lamp", listed).group(1)
    proactive.stop_price_watch(wid)
    assert "Desk lamp" not in proactive.list_price_watches()


def test_price_watch_explains_a_blocked_store(monkeypatch):
    from tools import proactive
    def blocked(url):
        raise ValueError("HTTP 403")
    monkeypatch.setattr(proactive, "read_price", blocked)
    out = proactive.watch_price("https://shop.example/item/9", "Chair", 50)
    assert "couldn't read a price" in out and proactive.list_price_watches() == "No price watches."


# -- nutrition, achievements, household -----------------------------------------------------------------
def test_food_log_adds_up_for_today():
    from tools import kitchen
    kitchen.log_food("Chicken breast", 280, 52)
    kitchen.log_food("Rice", 200, 4)
    assert kitchen.nutrition_today().startswith("Today so far: 480 kcal, 56 g protein.")


def test_achievements_reflect_logged_workouts():
    from tools import fitness, life
    fitness.log_workout("Easy run", [{"exercise": "run", "distance_mi": 3.0}], 6, "")
    out = life.my_achievements()
    assert "1-workout streak" in out and "1 in total" in out


def test_household_member_can_be_added_and_served(monkeypatch):
    from tools import voice
    monkeypatch.setattr(voice, "manual_speaker", None)          # module state: restored after the test
    out = voice.add_household_member("Ana", "wife", "Mrs. Ana", "Madanm Ana", "Madame Ana")
    assert "Added Ana (wife), id 'ana'" in out
    assert voice.switch_person("Ana").startswith("From the next message I'm serving Ana (addressed as 'Mrs. Ana')")
    assert voice.manual_speaker == "ana"                        # applied by the main loop on the next message
    assert "add them with add_household_member first" in voice.switch_person("Nobody")
