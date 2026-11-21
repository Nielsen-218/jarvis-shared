// Appearance profile client-side loader + adapter registry.
//
// A "profile" is cosmetic-only data (see core/companion_profile.py); it
// names which model/adapter the renderer should use, which colors/materials
// to apply and which proportions to scale. This file exposes:
//
//   loadActiveProfile()           -> fetched via pywebview Api, default fallback
//   ADAPTERS                      -> id -> factory({T, profile}) -> adapter
//   buildAdapter(T, profile)      -> builds the rig+materials for profile.adapter_id
//   retintActive(mats, profile)   -> live color update without rebuild
//   capabilitiesOf(adapter_id)    -> static capability report for a given adapter
//
// Adapter objects must expose:
//   { group, bones, materials, dispose, capabilities }
// so a swap replaces group/bones/materials in-place without touching the
// scheduler or voice bridge.

import { retintMaterials } from "/companion/materials.js";
import { buildGlbAdapter } from "/companion/glb-adapter.js";

// The companion IS the concept robot: Hunyuan3D-textured GLB + runtime skeleton/face rig (rigs/robot-rig.json).
const DEFAULT_PROFILE = {
  schema_version: 1,
  id: "default",
  name: "Jarvis (concept robot)",
  model_id: "robot-lod1-multimat",
  adapter_id: "glb-model-v1",
  model_url: "/companion/models/robot/robot-lod1-multimat.glb",
  rig_url: "/companion/rigs/robot-rig.json",
  proportions: { head_scale: 1.0, body_scale: 1.0, arm_length: 1.0, leg_length: 1.0, overall_scale: 1.0 },
  // Mirrors core/companion_profile.py default palette so the plain-browser / lab path matches the
  // pywebview app (which fetches the palette from Python). The shell keeps its baked texture; the
  // glow slots (eyes/chest/accent) take the concept colours as emissive — the Studio tints each live.
  materials: {
    shell_light:      { color: "#e4e9f1", metalness: 0.5, roughness: 0.32 },
    eye_glow:         { color: "#ff36ce", emissive: 1.0 },   // magenta ring eyes
    mouth_glow:       { color: "#00e5ff", emissive: 0.9 },
    accent_primary:   { color: "#00e5ff", emissive: 0.9 },   // cyan ear modules
    chest_core:       { color: "#00e5ff", emissive: 1.1 },   // cyan chest core
  },
  lights:    {},
  animation_preset: "idle",
};

/** Registry of adapter factories keyed by `adapter_id`. A second entry here (another model + rig) is all a
 *  hot-swap needs; the animation clips and the face driver are model independent. */
export const ADAPTERS = {
  // A glTF model, optionally skinned at load time by profile.rig_url. Async: build() returns at once with an
  // empty mount and a `ready` promise; use buildAdapterAsync().
  "glb-model-v1": {
    label: "glTF model (profile.model_url + profile.rig_url)",
    build(T, profile) { return buildGlbAdapter(T, profile); },
  },
};

/** Fetch the active profile via the pywebview Api, falling back to the
 *  default when running under a plain browser (diagnostics / tests). */
export async function loadActiveProfile() {
  const api = typeof window !== "undefined" ? (window.pywebview && window.pywebview.api) : null;
  if (!api || typeof api.load_companion_profile !== "function") {
    return { ...DEFAULT_PROFILE };
  }
  try {
    const p = await api.load_companion_profile();
    if (p && typeof p === "object" && !p.error) return p;
  } catch {}
  return { ...DEFAULT_PROFILE };
}

/** Build the renderer-side adapter for a validated profile. */
export function buildAdapter(T, profile) {
  const id = (profile && profile.adapter_id) || "glb-model-v1";
  const entry = ADAPTERS[id] || ADAPTERS["glb-model-v1"];
  const adapter = entry.build(T, profile || DEFAULT_PROFILE);
  adapter.adapter_id = id;
  adapter.profile_id = profile?.id || "default";
  return adapter;
}

/** Build and wait until the model is loaded. A failed load rejects: the page then shows the 2D concept sprite
 *  (companion.html drawFallback) instead of an empty window, and the reason is logged. */
export async function buildAdapterAsync(T, profile) {
  const adapter = buildAdapter(T, profile);
  if (!adapter.ready) return adapter;
  try {
    await adapter.ready;
    return adapter;
  } catch (e) {
    try { adapter.dispose(); } catch {}
    throw e;
  }
}

/** Live retint current materials from a profile (no geometry rebuild). */
export function retintActive(mats, profile) {
  retintMaterials(mats, profile?.materials || {});
}

/** Static capability report consumed by the diagnostics endpoint. */
export function capabilitiesOf(adapter_id) {
  const entry = ADAPTERS[adapter_id] || ADAPTERS["glb-model-v1"];
  // The static report doesn't actually build anything; it names what the
  // adapter will provide once built. Needed for the two-adapter test.
  return {
    adapter_id,
    label: entry.label,
    rig_builder: "GLTFLoader + robot-skin.js (glb-adapter.js)",
  };
}
