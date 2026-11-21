"""Who owns a 3D asset JARVIS works on (Mark V, 2026-11-09).

The owner's standing statement: "Paid plan i own it", "Its my design as well", "all things from meshy is mine remeber that
from now on and make sure jarvis do too". So every Meshy AI model is owner-owned and his original design. JARVIS recognises one:
  - by its name: Meshy exports are named "Meshy_AI_..." (also after JARVIS converts them: the stem is kept);
  - by its fingerprint: sources the owner said are his Meshy models even though the file was renamed
    (data/learning/owner-assets.json, "meshy_sha256"), seeded with the Shadow World main character.
The record says it is the owner's statement; JARVIS does not check it against Meshy's terms.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

STATEMENT = "Owner-owned output: generated on the owner's paid Meshy plan; the owner's own design (owner's standing statement, 2026-11-09)"
KNOWN_MESHY = {"41150e0322f2d05594a57040ea5ee1af035ee1af5a7ab723e0f864385844fb60"}   # Shadow_world_main_character (renamed Meshy export)
_MESHY_NAME = re.compile(r"(?:^|[\\/])meshy[_ -]?ai[_ -]|(?:^|[\\/])meshy[_ -]", re.I)


def _store() -> Path:
    from core import storage
    return storage.DATA / "learning" / "owner-assets.json"


def known_meshy() -> set:
    try:
        data = json.loads(_store().read_text(encoding="utf-8"))
        return KNOWN_MESHY | {str(x) for x in data.get("meshy_sha256", [])}
    except (OSError, ValueError, AttributeError):
        return set(KNOWN_MESHY)


def remember_meshy(sha256: str) -> None:
    """The owner said this file is one of his Meshy models (a renamed export)."""
    path = _store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    shas = set(data.get("meshy_sha256", [])) | {sha256}
    data["meshy_sha256"] = sorted(shas)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_meshy(path: str = "", sha256: str = "") -> bool:
    name = Path(path).name if path else ""
    return bool((name and _MESHY_NAME.search(name)) or (sha256 and sha256 in known_meshy()))


def provenance(path: str = "", sha256: str = "") -> Optional[dict]:
    """The provenance record for an owner-owned Meshy source, else None (unknown sources are never guessed)."""
    if not is_meshy(path, sha256):
        return None
    return {"generator": "Meshy AI (meshy.ai), the owner's own model", "license": STATEMENT,
            "design": "The owner's own original character design", "original_character": True,
            "owner_statement": "Paid plan i own it / Its my design as well / all things from meshy is mine",
            "recorded_by": "JARVIS from the owner's standing statement; not checked against Meshy's terms",
            "source_name": Path(path).name if path else "", "source_sha256": sha256}


def record(out_folder, source_path: str, sha256: str = "") -> bool:
    """Write provenance.json into a rig output folder for a Meshy source (never overwrites an existing record)."""
    out = Path(out_folder)
    target = out / "provenance.json"
    if target.exists():
        return False
    prov = provenance(source_path, sha256)
    if not prov:
        return False
    target.write_text(json.dumps(prov, indent=2), encoding="utf-8")
    return True
