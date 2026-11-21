"""24-hour stability monitor for the live Jarvis on this PC: one JSON line a minute, a summary on demand.

    .venv\\Scripts\\pythonw.exe scripts\\stability_monitor.py --hours 24          # run detached
    .venv\\Scripts\\python.exe scripts\\stability_monitor.py --summary             # read what it found so far

Samples: the process on port 8765 (pid, start time, RSS, CPU%, threads), http://127.0.0.1:8765/ health and latency,
GPU memory used (nvidia-smi), Ollama residency, new error-like lines in data/jarvis.log since the last sample, and
the local tiers' audit outcomes since the last sample. Writes runs/stability-<date>.jsonl. Observes only: it never
restarts anything, never talks to a model, never reads conversation content (log lines are matched, then counted)."""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG = ROOT / "data" / "jarvis.log"
AUDIT = ROOT / "data" / "users" / "owner" / "intelligence_usage-pc.json"
ERROR = re.compile(r"Traceback|Error|error:|exception|failed|\[diag\]|\[resume\]|circuit", re.I)


def listener_pid():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-NetTCPConnection -LocalPort 8765 -State Listen | Select-Object -First 1).OwningProcess"],
                         capture_output=True, text=True, timeout=20).stdout.strip()
    return int(out) if out.isdigit() else None


def gpu():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        used, total, util = [int(x) for x in out.strip().split(",")]
        return {"vram_used_mb": used, "vram_total_mb": total, "gpu_util": util}
    except Exception:
        return {}


def ollama():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3) as r:
            return {m["name"]: round(m.get("size_vram", 0) / 2 ** 30, 1) for m in json.loads(r.read()).get("models", [])}
    except Exception:
        return None


def health():
    t = time.monotonic()
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/", timeout=5) as r:
            return r.status, round((time.monotonic() - t) * 1000)
    except Exception as error:
        return type(error).__name__, round((time.monotonic() - t) * 1000)


def sample(state):
    import psutil
    pid = listener_pid()
    proc = {}
    if pid:
        try:
            p = psutil.Process(pid)
            proc = {"pid": pid, "started": datetime.fromtimestamp(p.create_time()).isoformat(timespec="seconds"),
                    "rss_mb": round(p.memory_info().rss / 2 ** 20), "cpu_pct": p.cpu_percent(interval=1.0),
                    "threads": p.num_threads()}
        except Exception as error:
            proc = {"pid": pid, "error": type(error).__name__}
    code, ms = health()
    size = LOG.stat().st_size if LOG.exists() else 0
    new_errors = []
    if size > state.get("log_pos", 0):
        with LOG.open("rb") as f:
            f.seek(state.get("log_pos", 0))
            for line in f.read().decode("utf-8", "replace").splitlines():
                if ERROR.search(line):
                    new_errors.append(re.sub(r"\d{2,}", "N", line)[:120])
    state["log_pos"] = size
    audit_new = {}
    try:
        rows = json.loads(AUDIT.read_text(encoding="utf-8"))
        since = state.get("audit_at", time.time() - 60)
        for r in rows:
            if r.get("at", 0) > since:
                key = f"{r['role']}:{r['status']}"
                audit_new[key] = audit_new.get(key, 0) + 1
        state["audit_at"] = time.time()
    except Exception:
        pass
    return {"t": datetime.now().isoformat(timespec="seconds"), "process": proc, "health": code, "health_ms": ms,
            **gpu(), "ollama": ollama(), "new_errors": new_errors[:20], "audit": audit_new,
            "sys_cpu": psutil.cpu_percent(interval=None), "sys_ram_pct": psutil.virtual_memory().percent}


def run(hours):
    out = ROOT / "runs" / f"stability-{datetime.now():%Y%m%d-%H%M}.jsonl"
    out.parent.mkdir(exist_ok=True)
    state = {"log_pos": LOG.stat().st_size if LOG.exists() else 0, "audit_at": time.time()}
    end = time.time() + hours * 3600
    while time.time() < end:
        row = sample(state)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        time.sleep(max(1.0, 60 - 2))


def summary():
    files = sorted((ROOT / "runs").glob("stability-*.jsonl"))
    if not files:
        print("no stability runs yet")
        return
    rows = [json.loads(line) for line in files[-1].read_text(encoding="utf-8").splitlines() if line.strip()]
    pids = {r["process"].get("pid") for r in rows if r.get("process")}
    starts = {r["process"].get("started") for r in rows if r.get("process")}
    bad = [r for r in rows if r["health"] != 200]
    rss = [r["process"]["rss_mb"] for r in rows if r.get("process", {}).get("rss_mb")]
    vram = [r["vram_used_mb"] for r in rows if r.get("vram_used_mb")]
    ms = sorted(r["health_ms"] for r in rows)
    errors = {}
    for r in rows:
        for e in r["new_errors"]:
            errors[e] = errors.get(e, 0) + 1
    audit = {}
    for r in rows:
        for k, v in r["audit"].items():
            audit[k] = audit.get(k, 0) + v
    print(f"{files[-1].name}: {len(rows)} samples, {rows[0]['t']} -> {rows[-1]['t']}")
    print(f"process restarts: {max(0, len(starts) - 1)} (pids {sorted(p for p in pids if p)}); health failures: {len(bad)}")
    if ms:
        print(f"health latency ms: median {ms[len(ms) // 2]} p95 {ms[int(len(ms) * .95)]} max {ms[-1]}")
    if rss:
        print(f"RSS MB: min {min(rss)} max {max(rss)} last {rss[-1]}")
    if vram:
        print(f"VRAM used MB: min {min(vram)} max {max(vram)} last {vram[-1]}")
    print("audit outcomes:", dict(sorted(audit.items(), key=lambda kv: -kv[1])))
    print("error-like log lines:", dict(sorted(errors.items(), key=lambda kv: -kv[1])[:12]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    summary() if a.summary else run(a.hours)
