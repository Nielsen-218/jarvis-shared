/* J.A.R.V.I.S. dressing room: a real 3D avatar that wears real clothes.
 *
 * Body: the CC0 MakeHuman mesh (assets/avatar) with its morph targets, skin textures and the CC0 MPFB
 * skeleton (avatar-rig.js); hair, eyes, brows and lashes are the CC0 community assets fitted with their
 * MHCLO bindings. Clothing: garment meshes generated from the body surface itself (so they fit any body
 * shape and move with the same skeleton), then dressed with the person's own garment photo, projected in the
 * avatar's posed front view; the back gets a print-free version of the same fabric. Shoes take a side
 * projection, hats a front one, accessories their colour and finish.
 * Changing one garment rebuilds only that garment and the skin mask, never the whole avatar.
 */
import * as T from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {loadHuman} from './avatar-human.js';
import {createCharacterRig} from './avatar-rig.js';

/* ---------------------------------------------------------------- garment catalogue */
export const TEMPLATES = {
  tee:        {slot:'top',    label:'T-shirt',         sleeve:'short', offset:.017, fabric:'cotton'},
  longsleeve: {slot:'top',    label:'Long-sleeve',     sleeve:'long',  offset:.017, fabric:'cotton'},
  polo:       {slot:'top',    label:'Polo',            sleeve:'short', offset:.018, fabric:'pique', collar:'polo'},
  shirt:      {slot:'top',    label:'Button shirt',    sleeve:'long',  offset:.019, fabric:'cotton', collar:'shirt', placket:true},
  tank:       {slot:'top',    label:'Tank top',        sleeve:'none',  offset:.014, fabric:'cotton'},
  sweater:    {slot:'top',    label:'Sweater',         sleeve:'long',  offset:.024, fabric:'knit', loose:1.03},
  hoodie:     {slot:'top',    label:'Hoodie',          sleeve:'long',  offset:.028, fabric:'fleece', loose:1.05, hood:true, cuffs:true},
  jacket:     {slot:'outer',  label:'Jacket',          sleeve:'long',  offset:.046, fabric:'leather', open:true, collar:'jacket', hem:.505},
  blazer:     {slot:'outer',  label:'Blazer',          sleeve:'long',  offset:.042, fabric:'wool', open:true, collar:'lapel', hem:.47},
  coat:       {slot:'outer',  label:'Coat',            sleeve:'long',  offset:.052, fabric:'wool', open:true, collar:'lapel', hem:.36, loose:1.04},
  zip_hoodie: {slot:'outer',  label:'Zip hoodie',      sleeve:'long',  offset:.04,  fabric:'fleece', open:true, hood:true, loose:1.04, hem:.52},
  vest:       {slot:'outer',  label:'Vest',            sleeve:'none',  offset:.04,  fabric:'nylon', open:true, hem:.52},
  jeans:      {slot:'bottom', label:'Jeans',           leg:'long',   offset:.013, fabric:'denim', straight:1.06},
  trousers:   {slot:'bottom', label:'Trousers',        leg:'long',   offset:.014, fabric:'wool', straight:1.12},
  joggers:    {slot:'bottom', label:'Joggers',         leg:'long',   offset:.016, fabric:'fleece', straight:1.1, cuffs:true},
  shorts:     {slot:'bottom', label:'Shorts',          leg:'short',  offset:.016, fabric:'cotton', straight:1.14},
  skirt:      {slot:'bottom', label:'Skirt',           leg:'skirt',  offset:.016, fabric:'cotton'},
  leggings:   {slot:'bottom', label:'Leggings',        leg:'long',   offset:.007, fabric:'stretch'},
  dress:      {slot:'top',    label:'Dress',           sleeve:'short', offset:.017, fabric:'cotton', dress:true},
  sneakers:   {slot:'shoes',  label:'Sneakers',        top:.082, sole:.034, offset:.016, fabric:'canvas'},
  hightops:   {slot:'shoes',  label:'High-tops',       top:.13,  sole:.036, offset:.017, fabric:'leather'},
  boots:      {slot:'shoes',  label:'Boots',           top:.16,  sole:.04,  offset:.018, fabric:'leather'},
  dress_shoes:{slot:'shoes',  label:'Dress shoes',     top:.066, sole:.022, offset:.010, fabric:'polished'},
  sandals:    {slot:'shoes',  label:'Sandals',         top:.03,  sole:.028, offset:.006, fabric:'leather'},
  cap:        {slot:'hat',    label:'Cap',             fabric:'cotton'},
  beanie:     {slot:'hat',    label:'Beanie',          fabric:'knit'},
  watch:      {slot:'watch',  label:'Watch',           fabric:'metal'},
  chain:      {slot:'neck',   label:'Chain',           fabric:'metal'},
  glasses:    {slot:'eyes',   label:'Glasses',         fabric:'acetate'},
};
export const SLOTS = ['top','outer','bottom','shoes','hat','watch','neck','eyes'];
const FABRIC = { // roughness, clearcoat, sheen, normal strength, weave repeat
  cotton:[.9,0,.25,.35,26], pique:[.88,0,.3,.6,40], knit:[.96,0,.45,.8,18], fleece:[.97,0,.55,.5,22], denim:[.82,0,.1,.9,30],
  wool:[.86,0,.2,.45,34], leather:[.42,.35,0,.25,10], nylon:[.5,.15,0,.15,20], stretch:[.6,.05,.1,.2,30], canvas:[.75,0,.05,.5,24],
  polished:[.28,.6,0,.1,8], metal:[.25,0,0,0,1], acetate:[.3,.4,0,0,1],
};
const HAIR = ['short','messy','afro','bob','braid','ponytail','fade'];

/* ---------------------------------------------------------------- the room */
export async function createDressingRoom(canvas, {onStatus=()=>{}, mobile=matchMedia('(pointer:coarse)').matches}={}) {
  const renderer = new T.WebGLRenderer({canvas, antialias:true, alpha:true, preserveDrawingBuffer:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, mobile ? 2 : 2.5));
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.ACESFilmicToneMapping; renderer.toneMappingExposure = .92;
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = T.PCFSoftShadowMap;
  const scene = new T.Scene();
  const pmrem = new T.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), .04).texture; scene.environmentIntensity = .34;
  const camera = new T.PerspectiveCamera(28, 1, .05, 60);
  camera.position.set(0, 1.35, 7.6);
  const controls = new OrbitControls(camera, canvas);
  Object.assign(controls, {enableDamping:true, dampingFactor:.08, minDistance:1.1, maxDistance:11, rotateSpeed:.75, zoomSpeed:.9, panSpeed:.7,
    maxPolarAngle:Math.PI*.9, minPolarAngle:Math.PI*.08});
  controls.target.set(0, 1.22, 0);
  controls.touches = {ONE:T.TOUCH.ROTATE, TWO:T.TOUCH.DOLLY_PAN};

  // lights: soft key with shadow, cool rim, warm fill, sky/ground bounce
  scene.add(new T.HemisphereLight('#dfe9ff', '#3a2f2a', .3));
  const key = new T.DirectionalLight('#fff4e6', 1.2); key.position.set(2.6, 4.6, 3.4); key.castShadow = true;
  key.shadow.mapSize.set(mobile ? 1024 : 2048, mobile ? 1024 : 2048); key.shadow.camera.left = -1.6; key.shadow.camera.right = 1.6;
  key.shadow.camera.top = 2.8; key.shadow.camera.bottom = -.3; key.shadow.bias = -.0004; key.shadow.normalBias = .02; key.shadow.radius = 4;
  scene.add(key);
  const rim = new T.DirectionalLight('#9fd8ff', .95); rim.position.set(-3, 3.2, -3.4); scene.add(rim);
  const fill = new T.DirectionalLight('#ffd9c2', .4); fill.position.set(-3.2, 1.6, 2.4); scene.add(fill);
  // a soft, slightly warm light from the camera side: brings out face and skin form (dark skin reads flat without it)
  const front = new T.DirectionalLight('#ffe6d6', .45); front.position.set(.8, 2.3, 5); scene.add(front);

  // platform + contact shadow
  const stage = new T.Group(); scene.add(stage);
  const disc = new T.Mesh(new T.CylinderGeometry(1.25, 1.32, .07, 96), new T.MeshPhysicalMaterial({color:'#121a26', roughness:.55, metalness:.25, clearcoat:.3, clearcoatRoughness:.5}));
  disc.position.y = .035; disc.receiveShadow = true; stage.add(disc);
  const ring = new T.Mesh(new T.TorusGeometry(1.29, .008, 12, 160), new T.MeshBasicMaterial({color:'#4fc3f7', transparent:true, opacity:.75}));
  ring.rotation.x = Math.PI/2; ring.position.y = .07; stage.add(ring);
  const shadowPlane = new T.Mesh(new T.CircleGeometry(1.24, 64), new T.ShadowMaterial({opacity:.38}));
  shadowPlane.rotation.x = -Math.PI/2; shadowPlane.position.y = .0712; shadowPlane.receiveShadow = true; stage.add(shadowPlane);

  onStatus('Loading the 3D human…');
  const asset = await loadHuman(T);
  const avatar = new Avatar(asset, scene, renderer);

  // camera moves (views + zoom presets) animate smoothly
  let tween = null, autoSpin = false;
  function goTo(theta, phi, dist, targetY, ms = 650) {
    const sph = new T.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
    const from = {theta: sph.theta, phi: sph.phi, r: sph.radius, y: controls.target.y};
    let dTheta = theta - from.theta; dTheta = Math.atan2(Math.sin(dTheta), Math.cos(dTheta));
    tween = {from, to: {theta: from.theta + dTheta, phi, r: dist, y: targetY}, t0: performance.now(), ms};
  }
  const VIEWS = {front:0, back:Math.PI, left:-Math.PI/2, right:Math.PI/2, three:Math.PI/5};
  const FOCUS = {full:[Math.PI*.47, 7.4, 1.22], top:[Math.PI*.46, 3.4, 1.72], bottom:[Math.PI*.5, 3.9, .72], shoes:[Math.PI*.38, 2.0, .16],
                 face:[Math.PI*.48, 1.45, 2.16], hands:[Math.PI*.47, 2.0, 1.15]};
  let focus = 'full', view = 'front';
  function setView(v = view, f = focus, ms) {
    view = v; focus = f;
    const [phi, dist, y] = FOCUS[f] || FOCUS.full, theta = VIEWS[v] ?? 0;
    goTo(theta, phi, dist * (avatar.heightScale || 1), y * (avatar.heightScale || 1), ms);
  }

  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    renderer.setSize(r.width, r.height, false); camera.aspect = r.width / Math.max(1, r.height); camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(canvas.parentElement); resize();
  controls.addEventListener('start', () => { tween = null; });

  let last = performance.now();
  renderer.setAnimationLoop(now => {
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    if (tween) {
      const k = Math.min(1, (now - tween.t0) / tween.ms), e = k < .5 ? 4*k*k*k : 1 - Math.pow(-2*k + 2, 3)/2, a = tween.from, b = tween.to;
      const sph = new T.Spherical(a.r + (b.r - a.r)*e, a.phi + (b.phi - a.phi)*e, a.theta + (b.theta - a.theta)*e);
      controls.target.y = a.y + (b.y - a.y)*e;
      camera.position.setFromSpherical(sph).add(controls.target);
      if (k >= 1) tween = null;
    }
    if (autoSpin && !tween) { const s = new T.Spherical().setFromVector3(camera.position.clone().sub(controls.target)); s.theta += dt * .6;
      camera.position.setFromSpherical(s).add(controls.target); }
    controls.update();
    avatar.tick(now);
    ring.material.opacity = .55 + .2 * Math.sin(now * .0016);
    renderer.render(scene, camera);
  });

  return {
    avatar, renderer, camera, controls, scene,
    setView, view: () => view, focus: () => focus,
    spin(on) { autoSpin = on ?? !autoSpin; return autoSpin; },
    snapshot(w = 360) {
      renderer.render(scene, camera);
      const c = document.createElement('canvas'), src = renderer.domElement, h = Math.round(w * src.height / src.width);
      c.width = w; c.height = h; c.getContext('2d').drawImage(src, 0, 0, w, h); return c.toDataURL('image/webp', .82);
    },
  };
}

/* ---------------------------------------------------------------- the avatar */
export class Avatar {
  constructor(asset, scene, renderer) {
    this.asset = asset; this.scene = scene; this.renderer = renderer;
    this.root = new T.Group(); scene.add(this.root);
    this.cfg = null; this.worn = {}; this.meshes = {}; this.hides = {}; this.textures = new Map(); this.heightScale = 1;
  }

  /* ---- body: sex, shape, skin, hair, eyes, facial hair. A full rebuild; garments are re-fitted after. */
  async setBody(cfg) {
    const a = this.asset, pack = a.community;
    cfg = {sex:'m', skin:'#8d5a33', hair:'short', hairColor:'#161210', eyeColor:'#33241b', beard:'none', ...cfg,
           shape:{height:.5, muscle:.35, weight:.2, chest:.2, nose:.4, jaw:.5, lips:.4, chin:.5, cheek:.4, eyes:.4, ...(cfg?.shape||{})}};
    this.cfg = cfg;
    const sex = cfg.sex === 'f' ? 'female' : 'male', shape = {};
    for (const k in cfg.shape) shape[k] = Math.max(0, Math.min(1, Number(cfg.shape[k]) || 0));
    const v = a.positions.map((p, i) => p.map((x, j) => x + a.macros[sex][i][j]));
    for (const [key, val] of [[sex+'_muscle', shape.muscle], [sex+'_weight', shape.weight], ['chest', shape.chest], ['nose', shape.nose], ['jaw', shape.jaw],
                              ['lips', shape.lips*.55], ['chin', shape.chin*.65], ['cheek', shape.cheek*.65], ['eyes', shape.eyes*.4]])
      for (const [i, x, y, z] of a.morphs[key]) { v[i][0] += x*val; v[i][1] += y*val; v[i][2] += z*val; }
    const used = [...new Set(a.triangles)], floor = Math.min(...used.map(i => v[i][1])), ceil = Math.max(...used.map(i => v[i][1]));
    const H = 2.34 + (shape.height - .5) * .28, scale = H / (ceil - floor);
    this.heightScale = H / 2.34;
    const point = p => new T.Vector3(p[0]*scale, (p[1] - floor)*scale + .08, p[2]*scale);
    // dispose the old body
    for (const o of [...this.root.children]) { this.root.remove(o); o.geometry?.dispose(); }
    this.rig?.dispose?.();
    this.raw = v; this.H = H; this.point = point; this.scale = scale; this.floor = floor;
    this.body = v.map(point);
    this.hn = this.body.map(p => (p.y - .08) / H);               // 0 at the soles, 1 at the crown
    this.used = new Set(used);
    if (!this.adj) { this.adj = this.body.map(() => new Set());
      for (let t = 0; t < a.triangles.length; t += 3) { const [i, j, k] = a.triangles.slice(t, t + 3); this.adj[i].add(j).add(k); this.adj[j].add(i).add(k); this.adj[k].add(i).add(j); }
      this.adj = this.adj.map(s => [...s]); }
    this.rig = createCharacterRig(T, a, this.body, this.root);
    this.boneIndex = Object.fromEntries(this.rig.bones.map((b, i) => [b.name, i]));
    const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(this.body.flatMap(p => p.toArray()), 3));
    g.setIndex(a.triangles); g.computeVertexNormals();
    const n = g.getAttribute('normal'); this.normals = this.body.map((_, i) => new T.Vector3(n.getX(i), n.getY(i), n.getZ(i))); g.dispose();
    this.fitCache = new WeakMap();
    // skin
    const tint = new T.Color(cfg.skin), ref = new T.Color('#8d5a33'); tint.r /= ref.r; tint.g /= ref.g; tint.b /= ref.b;
    this.skinMaterial = new T.MeshPhysicalMaterial({map: pack.textures['skin_' + sex], normalMap: pack.textures.skin_detail, normalScale: new T.Vector2(.12, .12),
      color: tint, roughness: .5, metalness: 0, specularIntensity: .55, specularColor: new T.Color('#ffe8dc'), sheen: .16, sheenColor: new T.Color('#c48a72')});
    // eyes, brows, lashes, hair (CC0 community, MHCLO-fitted)
    const eyeKey = ({'#8a6030':'eyes_brownlight', '#315a41':'eyes_green', '#36576e':'eyes_deepblue', '#686972':'eyes_grey'})[cfg.eyeColor] || 'eyes';
    this.addFitted('eyes', new T.MeshPhysicalMaterial({map: pack.textures[eyeKey], color:'#c4beb8', alphaTest:.05, roughness:.25, clearcoat:.35, clearcoatRoughness:.15}));
    this.addFitted('brows', new T.MeshStandardMaterial({map: pack.textures.brows_diffuse, color: new T.Color(cfg.hairColor).lerp(new T.Color('#fff'), .25),
      alphaTest:.03, transparent:true, depthWrite:false, roughness:.9, side:T.DoubleSide}));
    this.addFitted('lashes', new T.MeshStandardMaterial({map: pack.textures.lashes_diffuse, alphaTest:.03, transparent:true, depthWrite:false, roughness:.9, side:T.DoubleSide}));
    this.hairHide = new Set();
    if (cfg.hair !== 'bald' && HAIR.includes(cfg.hair)) {
      const mesh = cfg.hair === 'fade' ? 'afro' : cfg.hair, finished = ['messy','bob'].includes(cfg.hair);
      const m = new T.MeshPhysicalMaterial({map: pack.textures[mesh + '_diffuse'], normalMap: pack.textures[mesh + '_normal'] || null, alphaTest:.45,
        side:T.DoubleSide, roughness: finished ? .52 : .88, specularIntensity: finished ? .5 : .28, sheen: finished ? .22 : .14,
        color: new T.Color(cfg.hairColor).lerp(new T.Color('#fff'), finished ? .03 : .3)});
      this.addFitted(mesh, m, 'hair', cfg.hair === 'fade' ? pos => this.closeCrop(pos) : null);
      if (cfg.hair !== 'fade') pack.meshes[mesh].hide.forEach(i => this.hairHide.add(i));
    }
    if (cfg.beard && cfg.beard !== 'none') this.addBeard(cfg.beard, cfg.hairColor);
    this.axes = null; this.byName = null; this.pose(0); this.root.updateMatrixWorld(true); this.rig.skeleton.update();
    // re-fit what was being worn
    const worn = this.worn; this.worn = {}; this.meshes = {}; this.hides = {};
    for (const [slot, item] of Object.entries(worn)) if (item && !item.under) await this.wearOne(slot, item, {skin:false});
    if (this.worn.top && this.worn.outer) await this.wearOne('top', this.worn.top, {skin:false});
    await this.modesty();
    this.updateSkin();
  }

  fitted(mesh) {
    if (this.fitCache.has(mesh)) return this.fitCache.get(mesh);
    const v = this.raw, f = ['x','y','z'].map((ax, i) => { const s = mesh.scales[ax]; return s ? Math.abs(v[s[0]][i] - v[s[1]][i]) / s[2] : 1; });
    const ctrl = mesh.refs.map(r => { const raw = [0,0,0]; for (let k = 0; k < 3; k++) for (let j = 0; j < 3; j++) raw[j] += v[r[k]][j] * r[3+k];
      for (let j = 0; j < 3; j++) raw[j] += r[6+j] * f[j]; return this.point(raw); });
    const out = mesh.subdivision ? mesh.subdivision.map(row => { const p = new T.Vector3(); for (const [i, w] of row) p.addScaledVector(ctrl[i], w); return p; }) : ctrl;
    this.fitCache.set(mesh, out); return out;
  }
  addFitted(key, material, name = key, reshape = null) {
    const mesh = this.asset.community.meshes[key]; if (!mesh) return null;
    let pos = this.fitted(mesh); if (reshape) pos = reshape(pos);
    const weights = mesh.subdivision ? mesh.subdivision.map(r => this.rig.combine(mesh.refs.map(this.rig.blendWeights), r)) : mesh.refs.map(this.rig.blendWeights);
    const g = expand(pos, null, mesh.triangles, weights, mesh.uvs, mesh.triangleUVs);
    const m = this.rig.mesh(g, material); m.name = name; m.castShadow = true; this.root.add(m); return m;
  }

  /* a close crop: the afro pulled in to just above the scalp (each strand towards its nearest scalp point) */
  closeCrop(pos) {
    const scalp = []; for (const i of this.used) if (this.hn[i] > .9) scalp.push(i);
    return pos.map(p => {
      let best = scalp[0], bd = 1e9; for (const i of scalp) { const q = this.body[i], d = (q.x - p.x) ** 2 + (q.y - p.y) ** 2 + (q.z - p.z) ** 2; if (d < bd) { bd = d; best = i; } }
      const q = this.body[best], out = q.clone().lerp(p, .22); return out.addScaledVector(this.normals[best], .006 * this.heightScale);
    });
  }

  /* facial hair: a thin shell over the jaw, chin and upper lip with a hair-strand alpha */
  addBeard(kind, color) {
    const a = this.asset, L = key => centroid(a.landmarks[key].map(i => this.body[i]));
    const mouth = L('joint-mouth'), jaw = L('joint-jaw'), head = L('joint-head');
    const full = kind === 'beard', depth = full ? .011 : kind === 'goatee' ? .006 : .0025;
    const keep = i => {
      const p = this.body[i]; if (this.hn[i] < .86 || p.z < head.z - .02) return false;
      const dy = p.y - mouth.y, dx = Math.abs(p.x - mouth.x);
      if (kind === 'goatee') return dy < .045 && dy > -.11 && dx < .065 && !(Math.abs(dy) < .016 && dx < .04);
      if (Math.abs(dy) < .013 && dx < .036 && p.z > mouth.z - .012) return false;        // the lips stay bare
      return dy < .05 && p.y > jaw.y - (full ? .09 : .05) && (dy < .02 || dx > .055 || (dy < .045 && dx < .045));
    };
    const tris = []; for (let t = 0; t < a.triangles.length; t += 3) { const ids = a.triangles.slice(t, t+3); if (ids.every(keep)) tris.push(...ids); }
    const pos = this.body.map((p, i) => p.clone().addScaledVector(this.normals[i], depth));
    const g = expand(pos, this.normals, tris, this.rig.baseWeights);
    const tex = strandTexture(full ? .95 : kind === 'stubble' ? .55 : .85);
    const uv = []; const P = g.getAttribute('position'); for (let i = 0; i < P.count; i++) uv.push(P.getX(i) * 14, P.getY(i) * 14);
    g.setAttribute('uv', new T.Float32BufferAttribute(uv, 2));
    const m = this.rig.mesh(g, new T.MeshStandardMaterial({color, alphaMap: tex, transparent: true, depthWrite: false, roughness: .95,
      opacity: kind === 'stubble' ? .55 : .92}));
    m.name = 'facial hair'; this.root.add(m);
  }

  /* ---- clothing ---- */
  async wear(slot, item, {skin = true} = {}) {
    const hadOuter = !!this.worn.outer, meshes = await this.wearOne(slot, item, {skin: false});
    // the top is cut a little closer while a jacket is over it: refit it when the jacket comes or goes
    if (slot === 'outer' && this.worn.top && hadOuter !== !!this.worn.outer) await this.wearOne('top', this.worn.top, {skin: false});
    await this.modesty();
    if (skin) this.updateSkin();
    return meshes;
  }
  /* never fully undressed: plain dark underwear fills an empty bottom (and, for women, top) slot */
  async modesty() {
    const dress = this.worn.top && TEMPLATES[this.worn.top.template]?.dress;
    if (!this.worn.bottom && !dress) await this.wearOne('bottom', {template: 'shorts', colors: ['#2a2e35'], hem: .405, under: true}, {skin: false});
    if (this.cfg?.sex === 'f' && !this.worn.top) await this.wearOne('top', {template: 'tank', colors: ['#2a2e35'], under: true}, {skin: false});
  }
  async wearOne(slot, item, {skin = true} = {}) {
    for (const m of this.meshes[slot] || []) { this.root.remove(m); m.geometry.dispose(); }
    delete this.meshes[slot];
    if (!item) { delete this.worn[slot]; this.hides[slot] = null; if (skin) this.updateSkin(); return; }
    const tpl = TEMPLATES[item.template] || TEMPLATES.tee;
    this.worn[slot] = item;
    if (tpl.dress && slot === 'top' && this.worn.bottom) { for (const m of this.meshes.bottom || []) this.root.remove(m); delete this.meshes.bottom; delete this.worn.bottom; this.hides.bottom = null; }
    const look = await this.lookFor(item, tpl);
    let meshes;
    if (['top', 'outer', 'bottom', 'shoes'].includes(tpl.slot)) meshes = this.buildAuthored(item, tpl, look, slot);
    else if (tpl.slot === 'hat') meshes = this.buildHat(item, tpl, look);
    else meshes = this.buildAccessory(item, tpl, look);
    this.meshes[slot] = meshes;
    if (skin) this.updateSkin();
    return meshes;
  }

  /* skin under clothing is removed (authored garments carry the exact list of body vertices they cover) */
  updateSkin() {
    if (this.skinMesh) { this.root.remove(this.skinMesh); this.skinMesh.geometry.dispose(); }
    const a = this.asset, covered = new Set(this.hairHide);
    for (const h of Object.values(this.hides || {})) if (h) for (const i of h) covered.add(i);
    const idx = [], uvIdx = [];
    for (let t = 0; t < a.triangles.length; t += 3) {
      const ids = a.triangles.slice(t, t + 3); if (ids.every(i => covered.has(i))) continue;
      idx.push(...ids); uvIdx.push(...a.triangleUVs.slice(t, t + 3));
    }
    const g = expand(this.body, this.normals, idx, this.rig.baseWeights, a.uvs, uvIdx);
    this.skinMesh = this.rig.mesh(g, this.skinMaterial); this.skinMesh.name = 'skin'; this.skinMesh.castShadow = true; this.root.add(this.skinMesh);
  }

  /* -- clothing from the CC0 MakeHuman garment meshes, fitted by their MHCLO bindings; variants are derived
        (inflated, cropped, opened) so every garment keeps authored folds, seams and collars -- */
  buildAuthored(item, tpl, look, slot) {
    const sex = this.cfg.sex === 'f' ? 'female' : 'male';
    let [key, part, inflate = 0] = BASE[item.template] || BASE.tee;
    if (key === '$bottom') key = sex + '_outfit';
    const pack = this.asset.community, mesh = pack.meshes[key], H = this.H;
    let pos = this.fitted(mesh);
    const weights = mesh.subdivision ? mesh.subdivision.map(r => this.rig.combine(mesh.refs.map(this.rig.blendWeights), r)) : mesh.refs.map(this.rig.blendWeights);
    if (tpl.slot === 'top' && this.worn.outer) inflate -= .006;   // a top under a jacket sits a little closer, so it never pokes through
    if (inflate) pos = inflated(pos, mesh.triangles, inflate * this.heightScale);
    // knitwear and jackets hang over the waistband instead of tucking in: flare the lower torso out and end the hem there
    const overHem = !!ISLANDS[key] && ['sweater', 'hoodie', 'zip_hoodie', 'jacket', 'vest', 'longsleeve'].includes(item.template);
    if (overHem) {   // the shirt is cut to tuck in: lengthen the lower torso so the hem reaches over the waistband
      const ids = new Set(), isl = ISLANDS[key];
      for (let t = 0; t < mesh.triangles.length; t += 3) { const tu = mesh.triangleUVs, c = [0, 1].map(j => (mesh.uvs[tu[t]][j] + mesh.uvs[tu[t + 1]][j] + mesh.uvs[tu[t + 2]][j]) / 3);
        if (isl(c[0], c[1]) === 'top') for (let k = 0; k < 3; k++) ids.add(mesh.triangles[t + k]); }
      let hmin = 1; for (const i of ids) hmin = Math.min(hmin, (pos[i].y - .08) / H);
      const want = ['jacket', 'vest'].includes(item.template) ? HEM_ABOVE_BELT - .015 : .488, top = .64;
      if (hmin > want && hmin < top) { const k = (top - want) / (top - hmin);
        pos = pos.map((p, i) => { const h = (p.y - .08) / H; return ids.has(i) && h < top ? new T.Vector3(p.x, .08 + (top - (top - h) * k) * H, p.z) : p; }); }
      pos = flared(pos, mesh.triangles, H, .6, .5, .026 * this.heightScale);
    }
    const heights = pos.map(p => (p.y - .08) / H), clips = [];
    if (overHem) clips.push({bound: item.template === 'jacket' || item.template === 'vest' ? HEM_ABOVE_BELT - .012 : .5, sign: 1});
    // open jackets: a smooth cut down the centre front (clipped like the hems, so the edge is clean, not torn)
    const openHalf = ['jacket', 'vest'].includes(item.template) ? .042 * this.heightScale : 0;
    const opening = openHalf ? pos.map(p => p.z > .02 && (p.y - .08) / H > .52 ? Math.abs(p.x) : 1) : null;
    if (openHalf) clips.push({bound: openHalf, sign: 1, chan: 9});
    // tops made from the casual outfit stop just above its belt (the belt belongs to the trousers, not the shirt)
    // the casual outfits are split by their texture islands (shirt / jeans+belt / shoes), so a shirt never carries the belt;
    // a jacket made from the shirt still stops above the belt and hangs over the trousers
    const islands = ISLANDS[key], hem = key === 'blazer' ? .505 : islands ? -1 : .53;
    if (part === 'top' && hem > 0) clips.push({bound: hem, sign: 1});
    if (part === 'bottom' && !islands) clips.push({bound: .535, sign: -1});
    if (item.template === 'shorts') clips.push({bound: item.hem || .3, sign: 1});
    if (['hoodie', 'sweater', 'zip_hoodie'].includes(item.template)) clips.push({bound: .836, sign: -1});   // no shirt collar on knitwear
    if (['joggers', 'leggings'].includes(item.template)) clips.push({bound: .516, sign: -1});               // no belt: elastic waist instead
    if (tpl.slot === 'shoes') clips.push({bound: .075, sign: -1});
    let extra = opening;
    if (tpl.slot === 'top' && this.worn.outer) {   // under a jacket the top only exists where it can be seen: the open front and the neckline
      const ot = this.worn.outer.template, show = ['jacket', 'vest'].includes(ot) ? .042 * this.heightScale * 1.6 : .0001;
      extra = pos.map(p => (p.y - .08) / H > .845 && Math.abs(p.x) < .05 * H ? 0 : p.z > 0 ? Math.abs(p.x) : 1);
      clips.push({bound: show, sign: -1, chan: 9});
    }
    let tris = mesh.triangles, triUV = mesh.triangleUVs;
    if (islands && part) {
      const keep = []; for (let t = 0; t < tris.length; t += 3) { const c = [0, 1].map(j => (mesh.uvs[triUV[t]][j] + mesh.uvs[triUV[t + 1]][j] + mesh.uvs[triUV[t + 2]][j]) / 3);
        if (islands(c[0], c[1]) === part) keep.push(t); }
      tris = keep.flatMap(t => [tris[t], tris[t + 1], tris[t + 2]]); triUV = keep.flatMap(t => [triUV[t], triUV[t + 1], triUV[t + 2]]);
    }
    const g = expandClipped(pos, tris, weights, mesh.uvs, triUV, heights, clips, this.rig, extra);
    if (item.template === 'vest') dropSleeves(g, .118 * H);
    // body under it: the mesh's own cover list, limited to the part we kept
    const hn = this.hn, floor = part === 'top' ? (hem > 0 ? hem : .53) : -1, ceil = part === 'bottom' ? .535 : 2, low = item.template === 'shorts' ? (item.hem || .3) + .02 : tpl.slot === 'shoes' ? -1 : -1;
    this.hides[slot] = new Set(mesh.hide.filter(i => hn[i] >= floor && hn[i] <= (tpl.slot === 'shoes' ? .1 : ceil) && hn[i] >= low &&
      !(openHalf && this.body[i].z > 0 && Math.abs(this.body[i].x) < openHalf * .9 && hn[i] > .55)));
    // knitwear made from the shirt mesh must not show the shirt's buttons and placket: plain knit surface instead
    const matKey = ['hoodie', 'sweater', 'zip_hoodie'].includes(item.template) ? null : key;
    const out = [this.dress(g, {...look, sleeve: ['top', 'outer'].includes(tpl.slot) ? tpl.sleeve : null, open: !!openHalf}, tpl, tpl.slot === 'shoes' ? 'side' : 'front', mesh, matKey)];
    if (tpl.hood) out.push(this.hood(look, tpl));
    if (tpl.cuffs && part === 'top' && !(tpl.slot === 'top' && this.worn.outer)) out.push(...this.cuffs(look, tpl, true));
    if (tpl.cuffs && part === 'bottom') out.push(...this.legCuffs(look, tpl));
    if (['joggers', 'leggings'].includes(item.template)) out.push(this.waistband(look, tpl, .518));
    if (tpl.slot === 'shoes') {
      if (['sneakers', 'hightops'].includes(item.template)) for (const side of [-1, 1]) out.push(this.sole(side, tpl, look));
      if (['hightops', 'boots'].includes(item.template)) out.push(this.shaft(look, tpl));
    }
    if (item.template === 'dress' || item.template === 'skirt') out.push(this.skirt(look, tpl, item.template === 'dress'));
    return out;
  }

  /* a sneaker midsole: the foot outline, a little wider, wrapped round the base of the shoe */
  sole(side, tpl, look) {
    const b = this.body, hn = this.hn, foot = [];
    for (const i of this.used) if (hn[i] < .025 && b[i].x * side > 0) foot.push(i);
    const hull = convexHull(foot.map(i => new T.Vector2(b[i].x, b[i].z))), cx = centroid2(hull);
    const ring = hull.map(p => { const d = p.clone().sub(cx), l = d.length() || 1; return cx.clone().add(d.multiplyScalar((l + .011) / l)); });
    const shape = new T.Shape(new T.SplineCurve([...ring, ring[0], ring[1]]).getPoints(72).slice(0, -2));   // a smooth sole outline, not a polygon
    const geo = new T.ExtrudeGeometry(shape, {depth: tpl.sole * .5, bevelEnabled: true, bevelSize: .006, bevelThickness: .006, bevelSegments: 4, curveSegments: 10});
    geo.rotateX(Math.PI / 2); geo.translate(0, .08 + tpl.sole * .5 + .004, 0);
    const P = geo.getAttribute('position'), si = [], sw = [];
    for (let k = 0; k < P.count; k++) {
      let best = foot[0], bd = 1e9; for (const i of foot) { const d = (b[i].x - P.getX(k)) ** 2 + (b[i].z - P.getZ(k)) ** 2; if (d < bd) { bd = d; best = i; } }
      const [ids, w] = this.rig.baseWeights[best]; si.push(...ids); sw.push(...w);
    }
    geo.setAttribute('skinIndex', new T.Uint16BufferAttribute(si, 4)); geo.setAttribute('skinWeight', new T.Float32BufferAttribute(sw, 4));
    const m = this.rig.mesh(geo, new T.MeshPhysicalMaterial({color: look.sole, roughness: .7, clearcoat: .15, sheen: .2}));
    m.castShadow = true; m.receiveShadow = true; m.name = 'sole'; this.root.add(m); return m;
  }
  /* high-top / boot shaft: a smooth shell round the ankle */
  shaft(look, tpl) {
    const top = tpl.top, regions = [[[1, .055, 1], [1, top, -1], [0, -.2 * this.H, 1], [0, .2 * this.H, -1]]];
    const pos = this.body.map((p, i) => p.clone().addScaledVector(this.normals[i], .02 * this.heightScale));
    const tris = this.trianglesIn(regions);
    const g = clipBuild(tris, this.body, this.hn, this.smoothShell(pos, tris, 10, .016), this.normals, this.rig, regions, this.H);
    return this.dress(g, {...look, map: null}, tpl, null);
  }
  /* skirts and dress skirts: a flared surface from the waist, following the hips */
  skirt(look, tpl, dress) {
    const ring = []; for (const i of this.used) if (Math.abs(this.hn[i] - .47) < .01 && Math.abs(this.body[i].x) < .18 * this.H) ring.push(this.body[i]);
    const c = centroid(ring), rx = Math.max(...ring.map(p => Math.abs(p.x - c.x))) + .03, H = this.H;
    const pts = [new T.Vector2(rx * .92, .54 * H), new T.Vector2(rx, .47 * H), new T.Vector2(rx * 1.12, .4 * H), new T.Vector2(rx * 1.28, (dress ? .3 : .34) * H)];
    const g = new T.LatheGeometry(pts, 64); g.scale(1, 1, .78); g.translate(c.x, .08, c.z);
    return this.rigid(g, this.boneIndex.pelvis, look, tpl, 'front');
  }

  /* -- hats: a fitted crown (+ brim for caps) that follows the head bone -- */
  buildHat(item, tpl, look) {
    const b = this.body, hn = this.hn, top = []; for (const i of this.used) if (hn[i] > .93) top.push(b[i]);
    const c = centroid(top), r = Math.max(...top.map(p => Math.hypot(p.x - c.x, p.z - c.z))) + .012, crownY = Math.max(...top.map(p => p.y));
    const group = [], head = this.boneIndex.head;
    const crown = new T.SphereGeometry(r, 48, 24, 0, Math.PI * 2, 0, Math.PI * (tpl === TEMPLATES.beanie ? .56 : .5));
    crown.scale(1, tpl === TEMPLATES.beanie ? 1.02 : .82, 1.08); crown.translate(c.x, crownY - r * (tpl === TEMPLATES.beanie ? .78 : .72) + .012, c.z - .01);
    group.push(this.rigid(crown, head, look, tpl, 'front'));
    if (tpl === TEMPLATES.cap) {
      const brim = new T.CylinderGeometry(r * .95, r * .95, .012, 40, 1, false, -Math.PI * .42, Math.PI * .84);
      brim.scale(1, 1, 1.25); brim.translate(c.x, crownY - r * .72 + .015, c.z + r * .55);
      group.push(this.rigid(brim, head, {...look, map: null}, tpl, null));
    } else {
      const fold = new T.TorusGeometry(r * .995, .018, 10, 48); fold.rotateX(Math.PI / 2); fold.scale(1, 1, 1.08); fold.translate(c.x, crownY - r * .78 + .03, c.z - .01);
      group.push(this.rigid(fold, head, {...look, map: null}, tpl, null));
    }
    return group;
  }

  /* -- watch, chain, glasses -- */
  buildAccessory(item, tpl, look) {
    const L = key => centroid(this.asset.landmarks[key].map(i => this.body[i]));
    const metal = {color: look.color, metalness: /gold|silver|metal|steel|chrome/.test(look.finish || '') || tpl.fabric === 'metal' ? .9 : .1};
    if (tpl === TEMPLATES.watch) {
      const hand = L('joint-l-hand'), elbow = L('joint-l-elbow'), dir = hand.clone().sub(elbow).normalize(), at = hand.clone().addScaledVector(dir, -.05);
      const band = new T.TorusGeometry(.036, .009, 10, 32); const face = new T.CylinderGeometry(.02, .02, .012, 32); face.rotateZ(Math.PI / 2);
      face.translate(.04, 0, 0);
      const q = new T.Quaternion().setFromUnitVectors(new T.Vector3(0, 0, 1), dir);
      for (const g of [band, face]) { g.applyQuaternion(q); g.translate(at.x, at.y, at.z); }
      return [this.rigid(band, this.boneIndex.lowerarm_l, {...look, ...metal, map: null}, tpl, null),
              this.rigid(face, this.boneIndex.lowerarm_l, {color: '#0d0f12', metalness: .3, map: null}, tpl, null)];
    }
    if (tpl === TEMPLATES.chain) {
      const neck = L('joint-neck'), g = new T.TorusGeometry(.105 * this.heightScale, .006, 8, 64); g.rotateX(Math.PI / 2 - .35); g.scale(1, 1, .92);
      g.translate(neck.x, neck.y - .07, neck.z + .02);
      return [this.rigid(g, this.boneIndex.spine_03, {...look, ...metal, metalness: .95, map: null}, tpl, null)];
    }
    const le = L('joint-l-eye'), re = L('joint-r-eye'), out = [];
    for (const e of [le, re]) { const rimG = new T.TorusGeometry(.024, .0035, 8, 32); rimG.translate(e.x, e.y, e.z + .03); out.push(this.rigid(rimG, this.boneIndex.head, {...look, map: null}, tpl, null)); }
    const bridge = new T.CylinderGeometry(.003, .003, Math.abs(le.x - re.x) - .048, 8); bridge.rotateZ(Math.PI / 2); bridge.translate((le.x + re.x) / 2, le.y + .006, le.z + .03);
    out.push(this.rigid(bridge, this.boneIndex.head, {...look, map: null}, tpl, null));
    for (const [e, s] of [[le, 1], [re, -1]]) { const arm = new T.BoxGeometry(.004, .004, .11); arm.translate(e.x + s * .026, e.y + .004, e.z - .025); out.push(this.rigid(arm, this.boneIndex.head, {...look, map: null}, tpl, null)); }
    const lens = new T.MeshPhysicalMaterial({color: '#1a2230', transmission: .2, roughness: .05, metalness: .2, transparent: true, opacity: .55});
    for (const e of [le, re]) { const g = new T.CircleGeometry(.022, 24); g.translate(e.x, e.y, e.z + .03); const m = this.rigidMesh(g, this.boneIndex.head, lens); out.push(m); }
    return out;
  }

  /* -- details: hood, collar, cuffs, waistband -- */
  hood(look, tpl) {
    const neck = centroid(this.asset.landmarks['joint-neck'].map(i => this.body[i])), s = this.heightScale;
    const g = new T.SphereGeometry(.13 * s, 32, 16, Math.PI * .15, Math.PI * .7, Math.PI * .35, Math.PI * .5);
    g.rotateY(Math.PI); g.scale(1.05, .7, .55); g.translate(neck.x, neck.y + .02, neck.z - .11 * s);
    return this.rigid(g, this.boneIndex.spine_03, {...look, map: null}, tpl, null);
  }
  collar(look, tpl) {
    const neck = centroid(this.asset.landmarks['joint-neck'].map(i => this.body[i])), s = this.heightScale;
    const r = tpl.collar === 'lapel' ? .085 : tpl.collar === 'jacket' ? .078 : .068, tube = tpl.collar === 'jacket' ? .022 : .011;
    const g = new T.TorusGeometry(r * s, tube, 10, 48, tpl.collar === 'lapel' || tpl.collar === 'jacket' ? Math.PI * 1.55 : Math.PI * 2);
    g.rotateX(Math.PI / 2); if (tpl.collar === 'lapel' || tpl.collar === 'jacket') g.rotateY(Math.PI * .225); g.scale(1, 1.6, 1.12);
    g.translate(neck.x, neck.y - .025, neck.z + .005);
    return this.rigid(g, this.boneIndex.neck_01, {...look, map: null}, tpl, null);
  }
  cuffs(look, tpl, sleeveTo) {
    if (!sleeveTo) return [];
    const out = [];
    for (const side of ['l', 'r']) {
      const hand = centroid(this.asset.landmarks['joint-' + side + '-hand'].map(i => this.body[i])), elbow = centroid(this.asset.landmarks['joint-' + side + '-elbow'].map(i => this.body[i]));
      const dir = hand.clone().sub(elbow).normalize(), at = hand.clone().addScaledVector(dir, -.035), g = new T.TorusGeometry(.04 * this.heightScale, .012, 8, 28);
      g.applyQuaternion(new T.Quaternion().setFromUnitVectors(new T.Vector3(0, 0, 1), dir)); g.translate(at.x, at.y, at.z);
      out.push(this.rigid(g, this.boneIndex['lowerarm_' + side], {...look, map: null}, tpl, null));
    }
    return out;
  }
  legCuffs(look, tpl) {
    const out = [];
    for (const side of ['l', 'r']) {
      const ank = centroid(this.asset.landmarks['joint-' + side + '-ankle'].map(i => this.body[i])), g = new T.TorusGeometry(.055 * this.heightScale, .016, 8, 28);
      g.rotateX(Math.PI / 2); g.translate(ank.x, ank.y + .035, ank.z - .005);
      out.push(this.rigid(g, this.boneIndex['calf_' + side], {...look, map: null}, tpl, null));
    }
    return out;
  }
  neckRib(look, tpl) {
    const ring = []; for (const i of this.used) if (Math.abs(this.hn[i] - .818) < .006 && Math.abs(this.body[i].x) < .09 * this.H) ring.push(this.body[i]);
    const c = centroid(ring), rx = Math.max(...ring.map(p => Math.abs(p.x - c.x))) + .022, rz = Math.max(...ring.map(p => Math.abs(p.z - c.z))) + .022;
    const g = new T.TorusGeometry(1, .016, 10, 64); g.rotateX(Math.PI / 2); g.scale(rx, 1.6, rz); g.translate(c.x, c.y + .004, c.z + .004);
    return this.rigid(g, this.boneIndex.neck_01, {...look, map: null, color: look.trim || look.color}, tpl, null);
  }
  waistband(look, tpl, at = .534) {
    const ring = []; for (const i of this.used) if (Math.abs(this.hn[i] - at) < .006 && Math.abs(this.body[i].x) < .17 * this.H) ring.push(this.body[i]);
    const c = centroid(ring), rx = Math.max(...ring.map(p => Math.abs(p.x - c.x))) + (tpl.offset || .014) + .01, rz = Math.max(...ring.map(p => Math.abs(p.z - c.z))) + (tpl.offset || .014) + .01;
    const g = new T.TorusGeometry(1, .02, 10, 64); g.rotateX(Math.PI / 2); g.scale(rx, 1, rz); g.translate(c.x, c.y + .006, c.z);
    return this.rigid(g, this.boneIndex.pelvis, {...look, map: null, color: look.trim || look.color}, tpl, null);
  }

  /* cloth, not skin: Laplacian-smooth the garment surface (no muscles, navel or toes through fabric), then push it back
     out so it never sinks closer to the body than its thickness */
  smoothShell(pos, tris, iters, off, keepDown = false) {
    const ids = [...new Set(tris)], inSet = new Set(ids), b = this.body, n = this.normals;
    let cur = pos;
    for (let it = 0; it < iters; it++) {
      const next = cur.slice();
      for (const i of ids) { const nb = this.adj[i].filter(j => inSet.has(j)); if (nb.length < 2) continue;
        const avg = new T.Vector3(); for (const j of nb) avg.add(cur[j]); avg.divideScalar(nb.length);
        next[i] = cur[i].clone().lerp(avg, .6); }
      cur = next;
    }
    for (const i of ids) { const d = cur[i].clone().sub(b[i]).dot(n[i]); if (d < off) cur[i] = cur[i].clone().addScaledVector(n[i], off - d);
      if (keepDown && this.hn[i] < .012) cur[i].y = b[i].y; }
    return cur;
  }

  /* triangles of the body inside any region (a region = list of [axis, bound, sign] half-spaces; axis 1 is height 0..1) */
  trianglesIn(regions) {
    const a = this.asset, b = this.body, hn = this.hn, out = [];
    const inside = (i, r) => r.every(([ax, bound, s]) => ((ax === 1 ? hn[i] : b[i].getComponent(ax)) - bound) * s >= 0);
    for (let t = 0; t < a.triangles.length; t += 3) {
      const ids = a.triangles.slice(t, t + 3);
      if (regions.some(r => ids.some(i => inside(i, r)))) out.push(...ids);
    }
    return out;
  }

  /* a rigid part (hat, collar, watch…) bound 100% to one bone */
  rigid(geo, bone, look, tpl, projection) {
    const n = geo.getAttribute('position').count, si = new Uint16Array(n * 4), sw = new Float32Array(n * 4);
    for (let i = 0; i < n; i++) { si[i * 4] = bone; sw[i * 4] = 1; }
    geo.setAttribute('skinIndex', new T.BufferAttribute(si, 4)); geo.setAttribute('skinWeight', new T.BufferAttribute(sw, 4));
    if (!geo.getAttribute('normal')) geo.computeVertexNormals();
    return projection ? this.dress(geo, look, tpl, projection) : this.rigidMesh(geo, bone, this.material(look, tpl, null));
  }
  rigidMesh(geo, bone, material) {
    if (!geo.getAttribute('skinIndex')) {
      const n = geo.getAttribute('position').count, si = new Uint16Array(n * 4), sw = new Float32Array(n * 4);
      for (let i = 0; i < n; i++) { si[i * 4] = bone; sw[i * 4] = 1; }
      geo.setAttribute('skinIndex', new T.BufferAttribute(si, 4)); geo.setAttribute('skinWeight', new T.BufferAttribute(sw, 4));
    }
    const m = this.rig.mesh(geo, material); m.castShadow = true; this.root.add(m); return m;
  }

  /* ---- appearance: the garment photo projected over authored fabric detail ---- */
  async lookFor(item, tpl) {
    const colors = (item.colors && item.colors.length ? item.colors : ['#7a7f87']).map(c => new T.Color(c));
    const look = {color: '#' + colors[0].getHexString(), trim: colors[1] ? '#' + colors[1].getHexString() : null,
      sole: item.sole || '#f1efe9', finish: item.finish || '', map: null, bbox: item.bbox || [0, 0, 1, 1], toe: item.toe || 'left'};
    if (item.texture) { try { look.map = await this.atlas(item.texture, look.color); } catch (e) { console.warn('texture', e); } }
    return look;
  }
  async atlas(url, base) {
    if (this.textures.has(url)) return this.textures.get(url);
    const img = await new Promise((ok, fail) => { const i = new Image(); i.crossOrigin = 'anonymous'; i.onload = () => ok(i); i.onerror = fail; i.src = url; });
    const W = Math.min(1024, img.naturalWidth), Hh = Math.round(W * img.naturalHeight / img.naturalWidth);
    const c = document.createElement('canvas'); c.width = W * 2; c.height = Hh; const x = c.getContext('2d');
    x.fillStyle = base; x.fillRect(0, 0, c.width, c.height);
    x.drawImage(img, 0, 0, W, Hh);                                   // front: the real garment, prints and all
    x.save(); x.translate(W * 2, 0); x.scale(-1, 1); x.filter = 'blur(' + Math.round(W / 22) + 'px)';
    x.drawImage(img, 0, 0, W, Hh); x.restore();                       // back: same fabric and colour blocking, no front graphics,
    x.globalAlpha = .62; x.fillStyle = base; x.fillRect(W, 0, W, Hh); x.globalAlpha = 1;   // and no front opening or zip
    const tex = new T.CanvasTexture(c); tex.colorSpace = T.SRGBColorSpace; tex.anisotropy = 8; tex.channel = 1;
    this.textures.set(url, tex); return tex;
  }
  /* the authored diffuse, desaturated and brightened, so a colour tint keeps its weave, seams and shading */
  plainFabric(key) {
    const k = 'plain:' + key; if (this.textures.has(k)) return this.textures.get(k);
    const src = this.asset.community.textures[key + '_diffuse']; if (!src?.image) return null;
    const im = src.image, c = document.createElement('canvas'); c.width = Math.min(1024, im.width); c.height = Math.round(c.width * im.height / im.width);
    const x = c.getContext('2d'); x.drawImage(im, 0, 0, c.width, c.height);
    const d = x.getImageData(0, 0, c.width, c.height), px = d.data; let sum = 0, n = 0;
    for (let i = 0; i < px.length; i += 4) if (px[i + 3] > 20) { sum += .3 * px[i] + .59 * px[i + 1] + .11 * px[i + 2]; n++; }
    const gain = 214 / Math.max(30, sum / Math.max(1, n));
    for (let i = 0; i < px.length; i += 4) { const l = Math.min(255, (.3 * px[i] + .59 * px[i + 1] + .11 * px[i + 2]) * gain); px[i] = px[i + 1] = px[i + 2] = l; }
    x.putImageData(d, 0, 0);
    const t = new T.CanvasTexture(c); t.colorSpace = T.SRGBColorSpace; t.anisotropy = 8; t.flipY = src.flipY;
    this.textures.set(k, t); return t;
  }
  material(look, tpl, key = null) {
    const [rough, coat, sheen, nrm, rep] = FABRIC[tpl.fabric] || FABRIC.cotton, tex = this.asset.community.textures;
    const authoredNormal = key && tex[key + '_normal'];
    // a photo already carries its own light: render it a little darker and without the extra sheen, or it washes out
    // light garments stay light, mid tones are held back a little (a photo already carries its own light)
    const lum = new T.Color(look.color || '#888').getHSL({}).l, k = .74 + .25 * lum;
    let map = look.map, color = new T.Color(k, k, k);
    if (!map) { map = key ? this.plainFabric(key) : null; color = look.color; }
    const photo = !!look.map;
    const m = new T.MeshPhysicalMaterial({color, map, roughness: photo ? Math.max(rough, .6) : rough, metalness: look.metalness ?? 0,
      clearcoat: photo ? coat * .35 : coat, clearcoatRoughness: .4, sheen: photo ? sheen * .4 : sheen, sheenRoughness: .8,
      sheenColor: new T.Color(look.color).lerp(new T.Color('#fff'), photo ? .1 : .3), side: T.DoubleSide, alphaTest: key ? .05 : 0,
      normalMap: authoredNormal || (nrm ? weaveTexture(tpl.fabric) : null),
      normalScale: authoredNormal ? new T.Vector2(.85, .85) : new T.Vector2(nrm, nrm)});
    if (!authoredNormal && m.normalMap) m.normalMap.repeat.set(rep, rep);
    if (key && tex[key + '_roughness']) m.roughnessMap = tex[key + '_roughness'];
    return m;
  }
  /* project the garment photo onto the posed garment; front faces get the photo, back faces the print-free copy */
  dress(geo, look, tpl, projection, mesh = null, key = null) {
    const m = this.rig.mesh(geo, this.material(projection ? look : {...look, map: null}, tpl, key)); m.castShadow = true; m.receiveShadow = true;
    this.root.add(m); this.root.updateMatrixWorld(true); this.rig.skeleton.update();
    if (projection && look.map) projectUVs(m, look, projection);
    const n = geo.getAttribute('position').count;
    if (!geo.getAttribute('uv')) geo.setAttribute('uv', geo.getAttribute('uv1') ? geo.getAttribute('uv1').clone() : new T.Float32BufferAttribute(new Float32Array(n * 2), 2));
    if (!geo.getAttribute('uv1')) geo.setAttribute('uv1', geo.getAttribute('uv').clone());
    return m;
  }
  /* a natural standing pose: arms relaxed by the sides, palms toward the thighs, fingers softly curled, breathing */
  pose(now = 0) {
    const r = this.rig; if (!r) return;
    const by = this.byName || (this.byName = Object.fromEntries(r.bones.map(b => [b.name, b])));
    if (!this.axes) {   // rest-pose bone axes (bones start unrotated, so local == world directions here)
      const L = key => centroid(this.asset.landmarks[key].map(i => this.body[i]));
      this.axes = {};
      for (const s of ['l', 'r']) {
        this.axes['upper_' + s] = L('joint-' + s + '-elbow').sub(L('joint-' + s + '-shoulder')).normalize();
        this.axes['fore_' + s] = L('joint-' + s + '-hand').sub(L('joint-' + s + '-elbow')).normalize();
      }
    }
    for (const b of r.bones) b.quaternion.identity();
    const P = this.poseCfg || {out: .1, fwd: .03, bend: .1, twist: -.35, curl: .2}, breath = Math.sin(now * .0016), sway = Math.sin(now * .0006);
    // relaxed stance: each bone is aimed at a target direction (the rest pose's arms point forward, so plain rotations don't hang them down)
    for (const [s, side] of [['l', 1], ['r', -1]]) {
      const up = by['upperarm_' + s], fore = by['lowerarm_' + s], ua = this.axes['upper_' + s], fa = this.axes['fore_' + s];
      const sideX = Math.sign(ua.x) || side;
      const dUp = new T.Vector3(sideX * (P.out + breath * .004), -1, P.fwd).normalize();
      up.quaternion.setFromUnitVectors(ua, dUp);
      const dFore = new T.Vector3(sideX * P.out * .4, -1, P.fwd + P.bend * 1.6).normalize().applyQuaternion(up.quaternion.clone().invert());
      fore.quaternion.setFromUnitVectors(fa, dFore).multiply(new T.Quaternion().setFromAxisAngle(fa, -sideX * P.twist));
    }
    for (const b of r.bones) {
      const m = /^(index|middle|ring|pinky|thumb)_0([123])_([lr])$/.exec(b.name);
      if (!m) continue;
      const child = b.children.find(c => c.isBone), dir = (child ? child.position.clone() : b.position.clone()).normalize();
      const axis = dir.clone().cross(new T.Vector3(0, 0, 1)).normalize();
      const amount = m[1] === 'thumb' ? P.curl * .45 : P.curl * (m[2] === '1' ? .75 : m[2] === '2' ? 1.1 : .9);
      b.quaternion.setFromAxisAngle(axis, amount);
    }
    if (by.spine_03) by.spine_03.rotation.x = breath * .006;
    if (by.head) { by.head.rotation.y = sway * .05; by.head.rotation.x = -.02; }
    if (by.pelvis) by.pelvis.rotation.z = sway * .006;
  }
  tick(now) { this.pose(now); }
  /* what the page saves/restores */
  state() { return {cfg: this.cfg, worn: this.worn}; }
}

/* ---------------------------------------------------------------- geometry helpers */
function centroid(ps) { const c = new T.Vector3(); ps.forEach(p => c.add(p)); return c.divideScalar(Math.max(1, ps.length)); }
function centroid2(ps) { const c = new T.Vector2(); ps.forEach(p => c.add(p)); return c.divideScalar(Math.max(1, ps.length)); }
function convexHull(points) {
  const p = points.slice().sort((a, b) => a.x - b.x || a.y - b.y), cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lo = [], hi = [];
  for (const q of p) { while (lo.length > 1 && cross(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
  for (const q of p.reverse()) { while (hi.length > 1 && cross(hi[hi.length - 2], hi[hi.length - 1], q) <= 0) hi.pop(); hi.push(q); }
  return lo.slice(0, -1).concat(hi.slice(0, -1));
}
/* non-indexed geometry with per-corner skin weights (and optional UVs) */
function expand(pos, normals, indices, weights, uvs, uvIdx) {
  const p = [], n = [], uv = [], si = [], sw = [];
  let nrm = normals;
  if (!nrm) { const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(pos.flatMap(v => v.toArray()), 3)); g.setIndex(indices); g.computeVertexNormals();
    const a = g.getAttribute('normal'); nrm = pos.map((_, i) => new T.Vector3(a.getX(i), a.getY(i), a.getZ(i))); g.dispose(); }
  for (let k = 0; k < indices.length; k++) {
    const i = indices[k]; p.push(pos[i].x, pos[i].y, pos[i].z); n.push(nrm[i].x, nrm[i].y, nrm[i].z);
    if (uvs) { const u = uvs[uvIdx[k]] || [0, 0]; uv.push(u[0], u[1]); }
    const w = weights[i]; si.push(...w[0]); sw.push(...w[1]);
  }
  const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(p, 3)); g.setAttribute('normal', new T.Float32BufferAttribute(n, 3));
  if (uvs) g.setAttribute('uv', new T.Float32BufferAttribute(uv, 2));
  g.setAttribute('skinIndex', new T.Uint16BufferAttribute(si, 4)); g.setAttribute('skinWeight', new T.Float32BufferAttribute(sw, 4));
  return g;
}
/* clip body triangles to regions (tests in rest space: x, height 0..1, z) and emit the offset garment surface */
function clipBuild(tris, body, hn, pos, normals, rig, regions, H) {
  const P = [], N = [], SI = [], SW = [];
  const corner = i => ({t: [body[i].x, hn[i], body[i].z], p: pos[i].toArray(), n: normals[i].toArray(), s: rig.baseWeights[i]});
  for (let k = 0; k < tris.length; k += 3) {
    for (const region of regions) {
      let poly = [corner(tris[k]), corner(tris[k + 1]), corner(tris[k + 2])];
      for (const [ax, bound, sign] of region) {
        if (!poly.length) break;
        const next = [];
        for (let j = 0; j < poly.length; j++) {
          const a = poly[j], b = poly[(j + 1) % poly.length], da = (a.t[ax] - bound) * sign, db = (b.t[ax] - bound) * sign;
          if (da >= 0) next.push(a);
          if ((da >= 0) !== (db >= 0)) { const f = da / (da - db), lerp = (u, v) => u.map((x, q) => x + (v[q] - x) * f);
            next.push({t: lerp(a.t, b.t), p: lerp(a.p, b.p), n: lerp(a.n, b.n), s: rig.mix(a.s, b.s, f)}); }
        }
        poly = next;
      }
      for (let j = 1; j < poly.length - 1; j++) for (const c of [poly[0], poly[j], poly[j + 1]]) { P.push(...c.p); N.push(...c.n); SI.push(...c.s[0]); SW.push(...c.s[1]); }
    }
  }
  const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(P, 3)); g.setAttribute('normal', new T.Float32BufferAttribute(N, 3));
  g.setAttribute('skinIndex', new T.Uint16BufferAttribute(SI, 4)); g.setAttribute('skinWeight', new T.Float32BufferAttribute(SW, 4));
  g.normalizeNormals?.();
  return g;
}
/* which authored mesh each template starts from: [mesh, part, inflate] ($bottom = the sex's casual trousers) */
const HEM_ABOVE_BELT = .552;
/* which part of a casual outfit a mesh UV position belongs to (mapped from the meshes: jeans with their belt vs. shirt) */
const ISLANDS = {
  male_outfit: (u, v) => u > .36 && u < .71 ? 'bottom' : 'top',
  female_outfit: (u, v) => v < .57 && u < .74 ? 'bottom' : 'top',
};
const BASE = {
  tee: ['polo'], polo: ['polo'], tank: ['keyhole'], longsleeve: ['male_outfit', 'top'], shirt: ['male_outfit', 'top'],
  sweater: ['male_outfit', 'top', .008], hoodie: ['male_outfit', 'top', .01], jacket: ['male_outfit', 'top', .02], zip_hoodie: ['male_outfit', 'top', .015],
  vest: ['male_outfit', 'top', .016], blazer: ['blazer', 'top'], coat: ['blazer', 'top', .014],
  jeans: ['$bottom', 'bottom'], trousers: ['$bottom', 'bottom', .004], joggers: ['$bottom', 'bottom', .008], shorts: ['$bottom', 'bottom', .006],
  leggings: ['female_outfit', 'bottom'], skirt: ['$bottom', 'bottom'], dress: ['keyhole'],
  sneakers: ['shoes'], hightops: ['shoes'], boots: ['shoes'], dress_shoes: ['shoes'], sandals: ['shoes'],
};
/* push a fitted garment outward along its smooth normals (looser cuts, outer layers) */
function inflated(pos, triangles, amount) {
  const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(pos.flatMap(p => p.toArray()), 3)); g.setIndex(triangles);
  g.computeVertexNormals(); const n = g.getAttribute('normal');
  // less on top of the shoulders (straight up), or outer layers look padded
  const out = pos.map((p, i) => p.clone().add(new T.Vector3(n.getX(i), n.getY(i), n.getZ(i)).multiplyScalar(amount * (1 - .6 * Math.max(0, n.getY(i)))))); g.dispose(); return out;
}
/* push the part of a garment below `from` (height 0-1) outward, growing to `amount` at `to`, so a hem sits over the trousers */
function flared(pos, triangles, H, from, to, amount) {
  const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(pos.flatMap(p => p.toArray()), 3)); g.setIndex(triangles);
  g.computeVertexNormals(); const n = g.getAttribute('normal');
  const out = pos.map((p, i) => { const h = (p.y - .08) / H, k = Math.max(0, Math.min(1, (from - h) / (from - to)));
    if (!k) return p; const d = new T.Vector3(n.getX(i), 0, n.getZ(i)); if (d.lengthSq() < 1e-6) return p;
    return p.clone().addScaledVector(d.normalize(), amount * k * k * (3 - 2 * k)); });
  g.dispose(); return out;
}
/* non-indexed garment geometry, clipped to height bands with interpolated UVs and skin weights */
function expandClipped(pos, tri, weights, uvs, triUV, heights, clips, rig, extra = null) {
  const sm = new T.BufferGeometry(); sm.setAttribute('position', new T.Float32BufferAttribute(pos.flatMap(p => p.toArray()), 3)); sm.setIndex(tri); sm.computeVertexNormals();
  const nrm = sm.getAttribute('normal'), P = [], N = [], U = [], SI = [], SW = [];
  for (let t = 0; t < tri.length; t += 3) {
    let poly = [0, 1, 2].map(k => { const id = tri[t + k], uv = uvs[triUV[t + k]] || [0, 0];
      return {v: [pos[id].x, pos[id].y, pos[id].z, nrm.getX(id), nrm.getY(id), nrm.getZ(id), uv[0], uv[1], heights[id], extra ? extra[id] : 0], s: weights[id]}; });
    for (const c of clips) {
      const next = [];
      for (let k = 0; k < poly.length; k++) {
        const a = poly[k], b = poly[(k + 1) % poly.length], ch = c.chan || 8, da = (a.v[ch] - c.bound) * c.sign, db = (b.v[ch] - c.bound) * c.sign;
        if (da >= 0) next.push(a);
        if ((da >= 0) !== (db >= 0)) { const f = da / (da - db); next.push({v: a.v.map((x, j) => x + (b.v[j] - x) * f), s: rig.mix(a.s, b.s, f)}); }
      }
      poly = next; if (!poly.length) break;
    }
    for (let j = 1; j < poly.length - 1; j++) for (const c of [poly[0], poly[j], poly[j + 1]]) {
      P.push(c.v[0], c.v[1], c.v[2]); N.push(c.v[3], c.v[4], c.v[5]); U.push(c.v[6], c.v[7]); SI.push(...c.s[0]); SW.push(...c.s[1]); }
  }
  sm.dispose();
  const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(P, 3)); g.setAttribute('normal', new T.Float32BufferAttribute(N, 3));
  g.setAttribute('uv', new T.Float32BufferAttribute(U, 2)); g.setAttribute('skinIndex', new T.Uint16BufferAttribute(SI, 4)); g.setAttribute('skinWeight', new T.Float32BufferAttribute(SW, 4));
  return g;
}
function keepTriangles(g, keep) {
  const P = g.getAttribute('position');
  for (const name of Object.keys(g.attributes)) {
    const a = g.getAttribute(name), arr = [];
    for (let t = 0; t < P.count; t += 3) if (keep(t)) for (let k = 0; k < 3; k++) for (let j = 0; j < a.itemSize; j++) arr.push(a.array[(t + k) * a.itemSize + j]);
    g.setAttribute(name, a.array instanceof Uint16Array ? new T.Uint16BufferAttribute(arr, a.itemSize) : new T.Float32BufferAttribute(arr, a.itemSize));
  }
}
/* open-front jackets: remove the centre-front strip so the layer underneath shows */
function openFront(g, half) {
  if (!half) return;
  const P = g.getAttribute('position');
  keepTriangles(g, t => { let front = 0, centre = 0, hy = 0; for (let k = 0; k < 3; k++) { if (P.getZ(t + k) > .02) front++; if (Math.abs(P.getX(t + k)) < half) centre++; hy += P.getY(t + k); }
    return !(front === 3 && centre === 3 && hy / 3 < 1.98); });
}
function dropSleeves(g, torsoX) {
  const P = g.getAttribute('position');
  keepTriangles(g, t => { for (let k = 0; k < 3; k++) if (Math.abs(P.getX(t + k)) > torsoX * 1.12) return false; return true; });
}
/* where an open garment's photo shows its inside: the band of columns near the middle that isn't the garment's colour */
function photoGap(canvas, bbox, base) {
  try {
    const W = canvas.width / 2, Hh = canvas.height, [bx0, by0, bx1, by1] = bbox, x = canvas.getContext('2d');
    const cx0 = Math.floor(W * (bx0 + (bx1 - bx0) * .3)), cx1 = Math.ceil(W * (bx0 + (bx1 - bx0) * .7)), ry0 = Math.floor(Hh * (by0 + (by1 - by0) * .35)), ry1 = Math.ceil(Hh * (by0 + (by1 - by0) * .8));
    const d = x.getImageData(cx0, ry0, cx1 - cx0, ry1 - ry0).data, c = new T.Color(base), br = c.r * 255, bg = c.g * 255, bb = c.b * 255;
    const cols = cx1 - cx0, rows = ry1 - ry0, off = new Float32Array(cols);
    for (let r = 0; r < rows; r++) for (let k = 0; k < cols; k++) { const i = (r * cols + k) * 4; off[k] += Math.hypot(d[i] - br, d[i + 1] - bg, d[i + 2] - bb) > 70 ? 1 / rows : 0; }
    let best = null, run = null;
    for (let k = 0; k <= cols; k++) { if (k < cols && off[k] > .35) { run = run || [k, k]; run[1] = k; } else if (run) { if (!best || run[1] - run[0] > best[1] - best[0]) best = run; run = null; } }
    if (!best || best[1] - best[0] < cols * .04) return null;
    const toRel = k => ((cx0 + k) / W - bx0) / (bx1 - bx0);
    const pad = Math.round(cols * .06);   // its shadowed edges too
    return [toRel(Math.max(0, best[0] - pad)), toRel(Math.min(cols, best[1] + 1 + pad))];
  } catch (e) { return null; }
}
/* sleeves are "unfolded" onto the photo's own sleeves: a product shot hangs them beside the body (long) or angled out (short),
   so each sleeve vertex is placed by how far down the arm it is and which side of the arm it is on; returns per-vertex
   photo coordinates (0-1 of the garment box) and a blend weight (0 on the body, 1 on the arm) */
const SLEEVE_LINE = {short: [[.19, .05], [.06, .37], .13], long: [[.14, .1], [.05, .95], .1]};
function sleeveMap(mesh, posed, kind) {
  const [A, B, width] = SLEEVE_LINE[kind] || SLEEVE_LINE.long, g = mesh.geometry, SI = g.getAttribute('skinIndex'), SW = g.getAttribute('skinWeight');
  const bones = mesh.skeleton.bones, armSide = bones.map(b => (/^(upperarm|lowerarm|hand|index|middle|ring|pinky|thumb)\w*_([lr])$/.exec(b.name) || [])[2] || null);
  const at = name => { const b = bones.find(x => x.name === name); return b ? b.getWorldPosition(new T.Vector3()) : null; };
  const n = posed.length, u = new Float32Array(n), v = new Float32Array(n), w = new Float32Array(n), s = new Float32Array(n), across = new Float32Array(n), side = new Array(n);
  const arms = {}; for (const k of ['l', 'r']) arms[k] = [at('upperarm_' + k), at('lowerarm_' + k), at('hand_' + k)];
  if (Object.values(arms).some(a => a.some(p => !p))) return null;
  const seg = (p, a, b) => { const ab = b.clone().sub(a), t = Math.max(0, Math.min(1, p.clone().sub(a).dot(ab) / ab.lengthSq())); return [t, a.clone().addScaledVector(ab, t)]; };
  const tmax = {l: 0, r: 0}, rmax = {l: 1e-4, r: 1e-4};
  for (let i = 0; i < n; i++) {
    let aw = {l: 0, r: 0};
    for (let j = 0; j < 4; j++) { const sd = armSide[SI.getComponent(i, j)]; if (sd) aw[sd] += SW.getComponent(i, j); }
    const sd = aw.l >= aw.r ? 'l' : 'r', ww = aw[sd];
    if (ww < .3) continue;
    const [sh, el, wr] = arms[sd], p = posed[i], [t1, q1] = seg(p, sh, el), [t2, q2] = seg(p, el, wr);
    const L1 = sh.distanceTo(el), L2 = el.distanceTo(wr), useUpper = p.distanceTo(q1) <= p.distanceTo(q2);
    s[i] = useUpper ? t1 * L1 : L1 + t2 * L2; const q = useUpper ? q1 : q2;
    across[i] = p.x - q.x; side[i] = sd; w[i] = Math.min(1, Math.max(0, (ww - .3) / .5));
    tmax[sd] = Math.max(tmax[sd], s[i]); rmax[sd] = Math.max(rmax[sd], Math.abs(across[i]));
  }
  for (let i = 0; i < n; i++) {
    if (!side[i]) continue;
    const sd = side[i], f = s[i] / (tmax[sd] || 1), imageLeft = arms[sd][0].x < 0;     // the posed -x arm is on the photo's left
    let uu = A[0] + (B[0] - A[0]) * f + (across[i] / rmax[sd]) * width * .5 * (imageLeft ? 1 : -1) * -1, vv = A[1] + (B[1] - A[1]) * f;
    if (!imageLeft) uu = 1 - uu;
    u[i] = uu; v[i] = vv;
  }
  return {u, v, w};
}
/* UVs (second channel) from the posed garment: front view for clothing and hats, side view for shoes */
function projectUVs(mesh, look, mode) {
  const g = mesh.geometry, P = g.getAttribute('position'), n = P.count, posed = new Array(n), v = new T.Vector3();
  for (let i = 0; i < n; i++) { v.fromBufferAttribute(P, i); mesh.applyBoneTransform(i, v); posed[i] = v.clone(); }
  const [tx0, ty0, tx1, ty1] = look.bbox, uv = new Float32Array(n * 2);
  if (mode === 'side') {
    const toeLeft = look.toe !== 'right';
    for (const side of [-1, 1]) {
      const idx = []; for (let i = 0; i < n; i++) if (posed[i].x * side > 0) idx.push(i);
      if (!idx.length) continue;
      const zs = idx.map(i => posed[i].z), ys = idx.map(i => posed[i].y), z0 = Math.min(...zs), z1 = Math.max(...zs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      for (const i of idx) {
        const fz = (posed[i].z - z0) / (z1 - z0 || 1), fy = (posed[i].y - y0) / (y1 - y0 || 1);
        uv[i * 2] = (tx0 + (toeLeft ? 1 - fz : fz) * (tx1 - tx0)) * .5; uv[i * 2 + 1] = 1 - (ty1 - fy * (ty1 - ty0));
      }
    }
  } else {
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9; for (const p of posed) { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); }
    // front or back by where the triangle sits relative to the garment's own middle at that height (garments are double-sided)
    const bins = 48, zs = new Float32Array(bins), cnt = new Float32Array(bins), bin = y => Math.max(0, Math.min(bins - 1, Math.floor((y - y0) / (y1 - y0 || 1) * bins)));
    for (const p of posed) { const k = bin(p.y); zs[k] += p.z; cnt[k]++; }
    const sleeves = look.sleeve && look.sleeve !== 'none' ? sleeveMap(mesh, posed, look.sleeve) : null;
    // an open jacket: its two front edges meet the photo's own opening edges (so the mannequin inside never shows)
    const gap = look.open ? photoGap(look.map.image, look.bbox, look.color) : null, rc = (0 - x0) / (x1 - x0 || 1);
    for (let t = 0; t + 2 < n; t += 3) {
      const my = (posed[t].y + posed[t + 1].y + posed[t + 2].y) / 3, mz = (posed[t].z + posed[t + 1].z + posed[t + 2].z) / 3, k = bin(my);
      const front = mz >= (cnt[k] ? zs[k] / cnt[k] : 0);
      for (let k = 0; k < 3; k++) {
        const i = t + k, p = posed[i];
        let rx = (p.x - x0) / (x1 - x0 || 1), ry = 1 - (p.y - y0) / (y1 - y0 || 1);
        if (gap) rx = rx < rc ? rx / rc * gap[0] : gap[1] + (rx - rc) / (1 - rc) * (1 - gap[1]);
        if (sleeves && sleeves.w[i] > 0) { const f = sleeves.w[i]; rx += (sleeves.u[i] - rx) * f; ry += (sleeves.v[i] - ry) * f; }
        const ux = tx0 + rx * (tx1 - tx0), vy = ty0 + ry * (ty1 - ty0);
        uv[i * 2] = front ? ux * .5 : .5 + (1 - ux) * .5; uv[i * 2 + 1] = 1 - vy;
      }
    }
  }
  g.setAttribute('uv1', new T.BufferAttribute(uv, 2));
}
const weaveCache = {};
function weaveTexture(kind) {
  if (weaveCache[kind]) return weaveCache[kind];
  const s = 64, c = document.createElement('canvas'); c.width = c.height = s; const x = c.getContext('2d'), img = x.createImageData(s, s);
  for (let y = 0; y < s; y++) for (let i = 0; i < s; i++) {
    let h;
    if (kind === 'knit' || kind === 'fleece') h = Math.sin((i % 8) / 8 * Math.PI) * .6 + Math.sin(y / 4) * .2;
    else if (kind === 'denim') h = ((i + y) % 6 < 3 ? .7 : .3) + Math.random() * .2;
    else if (kind === 'leather' || kind === 'polished') h = .5 + (Math.random() - .5) * .25;
    else h = ((i % 4 < 2) !== (y % 4 < 2) ? .65 : .35) + (Math.random() - .5) * .15;
    const nx = 128 + (h - .5) * 120, ny = 128 + (Math.random() - .5) * 40, o = (y * s + i) * 4;
    img.data[o] = nx; img.data[o + 1] = ny; img.data[o + 2] = 255; img.data[o + 3] = 255;
  }
  x.putImageData(img, 0, 0);
  const t = new T.CanvasTexture(c); t.wrapS = t.wrapT = T.RepeatWrapping; return (weaveCache[kind] = t);
}
function strandTexture(density) {
  const s = 256, c = document.createElement('canvas'); c.width = c.height = s; const x = c.getContext('2d');
  x.fillStyle = '#000'; x.fillRect(0, 0, s, s); x.strokeStyle = '#fff'; x.lineWidth = 1;
  for (let k = 0; k < 2200 * density; k++) { const px = Math.random() * s, py = Math.random() * s; x.globalAlpha = .4 + Math.random() * .6;
    x.beginPath(); x.moveTo(px, py); x.lineTo(px + (Math.random() - .5) * 3, py + 3 + Math.random() * 5); x.stroke(); }
  const t = new T.CanvasTexture(c); t.wrapS = t.wrapT = T.RepeatWrapping; return t;
}
