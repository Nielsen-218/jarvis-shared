"""Deterministic tactical scenario replay. This is NOT a vision benchmark.

Events are synthetic or human-labeled evidence, never automatic detections.
Use to regression-test tactical decision quality, proof gates and silence.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.gamecoach import CoachConfig, Policy
from core.gamecoach_tactics import Evidence, TacticalCoach

class Clock:
    def __init__(self):
        self.now = 1000.
    def __call__(self):
        return self.now

def evaluate(case: dict) -> dict:
    clock = Clock()
    brain = TacticalCoach(game=case["game"], allow_replay_labels=True,
                          learning_weights=case.get("learning_weights", {}))
    policy = Policy(CoachConfig(global_cooldown_s=0, repeated_kind_cooldown_s=0,
                                max_callouts_per_minute=1000), clock=clock)
    actual = []
    frames = case["frames"]
    last_ms = -1.
    for i, frame in enumerate(frames):
        ms = float(frame["ms"])
        if ms <= last_ms:
            raise ValueError("Frame timestamps must be increasing")
        last_ms = ms
        clock.now = 1000 + ms / 1000
        evidence = {}
        for key, item in frame.get("signals", {}).items():
            if not isinstance(item, dict):
                continue
            evidence[key] = Evidence(
                value=item.get("value"),
                seen_at=clock.now - float(item.get("age_ms", 0)) / 1000,
                confidence=float(item.get("confidence", .95)),
                source=item.get("source", "clip_label"),
                proof=item.get("proof", "human-labeled replay observation"),
            )
        decision = brain.choose(evidence, now=clock.now)
        if decision:
            call = policy.decide(brain.to_observation(decision))
            if call is not None:
                actual.append({"frame": i, "kind": call.kind, "text": call.text,
                               "proof": list(decision.evidence)})
    expected = {(int(i["frame"]), i["kind"]) for i in case.get("expected", [])}
    detected = {(x["frame"], x["kind"]) for x in actual}
    return {"passed": expected == detected, "frames": len(frames),
            "expected_count": len(expected),
            "true_positives": len(expected & detected),
            "false_positives": len(detected - expected),
            "misses": len(expected - detected),
            "decisions": actual,
            "scope": "synthetic or human-annotated replay; no automatic visual perception",
            "game_fps_measured": False}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("case", type=Path)
    ap.add_argument("--report", type=Path)
    opts = ap.parse_args()
    result = evaluate(json.loads(opts.case.read_text(encoding="utf-8")))
    print(json.dumps(result, indent=2))
    if opts.report:
        opts.report.parent.mkdir(parents=True, exist_ok=True)
        opts.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    sys.exit(0 if result["passed"] else 1)
