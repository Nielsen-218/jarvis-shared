"""Run actual HTML scene functions with deferred, in-memory host calls only.

No browser, native window, user store, network, or extra JS fixture is used.
The hide tests include the real IPC handler rather than calling close directly.
"""
import ast
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[1], 'utf8').replace(/\r\n/g, '\n');
const scenario = process.argv[2];
function section(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert(a >= 0 && b > a, `Missing actual HTML section: ${start}`);
  return html.slice(a, b);
}
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const tick = () => new Promise(resolve => setImmediate(resolve));
const classList = () => ({add(){}, remove(){}});
const calls = [];
let load = async () => ({}), board = async () => ({ok:true,state:{points:[]}});
let native = async () => true;
const c = vm.createContext({
  console, performance:{now:()=>100}, window:{devicePixelRatio:1},
  roomOpen:false, directOpen:false, performanceOpen:false,
  directPointer:null, drawPointer:false, dozing:false,
  menu:{isOpen:false,close:async()=>{}},
  homeScene:{group:{visible:false}},
  directPlay:{start(){},stop(){}},
  performances:{name:null,start(name){this.name=name;},stop(){this.name=null;},
    snapshot(){return {name:this.name};},setDrawing(){},getDrawing(){return []; }},
  robotMount:null, activeAdapter:null,
  document:{documentElement:{classList:classList()},body:{style:{}},
    getElementById:()=>({textContent:'',style:{}})}, stageEl:{classList:classList()},
  ix:{state:'ambient',set(s){this.state=s;},rest(){this.state='ambient';},is(s){return this.state===s;}},
  API:()=>({companion_scene:async mode=>{calls.push(mode);return native(mode);},
    companion_board:()=>board()}),
  loadHome:()=>load(), changeHome:async()=>{},leaveSurface(){},
  resize(){},fitCameraToRig(){},setRobotOpacity(){},saveDrawing:async()=>{},
  localSpeech:{cancel(){},clearQueue(){}}, clickGate:{cancel(){}}, examining:false,
  incoming:{put:()=>true}, gesture:{active:false},
  scheduler:{suspend(){},resume(){}},maskSpec:null,maskPrev:null,panelHit:false,
  sendHitMask(){},
});
vm.runInContext(section('let sceneQueue=', '\n') + '\n' +
  section('function scheduleScene(', 'function setRobotOpacity(') +
  section('async function _startPerformance(', 'async function saveDrawing(') +
  'function ' + section('handle(msg) {', '\n  },\n};') + '\n}', c);
const run = code => vm.runInContext(code, c);
const modes = () => [c.roomOpen,c.directOpen,c.performanceOpen];
function check(expected) {assert.deepEqual(modes(),expected);assert(modes().filter(Boolean).length<=1);}
async function main() {
  if (scenario === 'home_direct' || scenario === 'home_close' || scenario === 'queued_hide') {
    const gate=deferred();load=()=>gate.promise;
    const first=run('openHome()');await tick();
    const second=run(scenario==='home_close'?'closeScene()':'openDirect("catch")');
    await tick();check([false,false,false]);assert.deepEqual(calls,[]);
    if(scenario==='queued_hide') run('handle({type:"hide"})');
    gate.resolve({});await Promise.all([first,second]);await run('sceneQueue');
    check(scenario==='home_direct'?[false,true,false]:[false,false,false]);
    assert.deepEqual(calls,scenario==='home_close'?['home','']:scenario==='home_direct'?['home','','play']:['home','','play','']);
    assert.equal(c.homeScene.group.visible,false);
  } else if(scenario==='direct_home') {
    const gate=deferred();native=mode=>mode==='play'?gate.promise:true;
    const first=run('openDirect("catch")');await tick();
    const second=run('openHome()');await tick();check([false,true,false]);
    assert.deepEqual(calls,['play']);gate.resolve(true);
    await Promise.all([first,second]);check([true,false,false]);
    assert.deepEqual(calls,['play','','home']);
  } else if(scenario==='draw_close' || scenario==='draw_hide' || scenario==='draw_direct') {
    const gate=deferred();board=()=>gate.promise;
    const first=run('startPerformance("draw")');await tick();
    check([false,false,false]);
    const second=scenario==='draw_close'?run('closeScene()'):
      scenario==='draw_direct'?run('openDirect("catch")'):run('handle({type:"hide"})');
    gate.resolve({ok:true,state:{points:[]}});
    await Promise.all([first,second]);await run('sceneQueue');
    check(scenario==='draw_direct'?[false,true,false]:[false,false,false]);
    assert.deepEqual(calls,scenario==='draw_direct'?['performance','','play']:['performance','']);
    assert.equal(c.performances.name,null);
  } else if(scenario==='active_hide') {
    await run('openDirect("catch")');run('handle({type:"hide"})');
    await run('sceneQueue');check([false,false,false]);assert.deepEqual(calls,['play','']);
  } else if(scenario==='rejected_entry') {
    load=async()=>{throw new Error('synthetic load failure');};
    await assert.rejects(run('openHome()'),/synthetic load failure/);
    await run('openDirect("catch")');check([false,true,false]);
    assert.deepEqual(calls,['play']);
  } else throw new Error('Unknown scenario');
  console.log(scenario+' passed');
}
// A promise stuck in the queue must fail, even with no other Node handles.
const watchdog=setTimeout(()=>{console.error('Scene queue did not settle');process.exit(1);},2000);
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>clearTimeout(watchdog));
"""


@pytest.mark.parametrize("scenario", [
    "home_direct", "direct_home", "home_close", "draw_close", "draw_direct",
    "active_hide", "queued_hide", "draw_hide", "rejected_entry",
])
def test_actual_scene_lifecycle(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for actual HTML lifecycle functions")
    result = subprocess.run(
        [node, "-e", HARNESS, str(ROOT / "app/companion.html"), scenario],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_protection_order_preserves_controls_but_not_taskbar():
    """Source-order contract only; does not claim native hit-test acceptance."""
    tree = ast.parse((ROOT / "hud/app.py").read_text(encoding="utf-8"))
    painter = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "_paint_hitmask")
    paint = next(n for n in ast.walk(painter) if isinstance(n, ast.FunctionDef)
                 and n.name == "paint")
    source = ast.unparse(paint)
    body = source.index("protected_local(api._protected, wr)")
    controls = source.index("grid['rects']")
    menu = source.index("g.FillPath(brush, path)")
    strict = source.index("getattr(api, '_strict_protected', [])")
    assert body < controls < menu < strict

    tree = ast.parse((ROOT / "core/desktop_habitat.py").read_text(encoding="utf-8"))
    snapshot = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                    and n.name == "snapshot")
    result = next(n.value for n in snapshot.body if isinstance(n, ast.Return))
    values = {k.value: v for k, v in zip(result.keys, result.values)}
    assert ast.unparse(values["strict_protected"]) == "[b.rect for b in bars]"
