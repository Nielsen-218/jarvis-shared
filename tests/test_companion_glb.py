"""GLB model adapter for the Living Desktop Companion: profile schema support,
the pure renderer helpers (run under Node), and the Blender LOD pipeline
(run for real against the bundled avatar GLB when Blender is installed).
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from core import companion_profile as cp
from scripts import optimize_companion_model as opt

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "node" / "test_glb_adapter.mjs"
SAMPLE_GLB = ROOT / "data" / "users" / "owner" / "fashion" / "avatar" / "avatar.glb"


# -- profile schema ---------------------------------------------------------------------------------
def test_glb_adapter_is_registered_and_is_the_default():
    assert cp.ADAPTERS["glb-model-v1"]["model_kind"] == "glb"
    assert "procedural-robot-v1" not in cp.ADAPTERS
    d = cp.default_profile()
    assert d["adapter_id"] == "glb-model-v1"
    assert d["model_url"] == cp.DEFAULT_MODEL_URL and d["rig_url"] == cp.DEFAULT_RIG_URL
    assert cp.validate_profile(d)["rig_url"] == cp.DEFAULT_RIG_URL          # the default validates as shipped


def test_glb_profile_needs_a_same_origin_model_url():
    base = {**cp.default_profile(), "id": "robot", "adapter_id": "glb-model-v1", "model_url": None}
    with pytest.raises(cp.ProfileError):
        cp.validate_profile(base)                                   # no model_url
    for bad in ("https://evil.example/x.glb", "/api/../etc", "companion/models/x.glb", "/vendor/x.glb", "x" * 300):
        with pytest.raises(cp.ProfileError):
            cp.validate_profile({**base, "model_url": bad})
    ok = cp.validate_profile({**base, "model_url": "/companion/models/jarvis/jarvis-lod0.glb"})
    assert ok["model_url"] == "/companion/models/jarvis/jarvis-lod0.glb"
    assert ok["materials"]["eye_glow"]["color"].startswith("#")      # palette still applies to GLB glow slots
    assert cp.validate_profile({**base, "model_url": "/api/companion/model/abc.glb"})["model_url"].startswith("/api/")


def test_rig_url_must_be_a_same_origin_json_under_companion_rigs():
    base = cp.default_profile()
    for bad in ("https://evil.example/r.json", "/companion/rigs/../x.json", "/companion/models/r.json", "/companion/rigs/r.txt", "/companion/rigs\\x.json"):
        with pytest.raises(cp.ProfileError):
            cp.validate_profile({**base, "rig_url": bad})
    assert cp.validate_profile({**base, "rig_url": None})["rig_url"] is None          # a static, unrigged model is allowed
    assert cp.validate_profile({**base, "rig_url": "/companion/rigs/other.json"})["rig_url"] == "/companion/rigs/other.json"


# -- renderer helpers (actual JS, under Node) -------------------------------------------------------
def test_glb_adapter_js_suite_passes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    proc = subprocess.run([node, str(HARNESS)], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["failed"] == 0, summary
    assert summary["passed"] >= 40, summary


def test_companion_page_awaits_the_adapter_and_has_a_sprite_fallback():
    src = (ROOT / "app" / "companion.html").read_text(encoding="utf-8")
    assert "buildAdapterAsync" in src and "fallback-robot.png" in src
    assert (ROOT / "app" / "companion" / "fallback-robot.png").stat().st_size > 10_000
    prof = (ROOT / "app" / "companion" / "profiles.js").read_text(encoding="utf-8")
    assert '"glb-model-v1"' in prof and "buildRobot(" not in prof


def test_the_procedural_robot_is_gone():
    comp = ROOT / "app" / "companion"
    assert not (comp / "rig.js").exists() and not (comp / "facial.js").exists()
    for f in list(comp.glob("*.js")) + [ROOT / "app" / "companion.html"]:
        text = f.read_text(encoding="utf-8")
        assert "buildRobot(" not in text and "/companion/rig.js" not in text and "/companion/facial.js" not in text, f


# -- Blender LOD pipeline ---------------------------------------------------------------------------
def _glb_triangles(path: Path) -> int:
    """Count indexed triangles straight from the GLB's JSON chunk (no three.js needed)."""
    raw = path.read_bytes()
    assert raw[:4] == b"glTF"
    jlen = struct.unpack_from("<I", raw, 12)[0]
    j = json.loads(raw[20:20 + jlen])
    acc = j["accessors"]
    return sum(acc[p["indices"]]["count"] // 3 for m in j.get("meshes", []) for p in m["primitives"] if "indices" in p)


def test_optimize_pipeline_decimates_a_real_glb(tmp_path):
    if not opt.find_blender():
        pytest.skip("Blender not installed")
    if not SAMPLE_GLB.exists():
        pytest.skip("no sample GLB on this machine")
    src_tris = _glb_triangles(SAMPLE_GLB)
    targets = (max(1000, src_tris // 3), max(500, src_tris // 8))
    report = opt.optimize(SAMPLE_GLB, tmp_path, "sample", targets, timeout=600)
    assert report["source_triangles"] >= src_tris * 0.9
    assert (tmp_path / "report.json").exists()
    for lod, target in zip(report["lods"], targets):
        out = Path(lod["file"])
        assert out.exists() and out.stat().st_size > 1000
        measured = _glb_triangles(out)
        assert measured <= target * 1.15, (measured, target)      # measured, not assumed
        assert measured < src_tris
        assert abs(measured - lod["triangles"]) <= max(50, measured * 0.05)   # Blender's count matches the file


def test_optimize_cli_reports_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        opt.optimize(tmp_path / "nope.glb", tmp_path, blender=opt.find_blender() or r"C:\nope\blender.exe")


def test_robot_rig_js_suite_passes():
    """Skin weights, skeleton, proxy-bone sync and face helpers, run under Node against the real rig JSON."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    proc = subprocess.run([node, str(ROOT / "tests" / "node" / "test_robot_skin.mjs")], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["failed"] == 0 and summary["passed"] >= 50, summary


def _glb_json(path: Path) -> dict:
    raw = path.read_bytes()
    assert raw[:4] == b"glTF"
    jlen = struct.unpack_from("<I", raw, 12)[0]
    return json.loads(raw[20:20 + jlen])


def _glb_pos_bounds(j: dict):
    acc = j["accessors"]
    mins, maxs = [1e9] * 3, [-1e9] * 3
    for m in j["meshes"]:
        for p in m["primitives"]:
            pa = acc[p["attributes"]["POSITION"]]
            for i in range(3):
                mins[i] = min(mins[i], pa["min"][i]); maxs[i] = max(maxs[i], pa["max"][i])
    return [round(x, 4) for x in mins], [round(x, 4) for x in maxs]


def test_shipped_multimaterial_robot_has_per_part_slots_and_preserves_geometry():
    """The default companion model is split into per-region material SLOTS so colour can be set
    per part. Geometry (vertex positions) must be preserved so the runtime position-based skin and
    the face landmarks still line up. Built by scripts/split_companion_materials.py."""
    multimat = ROOT / "app" / "companion" / "models" / "robot" / "robot-lod1-multimat.glb"
    base = ROOT / "app" / "companion" / "models" / "robot" / "robot-lod1.glb"
    assert multimat.exists(), "run scripts/split_companion_materials.py to build the multi-material model"
    mj, bj = _glb_json(multimat), _glb_json(base)
    names = [m["name"] for m in mj["materials"]]
    assert names == ["shell", "eye_glow", "chest_core", "accent_primary"], names
    # the shell must NOT be named "shell_light" (that name hits the /light/ accent rule in the adapter)
    assert "shell_light" not in names
    # every primitive keeps its UVs (baked texture) and the texture image survives
    for m in mj["meshes"]:
        for p in m["primitives"]:
            assert "TEXCOORD_0" in p["attributes"]
    assert len(mj.get("images", [])) >= 1
    # geometry preserved: identical position bounds to the single-material source
    assert _glb_pos_bounds(mj) == _glb_pos_bounds(bj)
    # the glow slots carry the concept emissive colours (so a plain viewer shows them too)
    emis = {m["name"]: m.get("emissiveFactor") for m in mj["materials"]}
    assert emis["eye_glow"] and emis["eye_glow"][0] > 0.9        # magenta (R high)
    assert emis["chest_core"] and emis["chest_core"][2] > 0.9    # cyan (B high)


def test_sculpted_build_variants_are_valid_and_reshaped():
    """Free-form mesh sculpting via headless Blender (scripts/sculpt_companion.py) produces body-shape
    variants. They must keep the per-part material slots, UVs and texture, keep the same faces (sculpting
    moves vertices, it doesn't retopologise), and actually differ in shape from the standard build."""
    robot = ROOT / "app" / "companion" / "models" / "robot"
    base = robot / "robot-lod1-multimat.glb"
    base_tris = _glb_triangles(base)
    base_bounds = _glb_pos_bounds(_glb_json(base))
    for name in ("rounder", "lean"):
        v = robot / f"robot-lod1-{name}.glb"
        assert v.exists(), f"run scripts/sculpt_companion.py --build {name}"
        j = _glb_json(v)
        assert [m["name"] for m in j["materials"]] == ["shell", "eye_glow", "chest_core", "accent_primary"]
        assert all("TEXCOORD_0" in p["attributes"] for m in j["meshes"] for p in m["primitives"])
        assert len(j.get("images", [])) >= 1
        # sculpting moves vertices, it doesn't retopologise; the pre-weld may drop a couple of degenerate faces
        assert abs(_glb_triangles(v) - base_tris) <= 12, (name, _glb_triangles(v), base_tris)


def test_sculpt_build_presets_are_defined():
    from scripts import sculpt_companion as sc
    assert "rounder" in sc.BUILDS and "lean" in sc.BUILDS
    for strokes in sc.BUILDS.values():
        for s in strokes:
            assert s["mode"] in ("inflate", "grab", "smooth", "pinch")
            assert len(s["center"]) == 3 and s["radius"] > 0
    # the Studio exposes the build picker
    studio = (ROOT / "app" / "studio.html").read_text(encoding="utf-8")
    assert "robot-lod1-rounder.glb" in studio and "robot-lod1-lean.glb" in studio and 'id="build"' in studio


def test_default_profile_uses_the_multimaterial_model():
    import core.companion_profile as cp
    assert cp.DEFAULT_MODEL_URL.endswith("robot-lod1-multimat.glb")
    assert cp.default_profile()["model_url"] == cp.DEFAULT_MODEL_URL
    # the JS side must agree (plain-browser / lab path has no Python API to ask)
    prof = (ROOT / "app" / "companion" / "profiles.js").read_text(encoding="utf-8")
    assert "robot-lod1-multimat.glb" in prof and "eye_glow" in prof


def test_rig_json_matches_the_shipped_model_frame():
    rig = json.loads((ROOT / "app" / "companion" / "rigs" / "robot-rig.json").read_text(encoding="utf-8"))
    assert rig["model_height_m"] == 0.30
    from core.companion_profile import SEMANTIC_BONES
    for bone in SEMANTIC_BONES:                          # the clips drive these names; the rig must provide them
        if bone not in ("root", "chestCore", "faceScreen"):
            assert bone in rig["joints"], bone
    for e in ("eyeL", "eyeR", "mouth"):
        assert len(rig["face"][e]["center"]) == 3 and len(rig["face"][e]["normal"]) == 3


def test_accessories_js_suite_and_profile_field():
    import shutil, subprocess, json as _json
    import core.companion_profile as cp
    assert "none" in cp.ACCESSORY_KINDS and "tophat" in cp.ACCESSORY_KINDS
    p = cp.validate_profile({**cp.default_profile(), "accessory": "tophat"})
    assert p["accessory"] == "tophat"
    assert cp.validate_profile({**cp.default_profile(), "accessory": "bogus"})["accessory"] == "none"
    node = shutil.which("node")
    if not node:
        import pytest; pytest.skip("node not installed")
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    proc = subprocess.run([node, str(root / "tests" / "node" / "test_accessories.mjs")], cwd=str(root), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _json.loads(proc.stdout.strip().splitlines()[-1])["failed"] == 0


def test_viseme_js_suite_passes():
    import shutil, subprocess, json as _json
    node = shutil.which("node")
    if not node:
        import pytest; pytest.skip("node not installed")
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    proc = subprocess.run([node, str(root / "tests" / "node" / "test_viseme.mjs")], cwd=str(root), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _json.loads(proc.stdout.strip().splitlines()[-1])["failed"] == 0
    html = (root / "app" / "companion.html").read_text(encoding="utf-8")
    assert "textToVisemes" in html and "VISEME_SHAPES" in html      # wired into the speech path
