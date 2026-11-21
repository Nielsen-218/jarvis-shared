"""Pure image helpers that keep the photo's own clothes and wall out of a try-on render (synthetic images only)."""
import numpy as np
from PIL import Image

from imaging import person

FACE = (60, 20, 40, 50)          # x, y, w, h of a pretend face


def _person_matte(h=300, w=160):
    """A head blob, a body blob below it, and a 'wall fragment' blob next to the head that the matte caught."""
    m = np.zeros((h, w), np.float32)
    m[15:75, 55:105] = 1.0         # head + hair
    m[75:260, 30:130] = 1.0        # torso and legs
    return m


def test_head_matte_keeps_head_and_drops_body():
    m = person.head_matte(_person_matte(), FACE)
    assert m[45, 80] > 0.9                      # middle of the face
    assert m[150, 80] == 0.0                    # chest is not part of the head
    assert m[250, 80] == 0.0


def test_head_matte_drops_wall_next_to_hair():
    matte = _person_matte()
    matte[10:80, 120:155] = 1.0                 # a detached blob (wall) beside the head
    m = person.head_matte(matte, FACE)
    assert m[40, 140] == 0.0
    assert m[45, 80] > 0.9


def test_head_matte_is_feathered_not_a_box():
    m = person.head_matte(_person_matte(), FACE)
    assert 0.0 < m[60:76, 80].max() <= 1.0 and ((m > 0.05) & (m < 0.95)).any()


def test_skin_mask_follows_the_faces_own_colour():
    img = np.full((120, 120, 3), (20, 20, 20), np.uint8)        # dark clothes
    img[:, :60] = (120, 80, 60)                                 # skin-coloured half
    mask = person.skin_mask(Image.fromarray(img), (15, 20, 30, 40))
    assert mask[60, 20] == 1.0 and mask[60, 100] == 0.0


def test_clothing_region_spares_skin_head_and_shoes():
    matte = _person_matte()
    skin = np.zeros_like(matte)
    skin[150:170, 60:80] = 1.0                                  # a hand on the torso
    region = person.clothing_region(matte, skin, FACE, "both")
    assert region[120, 50] > 0.9                                # shirt is covered
    assert region[160, 70] < 0.05                               # hand is kept
    assert region[40, 80] == 0.0                                # head is kept
    assert region[255, 80] == 0.0                               # shoe zone is kept
    assert region[120, 5] == 0.0                                # wall outside the person is untouched


def test_top_only_and_bottom_only_split_at_the_hip():
    matte, skin = _person_matte(), np.zeros((300, 160), np.float32)
    top = person.clothing_region(matte, skin, FACE, "top")
    bottom = person.clothing_region(matte, skin, FACE, "bottom")
    assert top[100, 80] > 0.9 and top[222, 80] < 0.01
    assert bottom[212, 80] > 0.9 and bottom[100, 80] < 0.01


def test_neutralise_clothes_paints_flat_grey_and_keeps_the_face(monkeypatch):
    img = Image.fromarray(np.random.default_rng(1).integers(0, 255, (300, 160, 3), dtype=np.uint8))
    monkeypatch.setattr(person, "find_face", lambda *a, **k: FACE)
    monkeypatch.setattr(person, "person_mask", lambda im: _person_matte())
    monkeypatch.setattr(person, "skin_mask", lambda im, f: np.zeros((300, 160), np.float32))
    out = np.asarray(person.neutralise_clothes(img, "both", grey=150)).astype(int)
    assert abs(out[130:140, 60:100] - 150).max() <= 2           # chest is now flat grey: no print left
    assert (out[40:50, 70:90] == np.asarray(img)[40:50, 70:90]).all()   # face untouched


def test_neutralise_without_a_face_returns_the_photo(monkeypatch):
    img = Image.new("RGB", (50, 80), (9, 9, 9))
    monkeypatch.setattr(person, "find_face", lambda *a, **k: None)
    assert person.neutralise_clothes(img).tobytes() == img.tobytes()


def test_restore_face_pastes_only_the_matted_head(monkeypatch):
    wall = (200, 30, 30)
    orig = Image.new("RGB", (160, 300), wall)
    result = Image.new("RGB", (160, 300), (180, 180, 180))
    arr = np.asarray(orig).copy(); arr[15:75, 55:105] = (90, 60, 40)
    orig = Image.fromarray(arr)
    monkeypatch.setattr(person, "find_face", lambda *a, **k: FACE)
    monkeypatch.setattr(person, "person_mask", lambda im: _person_matte())
    out = np.asarray(person.restore_face(orig, result)).astype(int)
    assert abs(out[45, 80] - np.array([90, 60, 40])).max() < 10      # real head arrived
    assert abs(out[40, 140] - 180).max() <= 1                        # no wall next to it
    assert abs(out[200, 20] - 180).max() <= 1
