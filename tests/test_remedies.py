"""The failure taxonomy picks remedies by evidence and the retry loop keeps the best attempt within budget."""
from tools import remedies as R
from tools import rigging


def res(ok=False, stages=None, failing=(), kind="humanoid", **kw):
    return {"ok": ok, "kind": kind, "stages": stages or {}, "failing_pose_tests": list(failing), **kw}


def test_classification():
    assert R.classify("Rigging timed out after 25 minutes") == "timeout"
    assert R.classify("Rigging crashed before producing a report. Tail") == "crash"
    assert R.classify("Give me an existing .glb") is None                       # bad input is not retried
    assert R.classify(res(True)) is None
    assert R.classify(res(stages={"skin_valid": False})) == "skin_invalid"
    assert R.classify(res(failing=["finger_curl.L"], stages={"pose_tests": "15/16 passed"})) == "pose_fingers"
    assert R.classify(res(failing=["knee.L"], stages={"pose_tests": "15/16 passed"})) == "pose_limbs"
    assert R.classify(res(stages={"pose_tests": "16/16 passed", "ik_fk": "3/4 passed"})) == "ik"
    assert R.classify(res(stages={"unreal_import": "FAIL"})) == "unreal"


def test_ranking_follows_recorded_wins():
    stats = {}
    assert R.rank("pose_limbs", stats, set())[0] == "double_faces"              # table order with no evidence
    for _ in range(4):
        R.record(stats, "pose_limbs", "halve_faces", True)
        R.record(stats, "pose_limbs", "double_faces", False)
    assert R.rank("pose_limbs", stats, set())[0] == "halve_faces"
    assert "halve_faces" not in R.rank("pose_limbs", stats, {"halve_faces"})
    assert "swap_kind" not in R.TAXONOMY["pose_limbs"]


def test_next_step_never_repeats_parameters():
    assert R.next_step("timeout", "humanoid", 40000, {}, set(), set()) == ("halve_faces", "humanoid", 20000)
    assert R.next_step("timeout", "humanoid", 40000, {}, {("humanoid", 20000)}, set()) is None
    assert R.apply("halve_faces", "humanoid", 8000) is None                     # already at the floor


def test_retry_loop_recovers_and_learns(monkeypatch, tmp_path):
    monkeypatch.setattr(rigging, "_ledger", lambda: store)
    monkeypatch.setattr(rigging, "_save", lambda led: None)
    store = {"runs": [], "lessons": []}
    calls = []
    def once(src, kind, profile, faces, max_faces=0):          # Mark V: UE5 rigs carry a game face budget
        calls.append((kind, faces))
        if len(calls) == 1:
            return "Rigging timed out after 25 minutes; partial files"
        return res(True, {"pose_tests": "16/16 passed"}, output_folder="x")
    monkeypatch.setattr(rigging, "_rig_once", once)
    out = rigging._rig("m.glb", "", "ue5", 40000)
    assert out["ok"] and calls == [("", 40000), ("humanoid", 20000)]
    assert [a["failure"] for a in out["attempts"]] == ["timeout", None]
    assert store["remedies"]["timeout"]["halve_faces"] == {"tries": 1, "wins": 1}


def test_retry_budget_and_best_attempt_kept(monkeypatch):
    store = {"runs": [], "lessons": []}
    monkeypatch.setattr(rigging, "_ledger", lambda: store)
    monkeypatch.setattr(rigging, "_save", lambda led: None)
    n = []
    def once(src, kind, profile, faces, max_faces=0):          # Mark V: UE5 rigs carry a game face budget
        n.append(faces)
        return res(False, {"skin_valid": True, "pose_tests": "14/16 passed"}, failing=["knee.L", "elbow.R"], kind="humanoid")
    monkeypatch.setattr(rigging, "_rig_once", once)
    out = rigging._rig("m.glb", "humanoid", "ue5", 40000)
    assert len(n) <= R.MAX_ATTEMPTS and not out["ok"] and len(out["attempts"]) == len(n)
