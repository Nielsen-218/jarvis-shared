// Browser check for the Captain America exercise demonstrations (app/captain.html), driven over the DevTools protocol in
// headless Edge. Starts scripts/pca_dev_server.py on throwaway data (--seed --stub-demo --stub-music: no network), begins
// the program through the API on a week-1 Monday, and checks at 390x844 and 1440x900: the figure of the first three
// exercises moves and its phase labels change, LOG SET (or the set button) sits within one screen of the card top, a
// warm-up drill opens its demonstration, Mission / Learn / plan-browser names open the demonstration sheet, Friday of
// week 3 opens a day sheet with every item, "Watch video demo" creates the youtube-nocookie iframe only after the tap
// and "Different video" changes it, the Run view shows "How to run it" (and the session shape on an interval day), no
// console errors, no horizontal overflow. Sheets: Tab and Shift+Tab stay inside, Escape / the close button / the phone's Back
// (history.back()) close one level and give focus back to the opener, a day sheet -> demonstration sheet unwinds one level
// per Back, and a second Back leaves the page. The pose validator (scripts/pca_pose_check.mjs) runs first.
// Screenshots go to docs/pca/screens/demo-*.png.
//
//   node scripts/pca_demo_browser_check.mjs            (PYTHON=<python with the repo's packages>, EDGE=<msedge.exe>)
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { validate } from './pca_pose_check.mjs';

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
  const wait = async (x, ms = 8000) => {
    const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (await ev("!!(" + x + ")")) return; } catch {} await sleep(100); }
    let state = ''; try { state = await ev('location.hash + " | sheet: " + document.getElementById("sheet-root").innerHTML.slice(0, 300) + " | toast: " + document.getElementById("toast").textContent'); } catch {}
    throw new Error('timed out waiting for ' + x + '\n  page: ' + state + '\n  errors: ' + errors.join('\n'));
  };
  await send('Runtime.enable'); await send('Page.enable'); await send('Log.enable');
  return { send, ev, wait, errors, close: () => ws.close() };
}
const realErrors = errs => errs.filter(e => !/sw\.js|serviceWorker|ServiceWorker|favicon|manifest/i.test(e));

async function shot(t, name) { await t.ev(`document.getElementById("toast").hidden = true`); const s = await t.send('Page.captureScreenshot', { format: 'png' }); await fs.writeFile(path.join(SHOTS, name), Buffer.from(s.data, 'base64')); }
async function size(t, w, h) { await t.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 }); await t.send('Emulation.setTouchEmulationEnabled', { enabled: w < 600 }); }
async function open(t, url) { await t.ev('window.__nav = 1'); await t.send('Page.navigate', { url }); await t.wait('!window.__nav && typeof D !== "undefined" && D !== null && document.querySelector("main#views section:not([hidden]) .hud")'); await sleep(300); }
const click = (t, sel) => t.ev(`(() => { const n = document.querySelector(${JSON.stringify(sel)}); if (!n) throw new Error("no " + ${JSON.stringify(sel)}); n.scrollIntoView({ block: "center" }); n.focus(); n.click(); return true; })()`);
const closed = t => t.wait('!document.querySelector("#sheet-root .sheet")');
const closeTop = async t => { await click(t, '.sheet [data-act="close-sheet"]'); await closed(t); };
async function key(t, k, shift) {
  const code = { Tab: 9, Escape: 27 }[k];
  for (const type of ['keyDown', 'keyUp']) await t.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: code, modifiers: shift ? 8 : 0 });
}
async function mark(t, sel) {           // tag the element that will open a sheet, to check focus comes back to it
  await t.ev(`(() => { document.querySelectorAll("[data-check-opener]").forEach(n => n.removeAttribute("data-check-opener")); const n = document.querySelector(${JSON.stringify(sel)}); n.setAttribute("data-check-opener", "1"); return true; })()`);
}
const focusBack = t => t.ev('document.activeElement && document.activeElement.hasAttribute("data-check-opener")');
async function trapped(t) {             // Tab and Shift+Tab never leave the open sheet
  for (let i = 0; i < 40; i++) { await key(t, 'Tab'); assert(await t.ev('!!document.activeElement.closest("#sheet-root .sheet")'), 'Tab left the sheet after ' + (i + 1)); }
  for (let i = 0; i < 12; i++) { await key(t, 'Tab', true); assert(await t.ev('!!document.activeElement.closest("#sheet-root .sheet")'), 'Shift+Tab left the sheet'); }
}
const frame = async (t, sel) => {    // the element at the top of the screen, below the sticky header, with no toast over it
  await t.ev(`(() => { document.getElementById("toast").hidden = true; const el = document.querySelector(${JSON.stringify(sel)}); el.scrollIntoView({ block: "start" });
    const sc = document.querySelector("main#views"), hh = document.querySelector("header.top").offsetHeight; if (sc.scrollHeight > sc.clientHeight + 2) sc.scrollBy(0, -8); else window.scrollBy(0, -hh - 8); return true; })()`);
  await sleep(250);
};
const noOverflow = async t => assert(await t.ev('document.documentElement.scrollWidth <= innerWidth + 1'), 'horizontal overflow');
const results = [];
const ok = (what, extra = '') => { results.push(what); console.log('PASS', what, extra); };

async function moving(t, root) {        // a limb coordinate changes over 400 ms, and the phase label changes within ~4 s
  const coord = `(() => { const ls = document.querySelectorAll(${JSON.stringify(root + ' .figbox[data-fig] svg line')}); return [...ls].slice(1).map(l => l.getAttribute('x2') + ',' + l.getAttribute('y2')).join(' '); })()`;
  await t.wait(`document.querySelectorAll(${JSON.stringify(root + " .figbox[data-fig] svg line")}).length > 3`);
  const a = await t.ev(coord); await sleep(400); const b = await t.ev(coord);
  assert(a && b && a !== b, 'figure did not move under ' + root);
  const seen = new Set();
  for (let i = 0; i < 45 && seen.size < 2; i++) { seen.add(await t.ev(`document.querySelector(${JSON.stringify(root + ' .figbox[data-fig]')}).dataset.phase || ""`)); await sleep(100); }
  assert(seen.size >= 2, 'phase label did not change under ' + root + ': ' + [...seen]);
  return [...seen];
}

try {
  await fs.mkdir(SHOTS, { recursive: true });
  const poses = validate(await fs.readFile(path.join(ROOT, 'app', 'captain.html'), 'utf8'));
  assert.equal(poses.failures.length, 0, poses.failures.join('\n'));
  ok(`pose validator: ${poses.checked} figures, ${poses.samples} sampled poses, no stretched limb, nothing under the floor or off the picture`);
  const A = await server(8796, ['--date', '2026-11-23', '--seed', '--stub-demo', '--stub-music', '--stub-link']);
  const began = await (await fetch(A.url + '/api/pca/action', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'begin' }) })).json();
  assert(began.data && began.data.status === 'active', 'begin failed: ' + began.message);
  await edge(9351);
  for (const [w, h, tag] of [[390, 844, 'phone'], [1440, 900, 'desktop']]) {
    const t = await tab(9351);
    await size(t, w, h);
    await open(t, A.url + '/captain.html#mission');
    assert.equal(await t.ev('document.querySelectorAll("iframe").length'), 0, 'an iframe before any tap');
    // Mission: today's list -> demonstration sheet
    await click(t, 'section[data-view="mission"] .items .exlink');
    await t.wait('document.querySelector(".sheet .figbox[data-fig]")');
    await moving(t, '.sheet');
    if (tag === 'phone') await shot(t, 'demo-sheet-phone.png'); else await shot(t, 'demo-sheet-desktop.png');
    await closeTop(t);
    ok(tag + ': mission name opens the demonstration sheet');
    // Sheets: focus trap, Escape, the close button and Back all close one level and give focus back to the opener
    const opener = 'section[data-view="mission"] .items .exlink';
    await mark(t, opener); await click(t, opener); await t.wait('document.querySelector(".sheet .figbox")');
    await trapped(t);
    await key(t, 'Escape'); await closed(t);
    assert(await focusBack(t), 'Escape: focus did not come back to the opener');
    await mark(t, opener); await click(t, opener); await t.wait('document.querySelector(".sheet .figbox")');
    await t.ev('history.back(), true'); await closed(t);
    assert(await t.ev('location.pathname.endsWith("/captain.html")'), 'Back left the page instead of closing the sheet');
    assert(await focusBack(t), 'Back: focus did not come back to the opener');
    await mark(t, opener); await click(t, opener); await t.wait('document.querySelector(".sheet .figbox")');
    await closeTop(t);
    assert(await focusBack(t), 'close button: focus did not come back to the opener');
    await click(t, '[data-act="audio-sheet"]'); await t.wait('document.querySelector(".sheet[aria-label=\'Performance audio\']")');
    await trapped(t); await key(t, 'Escape'); await closed(t);
    ok(tag + ': sheets trap Tab, and Escape, the close button and Back each close one level with focus back on the opener');
    // Train: begin the mission, warm-up drill sheet, then the first three exercises
    await click(t, 'section[data-view="mission"] [data-act="begin-mission"]');
    await t.wait(`location.hash === "#train" && document.querySelector("section[data-view=train] .hud")`);
    if (!(await t.ev("!!document.querySelector(\".ckrow .thumbbtn\")"))) await click(t, `[data-act="phase"][data-to="warmup"]`);   // the session is already open (the phone pass began it)
    await t.wait(`document.querySelector(".ckrow .thumbbtn")`);
    await click(t, '.ckrow .thumbbtn');
    await t.wait('document.querySelector(".sheet .figbox[data-fig]")');
    await moving(t, '.sheet');
    await closeTop(t);
    ok(tag + ': a warm-up drill opens its demonstration');
    await click(t, '[data-act="phase"][data-to="main"]');
    for (let n = 0; n < 3; n++) {
      await t.wait('document.querySelector("#excard .figbox[data-fig]")');
      const name = await t.ev('document.querySelector("#excard .exname").textContent');
      const ph = await moving(t, '#excard');
      const gap = await t.ev(`(() => { const c = document.querySelector("#excard").getBoundingClientRect(), b = document.querySelector('#excard [data-act="log-set"], #excard [data-act="tap-done"], #excard [data-act="hold-toggle"], #excard form [type="submit"]').getBoundingClientRect(); return Math.round(b.bottom - c.top); })()`);
      assert(gap <= h, `${name}: the set button ends ${gap}px below the card top (screen ${h})`);
      if (n === 1) {
        await frame(t, '#excard');
        await shot(t, `demo-train-${tag}.png`);
        assert.equal(await t.ev('document.querySelectorAll("iframe").length'), 0, 'iframe before the tap');
        await click(t, '#excard [data-act="vid-watch"]');
        await t.wait('document.querySelector("#excard .vid iframe")');
        const src1 = await t.ev('document.querySelector("#excard .vid iframe").src');
        assert(/^https:\/\/www\.youtube-nocookie\.com\/embed\/[A-Za-z0-9_-]{11}\?rel=0&playsinline=1$/.test(src1), 'iframe src ' + src1);
        await click(t, '#excard [data-act="vid-next"]');
        await t.wait(`document.querySelector("#excard .vid iframe") && document.querySelector("#excard .vid iframe").src !== ${JSON.stringify(src1)}`);
        const src2 = await t.ev('document.querySelector("#excard .vid iframe").src');
        ok(tag + ': video only after the tap; Different video changes it', src1.split('/').pop() + ' -> ' + src2.split('/').pop());
        if (tag === 'desktop') { await t.ev('document.querySelector("#excard .vid").scrollIntoView({block:"center"})'); await sleep(300); await shot(t, 'demo-video-desktop.png'); }
      }
      ok(`${tag}: exercise ${n + 1} (${name}) moves, phases ${ph.join(' / ')}; set button ${gap}px from the card top`);
      await click(t, '#excard [data-act="skip"]');
      await sleep(600);
    }
    // Learn: a row opens the sheet
    await click(t, '[data-nav="learn"]');
    await t.wait('document.querySelector(".exrow")');
    await click(t, '.exrow');
    await t.wait('document.querySelector(".sheet .figbox[data-fig]")');
    await closeTop(t);
    ok(tag + ': Learn opens the demonstration sheet');
    // Progress: week 3 Friday day sheet with every item, and a plan-browser exercise
    await click(t, '[data-nav="progress"]');
    await t.wait('document.querySelector("details.wk")');
    await t.ev('document.querySelectorAll("details.wk")[2].open = true');
    const want = await t.ev('D.program.weeks[2].days.find(d => d.day === "Fri").items.length');
    await mark(t, 'details.wk:nth-of-type(3) [data-act="day-sheet"][data-day="Fri"]');
    await click(t, 'details.wk:nth-of-type(3) [data-act="day-sheet"][data-day="Fri"]');
    await t.wait('document.querySelector(".sheet[aria-label^=\'Day:\']")');
    const rows = await t.ev('[...document.querySelectorAll(".sheet .check")].find(c => c.querySelector(".kick").textContent === "Main work").querySelectorAll(".exrow").length');
    assert.equal(rows, want, 'day sheet items');
    await shot(t, `demo-day-${tag}.png`);
    await click(t, '.sheet .exrow');
    await t.wait('document.querySelector(".sheet [data-act=\'back-day\']") && document.querySelector(".sheet .note.gold .kick")');
    await click(t, '.sheet [data-act="back-day"]');
    await t.wait('document.querySelector(".sheet[aria-label^=\'Day:\']")');
    // nested: day sheet -> demonstration sheet; Back unwinds one level at a time, focus follows
    await t.ev('(() => { const n = [...document.querySelectorAll(".sheet .check")].find(c => c.querySelector(".kick").textContent === "Main work").querySelector(".exrow"); n.focus(); n.click(); return true; })()');
    await t.wait('document.querySelector(".sheet[aria-label^=\'Demonstration\']")');
    await t.ev('history.back(), true');
    await t.wait('document.querySelector(".sheet[aria-label^=\'Day:\']")');
    assert(await t.ev('!!(document.activeElement.closest(".sheet") && document.activeElement.matches(".exrow"))'), 'Back to the day: focus is not on the exercise that opened the demonstration');
    await t.ev('history.back(), true'); await closed(t);
    assert(await focusBack(t), 'Back from the day sheet: focus did not come back to the day button');
    assert(await t.ev('location.pathname.endsWith("/captain.html")'), 'two Backs left the page');
    ok(`${tag}: week 3 Friday day sheet lists all ${want} items; an item opens its demonstration; Back unwinds demonstration -> day -> page`);
    await click(t, 'details.wk:nth-of-type(3) [data-act="plan-ex"]');
    await t.wait('document.querySelector(".sheet .figbox")');
    await closeTop(t);
    ok(tag + ': a plan-browser exercise opens the demonstration sheet');
    await noOverflow(t);
    const errs = realErrors(t.errors); assert.equal(errs.length, 0, errs.join('\n'));
    ok(tag + ': no console errors, no horizontal overflow');
    if (tag === 'phone') {               // Back closes the sheet; the Back after that leaves the page
      await click(t, '[data-nav="learn"]'); await t.wait('document.querySelector(".exrow")');
      await click(t, '.exrow'); await t.wait('document.querySelector(".sheet .figbox")');
      await t.ev('history.back(), true'); await closed(t);
      assert(await t.ev('location.pathname.endsWith("/captain.html")'), 'the first Back left the page');
      await t.ev('setTimeout(() => history.back(), 50), true');   // returns before the page goes
      await t.wait('!location.pathname.endsWith("/captain.html")');
      ok(tag + ': a second Back leaves the page');
    }
    t.close();
  }
  // Runs: the same data on a week-3 Wednesday (tempo: the session shape) and on the week-1 Wednesday (easy run)
  A.p.kill();
  for (const [date, label] of [['2026-12-09', 'tempo'], ['2026-11-25', 'easy']]) {
    const S = await server(8797, ['--keep', A.box, '--date', date, '--stub-demo', '--stub-music', '--stub-link']);
    for (const [w, h, tag] of [[390, 844, 'phone'], [1440, 900, 'desktop']]) {
      const t = await tab(9351);
      await size(t, w, h);
      await open(t, S.url + '/captain.html#run');
      await t.wait('document.querySelector("#run-howto .figbox[data-fig]")');
      await moving(t, '#run-howto');
      if (label === 'tempo') { assert(await t.ev('!!document.querySelector("#run-howto .paceshape")'), 'no session shape'); await frame(t, '#run-howto'); await shot(t, `demo-run-${tag}.png`); }
      await noOverflow(t);
      const errs = realErrors(t.errors); assert.equal(errs.length, 0, errs.join('\n'));
      ok(`${tag}: ${label} run day shows How to run it${label === 'tempo' ? ' with the session shape' : ''}`);
      t.close();
    }
    S.p.kill(); await sleep(500);
  }
  console.log(`\n${results.length} checks passed`);
} finally {
  for (const p of procs) { try { p.kill(); } catch {} }
}
