# Detailed human asset

Source: MakeHuman community, revision `a8bc2d54ff0ac92e78ff71431b1023eda42bf482`.
License: CC0-1.0 for graphical data, as explicitly documented by upstream LICENSE.md
section C and the included `LICENSE-CC0.md`. No AGPL application code is included.
Exact source files and SHA-256 digests are recorded in `provenance.json`.

`human.json` contains body topology, anatomical landmarks and selected adult morph targets.
The ancestry-labelled macro target deltas are averaged for each sex; there is no inference
from a person's nationality, ethnicity, medical history or photographs. Browser shape sliders
adjust appearance only. They must not change saved health measurements or claim scan accuracy.

Rebuild intentionally with `python scripts/build_avatar_assets.py` (downloads about 5 MB
of pinned raw asset data into ignored `data/avatar-source/`; no remote program code executed).
The renderer generates and adjusts generic clothing shells around this topology. It does not
reconstruct the user's actual garments in 3D. Detailed anatomy is always covered by base clothes
in the studio. This is a generic licensed model, not either household member's private scan.
