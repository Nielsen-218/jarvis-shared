// Wearable accessories for the companion (Feature #27 — outfits & accessories).
//
// Each is a small procedural mesh group in the model's metric frame (metres), parented to the head bone so it
// rides along with every animation. buildAccessory(T, kind, headPivot) returns a group positioned relative to
// the head bone's pivot, or null for "none"/unknown. Kept Node-safe (three is passed in as `T`).

export const ACCESSORIES = ["none", "tophat", "partyhat", "halo", "cap", "sunglasses", "bowtie"];

const hex = (h) => parseInt(String(h).replace("#", ""), 16);

export function buildAccessory(T, kind, headPivot = [0, 0.158, 0]) {
  if (!kind || kind === "none" || !ACCESSORIES.includes(kind)) return null;
  const g = new T.Group(); g.name = "accessory:" + kind;
  const metal = (color, m = 0.6, r = 0.3) => new T.MeshStandardMaterial({ color: hex(color), metalness: m, roughness: r });
  const glow = (color) => { const mm = new T.MeshBasicMaterial({ color: hex(color) }); mm.toneMapped = false; return mm; };
  const up = 0.108;                                   // head-top offset above the head bone's pivot (tuned to the rig)

  if (kind === "tophat") {
    const brim = new T.Mesh(new T.CylinderGeometry(0.075, 0.075, 0.006, 24), metal("#0a0a10", 0.3, 0.5));
    const tube = new T.Mesh(new T.CylinderGeometry(0.05, 0.05, 0.075, 24), metal("#0a0a10", 0.3, 0.5));
    tube.position.y = 0.04; const band = new T.Mesh(new T.CylinderGeometry(0.051, 0.051, 0.014, 24), metal("#c81d3a", 0.2, 0.4)); band.position.y = 0.012;
    g.add(brim, tube, band); g.position.set(0, up + 0.02, 0.004);
  } else if (kind === "partyhat") {
    const cone = new T.Mesh(new T.ConeGeometry(0.05, 0.11, 20), metal("#ff36ce", 0.1, 0.5));
    const pom = new T.Mesh(new T.SphereGeometry(0.014, 12, 10), glow("#ffe14d")); pom.position.y = 0.062;
    g.add(cone, pom); g.position.set(0, up + 0.03, 0);
  } else if (kind === "halo") {
    const ring = new T.Mesh(new T.TorusGeometry(0.052, 0.008, 12, 28), glow("#ffe14d"));
    ring.rotation.x = Math.PI / 2; g.add(ring); g.position.set(0, up + 0.055, 0);
  } else if (kind === "cap") {
    const dome = new T.Mesh(new T.SphereGeometry(0.058, 20, 12, 0, Math.PI * 2, 0, Math.PI / 2), metal("#2f6be0", 0.3, 0.5));
    const brim = new T.Mesh(new T.CylinderGeometry(0.052, 0.052, 0.006, 20, 1, false, 0, Math.PI), metal("#2456c0", 0.3, 0.5));
    brim.position.set(0, -0.002, 0.052); brim.scale.set(1, 1, 0.6);
    g.add(dome, brim); g.position.set(0, up, 0);
  } else if (kind === "sunglasses") {
    const lens = () => new T.Mesh(new T.CircleGeometry(0.02, 20), new T.MeshStandardMaterial({ color: 0x05070a, metalness: 0.4, roughness: 0.1 }));
    const l = lens(), r = lens(); l.position.set(-0.036, 0.04, 0.066); r.position.set(0.036, 0.04, 0.066);
    const bridge = new T.Mesh(new T.BoxGeometry(0.04, 0.004, 0.004), metal("#111")); bridge.position.set(0, 0.04, 0.066);
    g.add(l, r, bridge);                               // positioned in head-local space already (eyes), no extra offset
  } else if (kind === "bowtie") {
    const tri = (s) => { const geo = new T.ConeGeometry(0.018, 0.03, 3); const m = new T.Mesh(geo, metal("#c81d3a", 0.1, 0.4)); m.rotation.z = s * Math.PI / 2; m.position.x = s * 0.016; return m; };
    const knot = new T.Mesh(new T.BoxGeometry(0.01, 0.014, 0.01), metal("#8a0f26", 0.1, 0.4));
    g.add(tri(1), tri(-1), knot); g.position.set(0, -0.12, 0.052);   // at the neck/chest, below the head bone
  }
  g.traverse((o) => { if (o.isMesh) o.frustumCulled = false; });
  return g;
}
