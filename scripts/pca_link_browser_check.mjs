// Browser check for Jarvis Link on the Captain America page (app/captain.html), driven over the DevTools protocol in
// headless Edge at phone size (390x844). Two throwaway dev servers (scripts/pca_dev_server.py, --seed --stub-music: no
// network): one where the Link was never connected (the Mission card and the audio sheet show the setup steps, the
// download button and a pairing code), and one with --stub-link (a fake phone inside the harness): NOW PLAYING shows the
// track the phone reports with a progress bar, PAUSE / RESUME / SKIP / LIKE go through the phone, the "Playing from
// Metrolist's queue" label appears when the fake phone moves on, the Auto switch persists over a reload. No console
// errors (the harness's service-worker error is ignored), no horizontal overflow. Screenshots: docs/pca/screens/link-*.png.
//
//   node scripts/pca_link_browser_check.mjs            (PYTHON=<python with the repo's packages>, EDGE=<msedge.exe>)
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
  const m = out.match(/data=(\S+)/); assert(m, 'dev server did not start: ' + out);
  return { p, box: m[1], url: `http://127.0.0.1:${port}` };
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
    const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (await ev("!!(" + x + ")")) return; } catch {} await sleep(100); }
    let state = ''; try { state = await ev('location.hash + " | sheet: " + document.getElementById("sheet-root").innerText.slice(0, 500) + " | toast: " + document.getElementById("toast").textContent'); } catch {}
    throw new Error('timed out waiting for ' + x + '\n  page: ' + state + '\n  errors: ' + errors.join('\n'));
  };
  await send('Runtime.enable'); await send('Page.enable'); await send('Log.enable');
  return { send, ev, wait, errors, close: () => ws.close() };
}
const realErrors = errs => errs.filter(e => !/sw\.js|serviceWorker|ServiceWorker|favicon|manifest/i.test(e));
async function shot(t, name) { await t.ev(`document.getElementById("toast").hidden = true`); const s = await t.send('Page.captureScreenshot', { format: 'png' }); await fs.writeFile(path.join(SHOTS, name), Buffer.from(s.data, 'base64')); }
async function size(t, w, h) { await t.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 }); await t.send('Emulation.setTouchEmulationEnabled', { enabled: w < 600 }); }
async function open(t, url) { await t.ev('window.__nav = 1'); await t.send('Page.navigate', { url }); await t.wait('!window.__nav && typeof D !== "undefined" && D !== null && document.querySelector("main#views section:not([hidden]) .hud")'); await sleep(300); }
const click = (t, sel) => t.ev(`(() => { const n = document.querySelector(${JSON.stringify(sel)}); if (!n) throw new Error("no " + ${JSON.stringify(sel)}); n.scrollIntoView({ block: "center" }); n.click(); return true; })()`);
const noOverflow = async t => assert(await t.ev('document.documentElement.scrollWidth <= innerWidth + 1'), 'horizontal overflow');
const results = [];
const ok = (what, extra = '') => { results.push(what); console.log('PASS', what, extra); };
const begin = async url => {
  const r = await (await fetch(url + '/api/pca/action', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'begin' }) })).json();
  assert(r.data && r.data.status === 'active', 'begin failed: ' + r.message);
};
const NP = 'document.querySelector("#np-sheet")';

try {
  await fs.mkdir(SHOTS, { recursive: true });
  await edge(9353);
  // ---- 1. never connected: the setup steps ----------------------------------------------------------------------------------
  const A = await server(8798, ['--date', '2026-11-23', '--seed', '--stub-music']);
  await begin(A.url);
  let t = await tab(9353);
  await size(t, 390, 844);
  await open(t, A.url + '/captain.html#mission');
  await t.wait('document.querySelector("#link-card")');
  const card = await t.ev('document.querySelector("#link-card").innerText');
  for (const w of ['Let Jarvis run the music hands-free', 'Download', 'notification access', 'it does not read your notifications', 'pairing code', 'Allow background running'])
    assert(card.toLowerCase().includes(w.toLowerCase()), "mission card lacks " + w);
  await click(t, '#link-card [data-act="link-code"]');
  await t.wait('document.querySelector("#link-card .linkcode") && /^\\d{6}$/.test(document.querySelector("#link-card .linkcode").textContent)');
  await t.ev('document.querySelector("#link-card").scrollIntoView({ block: "start" })'); await sleep(200);
  await shot(t, 'link-card-phone.png');
  ok('never connected: the Mission card shows the steps, the download button and a pairing code');
  const apk = await t.ev(`fetch("/api/link/apk", { headers: { Authorization: "Bearer x" } }).then(r => r.status + " " + r.headers.get("content-type"))`);
  assert.equal(apk, '200 application/vnd.android.package-archive', 'apk download ' + apk);
  await click(t, '#link-card [data-act="link-apk"]');
  await t.wait('document.getElementById("toast").textContent.includes("JarvisLink.apk")');
  ok('the download button fetches the APK with the page token (blob download)');
  await click(t, '#audio-btn');
  await t.wait('document.querySelector("#link-block")');
  const block = await t.ev('document.querySelector("#link-block").innerText');
  assert(block.includes('JARVIS LINK') && /not installed/i.test(block) && /download/i.test(block), 'link block: ' + block);
  assert(!(await t.ev(`${NP}.innerText.includes("controlling Metrolist")`)), 'controlling line without a phone');
  await t.ev('document.querySelector("#link-block").scrollIntoView({ block: "start" })'); await sleep(200);
  await shot(t, 'link-sheet-setup-phone.png');
  ok('never connected: the audio sheet has the JARVIS LINK block (Not installed, steps) and today\'s NOW PLAYING');
  await click(t, '.sheet [data-act="close-sheet"]');
  await click(t, '#link-card [data-act="link-card-off"]');
  await open(t, A.url + '/captain.html?again=1#mission');
  assert(!(await t.ev('!!document.querySelector("#link-card")')), 'the card came back after dismissing');
  ok('the Mission card is dismissible and stays dismissed');
  await noOverflow(t);
  let errs = realErrors(t.errors); assert.equal(errs.length, 0, errs.join('\n'));
  ok('setup pages: no console errors, no horizontal overflow');
  t.close(); A.p.kill();

  // ---- 2. a fake phone: Jarvis runs Metrolist ---------------------------------------------------------------------------------
  const B = await server(8799, ['--date', '2026-11-23', '--seed', '--stub-music', '--stub-link']);
  await begin(B.url);
  t = await tab(9353);
  await size(t, 390, 844);
  await open(t, B.url + '/captain.html#mission');
  await t.wait('D.audio.dj.link && D.audio.dj.link.connected');
  assert(!(await t.ev('!!document.querySelector("#link-card")')), 'the setup card shows although the phone is connected');
  await click(t, '#audio-btn');
  await t.wait(`${NP} && ${NP}.innerText.includes("Jarvis is controlling Metrolist")`);
  assert(/connected/i.test(await t.ev('document.querySelector("#link-block").innerText')), 'link block not Connected');
  await click(t, '#np-sheet [data-act="dj-start"]');
  await t.wait('D.audio.dj.now && D.audio.dj.link.playing && D.audio.dj.link.playing.mine', 15000);
  const title = await t.ev('D.audio.dj.now.title');
  await t.wait(`${NP}.innerText.includes(${JSON.stringify(title)}) && document.querySelector("#np-sheet .npbar i")`);
  await shot(t, 'link-nowplaying-phone.png');
  ok('NOW PLAYING shows the track the phone reports, with a progress bar', title);
  await click(t, '#np-sheet [data-act="dj-pause"]');
  await t.wait('document.querySelector("#np-sheet [data-act=dj-resume]")');
  await click(t, '#np-sheet [data-act="dj-resume"]');
  await t.wait('document.querySelector("#np-sheet [data-act=dj-pause]")');
  ok('PAUSE and RESUME go through the phone');
  await click(t, '#np-sheet [data-act="dj-like"]');
  await t.wait('document.querySelector("#np-sheet [data-act=dj-like][aria-pressed=true]")');
  ok('LIKE is recorded and shown');
  await t.wait(`${NP}.innerText.includes("Playing from Metrolist's queue")`, 20000);
  const q = await t.ev('D.audio.dj.link.playing.title');
  await shot(t, 'link-queue-phone.png');
  ok('the Metrolist-queue label appears when the phone moves on', q);
  await click(t, '#np-sheet [data-act="dj-skip"]');
  await t.wait(`D.audio.dj.link.playing && D.audio.dj.link.playing.mine && D.audio.dj.now.title !== ${JSON.stringify(title)} && ${NP}.innerText.includes(D.audio.dj.now.title)`, 15000);
  const skipped = await t.ev('fetch("/api/pca").then(r => r.json()).then(d => d.audio.dj.stats.skips)');
  assert(skipped >= 1, 'no skip recorded');
  ok('SKIP of the queue track records it and plays Jarvis\'s next pick', await t.ev('D.audio.dj.now.title'));
  assert.equal(await t.ev('document.querySelector("#np-sheet [data-act=dj-auto]").getAttribute("aria-checked")'), 'true', 'Auto is not on by default');
  await click(t, '#np-sheet [data-act="dj-auto"]');
  await t.wait('D.audio.dj.link.auto_dj === false');
  await open(t, B.url + '/captain.html?again=1#mission');
  await click(t, '#audio-btn');
  await t.wait('document.querySelector("#np-sheet [data-act=dj-auto]")');
  assert.equal(await t.ev('document.querySelector("#np-sheet [data-act=dj-auto]").getAttribute("aria-checked")'), 'false', 'Auto did not persist');
  ok('the Auto switch persists over a reload');
  await noOverflow(t);
  errs = realErrors(t.errors); assert.equal(errs.length, 0, errs.join('\n'));
  ok('Link pages: no console errors, no horizontal overflow');
  t.close();
  console.log(`\n${results.length} checks passed`);
} finally {
  for (const p of procs) { try { p.kill(); } catch {} }
}
