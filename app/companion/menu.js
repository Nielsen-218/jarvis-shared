// Living Presence Engine — his holographic interaction menu (DOM module, loaded by companion.html).
//
// Clicking him opens this beside him: the host grows his window to hold both (core/presence.expand_layout) so he
// never moves, and the full Jarvis panel is NOT raised (it stays one click away). Every entry reaches a real
// system: Talk -> his brain (/api/chat) or the microphone, Skills -> the registered tools (/api/skills, run through
// tools/registry), Play -> the panel's mini-games, Wardrobe and Customize -> their own windows.

export const SIZES = { main: [300, 300], talk: [300, 320], skills: [384, 476], skill: [384, 476], play: [304, 404],
  settings: [298, 430], habitat: [310, 360], home: [360, 540], antics: [330, 460] };
const TOGGLES = [
  ["auto_place", "Move out of my way"], ["work_aware", "Calm while I work"], ["hover_greet", "Say hello on hover"],
  ["app_reactions", "Kick apps I open"], ["launch_drops", "Open shortcuts dropped on him"],
  ["proactive", "Greetings and break reminders"], ["seasonal", "Seasonal outfits"], ["reduced_motion", "Reduce motion"],
];

export function createMenu({ el, api, actions }) {
  let view = null, open = false, layout = null, busy = false, blurTimer = 0;
  const st = { skills: null, cats: [], prefs: { favorites: [], recent: [] }, filter: "all", query: "", detail: null,
    result: null, talk: [], games: null, runs: 0, home: null, furniture: null, homeError: "" };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  async function call(method, path, body) {
    const a = api();
    if (!a || !a.call) return { ok: false, data: { detail: "Jarvis's server isn't connected." } };
    try { return await a.call(method, path, body ?? null); } catch (e) { return { ok: false, data: { detail: String(e) } }; }
  }

  // ---------------------------------------------------------------- open / close / layout
  async function place(v) {
    const [w, h] = SIZES[v] || SIZES.main;
    const a = api();
    let lay = null;
    if (a && a.companion_expand) {
      try { lay = await a.companion_expand(w, h, layout ? layout.side : "", window.devicePixelRatio || 1); } catch {}
    }
    const r = actions.robotSize();
    layout = lay || { side: "right", robot: [0, 0, r.w, r.h], panel: [0, 0, w, h], local: true };
    actions.applyLayout(layout);
  }

  async function show(v = "main") {
    if (!open) return openMenu(v);
    view = v;
    await place(v);
    render();
  }

  async function openMenu(v = "main") {
    if (open) return show(v);
    open = true; view = v;
    actions.onOpen(v);
    await place(v);
    el.classList.remove("closing");
    el.classList.add("open");
    document.documentElement.classList.add("menu-open");
    render();
    const a = api();
    try { a && a.companion_focus && a.companion_focus(true); } catch {}
    setTimeout(() => { const first = el.querySelector("[data-act], input, button"); first && first.focus(); }, 30);
  }

  async function close() {
    if (!open) return;
    open = false; clearTimeout(blurTimer);
    el.classList.add("closing");
    await new Promise((r) => setTimeout(r, 120));
    el.classList.remove("open", "closing");
    el.innerHTML = "";
    document.documentElement.classList.remove("menu-open");
    const a = api();
    try { a && a.companion_collapse && (await a.companion_collapse()); } catch {}
    layout = null;
    actions.applyLayout(null);
    try { a && a.companion_focus && a.companion_focus(false); } catch {}
    actions.onClose();
  }

  const toggle = (v = "main") => (open ? close() : openMenu(v));

  // ---------------------------------------------------------------- rendering
  function head(title, back = true) {
    return `<div class="mhead">${back ? '<button class="mbtn icon" data-act="back" aria-label="Back">‹</button>' : ""}
      <b>${esc(title)}</b><span class="sub">${esc(actions.status())}</span>
      <button class="mbtn icon" data-act="close" aria-label="Close menu">✕</button></div>`;
  }

  function render() {
    if (!open) return;
    const html = { main: renderMain, talk: renderTalk, skills: renderSkills, skill: renderSkill, play: renderPlay,
      settings: renderSettings, habitat: renderHabitat, home: renderHome, antics: renderAntics }[view] || renderMain;
    const hadFocus = el.contains(document.activeElement) || document.activeElement === document.body;
    el.innerHTML = html();
    wire();
    if (hadFocus) {                                   // the focused control was just replaced: keep the keyboard here
      const f = ["#sq", "#talktext", ".tile", "[data-skill]", "[data-act]:not([data-act='back']):not([data-act='close'])"]
        .map((q) => el.querySelector(q)).find(Boolean);                  // in this order (the text box before Listen)
      if (f) f.focus({ preventScroll: true });
    }
  }

  function renderMain() {
    const tile = (act, ic, label) => `<button class="mbtn tile" data-act="${act}"><span class="ic">${ic}</span>${label}</button>`;
    return `${head("J.A.R.V.I.S.", false)}
      <div class="grid" role="menu">
        ${tile("habitat", "🏙", "Hangouts")}${tile("room", "⌂", "Home / Room")}${tile("talk", "💬", "Talk")}${tile("skills", "✦", "Skills")}${tile("play", "🎮", "Play")}
        ${tile("wardrobe", "👔", "Wardrobe")}${tile("customize", "🎨", "Customize")}${tile("settings", "⚙️", "Settings")}
      </div>
      <div class="mfoot"><button class="mbtn" data-act="panel">Full panel</button><span class="muted">Ctrl+Alt+K · Esc</span></div>`;
  }

  function renderTalk() {
    const lines = st.talk.slice(-12).map((l) => `<div class="out${l.err ? " err" : ""}"><b>${l.who === "you" ? "You" : "Jarvis"}:</b> ${esc(l.text)}</div>`).join("");
    return `${head("Talk")}
      <div class="row"><button class="mbtn gold" data-act="listen">🎙 Listen</button><span class="muted">or type to him</span></div>
      <div class="scroll mview" id="talklog">${lines || '<div class="muted">Ask anything — he can use all his skills for you.</div>'}</div>
      <form class="row" id="talkform"><input class="minput" id="talktext" placeholder="Ask Jarvis…" style="flex:1" autocomplete="off">
        <button class="mbtn" type="submit"${busy ? " disabled" : ""}>${busy ? '<span class="spin"></span>' : "Send"}</button></form>`;
  }

  function skillMatches(s) {
    const q = st.query.trim().toLowerCase();
    if (st.filter === "fav" && !st.prefs.favorites.includes(s.name)) return false;
    if (st.filter === "recent" && !st.prefs.recent.includes(s.name)) return false;
    if (!["all", "fav", "recent"].includes(st.filter) && s.category !== st.filter) return false;
    if (!q) return true;
    return (s.title + " " + s.name + " " + s.summary + " " + s.category + " " + (s.examples || []).join(" ")).toLowerCase().includes(q);
  }

  function renderSkills() {
    if (!st.skills) return `${head("Skills")}<div class="muted"><span class="spin"></span>Reading what I can do…</div>`;
    if (st.skills.error) return `${head("Skills")}<div class="out err">${esc(st.skills.error)}</div>`;
    const chip = (key, label) => `<button class="mbtn chip${st.filter === key ? " on" : ""}" data-filter="${esc(key)}">${esc(label)}</button>`;
    let list = st.skills.filter(skillMatches);
    if (st.filter === "recent") list = st.prefs.recent.map((n) => st.skills.find((s) => s.name === n)).filter(Boolean);
    const items = list.map((s) => `<div class="row"><button class="fav" data-fav="${esc(s.name)}" aria-label="Favourite">${st.prefs.favorites.includes(s.name) ? "★" : "☆"}</button>
        <button class="mbtn skill" data-skill="${esc(s.name)}"><span><span class="nm">${esc(s.title)}</span><small>${esc(s.summary)}</small></span>
        ${s.status !== "ready" ? `<span class="st">${esc(s.status_label)}</span>` : ""}</button></div>`).join("");
    return `${head(`Skills · ${st.skills.length}`)}
      <input class="minput" id="sq" placeholder="Search ${st.skills.length} skills…" value="${esc(st.query)}" autocomplete="off">
      <div class="chips">${chip("all", "All")}${chip("fav", "★ Favourites")}${chip("recent", "Recent")}${st.cats.map((c) => chip(c, c)).join("")}</div>
      <div class="scroll" id="slist">${items || '<div class="muted">Nothing matches.</div>'}</div>`;
  }

  function inputField(p) {
    const id = `arg-${p.name}`, label = `${esc(p.name.replace(/_/g, " "))}${p.required ? " *" : ""}`;
    const hint = p.description ? `<small class="muted">${esc(p.description)}</small>` : "";
    let input;
    if (p.enum) input = `<select class="minput" id="${id}">${p.required ? "" : '<option value="">(default)</option>'}${p.enum.map((v) => `<option>${esc(v)}</option>`).join("")}</select>`;
    else if (p.type === "boolean") input = `<input type="checkbox" id="${id}">`;
    else if (p.type === "integer" || p.type === "number") input = `<input class="minput" type="number" id="${id}" step="${p.type === "integer" ? 1 : "any"}">`;
    else if (p.type === "object" || (p.type === "array" && p.items_type === "object")) input = `<textarea class="minput" id="${id}" rows="2" placeholder="JSON"></textarea>`;
    else input = `<input class="minput" id="${id}" placeholder="${p.type === "array" ? "comma, separated" : ""}" autocomplete="off">`;
    return `<label style="display:flex;flex-direction:column;gap:2px;margin:4px 0"><span>${label}</span>${input}${hint}</label>`;
  }

  function renderResult() {
    const r = st.result;
    if (!r) return "";
    if (r.running) return `<div class="out"><span class="spin"></span>Running ${esc(r.title)}… <span id="elapsed">${r.elapsed || 0}s</span></div>`;
    const imgs = (r.images || []).map((src) => `<img src="${esc(src)}" alt="">`).join("");
    const said = r.explained ? `<div class="out"><b>Jarvis:</b> ${esc(r.explained)}</div>` : r.explaining ? '<div class="muted"><span class="spin"></span>Putting it into words…</div>' : "";
    return `<div class="out${r.ok ? "" : " err"}">${r.ok ? "" : "<b>That didn't work.</b> "}${esc(r.text || (r.ok ? "Done." : "No details."))}${imgs}</div>
      <div class="muted">${r.ok ? "Finished" : "Failed"} in ${r.seconds ?? "?"} s · ran ${esc(r.name)}</div>${said}`;
  }

  function renderSkill() {
    const s = st.detail;
    if (!s) return renderSkills();
    const ex = (s.examples || []).slice(0, 3).map((e) => `“${esc(e)}”`).join(" · ");
    const params = (s.params || []).map(inputField).join("");
    return `${head(s.title)}
      <div class="scroll mview">
        <div>${esc(s.description)}</div>
        ${ex ? `<div class="muted">e.g. ${ex}</div>` : ""}
        <div class="muted">${esc(s.category)} · ${s.status === "ready" ? "ready" : esc(s.status_label)}${s.slow ? " · takes a while" : ""}${s.depends_on ? " · uses " + esc(s.depends_on) : ""}</div>
        ${params ? `<div>${params}</div>` : '<div class="muted">No details needed.</div>'}
        <div id="skillout">${renderResult()}</div>
      </div>
      <div class="row"><button class="mbtn gold" data-act="run"${busy ? " disabled" : ""}>${busy ? '<span class="spin"></span>Running' : "▶ Run"}</button>
        <button class="mbtn" data-fav="${esc(s.name)}">${st.prefs.favorites.includes(s.name) ? "★ Favourite" : "☆ Favourite"}</button></div>`;
  }

  function renderPlay() {
    const games = st.games && st.games.games ? st.games.games : null;
    const list = games ? games.map((g) => `<button class="mbtn game" data-game="${esc(g.id)}"><span class="ic">${esc(g.icon || "🎮")}</span>
        <span><b>${esc(g.name)}</b><small class="muted">${esc(g.desc || "")}</small></span><span class="best">${esc(g.best || "")}</span></button>`).join("")
      : '<div class="muted">Open the full panel once and its games appear here.</div>';
    return `${head("Play")}
      <div class="scroll">
        <div class="muted" style="margin:2px 0 5px">Right here, with me</div>
        <button class="mbtn game" data-act="antics"><span class="ic">✦</span><span><b>Little adventures</b><small class="muted">Wings, hoverboards, shields, magic and more.</small></span></button>
        <button class="mbtn game" data-act="catchtoy"><span class="ic">🖐</span><span><b>Throw me a ball</b><small class="muted">Drag and release a toss into my hands.</small></span></button>
        <button class="mbtn game" data-act="juggletoy"><span class="ic">🤹</span><span><b>Juggle together</b><small class="muted">Real hand interceptions and rebounds.</small></span></button>
        <button class="mbtn game" data-act="kicktoy"><span class="ic">⚽</span><span><b>Kick practice</b><small class="muted">Toss towards my foot.</small></span></button>
        <button class="mbtn game" data-act="stoptoy">Stop direct play</button>
        <button class="mbtn game" data-act="ball"><span class="ic">⚽</span><span><b>Play ball</b><small class="muted">I bat a little toy around.</small></span></button>
        <button class="mbtn game" data-act="jetpack"><span class="ic">🚀</span><span><b>Jetpack joyride</b><small class="muted">A quick thruster-powered lap.</small></span></button>
        <button class="mbtn game" data-act="surprise"><span class="ic">✨</span><span><b>Surprise me</b><small class="muted">A little robotic nonsense.</small></span></button>
        <button class="mbtn game" data-act="dance"><span class="ic">🕺</span><span><b>Dance</b><small class="muted">A short victory jig.</small></span></button>
        <button class="mbtn game" data-act="tossme"><span class="ic">🤸</span><span><b>Toss me</b><small class="muted">Off I go, across the screen.</small></span></button>
        <div class="muted" style="margin:8px 0 5px">Mini-games · your best scores</div>${list}
      </div>`;
  }

  function renderAntics() {
    return `${head("Little adventures")}<div class="scroll"><p class="muted">Short, interruptible physical performances. Escape returns to the desktop. Draw lets you sketch with him.</p>
      ${actions.performances().map(name=>`<button class="mbtn game" data-performance="${esc(name)}">${esc(name.replaceAll("_"," "))}</button>`).join("")}</div>`;
  }

  function renderHome() {
    const s=actions.homeState();
    const rooms=["bedroom","kitchen","workshop","fitness","reading"];
    const items=Object.keys(s.furniture[s.room]);
    if(!items.includes(st.furniture)) st.furniture=items[0];
    const f=s.furniture[s.room][st.furniture];
    const button=(a,label)=>`<button class="mbtn" data-home-action="${a}">${label}</button>`;
    return `${head("Home / Room")}<div class="scroll home-controls">
      <div class="row"><button class="mbtn gold" data-act="enterroom">Enter home</button><button class="mbtn" data-act="exitroom">Return to desktop</button></div>
      <label>Room <select id="room-select" class="minput">${rooms.map(r=>`<option ${r===s.room?"selected":""}>${r}</option>`).join("")}</select></label>
      <p class="muted">${esc(s.activity)} · One furnished space, your robot, saved locally.</p>
      <div class="row">${button("sleep","Sleep")}${button("wake","Wake")}${button("cook","Cook")}${button("tinker","Workshop")}${button("exercise","Exercise")}${button("read","Read")}${button("pet","Pet Pip")}</div>
      <h4>Decorate</h4><div class="row"><label>Accent <input id="home-accent" type="color" value="${esc(s.accent)}"></label>
      <label><input id="home-lamp" type="checkbox" ${s.lamp?"checked":""}> Lamp</label></div>
      <label>Furniture <select class="minput" id="home-furniture">${items.map(i=>`<option ${i===st.furniture?"selected":""}>${i}</option>`).join("")}</select></label>
      <label>Left / right <input id="furniture-x" type="range" min="-0.24" max="0.24" step="0.005" value="${f.position[0]}"></label>
      <label>Forward / back <input id="furniture-z" type="range" min="-0.168" max="0.168" step="0.005" value="${f.position[2]}"></label>
      <label>Turn <input id="furniture-turn" type="range" min="-3.14" max="3.14" step="0.05" value="${f.rotation}"></label>
      <h4>Pet & routines</h4><label><input id="home-pet" type="checkbox" ${s.pet.enabled?"checked":""}> Pet companion</label>
      <label>Pet name <input id="pet-name" class="minput" maxlength="32" value="${esc(s.pet.name)}"></label>
      <label><input id="home-routines" type="checkbox" ${s.routines.enabled?"checked":""}> Follow saved daily routine</label>
      <p class="muted">${s.routines.entries.map(e=>`${esc(e.time)} ${esc(e.activity)}`).join(" · ") || "No routine saved."}</p>
      <button class="mbtn" data-act="routine-default">Save wake / meals / reading / night routine</button>
      <h4>Collections (${s.inventory.length})</h4><p>${s.inventory.map(i=>esc(i.id)).join(" · ") || "Catch, juggle or kick three times to earn a real play badge."}</p>
      <h4>Scrapbook</h4>${s.scrapbook.map(e=>`<article class="out"><b>${esc(e.title)}</b><p>${esc(e.text)}</p></article>`).join("")}
      <form id="scrapform"><input class="minput" id="scrap-title" maxlength="80" placeholder="Page title" required aria-label="Scrapbook page title">
      <textarea class="minput" id="scrap-text" maxlength="500" placeholder="A little memory" required aria-label="Scrapbook memory"></textarea><button class="mbtn">Save page</button></form>
      <p class="muted" role="status">${esc(st.homeError)}</p>
    </div>`;
  }

  function renderHabitat() {
    return `${head("Real desktop hangouts")}<div class="scroll">
      <p class="muted">Only real, visible surfaces. Search, taskbar controls and folders stay clickable.</p>
      ${[["favorite","Favorite hangout"],["taskbar","Sit on taskbar"],["search","By Windows search"],["folder","Peek behind a folder"],["window","Perch on a window"],["save","Remember this surface"],["leave","Climb down / leave"]]
        .map(([a,label])=>`<button class="mbtn game" data-habitat="${a}">${label}</button>`).join("")}
    </div>`;
  }

  function renderSettings() {
    const s = actions.settings();
    const rows = TOGGLES.map(([k, label]) => `<label class="toggle"><span>${esc(label)}</span><input type="checkbox" data-setting="${k}"${s[k] ? " checked" : ""}></label>`).join("");
    return `${head("Settings")}
      <div class="scroll">${rows}
        <div class="toggle"><span>Size</span><input type="range" min="50" max="400" step="10" id="msize" value="${s.character_px}" style="width:120px"><span class="muted" id="msizel">${s.character_px} px</span></div>
        <div class="row" style="flex-wrap:wrap;margin-top:8px">
          <button class="mbtn" data-act="calmday">Quiet play for today</button><button class="mbtn" data-act="yield">Let clicks through me (30 s)</button>
          <button class="mbtn" data-act="${actions.docked() ? "undock" : "dock"}">${actions.docked() ? "Come back out" : "Tuck me away"}</button>
          <button class="mbtn" data-act="home">Back to my spot</button>
          <button class="mbtn mag" data-act="panel">Full panel</button>
        </div>
      </div>`;
  }

  // ---------------------------------------------------------------- behaviour
  function wire() {
    const saveHome=async patch=>{const r=await actions.changeHome(patch);st.homeError=r?.ok?"Saved.":r?.reason||"Could not save.";if(open&&view==="home") render();};
    el.querySelectorAll("[data-home-action]").forEach(b=>b.addEventListener("click",async()=>{
      const action=b.dataset.homeAction, room={sleep:"bedroom",wake:"bedroom",cook:"kitchen",tinker:"workshop",exercise:"fitness",read:"reading"}[action]||actions.homeState().room;
      await saveHome({room,activity:action,routines:{enabled:false}});
      await close();await actions.enterHome();
    }));
    const bind=(id,fn)=>el.querySelector("#"+id)?.addEventListener("change",fn);
    bind("room-select",e=>saveHome({room:e.target.value,activity:"idle",routines:{enabled:false}}));
    bind("home-accent",e=>saveHome({accent:e.target.value}));bind("home-lamp",e=>saveHome({lamp:e.target.checked}));
    bind("home-pet",e=>saveHome({pet:{enabled:e.target.checked}}));bind("pet-name",e=>saveHome({pet:{name:e.target.value||"Pip"}}));
    bind("home-routines",e=>saveHome({routines:{enabled:e.target.checked}}));
    bind("home-furniture",e=>{st.furniture=e.target.value;render();});
    for(const [id,axis] of [["furniture-x",0],["furniture-z",2],["furniture-turn",null]]) bind(id,e=>{
      const s=actions.homeState(), f=structuredClone(s.furniture[s.room][st.furniture]);
      if(axis===null) f.rotation=Number(e.target.value);else f.position[axis]=Number(e.target.value);
      saveHome({furniture:{[s.room]:{[st.furniture]:f}}});
    });
    el.querySelector("#scrapform")?.addEventListener("submit",e=>{e.preventDefault();const s=actions.homeState();
      saveHome({scrapbook:[...s.scrapbook,{title:el.querySelector("#scrap-title").value,text:el.querySelector("#scrap-text").value,color:s.accent}].slice(-24)});});
    el.querySelectorAll("[data-performance]").forEach(b=>b.addEventListener("click",()=>actions.perform(b.dataset.performance)));
    el.querySelectorAll("[data-habitat]").forEach(b=>b.addEventListener("click",()=>actions.habitat(b.dataset.habitat)));
    el.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); act(b.dataset.act); }));
    el.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => { st.filter = b.dataset.filter; render(); }));
    el.querySelectorAll("[data-fav]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); toggleFav(b.dataset.fav); }));
    el.querySelectorAll("[data-skill]").forEach((b) => b.addEventListener("click", () => {
      st.detail = st.skills.find((s) => s.name === b.dataset.skill); st.result = null; show("skill");
    }));
    el.querySelectorAll("[data-game]").forEach((b) => b.addEventListener("click", async () => {
      await actions.exitScene();
      const a = api(); try { a && a.play_game && a.play_game(b.dataset.game); } catch {}
      actions.bubble("Game on — it's in the panel."); close();
    }));
    el.querySelectorAll("[data-setting]").forEach((c) => c.addEventListener("change", () => actions.saveSetting(c.dataset.setting, c.checked)));
    const sq = el.querySelector("#sq");
    if (sq) {
      sq.addEventListener("input", () => { st.query = sq.value; const pos = sq.selectionStart; render(); const n = el.querySelector("#sq"); if (n) { n.focus(); n.setSelectionRange(pos, pos); } });
    }
    const tf = el.querySelector("#talkform");
    if (tf) tf.addEventListener("submit", (e) => { e.preventDefault(); const t = el.querySelector("#talktext"); ask(t.value); });
    const ms = el.querySelector("#msize");
    if (ms) {
      ms.addEventListener("input", () => { el.querySelector("#msizel").textContent = ms.value + " px"; });
      ms.addEventListener("change", () => actions.setSize(Number(ms.value)));
    }
    const log = el.querySelector("#talklog"); if (log) log.scrollTop = 1e9;
  }

  async function act(name) {
    const a = api();
    if (name === "close") return close();
    if(name==="calmday") {await actions.quietToday();return close();}
    if(name==="room") { await actions.loadHome(); return show("home"); }
    if(name==="enterroom") { await close();return actions.enterHome(); }
    if(name==="exitroom") { await close();return actions.exitScene(); }
    if(name==="routine-default") {
      const r=await actions.changeHome({routines:{enabled:true,entries:[{time:"07:00",room:"bedroom",activity:"wake"},{time:"07:05",room:"kitchen",activity:"cook"},{time:"09:00",room:"reading",activity:"read"},{time:"22:00",room:"bedroom",activity:"sleep"}]}});
      st.homeError=r?.ok?"Routine saved.":r?.reason||"Could not save.";return render();
    }
    if (name === "back") return show(view === "skill" ? "skills" : "main");
    if (["talk", "play", "settings", "habitat", "antics"].includes(name)) {
      if (name === "play") loadGames();
      return show(name);
    }
    if (name === "skills") { loadSkills(); return show("skills"); }
    if (name === "wardrobe") { try { a && a.open_wardrobe && a.open_wardrobe(); } catch {} actions.bubble("Your wardrobe, sir."); return close(); }
    if (name === "customize") { try { a && a.open_studio && a.open_studio(); } catch {} actions.bubble("To the studio."); return close(); }
    if (name === "panel") { try { a && a.summon_panel && a.summon_panel(); } catch {} return close(); }
    if (name === "listen") { actions.listen(); st.talk.push({ who: "jarvis", text: "Listening — go ahead." }); return render(); }
    if (name === "run") return runSelected();
    if (["catchtoy","juggletoy","kicktoy","stoptoy"].includes(name) || name === "ball" || name === "dance" || name === "tossme" || name === "jetpack" || name === "surprise") { await close(); return actions.play(name); }
    if (name === "yield") { await close(); return actions.yieldClicks(30); }
    if (name === "dock") { await close(); return actions.dock(true); }
    if (name === "undock") { await close(); return actions.dock(false); }
    if (name === "home") { await close(); return actions.home(); }
  }

  async function loadSkills(force = false) {
    if (st.skills && !force) return;
    const a = api();
    try { st.prefs = (a && a.skill_prefs && (await a.skill_prefs())) || st.prefs; } catch {}
    const r = await call("GET", "/api/skills");
    if (r.ok && r.data && Array.isArray(r.data.skills)) { st.skills = r.data.skills; st.cats = r.data.categories || []; }
    else st.skills = { error: (r.data && r.data.detail) || "Couldn't read my skills just now." };
    if (open && (view === "skills" || view === "skill")) render();
  }

  async function loadGames() {
    const a = api();
    try { st.games = a && a.games ? await a.games() : null; } catch { st.games = null; }
    if (open && view === "play") render();
  }

  async function savePrefs() { const a = api(); try { a && a.save_skill_prefs && (await a.save_skill_prefs(st.prefs)); } catch {} }
  function toggleFav(name) {
    const f = st.prefs.favorites;
    st.prefs.favorites = f.includes(name) ? f.filter((n) => n !== name) : [name, ...f].slice(0, 40);
    savePrefs(); render();
  }

  function readArgs(s) {
    const args = {}, missing = [];
    for (const p of s.params || []) {
      const node = el.querySelector(`#arg-${CSS.escape(p.name)}`);
      if (!node) continue;
      let v;
      if (p.type === "boolean") v = node.checked;
      else {
        const raw = String(node.value || "").trim();
        if (!raw) { if (p.required) missing.push(p.name); continue; }
        if (p.type === "integer") v = parseInt(raw, 10);
        else if (p.type === "number") v = parseFloat(raw);
        else if (p.type === "object" || (p.type === "array" && p.items_type === "object")) { try { v = JSON.parse(raw); } catch { missing.push(p.name + " (JSON)"); continue; } }
        else if (p.type === "array") v = raw.startsWith("[") ? (() => { try { return JSON.parse(raw); } catch { return raw.split(",").map((x) => x.trim()).filter(Boolean); } })() : raw.split(",").map((x) => x.trim()).filter(Boolean);
        else v = raw;
      }
      args[p.name] = v;
    }
    return { args, missing };
  }

  async function runSelected() {
    const s = st.detail;
    if (!s || busy) return;
    const { args, missing } = readArgs(s);
    if (missing.length) { st.result = { ok: false, text: "I still need: " + missing.join(", ") + ".", name: s.name, seconds: 0 }; return render(); }
    busy = true; const run = ++st.runs;
    st.result = { running: true, title: s.title, elapsed: 0 };
    render();
    const t0 = performance.now();
    const tick = setInterval(() => { const n = el.querySelector("#elapsed"); if (n) n.textContent = ((performance.now() - t0) / 1000).toFixed(0) + "s"; }, 250);
    let out;
    try { out = await actions.runSkill(s, args); } finally { clearInterval(tick); busy = false; }
    if (run !== st.runs) return;
    st.prefs.recent = [s.name, ...st.prefs.recent.filter((n) => n !== s.name)].slice(0, 12); savePrefs();
    st.result = { ...out, name: s.name, explaining: !!(out.ok && out.text) };
    if (open && view === "skill") render();
    if (out.ok && out.text) {                       // he explains what he found, out loud (mouth follows the audio)
      const ex = await actions.explain(s, out.text);
      st.result = { ...st.result, explaining: false, explained: ex || null };
      if (open && view === "skill" && st.detail === s) render();
    }
  }

  async function ask(text) {
    text = String(text || "").trim();
    if (!text || busy) return;
    busy = true; st.talk.push({ who: "you", text }); render();
    const r = await actions.ask(text);
    busy = false;
    st.talk.push(r.ok ? { who: "jarvis", text: r.reply || "…" } : { who: "jarvis", text: r.reply || "I couldn't reach my own brain just then.", err: true });
    if (open && view === "talk") { render(); const t = el.querySelector("#talktext"); t && t.focus(); }
  }

  // Keyboard: Esc goes back / closes, arrows move between the tiles. On the document, not the menu element: a
  // re-render replaces the focused control, and its keys would otherwise never reach the menu.
  document.addEventListener("keydown", (e) => {
    if (!open) return;
    if (e.key === "Escape") { e.preventDefault(); if (view && view !== "main") show(view === "skill" ? "skills" : "main"); else close(); return; }
    const tiles = [...el.querySelectorAll(".tile")];
    const i = tiles.indexOf(document.activeElement);
    if (i < 0) return;
    const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 3, ArrowUp: -3 }[e.key];
    if (step) { e.preventDefault(); tiles[(i + step + tiles.length) % tiles.length].focus(); }
  });
  // The user went back to their work: the main menu gets out of the way; a view in use stays a while.
  window.addEventListener("blur", () => {
    if (!open) return;
    clearTimeout(blurTimer);
    blurTimer = setTimeout(() => { if (open && !busy && !document.hasFocus()) close(); }, view === "main" ? 500 : 45000);
  });
  window.addEventListener("focus", () => clearTimeout(blurTimer));

  /** Screen positions of the menu's VISIBLE controls (diagnostics: lets a test click a real button by name). */
  function rects() {
    if (!open) return [];
    const box = el.getBoundingClientRect();
    const name = (b) => b.dataset.habitat ? "habitat:" + b.dataset.habitat : b.dataset.homeAction ? "home:" + b.dataset.homeAction : b.dataset.act ? b.dataset.act : b.dataset.skill ? "skill:" + b.dataset.skill
      : b.dataset.game ? "game:" + b.dataset.game : b.dataset.filter ? "filter:" + b.dataset.filter : b.id === "sq" ? "search" : "?";
    return [...el.querySelectorAll("[data-act], [data-habitat], [data-home-action], [data-skill], [data-game], [data-filter], #sq")].filter((b) => {
      const r = b.getBoundingClientRect(), clip = (b.closest(".scroll, .chips") || el).getBoundingClientRect();
      const y = r.top + r.height / 2, x = r.left + r.width / 2;
      return r.width > 0 && y > Math.max(box.top, clip.top) && y < Math.min(box.bottom, clip.bottom) && x > box.left && x < box.right;
    }).slice(0, 80).map((b) => {
      const r = b.getBoundingClientRect();
      return { id: name(b), x: Math.round(window.screenX + r.left + r.width / 2), y: Math.round(window.screenY + r.top + r.height / 2) };
    });
  }

  return { open: openMenu, close, toggle, show, rects, loadSkills,
    get isOpen() { return open; }, get view() { return view; }, get layout() { return layout; }, get busy() { return busy; } };
}
