"""Exercise the direct scene interaction against the actual vendored Three.js."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_direct_play_node():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for direct play physics tests')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / 'tests/node/test_direct_play.mjs')],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary == {'schema': 1, 'passed': 8, 'failed': 0}
