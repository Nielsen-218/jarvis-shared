"""Anatomy understanding and skeleton generation from GEOMETRY, with no body template.

Runs inside Blender (numpy only). For any mesh:
  voxelize -> solid + thickness field -> geodesic distances from the thickest core -> extremities (tips) found by
  successive geodesic extremes -> each tip traced back to the body, centred in the tissue -> a TREE of branches ->
  simplified into joints -> branches described from measurements (axial / lateral, round / flat, mirrored, drop) ->
  an anatomy plan (what parts, how many, which movements they suggest) -> a skeleton spec for rigging.rig builder.
The plan is DATA the dojo can question and improve; nothing here names a species. Humanoid, dragon, spider, serpent,
bird and creatures nobody modelled all go through the same path.

Tunables come from rigging.P (rig_params.py). Functions named anatomy_* are replaceable by JARVIS's own guarded
overlays; the judge functions (generic_pose_tests, run_motion_tests, ...) are protected."""
import math

import numpy as np

import rigging as R


# ------------------------------------------------------------------ voxels
def _dilate6(o):
    d = o.copy()
    d[1:] |= o[:-1]
    d[:-1] |= o[1:]
    d[:, 1:] |= o[:, :-1]
    d[:, :-1] |= o[:, 1:]
    d[:, :, 1:] |= o[:, :, :-1]
    d[:, :, :-1] |= o[:, :, 1:]
    return d


def _erode6(c):
    e = c.copy()
    e[1:] &= c[:-1]
    e[:-1] &= c[1:]
    e[:, 1:] &= c[:, :-1]
    e[:, :-1] &= c[:, 1:]
    e[:, :, 1:] &= c[:, :, :-1]
    e[:, :, :-1] &= c[:, :, 1:]
    e[0] = e[-1] = False
    e[:, 0] = e[:, -1] = False
    e[:, :, 0] = e[:, :, -1] = False
    return e


def anatomy_voxelize(P, tris, n=112):
    """Surface samples -> sealed shell -> filled solid. Returns dict(solid, origin, vs)."""
    lo, hi = P.min(0), P.max(0)
    vs = float((hi - lo).max() / n)
    pad = 3
    shape = tuple(int(math.ceil((hi[i] - lo[i]) / vs)) + 1 + 2 * pad for i in range(3))
    origin = lo - pad * vs
    A, B, C = P[tris[:, 0]], P[tris[:, 1]], P[tris[:, 2]]
    e = np.maximum(np.maximum(np.linalg.norm(B - A, axis=1), np.linalg.norm(C - B, axis=1)), np.linalg.norm(A - C, axis=1))
    m = np.clip(np.ceil(e / (vs * 0.7)).astype(int), 1, 40)
    occ = np.zeros(shape, bool)
    for mv in np.unique(m):
        sel = m == mv
        bary = np.array([(i / mv, j / mv, 1 - i / mv - j / mv) for i in range(mv + 1) for j in range(mv + 1 - i)])
        pts = (A[sel][:, None, :] * bary[None, :, 2:3] + B[sel][:, None, :] * bary[None, :, 0:1] + C[sel][:, None, :] * bary[None, :, 1:2]).reshape(-1, 3)
        ijk = np.clip(np.floor((pts - origin) / vs).astype(int), 0, np.array(shape) - 1)
        occ[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = True
    sealed = _dilate6(occ)
    outside = np.zeros(shape, bool)
    outside[0, 0, 0] = True
    n_prev = 0
    while True:
        outside = _dilate6(outside) & ~sealed
        cnt = int(outside.sum())
        if cnt == n_prev:
            break
        n_prev = cnt
    solid = ~outside
    return {"solid": solid, "origin": origin, "vs": vs}


def anatomy_thickness(solid, cap=96):
    """Erosion depth of every solid voxel (a robust thickness / medial field)."""
    D = np.zeros(solid.shape, np.int16)
    cur = solid.copy()
    k = 0
    while cur.any() and k < cap:
        k += 1
        D[cur] = k
        cur = _erode6(cur)
    return D


_OFFS = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) if (a, b, c) != (0, 0, 0)]


def anatomy_sheets(solid, D, thin_cut=2, min_voxels=150):
    """Membranes (wings, fins, fans): tissue that is thin everywhere locally AND wide in two directions.
    Returns a bool grid. Tubes (legs, tails) are thin too but wide in one direction only; the body shell is excluded
    because thick tissue lies right behind it."""
    Dm = D.copy()
    for _ in range(3):
        d = Dm.copy()
        for ax in range(3):
            d = np.maximum(d, np.roll(Dm, 1, ax))
            d = np.maximum(d, np.roll(Dm, -1, ax))
        Dm = d
    thin = solid & (Dm <= thin_cut)
    flat_thin = thin.ravel()
    shape = solid.shape
    sy, sz = shape[1] * shape[2], shape[2]
    offs = [a * sy + b * sz + c for a, b, c in _OFFS]
    seen = np.zeros(flat_thin.size, bool)
    sheet = np.zeros(flat_thin.size, bool)
    for start in np.flatnonzero(flat_thin):
        if seen[start]:
            continue
        comp = [np.array([start], np.int64)]
        seen[start] = True
        frontier = comp[0]
        while frontier.size:
            new = []
            for off in offs:
                nb = frontier + off
                ok = flat_thin[nb] & ~seen[nb]
                if ok.any():
                    nn = nb[ok]
                    seen[nn] = True
                    new.append(nn)
            frontier = np.concatenate(new) if new else np.empty(0, np.int64)
            if frontier.size:
                comp.append(frontier)
        ids = np.concatenate(comp)
        if ids.size < min_voxels:
            continue
        co = np.stack(np.unravel_index(ids, shape), 1).astype(float)
        w = np.sort(np.abs(np.linalg.eigvalsh(np.cov(co.T))))[::-1]
        s1, s2 = float(np.sqrt(w[0])), float(np.sqrt(w[1]))
        if s2 / max(s1, 1e-9) > 0.30 and s2 > 3.0:
            sheet[ids] = True
    return sheet.reshape(shape)


def anatomy_prune_empty(rig, mesh, names):
    """Remove bones that control no tissue (found by the skin check), re-parenting their children. Returns how many."""
    R.bpy.context.view_layer.objects.active = rig
    R.bpy.ops.object.mode_set(mode="EDIT")
    eb = rig.data.edit_bones
    n = 0
    for nm in names:
        b = eb.get(nm)
        if b is None or nm in ("root", "body_01"):
            continue
        for ch in list(b.children):
            ch.parent = b.parent
            ch.use_connect = False
        eb.remove(b)
        n += 1
    R.bpy.ops.object.mode_set(mode="OBJECT")
    for nm in names:
        g = mesh.vertex_groups.get(nm)
        if g is not None:
            mesh.vertex_groups.remove(g)
    R.clean_weights(mesh)
    return n


def anatomy_geodesic(solid, root_flat):
    """BFS (26-neighbour) from the root through solid voxels. Returns (g, parent) as flat int32 arrays."""
    shape = solid.shape
    S = solid.ravel()
    N = S.size
    sy, sz = shape[1] * shape[2], shape[2]
    offs = [a * sy + b * sz + c for a, b, c in _OFFS]
    g = np.full(N, -1, np.int32)
    par = np.full(N, -1, np.int32)
    g[root_flat] = 0
    frontier = np.array([root_flat], np.int64)
    d = 0
    while frontier.size:
        d += 1
        new = []
        for off in offs:
            nb = frontier + off
            ok = S[nb] & (g[nb] < 0)
            if ok.any():
                nn = nb[ok]
                g[nn] = d
                par[nn] = frontier[ok]
                new.append(nn)
        frontier = np.concatenate(new) if new else np.empty(0, np.int64)
    return g, par


def _paint(cov3, c, r):
    x0, y0, z0 = (max(0, int(v - r)) for v in c)
    x1, y1, z1 = (min(s, int(v + r) + 2) for v, s in zip(c, cov3.shape))
    gx, gy, gz = np.ogrid[x0:x1, y0:y1, z0:z1]
    cov3[x0:x1, y0:y1, z0:z1] |= ((gx - c[0]) ** 2 + (gy - c[1]) ** 2 + (gz - c[2]) ** 2) <= r * r


def anatomy_center_path(pts, solid, D, iters=3):
    """Pull each path point to the centre of the tissue cross-section (slab perpendicular to the path)."""
    pts = np.array(pts, float)
    K = len(pts)
    if K < 3:
        return pts
    for _ in range(iters):
        sm = pts.copy()
        for i in range(K):                                   # light smoothing so tangents are stable
            a, b = max(0, i - 2), min(K, i + 3)
            sm[i] = pts[a:b].mean(0)
        t = np.gradient(sm, axis=0)
        t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-9
        new = pts.copy()
        for i in range(K):
            c = pts[i]
            ci = np.clip(np.round(c).astype(int), 0, np.array(solid.shape) - 1)
            r = float(np.clip(1.8 * D[tuple(ci)] + 2, 3, 14))
            lo = np.maximum(np.floor(c - r).astype(int), 0)
            hi = np.minimum(np.ceil(c + r).astype(int) + 1, solid.shape)
            box = solid[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
            co = np.argwhere(box) + lo
            rel = co - c
            ok = (np.abs(rel @ t[i]) <= 1.0) & ((rel ** 2).sum(1) <= r * r)
            if ok.sum() >= 3:
                new[i] = co[ok].mean(0)
        pts = new
    out = pts.copy()
    for i in range(K):
        a, b = max(0, i - 2), min(K, i + 3)
        out[i] = pts[a:b].mean(0)
    out[0], out[-1] = pts[0], pts[-1]
    return out


def anatomy_extract_branches(solid, D, g, par, min_branch, max_branches):
    """Successive geodesic extremes. Returns list of {'path': (K,3) voxel coords root-side first, 'join': flat or -1}."""
    shape = solid.shape
    S = solid.ravel()
    cov = np.zeros(S.size, bool)
    cov3 = cov.reshape(shape)
    gmax = int(g.max())
    minlen = max(4, int(min_branch * gmax))
    out = []

    def unravel(flat):
        return np.array(np.unravel_index(flat, shape))

    for _ in range(max_branches * 12):
        if len(out) >= max_branches:
            break
        gg = np.where(S & ~cov, g, -1)
        tip = int(gg.argmax())
        if gg[tip] < 3:
            break
        path = [tip]
        cur = tip
        while par[cur] >= 0 and not cov[cur]:
            cur = int(par[cur])
            path.append(cur)
        joined = bool(cov[cur]) if len(out) else False
        pts = np.array([unravel(f) for f in path[::-1]], float)
        locD = float(np.mean([D[tuple(p.astype(int))] for p in pts])) if len(pts) else 1.0
        if len(out) and (len(path) < max(minlen, int(2.0 * locD))):
            _paint(cov3, unravel(tip), len(path) + 2)             # a bump, not a limb: dismiss the neighbourhood
            continue
        pts = anatomy_center_path(pts, solid, D, R.P("anat_center_iters"))
        for p in pts:
            ci = np.clip(np.round(p).astype(int), 0, np.array(shape) - 1)
            lo = np.maximum(ci - 3, 0)
            hi = np.minimum(ci + 4, shape)
            dm = float(D[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]].max())
            _paint(cov3, p, 1.2 * dm + 1.5)
        for f in path:                                            # the raw route and its tip are spent too: never find the same tip twice
            _paint(cov3, unravel(f), 2.5)
        _paint(cov3, unravel(tip), max(3.0, 0.25 * len(path)))
        out.append({"path": pts, "join": cur if joined else -1})
    return out


def anatomy_simplify(points, radii, tol_frac, seg_radii, keep, vs=0.0):
    """Dense centred polyline -> joint indices: Douglas-Peucker, then enough segments for the branch to bend."""
    K = len(points)
    keep = set(keep) | {0, K - 1}
    rad = float(np.median(radii)) if len(radii) else 1.0
    tol = max(tol_frac * rad, 1.6 * vs, 1e-6)          # never finer than the voxel grid can resolve
    idx = sorted(keep)
    stack = [(a, b) for a, b in zip(idx[:-1], idx[1:])]
    res = set(idx)
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = points[b] - points[a]
        L = np.linalg.norm(seg) + 1e-12
        d = np.linalg.norm(np.cross(points[a + 1:b] - points[a], seg / L), axis=1)
        j = int(d.argmax())
        if d[j] > tol:
            m = a + 1 + j
            res.add(m)
            stack += [(a, m), (m, b)]
    joints = sorted(res)
    length = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
    want = int(np.clip(round(length / (seg_radii * rad)), 1, 6))
    while len(joints) - 1 < want:                              # straight but long: still needs joints to articulate
        gaps = [(float(np.linalg.norm(points[b] - points[a])), a, b) for a, b in zip(joints[:-1], joints[1:]) if b - a >= 2]
        if not gaps:
            break
        _, a, b = max(gaps)
        joints = sorted(joints + [(a + b) // 2])
    merged = [joints[0]]
    for j in joints[1:]:                                       # drop joints too close to the previous (zero-length bones)
        if np.linalg.norm(points[j] - points[merged[-1]]) >= max(0.8 * rad, 3.0 * vs) or j == joints[-1] or j in keep:
            merged.append(j)
    if len(merged) < 2:
        merged = [0, K - 1]
    return merged


def _slab_flatness(pts, solid, D, ncheck=7):
    """Median (smallest / largest) spread of tissue across the branch: ~1 round tube, ~0 thin sheet."""
    out = []
    K = len(pts)
    for i in np.linspace(1, K - 2, min(ncheck, max(K - 2, 1))).astype(int):
        i = max(1, min(K - 2, i))
        c = pts[i]
        t = pts[min(K - 1, i + 1)] - pts[max(0, i - 1)]
        t /= np.linalg.norm(t) + 1e-9
        r = 10
        lo = np.maximum(np.floor(c - r).astype(int), 0)
        hi = np.minimum(np.ceil(c + r).astype(int) + 1, solid.shape)
        co = np.argwhere(solid[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]) + lo
        rel = co - c
        ok = (np.abs(rel @ t) <= 1.0) & ((rel ** 2).sum(1) <= r * r)
        if ok.sum() < 6:
            continue
        q = rel[ok] - (rel[ok] @ t)[:, None] * t
        w = np.linalg.eigvalsh(np.cov(q.T) + 1e-9 * np.eye(3))
        w = np.sort(np.abs(w))[::-1]
        out.append(float(np.sqrt(w[1] / max(w[0], 1e-9))))
    return float(np.median(out)) if out else 1.0


# ------------------------------------------------------------------ the tree and its description
def anatomy_build_tree(vox, branches, D, core):
    """Attach limbs to the body core (or to the branch they grow from), choose joints, return the tree in WORLD coordinates."""
    vs, origin = vox["vs"], vox["origin"]
    c, rc = core["c"], core["r"]
    nodes = []
    for bi, b in enumerate(branches):
        pts = b["path"].copy()
        inside = np.linalg.norm(pts - c, axis=1) <= rc
        att = ("core", 0)
        if inside[0] or bi == 0:
            first_out = int(np.argmax(~inside)) if (~inside).any() else len(pts) - 1
            pts = np.vstack([c, pts[max(first_out, 1):]])
        else:
            best = (1e18, None, None)
            for pj in range(len(nodes)):
                d = np.linalg.norm(nodes[pj]["dense"] - pts[0], axis=1)
                k = int(d.argmin())
                if d[k] < best[0]:
                    best = (float(d[k]), pj, k)
            _, pj, pk = best
            if pk / max(len(nodes[pj]["dense"]) - 1, 1) < 0.25 and nodes[pj]["attach"][0] == "core":
                pts = np.vstack([c, pts[1:]])               # grows from the first quarter of a limb root = grows from the body
            else:
                att = (pj, pk)
                pts[0] = nodes[pj]["dense"][pk]
        if len(pts) < 2:
            continue
        rad = np.array([max(D[tuple(np.clip(np.round(p).astype(int), 0, np.array(D.shape) - 1))], 1) for p in pts], float)
        nodes.append({"dense": pts, "radii": rad, "attach": att, "parent": None if att[0] == "core" else att[0]})
    keeps = {i: set() for i in range(len(nodes))}
    for n in nodes:
        if n["attach"][0] != "core":
            keeps[n["attach"][0]].add(n["attach"][1])
    for i, n in enumerate(nodes):
        j = anatomy_simplify(n["dense"], n["radii"], R.P("anat_simplify"), R.P("anat_seg_len"), keeps[i], 1.0)   # voxel units
        n["joints"] = j
        n["world"] = origin + (n["dense"][j] + 0.5) * vs
        n["jr"] = n["radii"][j] * vs
        n["length"] = float(np.linalg.norm(np.diff(origin + (n["dense"] + 0.5) * vs, axis=0), axis=1).sum())
    return nodes


def anatomy_symmetry(solid, vs, origin):
    """Which horizontal axis is the mirror axis and how good the mirror is (0..1)."""
    co = np.argwhere(solid)
    best = (0.0, 0)
    for ax in (0, 1):
        c = (co[:, ax].min() + co[:, ax].max()) / 2.0
        flipped = co.copy()
        flipped[:, ax] = np.round(2 * c - flipped[:, ax]).astype(int)
        ok = (flipped[:, ax] >= 0) & (flipped[:, ax] < solid.shape[ax])
        f = flipped[ok]
        score = float(solid[f[:, 0], f[:, 1], f[:, 2]].mean())
        if score > best[0]:
            best = (score, ax)
    ax = best[1]
    plane = float(origin[ax] + ((co[:, ax].min() + co[:, ax].max()) / 2.0 + 0.5) * vs)
    return {"axis": ax, "score": round(best[0], 3), "plane": plane}


def _sheet_share(pts, sheet, solid, r=7):
    """Share of the tissue around a branch that is membrane (0 = pure tube, ~1 = lies in a wing/fan)."""
    tot = mem = 0
    for p in pts[:: max(1, len(pts) // 12)]:
        lo = np.maximum(np.floor(p - r).astype(int), 0)
        hi = np.minimum(np.ceil(p + r).astype(int) + 1, solid.shape)
        sl = (slice(lo[0], hi[0]), slice(lo[1], hi[1]), slice(lo[2], hi[2]))
        tot += int(solid[sl].sum())
        mem += int(sheet[sl].sum())
    return mem / max(tot, 1)


def anatomy_refine_roles(roles, info, nodes):
    """EXTENSION POINT (replaceable by JARVIS's own guarded improvement). Called after the roles are classified.
    roles: {branch: role}; info: {branch: {length, radius, flat, sheet_share, lat, drop, tip_z, parent, attach_frac}};
    nodes: list of {length, ...}. Return a roles dict with the same keys; allowed values: axial leg arm wing digit appendage drop.
    Default: unchanged."""
    return roles


def anatomy_classify(nodes, vox, D, sym, L):
    """Describe each branch from measurements and name its role. Returns the anatomy plan (data)."""
    solid, vs, origin = vox["solid"], vox["vs"], vox["origin"]
    ax, plane = sym["axis"], sym["plane"]
    lat = lambda p: float(p[ax] - plane)
    info = {}
    for i, n in enumerate(nodes):
        w = n["world"]
        flat = _slab_flatness(n["dense"], solid, D)
        share = _sheet_share(n["dense"], vox["sheet"], solid)
        info[i] = ({"i": i, "tip": w[-1], "tip_z": float(w[-1][2]), "sheet_share": round(share, 3), "start": w[0], "length": n["length"], "radius": float(np.median(n["jr"])), "flat": round(flat, 3),
                     "lat": lat(w[-1]) / L, "drop": float((w[0][2] - w[-1][2]) / L), "parent": n["parent"],
                     "attach_frac": None})
    for k, n in enumerate(nodes):                              # where along the parent the branch grows
        if n["attach"][0] != "core":
            pj, pk = n["attach"]
            info[k]["attach_frac"] = round(pk / max(len(nodes[pj]["dense"]) - 1, 1), 3)
    zlo, zhi = float(origin[2]), float(origin[2] + solid.shape[2] * vs)
    zs = np.argwhere(solid.any(axis=(0, 1))).ravel()
    zmin_w, zmax_w = float(origin[2] + zs.min() * vs), float(origin[2] + (zs.max() + 1) * vs)
    ground = zmin_w + 0.12 * (zmax_w - zmin_w)
    roles = {}
    cworld = vox["core_world"]
    cands = [k for k, n in enumerate(nodes) if n["parent"] is None and abs(lat(n["world"][-1])) < 0.2 * L and n["length"] > 0.1 * L]
    axial_other = None
    if cands:
        a1 = max(cands, key=lambda k: nodes[k]["length"])
        roles[a1] = "axial"
        d1 = nodes[a1]["world"][-1] - cworld
        opp = [k for k in cands if k != a1 and float(d1 @ (nodes[k]["world"][-1] - cworld))
               / (np.linalg.norm(d1) * np.linalg.norm(nodes[k]["world"][-1] - cworld) + 1e-9) < -0.2]
        if opp:
            axial_other = max(opp, key=lambda k: nodes[k]["length"])
            roles[axial_other] = "axial"
    for k in range(len(nodes)):
        if k in roles:
            continue
        f = info[k]
        par = f["parent"]
        share, ground_hit = f["sheet_share"], f["tip_z"] <= ground
        if par is not None and roles.get(par) in ("leg", "arm", "wing", "appendage") and f["length"] < 0.7 * nodes[par]["length"] \
                and (f["attach_frac"] or 0) > 0.6:
            roles[k] = "digit"
        elif share > R.P("anat_wing_share") and f["length"] > 0.12 * L and not ground_hit:
            roles[k] = "wing"
        elif par is not None and roles.get(par) == "wing":
            roles[k] = "digit" if share > 0.05 else "appendage"
        elif ground_hit and f["length"] > 0.08 * L:
            roles[k] = "leg"                                    # a limb that carries the body: its tip reaches the ground
        elif share > 0.35 and f["length"] < 0.2 * L:
            roles[k] = "drop"                                   # an edge/corner of a membrane, not a limb
        elif f["length"] < 0.10 * L:
            roles[k] = "appendage"
        elif f["drop"] > 0.10:
            roles[k] = "leg"
        elif abs(f["lat"]) > 0.08 and f["length"] > 0.12 * L:
            roles[k] = "arm"
        else:
            roles[k] = "appendage"
    try:
        refined = anatomy_refine_roles(dict(roles), info, nodes)
        ok_roles = {"axial", "leg", "arm", "wing", "digit", "appendage", "drop"}
        if isinstance(refined, dict):
            for k, r in refined.items():
                if k in roles and r in ok_roles and (roles[k] == "axial") == (r == "axial"):    # the refine hook may not invent a spine
                    roles[k] = r
    except Exception:
        pass                                                      # a broken refinement can never break rigging
    # head / tail: of the two axial ends, the head is the one with the larger terminal bulk
    axial = [k for k, r in roles.items() if r == "axial"]
    ends = {}
    for k in axial:
        jr = nodes[k]["jr"]
        ends[k] = float(jr[-max(2, len(jr) // 4):].mean())
    head_k = max(ends, key=ends.get) if ends else None
    for k in axial:
        info[k]["role"] = "head_end" if (len(axial) > 1 and k == head_k) or len(axial) == 1 else "tail_end"
    for k, r in roles.items():
        if k not in axial:
            info[k]["role"] = r
    # mirror pairs (lateral symmetry), L = positive side of the lateral axis (pipeline: left = +X)
    side = {}
    pairs = []
    free = [k for k in info if info[k].get("role") in ("leg", "arm", "wing", "appendage", "digit")]
    used = set()
    for k in free:
        if k in used:
            continue
        mir = nodes[k]["world"][-1].copy()
        mir[ax] = 2 * plane - mir[ax]
        best, bd = None, 0.12 * L
        for m in free:
            if m == k or m in used or info[m]["role"] != info[k]["role"]:
                continue
            d = float(np.linalg.norm(nodes[m]["world"][-1] - mir))
            if d < bd:
                best, bd = m, d
        if best is not None:
            used |= {k, best}
            a, b = (k, best) if lat(nodes[k]["world"][-1]) >= 0 else (best, k)
            side[a], side[b] = "L", "R"
            pairs.append((a, b))
        elif abs(info[k]["lat"]) < 0.03:
            side[k] = "C"
    fwd = 1 if ax == 0 else 0
    ranked = sorted(pairs, key=lambda ab: float(nodes[ab[0]]["world"][-1][fwd] + nodes[ab[1]]["world"][-1][fwd]))
    pair_rank = {}
    for rnk, (a, b) in enumerate(ranked, 1):
        pair_rank[a] = pair_rank[b] = rnk
    counts = {}
    for k, f in info.items():
        r = f["role"]
        if r != "drop":
            counts[r] = counts.get(r, 0) + 1
    legs, wings = counts.get("leg", 0), counts.get("wing", 0)
    wing_parts = len({k for k in info if info[k]["role"] == "wing" and info[k]["parent"] is None})
    ax_len = sum(nodes[k]["length"] for k in axial)
    ax_rad = float(np.median(np.concatenate([nodes[k]["jr"] for k in axial]))) if axial else 1.0
    elong = ax_len / max(2 * ax_rad, 1e-9)
    modes = []
    if legs >= 2:
        modes.append("walk" if legs <= 4 else "crawl")
    if wing_parts >= 2:
        modes.append("fly_flap")
    if legs == 0 and wings == 0 and elong > 8:
        modes.append("slither")
    if counts.get("arm", 0):
        modes.append("reach")
    if not modes:
        modes.append("articulate")
    return {"roles": {k: info[k]["role"] for k in info}, "side": side, "pairs": pairs, "branches": [
        {k: (round(float(v), 3) if isinstance(v, (float, np.floating)) else v) for k, v in info[i].items() if k not in ("tip", "start")} for i in info],
        "counts": counts, "wing_parts": wing_parts, "legs": legs, "movement_modes": modes, "symmetry": {"axis": "xyz"[ax], "score": sym["score"]},
        "axial": axial, "pair_rank": pair_rank, "head_branch": head_k if axial else None, "elongation": round(elong, 1)}


def anatomy_build_spec(nodes, plan, vox, z_ground):
    """Bones from the tree: [(name, parent, head, tail, deform)] for rigging.build_armature."""
    roles, side = plan["roles"], plan["side"]
    spec = []
    sx = lambda p: tuple(float(v) for v in p)
    cw, crw = vox["core_world"], vox["core_radius_world"]
    spec.append(("root", None, (float(cw[0]), float(cw[1]), float(z_ground)), (float(cw[0]), float(cw[1]), float(z_ground) + 0.4 * crw), False))
    spec.append(("body_01", "root", sx(cw), sx(cw + np.array([0, 0, 0.8 * crw])), True))
    counters, names = {}, {}
    forward_axis = 1 if plan["symmetry"]["axis"] == "x" else 0

    def label(k):
        r = roles[k]
        s_ = side.get(k, "")
        base = {"head_end": "neck", "tail_end": "tail"}.get(r, r)
        if len(plan["axial"]) == 1 and r in ("head_end", "tail_end"):
            base = "spine"
        mp = max(plan["pair_rank"].values(), default=0)
        if k in plan["pair_rank"]:
            idx = plan["pair_rank"][k]                         # mirrored limbs share an index (front to back)
        else:
            counters[base] = counters.get(base, 0) + 1
            idx = mp + counters[base]                          # unpaired parts never collide with paired indices
        nm = f"{base}_{idx:02d}"
        names[k] = nm
        return nm, s_

    first_bone = {}
    last_bone_at = {}                                          # (branch, dense index) -> bone name ending at that joint
    order = sorted(range(len(nodes)), key=lambda k: (0 if nodes[k]["attach"][0] == "core" else 1 + k, k))
    for k in order:
        n = nodes[k]
        if roles[k] == "drop":
            continue
        nm, s = label(k)
        w, j = n["world"], n["joints"]
        parent_bone = "body_01"
        if n["attach"][0] != "core":
            pj, pk = n["attach"]
            parent_bone = last_bone_at.get((pj, pk)) or first_bone.get(pj, "body_01")   # a dropped parent falls back to the body
        prev = parent_bone
        for seg in range(len(w) - 1):
            bname = f"{nm}_{seg + 1:02d}" + (f".{s}" if s in ("L", "R") else "")
            spec.append((bname, prev, sx(w[seg]), sx(w[seg + 1]), True))
            if seg == 0:
                first_bone[k] = bname
            last_bone_at[(k, j[seg + 1])] = bname
            prev = bname
    return spec


def analyze_anatomy(mesh_obj, n=None):
    """Mesh object -> {'plan', 'spec', 'nodes', 'vox'}. The whole understanding pipeline."""
    n = n or R.P("anat_voxels")
    P = R.world_verts(mesh_obj)
    me = mesh_obj.data
    me.calc_loop_triangles()
    tris = np.array([t.vertices[:] for t in me.loop_triangles], int)
    vox = anatomy_voxelize(P, tris, n)
    D = anatomy_thickness(vox["solid"])
    vox["sheet"] = anatomy_sheets(vox["solid"], D, R.P("anat_sheet_cut"))
    root = int(np.argmax(np.where(vox["solid"].ravel(), D.ravel(), -1)))
    g, par = anatomy_geodesic(vox["solid"], root)
    branches = anatomy_extract_branches(vox["solid"], D, g, par, R.P("anat_min_branch"), R.P("anat_max_branches"))
    rc = 1.15 * float(D.ravel()[root])
    rv = np.array(np.unravel_index(root, vox["solid"].shape), float)
    box_lo = np.maximum(np.floor(rv - rc).astype(int), 0)
    box_hi = np.minimum(np.ceil(rv + rc).astype(int) + 1, vox["solid"].shape)
    co = np.argwhere(vox["solid"][box_lo[0]:box_hi[0], box_lo[1]:box_hi[1], box_lo[2]:box_hi[2]]) + box_lo
    co = co[np.linalg.norm(co - rv, axis=1) <= rc]
    cc = co.mean(0) if len(co) else rv
    core = {"c": cc, "r": rc}
    vox["core_world"] = vox["origin"] + (cc + 0.5) * vox["vs"]
    vox["core_radius_world"] = rc * vox["vs"]
    nodes = anatomy_build_tree(vox, branches, D, core)
    L = float(np.linalg.norm(P.max(0) - P.min(0)))
    sym = anatomy_symmetry(vox["solid"], vox["vs"], vox["origin"])
    plan = anatomy_classify(nodes, vox, D, sym, L)
    spec = anatomy_build_spec(nodes, plan, vox, float(P[:, 2].min()))
    plan["bones"] = len(spec)
    plan["voxel"] = {"n": n, "size": round(vox["vs"], 5), "solid": int(vox["solid"].sum()), "membrane_voxels": int(vox["sheet"].sum())}
    return {"plan": plan, "spec": spec, "nodes": nodes, "vox": vox}


# ------------------------------------------------------------------ the JUDGE for arbitrary skeletons (protected)
def generic_pose_tests(mesh, rig, min_subtree=20):
    """Bend EVERY joint of any skeleton and measure binding quality with the same rules as the humanoid judge."""
    from mathutils import Vector
    W, wn = R.weight_matrix(mesh, rig)
    R._rest_pose(rig)
    bpy_dg = R.bpy.context.evaluated_depsgraph_get()
    rest_P = R.world_verts(mesh, bpy_dg)
    H = float(np.ptp(rest_P[:, 2]))
    H = max(H, float(np.ptp(rest_P[:, 0])), float(np.ptp(rest_P[:, 1])))
    edges = R.mesh_edges(mesh)
    rest_len = np.linalg.norm(rest_P[edges[:, 0]] - rest_P[edges[:, 1]], axis=1)
    out = []
    saved = {k: rig[k] for k in rig.keys() if k.startswith("ik_")}
    for k in saved:
        rig[k] = 0.0
    try:
        for b in rig.data.bones:
            if not b.use_deform or b.parent is None or not b.parent.use_deform:
                continue
            sub = R._descendants(rig, b.name)
            cols = [i for i, nm in enumerate(wn) if nm in sub]
            if not cols or (W[:, cols].argmax(1) >= 0).sum() == 0:
                continue
            axis_b = np.array(b.tail_local - b.head_local)
            axis_b /= np.linalg.norm(axis_b) + 1e-9
            cand = [np.array(a, float) for a in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
            prefer = min(cand, key=lambda a: abs(float(a @ axis_b)))
            joint = np.array(rig.matrix_world @ b.head_local)
            out.append(R.pose_test(mesh, rig, b.name, [b.name], prefer, 40, W, wn, H, edges, rest_P, rest_len, joint))
    finally:
        for k, v in saved.items():
            rig[k] = v
        R._rest_pose(rig)
    return out


def _chains(rig):
    """{(stem, side): [bone names in order]} for bones named <stem>_<n>[.L|.R]."""
    groups = {}
    for b in rig.data.bones:
        nm = b.name
        if nm == "root":
            continue
        stem, _, sd = nm.partition(".")
        head, _, num = stem.rpartition("_")
        if head and num.isdigit():
            groups.setdefault((head, sd), []).append((int(num), nm))
    return {k: [n for _, n in sorted(v)] for k, v in groups.items()}


def _gait_assignments(plan, rig, mode, phase):
    """[(bone, world_axis, degrees)] for one phase (0..1) of a movement mode, derived from the plan's roles."""
    lat_ax = np.array([1.0, 0, 0]) if plan["symmetry"]["axis"] == "x" else np.array([0, 1.0, 0])
    fwd_ax = np.array([0, 1.0, 0]) if plan["symmetry"]["axis"] == "x" else np.array([1.0, 0, 0])
    up = np.array([0, 0, 1.0])
    ph = 2 * math.pi * phase
    groups = _chains(rig)
    axial = ("spine", "tail", "neck")
    legs = sorted(k for k in groups if k[0].startswith("leg"))
    wings = sorted(k for k in groups if k[0].startswith("wing"))
    out = []
    if mode in ("walk", "crawl"):
        for li, key in enumerate(legs):
            chain = groups[key]
            off = math.pi if li % 2 else 0.0                      # alternate legs in antiphase (tripod / diagonal / wave by count)
            out.append((chain[0], lat_ax, 25 * math.sin(ph + off)))
            if len(chain) > 1:
                out.append((chain[1], lat_ax, 30 * max(0.0, math.sin(ph + off + 1.2))))
    elif mode == "fly_flap":
        for key in wings:
            chain = groups[key]
            sgn = -1 if key[1] == "R" else 1
            out.append((chain[0], fwd_ax, sgn * 40 * math.sin(ph)))
            if len(chain) > 1:
                out.append((chain[1], fwd_ax, sgn * 25 * math.sin(ph - 0.8)))
    elif mode == "slither":
        for key in sorted(k for k in groups if k[0].startswith(axial)):
            for ci, n in enumerate(groups[key]):
                out.append((n, up, 14 * math.sin(ph - 0.7 * ci)))
    else:                                                      # reach / articulate: swing every free branch root
        for key in sorted(groups):
            if not key[0].startswith(axial):
                out.append((groups[key][0], lat_ax, 30 * math.sin(ph)))
    return out


def run_motion_tests(mesh, rig, plan, frames=(0.25, 0.75), amplitude=1.0):
    """Pose the rig at the extremes of every movement mode the plan implies; measure deformation. Judge: protected."""
    from mathutils import Vector
    results = []
    W, wn = R.weight_matrix(mesh, rig)
    R._rest_pose(rig)
    dg = R.bpy.context.evaluated_depsgraph_get()
    rest_P = R.world_verts(mesh, dg)
    size = max(float(np.ptp(rest_P[:, i])) for i in range(3))
    edges = R.mesh_edges(mesh)
    rest_len = np.linalg.norm(rest_P[edges[:, 0]] - rest_P[edges[:, 1]], axis=1)
    saved = {k: rig[k] for k in rig.keys() if k.startswith("ik_")}
    for k in saved:
        rig[k] = 0.0
    try:
        for mode in plan["movement_modes"]:
            worst = {"torn": 0.0, "p99": 1.0, "rmin": 9.0, "disp": 0.0, "bones": 0}
            for ph in frames:
                R._rest_pose(rig)
                assign = _gait_assignments(plan, rig, mode, ph)
                for bone, axis, deg in assign:
                    if bone in rig.pose.bones:
                        R._rot_world(rig, bone, Vector(tuple(float(x) for x in axis)), math.radians(deg * amplitude))
                R.bpy.context.view_layer.update()
                P = R.world_verts(mesh, R.bpy.context.evaluated_depsgraph_get())
                ratio = np.linalg.norm(P[edges[:, 0]] - P[edges[:, 1]], axis=1) / np.maximum(rest_len, 1e-9)
                worst["torn"] = max(worst["torn"], float((ratio > 2.5).mean()))
                worst["p99"] = max(worst["p99"], float(np.percentile(ratio, 99)))
                worst["rmin"] = min(worst["rmin"], float(np.percentile(ratio, 0.1)))
                worst["disp"] = max(worst["disp"], float(np.linalg.norm(P - rest_P, axis=1).max() / size))
                worst["bones"] = max(worst["bones"], len(assign))
            ok = worst["bones"] > 0 and worst["disp"] >= 0.03 and worst["torn"] <= 0.0015 and worst["p99"] < 1.6 and worst["rmin"] > 0.25
            results.append({"test": f"motion:{mode}", "mode": mode, "pass": bool(ok), "bones_driven": worst["bones"],
                            "moved_fraction": 1.0 if worst["disp"] >= 0.03 else 0.0, "stray_fraction": 0.0,
                            "torn_fraction": round(worst["torn"], 5), "edge_stretch_p99": round(worst["p99"], 3),
                            "edge_compress_p0.1": round(worst["rmin"], 3), "max_displacement_over_size": round(worst["disp"], 3)})
    finally:
        for k, v in saved.items():
            rig[k] = v
        R._rest_pose(rig)
    return results


def key_motion_clip(rig, plan, mode, frames=24):
    """A real keyframed action for the mode (used for export and the Unreal check)."""
    from mathutils import Vector
    R._rest_pose(rig)
    rig.animation_data_create()
    act = R.bpy.data.actions.new(f"JV_{mode}")
    rig.animation_data.action = act
    for f in (1, 7, 13, 19, 25):
        ph = (f - 1) / 24.0
        R._rest_pose(rig)
        assign = _gait_assignments(plan, rig, mode, ph)
        for bone, axis, deg in assign:
            if bone in rig.pose.bones:
                R._rot_world(rig, bone, Vector(tuple(float(x) for x in axis)), math.radians(deg))
                rig.pose.bones[bone].keyframe_insert("rotation_quaternion", frame=f)
    R.bpy.context.scene.frame_set(1)
    return act.name
