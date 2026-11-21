import assert from 'node:assert/strict';
import { SpeechSession } from '../../app/companion/speech-session.js';
import { AmplitudeMeter } from '../../app/companion/voice-bridge.js';
class Audio extends EventTarget {
  plays = []; paused = true; ended = false; error = null;
  pause() { this.paused = true; this.dispatchEvent(new Event('pause')); }
  play() { this.paused = false; this.plays.push(this.src); return Promise.resolve(); }
}
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; };
const a = new Audio(), one = deferred(), two = deferred(), states = [];
let requests = 0;
const s = new SpeechSession({ audio: a, generate: () => (++requests === 1 ? one.promise : two.promise), onState: v => states.push(v) });
const first = s.speak('one'); await Promise.resolve();
const second = s.speak('two'); await Promise.resolve();
two.resolve('two'); assert.equal(await second, true);
one.resolve('one'); assert.equal(await first, false);
assert.deepEqual(a.plays, ['two']);
s.setMainState('listening'); assert.equal(a.paused, true);
assert.equal(states.at(-1), 'speaking'); // cancellation must not overwrite main's listening state
assert.equal(await s.speak('blocked'), false);
s.setMainState('idle');
const pending = deferred(); s.generate = () => pending.promise;
const late = s.speak('late'); await Promise.resolve();
s.setMainState('thinking'); pending.resolve('stale');
assert.equal(await late, false); assert.deepEqual(a.plays, ['two']);
s.setMainState('idle');
assert.equal(await s.play('three'), true);
a.ended = true; a.dispatchEvent(new Event('ended')); assert.equal(s.active, false); assert.equal(states.at(-1), 'idle');
a.play = () => Promise.reject(new Error('blocked'));
assert.equal(await s.play('reject'), false); assert.equal(s.active, false);
const t = new SpeechSession({ audio: new Audio(), generate: () => new Promise(() => {}), timeoutMs: 5 });
assert.equal(await t.speak('timeout'), false); assert.equal(t.active, false);
for (let i=0; i<150; i++) s.cancel(); assert.equal(s.trace.length, 128);
assert.ok(s.trace.every(e => !('text' in e)));
let zeros = 0;
const meter = new AmplitudeMeter(new Audio(), {onLevel: v => { if(v === 0) zeros++; }});
globalThis.cancelAnimationFrame = () => {};
meter.attach(); meter.source.dispatchEvent(new Event('error'));
assert.equal(zeros, 1, 'media error clears amplitude immediately');
meter.detach();
console.log('speech lifecycle assertions passed');
// Cancellation must settle a caller even when synthesis never resolves.
const hung = new SpeechSession({ audio: new Audio(), generate: () => new Promise(() => {}) });
const waiting = hung.speak('hung'); await Promise.resolve(); hung.cancel();
assert.equal(await waiting, false);
// Timeout also stops already-started playback with no terminal media event.
const stalledAudio = new Audio();
const stalled = new SpeechSession({audio: stalledAudio, timeoutMs: 5});
assert.equal(await stalled.play('stall'), true);
await new Promise(r => setTimeout(r, 15));
assert.equal(stalled.active, false); assert.equal(stalledAudio.paused, true);
let errors = 0;
const attached = new AmplitudeMeter(new Audio(), {onEnd: () => errors++});
attached.attach(); attached.attach(); attached.source.dispatchEvent(new Event('error'));
assert.equal(errors, 1); attached.detach(); attached.source.dispatchEvent(new Event('error'));
assert.equal(errors, 1);
console.log('cancellation, stalled playback and detach assertions passed');
// Request reservation is the playback generation; it cannot be revived.
const ownedAudio = new Audio(), ownedStates = [];
const owned = new SpeechSession({audio: ownedAudio, onState: state => ownedStates.push(state)});
const reservation = owned.begin('thinking');
assert.equal(await owned.play('reserved', reservation), true);
assert.equal(owned.generation, reservation);
owned.setMainState('listening'); owned.setMainState('idle');
assert.equal(await owned.play('late', reservation), false);
assert.deepEqual(ownedAudio.plays, ['reserved']);
owned.finish(reservation); assert.equal(ownedStates.at(-1), 'speaking');
// Old queued events cannot end the replacement, but actual current terminals do.
const fresh = owned.begin('thinking');
ownedAudio.ended = false; ownedAudio.error = null;
assert.equal(await owned.play('fresh', fresh), true);
ownedAudio.dispatchEvent(new Event('pause'));
ownedAudio.dispatchEvent(new Event('ended'));
ownedAudio.dispatchEvent(new Event('error'));
assert.equal(owned.active, true);
ownedAudio.error = new Error('synthetic'); ownedAudio.dispatchEvent(new Event('error'));
assert.equal(owned.active, false); assert.equal(ownedAudio.paused, true);
owned.dispose();
console.log('request generation and queued media event assertions passed');
