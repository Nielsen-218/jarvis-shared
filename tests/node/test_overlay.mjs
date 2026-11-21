import assert from 'node:assert/strict';
import { test } from 'node:test';
import { OverlayModel, OVERLAY, chipText, words, overlayBox } from '../../app/companion/overlay.js';

test('a whole turn: listening with level, heard words, thinking with a chip, the reply, then gone', () => {
  const m = new OverlayModel();
  assert.equal(m.tick(0).visible, false);
  m.state('listening', 100); m.level(0.8);
  let v = m.tick(400);
  assert.equal(v.phase, 'listening'); assert.equal(v.label, 'Listening'); assert.ok(v.level > 0.5); assert.equal(v.text, 'none');
  m.line('you', 'open my music please', 1000);
  v = m.tick(1000 + OVERLAY.HEARD_WORD_MS * 2);
  assert.equal(v.text, 'heard'); assert.equal(v.words.length, 4); assert.equal(v.shown, 3, 'words appear one by one');
  m.state('thinking', 1500); m.doing('opening Spotify.', 1600);
  v = m.tick(1700);
  assert.equal(v.phase, 'thinking'); assert.equal(v.busy, true); assert.equal(v.chip, 'Opening Spotify');
  assert.equal(v.text, 'heard', 'what he heard stays as confirmation'); assert.equal(v.level, 0);
  m.doing('', 2500);
  m.line('jarvis', 'Your music is playing, sir.', 2600); m.state('speaking', 2700);
  v = m.tick(2750);
  assert.equal(v.phase, 'speaking'); assert.equal(v.text, 'reply'); assert.ok(v.shown >= 1 && v.shown < v.words.length, 'the caption follows the voice');
  v = m.tick(2700 + 5000); assert.equal(v.shown, v.words.length);
  m.state('idle', 8000);
  assert.equal(m.tick(8100).phase, 'speaking', 'a short pause is still him speaking');
  v = m.tick(8000 + OVERLAY.SPEECH_GAP_MS + 10); assert.equal(v.phase, 'result');
  assert.equal(m.tick(8000 + OVERLAY.HOLD_MS - 50).visible, true);
  assert.equal(m.tick(8000 + OVERLAY.HOLD_MS + 4100).visible, false);
});

test('nothing leaves it stuck on screen', () => {
  const a = new OverlayModel(); a.state('listening', 0); a.state('idle', 500);
  assert.equal(a.tick(500 + OVERLAY.LISTEN_LINGER_MS + 1).visible, false, 'listened, heard nothing');
  const b = new OverlayModel(); b.state('thinking', 0); b.state('idle', 500);
  assert.equal(b.tick(500 + OVERLAY.THINK_LINGER_MS + 1).visible, false, 'thought, said nothing');
  const c = new OverlayModel(); c.state('thinking', 0); c.doing('searching the web', 10); c.state('idle', 500);
  assert.equal(c.tick(5000).visible, true, 'a tool is still busy');
  assert.equal(c.tick(500 + OVERLAY.STUCK_MS + 1).visible, false, 'a chip that never cleared');
  const d = new OverlayModel(); d.state('error', 0); assert.equal(d.tick(10).phase, 'error');
  assert.equal(d.tick(OVERLAY.ERROR_MS + 1).visible, false);
  const e = new OverlayModel(); e.state('thinking', 0); e.line('jarvis', 'A reply that never got a voice.', 100); e.state('idle', 200);
  const v = e.tick(100 + OVERLAY.PENDING_REPLY_MS + 1);
  assert.equal(v.phase, 'result'); assert.equal(v.shown, v.words.length, 'shown anyway');
  assert.equal(e.tick(100 + OVERLAY.PENDING_REPLY_MS + OVERLAY.HOLD_MS + OVERLAY.READ_MAX_MS + 2).visible, false);
  const f = new OverlayModel(); f.state('listening', 0); f.state('muted', 10); assert.equal(f.tick(20).visible, false);
});

test('two sentences of one reply stay one caption; a new turn starts clean', () => {
  const m = new OverlayModel();
  m.state('thinking', 0); m.line('jarvis', 'First sentence.', 100); m.state('speaking', 100);
  m.state('thinking', 1500); m.line('jarvis', 'Second one.', 1600); m.state('speaking', 1700);
  let v = m.tick(9000);
  assert.deepEqual(v.words, ['First', 'sentence.', 'Second', 'one.']);
  m.state('idle', 9000); m.tick(13000);
  m.state('listening', 20000);
  v = m.tick(20010); assert.equal(v.words.length, 0); assert.equal(v.phase, 'listening');
});

test('his own reactions: captioned while free, never over the main conversation', () => {
  const m = new OverlayModel();
  assert.equal(m.quip('Yes, sir?', 0), true);
  let v = m.tick(2000); assert.equal(v.phase, 'speaking'); assert.equal(v.local, true); assert.equal(v.shown, 2);
  m.quipEnd(2100); assert.equal(m.tick(2200).phase, 'result');
  assert.equal(m.tick(2100 + OVERLAY.QUIP_HOLD_MS + 1).visible, false);
  assert.equal(m.quip('Shown, not spoken.', 9000, { silent: true }), true);
  v = m.tick(9001); assert.equal(v.phase, 'result'); assert.equal(v.shown, 3);
  m.state('listening', 9500); assert.equal(m.quip('Not now.', 9600), false);
  v = m.tick(9700); assert.equal(v.phase, 'listening'); assert.equal(v.words.length, 0, 'the quip left when the turn began');
});

test('text hygiene and placement', () => {
  assert.deepEqual(words('[ignore]  hello   there'), ['hello', 'there']);
  assert.equal(chipText('opening Spotify...'), 'Opening Spotify');
  const long = chipText('searching the entire internet for a very long thing indeed and more');
  assert.ok(long.length <= OVERLAY.CHIP_MAX && long.endsWith('…'));
  const m = new OverlayModel(); m.line('you', '\u{1F4F1} from the phone', 0); assert.equal(m.tick(1).visible, false);
  const small = overlayBox(50), big = overlayBox(300);
  assert.equal(small.boxH, small.cardH); assert.equal(small.cardTop, 0);
  assert.ok(big.cardTop >= 0 && big.cardTop + big.cardH <= big.boxH);
  const lvl = new OverlayModel(); lvl.state('listening', 0); lvl.level('junk'); assert.equal(lvl.tick(50).level, 0);
});

test('watchdog: a lost idle event cannot leave the card up for ever', () => {
  const a = new OverlayModel(); a.state('listening', 0); a.level(0.5, 5000);
  assert.equal(a.tick(5000 + OVERLAY.LISTEN_MAX_QUIET_MS - 10).visible, true, 'events keep it alive');
  assert.equal(a.tick(5000 + OVERLAY.LISTEN_MAX_QUIET_MS + 10).visible, false);
  assert.equal(a.main, 'idle');
  a.level(0.4, 60000); assert.equal(a.tick(60010).visible, false, 'a stray level does not bring it back');
  const b = new OverlayModel(); b.line('jarvis', 'Words that never end.', 0); b.state('speaking', 0);
  for (let t = 1000; t <= 40000; t += 1000) { b.level(0.3 + (t % 2000) / 4000, t); assert.equal(b.tick(t).visible, true, 'a long answer is fine'); }
  assert.equal(b.tick(40000 + OVERLAY.SPEAK_MAX_QUIET_MS + 10).visible, false);
});
