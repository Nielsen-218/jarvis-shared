"""Bounded, resumable model benchmark (competitive elimination). Local only: no paid API, no model deletion, no merge.

  python scripts/model_bench.py spec                      write + hash the pre-registered spec (bench/spec.json)
  python scripts/model_bench.py validate                  the teacher reference must pass the judge in THIS harness first
  python scripts/model_bench.py blender MODEL [--stage A|B]
  python scripts/model_bench.py summary

Every model gets the same prompt, context, sampling, token cap, seeds and judge. Results are appended to
data/bench/<spec-hash>/blender.jsonl (one row per attempt) so a run can stop and resume. A game process, the file data/bench/STOP,
a per-attempt timeout and a per-model wall-clock cap all stop the run. Elimination rule (spec): zero complete passes in Stage A."""
import argparse
import concurrent.futures as cf
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPEC_FILE = ROOT / "bench" / "spec.json"
OUT = ROOT / "data" / "bench"
OLLAMA = "http://127.0.0.1:11434"

SPEC = {
    "version": 1,
    "rules": "zero complete passes in Stage A = eliminated from that board; ranking: passes, then passes per generation-minute, then VRAM; judge is the authority",
    "sampling": {"temperature": 0.7, "top_p": 0.95, "num_ctx": 8192, "num_predict": 3072, "seed_base": 1000,
                 "think": "gpt-oss: low; every other model: off (same reasoning budget class, same output cap)"},
    "blender": {
        "prompt": "core.learn_blender_basics.TASK_TEXT + one-code-block instruction",
        "stage_A": {"tasks": ["bb:train:0", "bb:train:3", "bb:train:5", "bb:train:8", "bb:train:11", "bb:train:16", "bb:train:19", "bb:train:21"],
                    "k": 4, "wall_cap_min": 30},
        "stage_B": {"tasks": "all 24 bb:train", "k": 16, "wall_cap_min": 90},
        "attempt_timeouts_s": {"generate": 300, "judge": 120},
        "holdout": "bb:holdout tasks stay sealed and are never touched by the bench"},
}


def spec_hash():
    return hashlib.sha256(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()[:12]


def write_spec():
    SPEC_FILE.parent.mkdir(exist_ok=True)
    body = {**SPEC, "hash": spec_hash()}
    SPEC_FILE.write_text(json.dumps(body, indent=2), encoding="utf-8")
    print("spec", body["hash"], "->", SPEC_FILE)


def out_dir():
    d = OUT / spec_hash()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _http(path, body=None, timeout=30):
    req = urllib.request.Request(OLLAMA + path, json.dumps(body).encode() if body is not None else None, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def stop_reason():
    from core import learn
    if (OUT / "STOP").exists():
        return "STOP file"
    if learn.default_gaming():
        return "a game is running"
    return None


def generate(model, prompt, seed):
    s = SPEC["sampling"]
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "keep_alive": "5m",
            "options": {"temperature": s["temperature"], "top_p": s["top_p"], "num_ctx": s["num_ctx"], "num_predict": s["num_predict"], "seed": seed}}
    body["think"] = "low" if model.startswith("gpt-oss") else False
    t0 = time.monotonic()
    try:
        r = _http("/api/chat", body, timeout=SPEC["blender"]["attempt_timeouts_s"]["generate"])
    except urllib.error.HTTPError as e:                                  # a model that rejects the think flag: same call without it
        if e.code != 400:
            raise
        body.pop("think")
        r = _http("/api/chat", body, timeout=SPEC["blender"]["attempt_timeouts_s"]["generate"])
    return r["message"]["content"], round(time.monotonic() - t0, 1), r.get("eval_count", 0), r.get("eval_duration", 0)


def vram_use(model):
    try:
        for m in _http("/api/ps").get("models", []):
            if m["name"].split(":")[0] == model.split(":")[0]:
                return {"size_gb": round(m["size"] / 1e9, 1), "vram_gb": round(m.get("size_vram", 0) / 1e9, 1)}
    except Exception:
        pass
    return {}


def unload(model):
    try:
        _http("/api/generate", {"model": model, "keep_alive": 0})
    except Exception:
        pass


def prompt_for_blender():
    from core import learn_blender_basics as bb
    return bb.TASK_TEXT + "\nReply with ONE python code block containing the complete script and nothing else.\n"


def run_blender(model, stage):
    from core import learn_blender_basics as bb, learn_llm
    cfg = SPEC["blender"]["stage_" + stage]
    ids = cfg["tasks"] if isinstance(cfg["tasks"], list) else [t["id"] for t in bb.tasks("train")]
    by = {t["id"]: t for t in bb.tasks("train")}
    f = out_dir() / "blender.jsonl"
    done = set()
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["model"], r["stage"], r["task"], r["k"]))
    prompt = prompt_for_blender()
    deadline = time.monotonic() + cfg["wall_cap_min"] * 60
    pool = cf.ThreadPoolExecutor(max_workers=3)
    pending = []
    res_info = {}
    stopped = None

    def finish(meta, code, fut_task):
        v = bb.judge(by[fut_task]["variant"], code, timeout=SPEC["blender"]["attempt_timeouts_s"]["judge"])
        row = {**meta, "full": v["full"], "fail_count": v["fail_count"], "failing": v["failing"], "error": v.get("error")}
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row

    try:
        for k in range(cfg["k"]):                                    # round-robin over tasks so a wall cap cuts every task equally
            for tid in ids:
                if (model, stage, tid, k) in done:
                    continue
                stopped = stop_reason() or ("wall cap" if time.monotonic() > deadline else None)
                if stopped:
                    raise StopIteration
                try:
                    text, gen_s, toks, ns = generate(model, prompt, SPEC["sampling"]["seed_base"] + k)
                    code = learn_llm.code_block(text)
                except Exception as e:
                    text, gen_s, toks, ns, code = "", 0.0, 0, 0, ""
                    err = type(e).__name__
                else:
                    err = None
                if not res_info:
                    res_info = vram_use(model)
                meta = {"spec": spec_hash(), "model": model, "stage": stage, "task": tid, "k": k, "gen_s": gen_s, "tokens": toks,
                        "tok_s": round(toks / (ns / 1e9), 1) if ns else 0, "gen_error": err, "code_chars": len(code), "vram": res_info}
                pending.append(pool.submit(finish, meta, code, tid))
    except StopIteration:
        pass
    finally:
        for p in pending:
            p.result()
        pool.shutdown()
        unload(model)
    print("stopped early:", stopped) if stopped else None
    summary(model_filter=model)


def rows():
    f = out_dir() / "blender.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def summary(model_filter=None):
    by = {}
    for r in rows():
        if model_filter and r["model"] != model_filter:
            continue
        by.setdefault((r["model"], r["stage"]), []).append(r)
    for (m, st), rs in sorted(by.items()):
        passes = sum(1 for r in rs if r["full"])
        solved = len({r["task"] for r in rs if r["full"]})
        gen_min = sum(r["gen_s"] for r in rs) / 60
        near = sum(1 for r in rs if not r["full"] and r["fail_count"] <= 2)
        v = next((r["vram"] for r in rs if r["vram"]), {})
        toks = [r["tok_s"] for r in rs if r["tok_s"]]
        print(f"{m:28s} stage {st}: {passes}/{len(rs)} complete passes, {solved} distinct tasks solved, near-miss (not counted) {near}, "
              f"gen {gen_min:.1f} min, {sum(toks) / max(len(toks), 1):.0f} tok/s, mem {v}")


def validate():
    """The harness itself must work: the teacher reference script (never shown to any model) passes the judge here."""
    from core import learn_blender_basics as bb
    ref = (ROOT / "tests" / "fixtures" / "basics_reference.py").read_text(encoding="utf-8")
    tk = {t["id"]: t for t in bb.tasks("train")}
    ok = True
    for tid in ("bb:train:0", "bb:train:8", "bb:train:16"):
        v = bb.judge(tk[tid]["variant"], ref)
        print(tid, "full" if v["full"] else v)
        ok &= v["full"]
    empty = bb.judge(tk["bb:train:0"]["variant"], "pass\n")
    print("empty script fails as it should:", not empty["full"])
    sys.exit(0 if ok and not empty["full"] else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["spec", "validate", "blender", "summary"])
    ap.add_argument("model", nargs="?")
    ap.add_argument("--stage", default="A")
    a = ap.parse_args()
    {"spec": write_spec, "validate": validate, "summary": summary}.get(a.cmd, lambda: run_blender(a.model, a.stage))()
