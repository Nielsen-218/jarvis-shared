"""Run the rigging benchmark corpus and write a scorecard; exit 1 when a case that passed before now fails.

  python scripts/rig_corpus.py [--only name,name] [--split dev|heldout] [--unreal] [--out data/rigging/scorecards]

Real Blender (and with --unreal real headless Unreal) runs; nothing is mocked. The scorecard also lists the OBSERVED
distribution of the measured pose metrics, which is the evidence for any threshold change (never a hand-picked number)."""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JARVIS_BLENDER_WORKSPACE", str(ROOT / "data" / "blender-workspace"))
os.environ["JARVIS_MODEL_ROOTS"] = os.pathsep.join([str(Path.home() / "Downloads"), str(ROOT / "docs")])


def resolve(p):
    return Path(p.replace("@", str(ROOT), 1) if p.startswith("@") else os.path.expanduser(p))


def metrics(report):
    out = {"moved": [], "stray": [], "stretch": [], "compress": []}
    out["rows"] = [(t["test"], t["pass"], t["moved_fraction"], t["stray_fraction"], t["edge_stretch_p99.9"], t.get("edge_stretch_p99"), t.get("torn_fraction"), t["edge_compress_p0.1"]) for t in report.get("pose_tests", [])]
    for t in report.get("pose_tests", []):
        out["moved"].append(t["moved_fraction"]); out["stray"].append(t["stray_fraction"])
        out["stretch"].append(t["edge_stretch_p99.9"]); out["compress"].append(t["edge_compress_p0.1"])
    return out


def dist(vals):
    if not vals:
        return None
    v = sorted(vals)
    q = lambda f: v[min(len(v) - 1, int(f * len(v)))]
    return {"n": len(v), "min": v[0], "p5": q(0.05), "median": statistics.median(v), "p95": q(0.95), "max": v[-1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only"); ap.add_argument("--split"); ap.add_argument("--retries", action="store_true", help="use the full retry loop instead of one attempt"); ap.add_argument("--unreal", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "data" / "rigging" / "scorecards"))
    a = ap.parse_args()
    if not a.unreal:
        os.environ["JARVIS_SKIP_UNREAL"] = "1"
    from tools import rigging
    corpus = json.loads((ROOT / "docs" / "rigging" / "corpus.json").read_text(encoding="utf-8"))["cases"]
    if a.only:
        corpus = [c for c in corpus if c["name"] in a.only.split(",")]
    if a.split:
        corpus = [c for c in corpus if c["split"] == a.split]
    out_dir = Path(a.out); out_dir.mkdir(parents=True, exist_ok=True)
    prev_file = out_dir / "latest.json"
    prev = json.loads(prev_file.read_text(encoding="utf-8")) if prev_file.is_file() else {"cases": {}}
    rows, pooled = {}, {"moved": [], "stray": [], "stretch": [], "compress": []}
    for c in corpus:
        p = resolve(c["path"])
        row = {"split": c["split"], "type": c["type"], "path": str(p)}
        if not p.is_file():
            row.update(ok=None, status="missing")
        else:
            t = time.time()
            r = (rigging._rig if a.retries else rigging._rig_once)(str(p), c.get("kind", ""), "ue5", 40000)
            row["seconds"] = round(time.time() - t, 1)
            if isinstance(r, str):
                row.update(ok=False, status="error", error=r[:300])
            else:
                rep = json.loads((Path(r["output_folder"]) / "report.json").read_text(encoding="utf-8"))
                m = metrics(rep)
                for k in pooled:
                    pooled[k] += m[k]
                row["tests"] = m["rows"]
                row.update(attempts=r.get("attempts"), ok=bool(r["ok"]), status="pass" if r["ok"] else "fail", kind=r["kind"], bones=r["bones"], stages=r["stages"],
                           failing=r["failing_pose_tests"], skin_method=r["skin_method"], folder=r["output_folder"],
                           pruned_bones=len(rep["pipeline"].get("pruned_bones") or []), unreal=(r.get("unreal_gate") or {}).get("ok"))
        rows[c["name"]] = row
        print(f"{c['name']:22s} {c['split']:8s} {row['status']:8s} {row.get('seconds', '')}", flush=True)
    done = [r for r in rows.values() if r["ok"] is not None]
    summ = {s: {"cases": sum(1 for r in done if r["split"] == s), "passed": sum(1 for r in done if r["split"] == s and r["ok"])} for s in ("dev", "heldout")}
    regress = [n for n, r in rows.items() if r["ok"] is False and (prev["cases"].get(n) or {}).get("ok") is True]
    card = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "unreal": a.unreal, "summary": summ, "regressions": regress,
            "observed_metric_distribution": {k: dist(v) for k, v in pooled.items()}, "cases": rows}
    (out_dir / f"scorecard-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    if not a.only and not a.split:                            # only a full run replaces the baseline
        merged = dict(prev["cases"]); merged.update(rows)
        prev_file.write_text(json.dumps({**card, "cases": merged}, indent=2), encoding="utf-8")
    print(json.dumps({"summary": summ, "regressions": regress}, indent=2))
    return 1 if regress else 0


if __name__ == "__main__":
    sys.exit(main())
