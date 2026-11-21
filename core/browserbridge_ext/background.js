// JARVIS Tab Bridge: the browser's own tabs/windows API, offered to JARVIS over a local long-poll channel.
// It runs only the five operations below. No page content, history, cookies, downloads or navigation to arbitrary URLs.
// config.json (written next to this file for each session) holds the 127.0.0.1 port and a random token.

const OPS = {
  async windows() {
    const ws = await chrome.windows.getAll({populate: true, windowTypes: ["normal"]});
    return ws.map(w => ({id: w.id, focused: !!w.focused, state: w.state, tab_count: (w.tabs || []).length}));
  },
  async tabs(args) {
    const q = args && args.window ? {windowId: args.window} : {};
    const ts = await chrome.tabs.query(q);
    return ts.map(tab);
  },
  async tab(args) {
    try { return tab(await chrome.tabs.get(args.tab)); } catch (e) { return null; }
  },
  async activate(args) {
    const t = await chrome.tabs.update(args.tab, {active: true});
    await chrome.windows.update(t.windowId, {focused: true});
    return tab(await chrome.tabs.get(args.tab));
  },
  async close(args) {
    await chrome.tabs.remove(args.tab);
    try { await chrome.tabs.get(args.tab); return {closed: false, tab: args.tab}; } catch (e) { return {closed: true, tab: args.tab}; }
  },
  async new_tab(args) {
    return tab(await chrome.tabs.create({windowId: args.window, url: "about:blank", active: !!args.active}));
  },
};

function tab(t) {
  return {id: t.id, window: t.windowId, index: t.index, url: t.url || t.pendingUrl || "", title: t.title || "", active: !!t.active};
}

let cfg = null;
let running = false;

// In an everyday browser the service worker is stopped when idle. Two safeguards: every poll makes an extension API call
// (which counts as activity), and an alarm every 30 s restarts the loop if the worker had been stopped anyway.
chrome.alarms.create("jarvis-bridge-keepalive", {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener(() => { if (!running) loop(); });
chrome.runtime.onStartup.addListener(() => { if (!running) loop(); });

async function loop() {
  if (running) return;
  running = true;
  try {
    if (!cfg) cfg = await (await fetch(chrome.runtime.getURL("config.json"))).json();
  } catch (e) { running = false; return; }
  const base = `http://127.0.0.1:${cfg.port}`;
  for (;;) {
    await chrome.runtime.getPlatformInfo();
    let cmd = null;
    try {
      const r = await fetch(`${base}/poll`, {headers: {"X-Bridge-Token": cfg.token, "X-Bridge-Session": cfg.session}});
      if (r.status === 200) cmd = await r.json();
    } catch (e) {
      await new Promise(res => setTimeout(res, 500));
      continue;
    }
    if (!cmd || !cmd.op) continue;
    let reply;
    try {
      if (!(cmd.op in OPS)) throw new Error(`unknown operation ${cmd.op}`);
      reply = {id: cmd.id, ok: true, result: await OPS[cmd.op](cmd.args || {})};
    } catch (e) {
      reply = {id: cmd.id, ok: false, error: String(e && e.message || e)};
    }
    try {
      await fetch(`${base}/reply`, {method: "POST", headers: {"X-Bridge-Token": cfg.token, "Content-Type": "application/json"},
                                   body: JSON.stringify(reply)});
    } catch (e) { /* the host went away: the next poll retries */ }
  }
}

loop();
