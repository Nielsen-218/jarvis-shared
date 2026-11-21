"""Measure what a model switch really costs on this PC: cold load time, warm one-token time and resident VRAM for
each local tier model, at the context size the tiers use and at a smaller one.

    .venv\\Scripts\\python.exe scripts\\bench_models.py            # the three tier models, num_ctx 16384 and 8192

Talks to the local Ollama only; unloads and reloads models (the live Jarvis reloads them on demand). Prints a table
and writes the rows to the folder in JARVIS_BENCH_OUT (default: this repo's runs/bench-models.json)."""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import intelligence  # noqa: E402

URL = "http://127.0.0.1:11434"


def _post(path, body, timeout=300):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _ps():
    with urllib.request.urlopen(URL + "/api/ps", timeout=5) as r:
        return {m["name"]: m for m in json.loads(r.read()).get("models", [])}


def unload_all():
    for name in list(_ps()):
        _post("/api/generate", {"model": name, "keep_alive": 0}, 30)
    time.sleep(1.5)


def one(model, num_ctx):
    unload_all()
    body = {"model": model, "messages": [{"role": "user", "content": "Say ok."}], "stream": False,
            "options": {"num_ctx": num_ctx, "num_predict": 1}, "keep_alive": "2m"}
    t = time.monotonic()
    _post("/api/chat", body)
    cold = time.monotonic() - t
    t = time.monotonic()
    _post("/api/chat", body)
    warm = time.monotonic() - t
    vram = _ps().get(model, {}).get("size_vram", 0) / 2 ** 30
    return {"model": model, "num_ctx": num_ctx, "cold_s": round(cold, 1), "warm_s": round(warm, 2), "vram_gb": round(vram, 2)}


def main():
    models = [m for m in (intelligence.model("fast"), intelligence.model("advanced"), intelligence.vision_model()) if m]
    rows = []
    print(f"{'model':24} {'ctx':>6} {'cold s':>7} {'warm s':>7} {'VRAM GB':>8}")
    for model in dict.fromkeys(models):
        for ctx in (16384, 8192):
            row = one(model, ctx)
            rows.append(row)
            print(f"{row['model']:24} {row['num_ctx']:>6} {row['cold_s']:>7} {row['warm_s']:>7} {row['vram_gb']:>8}")
    out = Path(os.getenv("JARVIS_BENCH_OUT", Path(__file__).resolve().parent.parent / "runs" / "bench-models.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M"), "rows": rows}, indent=1), encoding="utf-8")
    print(f"written: {out}")
    unload_all()


if __name__ == "__main__":
    main()
