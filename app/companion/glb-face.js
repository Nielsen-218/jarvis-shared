// Face overlays for the textured concept robot: eyelids over the baked magenta eyes and a digital mouth in the
// chin vent. The baked eyes/vent stay as the model's own art; these are real meshes parented to the head bone,
// so blinking, narrowed "thinking" eyes and the amplitude-driven mouth all follow the head.
//
// Honest labelling (same as facial.js): the mouth follows playback AMPLITUDE (smoothed jaw envelope), not phonemes.
//
// Landmarks come from rigs/robot-rig.json "face" (eye centres/normals measured from the texture's magenta texels).

const EYELID_COLOR = 0xa3abb6;      // the helmet's silver, as seen in the baked texture around the sockets
const MOUTH_GLOW = 0x36eeff;        // mouth_glow slot colour
const BLINK_MS = 150;

// Eyelid coverage per companion state (0 = wide open, 1 = shut). Muted = drowsy, thinking = narrowed.
const LID_BY_MODE = { idle: 0, listening: 0, speaking: 0, thinking: 0.28, working: 0.28, muted: 0.55, error: 0 };

/** Pure. Orthonormal frame for a face overlay: z = outward normal, y = world-up projected onto the surface. */
export function faceFrame(normal) {
  let [nx, ny, nz] = normal; const nl = Math.hypot(nx, ny, nz) || 1; nx /= nl; ny /= nl; nz /= nl;
  let ux = -nx * ny, uy = 1 - ny * ny, uz = -nz * ny;                  // up - n (up . n)
  const ul = Math.hypot(ux, uy, uz) || 1; ux /= ul; uy /= ul; uz /= ul;
  const rx = uy * nz - uz * ny, ry = uz * nx - ux * nz, rz = ux * ny - uy * nx;   // right = up x n
  return { n: [nx, ny, nz], up: [ux, uy, uz], right: [rx, ry, rz] };
}

/** Pure. Mouth overlay size for a smoothed amplitude `open` in [0,1] (metres). */
export function mouthSize(open, spec = {}) {
  const w = spec.width ?? 0.020, h = spec.height ?? 0.028;
  return { w: w * (0.3 + 0.7 * open), h: h * (0.14 + 0.86 * open), visible: open > 0.04 };
}

export function buildGlbFace(T, faceSpec, headBone, headPivot) {
  const basis = (f) => new T.Matrix4().makeBasis(new T.Vector3(...f.right), new T.Vector3(...f.up), new T.Vector3(...f.n));
  const local = (p) => new T.Vector3(p[0] - headPivot[0], p[1] - headPivot[1], p[2] - headPivot[2]);
  const root = new T.Group(); root.name = "glb-face"; headBone.add(root);
  const owned = [];

  // ---- eyelids ----
  const lids = [];
  const lidMat = new T.MeshStandardMaterial({ color: EYELID_COLOR, metalness: 0.55, roughness: 0.38, side: T.DoubleSide });
  owned.push(lidMat);
  for (const key of ["eyeL", "eyeR"]) {
    const e = faceSpec[key]; if (!e) continue;
    const f = faceFrame(e.normal), R = e.radius * 1.14;
    const g = new T.CircleGeometry(R, 40); g.translate(0, -R, 0); owned.push(g);
    const lid = new T.Group(); lid.name = `lid:${key}`;
    const top = local(e.center).add(new T.Vector3(...f.up).multiplyScalar(R * 0.88)).add(new T.Vector3(...f.n).multiplyScalar(0.0016));
    lid.position.copy(top); lid.quaternion.setFromRotationMatrix(basis(f));
    const mesh = new T.Mesh(g, lidMat); mesh.frustumCulled = false; lid.add(mesh);
    lid.scale.y = 0.0001; lid.visible = false; root.add(lid); lids.push(lid);
  }

  // ---- mouth ----
  const m = faceSpec.mouth || { center: [0, 0.1775, 0.0725], normal: [0, 0, 1] };
  const mf = faceFrame(m.normal);
  const mouth = new T.Group(); mouth.name = "mouth";
  mouth.position.copy(local(m.center)).add(new T.Vector3(...mf.n).multiplyScalar(0.0012));
  mouth.quaternion.setFromRotationMatrix(basis(mf));
  const pill = (inner, outer, edge) => {                 // soft rounded-bar texture (browser only; flat colour without a DOM)
    if (typeof document === "undefined") return null;
    const c = document.createElement("canvas"); c.width = c.height = 128; const x = c.getContext("2d");
    const rr = (inset) => { const r = 64 - inset; x.beginPath(); x.roundRect(inset, inset, 128 - 2 * inset, 128 - 2 * inset, r); };
    rr(2); x.fillStyle = outer; x.fill();
    const g = x.createLinearGradient(0, 0, 0, 128); g.addColorStop(0, outer); g.addColorStop(0.5, inner); g.addColorStop(1, outer);
    rr(10); x.fillStyle = g; x.fill();
    if (edge) { x.lineWidth = 3; x.strokeStyle = edge; rr(2); x.stroke(); }
    const t = new T.CanvasTexture(c); t.colorSpace = T.SRGBColorSpace; return t;
  };
  const backTex = pill("#0b1626", "#050a12"), glowTex = pill("#d4fdff", "#36eeff");
  const backMat = new T.MeshBasicMaterial({ color: 0xffffff, map: backTex, transparent: true });
  const glowMat = new T.MeshBasicMaterial({ color: 0xffffff, map: glowTex, transparent: true });
  glowMat.toneMapped = false; backMat.toneMapped = false; owned.push(backMat, glowMat, ...(backTex ? [backTex, glowTex] : []));
  const plane = new T.PlaneGeometry(1, 1); owned.push(plane);
  const back = new T.Mesh(plane, backMat), glow = new T.Mesh(plane, glowMat);
  back.frustumCulled = glow.frustumCulled = false; glow.position.z = 0.0003;
  back.scale.set(0.001, 0.001, 1); glow.scale.set(0.001, 0.001, 1);     // the unit plane must not inflate the camera-fit box while hidden
  mouth.add(back, glow); mouth.visible = false; root.add(mouth);

  let open = 0, blinkUntil = 0, nextBlink = 0, lid = 0;
  return {
    root, lids, mouth, glowMaterial: glowMat,
    mouthVisible: true,
    /** jaw: smoothed amplitude 0..1; mode: companion state; now: ms; shape: optional viseme {w,h} multipliers;
     *  lidBias: extra eyelid coverage from mood (positive droops/narrows, negative clamps to wide-open). */
    update(jaw, mode, now = performance.now(), shape = null, lidBias = 0) {
      const target = Math.max(0, (jaw || 0) - 0.03) / 0.97;
      open += (target - open) * 0.45;
      const ms = mouthSize(open, m);
      // A viseme shapes the mouth (wide/round/closed) while amplitude gates how open it is.
      const vw = shape ? shape.w : 1, vh = shape ? shape.h : 1;
      const visible = shape ? (open > 0.02 || vh > 0.3) : ms.visible;
      mouth.visible = visible;
      if (visible) { const w = ms.w * vw, h = ms.h * vh; back.scale.set(w + 0.003, h + 0.003, 1); glow.scale.set(w, h, 1); }
      // blink on a random 2.5-6 s timer, never while the eyes are deliberately narrowed past half
      if (!nextBlink) nextBlink = now + 2500;
      if (now >= nextBlink) { blinkUntil = now + BLINK_MS; nextBlink = now + 2500 + Math.random() * 3500; }
      const blink = now < blinkUntil ? Math.sin(Math.min(1, 1 - (blinkUntil - now) / BLINK_MS) * Math.PI) : 0;
      const base = Math.max(0, Math.min(1, (LID_BY_MODE[mode] ?? 0) + (lidBias || 0)));
      lid += (Math.max(base, blink) - lid) * (blink > 0 ? 0.7 : 0.25);
      for (const l of lids) { l.visible = lid > 0.02; l.scale.y = Math.max(0.0001, lid); }
    },
    dispose() { for (const o of owned) o.dispose?.(); headBone.remove(root); },
  };
}
