// Lightweight torso-mounted twin thrusters. No textures, particles, lights or extra timers.
export function exhaustState(now, flying, reducedMotion = false) {
  const active = !!flying && !reducedMotion;
  return { active, length: active ? 0.85 + 0.15 * Math.sin(now / 47) : 0,
    width: active ? 0.92 + 0.08 * Math.sin(now / 61) : 0 };
}

export function buildJetpack(T) {
  const group = new T.Group(); group.name = "jetpack";
  const shell = new T.MeshStandardMaterial({ color: 0x323e58, metalness: .75, roughness: .3 });
  const rim = new T.MeshStandardMaterial({ color: 0x7bd9ee, metalness: .6, roughness: .25 });
  const glow = new T.MeshBasicMaterial({ color: 0x35dfff, toneMapped: false });
  const hot = new T.MeshBasicMaterial({ color: 0xe7fcff, toneMapped: false });
  shell.name = "jetpack_joint_dark"; rim.name = "jetpack_accent";
  glow.name = hot.name = "jetpack_glow";
  const spine = new T.Mesh(new T.BoxGeometry(.074, .065, .025), shell);
  spine.position.set(0, .025, -.047); group.add(spine);
  const flames = [];
  for (const side of [-1, 1]) {
    const pod = new T.Mesh(new T.CylinderGeometry(.018, .021, .068, 12), shell);
    pod.position.set(side * .086, .024, -.035); group.add(pod);
    const nozzle = new T.Mesh(new T.CylinderGeometry(.021, .015, .015, 12), rim);
    nozzle.position.set(side * .086, -.015, -.035); group.add(nozzle);
    const band = new T.Mesh(new T.TorusGeometry(.019, .003, 6, 16), glow);
    band.rotation.x = Math.PI / 2; band.position.set(side * .086, .035, -.035); group.add(band);
    const exhaust = new T.Group(); exhaust.position.set(side * .086, -.023, -.035);
    const plume = new T.Mesh(new T.ConeGeometry(.015, .065, 10), glow);
    plume.rotation.z = Math.PI; plume.position.y = -.0325;
    const core = new T.Mesh(new T.ConeGeometry(.008, .04, 8), hot);
    core.rotation.z = Math.PI; core.position.set(0, -.02, .002);
    exhaust.add(plume, core); exhaust.visible = false; group.add(exhaust); flames.push(exhaust);
  }
  group.traverse(o => { if (o.isMesh) o.frustumCulled = false; });
  return { group, update(now, flying, reducedMotion) {
    const state = exhaustState(now, flying, reducedMotion);
    for (const flame of flames) { flame.visible = state.active; flame.scale.set(state.width, state.length, state.width); }
  } };
}
