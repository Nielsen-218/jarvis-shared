"""The owner's standing rule (2026-11-09): everything from Meshy is his - paid plan, his own designs."""
import json

from core import assetrights


def test_meshy_by_name_or_by_the_owners_fingerprints(tmp_path, monkeypatch):
    monkeypatch.setattr(assetrights, "_store", lambda: tmp_path / "owner-assets.json")
    assert assetrights.is_meshy(r"C:\Users\you\Downloads\Meshy_AI_Neon_Sentinel_biped.glb")
    assert assetrights.is_meshy("Meshy_AI_Pink_Confidence_Look_20261029_t-20261031-130948.glb")
    assert not assetrights.is_meshy("Shadow_world_main_character-20261101-120830.glb")
    assert assetrights.is_meshy("Shadow_world_main_character-20261101-120830.glb",
                                "41150e0322f2d05594a57040ea5ee1af035ee1af5a7ab723e0f864385844fb60")    # renamed export, known
    assert not assetrights.is_meshy("hunyuan_output.glb") and not assetrights.is_meshy("my_mesh.glb", "00ff")
    assetrights.remember_meshy("00ff")
    assert assetrights.is_meshy("my_mesh.glb", "00ff")


def test_the_record_is_written_once_and_never_for_unknown_sources(tmp_path):
    out = tmp_path / "rig"
    out.mkdir()
    assert not assetrights.record(out, "random_model.glb", "abc")
    assert not (out / "provenance.json").exists()
    assert assetrights.record(out, r"C:\Downloads\Meshy_AI_Hero.glb", "abc")
    prov = json.loads((out / "provenance.json").read_text())
    assert prov["original_character"] is True and "paid Meshy plan" in prov["license"] and "not checked" in prov["recorded_by"]
    assert not assetrights.record(out, r"C:\Downloads\Meshy_AI_Hero.glb", "abc")                     # never overwrites


def test_the_grader_accepts_it(tmp_path):
    from core import unreal
    out = tmp_path / "rig"
    out.mkdir()
    (out / "report.json").write_text(json.dumps({"pose_tests": [{"test": "a", "pass": True}]}))
    assetrights.record(out, "Meshy_AI_Hero.glb", "abc")
    assert unreal.grade_character(out)["gates"]["provenance"]["state"] == "pass"
