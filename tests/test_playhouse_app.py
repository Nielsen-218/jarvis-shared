"""Exercise the production JavaScript story engine."""
import shutil
import subprocess
from pathlib import Path
import pytest

@pytest.mark.parametrize("script", ["test_playhouse_story.mjs", "test_playhouse_party.mjs", "test_playhouse_mystery.mjs", "test_playhouse_rivalry.mjs"])
def test_game_engines(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for production JavaScript tests")
    result = subprocess.run([node, "tests/node/" + script],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
