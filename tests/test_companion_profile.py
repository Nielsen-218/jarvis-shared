"""Tests for the appearance-profile schema in core/companion_profile.

Covers: defaults, validation of unknown adapter, rejection of forbidden
identity/brain fields, hex-color + numeric clamping, round-trip save/load,
corrupt file fallback, and adapter-capability registry shape.
"""
from __future__ import annotations

import json

import pytest

from core import companion_profile as cp


def test_default_profile_is_cosmetic_only():
    p = cp.default_profile()
    assert p["schema_version"] == cp.PROFILE_SCHEMA_VERSION
    assert p["adapter_id"] in cp.ADAPTERS
    assert set(p["materials"]).issuperset(set(cp.MATERIAL_SLOTS))
    for forbidden in cp.FORBIDDEN_FIELDS:
        assert forbidden not in p, forbidden


def test_validate_rejects_forbidden_identity_fields():
    for forbidden in ("identity", "memory", "voice_credentials", "brain_config",
                      "model_api_key", "voiceprint", "personality"):
        bad = {**cp.default_profile(), forbidden: "sensitive"}
        with pytest.raises(cp.ProfileError):
            cp.validate_profile(bad)


def test_validate_rejects_unknown_adapter_and_bad_id():
    with pytest.raises(cp.ProfileError):
        cp.validate_profile({**cp.default_profile(), "adapter_id": "does-not-exist"})
    with pytest.raises(cp.ProfileError):
        cp.validate_profile({**cp.default_profile(), "id": "Bad Id With Spaces!"})
    with pytest.raises(cp.ProfileError):
        cp.validate_profile({**cp.default_profile(), "schema_version": 999})
    with pytest.raises(cp.ProfileError):
        cp.validate_profile("not a dict")


def test_validate_clamps_bad_colors_and_numbers():
    raw = {
        "id": "custom", "adapter_id": "glb-model-v1", "model_url": cp.DEFAULT_MODEL_URL,
        "proportions": {"head_scale": 99, "arm_length": -3, "overall_scale": "abc"},
        "materials": {
            "shell_light": {"color": "not-a-hex", "metalness": 5, "roughness": -1},
            "chest_core":  {"color": "#3df0ff", "emissive": 99},
            "unknown_slot": {"color": "#ff0000"},
        },
        "lights": {"key": {"color": "oops", "intensity": 20}},
    }
    p = cp.validate_profile(raw)
    assert p["proportions"]["head_scale"] == 1.5          # clamp hi
    assert p["proportions"]["arm_length"] == 0.6          # clamp lo
    assert isinstance(p["proportions"]["overall_scale"], float)
    assert p["materials"]["shell_light"]["color"].startswith("#")
    assert 0 <= p["materials"]["shell_light"]["metalness"] <= 1
    assert 0 <= p["materials"]["shell_light"]["roughness"] <= 1
    assert 0 <= p["materials"]["chest_core"]["emissive"] <= 4
    # Unknown slot is preserved raw (renderer skips it) but defaults are present.
    for slot in cp.MATERIAL_SLOTS:
        assert slot in p["materials"]
    assert 0 <= p["lights"]["key"]["intensity"] <= 4


def test_adapter_registry_entries_declare_their_bones():
    """Every registered adapter must say what it provides; the JS side (profiles.js:ADAPTERS) must agree."""
    assert len(cp.ADAPTERS) >= 1
    ids = list(cp.ADAPTERS)
    for aid in ids:
        meta = cp.adapter_capabilities(aid)
        assert meta["bones_provided"]
        assert "model_kind" in meta


def test_profile_roundtrip_and_corruption(tmp_path):
    base = tmp_path
    saved = cp.save_profile({"id": "custom", "adapter_id": "glb-model-v1", "model_url": cp.DEFAULT_MODEL_URL,
                             "materials": {"accent_primary": {"color": "#ff00aa", "emissive": 1.5}}},
                            root=base)
    assert saved.name == "custom.json"
    loaded = cp.load_profile("custom", root=base)
    assert loaded["id"] == "custom"
    assert loaded["materials"]["accent_primary"]["color"] == "#ff00aa"
    # Corrupt the file; load falls back to default (does not raise).
    (base / "custom.json").write_text("{not json", encoding="utf-8")
    assert cp.load_profile("custom", root=base) == cp.default_profile()


def test_list_profiles_skips_corrupt(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(cp.default_profile()), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    forbidden = tmp_path / "leaky.json"
    forbidden.write_text(json.dumps({**cp.default_profile(), "voice_credentials": "x"}), encoding="utf-8")
    out = cp.list_profiles(root=tmp_path)
    ids = {p["id"] for p in out}
    assert ids == {"default"}, ids   # only the good one validates


def test_semantic_bone_contract_is_stable():
    """The bone contract is a public API. If a required bone is removed,
    every adapter must be re-reviewed; this test freezes the set so the
    change is deliberate."""
    assert "root" in cp.REQUIRED_BONES and "head" in cp.REQUIRED_BONES
    for optional in ("neck", "torso", "shoulderL", "shoulderR", "elbowL", "elbowR",
                     "wristL", "wristR", "fingersL", "fingersR",
                     "hipL", "hipR", "kneeL", "kneeR", "ankleL", "ankleR",
                     "earL", "earR", "chestCore", "faceScreen"):
        assert optional in cp.OPTIONAL_BONES
