"""Pure deterministic render-budget regression suite; no native/GPU calls."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parent.parent


def test_performance_budget_sequences():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js is required for the performance budget tests")
    proc = subprocess.run(
        [node_exe, str(ROOT / "tests/node/test_performance_budget.mjs")],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["performance_budget_failed"] == 0
    assert result["performance_budget_passed"] >= 11
