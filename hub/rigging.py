"""JARVIS autonomous rigging pipeline (runs INSIDE Blender, stdlib + numpy + bpy only).

mesh -> clean -> analyze/classify -> game-res decimation -> fitted skeleton -> skin (with fallback
chain) -> weight validation -> IK/FK controls -> measured pose tests -> export (+UE5 names).

Design notes (evidence in docs/rigging/):
  * Blender's ARMATURE_AUTO heat-diffusion is ~linear in face count: 740k faces took ~50 s
    for a 5-bone rig, 60k faces <1 s. So we skin a game-resolution mesh (or a proxy) and
    never the raw 740k-face generator output.
  * Heat weighting can fail silently (floaters, enclosed volumes, bones outside the mesh),
    so every skin is VALIDATED and repaired by a fallback chain; nothing is trusted blind.
  * Landmark fitting is heuristic (slice analysis). It is verified by the measured pose
    tests, never assumed correct.
Conventions: Blender Z-up, character faces -Y, character-left = +X.
"""
import json
import math
import time

import numpy as np

try:
    import bpy
    import bmesh
    from mathutils import Matrix, Vector
except ImportError:  # importable in a venv for pure-helper tests
    bpy = bmesh = Matrix = Vector = None

FINGERS = ("thumb", "index", "middle", "ring", "pinky")


# ------------------------------------------------------------------ mesh helpers
# ------------------------------------------------------------------ tunable parameters (the dojo's knobs)
from rig_params import PARAM_SPEC, validate_params  # noqa: E402  (the judge has no knobs; see rig_params.py)

PARAMS = {k: v[0] for k, v in PARAM_SPEC.items()}


def P(name):
    return PARAMS[name]


P_ = P      # alias for functions whose own argument is called P (the vertex array)


def set_params(d):
    """Apply validated overrides on top of the defaults; returns the previous params (for restore)."""
    err = validate_params(d or {})
    if err:
        raise ValueError(err)
    old = dict(PARAMS)
    PARAMS.clear()
    PARAMS.update({k: v[0] for k, v in PARAM_SPEC.items()})
    PARAMS.update(d or {})
    return old


def restore_params(old):
    PARAMS.clear()
    PARAMS.update(old)


def apply_overlay(source):
    """Install the functions defined in an (already guard-checked) overlay source into the engine module that owns them
    (rigging or anatomy); returns a restore() callable."""
    try:
        import anatomy as _A
        mods = [globals(), vars(_A)]
    except Exception:
        mods = [globals()]
    ns = {}
    for m in reversed(mods):
        ns.update(m)
    exec(compile(source, "<overlay>", "exec"), ns)
    saved = []
    for name, val in list(ns.items()):
        code = getattr(val, "__code__", None)
        if code is not None and code.co_filename == "<overlay>":
            target = next((m for m in mods[1:] if name in m), mods[0])
            saved.append((target, name, target.get(name)))
            target[name] = val

    def restore():
        for target, n, old in saved:
            if old is None:
                target.pop(n, None)
            else:
                target[n] = old
    return restore


VARIANTS = []        # learned strategies (dicts: id, params, overlay_source) tried after the default fit fails; set by the runner


def world_verts(obj, depsgraph=None):
    src = obj if depsgraph is None else obj.evaluated_get(depsgraph)
    me = src.data
    a = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", a)
    a = a.reshape(-1, 3).astype(np.float64)
    m = np.array(obj.matrix_world)
    return a @ m[:3, :3].T + m[:3, 3]


def mesh_edges(obj):
    me = obj.data
    e = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", e)
    return e.reshape(-1, 2)


def _components(bm):
    seen, comps = set(), []
    for v in bm.verts:
        if v.index in seen:
            continue
        stack, comp = [v], []
        seen.add(v.index)
        while stack:
            x = stack.pop()
            comp.append(x)
            for e in x.link_edges:
                o = e.other_vert(x)
                if o.index not in seen:
                    seen.add(o.index)
                    stack.append(o)
        comps.append(comp)
    return comps


def clean_mesh(obj, floater_fraction=0.01, merge_dist=1e-5):
    """Apply transforms, weld, drop floaters (<floater_fraction of verts), fix normals."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if obj.parent is not None:
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    n0 = len(obj.data.vertices)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_dist)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    comps = _components(bm)
    biggest = max(len(c) for c in comps)
    kill = [v for c in comps if len(c) < max(8, floater_fraction * biggest) for v in c]
    if kill:
        bmesh.ops.delete(bm, geom=kill, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return {"verts_before": n0, "verts_after": len(obj.data.vertices),
            "components_before": len(comps), "floaters_removed": sum(1 for c in comps if len(c) < max(8, floater_fraction * biggest))}


def decimate_copy(obj, target_faces, name):
    """Return a NEW object: collapse-decimated copy of obj (obj untouched)."""
    nfaces = len(obj.data.polygons)
    dup = obj.copy()
    dup.data = obj.data.copy()
    bpy.context.collection.objects.link(dup)
    if target_faces < nfaces:
        mod = dup.modifiers.new("jv_decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = max(0.001, target_faces / nfaces)
        dg = bpy.context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(dup.evaluated_get(dg))
        dup.modifiers.remove(mod)
        old = dup.data
        dup.data = me
        bpy.data.meshes.remove(old)
    dup.name = name
    dup.data.name = name
    dup.data.validate(verbose=False)
    dup.data.update()
    return dup


# ------------------------------------------------------------------ analysis
def _occupancy_components(xs, ys, grid=48):
    """Count 4-connected blobs of a 2D point cloud on a coarse grid."""
    if len(xs) == 0:
        return 0
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    gx = np.clip(((xs - x0) / max(x1 - x0, 1e-9) * (grid - 1)).astype(int), 0, grid - 1)
    gy = np.clip(((ys - y0) / max(y1 - y0, 1e-9) * (grid - 1)).astype(int), 0, grid - 1)
    occ = np.zeros((grid, grid), bool)
    occ[gx, gy] = True
    seen = np.zeros_like(occ)
    n = 0
    for i, j in zip(*np.nonzero(occ)):
        if seen[i, j]:
            continue
        n += 1
        st = [(i, j)]
        seen[i, j] = True
        while st:
            a, b = st.pop()
            for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p, q = a + da, b + db
                if 0 <= p < grid and 0 <= q < grid and occ[p, q] and not seen[p, q]:
                    seen[p, q] = True
                    st.append((p, q))
    return n


def analyze(obj):
    """Measure the mesh and classify its anatomy. Heuristic; returns evidence + confidence."""
    P = world_verts(obj)
    lo, hi = P.min(0), P.max(0)
    dim = hi - lo
    H = dim[2]
    sel = P[P[:, 2] < lo[2] + 0.22 * H]
    legs = _occupancy_components(sel[:, 0], sel[:, 1])
    horiz = max(dim[0], dim[1])
    elong = horiz / max(H, 1e-9)
    if elong > 1.25 and legs >= 3:
        kind, conf = "quadruped", 0.7
    elif elong > 2.5 and legs <= 2:
        kind, conf = "serpentine", 0.5
    elif legs == 2 or (legs == 1 and elong < 1.1):
        kind, conf = "humanoid", 0.8 if legs == 2 else 0.4
    else:
        kind, conf = "humanoid", 0.3
    return {"kind": kind, "confidence": conf, "leg_clusters": int(legs), "elongation": round(float(elong), 3),
            "dimensions": [round(float(d), 4) for d in dim], "bbox_min": [float(v) for v in lo],
            "bbox_max": [float(v) for v in hi], "verts": len(P), "faces": len(obj.data.polygons)}


def _slice_clusters(P, z0, z1, gap):
    sel = P[(P[:, 2] >= z0) & (P[:, 2] < z1)]
    if len(sel) == 0:
        return []
    xs = np.sort(sel[:, 0])
    cuts = np.nonzero(np.diff(xs) > gap)[0]
    edges = np.concatenate([[0], cuts + 1, [len(xs)]])
    return [(float(xs[a]), float(xs[b - 1])) for a, b in zip(edges[:-1], edges[1:])]


def humanoid_landmarks(P):
    """Slice-analysis fit of crotch, neck, hands, feet. Falls back to proportions."""
    lo, hi = P.min(0), P.max(0)
    z0, z1 = lo[2], hi[2]
    H = z1 - z0
    nb = 96
    zs = np.linspace(z0, z1, nb + 1)
    gap = 0.015 * H
    clusters = [_slice_clusters(P, zs[i], zs[i + 1], gap) for i in range(nb)]
    widths = np.array([(c[-1][1] - c[0][0]) if c else 0.0 for c in clusters])
    # crotch: lowest z above the feet where the band between the legs is filled for 3 slices
    # (arms/claws at torso height make cluster counting unreliable, so use the central void)
    cx0 = float(np.median(P[(P[:, 2] > z0 + 0.3 * H) & (P[:, 2] < z0 + 0.6 * H), 0]))
    band = 0.03 * H
    filled = np.array([bool(((P[:, 2] >= zs[i]) & (P[:, 2] < zs[i + 1]) & (np.abs(P[:, 0] - cx0) < band)).any()) for i in range(nb)])
    crotch_i = None
    for i in range(int(0.10 * nb), int(0.65 * nb)):
        if filled[i:int(0.6 * nb)].mean() >= 0.9:
            crotch_i = i
            break
    fitted = {}
    crotch_z = zs[crotch_i] if crotch_i is not None else z0 + 0.47 * H
    fitted["crotch"] = "central_void" if crotch_i is not None else "proportion"
    # plausibility: coats/skirts fill the gap between the legs, giving a crotch far too low.
    # Neck height is measured below; anatomy needs crotch >= ~45% of it (adult ~54%).
    _w = np.array([(c[-1][1] - c[0][0]) if c else 0.0 for c in clusters])
    _a = min(max(int(0.35 * nb), 1), nb - 2)
    neck_est = (zs[_a + int(np.argmin(_w[_a:int(0.92 * nb)]))] + zs[_a + int(np.argmin(_w[_a:int(0.92 * nb)])) + 1]) / 2
    if crotch_z - z0 < P_("crotch_plausible") * (neck_est - z0):
        crotch_z = z0 + P_("crotch_proportion") * (neck_est - z0)
        fitted["crotch"] = "anatomical_proportion(void implausible)"
    cx = float(np.median(P[(P[:, 2] > crotch_z) & (P[:, 2] < z0 + 0.7 * H), 0]))
    lcs, rcs = [], []
    for i in range(3, max(5, min(14, int(0.5 * (crotch_z - z0) / H * nb)))):
        cl = sorted(clusters[i], key=lambda c: c[1] - c[0], reverse=True)[:2]
        for c in cl:
            m = (c[0] + c[1]) / 2
            (lcs if m > cx else rcs).append(abs(m - cx))
    leg_off = float(np.mean(lcs + rcs)) if (lcs and rcs) else 0.11 * H
    leg_off = max(leg_off, 0.03 * H)
    a = int(((crotch_z - z0) / H + 0.30 * (1 - (crotch_z - z0) / H)) * nb)
    b = int(0.92 * nb)
    a = min(max(a, 1), nb - 2)
    neck_i = a + int(np.argmin(widths[a:max(b, a + 1)]))
    neck_z = (zs[neck_i] + zs[neck_i + 1]) / 2
    torso = P[(P[:, 2] > crotch_z) & (P[:, 2] < neck_z)]
    cy = float(np.median(torso[:, 1])) if len(torso) else float(np.median(P[:, 1]))
    tw = float(np.percentile(np.abs(torso[:, 0] - cx), 60)) if len(torso) else 0.12 * H
    arms, arm_fit = {}, {}
    for side, sgn in (("L", 1), ("R", -1)):
        lower = crotch_z - P_("arm_below_crotch") * H if P_("arm_floor_frac") is None else z0 + P_("arm_floor_frac") * H
        up = P[(P[:, 2] > lower) & (P[:, 2] < neck_z) & (sgn * (P[:, 0] - cx) > 0)]
        if len(up):
            ext = sgn * (up[:, 0] - cx)
            if ext.max() > P_("arm_ext_min") * tw:
                arms[side] = up[int(np.argmax(ext))]
                reg = up[(ext > P_("arm_reg_lo") * tw) & (ext < P_("arm_reg_hi") * ext.max())]
                if len(reg) > 30:  # arm axis by regression: z vs distance from the midline
                    k = np.polyfit(sgn * (reg[:, 0] - cx), reg[:, 2], 1)
                    yy = float(np.median(reg[:, 1]))
                    arms[side] = np.array([arms[side][0], yy, float(np.polyval(k, ext.max()))])
                    arm_fit[side] = (float(np.polyval(k, tw)), yy)
                continue
        arms[side] = None
    feet = {}
    for side, sgn in (("L", 1), ("R", -1)):
        f = P[(P[:, 2] < z0 + 0.1 * H) & (sgn * (P[:, 0] - cx) > 0)]
        feet[side] = float(f[:, 1].min()) if len(f) else cy - 0.12 * H
    return {"z0": float(z0), "z1": float(z1), "H": float(H), "cx": cx, "cy": cy, "crotch_z": float(crotch_z),
            "neck_z": float(neck_z), "torso_half": tw, "leg_off": float(leg_off), "arm_tips": arms, "arm_fit": arm_fit, "toe_y": feet,
            "fit_source": fitted}


# ------------------------------------------------------------------ skeletons
def humanoid_spec(L):
    """Bone list [(name, parent, head, tail, deform)] with fingers, wrist, toes. Blender .L/.R names."""
    H, cx, cy, z0 = L["H"], L["cx"], L["cy"], L["z0"]
    hip_z = L["crotch_z"] + 0.04 * H
    neck_z = L["neck_z"]
    S = []

    def add(name, parent, head, tail, deform=True):
        S.append((name, parent, tuple(float(v) for v in head), tuple(float(v) for v in tail), deform))

    V = lambda x, y, z: np.array([x, y, z], float)
    add("root", None, V(cx, cy, z0), V(cx, cy, z0 + 0.05 * H), False)
    add("pelvis", "root", V(cx, cy, hip_z - 0.04 * H), V(cx, cy, hip_z + 0.02 * H))
    sp = np.linspace(hip_z, neck_z, 4)
    prev = "pelvis"
    for i in range(3):
        add(f"spine_{i+1:02d}", prev, V(cx, cy, sp[i] if i else hip_z), V(cx, cy, sp[i + 1]))
        prev = f"spine_{i+1:02d}"
    head_top = L["z1"]
    nk = neck_z + 0.35 * (head_top - neck_z)
    add("neck", prev, V(cx, cy, neck_z), V(cx, cy, nk))
    add("head", "neck", V(cx, cy, nk), V(cx, cy, head_top))
    shoulder_z = neck_z - 0.12 * (neck_z - L["crotch_z"])
    for side, sgn in (("L", 1), ("R", -1)):
        tip = L["arm_tips"][side]
        if tip is None:  # no visible arm: tuck a proportional A-pose arm so the rig still exists
            tip = V(cx + sgn * (L["torso_half"] + 0.28 * H), cy, shoulder_z - 0.2 * H)
        sz, sy = L.get("arm_fit", {}).get(side, (shoulder_z, cy))
        S0 = V(cx + sgn * L["torso_half"] * 0.35, cy, sz)
        Sh = V(cx + sgn * L["torso_half"] * 0.95, sy, sz)
        d = tip - Sh
        el = Sh + P("elbow_frac") * d + V(0, 0.012 * H, 0)       # slight back-bend defines the IK plane
        wr = Sh + P("wrist_frac") * d
        add(f"clavicle.{side}", "spine_03", S0, Sh)
        add(f"upperarm.{side}", f"clavicle.{side}", Sh, el)
        add(f"forearm.{side}", f"upperarm.{side}", el, wr)
        add(f"hand.{side}", f"forearm.{side}", wr, wr + 0.35 * (tip - wr))
        dn = d / (np.linalg.norm(d) + 1e-9)
        side_v = np.cross(dn, V(0, 0, 1))
        side_v = side_v / (np.linalg.norm(side_v) + 1e-9)
        hl = np.linalg.norm(tip - wr)
        base = wr + 0.35 * (tip - wr)
        for fi, fname in enumerate(FINGERS):
            off = (fi - 2) * 0.16 * hl
            start = base + side_v * off + (V(0, -0.5, 0) * hl * 0.3 if fname == "thumb" else 0)
            seg = dn * hl * (0.22 if fname == "thumb" else 0.2)
            prev = f"hand.{side}"
            for k in range(3):
                nm = f"{fname}_{k+1:02d}.{side}"
                add(nm, prev, start + seg * k, start + seg * (k + 1))
                prev = nm
        hx = cx + sgn * L["leg_off"]
        hip = V(hx, cy, hip_z)
        ankle_z = z0 + 0.06 * H
        knee = V(hx, cy - 0.015 * H, (hip_z + ankle_z) / 2)
        ankle = V(hx, cy, ankle_z)
        toe_y = L["toe_y"][side]
        ball = V(hx, cy + 0.55 * (toe_y - cy), z0 + 0.025 * H)
        tipp = V(hx, toe_y, z0 + 0.012 * H)
        add(f"thigh.{side}", "pelvis", hip, knee)
        add(f"calf.{side}", f"thigh.{side}", knee, ankle)
        add(f"foot.{side}", f"calf.{side}", ankle, ball)
        add(f"toe.{side}", f"foot.{side}", ball, tipp)
    return S


def quadruped_spec(P):
    lo, hi = P.min(0), P.max(0)
    dim = hi - lo
    ax = 1 if dim[1] >= dim[0] else 0                    # long horizontal axis
    oth = 0 if ax == 1 else 1
    ctr = (lo + hi) / 2
    top_end = int(np.argmax([P[P[:, ax] > ctr[ax], 2].max() if (P[:, ax] > ctr[ax]).any() else 0,
                             P[P[:, ax] <= ctr[ax], 2].max()]))
    head_sgn = 1 if top_end == 0 else -1               # head toward the taller end
    Hh = dim[2]
    z0 = lo[2]
    belly = z0 + 0.55 * Hh
    back = z0 + 0.72 * Hh
    S = []

    def pt(a, o, z):
        v = [0.0, 0.0, z]
        v[ax], v[oth] = a, o
        return np.array(v)

    def add(name, parent, head, tail, deform=True):
        S.append((name, parent, tuple(float(x) for x in head), tuple(float(x) for x in tail), deform))

    L = dim[ax]
    c = ctr[ax]
    co = ctr[oth]
    add("root", None, pt(c, co, z0), pt(c, co, z0 + 0.05 * Hh), False)
    add("pelvis", "root", pt(c - head_sgn * 0.28 * L, co, back), pt(c - head_sgn * 0.2 * L, co, back))
    xs = [c - head_sgn * 0.2 * L, c - head_sgn * 0.05 * L, c + head_sgn * 0.1 * L, c + head_sgn * 0.25 * L]
    prev = "pelvis"
    for i in range(3):
        add(f"spine_{i+1:02d}", prev, pt(xs[i], co, back), pt(xs[i + 1], co, back))
        prev = f"spine_{i+1:02d}"
    add("neck", prev, pt(xs[3], co, back), pt(c + head_sgn * 0.36 * L, co, back + 0.05 * Hh))
    add("head", "neck", pt(c + head_sgn * 0.36 * L, co, back + 0.05 * Hh), pt(c + head_sgn * 0.5 * L, co, back + 0.02 * Hh))
    prev = "pelvis"
    for i in range(4):
        a0 = c - head_sgn * (0.28 + 0.1 * i) * L
        a1 = c - head_sgn * (0.28 + 0.1 * (i + 1)) * L
        add(f"tail_{i+1:02d}", prev, pt(a0, co, back), pt(a1, co, back - 0.04 * Hh * (i + 1)))
        prev = f"tail_{i+1:02d}"
    # leg positions measured from the footprint (4 groups: side x fore/hind), not guessed from bbox
    foot = P[P[:, 2] < z0 + 0.2 * Hh]
    along = (foot[:, ax] - c) * head_sgn
    leg_xy = {}
    for lname, sgn_o in (("L", 1), ("R", -1)):
        for pair, fsel in (("fore", along > np.median(along)), ("hind", along <= np.median(along))):
            m = ((foot[:, oth] - co) * sgn_o > 0) & fsel
            g = foot[m] if m.any() else foot
            leg_xy[(lname, pair)] = (float(g[:, ax].mean()), float(g[:, oth].mean()))
    for lname, sgn_o in (("L", 1), ("R", -1)):
        for pair, pos, parent in (("fore", 0.25, "spine_03"), ("hind", -0.25, "pelvis")):
            a, o = leg_xy[(lname, pair)]
            zs = np.linspace(belly, z0 + 0.03 * Hh, 4)
            names = ("shoulder", "upperleg", "lowerleg", "paw") if pair == "fore" else ("thigh", "shin", "metatarsal", "paw")
            prev = parent
            for i in range(3):
                nm = f"{pair}_{names[i]}.{lname}"
                knee_off = (0.02 * L) * (1 if pair == "hind" else -1) * head_sgn if i == 1 else 0
                add(nm, prev, pt(a, o, zs[i]), pt(a + (knee_off if i == 1 else 0), o, zs[i + 1]))
                prev = nm
            nm = f"{pair}_paw.{lname}"
            add(nm, prev, pt(a, o, zs[3]), pt(a + head_sgn * 0.04 * L, o, z0 + 0.005 * Hh))
    return S, {"axis": "xy"[ax], "head_sign": head_sgn}


def prune_unsupported(spec, P, H, radius=0.006, min_verts=25):
    """Drop finger/toe/tail-tip bones with no mesh around them (e.g. 3-claw hands), plus their children."""
    def supported(h, t):
        d = np.asarray(t) - np.asarray(h)
        tt = np.clip(((P - h) @ d) / max(d @ d, 1e-12), 0, 1)
        dist = np.linalg.norm(P - (np.asarray(h) + tt[:, None] * d), axis=1)
        return int((dist < radius * H).sum()) >= min_verts
    dead, out = set(), []
    for nm, parent, head, tail, deform in spec:
        optional = any(nm.startswith(f) for f in FINGERS) or nm.startswith("toe")
        if parent in dead or (optional and not supported(head, tail)):
            dead.add(nm)
            continue
        out.append((nm, parent, head, tail, deform))
    return out, sorted(dead)


def build_armature(spec, name="Rig"):
    bpy.ops.object.select_all(action="DESELECT")
    arm = bpy.data.armatures.new(name + "_Armature")
    rig = bpy.data.objects.new(name, arm)
    bpy.context.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.edit_bones
    made = {}
    for nm, parent, head, tail, deform in spec:
        b = eb.new(nm)
        b.head, b.tail = head, tail
        if (Vector(tail) - Vector(head)).length < 1e-5:
            b.tail = (head[0], head[1], head[2] + 1e-3)
        b.use_deform = deform
        made[nm] = b
    for nm, parent, head, tail, deform in spec:
        if parent:
            made[nm].parent = made[parent]
            if (made[nm].head - made[parent].tail).length < 1e-4:
                made[nm].use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.show_in_front = True
    return rig


# ------------------------------------------------------------------ skinning
def weight_matrix(mesh, rig):
    names = [b.name for b in rig.data.bones if b.use_deform]
    idx = {mesh.vertex_groups[g].index: g for g in range(len(mesh.vertex_groups))}
    col = {n: i for i, n in enumerate(names)}
    W = np.zeros((len(mesh.data.vertices), len(names)), np.float32)
    gname = {g.index: g.name for g in mesh.vertex_groups}
    for v in mesh.data.vertices:
        for g in v.groups:
            n = gname.get(g.group)
            if n in col:
                W[v.index, col[n]] = g.weight
    return W, names


def validate_skin(mesh, rig, min_vertex_fraction=0.0005):
    W, names = weight_matrix(mesh, rig)
    sums = W.sum(1)
    unweighted = int((sums < 1e-4).sum())
    per_bone = (W > 0.05).sum(0)
    empty = [n for n, c in zip(names, per_bone) if c < max(3, min_vertex_fraction * len(W))]
    return {"verts": int(len(W)), "unweighted_verts": unweighted, "unweighted_fraction": round(unweighted / max(len(W), 1), 6),
            "deform_bones": len(names), "empty_bones": empty,
            "ok": unweighted / max(len(W), 1) < 0.001 and len(empty) <= max(2, int(0.1 * len(names)))}


def _bone_segments(rig):
    M = np.array(rig.matrix_world)
    segs, names = [], []
    for b in rig.data.bones:
        if b.use_deform:
            h = M[:3, :3] @ np.array(b.head_local) + M[:3, 3]
            t = M[:3, :3] @ np.array(b.tail_local) + M[:3, 3]
            segs.append((h, t))
            names.append(b.name)
    return names, segs


def skin_nearest_segment(mesh, rig, sigma_frac=0.04, k=3):
    """Deterministic fallback: weights from distance to bone segments. Never fails."""
    P = world_verts(mesh)
    names, segs = _bone_segments(rig)
    H = np.ptp(P[:, 2])
    D = np.empty((len(P), len(names)), np.float32)
    for j, (h, t) in enumerate(segs):
        d = t - h
        tt = np.clip(((P - h) @ d) / max(d @ d, 1e-12), 0, 1)
        D[:, j] = np.linalg.norm(P - (h + tt[:, None] * d), axis=1)
    idx = np.argsort(D, 1)[:, :k]
    mesh.vertex_groups.clear()
    groups = {n: mesh.vertex_groups.new(name=n) for n in names}
    sig = sigma_frac * H
    for vi in range(len(P)):
        ds = D[vi, idx[vi]]
        w = np.exp(-((ds - ds.min()) ** 2) / (2 * sig * sig))
        w = w / w.sum()
        for j, wj in zip(idx[vi], w):
            if wj > 0.01:
                groups[names[j]].add([vi], float(wj), "REPLACE")


def _bind_ops(mesh, rig, kind):
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.parent_set(type=kind)


def transfer_weights(src, dst):
    """Copy skin weights src -> dst (nearest-face interpolated) and finish dst as skinned."""
    dst.vertex_groups.clear()
    for g in src.vertex_groups:
        dst.vertex_groups.new(name=g.name)
    mod = dst.modifiers.new("jv_xfer", "DATA_TRANSFER")
    mod.object = src
    mod.use_vert_data = True
    mod.data_types_verts = {"VGROUP_WEIGHTS"}
    mod.vert_mapping = "POLYINTERP_NEAREST"
    bpy.context.view_layer.objects.active = dst
    bpy.ops.object.modifier_apply(modifier=mod.name)


def clean_weights(mesh, limit=4):
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=limit)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode="BONE_DEFORM", lock_active=False)


def skin(mesh, rig, proxy_faces=None, log=None):
    """Skin with the fallback chain. Returns report incl. method used and timings."""
    log = log if log is not None else []
    proxy_faces = proxy_faces or PARAMS["proxy_faces"]
    skip = PARAMS["skin_skip"]
    for m in [m for m in mesh.modifiers if m.type == "ARMATURE"]:
        mesh.modifiers.remove(m)
    mesh.parent = None
    mesh.vertex_groups.clear()
    rep = {"attempts": []}

    def attempt(label, fn):
        t = time.time()
        err = None
        try:
            fn()
        except Exception as e:  # operator failures are RuntimeError; keep going down the chain
            err = str(e)[:200]
        v = validate_skin(mesh, rig) if err is None else {"ok": False}
        rep["attempts"].append({"method": label, "seconds": round(time.time() - t, 2), "error": err, "validation": v})
        return v.get("ok", False)

    ok = False if "direct" in skip else attempt("armature_auto_direct", lambda: _bind_ops(mesh, rig, "ARMATURE_AUTO"))
    if not ok and "proxy" not in skip:
        mesh.parent = None
        for m in [m for m in mesh.modifiers if m.type == "ARMATURE"]:
            mesh.modifiers.remove(m)

        def proxy_path():
            proxy = decimate_copy(mesh, proxy_faces, mesh.name + "_proxy")
            proxy.vertex_groups.clear()
            _bind_ops(proxy, rig, "ARMATURE_AUTO")
            transfer_weights(proxy, mesh)
            bpy.data.objects.remove(proxy)
            mesh.parent = rig
            mesh.parent_type = "OBJECT"
            mod = mesh.modifiers.new("Armature", "ARMATURE")
            mod.object = rig
        ok = attempt("armature_auto_via_proxy", proxy_path)
    if not ok and "weld" not in skip:
        for m in [m for m in mesh.modifiers if m.type == "ARMATURE"]:
            mesh.modifiers.remove(m)
        mesh.parent = None

        def weld_path():
            # disconnected/overlapping shells: union them with a voxel remesh, heat-weight the welded proxy,
            # then transfer to the real mesh (nearest-surface interpolation)
            H = float(np.ptp(world_verts(mesh)[:, 2]))
            proxy = decimate_copy(mesh, 10 ** 9, mesh.name + "_weld")
            proxy.data.remesh_voxel_size = H / PARAMS["weld_voxel_div"]
            bpy.context.view_layer.objects.active = proxy
            bpy.ops.object.voxel_remesh()
            proxy.vertex_groups.clear()
            _bind_ops(proxy, rig, "ARMATURE_AUTO")
            transfer_weights(proxy, mesh)
            bpy.data.objects.remove(proxy)
            mesh.parent = rig
            mod = mesh.modifiers.new("Armature", "ARMATURE")
            mod.object = rig
        ok = attempt("armature_auto_via_voxel_weld_proxy", weld_path)
    if not ok:
        for m in [m for m in mesh.modifiers if m.type == "ARMATURE"]:
            mesh.modifiers.remove(m)

        def nearest():
            skin_nearest_segment(mesh, rig)
            mesh.parent = rig
            mod = mesh.modifiers.new("Armature", "ARMATURE")
            mod.object = rig
        ok = attempt("nearest_segment_fallback", nearest)
    rep["final_method"] = rep["attempts"][-1]["method"]
    rep["ok"] = ok
    if ok:
        clean_weights(mesh)
        rep["final_validation"] = validate_skin(mesh, rig)
        rep["ok"] = rep["final_validation"]["ok"]
    return rep


# ------------------------------------------------------------------ IK / FK
def add_ik(rig, kind="humanoid"):
    """IK controls for arms/legs with an IK<->FK blend driven by a custom property."""
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    eb = rig.data.edit_bones
    plan = []
    if kind == "humanoid":
        for s in ("L", "R"):
            plan.append((f"hand.{s}", f"forearm.{s}", f"ik_hand.{s}", f"arm_{s}"))
            plan.append((f"foot.{s}", f"calf.{s}", f"ik_foot.{s}", f"leg_{s}"))
    else:
        for s in ("L", "R"):
            plan.append((f"fore_paw.{s}", f"fore_lowerleg.{s}", f"ik_fore_paw.{s}", f"fore_{s}"))
            plan.append((f"hind_paw.{s}", f"hind_metatarsal.{s}", f"ik_hind_paw.{s}", f"hind_{s}"))
    made = []
    for end_name, chain_end, ctl, prop in plan:
        if end_name not in eb:
            continue
        e = eb[end_name]
        b = eb.new(ctl)
        ln = max(e.length, 0.02)
        b.head = e.head.copy()
        b.tail = e.head + Vector((0, 0, ln))
        b.parent = eb["root"]
        b.use_deform = False
        made.append((ctl, chain_end, prop))
    bpy.ops.object.mode_set(mode="POSE")
    for ctl, chain_end, prop in made:
        pb = rig.pose.bones[chain_end]
        con = pb.constraints.new("IK")
        con.target, con.subtarget = rig, ctl
        con.chain_count = 2 if "paw" not in ctl else 3
        con.use_stretch = False
        rig[f"ik_{prop}"] = 1.0
        rig.id_properties_ui(f"ik_{prop}").update(min=0.0, max=1.0)
        drv = con.driver_add("influence").driver
        drv.type = "AVERAGE"
        var = drv.variables.new()
        var.name = "ik"
        var.targets[0].id = rig
        var.targets[0].data_path = f'["ik_{prop}"]'
    bpy.ops.object.mode_set(mode="OBJECT")
    return [c for c, _, _ in made]


# ------------------------------------------------------------------ pose tests
def _rest_pose(rig):
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)
    bpy.context.view_layer.update()


def _rot_world(rig, bone, axis, angle):
    pb = rig.pose.bones[bone]
    M = pb.bone.matrix_local.to_3x3()
    R = Matrix.Rotation(angle, 3, axis)
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = (M.inverted() @ R @ M).to_quaternion()


def _tail_world(rig, bone):
    return np.array(rig.matrix_world @ rig.pose.bones[bone].tail)


def _descendants(rig, bone):
    out, st = {bone}, [rig.data.bones[bone]]
    while st:
        for c in st.pop().children:
            out.add(c.name)
            st.append(c)
    return out


_AXES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def pose_test(mesh, rig, label, bones, prefer, angle_deg, W, wnames, H, edges, rest_P, rest_len, joint):
    """Rotate `bones` (each by angle) toward `prefer` direction; measure binding quality."""
    _rest_pose(rig)
    first = bones[0]
    base_tail = _tail_world(rig, bones[-1])
    best, best_score = None, -9
    for ax in (Vector(a) for a in _AXES):
        _rest_pose(rig)
        _rot_world(rig, first, ax, math.radians(angle_deg))
        bpy.context.view_layer.update()
        d = _tail_world(rig, bones[-1]) - base_tail
        score = float(np.dot(d, prefer) / (np.linalg.norm(d) + 1e-9)) if np.linalg.norm(d) > 1e-9 else -1
        if score > best_score:
            best, best_score = ax, score
    _rest_pose(rig)
    for bn in bones:
        # sequential bend of each joint in the chain about the chosen world axis
        _rot_world(rig, bn, best, math.radians(angle_deg))
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    P = world_verts(mesh, dg)
    disp = np.linalg.norm(P - rest_P, axis=1)
    moving = set()
    for bn in bones:
        moving |= _descendants(rig, bn)
    cols = [i for i, n in enumerate(wnames) if n in moving]
    dom = W.argmax(1)
    wsum = W.sum(1)
    in_sub = np.isin(dom, cols) & (wsum > 1e-4)
    # A spine twist legitimately blends the whole waist-to-hip band (belts, pouches, hems), so "far from the joint" is
    # wider there (0.2H, measured: the elf's blended band sat at 0.10-0.12H below the joint).
    far = np.linalg.norm(rest_P - np.array(joint), axis=1) > (0.2 if label == "spine_twist" else 0.12) * H
    stray_mask = (~in_sub) & far
    ln = np.linalg.norm(P[edges[:, 0]] - P[edges[:, 1]], axis=1)
    ratio = ln / np.maximum(rest_len, 1e-9)
    moved = float((disp[in_sub] > 0.0005 * H).mean()) if in_sub.any() else 0.0
    stray = float((disp[stray_mask] > 0.02 * H).mean()) if stray_mask.any() else 0.0
    p999, rmin = float(np.percentile(ratio, 99.9)), float(np.percentile(ratio, 0.1))
    p99 = float(np.percentile(ratio, 99))
    torn = float((ratio > 2.5).mean())              # share of edges stretched past 2.5x: a tear, not a seam
    res = {"test": label, "bones": bones, "angle_deg": angle_deg, "axis_chosen": [round(c) for c in best],
           "axis_alignment": round(best_score, 3), "subtree_verts": int(in_sub.sum()),
           "moved_fraction": round(moved, 3), "stray_fraction": round(stray, 4),
           "max_displacement_over_H": round(float(disp.max() / H), 3),
           "edge_stretch_p99.9": round(p999, 3), "edge_compress_p0.1": round(rmin, 3),
           "edge_stretch_p99": round(p99, 3), "torn_fraction": round(torn, 5)}
    # Tear rule, calibrated on the benchmark corpus (docs/rigging/ROADMAP.md): visually healthy rigs had <=0.11% of edges
    # stretched past 2.5x and p99 <= 1.35; the visibly torn Pink Confidence arms/hips had 0.21-0.53% and p99 up to 1.7.
    # The old tail rule (p99.9 < 2.2) flagged armpit seam noise on ordinary arms-down characters.
    res["pass"] = bool(in_sub.sum() >= 20 and moved >= 0.7 and stray <= 0.02 and torn <= 0.0015 and p99 < 1.6 and rmin > 0.25)
    sm = stray_mask & (disp > 0.02 * H)
    if sm.any():
        names, counts = np.unique(np.array(wnames)[dom[sm]], return_counts=True)
        top = sorted(zip(names.tolist(), counts.tolist()), key=lambda x: -x[1])[:3]
        res["stray_top_bones"] = top
        res["stray_centroid_over_H"] = [round(float(x), 2) for x in ((rest_P[sm].mean(0) - np.array(joint)) / H)]
    _rest_pose(rig)
    return res


def run_pose_tests(mesh, rig, kind="humanoid", skip=()):
    # FK only: IK constraints would override the FK rotations under test
    saved = {k: rig[k] for k in rig.keys() if k.startswith("ik_")}
    for k in saved:
        rig[k] = 0.0
    try:
        return _run_pose_tests(mesh, rig, kind, skip)
    finally:
        for k, v in saved.items():
            rig[k] = v
        _rest_pose(rig)


def _finger_chain(rig, s):
    for f in ("index", "middle", "ring", "thumb", "pinky"):
        ch = [f"{f}_{k:02d}.{s}" for k in (1, 2, 3)]
        if all(c in rig.pose.bones for c in ch):
            return ch
    return []


def _run_pose_tests(mesh, rig, kind, skip):
    W, wn = weight_matrix(mesh, rig)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    _rest_pose(rig)
    rest_P = world_verts(mesh, dg)
    H = float(np.ptp(rest_P[:, 2]))
    edges = mesh_edges(mesh)
    rest_len = np.linalg.norm(rest_P[edges[:, 0]] - rest_P[edges[:, 1]], axis=1)
    Z, mY, pY = np.array([0, 0, 1.0]), np.array([0, -1.0, 0]), np.array([0, 1.0, 0])
    tests = []
    if kind == "humanoid":
        for s in ("L", "R"):
            tests += [
                (f"shoulder.{s}", [f"upperarm.{s}"], Z, 55, f"upperarm.{s}"),
                (f"elbow.{s}", [f"forearm.{s}"], mY, 90, f"forearm.{s}"),
                (f"wrist.{s}", [f"hand.{s}"], Z, 45, f"hand.{s}"),
                (f"fingers.{s}", _finger_chain(rig, s), -Z, 40, (_finger_chain(rig, s) or ["hand." + s])[0]),
                (f"hip.{s}", [f"thigh.{s}"], mY, 50, f"thigh.{s}"),
                (f"knee.{s}", [f"calf.{s}"], pY, 80, f"calf.{s}"),
                (f"ankle.{s}", [f"foot.{s}"], Z, 30, f"foot.{s}")]
        tests += [("spine_twist", ["spine_02"], pY, 30, "spine_02"), ("neck_head", ["neck"], mY, 30, "neck")]
    else:
        for s in ("L", "R"):
            tests += [(f"fore_leg.{s}", [f"fore_upperleg.{s}"], mY, 40, f"fore_upperleg.{s}"),
                      (f"fore_knee.{s}", [f"fore_lowerleg.{s}"], pY, 50, f"fore_lowerleg.{s}"),
                      (f"hind_leg.{s}", [f"hind_thigh.{s}"], pY, 40, f"hind_thigh.{s}"),
                      (f"hind_hock.{s}", [f"hind_shin.{s}"], mY, 50, f"hind_shin.{s}")]
        tests += [("tail", ["tail_02"], Z, 40, "tail_02"), ("neck_head", ["neck"], Z, 25, "neck")]
    out = []
    for label, bones, prefer, ang, jb in tests:
        if label in skip or not bones or any(b not in rig.pose.bones for b in bones):
            continue
        joint = np.array(rig.matrix_world @ rig.data.bones[jb].head_local)
        out.append(pose_test(mesh, rig, label, bones, prefer, ang, W, wn, H, edges, rest_P, rest_len, joint))
    return out


def test_ik(rig, H, kind="humanoid"):
    """Move IK controls, verify the end effector follows; set FK and verify it does not."""
    res = []
    pairs = [("ik_hand.L", "hand.L"), ("ik_foot.L", "foot.L")] if kind == "humanoid" else [("ik_fore_paw.L", "fore_paw.L")]
    for ctl, bone in pairs:
        if ctl not in rig.pose.bones:
            continue
        prop = {"ik_hand.L": "ik_arm_L", "ik_foot.L": "ik_leg_L", "ik_fore_paw.L": "ik_fore_L"}[ctl]
        for mode in (1.0, 0.0):
            _rest_pose(rig)
            rig[prop] = mode
            bpy.context.view_layer.update()
            before = np.array(rig.matrix_world @ rig.pose.bones[bone].head)
            pb = rig.pose.bones[ctl]
            delta = Vector((0.0, -0.05 * H, 0.06 * H))
            pb.location = rig.pose.bones[ctl].bone.matrix_local.to_3x3().inverted() @ delta
            bpy.context.view_layer.update()
            after = np.array(rig.matrix_world @ rig.pose.bones[bone].head)
            target = before + np.array(delta)
            err = float(np.linalg.norm(after - target) / H)
            moved = float(np.linalg.norm(after - before) / H)
            ok = (err < 0.02) if mode == 1.0 else (moved < 0.005)
            res.append({"control": ctl, "ik_influence": mode, "end_effector_error_over_H": round(err, 4),
                        "end_effector_moved_over_H": round(moved, 4), "pass": bool(ok)})
        rig[prop] = 1.0
        _rest_pose(rig)
    return res


# ------------------------------------------------------------------ naming profiles / export
UE_BONE_MAP = {"pelvis": "pelvis", "spine_01": "spine_01", "spine_02": "spine_02", "spine_03": "spine_03",
               "neck": "neck_01", "head": "head", "root": "root"}


def ue_name(n):
    if n in UE_BONE_MAP:
        return UE_BONE_MAP[n]
    base, _, side = n.partition(".")
    suf = {"L": "_l", "R": "_r"}.get(side, "")
    base = {"upperarm": "upperarm", "forearm": "lowerarm", "hand": "hand", "calf": "calf", "foot": "foot",
            "toe": "ball", "thigh": "thigh", "clavicle": "clavicle"}.get(base, base)
    return base + suf


def apply_profile(rig, meshes, profile):
    if profile != "ue5":
        return {}
    mp = {}
    if "Armature" not in bpy.data.objects:     # FBX exporter adds an extra root bone unless the object is "Armature"
        rig.name = "Armature"
    for b in rig.data.bones:
        mp[b.name] = ue_name(b.name)
    for old, new in mp.items():
        if old != new:
            rig.data.bones[old].name = new
            for m in meshes:
                if old in m.vertex_groups:
                    m.vertex_groups[old].name = new
    return mp


def key_test_animation(rig, kind="humanoid"):
    """Author a short real action (arm swing + knee bend) for animation/export tests."""
    _rest_pose(rig)
    for k in [k for k in rig.keys() if k.startswith("ik_")]:
        rig[k] = 0.0      # FK animation: IK constraints would override the keyed rotations
    rig.animation_data_create()
    act = bpy.data.actions.new("JV_AnimTest")
    rig.animation_data.action = act
    for frame, ang in ((1, 0), (12, 45), (24, 0)):
        bpy.context.scene.frame_set(frame)
        for bn, axis in ((("upperarm.L", Vector((0, 1, 0))), ("thigh.R", Vector((1, 0, 0)))) if kind == "humanoid" else (("fore_upperleg.L", Vector((1, 0, 0))),)):
            if bn in rig.pose.bones:
                _rot_world(rig, bn, axis, math.radians(ang))
                rig.pose.bones[bn].keyframe_insert("rotation_quaternion", frame=frame)
    bpy.context.scene.frame_set(1)
    return act.name


def export_asset(objs, path_base, fmt):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    if fmt == "glb":
        bpy.ops.export_scene.gltf(filepath=path_base + ".glb", export_format="GLB", use_selection=True,
                                  export_skins=True, export_animations=True, export_apply=False)
        return path_base + ".glb"
    bpy.ops.export_scene.fbx(filepath=path_base + ".fbx", use_selection=True, object_types={"ARMATURE", "MESH"},
                             add_leaf_bones=False, bake_anim=True, mesh_smooth_type="OFF",
                             primary_bone_axis="Y", secondary_bone_axis="X", apply_unit_scale=True,
                             # Mark V, 2026-11-09: without these the FBX only REFERENCED its 3 textures (they live packed in the
                             # .blend/.glb), and Unreal imported a grey material. Embedded, the FBX carries them into the game.
                             path_mode="COPY", embed_textures=True)
    return path_base + ".fbx"


def scene_reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


# ------------------------------------------------------------------ one-call pipeline
def _auto_rig_once(mesh, kind=None, target_faces=40000, name=None, ik=True, log=None, overrides=None, max_faces=None):
    """clean -> analyze -> decimate -> skeleton -> skin -> validate -> IK. Returns (game_mesh, rig, report)."""
    t0 = time.time()
    rep = {"input": mesh.name}
    rep["clean"] = clean_mesh(mesh)
    rep["analysis"] = analyze(mesh)
    kind = kind or rep["analysis"]["kind"]
    kind = "quadruped" if kind == "quadruped" else "humanoid"
    rep["kind_used"] = kind
    P = world_verts(mesh)                      # fit + finger support are judged on the SOURCE mesh
    if kind == "humanoid":
        L = humanoid_landmarks(P)
        L.update(overrides or {})
        spec = humanoid_spec(L)
        spec, pruned = prune_unsupported(spec, P, L["H"], radius=PARAMS["prune_radius"], min_verts=PARAMS["prune_min_verts"])
        rep["pruned_bones"] = pruned
        rep["landmarks"] = json.loads(json.dumps(L, default=lambda o: np.asarray(o).round(4).tolist()))
        if any(n.split("_")[0] in FINGERS for n, *_ in spec):
            # fingers survive: keep enough density for them to take weight
            target_faces = max(target_faces, min(len(mesh.data.polygons), 150000))
            rep["finger_density_boost_faces"] = target_faces
    else:
        spec, info = quadruped_spec(P)
        rep["landmarks"] = info
    if max_faces and target_faces > max_faces:
        # A game's budget wins over the finger boost (Mark V, 2026-11-09: the boost kept all 106k faces of the Shadow World
        # main character, 70.7k vertices in Unreal against a 60k budget). The pose tests still judge the fingers.
        target_faces = int(max_faces)
        rep["game_budget_faces"] = target_faces
    game = decimate_copy(mesh, target_faces, (name or mesh.name) + "_game")
    rep["retopo"] = {"method": "collapse_decimate", "note": "decimation only, not true retopology", "faces_in": len(mesh.data.polygons), "faces_out": len(game.data.polygons)}
    rig = build_armature(spec, (name or mesh.name) + "_Rig")
    rep["bones"] = len(rig.data.bones)
    rep["skin"] = skin(game, rig)
    if ik and rep["skin"]["ok"]:
        rep["ik_controls"] = add_ik(rig, kind)
    rep["seconds"] = round(time.time() - t0, 2)
    return game, rig, rep


def _drop(*objs):
    for o in objs:
        if o is not None and o.name in bpy.data.objects:
            bpy.data.objects.remove(o)


def auto_rig(mesh, kind=None, target_faces=40000, name=None, ik=True, log=None, overrides=None, verify=True, max_faces=None):
    """Rig, then VERIFY with measured pose tests; on failure re-fit with safer landmark strategies and keep
    the best result. Returns (game_mesh, rig, report). report['verification'] lists every attempt."""
    strategies = [("fit", dict(overrides or {}), None)]
    if overrides:
        strategies.append(("auto_fit_ignoring_overrides", {}, None))
    for v in VARIANTS[:2]:                       # what JARVIS learned: only tried when the default fit did not fully pass
        strategies.append(("learned:" + str(v.get("id")), {}, v))
    strategies.append(("anatomical_proportions", {"__proportional": True}, None))
    best = None
    history = []
    for label, ov, variant in strategies:
        prop = ov.pop("__proportional", False)
        if prop:
            P = world_verts(mesh)
            lm = humanoid_landmarks(P)
            ov = {"crotch_z": lm["z0"] + 0.54 * (lm["neck_z"] - lm["z0"])}
        old_params, undo = None, None
        if variant:
            old_params = set_params(variant.get("params") or {})
            if variant.get("overlay_source"):
                undo = apply_overlay(variant["overlay_source"])
        try:
            game, rig, rep = _auto_rig_once(mesh, kind, target_faces, name, ik, log, ov, max_faces=max_faces)
        finally:
            if undo:
                undo()
            if old_params is not None:
                restore_params(old_params)
        poses = []
        if verify and rep["skin"]["ok"]:
            poses = run_pose_tests(game, rig, rep["kind_used"])
        passed = sum(p["pass"] for p in poses)
        entry = {"strategy": label, "skin_ok": rep["skin"]["ok"], "pose_pass": f"{passed}/{len(poses)}",
                 "failing": [p["test"] for p in poses if not p["pass"]]}
        history.append(entry)
        score = (rep["skin"]["ok"], passed / max(len(poses), 1) if poses else 0.0)
        rep["verification"] = history
        if best is None or score > best[0]:
            if best is not None:
                _drop(best[1], best[2])
            best = (score, game, rig, rep)
        else:
            _drop(game, rig)
        if not verify or (rep["skin"]["ok"] and poses and passed == len(poses)):
            break
    best[3]["verification"] = history
    best[3]["chosen_strategy"] = next(h["strategy"] for h in history if True) if len(history) == 1 else         max(history, key=lambda h: (h["skin_ok"], eval(h["pose_pass"].replace("/", "/max(1,") + ")")))["strategy"]
    return best[1], best[2], best[3]


# ------------------------------------------------------------------ Living Shadow World anatomy
_LSW_LIMBS = {
    "arm": {"root": "upperarm", "joints": ("shoulder", "elbow", "wrist"), "locomotion": "reduces carry/climb; two-handed actions blocked"},
    "leg": {"root": "thigh", "joints": ("hip", "knee", "ankle"), "locomotion": "limp/hop or crawl; one leg lost halves speed, both forces crawling"},
}


def anatomy_manifest(bone_names, kind="humanoid"):
    """Pure (no bpy): limb graph for the game's anatomy system: sever points, bone chains, consequences."""
    names = set(bone_names)
    limbs = []
    if kind == "humanoid":
        for side in ("L", "R"):
            for lname, spec in _LSW_LIMBS.items():
                root = f"{spec['root']}.{side}"
                if root not in names:
                    continue
                arm_parts = ("upperarm", "forearm", "hand", "clavicle_skip")
                leg_parts = ("thigh", "calf", "foot", "toe")
                chain = [n for n in bone_names if n.endswith("." + side) and (
                    (lname == "arm" and (n.split(".")[0] in arm_parts or n.split("_")[0] in FINGERS))
                    or (lname == "leg" and n.split(".")[0] in leg_parts))]
                limbs.append({"id": f"{lname}.{side}", "attach_bone": "clavicle." + side if lname == "arm" else "pelvis",
                              "bones": chain, "sever_joints": [{"joint": j, "bone": b} for j, b in zip(spec["joints"], chain[:3])],
                              "locomotion_consequence": spec["locomotion"], "independent_function_after_sever": True,
                              "regeneration": {"stump_bone": chain[0], "regrow_order": chain}})
    else:
        for side in ("L", "R"):
            for pair in ("fore", "hind"):
                chain = [n for n in bone_names if n.startswith(pair + "_") and n.endswith("." + side)]
                if chain:
                    limbs.append({"id": f"{pair}leg.{side}", "attach_bone": "spine_03" if pair == "fore" else "pelvis", "bones": chain,
                                  "sever_joints": [{"joint": f"joint_{i}", "bone": b} for i, b in enumerate(chain[:3])],
                                  "locomotion_consequence": "gait degrades per lost leg; two lost on one side forces drag/crawl",
                                  "independent_function_after_sever": True, "regeneration": {"stump_bone": chain[0], "regrow_order": chain}})
        tail = [n for n in bone_names if n.startswith("tail_")]
        if tail:
            limbs.append({"id": "tail", "attach_bone": "pelvis", "bones": tail, "sever_joints": [{"joint": "base", "bone": tail[0]}],
                          "locomotion_consequence": "balance loss at speed", "independent_function_after_sever": True,
                          "regeneration": {"stump_bone": tail[0], "regrow_order": tail}})
    return {"schema": "jarvis-lsw-anatomy/1", "kind": kind, "limbs": limbs,
            "vital_regions": [n for n in ("head", "neck", "spine_01", "spine_02", "spine_03", "pelvis") if n in names]}


def split_limb(mesh, rig, root_bone):
    """Sever: move every vertex dominated by root_bone's subtree into its own mesh with its own armature
    (so the limb can be posed/ragdolled independently), and cap the stump on the body. Real geometry op."""
    W, wn = weight_matrix(mesh, rig)
    sub = _descendants(rig, root_bone)
    cols = [i for i, n in enumerate(wn) if n in sub]
    take = np.isin(W.argmax(1), cols) & (W.sum(1) > 1e-4)
    if not take.any():
        return {"ok": False, "error": "no vertices belong to that limb"}
    sel = {int(i) for i in np.nonzero(take)[0]}
    bm = bmesh.new()
    bm.from_mesh(mesh.data)
    bm.verts.ensure_lookup_table()
    limb_faces = [f for f in bm.faces if all(v.index in sel for v in f.verts)]
    limb = mesh.copy()
    limb.data = mesh.data.copy()
    limb.name = mesh.name + "_sever_" + root_bone
    bpy.context.collection.objects.link(limb)
    lb = bmesh.new()
    lb.from_mesh(limb.data)
    lb.verts.ensure_lookup_table()
    keep = {v.index for f in limb_faces for v in f.verts}
    bmesh.ops.delete(lb, geom=[v for v in lb.verts if v.index not in keep], context="VERTS")
    lb.to_mesh(limb.data)
    lb.free()
    bmesh.ops.delete(bm, geom=limb_faces, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    open_edges = [e for e in bm.edges if e.is_boundary]
    filled = bmesh.ops.holes_fill(bm, edges=open_edges, sides=64) if open_edges else {"faces": []}
    bm.to_mesh(mesh.data)
    bm.free()
    mesh.data.update()
    limb_rig = rig.copy()
    limb_rig.data = rig.data.copy()
    limb_rig.name = rig.name + "_limb_" + root_bone
    bpy.context.collection.objects.link(limb_rig)
    bpy.context.view_layer.objects.active = limb_rig
    bpy.ops.object.mode_set(mode="EDIT")
    eb = limb_rig.data.edit_bones
    for b in [b for b in eb if b.name not in sub]:
        eb.remove(b)
    bpy.ops.object.mode_set(mode="OBJECT")
    limb.parent = limb_rig
    for m in [m for m in limb.modifiers if m.type == "ARMATURE"]:
        m.object = limb_rig
    return {"ok": True, "limb_object": limb.name, "limb_armature": limb_rig.name, "limb_verts": len(limb.data.vertices),
            "body_verts": len(mesh.data.vertices), "stump_cap_faces": len(filled.get("faces", [])), "limb_bones": sorted(sub)}
