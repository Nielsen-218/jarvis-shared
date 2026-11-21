// His own reactions are never silently lost and never talk over the main voice (speech-session.js say()).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SpeechSession } from '../../app/companion/speech-session.js';

class Audio extends EventTarget {
  plays = []; paused = true; ended = false; error = null;
  pause() { this.paused = true; this.dispatchEvent(new Event('pause')); }
  play() { this.paused = false; this.ended = false; this.plays.push(this.src); return Promise.resolve(); }
  end() { this.ended = true; this.paused = true; this.dispatchEvent(new Event('ended')); }
}
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function rig(opts = {}) {
  const clock = { t: 10000 }, timers = [], audio = new Audio(), lines = [], drops = [];
  const s = new SpeechSession({ audio, generate: async (text) => 'url:' + text, now: () => clock.t,
    onLine: (text, o) => lines.push([text, o.silent]), onDrop: (text, why) => drops.push([text, why]),
    setTimer: (fn, ms) => { const id = { fn, at: clock.t + ms }; timers.push(id); return id; },
    clearTimer: (id) => { const i = timers.indexOf(id); if (i >= 0) timers.splice(i, 1); }, ...opts });
  const advance = async (ms) => {
    const end = clock.t + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      if (!timers.length || timers[0].at > end) break;
      const next = timers.shift(); clock.t = next.at; next.fn(); await flush();
    }
    clock.t = end; await flush();
  };
  return { s, clock, audio, lines, drops, advance };
}

test('free: said at once, and the caption is told when the voice starts', async () => {
  const r = rig();
  assert.equal(await r.s.say('Yes, sir?'), true);
  assert.deepEqual(r.audio.plays, ['url:Yes, sir?']);
  assert.deepEqual(r.lines, [['Yes, sir?', false]]);
});

test('the main voice is busy: the line waits, then is said once he has been free a moment', async () => {
  const r = rig();
  r.s.setMainState('speaking');
  assert.equal(await r.s.say('Still here, sir.', { key: 'poke' }), 'queued');
  await r.advance(3000);
  assert.deepEqual(r.audio.plays, [], 'never over the main voice');
  r.s.setMainState('idle');
  await r.advance(200); assert.deepEqual(r.audio.plays, [], 'not the instant he stops');
  await r.advance(400);
  assert.deepEqual(r.audio.plays, ['url:Still here, sir.']);
  assert.deepEqual(r.drops, []);
});

test('a line that waited too long is dropped, and says so', async () => {
  const r = rig();
  r.s.setMainState('thinking');
  await r.s.say('Too late to matter.');
  await r.advance(9000);
  r.s.setMainState('idle'); await r.advance(2000);
  assert.deepEqual(r.audio.plays, []);
  assert.deepEqual(r.drops, [['Too late to matter.', 'expired']]);
  assert.ok(r.s.trace.some(e => e.event === 'queue_expired'));
});

test('a burst of pokes leaves one pending line; the queue holds at most two', async () => {
  const r = rig();
  r.s.setMainState('speaking');
  await r.s.say('one', { key: 'poke' }); await r.s.say('two', { key: 'poke' }); await r.s.say('three', { key: 'poke' });
  assert.deepEqual(r.s.queue.map(q => q.text), ['three']);
  await r.s.say('landing'); await r.s.say('catch');
  assert.deepEqual(r.s.queue.map(q => q.text), ['landing', 'catch']);
  assert.deepEqual(r.drops, [['three', 'overflow']]);
});

test('he is mid-line: the next reaction follows it instead of cutting it off', async () => {
  const r = rig();
  await r.s.say('first');
  assert.equal(await r.s.say('second'), 'queued');
  await r.advance(1000); assert.deepEqual(r.audio.plays, ['url:first']);
  r.audio.end(); await r.advance(400);
  assert.deepEqual(r.audio.plays, ['url:first', 'url:second']);
});

test('the main voice interrupts a queued line again: it keeps waiting, nothing overlaps', async () => {
  const r = rig();
  r.s.setMainState('listening'); await r.s.say('patience');
  r.s.setMainState('idle'); await r.advance(100);
  r.s.setMainState('thinking'); await r.advance(1000);
  assert.deepEqual(r.audio.plays, []);
  r.s.setMainState('idle'); await r.advance(600);
  assert.deepEqual(r.audio.plays, ['url:patience']);
});

test('no voice came back: the words are shown instead of lost', async () => {
  const r = rig({ generate: async () => '' });
  assert.equal(await r.s.say('Silent but visible.'), false);
  assert.deepEqual(r.lines, [['Silent but visible.', true]]);
  assert.ok(r.s.trace.some(e => e.event === 'no_audio'));
});

test('the owner starts talking, or he is hidden: waiting lines are forgotten, not said late', async () => {
  const r = rig();
  r.s.setMainState('speaking'); await r.s.say('a poke', { key: 'poke' });
  r.s.setMainState('listening'); assert.equal(r.s.queue.length, 0);
  r.s.setMainState('idle'); await r.advance(3000);
  assert.deepEqual(r.audio.plays, []);
  r.s.setMainState('thinking'); await r.s.say('one', { key: 'poke' }); await r.s.say('two');
  assert.equal(r.s.clearQueue('poke'), 1); assert.deepEqual(r.s.queue.map(q => q.text), ['two']);
  assert.equal(r.s.clearQueue(), 1);                    // what "hide" does
  r.s.setMainState('idle'); await r.advance(3000);
  assert.deepEqual(r.audio.plays, []);
  assert.ok(r.s.trace.some(e => e.event === 'queue_cleared'));
});
