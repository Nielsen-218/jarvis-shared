"""Isolated authenticated phone-route check against the real Windows desktop (development only).

Pairs a throwaway owner device in a TEMPORARY data folder (never the live users/devices), then drives the same
authenticated /api/pc/tasks route a phone uses: open an app, verify it, close it, verify it is gone.  Stage timings
are wall-clock for this process.  It does not test a physical phone; that remains an owner-assisted check.
"""
import json
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(reps=5, app="calculator"):
    from core import storage
    storage.DATA = Path(tempfile.mkdtemp(prefix="phone-route-"))
    import os
    os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
    from fastapi.testclient import TestClient
    from core import appserver, pcbridge
    from tools import apps
    token = appserver.add_device("owner", "dev-phone-route-check")
    headers = {"Authorization": "Bearer " + token}
    rows = []
    with TestClient(appserver.AppServer(lambda uid: None).app) as client:
        for index in range(reps):
            for phrase in (f"open {app}", f"close {app}"):
                t0 = time.monotonic()
                start = client.post("/api/pc/tasks", headers=headers, json={
                    "name": "control_computer", "arguments": {"task": phrase}, "request_id": str(uuid.uuid4()), "user_message": phrase})
                accepted = time.monotonic() - t0
                job = start.json()
                while job.get("state", job.get("status")) in ("queued", "running", "pending", None) and time.monotonic() - t0 < 30:
                    time.sleep(.15)
                    job = client.get("/api/pc/tasks/" + job["id"], headers=headers).json()
                total = time.monotonic() - t0
                visible = bool(apps.visible_windows_for_app(app))
                expected_visible = phrase.startswith("open")
                rows.append({"phrase": phrase, "http": start.status_code, "state": job.get("state", job.get("status")),
                             "accepted_ms": round(accepted * 1000), "total_ms": round(total * 1000),
                             "independent_window_check_passed": visible == expected_visible,
                             "result": str(job.get("result") or job.get("detail") or "")[:160]})
                print(rows[-1], file=sys.stderr, flush=True)
    for kind in ("open", "close"):
        sel = [r for r in rows if r["phrase"].startswith(kind)]
        ms = sorted(r["total_ms"] for r in sel)
        print(kind, "n=%d verified=%d p50=%s p95=%s" % (len(sel), sum(r["independent_window_check_passed"] for r in sel),
              statistics.median(ms), ms[min(len(ms) - 1, round(.95 * (len(ms) - 1)))]), file=sys.stderr)
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
