"""Dev probe for the companion GLB: point-sampled orthographic views with a metric grid, so joint
and feature positions can be read off the actual model (python = the blender-avengers Hunyuan venv)."""
import sys, numpy as np, trimesh
from PIL import Image, ImageDraw

def load(path):
    sc = trimesh.load(path, force="scene")
    m = trimesh.util.concatenate(list(sc.geometry.values()))
    return m

def sample_colors(m, n=900000, seed=0):
    rng = np.random.default_rng(seed)
    pts, fi = trimesh.sample.sample_surface(m, n, seed=seed)
    tri = m.triangles[fi]
    bary = trimesh.triangles.points_to_barycentric(tri, pts)
    uv = (m.visual.uv[m.faces[fi]] * bary[..., None]).sum(1)
    tex = np.asarray(m.visual.material.baseColorTexture.convert("RGB"))
    h, w = tex.shape[:2]
    px = np.clip((uv[:, 0] * w).astype(int), 0, w - 1); py = np.clip(((1 - uv[:, 1]) * h).astype(int), 0, h - 1)
    return pts, tex[py, px], m.face_normals[fi]

def view(pts, col, axis_u, axis_v, depth_axis, depth_sign, H=900, lo=None, hi=None, grid=0.02, title=""):
    lo = pts.min(0) if lo is None else lo; hi = pts.max(0) if hi is None else hi
    sc = H / (hi[axis_v] - lo[axis_v]); W = int((hi[axis_u] - lo[axis_u]) * sc) + 1
    img = np.full((H, W, 3), 60, np.uint8); zb = np.full((H, W), -1e9)
    u = ((pts[:, axis_u] - lo[axis_u]) * sc).astype(int).clip(0, W - 1); v = (H - 1 - (pts[:, axis_v] - lo[axis_v]) * sc).astype(int).clip(0, H - 1)
    z = pts[:, depth_axis] * depth_sign
    order = np.argsort(z)
    img[v[order], u[order]] = col[order]
    im = Image.fromarray(img); d = ImageDraw.Draw(im)
    g = lo[axis_v]
    k = 0
    while g <= hi[axis_v] + 1e-9:
        y = H - 1 - (g - lo[axis_v]) * sc; d.line([(0, y), (W, y)], fill=(255, 255, 0) if k % 5 == 0 else (90, 90, 40), width=1)
        if k % 5 == 0: d.text((2, y - 11), f"{g:.2f}", fill=(255, 255, 0))
        g += grid; k += 1
    g = np.ceil(lo[axis_u] / grid) * grid
    while g <= hi[axis_u]:
        x = (g - lo[axis_u]) * sc; kk = int(round(g / grid)); d.line([(x, 0), (x, H)], fill=(0, 255, 255) if kk % 5 == 0 else (30, 80, 80), width=1)
        if kk % 5 == 0: d.text((x + 2, 2), f"{g:.2f}", fill=(0, 255, 255))
        g += grid
    d.text((W // 2 - 30, H - 14), title, fill=(255, 255, 255))
    return im

if __name__ == "__main__":
    path, out = sys.argv[1], sys.argv[2]
    m = load(path)
    lo, hi = m.bounds
    pts, col, _ = sample_colors(m)
    print("bounds", lo.round(4), hi.round(4))
    front = view(pts, col, 0, 1, 2, 1, title="FRONT (x right, +z toward viewer)")
    side = view(pts, col, 2, 1, 0, 1, title="SIDE (z right)")
    W = front.width + side.width; sheet = Image.new("RGB", (W, front.height)); sheet.paste(front, (0, 0)); sheet.paste(side, (front.width, 0))
    sheet.save(out)
