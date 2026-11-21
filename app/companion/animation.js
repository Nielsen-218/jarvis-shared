// Named-bone animation adapter. Operates ONLY on the semantic bones
// defined in core/companion_profile.py (the robot rig in rigs/robot-rig.json provides all of them),
// so any model/adapter that honors that contract can be driven by the same
// clip registry. Each clip is a plain data record: `{ name, duration, apply }`.
// `apply` takes (bones, t, now) where `t` is normalized progress in [0, 1].
//
// There is NO heavy animation system here — three.js's AnimationMixer is too
// much machinery for the handful of poses we need, and the clips are the
// easiest piece to review. If a real skeletal asset lands in Phase 2, we'll
// register a parallel "mixer" adapter and let the clip registry pick between
// them per adapter capabilities.

export const CLIPS = {
  idle: {
    name: "idle",
    duration: 2.4,
    apply(bones, t, now) {
      // Gentle breathing: slight torso scale + arm sway.
      const breath = 1 + Math.sin(now / 1300) * 0.012;
      bones.torso && bones.torso.scale.set(breath, breath, breath);
      if (bones.shoulderL) bones.shoulderL.rotation.z = -0.25 + Math.sin(now / 1500) * 0.03;
      if (bones.shoulderR) bones.shoulderR.rotation.z = 0.25 + Math.sin(now / 1500 + 0.4) * 0.03;
      // Head drift (keeps it alive during long idles)
      if (bones.head) {
        bones.head.rotation.y = Math.sin(now / 1700) * 0.06;
        bones.head.rotation.z = Math.sin(now / 2300) * 0.02;
      }
    },
  },
  listening: {
    name: "listening",
    duration: 1.6,
    apply(bones, t, now) {
      // Head tilts up slightly, ears perked (rotate ear bones).
      if (bones.head) bones.head.rotation.x = -0.1;
      if (bones.earL) bones.earL.rotation.z = -0.1 + Math.sin(now / 400) * 0.03;
      if (bones.earR) bones.earR.rotation.z =  0.1 + Math.sin(now / 400 + 1.0) * 0.03;
    },
  },
  thinking: {
    name: "thinking",
    duration: 1.8,
    apply(bones, t, now) {
      // Head tilts down and to the right.
      if (bones.head) {
        bones.head.rotation.x = 0.14;
        bones.head.rotation.z = 0.09;
      }
      // Right hand up toward chin.
      if (bones.shoulderR) bones.shoulderR.rotation.z = 1.0 + Math.sin(now / 600) * 0.05;
      if (bones.elbowR) bones.elbowR.rotation.x = -1.4;
    },
  },
  wave: {
    name: "wave",
    duration: 1.2,
    apply(bones, t, now) {
      if (bones.shoulderR) {
        bones.shoulderR.rotation.z = 1.6;
        bones.shoulderR.rotation.x = -1.0;
      }
      if (bones.elbowR) bones.elbowR.rotation.z = Math.sin(now / 150) * 0.5;
      if (bones.head) bones.head.rotation.y = 0.1 * Math.sin(now / 500);
    },
  },
  stretch: {
    name: "stretch",
    duration: 1.4,
    apply(bones, t, now) {
      const s = Math.sin(t * Math.PI);
      if (bones.shoulderL) bones.shoulderL.rotation.z = -0.25 - 0.5 * s;
      if (bones.shoulderR) bones.shoulderR.rotation.z =  0.25 + 0.5 * s;
      if (bones.head) bones.head.rotation.x = -0.1 - 0.1 * s;
    },
  },
  look_around: {
    name: "look_around",
    duration: 3.2,
    apply(bones, t, now) {
      if (bones.head) {                              // a slow, curious sweep side to side (and a little up)
        bones.head.rotation.y = Math.sin(now / 900) * 0.5;
        bones.head.rotation.x = -0.06 + Math.sin(now / 1700) * 0.08;
      }
      if (bones.earL) bones.earL.rotation.z = -0.06 + Math.sin(now / 600) * 0.04;
      if (bones.earR) bones.earR.rotation.z = 0.06 + Math.sin(now / 600 + 1) * 0.04;
    },
  },
  ponder: {
    name: "ponder",
    duration: 2.6,
    apply(bones, t, now) {
      if (bones.head) { bones.head.rotation.x = -0.18; bones.head.rotation.z = 0.08; }   // chin up, thinking
      if (bones.shoulderR) bones.shoulderR.rotation.z = 0.9;                              // hand toward chin
      if (bones.elbowR) bones.elbowR.rotation.x = -1.5;
      if (bones.ankleR) bones.ankleR.rotation.x = Math.max(0, Math.sin(now / 240)) * 0.3; // foot tap
    },
  },
  yawn: {
    name: "yawn",
    duration: 2.8,
    apply(bones, t, now) {
      const o = Math.sin(Math.min(1, t * 1.3) * Math.PI);     // rise then settle
      if (bones.head) bones.head.rotation.x = -0.3 * o + 0.12 * t;   // tip back, then nod down drowsily
      if (bones.shoulderL) bones.shoulderL.rotation.z = -0.25 - 0.8 * o;   // stretch arms up
      if (bones.shoulderR) bones.shoulderR.rotation.z = 0.25 + 0.8 * o;
      if (bones.mouthOpen) { bones.mouthOpen.visible = o > 0.3; bones.mouthOpen.scale?.set?.(1, 0.1 + o * 1.5, 1); }
    },
  },
  dance: {
    name: "dance",
    duration: 3.0,
    apply(bones, t, now) {
      const beat = now / 260;                         // bob + sway + arm pump to a beat
      if (bones.root) { bones.root.position.x = (bones.root.userData.restX ?? 0) + Math.sin(beat) * 0.02; bones.root.rotation.z = Math.sin(beat) * 0.12; }
      if (bones.torso) bones.torso.rotation.z = Math.sin(beat) * 0.1;
      if (bones.head) bones.head.rotation.z = Math.sin(beat) * 0.15;
      if (bones.shoulderL) bones.shoulderL.rotation.z = -0.4 + Math.sin(beat) * 0.9;
      if (bones.shoulderR) bones.shoulderR.rotation.z = 0.4 - Math.sin(beat) * 0.9;
      if (bones.hipL) bones.hipL.rotation.x = Math.max(0, Math.sin(beat)) * 0.3;
      if (bones.hipR) bones.hipR.rotation.x = Math.max(0, -Math.sin(beat)) * 0.3;
    },
  },
  wander: {
    name: "wander",
    duration: 0.9,
    apply(bones, t, now) {                            // a walk cycle; companion.html moves the window meanwhile
      const step = now / 150;
      if (bones.hipL) bones.hipL.rotation.x = Math.sin(step) * 0.6;
      if (bones.hipR) bones.hipR.rotation.x = -Math.sin(step) * 0.6;
      if (bones.kneeL) bones.kneeL.rotation.x = Math.max(0, -Math.sin(step)) * 0.5;
      if (bones.kneeR) bones.kneeR.rotation.x = Math.max(0, Math.sin(step)) * 0.5;
      if (bones.shoulderL) bones.shoulderL.rotation.x = -Math.sin(step) * 0.4;
      if (bones.shoulderR) bones.shoulderR.rotation.x = Math.sin(step) * 0.4;
      if (bones.torso) bones.torso.rotation.z = Math.sin(step) * 0.04;
    },
  },
  sit: {
    name: "sit",
    duration: 3.2,
    apply(bones, t, now) {                            // sits down and swings his legs
      if (bones.root) bones.root.position.y -= 0.045;
      if (bones.hipL) bones.hipL.rotation.x = 1.4;
      if (bones.hipR) bones.hipR.rotation.x = 1.4;
      if (bones.kneeL) bones.kneeL.rotation.x = -1.1 + Math.sin(now / 300) * 0.35;        // dangling feet
      if (bones.kneeR) bones.kneeR.rotation.x = -1.1 + Math.sin(now / 300 + 0.7) * 0.35;
      if (bones.shoulderL) bones.shoulderL.rotation.set(0.3, 0, -0.2);
      if (bones.shoulderR) bones.shoulderR.rotation.set(0.3, 0, 0.2);
      if (bones.head) bones.head.rotation.x = Math.sin(now / 1500) * 0.08;
    },
  },
  perch: {
    name: "perch",
    duration: 3.5,
    apply(bones, t, now) { CLIPS.sit.apply(bones, t, now); },   // sits; companion.html walks him to the edge first
  },
  laugh: {
    name: "laugh",
    duration: 2.0,
    apply(bones, t, now) {                            // shakes with mirth, head tips back
      const q = Math.sin(now / 90);
      if (bones.torso) bones.torso.rotation.x = -0.12 + q * 0.06;
      if (bones.head) { bones.head.rotation.x = -0.25 + Math.abs(q) * 0.12; bones.head.rotation.z = q * 0.05; }
      if (bones.root) bones.root.position.y += Math.abs(q) * 0.015;        // bounces
      if (bones.shoulderL) bones.shoulderL.rotation.z = -0.4 - Math.abs(q) * 0.3;
      if (bones.shoulderR) bones.shoulderR.rotation.z = 0.4 + Math.abs(q) * 0.3;
    },
  },
  curious: {
    name: "curious",
    duration: 0.9,
    apply(bones, t, now) { CLIPS.wander.apply(bones, t, now); },   // walk while he investigates; companion.html moves him toward the cursor
  },
  ledge_sit: { name: "ledge_sit", duration: 6,
    apply(b,t,now) { CLIPS.sit.apply(b,t,now); if(b.head) b.head.rotation.y=Math.sin(now/2400)*.18; } },
  ledge_cross: { name: "ledge_cross", duration: 6,
    apply(b,t,now) { CLIPS.sit.apply(b,t,now); if(b.hipL) b.hipL.rotation.z=.28; if(b.ankleL) b.ankleL.rotation.z=-.2; } },
  ledge_lean: { name: "ledge_lean", duration: 6,
    apply(b,t,now) { CLIPS.sit.apply(b,t,now); if(b.torso) b.torso.rotation.z=.18; if(b.shoulderR) b.shoulderR.rotation.z=.8; } },
  ledge_hang: { name: "ledge_hang", duration: 4,
    apply(b,t,now) { if(b.shoulderL) b.shoulderL.rotation.z=-2.7; if(b.shoulderR) b.shoulderR.rotation.z=2.7;
      if(b.kneeL) b.kneeL.rotation.x=-.3+Math.sin(now/500)*.15; if(b.head) b.head.rotation.x=-.15; } },
  peek: { name: "peek", duration: 4,
    apply(b,t,now) { if(b.torso) b.torso.rotation.z=.25; if(b.head) {b.head.rotation.z=-.2;b.head.rotation.y=Math.sin(now/1800)*.25;} } },
  salute: {
    name: "salute", duration: 2.0,
    apply(bones, t) {
      const lift = Math.sin(Math.PI * t) ** .5;
      if (bones.shoulderR) bones.shoulderR.rotation.set(-.8 * lift, 0, 1.4 * lift);
      if (bones.elbowR) bones.elbowR.rotation.x = -1.3 * lift;
      if (bones.head) bones.head.rotation.x = -.12 * lift;
      if (bones.torso) bones.torso.rotation.z = -.06 * lift;
    },
  },
  double_take: {
    name: "double_take", duration: 2.2,
    apply(bones, t) {
      const envelope = Math.sin(Math.PI * t);
      if (bones.head) { bones.head.rotation.y = Math.sin(t * Math.PI * 3) * .65 * envelope; bones.head.rotation.z = .18 * envelope; }
      if (bones.shoulderL) bones.shoulderL.rotation.z = -.25 - .25 * envelope;
      if (bones.shoulderR) bones.shoulderR.rotation.z = .25 + .25 * envelope;
    },
  },
  shuffle: {
    name: "shuffle", duration: 3.0,
    apply(bones, t) {
      const beat = Math.sin(t * Math.PI * 12) * Math.sin(t * Math.PI);
      if (bones.torso) bones.torso.rotation.z = beat * .16;
      if (bones.head) bones.head.rotation.z = -beat * .12;
      if (bones.hipL) bones.hipL.rotation.x = beat * .35;
      if (bones.hipR) bones.hipR.rotation.x = -beat * .35;
      if (bones.shoulderL) bones.shoulderL.rotation.set(beat * .5, 0, -.5);
      if (bones.shoulderR) bones.shoulderR.rotation.set(-beat * .5, 0, .5);
    },
  },
  catch_ready: {name:"catch_ready",duration:2,
    apply(b,t,now) {if(b.shoulderL)b.shoulderL.rotation.set(-.85,0,-.4);if(b.shoulderR)b.shoulderR.rotation.set(-.85,0,.4);
      if(b.elbowL)b.elbowL.rotation.x=-.8;if(b.elbowR)b.elbowR.rotation.x=-.8;if(b.head)b.head.rotation.x=.12;}},
  kick_ready: {name:"kick_ready",duration:2,
    apply(b,t,now) {if(b.hipR)b.hipR.rotation.x=-.2;if(b.kneeR)b.kneeR.rotation.x=-.4;if(b.shoulderL)b.shoulderL.rotation.z=-.5;}},
  kick_trick: {name:"kick_trick",duration:.45,
    apply(b,t,now) {const k=Math.sin(Math.PI*t);if(b.hipR)b.hipR.rotation.x=.8*k;if(b.kneeR)b.kneeR.rotation.x=-.6*k;if(b.torso)b.torso.rotation.z=-.12*k;}},
  secret_shuffle: {
    name:"secret_shuffle",duration:4.8,
    apply(b,t,now) {
      CLIPS.shuffle.apply(b,t,now);
      if(b.root) {b.root.rotation.y=Math.sin(t*Math.PI)*Math.PI*2;b.root.position.x+=Math.sin(t*Math.PI*4)*.025;}
      if(b.head) b.head.rotation.z=Math.sin(t*Math.PI*8)*.15;
    },
  },
  fly: {
    name: "fly",
    duration: 1.0,
    apply(bones, t, now) {                            // superman glide: lean forward, arms back, legs trailing
      const g = Math.sin(now / 160) * 0.05;
      if (bones.torso) bones.torso.rotation.x = -0.5;
      if (bones.head) bones.head.rotation.x = -0.4;
      if (bones.shoulderL) bones.shoulderL.rotation.set(2.4, 0, -0.3 + g);
      if (bones.shoulderR) bones.shoulderR.rotation.set(2.4, 0, 0.3 - g);
      if (bones.hipL) bones.hipL.rotation.x = -0.3; if (bones.hipR) bones.hipR.rotation.x = -0.3;
      if (bones.kneeL) bones.kneeL.rotation.x = -0.2 + g; if (bones.kneeR) bones.kneeR.rotation.x = -0.2 - g;
    },
  },
  parkour: {
    name: "parkour",
    duration: 0.9,
    apply(bones, t, now) {                            // tucked hop pose (companion.html arcs the window between ledges)
      if (bones.hipL) bones.hipL.rotation.x = 1.1; if (bones.hipR) bones.hipR.rotation.x = 1.1;
      if (bones.kneeL) bones.kneeL.rotation.x = -1.3; if (bones.kneeR) bones.kneeR.rotation.x = -1.3;
      if (bones.shoulderL) bones.shoulderL.rotation.set(-0.6, 0, -0.5); if (bones.shoulderR) bones.shoulderR.rotation.set(-0.6, 0, 0.5);
      if (bones.head) bones.head.rotation.x = -0.1;
    },
  },
  play: {
    name: "play",
    duration: 1.0,
    apply(bones, t, now) { if (bones.head) bones.head.rotation.x = 0.25; },   // glance down at the toy (runPlay overrides when active)
  },
  read: {
    name: "read",
    duration: 6.0,
    apply(bones, t, now) {                            // sits and reads a tiny book (companion.html shows the prop)
      CLIPS.sit.apply(bones, t, now);
      if (bones.kneeL) bones.kneeL.rotation.x = -1.1; if (bones.kneeR) bones.kneeR.rotation.x = -1.1;   // feet still
      if (bones.head) { bones.head.rotation.x = 0.38; bones.head.rotation.y = Math.sin(now / 2600) * 0.06; }   // eyes on the page
      if (bones.shoulderL) bones.shoulderL.rotation.set(-0.9, 0, -0.35);   // both hands holding it up
      if (bones.shoulderR) bones.shoulderR.rotation.set(-0.9, 0, 0.35);
      const turn = Math.max(0, Math.sin(now / 1900)) ** 8;                  // now and then, a page turns
      if (bones.elbowR) bones.elbowR.rotation.z = turn * 0.6;
    },
  },
  hologram: {
    name: "hologram",
    duration: 5.0,
    apply(bones, t, now) {                            // cups a little spinning hologram toy in both hands
      if (bones.head) { bones.head.rotation.x = 0.28; bones.head.rotation.z = Math.sin(now / 1400) * 0.05; }
      if (bones.shoulderL) bones.shoulderL.rotation.set(-0.75, 0, -0.45 + Math.sin(now / 900) * 0.04);
      if (bones.shoulderR) bones.shoulderR.rotation.set(-0.75, 0, 0.45 - Math.sin(now / 900) * 0.04);
      if (bones.elbowL) bones.elbowL.rotation.x = -0.5; if (bones.elbowR) bones.elbowR.rotation.x = -0.5;
      if (bones.root) bones.root.rotation.z = Math.sin(now / 1800) * 0.03;  // a contented little sway
    },
  },
  weight_shift: {
    name: "weight_shift",
    duration: 1.2,
    apply(bones, t, now) {
      const sway = Math.sin(now / 500) * 0.03;     // metres; the robot is 0.30 m tall in a window barely wider than it
      if (bones.root) {
        bones.root.position.x = (bones.root.userData.restX ?? 0) + sway;
        bones.root.rotation.z = sway * -0.3;
      }
    },
  },
  speaking: {
    name: "speaking",
    duration: 1.0,
    apply(bones, t, now) {
      // Subtle emphasis gesture while speaking — hands slightly out.
      if (bones.shoulderL) bones.shoulderL.rotation.z = -0.4;
      if (bones.shoulderR) bones.shoulderR.rotation.z =  0.4;
    },
  },
};

/** Reset bones to their rest transform before applying a new clip, so
 *  switching clips doesn't accumulate drift. Captures rest at first call. */
export function resetBones(bones) {
  for (const name in bones) {
    const b = bones[name];
    if (!b || !b.position || !b.rotation) continue;
    if (!b.userData.rest) {
      b.userData.rest = {
        px: b.position.x, py: b.position.y, pz: b.position.z,
        rx: b.rotation.x, ry: b.rotation.y, rz: b.rotation.z,
        sx: b.scale.x, sy: b.scale.y, sz: b.scale.z,
      };
    }
    const r = b.userData.rest;
    b.position.set(r.px, r.py, r.pz);
    b.rotation.set(r.rx, r.ry, r.rz);
    b.scale.set(r.sx, r.sy, r.sz);
  }
}

/** Run the clip registered for `state.mode` or `state.action` on top of the
 *  base idle. The caller supplies `now` in ms (performance.now()). */
export function driveAnimation(bones, state, now) {
  resetBones(bones);
  // Baseline idle is always on so poses blend over it (not under it).
  CLIPS.idle.apply(bones, 0, now);
  const key = state.action || state.mode;
  const clip = CLIPS[key];
  if (clip && clip.name !== "idle") {
    const t = ((now - (state.actionStart || now)) / (clip.duration * 1000)) % 1;
    clip.apply(bones, t, now);
  }
}

// How each mood carries itself. Applied ON TOP of the idle pose (so it reads as body language, not a
// separate clip): a tired companion slumps and his arms hang; a proud one stands tall; a playful one is
// perky. `bob` (from moodExpression) scales a gentle liveliness bounce — faster + larger when upbeat,
// slow + almost still when tired. Values are radians/scale at full intensity.
const MOOD_POSTURE = {
  tired:   { headX: 0.15, lean: 0.06, shoulderDrop: 0.10 },
  happy:   { headX: -0.05 },
  playful: { headX: -0.04 },
  proud:   { headX: -0.07, chest: 0.025 },
  curious: { headX: -0.06 },
  annoyed: { headX: 0.03, shoulderTense: 0.07 },
  neutral: {},
};

/** Apply a mood's demeanor to the (proxy) bones, after the idle clip and before syncProxies.
 *  moodName/intensity from MoodModel.dominant(); bob from moodExpression().bob. Pure + testable. */
export function applyDemeanor(bones, moodName, intensity, bob = 1, now = 0) {
  const s = Math.max(0, Math.min(1, intensity || 0));
  if (s <= 0) return;
  const p = MOOD_POSTURE[moodName] || {};
  if (bones.head) {
    if (p.headX) bones.head.rotation.x += p.headX * s;
    const amp = (bob - 1) * 0.06 * s;                 // bob>1 perky bounce, bob<1 calmer-than-idle
    const period = 900 / Math.max(0.4, bob);          // livelier moods bounce faster
    bones.head.rotation.x += Math.sin(now / period) * amp;
  }
  if (p.lean && bones.torso) bones.torso.rotation.x += p.lean * s;             // slump forward when tired
  if (p.chest && bones.torso) bones.torso.scale.multiplyScalar(1 + p.chest * s);   // chest up when proud
  if (bones.shoulderL && bones.shoulderR) {
    if (p.shoulderDrop) { bones.shoulderL.rotation.z -= p.shoulderDrop * s; bones.shoulderR.rotation.z += p.shoulderDrop * s; }
    if (p.shoulderTense) { bones.shoulderL.rotation.z += p.shoulderTense * s; bones.shoulderR.rotation.z -= p.shoulderTense * s; }
  }
}

/** Capability report — which clips this adapter can play.  */
export function clipNames() { return Object.keys(CLIPS); }

/** Factory wrapper the companion.html uses: owns one per adapter and
 *  exposes a `.play(mode, now, currentIdle, idleStart)` method so the main
 *  render loop stays short. Reduced-motion is honored by refusing to apply
 *  the "big gesture" clips (stretch / wave / weight_shift). */
export function buildAnimator(bones, { reducedMotion = false } = {}) {
  const BIG = new Set(["stretch", "wave", "weight_shift", "yawn", "dance", "wander", "sit", "perch", "curious", "laugh", "fly", "parkour", "salute", "double_take", "shuffle", "secret_shuffle", "kick_trick"]);
  function pick(mode, idleKey) {
    // Mode wins over idle action; "speaking" / "listening" / "thinking" have
    // dedicated clips, idle actions only fire in the idle state.
    if (mode && CLIPS[mode] && mode !== "idle") return mode;
    if (idleKey && CLIPS[idleKey]) {
      if (reducedMotion && BIG.has(idleKey)) return null;
      return idleKey;
    }
    return null;
  }
  return {
    play(mode, now, currentIdle, idleStart) {
      const key = pick(mode, currentIdle);
      driveAnimation(bones, { mode, action: key, actionStart: idleStart || now }, now);
    },
    reducedMotion,
  };
}
