// Real vendored Three; runnable standalone: node tests/node/test_home.mjs
import assert from 'node:assert/strict';
import * as T from '../../app/vendor/three/three.module.min.js';
import {buildHome, defaultHome, validateHome, resolveRoutine, ACTIONS, ROOMS} from '../../app/companion/home.js';
import {CLIPS, buildAnimator} from '../../app/companion/animation.js';
import {readFileSync} from 'node:fs';
import {computeNormalization, TARGET_HEIGHT_M} from '../../app/companion/glb-adapter.js';
import {buildRobotSkeleton, makeProxyBones, syncProxies} from '../../app/companion/robot-skin.js';

let passed = 0;
function check(condition, label) {assert.ok(condition, label); passed++;}
const clone = value => JSON.parse(JSON.stringify(value));
const home = buildHome(T), state = defaultHome();
check(home.group.isGroup && !home.group.visible, 'parent owns entry and visibility');
check(validateHome(state), 'default validates');
if (process.env.HOME_TEST_DEFAULT) {assert.deepEqual(state, JSON.parse(process.env.HOME_TEST_DEFAULT)); passed++;}
if (process.env.HOME_TEST_SNAPSHOT) check(home.setState(JSON.parse(process.env.HOME_TEST_SNAPSHOT)), 'Python persisted snapshot accepted');
check(home.setState(state), 'reset');
const find = name => home.group.getObjectByName(name);
const room = name => find(`room:${name}`);
const item = (r, id) => room(r).getObjectByName(`furniture:${id}`);
let meshes = 0;
home.group.traverse(o => {if (o.isMesh) {meshes++; check(o.geometry.isBufferGeometry && Boolean(o.material), 'real geometry/material');}});
check(meshes > 100, 'substantial furnished rooms');
// Box3 includes hidden rooms too; default full home must fit the metric frame.
const bounds = new T.Box3().setFromObject(home.group), size = bounds.getSize(new T.Vector3());
check(size.x >= .64 && size.x < .72, `metric width ${size.x}`);
check(size.z >= .45 && size.z < .52, `metric depth ${size.z}`);
check(size.y >= .39 && size.y < .45, `metric height ${size.y}`);
for (const r of ROOMS) {
  check(home.setState({...state, room: r}), `${r} selected`);
  check(ROOMS.filter(n => room(n).visible).join() === r, `${r} only visible room`);
  let count = 0; room(r).traverse(o => {if (o.isMesh) count++;});
  check(count >= 12, `${r} furnished`);
}
check(item('bedroom', 'bed').getObjectByName('pillow').isMesh, 'bed and pillow');
check(item('kitchen', 'counter').getObjectByName('sink').isMesh, 'sink');
check(item('workshop', 'bench').getObjectByName('pegboard').isMesh, 'work tools');
check(item('fitness', 'weights').getObjectByName('dumbbell').isGroup, 'fitness equipment');
check(item('reading', 'bookcase').getObjectByName('book').isMesh, 'actual books');

const roomFor = {sleep:'bedroom', wake:'bedroom', cook:'kitchen', tinker:'workshop', exercise:'fitness', read:'reading'};
home.group.visible = true;
for (const a of ACTIONS) {
  const s = {...defaultHome(), room: roomFor[a] || 'bedroom', activity: a};
  check(home.setState(s), `${a} accepted`); home.update(1200, false);
  const p = home.activityPose();
  check(Boolean(CLIPS[p.action]), `${a} reuses existing clip`);
  check(p.position.length === 3 && [...p.position, ...p.rotation].every(Number.isFinite), `${a} finite mount pose`);
  // Real animator contract: reuse semantic rig nodes rather than a home robot.
  const bones = Object.fromEntries(['root', 'head', 'shoulderL', 'shoulderR', 'hipL', 'hipR'].map(k => [k, new T.Group()]));
  buildAnimator(bones).play('idle', 1200, p.action, 100);
  check(Object.values(bones).every(b => b.rotation.toArray().slice(0, 3).every(Number.isFinite)), `${a} plays on existing rig`);
}
home.setState({...state, activity:'sleep'});
const originalPose = home.activityPose();
check(originalPose.rotation[0] === -Math.PI/2 && originalPose.position[1] > .1, 'sleep lies on mattress');
// Reproduce native clipping using the REAL vendored robot POSITION vertices,
// adapter normalization and parent's .6 mount. No textures/DOM/loader mocks.
const glb = readFileSync(new URL('../../app/companion/models/robot/robot-lod1-multimat.glb', import.meta.url));
const jsonLength = glb.readUInt32LE(12), document = JSON.parse(glb.subarray(20, 20 + jsonLength));
const binOffset = 20 + jsonLength + 8, robotModel = new T.Group();
const robotMat = new T.MeshBasicMaterial(), robotGeometries = [];
check(document.nodes.length===1 && !document.nodes[0].matrix && !document.nodes[0].translation && !document.nodes[0].rotation && !document.nodes[0].scale,
  'vendored robot POSITION frame has no node transform');
for (const primitive of document.meshes[0].primitives) {
  const a=document.accessors[primitive.attributes.POSITION], v=document.bufferViews[a.bufferView];
  check(a.componentType===5126 && a.type==='VEC3' && !a.sparse, 'actual robot float positions');
  const points=new Float32Array(a.count*3), view=new DataView(glb.buffer,glb.byteOffset,glb.byteLength);
  for(let i=0;i<a.count;i++) for(let axis=0;axis<3;axis++)
    points[i*3+axis]=view.getFloat32(binOffset+(v.byteOffset||0)+(a.byteOffset||0)+i*(v.byteStride||12)+axis*4,true);
  const geometry=new T.BufferGeometry(); geometry.setAttribute('position',new T.BufferAttribute(points,3));
  robotGeometries.push(geometry); robotModel.add(new T.Mesh(geometry,robotMat));
}
const rawBounds=new T.Box3().setFromObject(robotModel), normalized=new T.Group();
const norm=computeNormalization(rawBounds,TARGET_HEIGHT_M,1);
normalized.scale.setScalar(norm.scale); normalized.position.set(norm.offset.x,norm.offset.y,norm.offset.z); normalized.add(robotModel);
const robotMount=new T.Group(); robotMount.scale.setScalar(.6); robotMount.add(normalized);
function robotBedBounds(pose) {
  // Home uses an internal .24 design scale; mount coordinates are metric.
  home.group.updateMatrixWorld(true); robotMount.position.set(...pose.position); robotMount.rotation.set(...pose.rotation);
  robotMount.updateMatrixWorld(true);
  const bounds=new T.Box3().setFromObject(robotMount,true), inverse=item('bedroom','bed').matrixWorld.clone().invert();
  // Convert the ACTUAL vertices, not a rotated world AABB, to bed-local space.
  const local=new T.Box3(), point=new T.Vector3();
  robotMount.traverse(o=>{if(o.isMesh){const p=o.geometry.getAttribute('position');
    for(let i=0;i<p.count;i++) local.expandByPoint(point.fromBufferAttribute(p,i).applyMatrix4(o.matrixWorld).applyMatrix4(inverse));}});
  return {bounds,local};
}
const resting=robotBedBounds(originalPose);
check(resting.bounds.min.z > -.95*.24, `sleep head stays in front of back wall (z=${resting.bounds.min.z})`);
check(resting.local.min.z>=-.65 && resting.local.max.z<=.65, 'entire .6-scaled robot fits mattress length');
check(resting.local.min.x>=-.41 && resting.local.max.x<=.41, 'entire .6-scaled robot fits mattress width');
const moved = defaultHome(); moved.activity='sleep';
moved.furniture.bedroom.bed.position[0] += .06; moved.furniture.bedroom.bed.rotation = Math.PI/2;
home.setState(moved);
check(Math.abs(item('bedroom','bed').position.x - moved.furniture.bedroom.bed.position[0]/.24) < 1e-9, 'saved metric furniture placement');
for(const yaw of [-Math.PI,-Math.PI/2,0,Math.PI/2,Math.PI]) {
  moved.furniture.bedroom.bed.rotation=yaw; home.setState(moved);
  const resting=robotBedBounds(home.activityPose()).local;
  check(resting.min.z>=-.65 && resting.max.z<=.65 && resting.min.x>=-.41 && resting.max.x<=.41,
    `sleeping robot fits moved and rotated mattress at yaw ${yaw}`);
}
moved.furniture.bedroom.bed.rotation=Math.PI/2; home.setState(moved);
check(Math.abs(home.activityPose().position[2] - moved.furniture.bedroom.bed.position[2]) < 1e-8, 'sleep offset rotates with bed');
robotGeometries.forEach(g=>g.dispose()); robotMat.dispose();
moved.furniture.bedroom.bed.position[0] = 0;
check(item('bedroom','bed').position.x !== 0, 'state is defensively copied');
home.setState({...state, activity:'wake'});
check(home.activityPose().action==='stretch' && home.activityPose().rotation[0]===0, 'wake stands and stretches');

// Ground the cook on visible geometry and the tinkerer on the saved stool.
const rigSpec=JSON.parse(readFileSync(new URL('../../app/companion/rigs/robot-rig.json',import.meta.url),'utf8'));
function wristHeights(pose) {
  const skel=buildRobotSkeleton(T,rigSpec), proxies=makeProxyBones(T,rigSpec), mount=new T.Group();
  mount.scale.setScalar(.6);mount.position.set(...pose.position);mount.rotation.set(...pose.rotation);mount.add(skel.root);
  buildAnimator(proxies).play('idle',1300,pose.action,100);syncProxies(T,skel.bones,proxies,rigSpec);mount.updateMatrixWorld(true);
  return ['wristL','wristR'].map(k=>skel.bones[k].getWorldPosition(new T.Vector3()).y);
}
function feetOnSupport(pose,support) {
  home.group.updateMatrixWorld(true);
  support.geometry.computeBoundingBox();
  const feet=support.worldToLocal(home.group.localToWorld(new T.Vector3(...pose.position))), b=support.geometry.boundingBox;
  return Math.abs(feet.y-b.max.y)<1e-6 && feet.x>=b.min.x && feet.x<=b.max.x && feet.z>=b.min.z && feet.z<=b.max.z;
}
home.setState({...state,room:'kitchen',activity:'cook'});
check(Boolean(find('cook-step')?.isMesh),'cook has a visible physical step');
const cookPose=home.activityPose();
check(feetOnSupport(cookPose,find('cook-step')),'cook feet sit exactly on step top');
const counterTop=new T.Box3().setFromObject(item('kitchen','counter').getObjectByName('worktop')).max.y;
check(wristHeights(cookPose).every(y=>Math.abs(y-counterTop)<.03),'source rig hologram wrists reach within 3cm of worktop from step');
const changedCounter=defaultHome();changedCounter.room='kitchen';changedCounter.activity='cook';
changedCounter.furniture.kitchen.counter.position=[-.08,0,-.12];changedCounter.furniture.kitchen.counter.rotation=.6;
home.setState(changedCounter);check(feetOnSupport(home.activityPose(),find('cook-step')),'cook stays supported after counter movement and yaw');
home.setState({...state,room:'workshop',activity:'tinker'});
const tinkerPose=home.activityPose(), stoolSeat=item('workshop','stool').getObjectByName('seat');
check(feetOnSupport(tinkerPose,stoolSeat),'tinker feet sit exactly on actual stool seat');
const benchTop=new T.Box3().setFromObject(item('workshop','bench').getObjectByName('top')).max.y;
check(wristHeights(tinkerPose).every(y=>Math.abs(y-benchTop)<.015),'source rig wrists reach workshop worktop height');
const changedStool=defaultHome();changedStool.room='workshop';changedStool.activity='tinker';
changedStool.furniture.workshop.stool.position=[.05,0,.03];changedStool.furniture.workshop.stool.rotation=-.8;
home.setState(changedStool);check(feetOnSupport(home.activityPose(),stoolSeat),'tinker follows saved stool layout');

function wakeFloorClear(pose) {
  home.group.updateMatrixWorld(true);
  const [x,y,z]=pose.position, radius=.05;
  if(y!==0 || x-radius<-.3168 || x+radius>.324 || z-radius<-.2208 || z+radius>.228) return false;
  const obstacles=[...room('bedroom').children.filter(o=>o.name.startsWith('furniture:')),find('lamp')];
  for(const root of obstacles) {
    const b=new T.Box3().setFromObject(root), dx=Math.max(b.min.x-x,0,x-b.max.x), dz=Math.max(b.min.z-z,0,z-b.max.z);
    if(dx*dx+dz*dz < radius*radius-1e-10) return false;
  }
  return true;
}
const wakeMoved=defaultHome();wakeMoved.activity='wake';wakeMoved.lamp=false;wakeMoved.furniture.bedroom.bed.position[0]=.08;
home.setState(wakeMoved);
check(wakeFloorClear(home.activityPose()),'wake from native moved-bed x=.08 has free floor, clear of lamp/wardrobe/bed with .05m radius');
check(home.activityPose().action==='stretch','safe waking still stretches upright');
const repeatedPose=home.activityPose();
check(JSON.stringify(home.activityPose())===JSON.stringify(repeatedPose),'safe wake placement stable across repeated frames');
for(const [x,z,yaw]of [[.08,-.024,0],[.24,-.168,.6],[-.24,.168,-Math.PI/2],[0,0,Math.PI]]) {
  wakeMoved.furniture.bedroom.bed.position=[x,0,z];wakeMoved.furniture.bedroom.bed.rotation=yaw;home.setState(wakeMoved);
  check(wakeFloorClear(home.activityPose()),`wake finds free floor after bed movement/yaw ${x},${z},${yaw}`);
  check(Math.abs(item('bedroom','bed').position.x-x/.24)<1e-9,'wake fallback preserves creative furniture placement');
}

const personal = defaultHome(); personal.lamp=false; personal.accent='#ff00aa'; personal.pet.name='Pixel'; personal.pet.color='#112233';
personal.inventory=[{id:'award',kind:'star',color:'#ffcc00'}, {id:'gem',kind:'crystal',color:'#aaeeff'}, {id:'medal',kind:'badge',color:'#ddccaa'}];
personal.scrapbook=[{title:'Home',text:'A memory',color:'#aabbcc'}, {title:'Workshop',text:'Built a gear',color:'#ccffaa'}];
home.setState(personal);
check(find('home-light').intensity===0 && find('bulb').material.emissiveIntensity===0, 'lamp off changes physical and emissive light');
check(find('shade').material.color.getHexString()==='ff00aa', 'accent applied');
check(find('pet').userData.name==='Pixel' && find('body').material.color.getHexString()==='112233', 'pet identity and color');
for (const [i,kind] of ['star','crystal','badge'].entries()) {
  check(find(`collectible:${i}`).visible && find(`collectible:${i}`).getObjectByName(kind).visible, `${kind} displayed`);
}
check(find('collectible:0').getObjectByName('star').geometry.type==='ExtrudeGeometry', 'star has a five-point silhouette distinct from crystal');
check(!find('collectible:3').visible, 'unused inventory hidden');
check(find('memory:0').userData.text==='A memory' && !find('memory:2').visible, 'scrapbook actual saved pages');
personal.activity='scrapbook'; home.setState(personal); home.update(700, false);
check(find('memory:0').rotation.z!==0, 'scrapbook page turns');
home.update(700, true); check(find('memory:0').rotation.z===0, 'reduced motion freezes page');
personal.pet.enabled=false; home.setState(personal); check(!find('pet').visible, 'pet disabled');

for (const [r,a,n,axis] of [['kitchen','cook','steam','y'], ['workshop','tinker','project-gear','z'], ['fitness','exercise','dumbbell','y']]) {
  home.setState({...state,room:r,activity:a}); home.update(1300,false);
  const obj=find(n), transform = n==='project-gear' ? obj.rotation : obj.position;
  check(transform[axis]!==0, `${a} prop animates`);
  home.update(1300,true); check(transform[axis]===0, `${a} reduced motion resets prop`);
}
home.setState(state); home.update(1000,false); const petX=find('pet').position.x;
home.update(2000,false); check(find('pet').position.x!==petX,'pet moves');
home.update(2000,true); check(find('pet').position.x===-.85,'reduced motion stops pet');
home.group.visible=false; home.update(9000,false); check(find('pet').position.x===-.85,'hidden home does no animation');
home.group.visible=true;

const routine=defaultHome(); routine.routines={enabled:true,entries:[
  {time:'22:00',room:'bedroom',activity:'sleep'}, {time:'07:00',room:'kitchen',activity:'cook'},
  {time:'09:00',room:'workshop',activity:'tinker'}]};
for (const [minute,a] of [[0,'sleep'], [419,'sleep'],[420,'cook'],[540,'tinker'],[1320,'sleep']])
  check(resolveRoutine(routine,minute).activity===a,`routine boundary ${minute}`);
home.setState(routine); home.update(0,true);
const d=new Date(), current=resolveRoutine(routine,d.getHours()*60+d.getMinutes());
check(room(current.room).visible,'routine switches rendered room');
check(find('routine:0').visible && find('routine:0').userData.time==='22:00','routine board state');
routine.routines.enabled=false; check(resolveRoutine(routine,0).activity==='idle','manual routine override');

for (const mutate of [s=>s.room='bogus', s=>s.schema=2, s=>s.lamp=1, s=>s.activity='cook',
  s=>s.pet.enabled='true', s=>s.furniture.bedroom.bed.position[0]=Infinity,
  s=>s.furniture.bedroom.bed.rotation=NaN, s=>s.accent='url(foo)',
  s=>s.scrapbook=Array(25).fill({}), s=>s.unknown=true, s=>s.routines.entries=[{time:'25:00',room:'bedroom',activity:'sleep'}]]) {
  const s=clone(state); mutate(s); check(!home.setState(s),'malformed snapshot rejected atomically');
}
check(!home.setState(null), 'null rejected');
// Disposal events on real geometries/materials, exactly once even if shared.
const geometries=new Set(), materials=new Set();
home.group.traverse(o=>{if(o.isMesh){geometries.add(o.geometry);materials.add(o.material);}});
let gd=0,md=0;
geometries.forEach(g=>g.addEventListener('dispose',()=>gd++)); materials.forEach(m=>m.addEventListener('dispose',()=>md++));
const scene=new T.Scene();scene.add(home.group); home.dispose();home.dispose();
check(gd===geometries.size && md===materials.size,'all owned resources disposed exactly once');
check(!home.group.parent && home.group.children.length===0 && !home.group.visible,'disposed home detached');
check(!home.setState(state),'disposed instance refuses mutation');
home.update(Infinity,false); check(home.activityPose().action==='idle','post-disposal calls safe');
console.log(JSON.stringify({schema:1,passed,failed:0}));
