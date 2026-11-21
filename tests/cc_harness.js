// Runs app/command-center.js against a tiny fake DOM (no browser, no network, no clock): node cc_harness.js <page.js> <scenario.json>
// Prints one JSON object with what the page did. Used by tests/test_command_center.py.
const fs = require("fs"), vm = require("vm");
const [, , jsPath, scenarioPath] = process.argv;
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));
const els = {}, fetched = [], timers = [], posted = [], winHandlers = {}; let clock = scenario.now ? scenario.now * 1000 : Date.now();
function el(sel) {
  if (els[sel]) return els[sel];
  const cls = new Set(), kids = [];
  const e = { sel, innerHTML: "", textContent: "", value: "", style: { setProperty() {} }, dataset: {}, children: kids, attrs: {}, src: "", href: "",
    classList: { add: c => cls.add(c), remove: c => cls.delete(c), contains: c => cls.has(c), toggle: (c, on) => { (on === undefined ? !cls.has(c) : on) ? cls.add(c) : cls.delete(c); } },
    has: c => cls.has(c), setAttribute(k, v) { e.attrs[k] = v; }, getAttribute: k => e.attrs[k] ?? null, removeAttribute(k) { delete e.attrs[k]; },
    toggleAttribute() {}, querySelector: s => el(sel + " " + s), querySelectorAll: () => [], closest: () => null, append() {}, remove() {}, focus() {},
    replaceChildren(...c) { e.replaced = c; }, handlers: {}, addEventListener(ev, fn) { (e.handlers[ev] = e.handlers[ev] || []).push(fn); },
    contentWindow: { postMessage: m => posted.push(m) }, lastElementChild: null, firstElementChild: { remove() {} }, scrollTop: 0 };
  return (els[sel] = e);
}
const doc = { querySelector: el, querySelectorAll: () => [], addEventListener() {}, createElement: () => el("new" + Math.random()), hidden: false, body: el("body") };
const win = { addEventListener(ev, fn) { (winHandlers[ev] = winHandlers[ev] || []).push(fn); }, jarvis: null, pywebview: scenario.desk ? { api: {} } : null, open() {}, location: { hash: "#t=tok", pathname: "/command-center.html", origin: "http://127.0.0.1:1" } };
const ctx = { document: doc, window: win, location: win.location, history: { replaceState() {} }, localStorage: { getItem: () => "tok", setItem() {} },
  fetch: async (url) => { fetched.push(url); const body = (scenario.api || {})[url];
    if (body === undefined) return { ok: false, status: 404, json: async () => ({}) }; return { ok: true, status: 200, json: async () => body }; },
  setInterval: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, setTimeout: () => 0, clearTimeout() {}, requestAnimationFrame() {},
  Date: class extends Date { constructor(...a) { super(...(a.length ? a : [clock])); } static now() { return clock; } },
  console, encodeURIComponent, decodeURIComponent, Math, Number, String, Array, JSON, Promise, Set };
ctx.window.window = ctx.window; ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(jsPath, "utf8").replace(/^"use strict";/m, ""), ctx);
(async () => {
  await new Promise(r => setImmediate(r));
  for (const step of scenario.steps || []) {
    if (step.advance) clock += step.advance * 1000;
    if (step.event) win.jarvis.handle(step.event);
    if (step.tick) for (const t of timers) if (t.ms === step.tick) await t.fn();
    if (step.fetchedReset) fetched.length = 0;
    if (step.fire) for (const fn of el(step.fire[0]).handlers[step.fire[1]] || []) fn({});
    if (step.message) { const m = { ...step.message }; if (m.source === "frame") m.source = el("#frame").contentWindow; for (const fn of winHandlers.message || []) fn(m); }
    await new Promise(r => setImmediate(r));
  }
  const out = { fetched, posted, intervals: timers.map(t => t.ms) };
  for (const sel of scenario.read || []) { const e = el(sel); out[sel] = e ? { data: e.dataset, html: e.innerHTML, text: e.textContent, on: e.has("on"), src: e.src || e.attrs.src || "", href: e.href, replaced: !!e.replaced } : null; }
  console.log(JSON.stringify(out));
})();
