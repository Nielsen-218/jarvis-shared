"""The synthetic test meshes under docs/rigging/synth/ are generated, gitignored binaries (docs/rigging/.gitignore: *.glb).

Tests that need them used to assume they were already on disk, so they passed only on the machine where someone had generated
them and failed with KeyError('report') / AdapterError on every clean checkout that has Blender. This builds any that are missing
with the repo's own generators (hub/synth_meshes.py, hub/synth_creatures.py, hub/synth_heldout.py), a few seconds to a minute each, once:
the files are cached on disk afterwards. Without Blender nothing can be generated, so the dependent tests skip with a reason.
"""
import subprocess
from pathlib import Path

import pytest

from core.config import ROOT
from tools import rigging

SYNTH = ROOT / "docs" / "rigging" / "synth"
_CREATURES = ("dragon", "spider", "serpent", "bird")
_HELDOUT = ("tripod", "crab", "manta", "centipede", "hydra", "starfish")

# One generator run. The largest mesh (heldout_centipede) takes about a minute on a quiet PC and passed three minutes
# while the PC was busy (2026-11-14: the file was never written, so five Blender tests errored in every fresh worktree).
GENERATE_TIMEOUT_S = 900

# fixture file stem -> (generator script, arguments after "--" excluding the output path, extra flags)
SPECS = {
    "synthetic_humanoid": ("hub/synth_meshes.py", "humanoid", []),
    "synthetic_humanoid_disjoint": ("hub/synth_meshes.py", "humanoid", ["--no-weld"]),
    "synthetic_quadruped": ("hub/synth_meshes.py", "quadruped", []),
    **{f"creature_{n}": ("hub/synth_creatures.py", n, []) for n in _CREATURES},
    **{f"heldout_{n}": ("hub/synth_heldout.py", n, []) for n in _HELDOUT},
}


def _generate(stem: str, blender: str) -> None:
    script, name, flags = SPECS[stem]
    out = SYNTH / f"{stem}.glb"
    SYNTH.mkdir(parents=True, exist_ok=True)
    subprocess.run([blender, "-b", "--python", str(ROOT / script), "--", name, str(out), *flags],
                   capture_output=True, timeout=GENERATE_TIMEOUT_S, check=False)


def ensure(stems=None) -> list:
    """Generate any missing synthetic fixtures (needs Blender). Returns the stems that are still missing."""
    wanted = list(stems or SPECS)
    missing = [s for s in wanted if not (SYNTH / f"{s}.glb").exists()]
    blender = rigging.blender_exe()
    if missing and blender:
        for stem in missing:
            _generate(stem, str(blender))
        missing = [s for s in wanted if not (SYNTH / f"{s}.glb").exists()]
    return missing


def require(stems=None) -> None:
    """Make the fixtures exist or skip the calling test with an explicit reason (never a confusing KeyError)."""
    missing = ensure(stems)
    if missing:
        pytest.skip("synthetic test meshes are not generated and cannot be (Blender not installed): " + ", ".join(missing[:4])
                    + (" ..." if len(missing) > 4 else "") + " -- see tests/synth_fixtures.py")


@pytest.fixture(scope="session")
def synthetic_meshes():
    """Every synthetic mesh, generated once per session if absent. Use via @pytest.mark.usefixtures("synthetic_meshes")."""
    require()
