// Browser check for the GARMIN card (Health Connect through Jarvis Link) on the Captain America page (app/captain.html),
// driven over the DevTools protocol in headless Edge at phone size (390x844). Three throwaway dev servers
// (scripts/pca_dev_server.py, made-up data, no network, no phone):
//   1. never connected: onboarding and Recovery show the GARMIN steps (install Jarvis Link, Health Connect in Garmin
//      Connect, Allow in Jarvis Link) and what Garmin does not share;
//   2. --stub-health on a Tuesday: Recovery shows the card connected with the seeded values (checked against
//      /api/health/status), the Run view offers the watch run, one tap + an effort logs it, Progress shows the
//      watch series;
//   3. --stub-health --short-night 5.1 --rhr-today 60 on a Monday (a hard day): today's plan changes with the reason
//      "from your Garmin".
// No console errors (the harness's service-worker error is ignored), no horizontal overflow.
// Screenshots: docs/pca/screens/garmin-*.png.
//
//   node scripts/pca_health_browser_check.mjs          (PYTHON=<python with the repo's packages>, EDGE=<msedge.exe>)
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.env.PYTHON || path.resolve(ROOT, '..', '..', '.venv', 'Scripts', 'python.exe');
const EDGE = process.env.EDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const SHOTS = path.join(ROOT, 'docs', 'pca', 'screens');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const procs = [];

async function server(port, args) {
  const p = spawn(PY, ['scripts/pca_dev_server.py', String(port), ...args], { cwd: ROOT, env: { ...process.env, PYTHONPATH: '.' } });
  procs.push(p);
  let out = '';
  p.stdout.on('data', d => out += d); p.stderr.on('data', () => {});
  for (let i = 0; i < 100 && !/data=(\S+)/.test(out); i++) await sleep(150);
  assert(/data=(\S+)/.test(out), 'dev server did not start: ' + out);
  return { p, url: `http://127.0.0.1:${port}` };
}
async function edge(port) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'pca-edge-'));
  procs.push(spawn(EDGE, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${dir}`, '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' }));
  for (let i = 0; i < 100; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/json/version`)).ok) break; } catch {} await sleep(150); }
}
async function tab(port) {
  const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let seq = 0; const pending = new Map(), errors = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id) { const p = pending.get(m.id); if (p) { pending.delete(m.id); m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result); } }
    else if (m.method === 'Runtime.exceptionThrown') errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 400));
    else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map(a => a.value ?? a.description).join(' '));
    else if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errors.push('[log] ' + m.params.entry.text + ' ' + (m.params.entry.url || ''));
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++seq; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  const ev = async x => { const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error(x.slice(0, 120) + ' -> ' + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
  const wait = async (x, ms = 10000) => {
    const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (await ev('!!(' + x + ')')) return; } catch {} await sleep(100); }
    throw new Error('timed out waiting for ' + x + '\n  errors: ' + errors.join('\n'));
  };
  await send('Runtime.enable'); await send('Page.enable'); await send('Log.enable');
  return { send, ev, wait, errors, close: () => ws.close() };
}
const realErrors = errs => errs.filter(e => !/sw\.js|serviceWorker|ServiceWorker|favicon|manifest/i.test(e));
async function shot(t, name) { await t.ev('document.getElementById("toast").hidden = true'); const s = await t.send('Page.captureScreenshot', { format: 'png' }); await fs.writeFile(path.join(SHOTS, name), Buffer.from(s.data, 'base64')); }
async function phone(t) { await t.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }); await t.send('Emulation.setTouchEmulationEnabled', { enabled: true }); }
async function open(t, url) { await t.ev('window.__nav = 1'); await t.send('Page.navigate', { url }); await t.wait('!window.__nav && typeof D !== "undefined" && D !== null && document.querySelector("main#views section:not([hidden]) .hud")'); await sleep(300); }
const nav = async (t, v) => { await t.ev(`document.querySelector('[data-nav="${v}"]').click()`); await sleep(400); };
const click = (t, sel) => t.ev(`(() => { const n = document.querySelector(${JSON.stringify(sel)}); if (!n) throw new Error("no " + ${JSON.stringify(sel)}); n.scrollIntoView({ block: "center" }); n.click(); return true; })()`);
const into = (t, sel) => t.ev(`(() => { const n = document.querySelector(${JSON.stringify(sel)}); n.scrollIntoView({ block: "start" }); scrollBy(0, -64); return true; })()`);
const text = (t, sel) => t.ev(`(document.querySelector(${JSON.stringify(sel)}) || {}).innerText || ""`);
const noOverflow = async t => assert(await t.ev('document.documentElement.scrollWidth <= innerWidth + 1'), 'horizontal overflow');
const ok = (what, extra = '') => console.log('PASS', what, extra);
const begin = async url => {
  const r = await (await fetch(url + '/api/pca/action', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'begin' }) })).json();
  assert(r.data && r.data.status === 'active', 'begin failed: ' + r.message);
};

try {
  await fs.mkdir(SHOTS, { recursive: true });
  await edge(9363);

  // ---- 1. never connected -----------------------------------------------------------------------------------------
  const A = await server(8821, ['--date', '2026-11-23', '--seed']);
  let t = await tab(9363); await phone(t);
  await open(t, A.url + '/captain.html');
  const steps = await text(t, '#garmin-card');
  assert(/Not connected/i.test(steps) && /Install Jarvis Link/.test(steps) && /Garmin Connect app, turn on Health Connect/.test(steps)
    && /Allow Jarvis to read health data/.test(steps) && /Body Battery, Training Readiness, HRV status or stress/.test(steps), steps);
  assert(!/garmin-login/i.test(await t.ev('document.body.innerText')), 'page still mentions garmin-login');
  await into(t, '#garmin-card'); await noOverflow(t); await shot(t, 'garmin-steps-phone.png');
  ok('not connected: the onboarding shows the three steps and what is not shared');
  await begin(A.url); await open(t, A.url + '/captain.html'); await nav(t, 'recovery');
  assert(/Not connected/i.test(await text(t, '#garmin-card')), 'Recovery has no steps card');
  ok('not connected: Recovery shows the steps');
  assert.deepEqual(realErrors(t.errors), []); t.close();

  // ---- 2. connected, a run day (Tuesday, week 1) --------------------------------------------------------------------
  const B = await server(8822, ['--date', '2026-11-24', '--seed', '--stub-health']);
  await begin(B.url);
  const api = await (await fetch(B.url + '/api/health/status')).json();
  t = await tab(9363); await phone(t);
  await open(t, B.url + '/captain.html'); await nav(t, 'recovery');
  const card = await text(t, '#garmin-card');
  assert(/Connected/i.test(card) && card.includes('7 h 24 min') && card.includes(String(api.resting_hr.bpm)) && card.includes(api.last_run.distance_mi + ' mi')
    && card.includes(api.last_run.pace), card);
  assert(!/score \d|readiness \d|body battery \d/i.test(card), 'a Garmin score is shown');
  await into(t, '#garmin-card'); await noOverflow(t); await shot(t, 'garmin-recovery-phone.png');
  ok('connected: Recovery shows the seeded values the API returned', `(sleep 7.4 h, RHR ${api.resting_hr.bpm}, run ${api.last_run.distance_mi} mi)`);

  await nav(t, 'run');
  const offer = (await text(t, '#garmin-run')).toLowerCase();                       // headings are uppercased by CSS
  assert(offer.includes('28:10') && offer.includes('2.6 mi') && offer.includes('10:50 /mi'), offer);
  await into(t, '#garmin-run'); await shot(t, 'garmin-run-offer-phone.png');
  await click(t, '[data-act="garmin-use"]'); await t.wait('document.querySelector("#garmin-rpe")');
  await click(t, '[data-act="garmin-rpe"][data-v="4"]');
  await t.wait('!document.querySelector("#garmin-run") && D.today.items.find(i => i.run).done.length === 1');
  const logged = await t.ev('JSON.stringify(D.today.items.find(i => i.run).done[0])');
  assert(/"minutes":28.2/.test(logged) && /"rpe":4/.test(logged), logged);
  await t.ev('scrollTo(0, 0)'); await shot(t, 'garmin-run-logged-phone.png');
  ok('the Run view offered the watch run; one tap and an effort logged it', logged);

  await nav(t, 'progress');
  const prog = await t.ev('document.querySelector("section[data-view=progress]").innerText');
  assert(/from your Garmin/i.test(prog) && /Resting heart rate/i.test(prog) && /Run heart rate/i.test(prog), 'no watch series on Progress: ' + prog.slice(0, 400));
  assert(await t.ev('document.querySelectorAll("section[data-view=progress] .pt.garmin").length') > 0, 'no Garmin points');
  await into(t, 'section[data-view=progress] .pt.garmin'); await noOverflow(t); await shot(t, 'garmin-progress-phone.png');
  ok('Progress shows the watch series, every point labelled from your Garmin');
  assert.deepEqual(realErrors(t.errors), []); t.close();

  // ---- 3. a short night before a hard day ---------------------------------------------------------------------------
  const C = await server(8823, ['--date', '2026-11-23', '--seed', '--stub-health', '--short-night', '5.1', '--rhr-today', '60']);
  await begin(C.url);
  t = await tab(9363); await phone(t);
  await open(t, C.url + '/captain.html');
  const mission = await t.ev('document.querySelector("section[data-view=mission]").innerText');
  assert(/from your Garmin/.test(mission) && /5\.1 hours/.test(mission), 'the hard day did not change from the watch: ' + mission.slice(0, 600));
  await into(t, '.adj'); await noOverflow(t); await shot(t, 'garmin-shortnight-phone.png');
  ok('a 5.1 h night (and resting HR 9 above usual) changed the hard day "from your Garmin"');
  assert.deepEqual(realErrors(t.errors), []); t.close();
  console.log('ALL PASS');
} finally {
  for (const p of procs) { try { p.kill(); } catch {} }
}
