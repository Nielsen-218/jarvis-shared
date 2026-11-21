"""Real vendored Three tests for finite procedural performances."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_performances_node():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for procedural performance tests')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / 'tests/node/test_performances.mjs')],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        'schema': 1, 'passed': 11, 'failed': 0,
    }
