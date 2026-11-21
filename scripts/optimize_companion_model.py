r"""Turn a high-poly source GLB (e.g. the Meshy export) into companion-ready LODs.

    .venv\Scripts\python.exe scripts\optimize_companion_model.py source.glb --out app\companion\models\jarvis
        [--name jarvis] [--tris 40000 12000 5000] [--max-texture 2048] [--blender "C:\...\blender.exe"]

Drives Blender headless with scripts/blender/optimize_glb.py and writes
<out>/report.json with MEASURED triangle counts and file sizes — the numbers
to quote, rather than the design targets. Exit code is non-zero if Blender
fails or any LOD lands more than 15% above its target.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INNER = ROOT / "scripts" / "blender" / "optimize_glb.py"
DEFAULT_BLENDER = [
    os.environ.get("BLENDER", ""),
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
]


def find_blender(explicit: str | None = None) -> str | None:
    for c in [explicit or "", *DEFAULT_BLENDER, shutil.which("blender") or ""]:
        if c and Path(c).exists():
            return c
    return None


def optimize(src: Path, out_dir: Path, name: str = "model", tris=(40000, 12000, 5000),
             max_texture: int = 2048, blender: str | None = None, timeout: int = 1800) -> dict:
    exe = find_blender(blender)
    if not exe:
        raise FileNotFoundError("Blender not found; set BLENDER=<path to blender.exe>")
    if not src.exists():
        raise FileNotFoundError(src)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "--background", "--python", str(INNER), "--", "--in", str(src), "--out-dir", str(out_dir),
           "--name", name, "--tris", *map(str, tris), "--max-texture", str(max_texture)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("JSON:")), None)
    if proc.returncode != 0 or not line:
        raise RuntimeError(f"Blender exited {proc.returncode}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    report = json.loads(line[5:])
    report["blender"] = exe
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source")
    p.add_argument("--out", required=True)
    p.add_argument("--name", default="model")
    p.add_argument("--tris", type=int, nargs="+", default=[40000, 12000, 5000])
    p.add_argument("--max-texture", type=int, default=2048)
    p.add_argument("--blender")
    a = p.parse_args()
    try:
        r = optimize(Path(a.source), Path(a.out), a.name, a.tris, a.max_texture, a.blender)
    except Exception as e:  # noqa: BLE001 - CLI boundary
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"source: {r['source_triangles']:,} triangles in {r['source_meshes']} meshes")
    bad = 0
    for lod in r["lods"]:
        over = lod["triangles"] > lod["target_triangles"] * 1.15
        bad += over
        print(f"  lod{lod['level']}: {lod['triangles']:>8,} tris (target {lod['target_triangles']:,})  {lod['bytes'] / 1e6:5.1f} MB"
              f"  {'OVER TARGET' if over else 'ok'}" + (f"  morph meshes kept: {lod['undecimated_morph_meshes']}" if lod["undecimated_morph_meshes"] else ""))
    print(f"report: {Path(a.out) / 'report.json'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
