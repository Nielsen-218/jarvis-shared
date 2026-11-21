"""Tests for the pantry + receipt parsing + Haitian recipe fallback (tools/pantry.py).

Storage and the local model are both monkeypatched so these are fast, offline and never
touch the real household pantry.
"""
import pytest

from tools import pantry


@pytest.fixture
def store(monkeypatch):
    mem = {}
    monkeypatch.setattr(pantry.storage, "load", lambda f, d=None: mem.get(f, d if d is not None else {}))
    monkeypatch.setattr(pantry.storage, "save", lambda f, v: mem.__setitem__(f, v))
    return mem


def test_add_list_remove(store):
    pantry.add_items([{"name": "Eggplant"}, {"name": "Rice", "qty": "5 lb"}])
    names = [i["name"] for i in pantry.list_items()]
    assert "Eggplant" in names and "Rice" in names
    assert pantry.remove_item("rice") is True
    assert "Rice" not in [i["name"] for i in pantry.list_items()]


def test_add_merges_same_item(store):
    pantry.add_items([{"name": "Milk", "qty": "1 gal"}])
    pantry.add_items([{"name": "milk", "qty": "2 gal"}])  # same slug, updated qty
    milk = [i for i in pantry.list_items() if i["name"].lower() == "milk"]
    assert len(milk) == 1 and milk[0]["qty"] == "2 gal"


def test_parse_receipt_uses_model_json(monkeypatch):
    monkeypatch.setattr(pantry, "_local_chat",
                        lambda *a, **k: '{"items":[{"name":"whole milk","qty":"1 gal"},{"name":"eggplant","qty":""}]}')
    items = pantry.parse_receipt("GV WHL MILK 1GAL 3.48\nEGGPLANT 1.27")
    assert items == [{"name": "whole milk", "qty": "1 gal"}, {"name": "eggplant", "qty": ""}]


def test_parse_receipt_heuristic_fallback(monkeypatch):
    def down(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(pantry, "_local_chat", down)
    items = pantry.parse_receipt("BANANAS 1.14\nSUBTOTAL 5.00\nTAX 0.30\nMILK 3.48\nVISA 5.30")
    names = " ".join(i["name"].lower() for i in items)
    assert "banana" in names and "milk" in names         # kept food
    assert "subtotal" not in names and "tax" not in names and "visa" not in names  # dropped non-food


def test_recipe_fallback_when_model_down(store, monkeypatch):
    store["household/pantry.json"] = {"items": {"eggplant": {"name": "Eggplant", "added": 1}}}
    def down(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(pantry, "_local_chat", down)
    out = pantry.recipe_from_pantry().lower()
    assert "legim" in out or "sòs pwa" in out or "sos pwa" in out   # Haitian fallback suggestion


def test_recipe_empty_pantry_prompts_scan(store):
    out = pantry.recipe_from_pantry()
    assert "empty" in out.lower() or "scan" in out.lower()
