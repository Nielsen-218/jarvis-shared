"""Procedural creatures for testing anatomy understanding (real Blender geometry, voxel-welded into one organic surface).

  blender -b --python hub/synth_creatures.py -- NAME OUT.glb        NAME in: dragon spider serpent bird

Z up, faces -Y, left = +X (the pipeline convention). These are DEVELOPMENT creatures: the algorithm may be tuned on
them. Creatures nobody has seen while developing live in synth_heldout.py (written after the algorithm was frozen)."""
import math
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix


class Body:
    def __init__(self):
        self.bm = bmesh.new()

    def blob(self, c, r, scale=(1, 1, 1), tilt=None, segs=14):
        ret = bmesh.ops.create_uvsphere(self.bm, u_segments=segs, v_segments=segs // 2 + 1, radius=1.0)
        v = ret["verts"]
        bmesh.ops.scale(self.bm, vec=tuple(r * s for s in scale), verts=v)
        if tilt:
            bmesh.ops.rotate(self.bm, cent=(0, 0, 0), matrix=Matrix.Rotation(tilt[1], 3, tilt[0]), verts=v)
        bmesh.ops.translate(self.bm, vec=tuple(c), verts=v)

    def tube(self, pts, r0, r1, segs=10):
        """Chain of overlapping spheres through pts (list of xyz), radius r0 -> r1."""
        pts = [np.array(p, float) for p in pts]
        L = sum(np.linalg.norm(b - a) for a, b in zip(pts[:-1], pts[1:]))
        n = max(3, int(L / (0.55 * min(r0, r1) + 0.45 * max(r0, r1)) / 0.9))
        acc = np.cumsum([0] + [np.linalg.norm(b - a) for a, b in zip(pts[:-1], pts[1:])])
        for t in np.linspace(0, 1, n):
            d = t * L
            i = min(int(np.searchsorted(acc, d, side="right")) - 1, len(pts) - 2)
            u = (d - acc[i]) / max(acc[i + 1] - acc[i], 1e-9)
            c = pts[i] * (1 - u) + pts[i + 1] * u
            self.blob(c, r0 + (r1 - r0) * t, segs=segs)

    def sheet(self, c, rx, ry, thick=0.011, tilt=None):
        self.blob(c, 1.0, scale=(rx, ry, thick), tilt=tilt, segs=18)

    def finish(self, name, out, voxel):
        me = bpy.data.meshes.new(name)
        self.bm.to_mesh(me)
        self.bm.free()
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        me.remesh_voxel_size = voxel
        bpy.ops.object.voxel_remesh()
        bpy.ops.object.shade_smooth()
        bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True)
        return len(me.polygons)


def dragon():
    b = Body()
    b.tube([(0, -0.35, 0.58), (0, 0.0, 0.57), (0, 0.38, 0.54)], 0.17, 0.15)                       # body
    b.tube([(0, -0.35, 0.6), (0, -0.52, 0.72), (0, -0.62, 0.9)], 0.085, 0.06)                     # neck
    b.blob((0, -0.72, 0.93), 1.0, scale=(0.065, 0.12, 0.055))                                       # head
    for s in (-1, 1):
        b.tube([(s * 0.04, -0.78, 0.97), (s * 0.08, -0.7, 1.08)], 0.016, 0.006)                    # horns
    b.tube([(0, 0.38, 0.52), (0, 0.8, 0.4), (0, 1.15, 0.33), (0, 1.5, 0.3)], 0.1, 0.012)           # tail
    for s in (-1, 1):
        for y, big in ((-0.2, 0.9), (0.22, 1.0)):                                                 # four legs
            b.tube([(s * 0.14, y, 0.5), (s * 0.2, y + 0.02, 0.3), (s * 0.19, y - 0.04, 0.12), (s * 0.19, y - 0.12, 0.025)], 0.06 * big, 0.03)
        shoulder = (s * 0.15, -0.12, 0.7)                                                          # wings
        b.tube([shoulder, (s * 0.55, -0.16, 1.1), (s * 1.05, -0.05, 1.18)], 0.03, 0.018)
        b.sheet((s * 0.58, 0.12, 1.02), 0.5, 0.3, tilt=("Y", s * 0.12))
        for k, ang in enumerate((-0.25, 0.15, 0.55)):
            b.tube([(s * 1.05, -0.05, 1.18), (s * (0.85 - 0.12 * k), 0.15 + 0.12 * k, 1.0), (s * (0.65 - 0.2 * k), 0.38 + 0.14 * k, 0.9)], 0.017, 0.01)
    return b, 0.011


def spider():
    b = Body()
    b.blob((0, -0.12, 0.3), 1.0, scale=(0.12, 0.14, 0.1))
    b.blob((0, 0.2, 0.34), 1.0, scale=(0.15, 0.2, 0.15))
    b.tube([(0, 0.0, 0.3), (0, 0.1, 0.32)], 0.06, 0.07)
    for s in (-1, 1):
        for y, dy in ((-0.2, -0.16), (-0.1, -0.06), (0.0, 0.05), (0.1, 0.15)):
            b.tube([(s * 0.1, y, 0.3), (s * 0.32, y + dy * 0.5, 0.52), (s * 0.58, y + dy * 1.0, 0.34), (s * 0.66, y + dy * 1.3, 0.02)], 0.027, 0.012)
    return b, 0.008


def serpent():
    b = Body()
    ys = np.linspace(-1.0, 1.0, 14)
    pts = [(0.14 * math.sin(5.5 * y), y, 0.07 + 0.01 * math.cos(3 * y)) for y in ys]
    b.tube(pts, 0.06, 0.012)
    b.blob((pts[0][0], -1.07, 0.09), 1.0, scale=(0.055, 0.09, 0.045))
    return b, 0.007


def bird():
    b = Body()
    b.blob((0, 0, 0.5), 1.0, scale=(0.12, 0.2, 0.12))
    b.tube([(0, -0.15, 0.55), (0, -0.2, 0.68)], 0.045, 0.035)
    b.blob((0, -0.22, 0.72), 0.05)
    b.tube([(0, -0.26, 0.72), (0, -0.36, 0.7)], 0.025, 0.004)
    for s in (-1, 1):
        b.tube([(s * 0.08, -0.02, 0.58), (s * 0.4, -0.04, 0.6), (s * 0.78, 0.02, 0.58)], 0.022, 0.012)
        b.sheet((s * 0.42, 0.1, 0.57), 0.4, 0.15)
        b.tube([(s * 0.04, 0.12, 0.42), (s * 0.05, 0.1, 0.22), (s * 0.05, 0.06, 0.02)], 0.018, 0.01)
    b.sheet((0, 0.4, 0.44), 0.1, 0.22, tilt=("X", -0.15))
    return b, 0.007


CREATURES = {"dragon": dragon, "spider": spider, "serpent": serpent, "bird": bird}


def main():
    name, out = sys.argv[sys.argv.index("--") + 1:][:2]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    body, voxel = CREATURES[name]()
    n = body.finish(name, out, voxel)
    print("CREATURE_OK", name, n, "faces")


if __name__ == "__main__":
    main()
