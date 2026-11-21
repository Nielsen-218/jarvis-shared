"""HELD-OUT creatures: written after the anatomy algorithm was frozen (docs/rigging/DOJO.md). Nobody tuned on these.

  blender -b --python hub/synth_heldout.py -- NAME OUT.glb      NAME in: tripod crab manta centipede hydra starfish

They are deliberately unlike the development creatures (no humanoid/quadruped/dragon/spider/bird/serpent): 3 legs, mixed
limbs, wings without legs, 12 leg pairs, several heads, radial symmetry."""
import math
import os
import sys

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from synth_creatures import Body  # noqa: E402


def tripod():
    b = Body()
    b.blob((0, 0, 0.62), 1.0, scale=(0.16, 0.16, 0.2))
    for k in range(3):
        a = math.radians(90 + 120 * k)
        dx, dy = math.cos(a), math.sin(a)
        b.tube([(0.1 * dx, 0.1 * dy, 0.55), (0.35 * dx, 0.35 * dy, 0.45), (0.55 * dx, 0.55 * dy, 0.2), (0.62 * dx, 0.62 * dy, 0.02)], 0.06, 0.025)
    for s in (-1, 1):
        b.tube([(s * 0.14, 0, 0.7), (s * 0.3, 0.02, 0.85), (s * 0.4, 0.05, 1.05)], 0.035, 0.015)
    b.tube([(0, 0, 0.78), (0, 0, 1.05), (0, 0, 1.3)], 0.06, 0.04)
    for s in (-1, 1):
        b.tube([(0, 0, 1.28), (s * 0.12, 0, 1.45)], 0.04, 0.035)
        b.blob((s * 0.14, 0, 1.5), 0.07)
    return b, 0.008


def crab():
    b = Body()
    b.blob((0, 0, 0.2), 1.0, scale=(0.3, 0.2, 0.09))
    for s in (-1, 1):
        for y, dy in ((-0.1, -0.12), (0.0, 0.0), (0.08, 0.1), (0.15, 0.18)):
            b.tube([(s * 0.25, y, 0.2), (s * 0.5, y + dy * 0.5, 0.3), (s * 0.65, y + dy, 0.1), (s * 0.7, y + dy * 1.1, 0.01)], 0.03, 0.012)
        b.tube([(s * 0.25, -0.18, 0.22), (s * 0.4, -0.38, 0.28), (s * 0.45, -0.55, 0.26)], 0.04, 0.035)
        b.blob((s * 0.47, -0.6, 0.26), 1.0, scale=(0.07, 0.1, 0.05))
        b.tube([(s * 0.08, -0.17, 0.27), (s * 0.08, -0.2, 0.4)], 0.012, 0.01)
        b.blob((s * 0.08, -0.2, 0.42), 0.022)
    return b, 0.007


def manta():
    b = Body()
    b.blob((0, 0, 0.5), 1.0, scale=(0.14, 0.3, 0.1))
    for s in (-1, 1):
        b.sheet((s * 0.45, 0.02, 0.5), 0.5, 0.28, tilt=("Y", s * 0.05))
        b.tube([(s * 0.1, -0.05, 0.5), (s * 0.5, -0.1, 0.52), (s * 0.95, 0.0, 0.5)], 0.03, 0.012)
    b.tube([(0, 0.3, 0.5), (0, 0.8, 0.46), (0, 1.3, 0.42)], 0.04, 0.008)
    b.blob((0, -0.34, 0.5), 1.0, scale=(0.07, 0.07, 0.06))
    return b, 0.008


def centipede():
    b = Body()
    ys = np.linspace(-0.7, 0.7, 12)
    b.tube([(0, y, 0.15) for y in ys], 0.07, 0.06)
    b.blob((0, -0.78, 0.16), 0.09)
    for y in ys[1:]:
        for s in (-1, 1):
            b.tube([(s * 0.05, y, 0.14), (s * 0.22, y, 0.1), (s * 0.3, y + 0.03, 0.01)], 0.017, 0.008)
    return b, 0.006


def hydra():
    b = Body()
    b.blob((0, 0.1, 0.5), 1.0, scale=(0.25, 0.35, 0.2))
    for k, (dx, h) in enumerate(((-0.32, 1.25), (-0.11, 1.45), (0.11, 1.4), (0.32, 1.2))):
        b.tube([(dx * 0.4, -0.15, 0.6), (dx * 0.9, -0.3, 0.8), (dx * 1.2, -0.3, h)], 0.06, 0.04)
        b.blob((dx * 1.25, -0.4, h + 0.02), 1.0, scale=(0.055, 0.09, 0.05))
    b.tube([(0, 0.4, 0.45), (0, 0.9, 0.3), (0, 1.3, 0.22)], 0.09, 0.015)
    for s in (-1, 1):
        b.tube([(s * 0.2, 0.0, 0.4), (s * 0.28, 0.0, 0.2), (s * 0.28, -0.05, 0.02)], 0.07, 0.04)
        b.tube([(s * 0.2, 0.3, 0.4), (s * 0.3, 0.3, 0.2), (s * 0.3, 0.26, 0.02)], 0.08, 0.04)
    return b, 0.009


def starfish():
    b = Body()
    b.blob((0, 0, 0.12), 1.0, scale=(0.14, 0.14, 0.08))
    for k in range(5):
        a = math.radians(90 + 72 * k)
        dx, dy = math.cos(a), math.sin(a)
        b.tube([(0.1 * dx, 0.1 * dy, 0.12), (0.4 * dx, 0.4 * dy, 0.1), (0.75 * dx, 0.75 * dy, 0.06)], 0.065, 0.015)
    return b, 0.007


HELDOUT = {"tripod": tripod, "crab": crab, "manta": manta, "centipede": centipede, "hydra": hydra, "starfish": starfish}


def main():
    name, out = sys.argv[sys.argv.index("--") + 1:][:2]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    body, voxel = HELDOUT[name]()
    print("CREATURE_OK", name, body.finish(name, out, voxel), "faces")


if __name__ == "__main__":
    main()
