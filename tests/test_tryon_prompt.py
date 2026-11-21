"""Which part of the photo's clothes a try-on replaces, and what the request tells the model about it."""
from tools import studio


def test_part_follows_the_garment_categories():
    assert studio.tryon_part(["top"]) == "top"
    assert studio.tryon_part(["top", "outerwear"]) == "top"
    assert studio.tryon_part(["bottom"]) == "bottom"
    assert studio.tryon_part(["top", "bottom"]) == "both"
    assert studio.tryon_part(["dress"]) == "both"
    assert studio.tryon_part(["shoes"]) == "none"


def test_prompt_says_the_grey_is_blank_and_garments_are_plain():
    text = studio.tryon_prompt("man", "the white polo shown in image 2", "both", "dark skin", "lean")
    assert "blank placeholders" in text and "no printed text" in text and "not more muscular" in text
    assert "the white polo shown in image 2" in text and "his own shoes" in text


def test_top_only_keeps_the_trousers_from_the_photo():
    text = studio.tryon_prompt("man", "the tee shown in image 2", "top", "dark skin", "")
    assert "his own trousers and shoes as in image 1" in text


def test_unblanked_photo_uses_the_plain_wording():
    text = studio.tryon_prompt("man", "x", "none", "s", "", neutral=False)
    assert "blank placeholders" not in text
