/** Procedural miniature home, no assets, DOM, renderer, listeners or RAF.
 * Integration (parent owns these steps):
 *   const home = buildHome(threeNS); scene.add(home.group);
 *   home.setState(await loadFromHost()); home.group.visible = true; // enter
 *   // In EXISTING render loop, after interaction/speech arbitration:
 *   home.update(performance.now(), companion.reducedMotion);
 *   const p = home.activityPose(); animator.play('idle', now, p.action, start);
 *   // Apply p.position + p.rotation to a dedicated robot mount AFTER animation;
 *   // mount wraps existing activeAdapter.group, scale=.6 (.30m rig -> .18m).
 *   // Pose arrays are home-group LOCAL metres and XYZ Euler radians, feet origin.
 *   // Mount must share home's parent frame (or transform through home.group).
 *   // Do not move bones/rest transforms, clone rig or build a second animator.
 *   // On exit: group.visible=false, restore robot parent/transform/camera/lease.
 * Fit camera to home bounds on entry; preserve/restore normal camera on exit.
 * Parent persists validated snapshots via core.companion_home, sends whole state
 * to setState, and wires existing menu/API. No visibility field is persisted.
 * Rooms/activities use the exported ROOMS/ACTIONS strings (mirrored in Python).
 * update now is milliseconds, no own loop. Routine boundaries use LOCAL civil
 * time (latest daily HH:MM boundary cyclically), only while routines.enabled.
 * Disable routines for manual selection. Reduced motion freezes all prop/pet
 * motion but retains static furniture, light, scheduled room and resting pose.
 * Scrapbook stores plain text in page mesh userData, never HTML; colored pages
 * turn while reading scrapbook. Parent should expose text in accessible menu.
 * dispose is idempotent; frees ONLY owned meshes/materials, never the robot.
 * Wake picks a stable free floor point from actual furniture/lamp footprints,
 * with .062m clearance for the .6 robot. Layout remains untouched. If every
 * candidate is obstructed, he stays on his bed rather than waking in furniture.
 * Cook feet rest on the counter's visible step; tinker feet on the actual stool.
 */
export const ROOMS = Object.freeze(['bedroom', 'kitchen', 'workshop', 'fitness', 'reading']);
export const ACTIONS = Object.freeze(['idle', 'sleep', 'wake', 'cook', 'tinker', 'exercise', 'read', 'pet', 'scrapbook']);
const UNIT = .24;
const placement = {
  bedroom: {bed: [-.45, 0, -.1], nightstand: [.45, 0, -.55], wardrobe: [.9, 0, -.6]},
  kitchen: {counter: [-.5, 0, -.65], fridge: [.8, 0, -.65], table: [.15, 0, .4]},
  workshop: {bench: [0, 0, -.6], stool: [.55, 0, .3], shelf: [-.95, 0, -.6]},
  fitness: {mat: [-.3, 0, .2], weights: [.75, 0, -.5]},
  reading: {chair: [-.4, 0, .1], bookcase: [.8, 0, -.65], table: [.4, 0, .35]},
};
const roomFor = {sleep: 'bedroom', wake: 'bedroom', cook: 'kitchen', tinker: 'workshop', exercise: 'fitness', read: 'reading'};
export function defaultHome() {
  return {schema: 1, room: 'bedroom', activity: 'idle', accent: '#63c9c0', lamp: true,
    furniture: Object.fromEntries(ROOMS.map(room => [room, Object.fromEntries(
      Object.entries(placement[room]).map(([id, position]) => [id, {position: position.map(v => Number((v * UNIT).toFixed(6))), rotation: 0}]))])),
    inventory: [], pet: {enabled: true, name: 'Pip', color: '#e9b76c'}, scrapbook: [],
    routines: {enabled: false, entries: []}};
}
const color = v => typeof v === 'string' && /^#[\da-f]{6}$/i.test(v);
const finite = (v, lo, hi) => typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi;
const text = (v, max) => typeof v === 'string' && v.length > 0 && v.length <= max && !/[\x00-\x1f]/.test(v);
const keys = (v, wanted) => v && typeof v === 'object' && !Array.isArray(v) &&
  Object.keys(v).length === wanted.length && wanted.every(k => Object.hasOwn(v, k));
function validActivity(room, action) { return ROOMS.includes(room) && ACTIONS.includes(action) && (!roomFor[action] || roomFor[action] === room); }
export function validateHome(s) {
  if (!keys(s, Object.keys(defaultHome())) || s.schema !== 1 || !validActivity(s.room, s.activity) ||
      !color(s.accent) || typeof s.lamp !== 'boolean' || !keys(s.furniture, ROOMS)) return false;
  for (const room of ROOMS) {
    if (!keys(s.furniture[room], Object.keys(placement[room]))) return false;
    for (const p of Object.values(s.furniture[room])) {
      if (!keys(p, ['position', 'rotation']) || !Array.isArray(p.position) || p.position.length !== 3 ||
          !p.position.every(v => finite(v, -.24, .24)) || p.position[1] !== 0 || Math.abs(p.position[2]) > .168 ||
          !finite(p.rotation, -Math.PI, Math.PI)) return false;
    }
  }
  if (!Array.isArray(s.inventory) || s.inventory.length > 12 || !s.inventory.every(i =>
    keys(i, ['id', 'kind', 'color']) && text(i.id, 40) && ['star', 'crystal', 'badge'].includes(i.kind) && color(i.color)) ||
    new Set(s.inventory.map(i => i.id)).size !== s.inventory.length) return false;
  if (!keys(s.pet, ['enabled', 'name', 'color']) || typeof s.pet.enabled !== 'boolean' || !text(s.pet.name, 32) || !color(s.pet.color)) return false;
  if (!Array.isArray(s.scrapbook) || s.scrapbook.length > 24 || !s.scrapbook.every(p =>
    keys(p, ['title', 'text', 'color']) && text(p.title, 80) && text(p.text, 500) && color(p.color))) return false;
  if (!keys(s.routines, ['enabled', 'entries']) || typeof s.routines.enabled !== 'boolean' || !Array.isArray(s.routines.entries) ||
      s.routines.entries.length > 12 || !s.routines.entries.every(e => keys(e, ['time', 'room', 'activity']) &&
        typeof e.time === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(e.time) && validActivity(e.room, e.activity)) ||
      new Set(s.routines.entries.map(e => e.time)).size !== s.routines.entries.length) return false;
  return true;
}
/** Pure routine resolver; explicit minute argument supports deterministic tests. */
export function resolveRoutine(s, minute) {
  if (!s.routines.enabled || !s.routines.entries.length) return {room: s.room, activity: s.activity};
  const entries = [...s.routines.entries].sort((a, b) => a.time.localeCompare(b.time));
  const at = e => Number(e.time.slice(0, 2)) * 60 + Number(e.time.slice(3));
  const chosen = entries.filter(e => at(e) <= minute).at(-1) || entries.at(-1);
  return {room: chosen.room, activity: chosen.activity};
}

export function buildHome(T) {
  const group = new T.Group(); group.name = 'companion-home'; group.visible = false;
  const model = new T.Group(); model.scale.setScalar(UNIT); group.add(model);
  const ownedGeo = new Set(), ownedMat = new Set();
  const mat = c => {const m = new T.MeshStandardMaterial({color: c, roughness: .72}); ownedMat.add(m); return m;};
  const wood = mat('#a87955'), dark = mat('#314052'), wall = mat('#ecdfca'), white = mat('#fff1d8');
  const accent = mat('#63c9c0'), brass = mat('#d7ae61'), paper = mat('#f8efd9');
  function mesh(parent, name, geometry, material, p = [0, 0, 0]) {
    ownedGeo.add(geometry); const m = new T.Mesh(geometry, material); m.name = name;
    m.position.set(...p); parent.add(m); return m;
  }
  const box = (g, n, size, m, p) => mesh(g, n, new T.BoxGeometry(...size), m, p);
  const ball = (g, n, r, m, p) => mesh(g, n, new T.SphereGeometry(r, 12, 8), m, p);
  const cyl = (g, n, r, h, m, p) => mesh(g, n, new T.CylinderGeometry(r, r, h, 16), m, p);
  function mount(parent, name) {const g = new T.Group(); g.name = name; parent.add(g); return g;}
  function table(g, w, h, d) {
    box(g, 'top', [w, .08, d], wood, [0, h, 0]);
    for (const x of [-1, 1]) for (const z of [-1, 1]) box(g, 'leg', [.065, h, .065], dark, [x * (w / 2 - .06), h / 2, z * (d / 2 - .06)]);
  }
  function shelves(g, w = .55, h = 1.35) {
    box(g, 'back', [w, h, .06], wood, [0, h / 2, -.12]);
    for (const x of [-w / 2, w / 2]) box(g, 'side', [.06, h, .34], wood, [x, h / 2, 0]);
    for (let y = .15; y < h; y += .4) {
      box(g, 'shelf', [w, .05, .35], wood, [0, y, 0]);
      for (let i = 0; i < 4; i++) box(g, 'book', [.075, .22 + i * .025, .21], i % 2 ? accent : white, [-w / 2 + .1 + i * .1, y + .16, 0]);
    }
  }
  const rooms = {}, furniture = {};
  for (const room of ROOMS) {
    const g = mount(model, `room:${room}`); rooms[room] = g; furniture[room] = {};
    box(g, 'floor', [2.7, .08, 1.9], wood, [0, -.04, 0]);
    box(g, 'back-wall', [2.7, 1.65, .06], wall, [0, .825, -.95]);
    box(g, 'left-wall', [.06, .85, 1.9], wall, [-1.35, .425, 0]);
    box(g, 'skirting', [2.65, .08, .05], white, [0, .06, -.9]);
    box(g, 'window-frame', [.65, .63, .06], white, [-.55, 1.14, -.9]);
    box(g, 'window', [.55, .53, .065], mat('#93bfd0'), [-.55, 1.14, -.86]);
    box(g, 'window-bar', [.025, .53, .07], white, [-.55, 1.14, -.82]);
    box(g, 'rug', [1.2, .015, .7], accent, [0, .012, .4]);
    for (const id of Object.keys(placement[room])) furniture[room][id] = mount(g, `furniture:${id}`);
  }
  const bed = furniture.bedroom.bed;
  box(bed, 'frame', [.86, .23, 1.36], wood, [0, .2, 0]);
  box(bed, 'mattress', [.82, .15, 1.3], white, [0, .39, 0]);
  box(bed, 'quilt', [.84, .045, .75], accent, [0, .49, -.22]);
  box(bed, 'pillow', [.57, .12, .28], white, [0, .5, .46]);
  box(bed, 'headboard', [.9, .7, .08], wood, [0, .4, .72]);
  table(furniture.bedroom.nightstand, .35, .45, .35);
  const wardrobe = furniture.bedroom.wardrobe;
  box(wardrobe, 'cabinet', [.5, 1.3, .45], wood, [0, .65, 0]);
  box(wardrobe, 'door', [.43, 1.18, .03], white, [0, .65, .24]);
  ball(wardrobe, 'handle', .028, brass, [.14, .65, .27]);
  const counter = furniture.kitchen.counter;
  box(counter, 'cabinet', [1.1, .62, .48], accent, [0, .31, 0]);
  box(counter, 'worktop', [1.15, .07, .54], white, [0, .66, 0]);
  // Physical support for the .6-scaled robot, attached to the saved counter.
  box(counter, 'cook-step', [.36, .30, .36], wood, [.25, .15, .65]);
  cyl(counter, 'sink', .17, .03, dark, [-.3, .71, 0]);
  box(counter, 'faucet', [.04, .18, .04], brass, [-.3, .78, -.15]);
  for (const x of [.1, .4]) cyl(counter, 'burner', .1, .02, dark, [x, .71, 0]);
  const pot = cyl(counter, 'pot', .12, .14, dark, [.25, .8, 0]);
  const steam = mount(counter, 'steam');
  for (let i = 0; i < 3; i++) ball(steam, 'puff', .04, white, [.25 + (i - 1) * .06, 1 + i * .12, 0]);
  const fridge = furniture.kitchen.fridge;
  box(fridge, 'fridge', [.5, 1.25, .5], white, [0, .625, 0]);
  box(fridge, 'freezer-seam', [.48, .02, .02], dark, [0, .9, .26]);
  box(fridge, 'handle', [.035, .23, .04], brass, [-.17, .62, .29]);
  table(furniture.kitchen.table, .65, .46, .5);
  cyl(furniture.kitchen.table, 'plate', .14, .025, white, [0, .52, 0]);
  table(furniture.workshop.bench, 1.25, .68, .55);
  const bench = furniture.workshop.bench;
  box(bench, 'pegboard', [1.2, .55, .045], dark, [0, 1.04, -.2]);
  for (let i = 0; i < 4; i++) {
    box(bench, 'tool-handle', [.035, .23, .04], brass, [-.42 + i * .27, 1.05, -.15]);
    box(bench, 'tool-head', [.12, .05, .05], white, [-.42 + i * .27, 1.17, -.15]);
  }
  const gear = mesh(bench, 'project-gear', new T.TorusGeometry(.12, .035, 8, 12), accent, [0, .83, .06]);
  cyl(furniture.workshop.stool, 'seat', .2, .08, accent, [0, .4, 0]);
  cyl(furniture.workshop.stool, 'stem', .05, .4, dark, [0, .2, 0]);
  shelves(furniture.workshop.shelf, .45, 1.3);
  box(furniture.fitness.mat, 'exercise-mat', [.85, .035, 1.15], accent, [0, .03, 0]);
  const dumbbell = mount(furniture.fitness.weights, 'dumbbell');
  box(dumbbell, 'bar', [.65, .06, .06], brass, [0, .16, 0]);
  for (const x of [-.26, .26]) { const w = cyl(dumbbell, 'weight', .15, .1, dark, [x, .16, 0]); w.rotation.z = Math.PI / 2; }
  const chair = furniture.reading.chair;
  box(chair, 'seat', [.53, .14, .5], accent, [0, .38, 0]);
  box(chair, 'backrest', [.53, .65, .14], accent, [0, .72, -.24]);
  for (const x of [-.29, .29]) box(chair, 'armrest', [.09, .35, .55], wood, [x, .5, 0]);
  for (const x of [-.2, .2]) for (const z of [-.18, .18]) box(chair, 'leg', [.06, .32, .06], dark, [x, .16, z]);
  shelves(furniture.reading.bookcase);
  table(furniture.reading.table, .4, .44, .4);
  const book = mount(chair, 'open-book'); book.position.set(0, .61, .23);
  for (const x of [-.09, .09]) {const page = box(book, 'page', [.18, .018, .23], paper, [x, 0, 0]); page.rotation.z = x > 0 ? .15 : -.15;}

  // Shared personal belongings follow the active room; do not duplicate robot.
  const personal = mount(model, 'personal');
  const lamp = mount(personal, 'lamp'); lamp.position.set(1.04, 0, .6);
  cyl(lamp, 'base', .13, .04, dark, [0, .03, 0]);
  cyl(lamp, 'stem', .025, 1.1, brass, [0, .57, 0]);
  const shade = mesh(lamp, 'shade', new T.ConeGeometry(.2, .25, 16, 1, true), accent, [0, 1.17, 0]);
  const bulbMat = mat('#fff1b5'); bulbMat.emissive.set('#ffd481');
  ball(lamp, 'bulb', .065, bulbMat, [0, 1.08, 0]);
  const light = new T.PointLight('#ffdc9a', .6, 3, 2); light.name = 'home-light'; light.position.set(0, 1.05, 0); lamp.add(light);
  const pet = mount(personal, 'pet'); const petMat = mat('#e9b76c');
  ball(pet, 'body', .11, petMat, [0, .14, 0]); ball(pet, 'head', .085, petMat, [0, .24, .07]);
  for (const x of [-.05, .05]) {
    mesh(pet, 'ear', new T.ConeGeometry(.04, .1, 8), petMat, [x, .32, .06]);
    ball(pet, 'eye', .012, dark, [x * .7, .26, .144]);
  }
  const tail = cyl(pet, 'tail', .018, .17, petMat, [0, .19, -.13]); tail.rotation.x = .6;
  const collection = mount(personal, 'inventory'); collection.position.set(0, 1.45, -.82);
  box(collection, 'display-shelf', [2.1, .04, .23], wood, [0, -.03, 0]);
  const collectibles = [];
  for (let i = 0; i < 12; i++) {
    const holder = mount(collection, `collectible:${i}`); holder.position.x = -.95 + i * .17;
    const m = mat('#ffd481');
    const outline = new T.Shape();
    for (let point = 0; point < 10; point++) {
      const angle = Math.PI / 2 + point * Math.PI / 5, radius = point % 2 ? .033 : .075;
      const x = Math.cos(angle) * radius, y = Math.sin(angle) * radius;
      if (point === 0) outline.moveTo(x, y); else outline.lineTo(x, y);
    }
    outline.closePath();
    const star = mesh(holder, 'star', new T.ExtrudeGeometry(outline, {depth: .025, bevelEnabled: false}), m, [0, .08, 0]);
    const crystal = mesh(holder, 'crystal', new T.OctahedronGeometry(.07), m, [0, .08, 0]); crystal.scale.y = 1.6;
    const badge = cyl(holder, 'badge', .065, .025, m, [0, .08, 0]); badge.rotation.x = Math.PI / 2;
    collectibles.push({holder, m, star, crystal, badge});
  }
  const scrapbook = mount(personal, 'scrapbook'); scrapbook.position.set(.5, .08, .64);
  box(scrapbook, 'cover', [.4, .035, .28], dark, [0, 0, 0]);
  const pages = [];
  for (let i = 0; i < 24; i++) {
    const page = box(scrapbook, `memory:${i}`, [.35, .012, .24], mat('#f8efd9'), [0, .025 + i * .002, 0]);
    pages.push(page);
  }
  const routineBoard = mount(personal, 'routine-board'); routineBoard.position.set(.65, 1.03, -.89);
  box(routineBoard, 'board', [.62, .5, .03], dark, [0, 0, 0]);
  const routineMarkers = [];
  for (let i = 0; i < 12; i++) routineMarkers.push(box(routineBoard, `routine:${i}`, [.1, .065, .025], accent, [-.21 + (i % 4) * .14, .15 - Math.floor(i / 4) * .13, .035]));
  let state = defaultHome(), effective = {room: state.room, activity: state.activity}, stopped = false, lastNow = 0, wakeFloor;
  function select() {
    for (const room of ROOMS) rooms[room].visible = effective.room === room;
    steam.visible = effective.activity === 'cook';
    book.visible = effective.activity === 'read';
    scrapbook.visible = state.scrapbook.length > 0;
  }
  function setState(value) {
    if (stopped || !validateHome(value)) return false;
    state = JSON.parse(JSON.stringify(value)); effective = {room: state.room, activity: state.activity};
    wakeFloor = undefined;
    accent.color.set(state.accent); petMat.color.set(state.pet.color);
    pet.visible = state.pet.enabled; pet.userData.name = state.pet.name;
    bulbMat.emissiveIntensity = state.lamp ? 1 : 0; light.intensity = state.lamp ? .6 : 0;
    for (const room of ROOMS) for (const [id, p] of Object.entries(state.furniture[room])) {
      furniture[room][id].position.set(...p.position.map(v => v / UNIT)); furniture[room][id].rotation.y = p.rotation;
    }
    collectibles.forEach((c, i) => {
      const item = state.inventory[i]; c.holder.visible = Boolean(item);
      for (const kind of ['star', 'crystal', 'badge']) c[kind].visible = item?.kind === kind;
      if (item) {c.m.color.set(item.color); c.holder.userData.id = item.id;}
    });
    pages.forEach((p, i) => {const entry = state.scrapbook[i]; p.visible = Boolean(entry);
      p.userData = entry ? {...entry} : {}; if (entry) p.material.color.set(entry.color);});
    routineBoard.visible = state.routines.entries.length > 0;
    routineMarkers.forEach((m, i) => {m.visible = Boolean(state.routines.entries[i]); m.userData = {...state.routines.entries[i]};});
    select(); animate(lastNow, true); return true;
  }
  function animate(now, reduced) {
    const t = reduced ? 0 : now / 1000;
    steam.position.y = reduced ? 0 : Math.sin(t * 2) * .04;
    gear.rotation.z = effective.activity === 'tinker' ? t : 0;
    dumbbell.position.y = effective.activity === 'exercise' ? (1 - Math.cos(t * 3)) * .16 : 0;
    const sleeping = effective.activity === 'sleep';
    pet.position.set(-.85 + (sleeping ? 0 : Math.sin(t * .6) * .12), .02, .64);
    pet.rotation.z = sleeping ? Math.PI / 2 : 0;
    tail.rotation.z = sleeping ? 0 : Math.sin(t * 4) * .4;
    if (effective.activity === 'pet') { pet.position.set(0, .02, .45); tail.rotation.z = Math.sin(t * 7) * .6; }
    pages.forEach((p, i) => {p.rotation.z = effective.activity === 'scrapbook' && i === Math.floor(t / 3) % Math.max(1, state.scrapbook.length) ? Math.sin(t * 2) * .35 : 0;});
  }
  function update(now, reducedMotion = false) {
    if (stopped || !group.visible) return;
    lastNow = Number.isFinite(now) ? now : 0;
    const date = new Date(); effective = resolveRoutine(state, date.getHours() * 60 + date.getMinutes());
    select(); animate(lastNow, Boolean(reducedMotion));
  }
  function freeWakeFloor() {
    if (wakeFloor !== undefined) return wakeFloor;
    group.updateWorldMatrix(true, true);
    const inverse = group.matrixWorld.clone().invert();
    const boundsOf = root => {
      const bounds = new T.Box3();
      root.traverseVisible(o => {
        if (!o.isMesh) return;
        if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
        bounds.union(o.geometry.boundingBox.clone().applyMatrix4(new T.Matrix4().multiplyMatrices(inverse, o.matrixWorld)));
      });
      return bounds;
    };
    const room = rooms.bedroom, floor = boundsOf(room.getObjectByName('floor'));
    const left = boundsOf(room.getObjectByName('left-wall')), back = boundsOf(room.getObjectByName('back-wall'));
    const radius = .062; // .30m rig * .6: actual half-width .0601m + margin
    const minX = Math.max(floor.min.x, left.max.x) + radius, maxX = floor.max.x - radius;
    const minZ = Math.max(floor.min.z, back.max.z) + radius, maxZ = floor.max.z - radius;
    const roots = [...Object.values(furniture.bedroom), lamp];
    if (pet.visible) roots.push(pet);
    if (scrapbook.visible) roots.push(scrapbook);
    const obstacles = roots.map(boundsOf).filter(b => !b.isEmpty());
    const bed = state.furniture.bedroom.bed;
    const wanted = new T.Vector3(.58 * UNIT, 0, .45 * UNIT)
      .applyAxisAngle(new T.Vector3(0, 1, 0), bed.rotation).add(new T.Vector3(...bed.position));
    const clear = (x, z) => obstacles.every(b => {
      const dx = Math.max(b.min.x - x, 0, x - b.max.x), dz = Math.max(b.min.z - z, 0, z - b.max.z);
      return dx * dx + dz * dz >= radius * radius - 1e-12;
    });
    // Obstacle/boundary-aligned candidates cover narrow clear aisles without
    // frame-dependent random probing or a coarse grid that skips usable gaps.
    const clampX = x => Math.max(minX, Math.min(maxX, x)), clampZ = z => Math.max(minZ, Math.min(maxZ, z));
    const xs = new Set([clampX(wanted.x), minX, maxX]), zs = new Set([clampZ(wanted.z), minZ, maxZ]);
    for (const b of obstacles) {
      xs.add(clampX(b.min.x - radius)); xs.add(clampX(b.max.x + radius));
      zs.add(clampZ(b.min.z - radius)); zs.add(clampZ(b.max.z + radius));
    }
    let best = null, distance = Infinity;
    for (const x of [...xs].sort((a,b) => a-b)) for (const z of [...zs].sort((a,b) => a-b)) {
      const d = (x - wanted.x) ** 2 + (z - wanted.z) ** 2;
      if (d < distance && clear(x, z)) {distance = d; best = new T.Vector3(x, 0, z);}
    }
    wakeFloor = best;
    return wakeFloor;
  }
  function activityPose() {
    let a = effective.activity;
    const neutral = {action: 'idle', position: [0, 0, .09], rotation: [0, 0, 0]};
    if (stopped) return neutral;
    const safeFloor = a === 'wake' ? freeWakeFloor() : null;
    if (a === 'wake' && !safeFloor) a = 'sleep';
    // -PI/2 points the upright rig's head toward -Z. Feet start at the
    // mattress FRONT (+Z), otherwise the head crosses the rear room wall.
    const target = {sleep: ['bed', [0, .51, .43], 'idle', [-Math.PI / 2, 0, 0]],
      wake: ['bed', [.58, 0, .45], 'stretch'], cook: ['counter', [.25, .30, .65], 'hologram'],
      tinker: ['stool', [0, .44, 0], 'hologram'], exercise: ['mat', [0, .05, 0], 'stretch'],
      read: ['chair', [0, .38, .12], 'read']}[a];
    if (a === 'scrapbook') return {action: 'read', position: [.12, 0, .14], rotation: [0, -.5, 0]};
    if (a === 'pet') return {action: 'curious', position: [0, 0, .04], rotation: [0, 0, 0]};
    if (!target) return neutral;
    const [id, offset, action, rotation] = target, f = state.furniture[effective.room][id];
    const v = new T.Vector3(...offset).multiplyScalar(UNIT).applyAxisAngle(new T.Vector3(0, 1, 0), f.rotation);
    v.add(new T.Vector3(...f.position));
    if (a === 'wake') v.copy(safeFloor);
    let facing = rotation || [0, f.rotation + (a === 'cook' || a === 'tinker' ? Math.PI : 0), 0];
    if (a === 'tinker') {
      const bench = state.furniture.workshop.bench.position;
      facing = [0, Math.atan2(bench[0] - v.x, bench[2] - v.z), 0];
    }
    if (rotation && f.rotation !== 0) {
      // Bed yaw acts in room space AFTER lying down. Setting Euler.y directly
      // composes in the wrong order and leaves the body's long axis off-bed.
      const q = new T.Quaternion().setFromEuler(new T.Euler(...rotation));
      q.premultiply(new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), f.rotation));
      facing = new T.Euler().setFromQuaternion(q, 'XYZ').toArray().slice(0, 3);
    }
    return {action, position: v.toArray(), rotation: [...facing]};
  }
  function dispose() {
    if (stopped) return; stopped = true; group.visible = false;
    group.removeFromParent(); ownedGeo.forEach(g => g.dispose()); ownedMat.forEach(m => m.dispose());
    light.dispose(); group.clear(); ownedGeo.clear(); ownedMat.clear();
  }
  setState(state);
  return {group, setState, update, activityPose, dispose};
}
