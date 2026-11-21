"""Runs tests/node/test_behavior.mjs under Node so pytest surfaces the result.

The JS tests exercise the ACTUAL renderer logic in app/companion/behavior.js
(state resolver, scheduler, mouth stale timeout, idle picker, event buffer),
not a Python mirror. If Node is not available on PATH, this suite skips with
a clear message so CI without Node does not falsely fail the slice.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "node" / "test_behavior.mjs"
MODULE = ROOT / "app" / "companion" / "behavior.js"


@pytest.fixture(scope="module")
def node_exe() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; the JS behaviour tests require it")
    return node


def test_behavior_module_exists():
    assert MODULE.exists(), f"missing behavior module: {MODULE}"
    src = MODULE.read_text(encoding="utf-8")
    # Public API surface the renderer and tests both depend on.
    for needle in ("export const SCHEMA", "export function validate",
                   "export function resolveState", "export class IdlePicker",
                   "export class Scheduler", "export class CompanionState",
                   "export class EventBuffer", "LEVEL_STALE_MS"):
        assert needle in src, needle


def test_js_behavior_suite_passes(node_exe):
    proc = subprocess.run(
        [node_exe, str(HARNESS)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"node exited {proc.returncode}:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    # Last stdout line is the JSON summary; everything before is informational.
    tail = proc.stdout.strip().splitlines()[-1]
    summary = json.loads(tail)
    assert summary["failed"] == 0, summary
    assert summary["passed"] >= 100, summary
    assert summary["schema"] == 1, summary


AMPLITUDE_HARNESS = ROOT / "tests" / "node" / "test_amplitude.mjs"


def test_js_amplitude_suite_passes(node_exe):
    """Real synthetic-PCM test of the voice-bridge amplitude meter.
    Zero live audio: feeds known sine waves and compares RMS to the
    analytical answer. If this ever breaks, the mouth will desync."""
    assert AMPLITUDE_HARNESS.exists(), "missing amplitude harness"
    proc = subprocess.run(
        [node_exe, str(AMPLITUDE_HARNESS)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"node exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    tail = proc.stdout.strip().splitlines()[-1]
    summary = json.loads(tail)
    assert summary["amplitude_failed"] == 0, summary
    assert summary["amplitude_passed"] >= 10, summary
