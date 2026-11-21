/* J.A.R.V.I.S. Command Center: the main desktop window. Live data from the local app server; live state from the HUD
   process (window.jarvis.handle, the same messages the old panel received) or, in a plain browser, from /api/dashboard. */
"use strict";
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
if (location.hash.startsWith("#t=")) { try { localStorage.setItem("jarvis_token", decodeURIComponent(location.hash.slice(3))); } catch (e) {} history.replaceState(null, "", location.pathname); }
let token = null; try { token = localStorage.getItem("jarvis_token"); } catch (e) {}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? { headers: { Authorization: `Bearer ${token}` } }
    : { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}
const desk = () => (window.pywebview && window.pywebview.api) || null;   // present only inside the desktop window

/* ---------- icons (Lucide, ISC licence) ---------- */
const IC = {
  home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h5v-6h4v6h5V10"/>', chat: '<path d="M21 12a8 8 0 0 1-8 8H5l-2 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z"/><path d="M8 11h.01M12 11h.01M16 11h.01"/>',
  code: '<path d="M8 6l-6 6 6 6M16 6l6 6-6 6M14 4l-4 16"/>', box: '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  gamepad: '<path d="M6 8h12a4 4 0 0 1 4 4v2a3 3 0 0 1-5.5 1.7L15 14H9l-1.5 1.7A3 3 0 0 1 2 14v-2a4 4 0 0 1 4-4z"/><path d="M7 11v2M6 12h2M16 11.5h.01M18 12.5h.01"/>',
  dumbbell: '<path d="M6.5 6.5v11M17.5 6.5v11M3.5 9v6M20.5 9v6M6.5 12h11"/>', book: '<path d="M2 5h7a3 3 0 0 1 3 3v12a2.5 2.5 0 0 0-2.5-2.5H2z"/><path d="M22 5h-7a3 3 0 0 0-3 3v12a2.5 2.5 0 0 1 2.5-2.5H22z"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 12l9 5 9-5M3 16l9 5 9-5"/>', cog: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.8 1.2V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 2.9-1.2V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.8 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.2a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.6 1z"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M12 1v4M12 19v4M1 12h4M19 12h4"/>', grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>', cube: '<path d="M12 2l8 4.5v9L12 20l-8-4.5v-9z"/><path d="M12 11l8-4.5M12 11v9M12 11L4 6.5"/>',
  monitor: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>', folder: '<path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>', bot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9 13v2M15 13v2M2 14h2M20 14h2"/><circle cx="12" cy="3" r="1"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>', speaker: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>', mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v4M8 22h8"/>',
  micoff: '<path d="M3 3l18 18M9 9v2a3 3 0 0 0 5 2.2M15 9.3V5a3 3 0 0 0-5.7-1.3M5 11a7 7 0 0 0 11 5.7M19 11a7 7 0 0 1-.6 2.8M12 18v4M8 22h8"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M8 14h.01M12 14h.01M16 14h.01"/>', rocket: '<path d="M5 15c-1.5 1-2 5-2 5s4-.5 5-2M9 13a22 22 0 0 1 2-4c2.500-4 6-6 10-6 0 4-2 7.500-6 10a22 22 0 0 1-4 2z"/><path d="M9 13l2 2"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  pulse: '<path d="M2 12h4l3-8 4 16 3-8h6"/>', plus: '<path d="M12 5v14M5 12h14"/>', check: '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
};
const icon = n => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${IC[n] || ""}</svg>`;
$$("i[data-ic]").forEach(i => { i.outerHTML = icon(i.dataset.ic); });

/* ---------- waveforms ---------- */
let level = 0, state = "starting";
function buildWave(el, n) { el.innerHTML = "<i></i>".repeat(n); }
buildWave($("#waveS"), 9); buildWave($("#waveL"), 34); buildWave($("#waveR"), 34);
const bars = $$(".wave i");
function paintWave(now) {
  const active = state === "listening" || state === "speaking", t = now / 160;
  bars.forEach((b, i) => {
    const shape = 0.5 + 0.5 * Math.sin(i * 0.9 + t) * Math.sin(i * 0.37 - t * 0.6);
    b.style.setProperty("--h", (active ? Math.min(1, 0.12 + level * 1.6) * shape : 0.06).toFixed(2));
  });
  requestAnimationFrame(paintWave);
}
requestAnimationFrame(paintWave);

/* ---------- state ---------- */
const LABELS = { starting: "STARTING", sleeping: "READY", idle: "READY", ready: "READY", listening: "LISTENING", thinking: "THINKING", speaking: "SPEAKING", offline: "OFFLINE" };
function setState(s) {
  state = s || "sleeping"; document.body.dataset.state = state;
  $("#state").dataset.s = state; $("#stateText").textContent = LABELS[state] || String(state).toUpperCase();
  $("#dot").classList.toggle("off", state === "offline" || state === "starting");
  $("#statusLine").textContent = state === "offline" ? "Offline" : state === "starting" ? "Starting" : "Online \u2022 " + (LABELS[state] || state).toLowerCase().replace(/^./, c => c.toUpperCase());
}

/* ---------- conversation ---------- */
const hm = d => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
let ownerShort = "You";
function addLine(who, text, when) {
  text = String(text || "").trim(); if (!text) return;
  const log = $("#log"), last = log.lastElementChild; $("#logEmpty") && $("#logEmpty").remove();
  if (who === "jarvis" && last && last.classList.contains("jarvis") && last.dataset.open === "1") { last.querySelector("p").append(" " + text); }
  else {
    if (last) last.dataset.open = "0";
    const d = document.createElement("div"); d.className = "msg " + (who === "jarvis" ? "jarvis" : who === "you" ? "you" : "sys");
    if (who === "sys") d.textContent = text;
    else { d.innerHTML = `<span class="av">${who === "jarvis" ? "N" : icon("user")}</span><span class="who">${who === "jarvis" ? "J.A.R.V.I.S." : "You"}</span><time>${esc(hm(when || new Date()))}</time><p></p>`;
      d.querySelector("p").textContent = text; if (who === "jarvis") d.dataset.open = "1"; }
    log.append(d);
  }
  while (log.children.length > 60) log.firstElementChild.remove();
  log.scrollTop = 1e9;
  if (inWorld && who !== "sys") worldPost({ jarvisWorldSay: { who, text } });
}
let taskTimer = 0;
function setDoing(text) {
  const box = $("#task"); text = String(text || "").trim(); clearTimeout(taskTimer);
  if (!text) {                                   // finished: show the tick briefly, then clear (no invented percent)
    if (box.classList.contains("on")) { $("#taskBar").classList.remove("busy"); $("#taskBar").style.setProperty("--p", "100%"); $("#taskOk").innerHTML = icon("check"); $("#taskPct").textContent = "";
      taskTimer = setTimeout(() => box.classList.remove("on"), 2500); }
    return;
  }
  const pct = (text.match(/(\d{1,3})\s?%/) || [])[1];
  box.classList.add("on"); $("#taskName").textContent = "Working"; $("#taskStep").textContent = text; $("#taskOk").innerHTML = "";
  $("#taskBar").classList.toggle("busy", !pct); $("#taskBar").style.setProperty("--p", pct ? Math.min(100, +pct) + "%" : "0%"); $("#taskPct").textContent = pct ? Math.min(100, +pct) + "%" : "";
  $("#log").scrollTop = 1e9;
}
async function send(text) {
  text = String(text || "").trim(); if (!text) return;
  if (!inWorld) showHome(); addLine("you", text); const before = state; setState("thinking");
  try { const r = await api("/api/chat", { text, speak: false }); addLine("jarvis", r.reply || "(no reply)"); }
  catch (e) { addLine("sys", "Jarvis's server isn't answering right now."); }
  if (state === "thinking") setState(before === "thinking" ? "sleeping" : before);
  const l = $("#log").lastElementChild; if (l) l.dataset.open = "0";
}
$("#ask").onsubmit = e => { e.preventDefault(); const v = $("#askText").value; $("#askText").value = ""; send(v); };
function typing(on, prefill) { $("#micbar").classList.toggle("typing", on); $("#kbBtn").setAttribute("aria-pressed", on ? "true" : "false");
  if (on) { if (prefill !== undefined) $("#askText").value = prefill; $("#askText").focus(); } }
$("#kbBtn").onclick = () => typing(!$("#micbar").classList.contains("typing"));
function toast(text) { const t = $("#toast"); t.textContent = text; t.classList.add("on"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("on"), 4200); }
async function act(path, body, okText) {
  try { const r = await api(path, body || {}); toast(r.ok === false ? (r.detail || "That isn't available right now.") : okText); return r; }
  catch (e) { toast("Jarvis's server isn't answering right now."); return null; }
}
$("#micBtn").onclick = () => act("/api/companion/listen", {}, "Listening.");
$("#stopBtn").onclick = () => act("/api/companion/stop", {}, "Stopped.");
function paintMute(on) { const b = $("#muteBtn"); b.setAttribute("aria-pressed", on ? "true" : "false"); b.title = on ? "Microphone is muted: click to unmute" : "Mute the microphone"; b.innerHTML = icon(on ? "micoff" : "sliders"); }
$("#muteBtn").onclick = async () => { const r = await act("/api/companion/mute", {}, "Microphone toggled."); if (r && "muted" in r) { paintMute(r.muted); toast(r.muted ? "Microphone muted." : "Microphone on."); } };

/* ---------- companion: called and dismissed ---------- */
function paintCompanion(on) { const b = $("#companionBtn"); if (!b) return; b.setAttribute("aria-pressed", on ? "true" : "false"); b.querySelector("span").textContent = on ? "Dismiss companion" : "Call companion"; }
if ($("#companionBtn")) $("#companionBtn").onclick = async () => {
  const on = $("#companionBtn").getAttribute("aria-pressed") !== "true";
  const r = await act("/api/companion/visible", { on }, on ? "The companion is on his way." : "The companion has left the screen.");
  if (r && "visible" in r) paintCompanion(r.visible);
};

/* ---------- navigation ---------- */
const PAGES = {                      // existing pages shown inside the Command Center
  fitness: ["Fitness", "fitness.html"], learn: ["Learn", "study.html"], dashboard: ["Dashboard", "dashboard.html"], pc: ["PC Control", "pc.html"],
  devices: ["Devices", "pc.html"], files: ["Files and memory", "memory.html"], projects: ["Projects and memory", "memory.html"], myworld: ["My World", "wardrobe.html"],
  build: ["Build: Character Studio", "studio.html"], settings: ["Settings: companion and voice", "studio.html"], automation: ["Automation: what Jarvis learned", "learning.html"],
  partner: ["Partner: goals and what I owe you", "partner.html"],
  workout: ["Workout", "fitness.html?tab=today"], play: ["Play", "play.html"], game: ["Play", "play.html"],
  world: ["World Intelligence", "world.html"], house: ["House", "house.html"], teach: ["Teach Jarvis", "teach.html"], languages: ["Languages", "languages.html"], record: ["Record my voice", "voice-record.html"],
};
function mark(key) { $$("[data-go]").forEach(b => { if (b.closest("#tabs, #sidebar")) b.toggleAttribute("aria-current", false); });
  $$(`#tabs [data-go="${key}"], #sidebar [data-go="${key}"]`).forEach(b => b.setAttribute("aria-current", "page")); }
/* ---------- Focus: the timer lives in this window (the Focus events and /api/focus the old panel used) ---------- */
const FOCUS_PRESETS = [[25, 5, 4, "Classic", "Four 25-minute blocks, 5-minute breaks"], [50, 10, 3, "Deep work", "Three 50-minute blocks, 10-minute breaks"],
  [15, 3, 4, "Quick sprint", "Four 15-minute bursts, 3-minute breaks"], [90, 15, 2, "Marathon", "Two 90-minute blocks for big jobs"]];
/* the 2-minute stretch break: six moves, twenty seconds each, as the old panel ran it */
const STRETCHES = [["Neck rolls", "Slow circles, five each way. Shoulders down."], ["Shoulder rolls", "Big circles backwards. Squeeze the shoulder blades."],
  ["Chest opener", "Hands clasped behind you, lift and open the chest."], ["Wrists and forearms", "Arm straight, gently pull the fingers back. Swap halfway."],
  ["Stand and reach", "Up on your toes, reach for the ceiling, breathe out slowly."], ["Eyes: 20-20-20", "Look at something far away. Blink. Let your eyes relax."]];
const STRETCH_SECS = 20;
let STRETCH = { on: false, i: 0, end: 0 };
async function sayLine(text) {
  if (!token) return;
  try { const r = await api("/api/say", { text }); if (r && r.audio) await new Audio(r.audio).play(); } catch (e) {}
}
function stretchStep() {
  if (STRETCH.i >= STRETCHES.length) { STRETCH.on = false; sayLine("Lovely. Back to it, sir, refreshed and slightly more flexible."); toast("Stretch done."); return; }
  STRETCH.end = Date.now() / 1000 + STRETCH_SECS; sayLine(STRETCHES[STRETCH.i].join(". "));
}
function startStretch() { STRETCH = { on: true, i: 0, end: 0 }; stretchStep(); paintFocus(); }
function stretchTick() {
  if (!STRETCH.on) return;
  if (STRETCH.end - Date.now() / 1000 <= 0) { STRETCH.i++; stretchStep(); }
}
function paintStretch(box) {
  const left = Math.max(0, Math.ceil(STRETCH.end - Date.now() / 1000)), [name, how] = STRETCHES[STRETCH.i] || ["", ""];
  box.innerHTML = `<div class="meta"><div>Stretch ${STRETCH.i + 1} of ${STRETCHES.length}</div><small>${esc(name)}</small></div><div class="ring big" id="sRing" style="--p:${Math.round(100 * (1 - left / STRETCH_SECS))}"><span id="sLeft">${left}</span></div>
    <p class="muted">${esc(how)}</p><div class="row"><button data-fa="snext">Next</button><button data-fa="sstop">Stop</button></div>`;
}
function paintFocus() {
  const box = $("#focusBody"); if (!box) return;
  if (STRETCH.on) return paintStretch(box);
  if (FOCUS.phase !== "focus" && FOCUS.phase !== "break") {
    box.innerHTML = `<p class="muted">I'll time the blocks, call the breaks and keep my opinions to myself while you work.</p><div class="presets">${FOCUS_PRESETS.map(([f, b, r, n, d], i) =>
      `<button data-preset="${i}"><b>${f} min</b><span><em>${esc(n)}</em><small>${esc(d)}</small></span></button>`).join("")}</div>`;
    return;
  }
  const isF = FOCUS.phase === "focus", left = Math.max(0, FOCUS.ends - Date.now() / 1000), total = Math.max(1, (isF ? FOCUS.focus_min : FOCUS.break_min) * 60);
  if (!$("#fLeft")) box.innerHTML = `<div class="ring big" id="fRing"><span id="fLeft">--</span></div><div class="meta"><div id="fPhase"></div><small id="fRound"></small></div>
    <div class="row"><button data-fa="skip" id="fSkip"></button><button data-fa="stop">End session</button><button data-fa="stretch" id="fStretch"></button></div>`;
  $("#fRing").style.setProperty("--p", Math.round(100 * (1 - left / total))); $("#fRing").classList.toggle("brk", !isF);
  $("#fLeft").textContent = mmss(left); $("#fPhase").textContent = isF ? "Focus" : "Break";
  $("#fRound").textContent = `Block ${FOCUS.done + (isF ? 1 : 0)} of ${FOCUS.rounds}`; $("#fSkip").textContent = isF ? "Break now" : "Skip break"; $("#fStretch").textContent = "2-minute stretch"; $("#fStretch").hidden = isF;
}
function showFocus() { leaveWorld(); $("#main").dataset.view = "focus"; mark("focus"); paintFocus(); api("/api/focus").then(f => { setFocusState(f); }).catch(() => {}); }
function setFocusState(f) { FOCUS = f && f.phase && f.phase !== "off" ? f : { phase: "off" }; paintChips(); if ($("#main").dataset.view === "focus") paintFocus(); }
$("#focusBody").addEventListener("click", async e => {
  const b = e.target.closest("[data-preset], [data-fa]"); if (!b) return;
  try {
    if (b.dataset.preset) { const [f, br, r] = FOCUS_PRESETS[+b.dataset.preset]; setFocusState(await api("/api/focus", { action: "start", focus_minutes: f, break_minutes: br, rounds: r })); toast(`Focus on for ${f} minutes.`); }
    else if (b.dataset.fa === "stretch") startStretch();
    else if (b.dataset.fa === "snext") { STRETCH.i++; stretchStep(); paintFocus(); }
    else if (b.dataset.fa === "sstop") { STRETCH.on = false; paintFocus(); }
    else setFocusState(await api("/api/focus", { action: b.dataset.fa }));
  } catch (err) { toast("I couldn't reach the focus timer."); }
});
/* World Intelligence: loaded only when opened (the iframe is empty until then); leaving it unloads the page and stops the globe service */
let inWorld = false;
function enterWorld() { inWorld = true; document.body.dataset.world = document.body.dataset.world || "1"; }
function leaveWorld() {
  if (!inWorld) return; inWorld = false; delete document.body.dataset.world;
  $("#frame").dataset.src = ""; $("#frame").src = "about:blank";
  api("/api/world/context", { open: false }).catch(() => {}); api("/api/world/service", { on: false }).catch(() => {});
}
function showHome() { leaveWorld(); $("#main").dataset.view = "home"; mark("home"); }
function showPage(key, then) { if (key === "world") enterWorld(); else leaveWorld(); const [title, src] = PAGES[key]; $("#main").dataset.view = "page"; $("#pageTitle").textContent = title;
  const changed = $("#frame").dataset.src !== src;
  if (changed) { $("#frame").dataset.src = src; $("#frame").src = src; } mark(key); if (then) toFrame(then, changed); }
function toFrame(msg, wait) {    // a message for the embedded page: after it has loaded when it was just opened, at once when it was already showing
  const f = $("#frame"), send = () => { try { f.contentWindow.postMessage(msg, location.origin); } catch (e) {} };
  if (wait) f.addEventListener("load", send, { once: true }); else send();
}
window.addEventListener("message", e => {   // the Play page asks this window to talk to the desktop bridge, or to open another page
  const m = e.origin === location.origin && e.source === $("#frame").contentWindow && e.data && e.data.jarvisPlay; if (!m) return;
  if (m.go) return go(String(m.go));
  const d = desk(), ok = ["companion_game", "companion_celebrate", "report_games"];
  if (d && ok.includes(m.call) && d[m.call]) { try { d[m.call](...(Array.isArray(m.args) ? m.args : [])); } catch (err) {} }
});
/* World Intelligence bridge: the workspace page (same origin) asks the Hub to resize, go home, open a launcher or ask JARVIS */
const WORLD_URLS = ["https://www.osirisai.live/", "https://www.worldmonitor.app/dashboard", "https://nukesimulation.com/", "https://lucy.decart.ai/",
  "https://github.com/bilawalsidhu/gods-eye-view"];
function worldPost(msg) { try { $("#frame").contentWindow.postMessage(msg, location.origin); } catch (e) {} }
window.addEventListener("message", async e => {
  const m = e.origin === location.origin && e.source === $("#frame").contentWindow && e.data && e.data.jarvisWorld; if (!m || !inWorld) return;
  if ("wide" in m) { document.body.dataset.world = String(m.wide === 2 ? 2 : 1); }
  else if (m.go === "home") { PAGES.world[1] = "world.html"; showHome(); }
  else if (typeof m.open === "string" && WORLD_URLS.includes(m.open)) { window.open(m.open, "_blank", "noopener"); }
  else if (typeof m.ask === "string" && m.ask.trim()) send(m.ask);
});
let worldPoll404 = false;
const pollWorld = every(4000, async () => {     // only for "open World Intelligence" said by voice/chat while it is closed
  if (inWorld || worldPoll404) return;
  let r; try { r = await api("/api/world/actions"); } catch (e) { if (String(e.message) === "404") worldPoll404 = true; return; }
  for (const a of r.actions || []) if (a.action === "open") { go("world"); api("/api/world/ack", { id: a.id, ok: true, detail: "World Intelligence is open." }).catch(() => {}); }
});
async function go(key) {
  if (key === "home") return showHome();
  if (key === "chat") { showHome(); mark("chat"); return typing(true); }
  if (key === "code") { showHome(); return typing(true, "Open Visual Studio Code"); }
  if (key === "apps") { showHome(); return typing(true, "Open "); }
  if (key === "focus") return showFocus();
  if (PAGES[key]) return showPage(key);
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-go], [data-say]"); if (!b) return;
  if (b.dataset.go) go(b.dataset.go); else send(b.dataset.say);
});
$("#pagePop").onclick = () => { const src = $("#frame").dataset.src; if (src) window.open(src + (token ? "#t=" + encodeURIComponent(token) : ""), "_blank"); };
/* ---------- app logos and the pinned apps: each icon comes from the program installed on this PC; no icon, the letter mark stays ---------- */
function appIcon(el, key) {
  const slot = el.querySelector(".mono"); if (!slot || !token || !key) return;
  fetch(`/api/app-icon?name=${encodeURIComponent(key)}`, { headers: { Authorization: `Bearer ${token}` } })
    .then(r => r.ok ? r.blob() : Promise.reject(new Error(String(r.status))))
    .then(b => { const im = new Image(); im.alt = ""; im.className = "appic"; im.onload = () => slot.replaceWith(im); im.src = URL.createObjectURL(b); })
    .catch(() => {});                                  // no icon: the letter mark stays
}
function paintPins(names) {
  $$("#launch .pinned").forEach(b => b.remove());
  (names || []).forEach(n => { const b = document.createElement("button"); b.className = "pinned"; b.dataset.say = "Open " + n; b.dataset.app = n; b.title = "Open " + n;
    b.innerHTML = `<span class="mono">${esc(n.slice(0, 2).toUpperCase())}</span>${esc(n.length > 11 ? n.slice(0, 10) + "..." : n)}`;
    $("#launchMore").before(b); appIcon(b, n); });
}
let PIN = { pinned: [], apps: [], max: 6 };
async function loadPins() { try { PIN = await api("/api/launcher"); paintPins(PIN.pinned); } catch (e) {} }
function paintPick() {
  const q = $("#pickFind").value.trim().toLowerCase();
  $("#pickPins").innerHTML = PIN.pinned.map(n => `<button data-un="${esc(n)}" title="Remove from Quick Launch">${esc(n)} <i>Remove</i></button>`).join("");
  const rows = PIN.apps.filter(n => !q || n.includes(q)).slice(0, 60);
  $("#pickList").innerHTML = PIN.pinned.length >= PIN.max ? `<p class="empty">Quick Launch is full. Remove one first.</p>`
    : rows.map(n => `<button data-pin="${esc(n)}">${esc(n)}</button>`).join("") || `<p class="empty">No installed app matches.</p>`;
}
$("#launchMore").onclick = async () => { $("#pick").hidden = false; await loadPins(); $("#pickFind").value = ""; paintPick(); $("#pickFind").focus(); };
$("#pickX").onclick = () => { $("#pick").hidden = true; };
$("#pickFind").oninput = paintPick;
$("#pick").addEventListener("click", async e => {
  const b = e.target.closest("[data-pin], [data-un]"); if (!b) return;
  try { const r = await api(b.dataset.pin ? "/api/launcher/pin" : "/api/launcher/unpin", { name: b.dataset.pin || b.dataset.un });
    PIN.pinned = r.pinned; await loadPins(); paintPick(); } catch (err) { toast("That app can't be pinned."); }
});
$$("[data-app]").forEach(b => appIcon(b, b.dataset.app));

/* ---------- right column data ---------- */
function gauge(name, pct, label) {
  $$(`.ring[data-g="${name}"]`).forEach(r => { const ok = Number.isFinite(pct);
    r.classList.toggle("na", !ok); r.classList.toggle("hot", ok && pct >= 90); r.style.setProperty("--p", ok ? pct : 0);
    r.querySelector("b").textContent = ok ? pct + "%" : "--"; r.title = label || (ok ? "" : "Not available on this computer"); });
}
async function loadSys() {
  let s = null; try { s = await api("/api/system"); } catch (e) {}
  const all = $("#sysAll");
  if (!s) { ["cpu", "gpu", "ram", "ssd"].forEach(g => gauge(g)); all.className = "na"; all.textContent = "System numbers unavailable"; return; }
  gauge("cpu", s.cpu_pct); gauge("ram", s.mem_pct, s.mem_gb ? `${s.mem_gb} GB memory` : ""); gauge("ssd", s.disk_pct, s.disk_free_gb != null ? `${s.disk_free_gb} GB free` : "");
  gauge("gpu", s.gpu ? s.gpu.util_pct : undefined, s.gpu ? `${s.gpu.name}, ${s.gpu.temp_c} C, ${s.gpu.vram_used_gb}/${s.gpu.vram_gb} GB` : "No graphics card numbers on this computer");
  const hot = [["CPU", s.cpu_pct], ["memory", s.mem_pct], ["disk", s.disk_pct]].filter(x => x[1] >= 90).map(x => x[0]);
  all.className = hot.length ? "warn" : ""; all.innerHTML = hot.length ? esc("High: " + hot.join(", ")) : icon("check") + "All Systems Operational";
  const lc = s.last_crash;   // the crash watchdog restarted him: say so (core/watchdog)
  if (lc && Date.now() / 1000 - lc.ts < 86400) { all.className = "warn"; all.innerHTML = esc(`Restarted after a crash at ${lc.at}: ${lc.cause}`); }
}
// The core scorecard (core/scorecard.py, /api/core/scorecard): his everyday commands done right the first time.
const KIND_NAMES = { open: "Open", close: "Close", minimize: "Minimize", focus: "Bring forward", screen: "Jarvis screens",
  globe: "Globe", pc_question: "PC questions", info: "Time, weather", cancel: "Never mind" };
const localDay = t => { const d = new Date(t); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
async function loadCore() {
  let c = null; try { c = await api("/api/core/scorecard"); } catch (e) {}
  const rate = $("#coreRate");
  if (!rate) return;
  const days = c && c.days ? Object.keys(c.days).sort().reverse() : [];
  if (!days.length) { rate.className = "na"; rate.textContent = "No commands yet"; $("#coreStats").innerHTML = ""; $("#coreKinds").innerHTML = ""; }
  else {
    const day = days[0], d = c.days[day], pct = d.rate == null ? null : Math.round(d.rate * 100), target = Math.round((c.target || 0.9) * 100);
    rate.className = pct == null ? "na" : (pct >= target ? "" : "warn");
    rate.textContent = pct == null ? "No commands" : `${pct}% first try`;
    $("#coreSub").textContent = `${day === localDay(Date.now()) ? "Today" : day}: ${d.first_try[0]} of ${d.first_try[1]} everyday commands done right the first time (target ${target}%).`;
    const stat = (n, l) => `<div><b>${esc(String(n))}</b>${esc(l)}</div>`;
    $("#coreStats").innerHTML = stat(d.repeats, "repeats") + stat(d.mishears, "misheard") + stat(d.not_understood, "not understood")
      + stat(d.latency_p50 == null ? "--" : d.latency_p50 + "s", "typical wait");
    $("#coreKinds").innerHTML = Object.entries(d.by_kind || {}).map(([k, [ok, n]]) => { const p = n ? Math.round(ok * 100 / n) : 0;
      return `<div class="k"><span>${esc(KIND_NAMES[k] || k)}</span><span class="bar"><i class="${p < target ? "low" : ""}" style="width:${p}%"></i></span><span>${ok}/${n}</span></div>`; }).join("");
    const tip = (d.missed || []).map(m => `${m.said} (${m.why.replace("_", " ")})`).join("\n");
    $("#coreKinds").title = tip ? "Missed:\n" + tip : "";
  }
  const strip = [];
  for (let i = 6; i >= 0; i--) { const k = localDay(Date.now() - i * 864e5), d = c && c.days ? c.days[k] : null;
    strip.push(d && d.rate != null ? `<span class="${d.rate < (c.target || 0.9) ? "low" : ""}" style="height:${Math.max(8, Math.round(d.rate * 100))}%" title="${k}: ${d.first_try[0]}/${d.first_try[1]}"></span>`
      : `<span class="none" title="${k}: no commands"></span>`); }
  $("#coreDays").innerHTML = strip.join("");
  const r = c && c.replay;
  $("#coreReplay").textContent = r ? `Replay of his ${r.cases} saved lines: ${r.passed}/${r.cases} (${Math.round(r.rate * 100)}%), ${r.when || ""}` : "";
}
function wxIcon(cond) {
  const c = String(cond).toLowerCase(), st = 'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"';
  if (/rain|drizzle|shower|thunder|storm/.test(c)) return `<svg viewBox="0 0 64 64" style="color:#7fd3ff" ${st}><path d="M20 36h26a10 10 0 0 0 0-20 14 14 0 0 0-27 3 9 9 0 0 0 1 17z"/><path d="M24 44l-3 7M34 44l-3 7M44 44l-3 7"/></svg>`;
  if (/cloud|overcast|fog|mist|snow/.test(c)) return `<svg viewBox="0 0 64 64" ${st}><circle cx="24" cy="22" r="9" style="color:#ffc94d" stroke="currentColor"/><path style="color:#cfe3f5" stroke="currentColor" d="M20 46h28a10 10 0 0 0 0-20 13 13 0 0 0-25 3 9 9 0 0 0-3 17z"/></svg>`;
  return `<svg viewBox="0 0 64 64" style="color:#ffc94d" ${st}><circle cx="32" cy="32" r="11"/><path d="M32 8v6M32 50v6M8 32h6M50 32h6M15 15l4 4M45 45l4 4M15 49l4-4M45 19l4-4"/></svg>`;
}
function renderWeather(w) {
  if (!w || !w.now) { $("#wxIcon").innerHTML = ""; $("#wx").innerHTML = `<small class="faint">Weather unavailable</small>`; return; }
  const now = w.now.split(" now: ")[1] || w.now, cond = (now.split(",")[0] || "").trim(), temp = (now.match(/(-?\d+)\s?°F/) || [])[1];
  const place = (w.now.split(" now:")[0] || "").split(",").slice(0, 2).join(",").trim();
  $("#wxIcon").innerHTML = wxIcon(cond);
  $("#wx").innerHTML = `<b>${temp ? esc(temp) + "\u00B0F" : "--"}</b><small>${esc(place)}</small><small class="muted">${esc(cond)}</small>`;
}
function tick() { const d = new Date();
  $("#date").textContent = d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" }); $("#clock").textContent = hm(d); }
const MISSION = { reminders: [], workout: null, missions: [] };
function renderMission() {
  const rows = [];
  if (MISSION.workout) rows.push([`Workout: ${MISSION.workout.title}`, MISSION.workout.done, MISSION.workout.done ? "Done" : ""]);
  MISSION.reminders.forEach(r => rows.push([r.replace(/^\d\d:\d\d /, ""), false, (r.match(/^\d\d:\d\d/) || [""])[0]]));
  MISSION.missions.forEach(m => rows.push([m.goal || m.title || "Mission", /^(done|finished|complete)/i.test(m.state || ""), String(m.state || "").replace(/_/g, " ")]));
  $("#missionList").innerHTML = rows.length ? rows.slice(0, 12).map(([t, done, meta]) => `<li class="${done ? "done" : ""}"><span>${esc(t)}</span>${meta ? `<small>${esc(meta)}</small>` : ""}</li>`).join("")
    : `<li class="empty" style="display:block">Nothing set for today. Tell Jarvis "remind me to..." or give him a mission.</li>`;
}
let seeded = false;
async function loadDash() {
  let d = null; try { d = await api("/api/dashboard"); } catch (e) {}
  if (!d) { if (!desk()) setState("offline"); renderWeather(null); return; }
  if (!meLoaded) loadMe();                        // the server came up after the window did
  renderWeather(d.weather); MISSION.reminders = d.today || [];
  if (d.jarvis) {
    if (!desk() || state === "starting") setState(d.jarvis.state);           // the desktop window gets live events instead
    if (!seeded) { seeded = true; (d.jarvis.lines || []).filter(l => l.who === "you" || l.who === "jarvis").slice(-6).forEach(l => addLine(l.who, l.text, l.t ? new Date(l.t * 1000) : undefined)); const l = $("#log").lastElementChild; if (l) l.dataset.open = "0"; }
  }
  renderMission();
}
async function loadSlow() {
  try { const f = await api("/api/fitness"), t = f && f.today, iso = new Date().toISOString().slice(0, 10);
    MISSION.workout = t && t.items && t.items.length ? { title: t.title || "Today's session", done: (f.workouts || []).some(w => String(w.date || "").startsWith(iso)) } : null; } catch (e) {}
  try { MISSION.missions = ((await api("/api/missions?active=1")).missions || []).slice(0, 6); } catch (e) {}
  renderMission();
  try { const t = await api("/api/timers"); if (Array.isArray(t.timers)) { TIMERS = t.timers.map(x => ({ id: x.id, label: x.label, ends: x.ends })); paintChips(); } } catch (e) {}
  try { const c = await api("/api/companion/visible"); paintCompanion(!!c.visible); if ("muted" in c) paintMute(!!c.muted); } catch (e) {}
}
/* profile card: click the photo to choose a picture, the pencil to edit the tagline (both saved on the server for him) */
async function upload(path, file) {
  const f = new FormData(); f.append("file", file);
  const r = await fetch(path, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: f });
  const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || "That didn't work.");
  return d;
}
function setPhoto(src) { if (!src || !/^data:image\//i.test(String(src))) return; const im = document.createElement("img"); im.alt = ""; im.src = src; $("#photo").replaceChildren(im); }
$("#photo").onclick = () => $("#photoFile").click();
$("#photoFile").onchange = async e => {
  const file = e.target.files && e.target.files[0]; e.target.value = ""; if (!file) return;
  if (file.size > 8 * 1024 * 1024) return toast("That picture is too big. Keep it under 8 MB.");
  try { const d = await upload("/api/me/photo", file); setPhoto(d.photo); toast("Photo saved."); } catch (err) { toast(err.message); }
};
$("#tagEdit").onclick = () => { $("#tagInput").value = $("#ownerTag").textContent; $("#ownerTag").hidden = true; $("#tagForm").hidden = false; $("#tagInput").focus(); };
$("#tagForm").onsubmit = async e => {
  e.preventDefault();
  try { const d = await api("/api/me/tagline", { tagline: $("#tagInput").value }); $("#ownerTag").textContent = d.tagline; toast("Saved."); } catch (err) { toast("Couldn't save that."); }
  $("#tagForm").hidden = true; $("#ownerTag").hidden = false;
};
const FALLBACK_NAME = "Your Name";
let meLoaded = false;
async function loadMe() {
  try { const me = await api("/api/me"), n = String(me.name || "").trim(); meLoaded = true;
    if (n) { $("#ownerName").textContent = n.includes(" ") ? n : FALLBACK_NAME.toLowerCase().startsWith(n.toLowerCase()) ? FALLBACK_NAME : n;
      $("#builtFor").textContent = "BUILT FOR " + n.split(" ")[0].toUpperCase(); ownerShort = n.split(" ")[0]; }
    if (me.tagline) $("#ownerTag").textContent = me.tagline;
    setPhoto(me.photo);                                                     // an <img>, never a CSS url("...") built from text
  } catch (e) {}
}

/* ---------- timers, focus session, picture card ---------- */
let TIMERS = [], FOCUS = { phase: "off" };
const mmss = n => { n = Math.max(0, Math.round(n)); const h = Math.floor(n / 3600), m = Math.floor(n % 3600 / 60), sec = String(n % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`; };
function paintChips() {
  const now = Date.now() / 1000, box = $("#chips");
  TIMERS = TIMERS.filter(t => t && Number.isFinite(+t.ends) && +t.ends > now - 2);
  let html = TIMERS.map(t => `<span class="chip">${icon("pulse")}${esc(t.label || "Timer")} <b>${mmss(t.ends - now)}</b></span>`).join("");
  if (FOCUS.phase === "focus" || FOCUS.phase === "break") html = `<span class="chip ${FOCUS.phase === "focus" ? "focus" : "brk"}">${FOCUS.phase === "focus" ? "Focus" : "Break"} <b>${mmss(FOCUS.ends - now)}</b></span>` + html;
  if (box.innerHTML !== html) box.innerHTML = html;
  box.classList.toggle("on", !!html);
}
function showMedia(m) {
  const src = String((m && m.src) || ""); if (!/^(data:image\/|https?:)/i.test(src)) return;
  $("#mediaImg").src = src; $("#mediaCap").textContent = String(m.caption || "");
  const link = /^https?:/i.test(String(m.link || "")) ? m.link : "", a = $("#mediaLink");
  a.style.display = link ? "" : "none"; if (link) a.href = link; else a.removeAttribute("href");
  $("#media").classList.add("on"); showHome();
}
function hideMedia() { $("#media").classList.remove("on"); $("#mediaImg").removeAttribute("src"); }
$("#mediaX").onclick = hideMedia;
setInterval(() => { if (live() && (TIMERS.length || FOCUS.phase !== "off" || STRETCH.on)) { paintChips(); stretchTick(); if ($("#main").dataset.view === "focus") paintFocus(); } }, 1000);

/* ---------- live events from the HUD process (same messages the old panel received) ---------- */
/* voice, tray and the companion's menu raise the window on the right view */
function openMode(msg) {
  const v = msg.value;
  if (v === "dashboard" || v === "chat") return go(v);
  if (v === "workout") { showPage("workout", { jarvisCC: "start-workout" }); return; }
  if (v === "play" || v === "joke") { showPage("play", { play: { game: v === "joke" ? "joke" : msg.game || "" } }); return; }
  if (v === "focus") return showFocus();
  if (v === "world") {                       // "spy mode", "open intelligence": the page opens (or re-modes) with that preset
    const preset = /^[a-z]{1,20}$/.test(String(msg.preset || "")) ? msg.preset : "standard";
    if (inWorld) return worldPost({ jarvisWorldPreset: preset });
    PAGES.world[1] = "world.html?preset=" + preset; return go("world");
  }
  if (v === "house") {                       // "show me my house": the camera page on the camera he named
    const cam = /^[a-z0-9_]{1,32}$/.test(String(msg.cam || "")) ? msg.cam : "";
    PAGES.house[1] = "house.html" + (cam ? "?cam=" + cam : "");
    $("#frame").dataset.src = "";             // always a fresh load: the camera may have come online since the page opened
    return go("house");
  }
  if (v === "stretch") { showFocus(); return startStretch(); }
}
window.jarvis = {
  handle(msg) {
    switch (msg && msg.type) {
      case "state": setState(msg.value); if (msg.value === "listening") { const l = $("#log").lastElementChild; if (l) l.dataset.open = "0"; } break;
      case "level": level = Math.min(1, +msg.value || 0); break;
      case "line": addLine(msg.who, msg.text); break;
      case "doing": setDoing(msg.value); break;
      case "notify": toast(`${msg.title || ""}${msg.body ? ": " + msg.body : ""}`); break;
      case "badges": if (msg.value && "muted" in msg.value) paintMute(!!msg.value.muted); break;
      case "companion": paintCompanion(!!msg.visible); break;
      case "visibility": setVisible(!!msg.value); break;
      case "timers": TIMERS = Array.isArray(msg.value) ? msg.value : []; paintChips(); break;
      case "focus": setFocusState(msg.value); break;
      case "media": showMedia(msg); break;
      case "media_clear": hideMedia(); break;
      case "mode": openMode(msg); break;
    }
  },
};

/* ---------- polling: only while the window is on screen, never overlapping, slow data slowly ---------- */
let shown = true;
const live = () => shown && !document.hidden;
function setVisible(on) { shown = on; if (live()) pollNow(); }
function every(period, fn) {                  // fn runs at most once at a time, and only while the window is visible
  let busy = false, last = Date.now();      // start() has just loaded everything once
  const run = async force => {
    const ms = typeof period === "function" ? period() : period;
    if (busy || !live() || (!force && Date.now() - last < ms - 50)) return;
    busy = true; last = Date.now();
    try { await fn(); } catch (e) {} finally { busy = false; }
  };
  setInterval(() => run(false), 5000);
  return () => run(true);
}
const SLOW_MS = 60000, FAST_MS = 5000;
const pollSys = every(FAST_MS, loadSys);
const pollDash = every(() => desk() ? SLOW_MS : FAST_MS, loadDash);
const pollSlow = every(SLOW_MS, loadSlow);
const pollCore = every(SLOW_MS, loadCore);
function pollNow() { pollSys(); pollDash(); pollSlow(); pollWorld(); pollCore(); }
document.addEventListener("visibilitychange", () => { if (live()) pollNow(); });

(async function start() {
  tick(); setInterval(() => { if (live()) tick(); }, 5000);
  if (!token) { addLine("sys", "Open the Command Center from Jarvis on the PC so it can sign in."); setState("offline"); return; }
  loadMe(); loadPins(); await loadDash(); loadSys(); loadSlow(); loadCore();
})();
