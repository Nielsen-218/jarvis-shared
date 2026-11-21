"""Appearance profile schema for the Living Desktop Companion.

An appearance profile is COSMETIC-ONLY local data: which 3D model the renderer
should load, which adapter maps its skeleton/morph targets to our semantic
bone contract, what colors / materials / lights / proportions to apply, and
which saved animation preset to idle with. It explicitly does NOT carry:

  * identity (name, voiceprint, pronouns)
  * personality or memory state
  * brain/model configuration
  * voice credentials or API tokens
  * microphone / speaker device settings

That separation is a hard invariant — switching the appearance of the
companion must never restart or rebuild the brain, and the owner's identity
can't leak into a cosmetic file that might be shared or exported.

Profiles are stored at data/companion-profiles/<slug>.json. The default
profile is written on first run of ``default_profile()`` if the directory
is empty; callers never auto-write user-authored profiles behind their back.
"""
from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any

PROFILE_SCHEMA_VERSION = 1
# Procedural accessories the companion can wear (mirrors app/companion/accessories.js:ACCESSORIES).
ACCESSORY_KINDS = frozenset({"none", "tophat", "partyhat", "halo", "cap", "sunglasses", "bowtie"})
PROFILES_DIR = Path(__file__).resolve().parent.parent / "data" / "companion-profiles"
BUILTIN_PROFILES_DIR = Path(__file__).resolve().parent.parent / "app" / "companion" / "profiles"

# Semantic-bone contract. A model adapter must at minimum provide these
# bones (or an explicit null mapping; the animation driver has fallbacks
# for missing optional bones but refuses to animate without a root/head).
REQUIRED_BONES = ("root", "head")
OPTIONAL_BONES = (
    "neck", "torso",
    "shoulderL", "shoulderR", "elbowL", "elbowR", "wristL", "wristR",
    "fingersL", "fingersR",
    "hipL", "hipR", "kneeL", "kneeR", "ankleL", "ankleR",
    "earL", "earR",
    "chestCore", "faceScreen",
)
SEMANTIC_BONES = REQUIRED_BONES + OPTIONAL_BONES

# Fields that must NEVER appear in a profile. If one is present on load, the
# whole profile is rejected rather than silently stripped — surfacing the
# tampering is more honest than quietly losing data.
FORBIDDEN_FIELDS = frozenset({
    "identity", "pronouns", "voice_credentials", "voice_api_key",
    "brain_config", "model_api_key", "memory", "personality",
    "voiceprint", "mic_device", "speaker_device", "token", "api_key",
    # Note: profile.name is allowed — it's the UI LABEL ("Cyan Jarvis"), not
    # the owner's identity. Owner identity lives in users.json, not here.
})

# Material slots the robot renderer honors. New slots can be added without
# breaking old profiles — unknown slots are ignored with a warning, not an
# error, because old renderers should still load newer profiles.
MATERIAL_SLOTS = (
    "shell_light", "shell_dark", "face_screen", "eye_glow", "mouth_glow",
    "accent_primary", "accent_secondary", "chest_core", "joint_dark", "foot_glow",
)

ADAPTERS: dict[str, dict[str, Any]] = {
    # id -> metadata consumed by the client renderer. The adapter is what
    # turns a model_id into a renderer-neutral semantic-bone layout.
    # The textured concept robot (Hunyuan3D GLB) worn through app/companion/glb-adapter.js. With a rig_url the
    # renderer skins the mesh at load time (robot-skin.js) and adds eyelid/mouth overlays (glb-face.js); without
    # one the model is a static mesh and only `root` is driven.
    "glb-model-v1": {
        "model_kind": "glb",
        "bones_provided": ["root"],       # guaranteed; a rig_url adds the whole SEMANTIC_BONES set at load time
        "has_face_screen": False,
        "has_mouth_morph": False,         # true once a rig is loaded (reported live in the renderer capabilities)
        "notes": "glTF 2.0 binary selected by profile.model_url, optionally skinned + given a face by profile.rig_url.",
    },
}

MODEL_URL_PREFIXES = ("/companion/models/", "/api/")   # same-origin only; never a third-party host
RIG_URL_PREFIX = "/companion/rigs/"                     # skeleton/skin/face data for a glb model (optional)
DEFAULT_MODEL_URL = "/companion/models/robot/robot-lod1-multimat.glb"   # 12k tris, per-part material slots (eye/chest/accent)
DEFAULT_RIG_URL = "/companion/rigs/robot-rig.json"


def _validate_rig_url(value: Any, adapter_id: str) -> str | None:
    if ADAPTERS[adapter_id]["model_kind"] != "glb" or value in (None, ""):
        return None
    if (not isinstance(value, str) or len(value) > 200 or not value.startswith(RIG_URL_PREFIX)
            or ".." in value or "://" in value or "\\" in value or not value.endswith(".json")):
        raise ProfileError(f"rig_url must be a same-origin {RIG_URL_PREFIX}*.json path: {value!r}")
    return value


def _validate_model_url(value: Any, adapter_id: str) -> str | None:
    """A relative, same-origin path to a .glb."""
    if ADAPTERS[adapter_id]["model_kind"] != "glb":
        return None
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ProfileError("glb-model-v1 needs model_url (a same-origin /companion/models/... or /api/... path)")
    if not value.startswith(MODEL_URL_PREFIXES) or ".." in value or "://" in value or "\\" in value:
        raise ProfileError(f"model_url must be a same-origin path under {MODEL_URL_PREFIXES}: {value!r}")
    return value


def _hex(value: str, default: str) -> str:
    """Return a validated 6-char `#RRGGBB` string, else the default."""
    if not isinstance(value, str):
        return default
    m = re.fullmatch(r"#?[0-9a-fA-F]{6}", value.strip())
    if not m:
        return default
    s = value.strip()
    return s if s.startswith("#") else "#" + s


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:
        return default
    return max(lo, min(hi, f))


def default_profile() -> dict[str, Any]:
    """The out-of-the-box appearance: the concept-board robot in default
    cyan trim. Returned as a fresh dict every call so callers can mutate."""
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "id": "default",
        "name": "Jarvis (concept robot)",
        "model_id": "robot-lod1",
        "adapter_id": "glb-model-v1",
        "model_url": DEFAULT_MODEL_URL,  # /companion/models/<name>.glb
        "rig_url": DEFAULT_RIG_URL,      # optional: /companion/rigs/<name>.json (skeleton + face)
        "proportions": {
            "head_scale": 1.0,          # 0.6..1.5
            "body_scale": 1.0,
            "arm_length": 1.0,
            "leg_length": 1.0,
            "overall_scale": 1.0,
        },
        "materials": {
            # the concept turnaround's palette (docs/companion/concept-turnaround.webp); mirrored in materials.js
            "shell_light":     {"color": "#e4e9f1", "metalness": 0.5, "roughness": 0.32},   # silver armour
            "shell_dark":      {"color": "#242735", "metalness": 0.55, "roughness": 0.35},  # graphite panels
            "face_screen":     {"color": "#080c17", "metalness": 0.1, "roughness": 0.08},
            "eye_glow":        {"color": "#ff36ce", "emissive": 1.0},    # magenta ring eyes + ear modules
            "mouth_glow":      {"color": "#00e5ff", "emissive": 0.9},
            "accent_primary":  {"color": "#00e5ff", "emissive": 0.9},    # cyan: emblem, fin strips, claws
            "accent_secondary":{"color": "#2f6be0", "emissive": 0.3},    # blue: knee discs, toe caps
            "chest_core":      {"color": "#00e5ff", "emissive": 1.1},
            "joint_dark":      {"color": "#1a1d28", "metalness": 0.5, "roughness": 0.55},
            "foot_glow":       {"color": "#00e5ff", "emissive": 0.8},
        },
        "lights": {
            "key":  {"color": "#ffffff", "intensity": 1.1},     # neutral key: the baked texture carries the colour
            "rim":  {"color": "#7eccff", "intensity": 0.55},
            "ambient": {"color": "#dce7f2", "intensity": 0.35},
        },
        "animation_preset": "idle",
        "accessory": "none",            # a worn accessory (see ACCESSORY_KINDS)
        "accessories": [],              # list of {slot, model_id} (future phase)
        "created_at": None,
        "updated_at": None,
    }


def _merge_defaults(raw: dict) -> dict:
    """Shallow merge with the default profile; nested dicts (materials,
    proportions, lights) are also merged key-by-key so a partial profile
    stays valid."""
    out = default_profile()
    for key, val in raw.items():
        if key in ("materials", "proportions", "lights") and isinstance(val, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            for slot, inner in val.items():
                if isinstance(inner, dict) and isinstance(merged.get(slot), dict):
                    cur = dict(merged[slot])
                    cur.update(inner)
                    merged[slot] = cur
                else:
                    merged[slot] = inner
            out[key] = merged
        else:
            out[key] = val
    return out


def _validate_materials(mats: dict) -> dict:
    """Clamp numeric channels, validate hex colors, drop unknown slots to a
    log-only warning (callers can still see them in the raw dict). Returns a
    normalized copy."""
    out: dict[str, Any] = {}
    defaults = default_profile()["materials"]
    for slot, spec in (mats or {}).items():
        if slot not in MATERIAL_SLOTS:
            # Preserve unknown slot but don't normalize it; the renderer skips it.
            out[slot] = spec
            continue
        base = defaults[slot]
        if not isinstance(spec, dict):
            out[slot] = dict(base)
            continue
        norm: dict[str, Any] = {}
        norm["color"] = _hex(spec.get("color"), base["color"])
        if "metalness" in base or "metalness" in spec:
            norm["metalness"] = _clamp(spec.get("metalness", base.get("metalness", 0.3)), 0, 1, base.get("metalness", 0.3))
        if "roughness" in base or "roughness" in spec:
            norm["roughness"] = _clamp(spec.get("roughness", base.get("roughness", 0.5)), 0, 1, base.get("roughness", 0.5))
        if "emissive" in base or "emissive" in spec:
            norm["emissive"] = _clamp(spec.get("emissive", base.get("emissive", 0.0)), 0, 4, base.get("emissive", 0.0))
        out[slot] = norm
    # Fill in any missing slots from defaults so the renderer never gets None.
    for slot, base in defaults.items():
        out.setdefault(slot, dict(base))
    return out


def _validate_proportions(p: dict) -> dict:
    out = default_profile()["proportions"]
    for k, (lo, hi) in {"head_scale": (0.6, 1.5), "body_scale": (0.6, 1.5),
                        "arm_length": (0.6, 1.5), "leg_length": (0.6, 1.5),
                        "overall_scale": (0.5, 2.0)}.items():
        out[k] = _clamp((p or {}).get(k, out[k]), lo, hi, out[k])
    return out


def _validate_lights(l: dict) -> dict:
    out = copy.deepcopy(default_profile()["lights"])
    for name, spec in (l or {}).items():
        if name not in out:
            continue
        if not isinstance(spec, dict):
            continue
        out[name]["color"] = _hex(spec.get("color"), out[name]["color"])
        out[name]["intensity"] = _clamp(spec.get("intensity", out[name]["intensity"]), 0, 4, out[name]["intensity"])
    return out


class ProfileError(ValueError):
    """Raised when a profile is unsafe to load (forbidden field, bad id,
    unknown adapter, wrong schema version)."""


def validate_profile(raw: Any, *, strict: bool = False) -> dict:
    """Return a normalized copy of ``raw`` safe to hand to the renderer.

    Raises ProfileError when a forbidden field is present, the adapter is
    unknown, the schema version is in the future, or ``id`` is invalid.

    ``strict=True`` additionally raises for any unknown top-level key; by
    default we preserve unknown keys so a newer profile stays loadable in
    an older renderer (the renderer ignores what it doesn't understand).
    """
    if not isinstance(raw, dict):
        raise ProfileError("profile must be a JSON object")
    for key in raw:
        if key in FORBIDDEN_FIELDS:
            raise ProfileError(f"forbidden field in cosmetic profile: {key!r}")
    sv = raw.get("schema_version", 1)
    if not isinstance(sv, int) or sv < 1 or sv > PROFILE_SCHEMA_VERSION:
        raise ProfileError(f"unsupported schema_version: {sv!r}")
    pid = raw.get("id", "default")
    if not isinstance(pid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", pid):
        raise ProfileError(f"invalid profile id: {pid!r}")
    adapter_id = raw.get("adapter_id", "glb-model-v1")
    if adapter_id not in ADAPTERS:
        raise ProfileError(f"unknown adapter_id: {adapter_id!r}")

    merged = _merge_defaults(raw)
    merged["schema_version"] = PROFILE_SCHEMA_VERSION
    merged["id"] = pid
    merged["adapter_id"] = adapter_id
    # name is cosmetic — but has nothing to do with identity; it's just the
    # label for the UI ("Jarvis — Carbon", "Jarvis — Festive").
    merged["name"] = str(merged.get("name") or pid)[:80]
    merged["model_id"] = str(merged.get("model_id") or ADAPTERS[adapter_id].get("model_kind", "glb"))[:80]
    merged["model_url"] = _validate_model_url(merged.get("model_url"), adapter_id)
    merged["rig_url"] = _validate_rig_url(merged.get("rig_url"), adapter_id)
    merged["proportions"] = _validate_proportions(merged.get("proportions") or {})
    merged["materials"] = _validate_materials(merged.get("materials") or {})
    merged["lights"] = _validate_lights(merged.get("lights") or {})
    anim = merged.get("animation_preset", "idle")
    merged["animation_preset"] = anim if isinstance(anim, str) and anim[:40] else "idle"
    acc = merged.get("accessory", "none")
    merged["accessory"] = acc if isinstance(acc, str) and acc in ACCESSORY_KINDS else "none"
    accs = merged.get("accessories", [])
    if not isinstance(accs, list):
        accs = []
    merged["accessories"] = [a for a in accs if isinstance(a, dict)]
    if strict:
        allowed = set(default_profile())
        for key in merged:
            if key not in allowed:
                raise ProfileError(f"strict: unknown top-level key {key!r}")
    return merged


def adapter_capabilities(adapter_id: str) -> dict:
    """What a renderer can rely on when it loads this adapter. Returns a
    copy so callers can't mutate the registry."""
    meta = ADAPTERS.get(adapter_id)
    if meta is None:
        raise ProfileError(f"unknown adapter_id: {adapter_id!r}")
    return copy.deepcopy(meta)


def profile_path(pid: str, root: Path | None = None) -> Path:
    pid = str(pid)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", pid):
        raise ProfileError(f"invalid profile id: {pid!r}")
    base = root or PROFILES_DIR
    return base / f"{pid}.json"


def load_profile(pid: str = "default", root: Path | None = None) -> dict:
    """Load and validate a profile. If missing or corrupt, falls back to the
    default profile (and does NOT auto-write anything)."""
    path = profile_path(pid, root)
    if root is None and not path.exists() and pid != "default":
        path = profile_path(pid, BUILTIN_PROFILES_DIR)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_profile()
    try:
        return validate_profile(raw)
    except ProfileError:
        return default_profile()


def save_profile(profile: dict, root: Path | None = None) -> Path:
    """Validate and persist a profile. Returns the written path. Raises
    ProfileError if the profile is unsafe."""
    normalized = validate_profile(profile)
    normalized["updated_at"] = int(time.time())
    if not normalized.get("created_at"):
        normalized["created_at"] = normalized["updated_at"]
    base = root or PROFILES_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = profile_path(normalized["id"], base)
    path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
    return path


def list_profiles(root: Path | None = None) -> list[dict]:
    """Enumerate profiles that validate without error. Corrupt files are
    omitted (and left on disk for the owner to inspect)."""
    base = root or PROFILES_DIR
    paths = {p.stem: p for p in BUILTIN_PROFILES_DIR.glob("*.json")} if root is None else {}
    paths.update({p.stem: p for p in base.glob("*.json")})
    out = []
    for p in sorted(paths.values()):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            out.append(validate_profile(raw))
        except (OSError, ValueError, ProfileError):
            continue
    return out


__all__ = [
    "PROFILE_SCHEMA_VERSION", "SEMANTIC_BONES", "REQUIRED_BONES", "OPTIONAL_BONES",
    "MATERIAL_SLOTS", "ADAPTERS", "FORBIDDEN_FIELDS",
    "ProfileError", "default_profile", "validate_profile",
    "adapter_capabilities", "load_profile", "save_profile", "list_profiles",
    "profile_path",
]
