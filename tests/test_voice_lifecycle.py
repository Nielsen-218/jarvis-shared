import ast
import queue
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from audio.tts import Speaker
from main import Status


def test_status_set_emits_without_poll_delay(monkeypatch):
    from core import hud
    emit = Mock()
    monkeypatch.setattr(hud, 'state', emit)
    status = Status.__new__(Status)
    status.value = 'sleeping'
    status.set('listening')
    emit.assert_called_once_with('listening')


def nested(name):
    tree = ast.parse(Path('main.py').read_text(encoding='utf-8'))
    loop = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'voice_loop')
    return next(n for n in loop.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_reply_completion_resets_thinking():
    speaker = Mock(spoken=[], first_audio_at=None)
    listener = Mock()
    listener.watch_for_interruption.return_value = None
    status = Mock()
    class Thread:
        def __init__(self, target, **kw): self.target = target
        def start(self): self.target()
        def is_alive(self): return False
    scope = dict(speaker=speaker, listener=listener, status=status,
                 threading=types.SimpleNamespace(Thread=Thread), log_timing=Mock())
    exec(compile(ast.Module(body=[nested('respond')], type_ignores=[]), 'main.py', 'exec'), scope)
    scope['respond']('', task=lambda: None)
    assert status.set.call_args.args == ('listening',)


@pytest.mark.parametrize('fail', [False, True])
def test_playback_clears_active_audio(monkeypatch, fail):
    speaker = Speaker('unused', muted=True)
    speaker._audio_q.put((0, 'synthetic', np.full(2400, 1000, dtype=np.int16)))
    class Done(Exception): pass
    original_get = speaker._audio_q.get
    def get():
        if speaker._audio_q.empty(): raise Done()
        return original_get()
    monkeypatch.setattr(speaker._audio_q, 'get', get)
    stream = Mock()
    if fail: stream.write.side_effect = RuntimeError('synthetic device failure')
    monkeypatch.setitem(sys.modules, 'sounddevice', types.SimpleNamespace(OutputStream=lambda **kw: stream))
    with pytest.raises(Done): speaker._play_loop()
    assert speaker._playing is None
    assert speaker.output_level() == 0
    assert not speaker.busy()


def test_wake_cue_precedes_warmup():
    loop = next(n for n in ast.parse(Path('main.py').read_text(encoding='utf-8')).body
                if isinstance(n, ast.FunctionDef) and n.name == 'voice_loop')
    calls = [ast.unparse(n) for n in ast.walk(loop) if isinstance(n, ast.Try)]
    block = next(s for s in calls if 'listener.wait_for_wake_word()' in s)
    assert block.index('status.set(\'listening\')', block.index('listener.wait_for_wake_word()')) < block.index('brain.warm_up()')

def test_timestamp_telemetry_is_bounded_and_has_no_transcripts():
    from core import hud
    speaker = Speaker('unused', muted=True)
    for _ in range(150):
        speaker._trace('play_start')
        hud.state('listening')
    assert len(speaker.lifecycle) == len(hud.voice_trace()) == 128
    assert all(set(e) == {'event', 'at_ms', 'generation'} for e in speaker.lifecycle)
    assert all(set(e) == {'event', 'value', 'at_ms'} for e in hud.voice_trace())

# Execute actual HTML callbacks with deferred local API/media; no model or mic.
@pytest.mark.parametrize('scenario', [
    'main_pending', 'main_playing', 'replace_pause', 'talk_start', 'talk_late', 'skill_finish',
    'skill_start', 'skill_explain_late', 'skill_explain_after_interrupt',
    'talk_out_of_order', 'talk_failure_main', 'listen_start',
    'skill_life_success', 'skill_life_fail', 'skill_life_invalid',
    'skill_life_blocked', 'skill_life_transport', 'skill_life_thrown', 'skill_life_unknown',
])
def test_companion_html_speech_integration(scenario):
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    script = r'''
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { SpeechSession } from './app/companion/speech-session.js';
import { EventBuffer } from './app/companion/behavior.js';
import { OverlayModel } from './app/companion/overlay.js';
const html = fs.readFileSync('app/companion.html', 'utf8');
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; };
class Audio extends EventTarget {
  paused = true; ended = false; error = null; plays = [];
  pause() {
    const wasPlaying = !this.paused; this.paused = true;
    if (this.asyncPause && wasPlaying) setTimeout(() => this.dispatchEvent(new Event('pause')), 0);
    else if (!this.asyncPause) this.dispatchEvent(new Event('pause'));
  }
  async play() { this.paused = false; this.plays.push(this.src); }
}
const synth = deferred(), request = deferred(), audio = new Audio(), incoming = new EventBuffer();
const lifeEvents = [];
const ctx = vm.createContext({ SpeechSession, incoming, quipEl: audio, overlayModel: new OverlayModel(), noteRecent(){}, clickGate: {cancel(){}},
  lifeInfo: null, lifeEvent: async kind => lifeEvents.push(kind), startPerformance(){},
  API: () => ({say: () => synth.promise, listen: async () => true}), menuEl: {}, applyLayout(){},
  loadHome(){}, changeHome(){}, openHome(){}, closeScene(){},
  skillSpeechTokens: new WeakMap(),
  createMenu: options => ({actions: options.actions, isOpen: true}),
  ix: {set(){}, is:()=>false}, apiCall:()=>request.promise,
  mood: {nudge(){}}, textToVisemes:()=>[], performance,
  visemeTimeline: null, visemeStart: 0,
});
const constructorStart = html.indexOf('const localSpeech = new SpeechSession(');
vm.runInContext(html.slice(constructorStart, html.indexOf('\n});', constructorStart) + 4), ctx);
const sayStart = html.indexOf('async function sayAudio(');
vm.runInContext(html.slice(sayStart, html.indexOf('\nconst STATUS', sayStart)), ctx);
const menuStart = html.indexOf('const menu = createMenu({');
vm.runInContext('let examining = false, reactPose = null;\n' +
  html.slice(menuStart, html.indexOf('    play: async', menuStart)) + '  }\n});', ctx);
const handleStart = html.indexOf('  handle(msg) {');
const handleEnd = html.indexOf('\n};', handleStart);
vm.runInContext('const ingress = ({' + html.slice(handleStart, handleEnd) + '\n});', ctx);
const run = code => vm.runInContext(code, ctx);
const main = state => run(`ingress.handle({type:'state',value:'${state}'})`);
const flush = async () => { for (let i=0; i<10; i++) await Promise.resolve(); };
const scenario = process.argv[1];
try {
  if (scenario === 'main_pending') {
    const result = run("localSpeech.speak('synthetic')"); await flush();
    main('listening'); synth.resolve('stale');
    assert.equal(await result, false); assert.deepEqual(audio.plays, []);
    assert.equal(incoming.drain().filter(e=>e.type==='state').at(-1).value, 'listening');
  } else if (scenario === 'main_playing') {
    await run("localSpeech.play('playing')"); main('speaking');
    assert.equal(audio.paused, true);
    assert.equal(incoming.drain().filter(e=>e.type==='state').at(-1).value, 'speaking');
  } else if (scenario === 'replace_pause') {
    await run("localSpeech.play('old')"); audio.asyncPause = true;
    await run("localSpeech.play('new')");
    await new Promise(r => setTimeout(r, 5));
    assert.equal(run('localSpeech.active'), true, 'Queued old pause must not cancel replacement playback');
  } else if (scenario === 'talk_start') {
    await run("localSpeech.play('quip')"); run("menu.actions.ask('synthetic')");
    assert.equal(audio.paused, true, 'Talk thinking must silence the quip immediately');
  } else if (scenario === 'talk_late') {
    const result = run("menu.actions.ask('synthetic')"); await flush();
    main('listening'); main('idle');
    request.resolve({ok:true,data:{audio:'old-reply',reply:'synthetic'}});
    await result; await flush();
    assert.deepEqual(audio.plays, [], 'An interrupted Talk reply must remain stale after main returns idle');
  } else if (scenario === 'listen_start') {
    await run("localSpeech.play('quip')"); await run('menu.actions.listen()');
    assert.equal(audio.paused, true);
    assert.equal(incoming.drain().filter(e=>e.type==='state').at(-1).value, 'listening');
  } else if (scenario === 'skill_start') {
    await run("localSpeech.play('quip')"); run("menu.actions.runSkill({name:'synthetic'}, {})");
    assert.equal(audio.paused, true);
    assert.equal(incoming.drain().filter(e=>e.type==='state').at(-1).value, 'working');
  } else if (scenario === 'skill_explain_late') {
    const result = run("menu.actions.explain({name:'synthetic'}, 'synthetic')"); await flush();
    main('listening'); main('idle');
    request.resolve({ok:true,data:{audio:'stale-explanation',reply:'synthetic'}});
    await result; await flush(); assert.deepEqual(audio.plays, []);
  } else if (scenario === 'skill_explain_after_interrupt') {
    run("const testSkill = {name:'synthetic'}");
    const result = run("menu.actions.runSkill(testSkill, {})"); await flush();
    main('listening'); main('idle');
    request.resolve({ok:true,data:{ok:true,text:'synthetic'}}); await result;
    assert.equal(await run("menu.actions.explain(testSkill, 'synthetic')"), null);
    assert.deepEqual(audio.plays, []);
  } else if (scenario === 'talk_out_of_order') {
    const oldRequest = deferred(), newRequest = deferred(); let calls = 0;
    ctx.apiCall = () => (++calls === 1 ? oldRequest.promise : newRequest.promise);
    const old = run("menu.actions.ask('old')"); await flush();
    const latest = run("menu.actions.ask('latest')"); await flush();
    newRequest.resolve({ok:true,data:{audio:'latest',reply:'latest'}}); await latest;
    oldRequest.resolve({ok:true,data:{audio:'old',reply:'old'}}); await old;
    assert.deepEqual(audio.plays, ['latest']);
  } else if (scenario === 'talk_failure_main') {
    const result = run("menu.actions.ask('synthetic')"); await flush();
    main('speaking'); request.resolve({ok:false,data:{detail:'synthetic'}}); await result;
    assert.equal(incoming.drain().filter(e=>e.type==='state').at(-1).value, 'speaking');
  } else if (scenario.startsWith('skill_life_')) {
    if (scenario === 'skill_life_blocked') main('listening');
    if (scenario === 'skill_life_thrown') ctx.apiCall = async () => { throw new Error('synthetic'); };
    const result = run("menu.actions.runSkill({name:'synthetic'}, {})"); await flush();
    const success = scenario === 'skill_life_success';
    request.resolve({ok: scenario !== 'skill_life_transport', data:{
      ok:success, text:'synthetic', seconds:scenario === 'skill_life_unknown' ? 0 : 0.2,
      invalid:scenario === 'skill_life_invalid'
    }});
    await result; await flush();
    assert.deepEqual(lifeEvents, success ? ['skill_success'] : scenario === 'skill_life_fail' ? ['skill_fail'] : []);
  } else if (scenario === 'skill_finish') {
    const result = run("menu.actions.runSkill({name:'synthetic'}, {})"); await flush();
    main('listening'); request.resolve({ok:true,data:{ok:true,text:'synthetic'}});
    await result; await flush();
    assert.equal(incoming.drain().filter(e=>e.type==='state').at(-1).value, 'listening',
      'Skill completion must not overwrite newer main listening');
  }
} finally { run('localSpeech.dispose()'); }
'''
    result = subprocess.run([node, '--input-type=module', '-e', script, scenario],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
