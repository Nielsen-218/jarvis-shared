"""Build app/companion/rigs/robot-rig.json for the textured concept robot.

The Hunyuan3D model is one static mesh. This writes the data the renderer (robot-skin.js) needs to turn it into a
skinned character at load time: joint pivots, capsule influences for the skin weights, and the face landmarks
(eye centres/normals measured from the magenta texels of the baked texture).

Joint pivots were read off metric-grid views (scripts/model_probe.py) of robot-lod0.glb; every LOD shares the
same frame (0.30 m tall, feet on y=0, front +Z, character's left = -X, matching the procedural rig).

  <blender-avengers>/02-image-to-3d/Hunyuan3D-2/.venv/Scripts/python.exe scripts/build_companion_rig.py MODEL.glb
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_probe as mp  # noqa: E402

MOUTH_Y = 0.1775      # centre of the chin vent slit, read off the front view
OUT = Path(__file__).resolve().parent.parent / "app" / "companion" / "rigs" / "robot-rig.json"


def mirror(p):
    return [-p[0], p[1], p[2]]


def build(model_path):
    m = mp.load(model_path)
    lo, hi = m.bounds
    assert abs((hi[1] - lo[1]) - 0.30) < 0.005 and abs(lo[1]) < 0.005, f"expected a 0.30 m model on y=0, got {lo} {hi}"

    # ---- joint pivots (metres). Left = -X. ----
    P = {
        "pelvis": [0, 0.090, 0], "torso": [0, 0.098, 0], "neck": [0, 0.150, 0], "head": [0, 0.158, 0],
        "shoulder": [-0.048, 0.137, 0.0], "elbow": [-0.059, 0.115, 0.0], "wrist": [-0.0757, 0.092, 0.0], "fingers": [-0.0735, 0.080, 0.0],
        "hip": [-0.030, 0.098, 0.0], "knee": [-0.033, 0.045, 0.004], "ankle": [-0.033, 0.018, 0.0],
        "ear": [-0.085, 0.215, 0.0],
    }
    joints = {"pelvis": {"parent": None, "pivot": P["pelvis"]}, "torso": {"parent": "pelvis", "pivot": P["torso"]},
              "neck": {"parent": "torso", "pivot": P["neck"]}, "head": {"parent": "neck", "pivot": P["head"]}}
    for s, sgn in (("L", 1), ("R", -1)):
        mx = (lambda p: p) if s == "L" else mirror
        joints[f"ear{s}"] = {"parent": "head", "pivot": mx(P["ear"])}
        joints[f"shoulder{s}"] = {"parent": "torso", "pivot": mx(P["shoulder"])}
        joints[f"elbow{s}"] = {"parent": f"shoulder{s}", "pivot": mx(P["elbow"])}
        joints[f"wrist{s}"] = {"parent": f"elbow{s}", "pivot": mx(P["wrist"])}
        joints[f"fingers{s}"] = {"parent": f"wrist{s}", "pivot": mx(P["fingers"])}
        joints[f"hip{s}"] = {"parent": "pelvis", "pivot": mx(P["hip"])}
        joints[f"knee{s}"] = {"parent": f"hip{s}", "pivot": mx(P["knee"])}
        joints[f"ankle{s}"] = {"parent": f"knee{s}", "pivot": mx(P["ankle"])}

    # ---- capsule influences for the body (head/ears are rule based, see robot-skin.js) ----
    seg = [
        {"bone": "torso", "a": [0, 0.145, 0], "b": [0, 0.108, 0], "r": 0.045},
        {"bone": "pelvis", "a": [0, 0.100, 0], "b": [0, 0.078, 0], "r": 0.040},
    ]
    for s in ("L", "R"):
        mx = (lambda p: p) if s == "L" else mirror
        seg += [
            {"bone": f"shoulder{s}", "a": mx([-0.048, 0.137, 0]), "b": mx([-0.059, 0.115, 0]), "r": 0.020},
            {"bone": f"elbow{s}", "a": mx([-0.059, 0.115, 0]), "b": mx([-0.0757, 0.092, 0]), "r": 0.017},
            {"bone": f"wrist{s}", "a": mx([-0.0757, 0.092, 0]), "b": mx([-0.072, 0.064, 0]), "r": 0.022},
            {"bone": f"fingers{s}", "a": mx([-0.0735, 0.078, 0]), "b": mx([-0.066, 0.060, 0]), "r": 0.012},
            {"bone": f"hip{s}", "a": mx([-0.030, 0.098, 0]), "b": mx([-0.033, 0.047, 0]), "r": 0.027},
            {"bone": f"knee{s}", "a": mx([-0.033, 0.047, 0]), "b": mx([-0.033, 0.020, 0]), "r": 0.026},
            {"bone": f"ankle{s}", "a": mx([-0.033, 0.020, 0]), "b": mx([-0.033, 0.012, 0.030]), "r": 0.030},
        ]

    # ---- face landmarks from the baked texture ----
    pts, col, nrm = mp.sample_colors(m, 1500000)
    r, g, b = col[:, 0].astype(int), col[:, 1].astype(int), col[:, 2].astype(int)
    mag = (r > 170) & (b > 170) & (g < 110)
    face = {}
    for name, sel in (("eyeL", mag & (pts[:, 0] < -0.015) & (pts[:, 2] > 0.03)), ("eyeR", mag & (pts[:, 0] > 0.015) & (pts[:, 2] > 0.03))):
        p, n = pts[sel], nrm[sel]
        c = p.mean(0); nn = n.mean(0); nn /= np.linalg.norm(nn)
        rad = float(np.percentile(np.linalg.norm((p - c) - np.outer((p - c) @ nn, nn), axis=1), 99))
        face[name] = {"center": c.round(5).tolist(), "normal": nn.round(4).tolist(), "radius": round(rad, 5)}
    # mouth: sit flush on the chin-vent surface (the face leans, so take the real surface point and normal)
    cell = (np.hypot(pts[:, 0], pts[:, 1] - MOUTH_Y) < 0.004) & (pts[:, 2] > 0.03)
    front = cell & (pts[:, 2] > pts[cell, 2].max() - 0.0015)
    mc = pts[front].mean(0); mn = nrm[front].mean(0); mn /= np.linalg.norm(mn)
    face["mouth"] = {"center": [0.0, MOUTH_Y, round(float(mc[2]), 5)], "normal": mn.round(4).tolist(), "width": 0.020, "height": 0.028}

    rig = {"schema": 1, "model_height_m": 0.30, "frame": "y-up, front +Z, left = -X", "joints": joints, "segments": seg,
           "head_rules": {"neck_y": [0.150, 0.160], "neck_max_abs_x": [0.050, 0.060], "ear_abs_x": [0.060, 0.068], "ear_y": [0.165, 0.175]},
           "clip_bias": {"shoulderL": [0, 0, -0.25], "shoulderR": [0, 0, 0.25]},
           "face": face}
    return rig


if __name__ == "__main__":
    rig = build(sys.argv[1])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rig, indent=1), encoding="utf-8")
    print("wrote", OUT, "joints", len(rig["joints"]), "segments", len(rig["segments"]), "eyeL", rig["face"]["eyeL"])
