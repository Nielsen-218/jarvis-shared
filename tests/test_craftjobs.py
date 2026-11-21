"""Background jobs: real processes are really cancelled, the GPU guard decides on measured numbers."""
import subprocess
import sys
import time

from core import craftjobs as J


def _wait(j, secs=15):
    t = time.time()
    while j["state"] in ("queued", "running") and time.time() - t < secs:
        time.sleep(0.1)


def test_job_runs_and_reports():
    j = J.submit("tiny job", lambda: "all good")
    _wait(j)
    assert j["state"] == "done" and "all good" in J.describe(j)


def test_cancel_kills_the_real_process():
    def work():
        return J.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=120).stdout
    j = J.submit("sleeping", work)
    time.sleep(1.0)
    t = time.time()
    assert J.cancel(j["id"]) is not None
    _wait(j)
    assert j["state"] == "cancelled" and time.time() - t < 10
    assert "Still working" not in J.describe(j) and "cancelled" in J.describe(j)


def test_a_crash_is_reported_not_raised():
    def boom():
        raise ValueError("bad mesh")
    j = J.submit("crashy", boom)
    _wait(j)
    assert j["state"] == "failed" and "bad mesh" in j["result"]


def test_jobs_queue_one_at_a_time():
    order = []
    a = J.submit("a", lambda: (order.append("a1"), time.sleep(0.6), order.append("a2"))[0] or "a")
    b = J.submit("b", lambda: order.append("b") or "b")
    _wait(b)
    assert order == ["a1", "a2", "b"]


def g(free, util=5, total=24000):
    return lambda: {"name": "x", "memory_total_mb": total, "memory_used_mb": total - free, "free_mb": free, "utilization_pct": util}


def test_gpu_guard_passes_when_there_is_room():
    assert J.gpu_state(gpu_fn=g(12000))["ok"]


def test_gpu_guard_unloads_the_language_model_when_that_makes_room():
    state = {"free": 3000}
    def gpu():
        return g(state["free"])()
    def unload():
        state["free"] = 14000
        return ["llama3.2:3b"]
    r = J.gpu_state(gpu_fn=gpu, unload_fn=unload)
    assert r["ok"] and "unloaded" in r["say"]


def test_gpu_guard_refuses_while_a_game_holds_the_card_and_touches_nothing():
    called = []
    r = J.gpu_state(gpu_fn=g(2000, util=95), unload_fn=lambda: called.append(1) or [], procs_fn=lambda: [{"name": "eldenring.exe"}])
    assert not r["ok"] and "game" in r["say"]


def test_a_cancelled_job_is_not_retried_after_its_process_dies():
    from tools import rigging
    calls = []
    def once(src, kind, profile, faces, max_faces=0):          # Mark V: UE5 rigs carry a game face budget
        calls.append(1)
        j = J.get()
        j["_cancel"].set()                                    # cancel lands while the attempt runs; the process then "crashes"
        return "Rigging crashed before producing a report. Tail"
    orig = rigging._rig_once
    rigging._rig_once = once
    try:
        j = J.submit("rigging x", lambda: rigging._rig("m.glb", "", "ue5", 40000))
        _wait(j)
    finally:
        rigging._rig_once = orig
    assert j["state"] == "cancelled" and len(calls) == 1
