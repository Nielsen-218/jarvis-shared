// Native FBX/glTF clips retain the imported rest pose. Existing gestures drive
// separate proxies so resetBones() cannot erase the character's bind rotations.
export function attachNativeAnimations(T, adapter, model, clips) {
  const mixer = new T.AnimationMixer(model);
  const real = {}, rest = {};
  for (const [key, bone] of Object.entries(adapter.bones)) {
    if (key === 'root') continue;
    real[key] = bone;
    adapter.bones[key] = new T.Object3D();
  }
  adapter.bones.root = adapter.group;
  model.traverse(o => { if (o.isBone) rest[o.uuid] = { object:o, position:o.position.clone(), quaternion:o.quaternion.clone(), scale:o.scale.clone() }; });
  const find = name => clips.find(c => c.name === name);
  const idleSource = find('Agree_Gesture') || clips[0];
  const idle = T.AnimationUtils.subclip(idleSource, 'Companion_Idle', 0, 2, 30);
  const choices = { wave:'Big_Wave_Hello', dance:'Hip_Hop_Dance_3', secret_shuffle:'Hip_Hop_Dance_3', shuffle:'Hip_Hop_Dance_3',
    wander:'Walking', happy:'Cheer_with_Both_Hands_1', laugh:'Cheer_with_Both_Hands_1', parkour:'Backflip', kick_trick:'Boxing_Guard_Right_Straight_Kick', sit:'Chair_Sit_Idle_M', perch:'Chair_Sit_Idle_M' };
  let current = null, last = null, mode = 'idle', idleKey = null, reducedMotion = false;
  adapter.updateAnimation = (nextMode, now, nextIdle = null, reduced = false) => { mode=nextMode; idleKey=nextIdle; reducedMotion=reduced; };
  adapter.sync = () => {
    const now = performance.now();
    const dt = last === null ? 0 : Math.min(0.1, Math.max(0, (now-last)/1000)); last=now;
    const key = reducedMotion ? null : choices[mode] || (mode === 'idle' ? choices[idleKey] : null);
    const clip = find(key) || idle;
    if (current?.getClip() !== clip) {
      current?.stop(); current=mixer.clipAction(clip); current.reset().play();
      current.paused = clip === idle;
    }
    for (const r of Object.values(rest)) { r.object.position.copy(r.position); r.object.quaternion.copy(r.quaternion); r.object.scale.copy(r.scale); }
    mixer.update(dt);
    for (const [key, bone] of Object.entries(real)) {
      const proxy=adapter.bones[key];
      bone.quaternion.multiply(proxy.quaternion);
      bone.scale.multiply(proxy.scale);
    }
  };
  adapter.capabilities.native_animations = clips.map(c=>c.name);
  adapter.capabilities.has_skeleton = true;
  const dispose=adapter.dispose;
  adapter.dispose=()=> { mixer.stopAllAction(); mixer.uncacheRoot(model); dispose(); };
  adapter.sync();
}
