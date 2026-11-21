// Live retinting for the Living Desktop Companion: profiles (core/companion_profile.py) swap
// colors/roughness/emissive on the adapter's slot materials without touching geometry.

function hexToInt(hex, fallback = 0xffffff) {
  if (typeof hex !== "string") return fallback;
  const m = hex.match(/^#?([0-9a-f]{6})$/i);
  return m ? parseInt(m[1], 16) : fallback;
}

export function retintMaterials(mats, profileMats = {}) {
  // Live retint without rebuilding geometry; used by the appearance profile
  // hot-swap path. New materials objects are NOT created so meshes keep
  // their references. Unlit-glow materials (userData.unlitGlow) carry intensity in their colour.
  for (const [slot, spec] of Object.entries(profileMats || {})) {
    const key = {
      shell_light: "shellLight", shell_dark: "shellDark",
      face_screen: "faceScreen", eye_glow: "eyeGlow", mouth_glow: "mouthGlow",
      accent_primary: "accentPrimary", accent_secondary: "accentSecondary",
      chest_core: "chestCore", joint_dark: "jointDark", foot_glow: "footGlow",
    }[slot];
    const mat = mats[key];
    if (!mat || !spec) continue;
    if (mat.userData.unlitGlow) {
      // unlit neon: colour carries the intensity (see glow() above)
      const base = typeof spec.color === "string" ? hexToInt(spec.color, mat.userData.glowHex) : mat.userData.glowHex;
      const k = "emissive" in spec ? Math.max(0, Math.min(4, +spec.emissive)) : mat.userData.glowIntensity;
      mat.userData.glowHex = base; mat.userData.glowIntensity = k;
      mat.color.setHex(base).multiplyScalar(Math.min(1, Math.max(0.15, k)));
      mat.needsUpdate = true;
      continue;
    }
    if (typeof spec.color === "string") mat.color.setHex(hexToInt(spec.color, mat.color.getHex()));
    if ("metalness" in spec && "metalness" in mat) mat.metalness = Math.max(0, Math.min(1, +spec.metalness));
    if ("roughness" in spec && "roughness" in mat) mat.roughness = Math.max(0, Math.min(1, +spec.roughness));
    if ("emissive" in spec && mat.emissive) {
      mat.emissiveIntensity = Math.max(0, Math.min(4, +spec.emissive));
      if (typeof spec.color === "string") mat.emissive.setHex(hexToInt(spec.color, mat.emissive.getHex()));
    }
    mat.needsUpdate = true;
  }
  return mats;
}

export function disposeMaterials(mats) {
  for (const m of Object.values(mats || {})) {
    try { m?.dispose?.(); } catch {}
  }
}

export { hexToInt as _hexToInt };
