/* Kitchen v2, "the Training Table" (docs/KITCHEN-V2.md). Vanilla JS, no framework.
   Data: GET /api/kitchen/v2 with the paired phone's Bearer token. kitchen.html?demo=1 (and only that) reads
   /kitchen-demo.json instead and never writes to the server. An older server without /v2 falls back to /api/kitchen. */
"use strict";

const $ = s => document.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
if (location.hash.startsWith("#t=")) { try { localStorage.setItem("jarvis_token", decodeURIComponent(location.hash.slice(3))); } catch (e) {} history.replaceState(null, "", location.pathname + location.search); }
let token = null; try { token = localStorage.getItem("jarvis_token"); } catch (e) {}
const DEMO = new URLSearchParams(location.search).get("demo") === "1";
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const slug = n => String(n || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
const count = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;
const money = n => (n == null || !isFinite(+n)) ? "" : "$" + (+n).toFixed(+n >= 100 ? 0 : 2);
const int = n => Math.round(+n || 0).toLocaleString("en-US");
const cap = s => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);
const isoDay = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };

let K = null;          // the v2 payload
const ui = { tab: "today", day: null, snapKind: "auto", pq: "", sheet: null, opener: null, cook: null, wake: null, snap: null };

// "2 cups", "1.5 cups", "1/3 cup", "1 1/2 lb", "3" -> summed per unit: ["2 cups","1.5 cups"] -> "3½ cups"
function totalQty(list) {
  const num = s => s.split(/\s+/).reduce((a, p) => a + (p.includes("/") ? (+p.split("/")[0]) / (+p.split("/")[1]) : +p), 0);
  const sums = new Map(), rest = [];
  for (const q of list || []) {
    const m = String(q).trim().match(/^(\d+\/\d+|\d+(?:\.\d+)?(?:\s+\d+\/\d+)?)\s*(.*)$/);
    const n = m && num(m[1]);
    if (!m || !isFinite(n)) { rest.push(String(q)); continue; }
    const unit = m[2].toLowerCase().replace(/(ch|sh|s|x)es$/, "$1").replace(/(?<![su])s$/, "");
    sums.set(unit, (sums.get(unit) || 0) + n);
  }
  const FR = [[0, ""], [.25, "¼"], [1 / 3, "⅓"], [.5, "½"], [2 / 3, "⅔"], [.75, "¾"], [1, ""]];
  const fmt = n => { const w = Math.floor(n), f = n - w; const [v, s] = FR.reduce((b, c) => Math.abs(c[0] - f) < Math.abs(b[0] - f) ? c : b);
    return Math.abs(v - f) < .04 ? (v === 1 ? String(w + 1) : (w || !s ? String(w) : "") + s) : String(+n.toFixed(2)); };
  const plural = (u, n) => !u || n <= 1 || /^(lb|oz|g|kg|ml|l|tsp|tbsp)$/.test(u) ? u : /(ch|sh|x)$/.test(u) ? u + "es" : u + "s";
  return [...[...sums].map(([u, n]) => `${fmt(n)}${u ? " " + plural(u, n) : ""}`), ...rest].join(" + ");
}

/* ---------- icons (Lucide, ISC) ---------- */
const ICON = (p, cls = "") => `<svg class="ds-icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${p}</svg>`;
const I = {
  back: ICON('<path d="M15 6l-6 6 6 6"/>'), x: ICON('<path d="M18 6 6 18M6 6l12 12"/>'), check: ICON('<path d="M20 6 9 17l-5-5"/>'),
  plus: ICON('<path d="M5 12h14M12 5v14"/>'), chevR: ICON('<path d="m9 18 6-6-6-6"/>'), chevL: ICON('<path d="m15 18-6-6 6-6"/>'),
  cam: ICON('<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>'),
  image: ICON('<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>'),
  clock: ICON('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
  flame: ICON('<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>'),
  dumbbell: ICON('<path d="M14.4 14.4 9.6 9.6"/><path d="M18.66 21.49a2 2 0 1 1-2.83-2.83l-1.77 1.77a2 2 0 1 1-2.83-2.83l6.36-6.36a2 2 0 1 1 2.83 2.83l-1.77 1.77a2 2 0 1 1 2.83 2.83z"/><path d="m21.5 21.5-1.4-1.4"/><path d="M3.9 3.9 2.5 2.5"/><path d="M6.4 12.77a2 2 0 1 1-2.83-2.83l1.77-1.77a2 2 0 1 1-2.83-2.83l2.83-2.83a2 2 0 1 1 2.83 2.83l1.77-1.77a2 2 0 1 1 2.83 2.83z"/>'),
  drop: ICON('<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>'),
  up: ICON('<path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>'),
  down: ICON('<path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/>'),
  baby: ICON('<path d="M9 12h.01M15 12h.01M10 16c.5.3 1.2.5 2 .5s1.5-.2 2-.5"/><path d="M19 6.3a9 9 0 0 1 1.8 3.9 2 2 0 0 1 0 3.6 9 9 0 0 1-17.6 0 2 2 0 0 1 0-3.6A9 9 0 0 1 12 3c2 0 3.5 1.1 3.5 2.5s-.9 2.5-2 2.5c-.8 0-1.5-.4-1.5-1"/>'),
  swap: ICON('<path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/>'),
  play: ICON('<path d="M6 3l14 9-14 9V3z"/>'),
  cart: ICON('<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>'),
  search: ICON('<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>'),
  receipt: ICON('<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17.5v-11"/>'),
  chef: ICON('<path d="M17 21a1 1 0 0 0 1-1v-5.35c0-.46.32-.84.73-1.04a4 4 0 0 0-2.13-7.59 5 5 0 0 0-9.19 0 4 4 0 0 0-2.13 7.59c.41.2.72.58.72 1.04V20a1 1 0 0 0 1 1Z"/><path d="M6 17h12"/>'),
  users: ICON('<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>'),
  scale: ICON('<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>'),
  sun: ICON('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>'),
  moon: ICON('<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>'),
  leaf: ICON('<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>'),
  layers: ICON('<path d="m12 2 10 5-10 5L2 7l10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>'),
  refresh: ICON('<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>'),
  warn: ICON('<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'),
  bowl: ICON('<path d="M3 11h18a9 9 0 0 1-18 0z"/><path d="M8 7c0-1.5 1-2 1-3.5M13 7c0-1.5 1-2 1-3.5"/>'),
};

/* ---------- toast (with an optional action) ---------- */
let toastT;
function toast(msg, kind = "", action = null) {
  const t = $("#toast");
  t.innerHTML = `<span>${esc(msg)}</span>${action ? `<button class="ds-btn sm ghost" type="button">${esc(action.label)}</button>` : ""}`;
  t.className = "ds-toast k-toast " + kind; t.hidden = false;
  if (action) t.querySelector("button").onclick = () => { t.hidden = true; action.run(); };
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, action ? 6000 : 3800);
}

/* ---------- API (same token pattern as every Jarvis page) ---------- */
const authH = () => ({ Authorization: `Bearer ${token}` });
async function readJson(r) { try { return await r.json(); } catch (e) { return {}; } }
function failure(r, j, fallback) { return r.status === 401 ? "Link this phone in the Jarvis app first." : (j && typeof j.detail === "string" && j.detail) || fallback; }
class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
async function apiGet(p) {
  const r = await fetch(p, { headers: authH() }), j = await readJson(r);
  if (!r.ok) throw new ApiError(failure(r, j, "Jarvis could not load that (error " + r.status + ")."), r.status);
  return j;
}
async function apiPost(p, body) {
  const r = await fetch(p, { method: "POST", headers: { ...authH(), "Content-Type": "application/json" }, body: JSON.stringify(body) }), j = await readJson(r);
  if (!r.ok) throw new ApiError(failure(r, j, "Jarvis could not do that (error " + r.status + ")."), r.status);
  return j;
}
async function apiForm(p, fd) {
  const r = await fetch(p, { method: "POST", headers: authH(), body: fd }), j = await readJson(r);
  if (!r.ok) throw new ApiError(failure(r, j, "Jarvis could not read that photo (error " + r.status + ")."), r.status);
  return j;
}
/* A write: in demo mode nothing leaves the page (returns null and the caller keeps its optimistic local change). */
async function write(path, body) { if (DEMO) return null; return apiPost(path, body); }
const demoNote = () => DEMO ? " (demo: not saved)" : "";
// <img> cannot send the Bearer header, so the server accepts ?t= on photo and snap URLs.
function imgURL(u) { if (!u || DEMO && u.startsWith("/api/")) return null; return u.startsWith("/api/") && token ? u + (u.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(token) : u; }

/* ---------- generated placeholder: a stylised overhead plate in the dish's own colours ---------- */
const PALETTE = [
  [/kidney|red bean|pwa\b/, "#6E2B25", "bits"], [/black bean/, "#2B2220", "sauce"], [/pigeon pea/, "#8C7A45", "bits"],
  [/rice/, "#EEE5D0", "bed"], [/cornmeal|mayi/, "#E6BF55", "bed"], [/plantain|breadfruit/, "#E6D394", "bed"],
  [/spaghetti|pasta|rigatoni|noodle/, "#E0B266", "bed"], [/potato/, "#E8D7A4", "bits"], [/eggplant/, "#4B3B30", "bed"],
  [/chicken/, "#B8703A", "pieces"], [/pork/, "#7A3E1F", "pieces"], [/beef|goat|tasso/, "#5A2D1E", "pieces"],
  [/sausage|hot dog/, "#A6533A", "pieces"], [/shrimp/, "#E8875C", "pieces"], [/fish|herring|cod|aranso|snapper/, "#D7BF98", "pieces"],
  [/\beggs?\b/, "#F3C443", "egg"], [/squash|calabaza|pumpkin|joumou/, "#E0872A", "liquid"], [/milk|yogurt|cream/, "#EFE6D6", "liquid"],
  [/coconut/, "#F1ECE1", "sauce"], [/tomato/, "#B5402D", "sauce"], [/cabbage/, "#D6E0B3", "shreds"], [/carrot/, "#E57B2C", "shreds"],
  [/scotch bonnet|bell pepper|pepper/, "#D1402E", "shreds"], [/red onion/, "#B8507C", "shreds"], [/banana/, "#EFD27E", "bits"],
  [/spinach|scallion|thyme|chayote|parsley|cilantro|callaloo|watercress|lalo/, "#4F7B34", "garnish"], [/lime|sour orange/, "#A3C24B", "garnish"],
];
const CAT_FALLBACK = { soup: ["#C9822F", "liquid"], shake: ["#E9DCC4", "liquid"], drink: ["#E9DCC4", "liquid"], dessert: ["#D9A35B", "bed"],
  sauce: ["#C8D6A0", "shreds"], side: ["#DCC07A", "bed"], snack: ["#D9B36C", "bed"], breakfast: ["#E6CD8A", "bed"], main: ["#C79A5B", "bed"] };
let phSeq = 0;
function hashStr(s) { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
function rng(seed) { let a = seed || 1; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function mix(a, b, t) { const p = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)); const A = p(a), B = p(b);
  return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, "0")).join(""); }
function blob(cx, cy, r, wob, R, n = 9) {
  const pts = Array.from({ length: n }, (_, i) => { const a = i / n * Math.PI * 2, rr = r * (1 - wob / 2 + R() * wob); return [cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]; });
  const mid = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
  let d = `M${mid(pts[n - 1], pts[0]).map(v => v.toFixed(1)).join(" ")}`;
  pts.forEach((p, i) => { const m = mid(p, pts[(i + 1) % n]); d += `Q${p[0].toFixed(1)} ${p[1].toFixed(1)} ${m[0].toFixed(1)} ${m[1].toFixed(1)}`; });
  return d + "Z";
}
function dishPalette(d) {
  const roles = {}; const ings = [...(d.ingredients || [])].sort((a, b) => (b.key ? 1 : 0) - (a.key ? 1 : 0));
  for (const ing of ings) {
    const name = String(ing.item || "").toLowerCase();
    const hit = PALETTE.find(([re]) => re.test(name));
    if (hit && !(roles[hit[2]] || []).includes(hit[1])) (roles[hit[2]] = roles[hit[2]] || []).push(hit[1]);
  }
  return roles;
}
function placeholderSVG(d) {
  const id = "kp" + (++phSeq), R = rng(hashStr(d.id || d.name || "dish")), roles = dishPalette(d);
  const cat = d.category || "main", fb = CAT_FALLBACK[cat] || CAT_FALLBACK.main;
  const first = r => (roles[r] || [])[0];
  const lead = first("pieces") || first("bed") || first("liquid") || first("sauce") || first("shreds") || fb[0];
  const table = mix(mix(lead, "#000000", .78), "#3A2A1C", .55), table2 = mix(table, "#000000", .35);
  const bowl = cat === "soup" || cat === "shake" || cat === "drink" || cat === "sauce" || (roles.liquid && !roles.bed);
  const cx = 200 + (R() - .5) * 16, cy = 150 + (R() - .5) * 8, plateR = bowl ? 98 : 112;
  let food = "";
  const grain = Array.from({ length: 7 }, (_, i) => `<path d="M-10 ${18 + i * 44 + R() * 10} C120 ${10 + i * 44 + R() * 30} 260 ${30 + i * 44 - R() * 30} 410 ${20 + i * 44 + R() * 10}" stroke="#FFFFFF" stroke-opacity=".035" stroke-width="${2 + R() * 3}" fill="none"/>`).join("");
  if (bowl) {
    const liquid = cat === "sauce" ? "#D9D9B4" : first("liquid") || first("sauce") || fb[0];
    food += `<circle cx="${cx}" cy="${cy}" r="${plateR - 14}" fill="${liquid}"/>`;
    if (cat === "sauce" && roles.shreds) for (let i = 0; i < 110; i++) {
      const col = roles.shreds[i % roles.shreds.length], a = R() * Math.PI * 2, rr = Math.sqrt(R()) * (plateR - 22), x = cx + Math.cos(a) * rr, y = cy + Math.sin(a) * rr, l = 12 + R() * 20, t = R() * Math.PI;
      food += `<path d="M${(x - Math.cos(t) * l / 2).toFixed(1)} ${(y - Math.sin(t) * l / 2).toFixed(1)} q${(R() * 8 - 4).toFixed(1)} ${(R() * 8 - 4).toFixed(1)} ${(Math.cos(t) * l).toFixed(1)} ${(Math.sin(t) * l).toFixed(1)}" stroke="${col}" stroke-width="${(2.2 + R() * 2).toFixed(1)}" stroke-linecap="round" fill="none"/>`;
    }
    food += `<circle cx="${cx - 18}" cy="${cy - 20}" r="${plateR - 40}" fill="#FFFFFF" fill-opacity=".08"/>`;
    const chunks = (cat === "sauce" || cat === "shake" || cat === "drink") ? [] : [...(roles.pieces || []), ...(roles.bits || []), ...(roles.shreds || []), ...(roles.bed || [])];
    for (let i = 0; i < (chunks.length ? 14 : 0); i++) {
      const a = R() * Math.PI * 2, rr = R() * (plateR - 34);
      food += `<path d="${blob(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr, 6 + R() * 9, .5, R, 7)}" fill="${chunks[i % chunks.length]}"/>`;
    }
    if (cat === "shake" || cat === "drink") for (let i = 0; i < 40; i++) { const a = R() * Math.PI * 2, rr = R() * 30; food += `<circle cx="${(cx + Math.cos(a) * rr).toFixed(1)}" cy="${(cy + Math.sin(a) * rr).toFixed(1)}" r="${(.8 + R() * 1.4).toFixed(1)}" fill="#7A4A25" fill-opacity=".7"/>`; }
  } else {
    let bed = first("bed"), sauceBed = false;
    if (bed && roles.bits) bed = mix(bed, roles.bits[0], .45);
    if (!bed && roles.sauce) { bed = roles.sauce[0]; sauceBed = true; }
    if (!bed) bed = fb[0];
    if (sauceBed) food += `<path d="${blob(cx, cy + 2, 84, .18, R, 12)}" fill="${bed}" filter="url(#${id}s)"/><path d="${blob(cx - 22, cy - 14, 50, .3, R)}" fill="#FFFFFF" fill-opacity=".1"/>`;
    else food += `<path d="${blob(cx - 14, cy + 4, 70, .22, R, 11)}" fill="${bed}"/><path d="${blob(cx - 26, cy - 8, 44, .3, R)}" fill="#FFFFFF" fill-opacity=".09"/>`;
    if (roles.sauce && !sauceBed) food += `<path d="${blob(cx + 30, cy + 8, 46, .3, R)}" fill="${roles.sauce[0]}" fill-opacity=".92" filter="url(#${id}s)"/>`;
    if (roles.shreds) for (let i = 0; i < 12; i++) {
      const col = roles.shreds[i % roles.shreds.length], a = R() * Math.PI * 2, rr = R() * 30;
      const x = cx + 46 + Math.cos(a) * rr, y = cy - 40 + Math.sin(a) * rr * .9, l = 10 + R() * 14, t = R() * Math.PI;
      food += `<path d="M${(x - Math.cos(t) * l / 2).toFixed(1)} ${(y - Math.sin(t) * l / 2).toFixed(1)} q${(R() * 8 - 4).toFixed(1)} ${(R() * 8 - 4).toFixed(1)} ${(Math.cos(t) * l).toFixed(1)} ${(Math.sin(t) * l).toFixed(1)}" stroke="${col}" stroke-width="${(2 + R() * 2).toFixed(1)}" stroke-linecap="round" fill="none"/>`;
    }
    if (roles.bits) for (let i = 0; i < 22; i++) { const a = R() * Math.PI * 2, rr = R() * 58; food += `<ellipse cx="${(cx - 14 + Math.cos(a) * rr).toFixed(1)}" cy="${(cy + 4 + Math.sin(a) * rr).toFixed(1)}" rx="${(3 + R() * 2).toFixed(1)}" ry="${(2 + R() * 1.5).toFixed(1)}" transform="rotate(${(R() * 180).toFixed(0)} ${(cx - 14 + Math.cos(a) * rr).toFixed(1)} ${(cy + 4 + Math.sin(a) * rr).toFixed(1)})" fill="${roles.bits[i % roles.bits.length]}"/>`; }
    if (roles.pieces) {
      const n = 4 + Math.floor(R() * 3);
      for (let i = 0; i < n; i++) {
        const col = roles.pieces[i % roles.pieces.length], a = -0.6 + i / n * 2.2 + R() * .3, rr = 36 + R() * 26;
        const x = cx + 26 + Math.cos(a) * rr * .8, y = cy - 6 + Math.sin(a) * rr, r = 15 + R() * 9;
        food += `<path d="${blob(x + 2, y + 3, r, .35, R)}" fill="#000000" fill-opacity=".25"/><path d="${blob(x, y, r, .35, R)}" fill="${col}"/><path d="${blob(x - r * .25, y - r * .3, r * .45, .4, R)}" fill="#FFFFFF" fill-opacity=".14"/>`;
      }
    }
    if (roles.egg) for (let i = 0; i < 2; i++) { const x = cx + 40 + i * 22 - 10, y = cy - 40 + i * 52;
      food += `<path d="${blob(x, y, 30, .3, R)}" fill="#FBF7EE"/><circle cx="${x + 3}" cy="${y - 2}" r="11" fill="${roles.egg[0]}"/><circle cx="${x}" cy="${y - 5}" r="3.5" fill="#FFFFFF" fill-opacity=".5"/>`; }
  }
  if (roles.garnish) for (let i = 0; i < 12; i++) {
    const a = R() * Math.PI * 2, rr = R() * (plateR - 30), x = cx + Math.cos(a) * rr, y = cy + Math.sin(a) * rr;
    food += `<ellipse cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" rx="${(3 + R() * 3).toFixed(1)}" ry="1.8" transform="rotate(${(R() * 180).toFixed(0)} ${x.toFixed(1)} ${y.toFixed(1)})" fill="${roles.garnish[i % roles.garnish.length]}"/>`;
  }
  const rim = bowl ? "#E2DCD0" : "#E9E3D8", well = bowl ? "#D3CBBD" : "#F1ECE3";
  return `<svg viewBox="0 0 400 300" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
<defs><linearGradient id="${id}t" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${table}"/><stop offset="1" stop-color="${table2}"/></linearGradient>
<radialGradient id="${id}l" cx=".25" cy=".15" r=".9"><stop offset="0" stop-color="#FFF6E8" stop-opacity=".22"/><stop offset=".55" stop-color="#FFF6E8" stop-opacity="0"/><stop offset="1" stop-color="#000000" stop-opacity=".35"/></radialGradient>
<filter id="${id}b" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="10"/></filter>
<filter id="${id}s" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="2.4"/></filter>
<clipPath id="${id}c"><circle cx="${cx}" cy="${cy}" r="${plateR - (bowl ? 14 : 8)}"/></clipPath></defs>
<rect width="400" height="300" fill="url(#${id}t)"/>${grain}
<circle cx="${cx + 10}" cy="${cy + 14}" r="${plateR}" fill="#000000" fill-opacity=".45" filter="url(#${id}b)"/>
<circle cx="${cx}" cy="${cy}" r="${plateR}" fill="${rim}"/>${bowl ? "" : `<circle cx="${cx}" cy="${cy}" r="${plateR - 3}" fill="none" stroke="#2F4E6E" stroke-opacity=".55" stroke-width="2.2"/>`}
<circle cx="${cx}" cy="${cy}" r="${plateR - (bowl ? 10 : 20)}" fill="${well}"/>
<g clip-path="url(#${id}c)">${food}</g>
<rect width="400" height="300" fill="url(#${id}l)"/></svg>`;
}
/* Photo frame: the generated plate shows at once; the real photo fades in over it when it loads. */
function photo(d, cls = "", alt = "") {
  const src = d && imgURL(d.photo);
  return `<div class="k-photo ${cls}"${src ? ` data-src="${esc(src)}" data-alt="${esc(alt)}"` : ""}>${placeholderSVG(d || { id: "x", name: "dish" })}</div>`;
}
function hydrate(root = document) {
  $$(".k-photo[data-src]", root).forEach(el => {
    const img = new Image(); img.decoding = "async"; img.loading = "lazy"; img.alt = el.dataset.alt || "";
    img.onload = () => img.classList.add("ok"); img.onerror = () => img.remove();
    img.src = el.dataset.src; el.removeAttribute("data-src"); el.appendChild(img);
  });
}

/* ---------- data helpers ---------- */
const D = id => (K && K.dishes && K.dishes[id]) || null;
const M = d => (d && d.macros) || {};
const SLOT_ORDER = { breakfast: 0, "pre-workout": 2, lunch: 1, snack: 2, drink: 2, "post-workout": 3, dinner: 4 };
const toMin = t => { const m = String(t || "").match(/(\d{1,2}):(\d{2})\s*(am|pm)?/i); if (!m) return 9999; let h = +m[1]; if (m[3]) { h %= 12; if (/pm/i.test(m[3])) h += 12; } return h * 60 + +m[2]; };
function guessSlot() { const h = new Date().getHours(); return h < 10 ? "breakfast" : h < 15 ? "lunch" : h < 17 ? "snack" : "dinner"; }
const STAPLE = /\b(epis|salt|oil|garlic|onion|lime|thyme|scallion|bouillon|black pepper|cloves?|scotch bonnet|vinegar|water)\b/;
const toks = s => String(s || "").toLowerCase().replace(/[^a-z ]+/g, " ").split(/\s+/).filter(w => w.length > 2 && !/^(and|the|with|fresh|dried|whole|large|small|can|cans|cup|cups|boneless|bone|skinless)$/.test(w)).map(w => w.replace(/(ies)$/, "y").replace(/(oes|es)$/, "o").replace(/s$/, ""));
const GENERIC = new Set(["rice", "egg", "milk", "onion", "garlic", "lime", "carrot", "cabbage", "banana", "plantain", "spaghetti", "cornmeal", "bean", "yogurt", "potato", "tomato", "pea"]);
function inPantry(item) {
  const it = toks(item);
  return (K.pantry || []).some(p => { const pt = toks(p.name); if (!pt.length || !it.length) return false;
    if (pt.every(w => it.includes(w)) || it.every(w => pt.includes(w))) return true;
    const head = it[it.length - 1]; return GENERIC.has(head) && pt[pt.length - 1] === head; });
}
function ingStatus(d, ing) {
  const cn = (K.cook_now || []).find(c => c.dish === d.id);
  const name = String(ing.item || "").toLowerCase();
  if (cn) { if ((cn.missing || []).some(m => m.toLowerCase() === name)) return "need"; if ((cn.have || []).some(m => m.toLowerCase() === name)) return "have"; }
  if (inPantry(ing.item)) return "have";
  if (!ing.key && STAPLE.test(name)) return "staple";
  return "need";
}
function weekKey() { return (K.week && K.week[0] && K.week[0].date) || isoDay(); }
function extraShopping() { return store.get("jarvis_kitchen_extra_" + weekKey(), []); }

/* ---------- shared bits ---------- */
const dayTypeChip = (type, label) => `<span class="k-daychip dt-${esc(type || "rest")}"><i></i><b>${esc(type || "Day")}</b>${label ? `<span class="k-sub">${esc(label)}</span>` : ""}</span>`;
const empty = (title, text, action = "", icon = I.bowl) => `<div class="ds-card"><div class="ds-empty">${icon}<b>${esc(title)}</b><span>${esc(text)}</span>${action}</div></div>`;
const busy = label => `<div class="k-busy"><span class="ds-spinner"></span>${esc(label)}</div>`;
function metaLine(d, extra = "") {
  const m = M(d), bits = [];
  if (d.total_min) bits.push(`<span>${I.clock}${esc(d.total_min)} min</span>`);
  if (m.protein_g != null) bits.push(`<span><b>${esc(m.protein_g)} g</b> protein</span>`);
  if (m.kcal != null) bits.push(`<span><b>${int(m.kcal)}</b> kcal</span>`);
  if (d.cost_per_serving_usd != null) bits.push(`<span>${esc(money(d.cost_per_serving_usd))}/serving</span>`);
  return `<div class="k-meta">${bits.join("")}${extra}</div>`;
}
function tasteBadge(d) { return d.taste === "like" ? `<span class="k-pill accent">${I.up}Liked</span>` : ""; }
/* Large card (Today): photo 3:2, overlay time + state, body with name, macros and the eat action. */
function bigCard(meal) {
  const d = D(meal.dish); if (!d) return "";
  const slot = meal.slot || "", done = !!meal.done;
  return `<article class="k-card ${done ? "done" : ""}" data-slot="${esc(slot)}">
    <button class="k-hit" type="button" data-open="${esc(d.id)}" aria-label="Open ${esc(d.name)}"></button>
    ${photo(d, "r32")}
    <div class="k-over tl"><span class="k-pill">${esc(clock12(meal.time))}${meal.time ? " · " : ""}${esc(cap(slot.replace("-", " ")))}</span></div>
    <div class="k-over tr">${done ? `<span class="k-pill ok">${I.check}Eaten</span>` : tasteBadge(d)}</div>
    <div class="k-card-body">
      <div><div class="k-card-name">${esc(d.name)}</div><div class="k-card-en">${esc(d.english || "")}</div></div>
      <div class="k-card-foot">${metaLine(d)}
        <button class="ds-btn sm k-eat k-above" type="button" data-eat="${esc(slot)}" data-dish="${esc(d.id)}" aria-pressed="${done}" ${done ? "disabled" : ""}>
          ${done ? I.check + "Eaten" : I.check + "Mark eaten"}</button></div>
    </div></article>`;
}
/* Compact card (rails and grids): photo 4:3, name, have/missing line. */
function smallCard(d, opts = {}) {
  if (!d) return "";
  const have = opts.have, missing = opts.missing;
  const total = have && missing ? have.length + missing.length : 0;
  const pct = total ? Math.round(have.length / total * 100) : null;
  return `<article class="k-card c">
    <button class="k-hit" type="button" data-open="${esc(d.id)}" aria-label="Open ${esc(d.name)}"></button>
    ${photo(d)}
    <div class="k-over tl">${opts.badge || ""}</div><div class="k-over tr">${tasteBadge(d)}</div>
    <div class="k-card-body">
      <div class="k-card-name">${esc(d.name)}</div>
      ${metaLine(d)}
      ${pct != null ? `<div class="k-have"><div class="k-track"><i style="width:${pct}%;--c:var(--ok)"></i></div>
        <small>${missing.length ? `Need ${esc(missing.slice(0, 3).join(", "))}${missing.length > 3 ? "…" : ""}` : "You have everything"}</small></div>` : ""}
      ${opts.note ? `<small class="k-sub">${esc(opts.note)}</small>` : ""}
    </div></article>`;
}
function bindCards(root) {
  $$("[data-open]", root).forEach(b => b.onclick = () => openDish(b.dataset.open, b));
  $$("[data-eat]", root).forEach(b => b.onclick = () => markEaten(b.dataset.dish, b.dataset.eat));
  hydrate(root);
}

/* ---------- TODAY ---------- */
function ring(eaten, target) {
  const C = 2 * Math.PI * 64, frac = target ? Math.min(eaten / target, 1) : 0;
  const left = Math.round((target || 0) - eaten);
  return `<div class="k-ring ${eaten > target * 1.05 ? "over" : ""}" role="img" aria-label="${int(eaten)} of ${int(target)} kcal eaten">
    <svg viewBox="0 0 148 148"><circle class="trk" cx="74" cy="74" r="64"/><circle class="val" cx="74" cy="74" r="64" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${C.toFixed(1)}" data-off="${(C * (1 - frac)).toFixed(1)}"/></svg>
    <div class="k-ring-c"><b>${int(Math.abs(left))}</b><span>${left >= 0 ? "kcal left" : "kcal over"}</span><span>of ${int(target)}</span></div></div>`;
}
function bar(label, v, t, color, unit = "g") {
  const pct = t ? Math.min(v / t * 100, 100) : 0;
  return `<div class="k-bar"><div class="k-bar-top"><span>${esc(label)}</span><b>${int(v)}<small> / ${int(t)} ${unit}</small></b></div>
    <div class="k-track ${v > t * 1.1 ? "over" : ""}" role="meter" aria-label="${esc(label)}" aria-valuemin="0" aria-valuemax="${Math.round(t)}" aria-valuenow="${Math.round(v)}"><i data-w="${pct.toFixed(1)}" style="--c:${color}"></i></div></div>`;
}
function animateMeters(root) {
  const go = () => { $$(".k-ring .val", root).forEach(c => c.style.strokeDashoffset = c.dataset.off); $$(".k-track i[data-w]", root).forEach(i => i.style.width = i.dataset.w + "%"); };
  REDUCED ? go() : requestAnimationFrame(() => requestAnimationFrame(go));
}
function waterRow(me) {
  const t = (me.targets && me.targets.water_l) || 3.5, v = (me.eaten && me.eaten.water_l) || 0, n = Math.max(6, Math.min(14, Math.round(t / .25 / 2)));
  const per = t / n, cells = Array.from({ length: n }, (_, i) => { const f = (v - i * per) / per; return `<i class="${f >= 1 ? "on" : f >= .5 ? "half" : ""}"></i>`; }).join("");
  return `<div class="k-water">${I.drop}<div class="k-drops" role="meter" aria-label="Water" aria-valuemin="0" aria-valuemax="${t}" aria-valuenow="${v}">${cells}</div>
    <span class="k-water-l">${v.toFixed(1)}<small> / ${t} L</small></span>
    <button class="ds-iconbtn sm" type="button" id="waterAdd" aria-label="Add a glass of water, 250 ml">${I.plus}</button></div>`;
}
function setupCard(where) {
  return `<div class="k-panel" id="setup-${where}"><div class="k-row">${I.scale}<b class="grow">Let's set your targets</b></div>
    <p class="k-sub">Tell the nutritionist your weight and he works out calories, protein and water for muscle and speed.</p>
    <form class="k-row" data-weight-form><label class="k-sr" for="w-${where}">Weight in pounds</label>
      <input class="ds-input grow" id="w-${where}" type="number" inputmode="decimal" min="70" max="500" step="0.1" placeholder="Weight (lb)" required>
      <button class="ds-btn primary" type="submit">Save</button></form></div>`;
}
function renderToday() {
  const s = $("#today"), me = K.me || {}, t = me.targets, e = me.eaten || {};
  const meals = [...((K.today && K.today.meals) || [])].sort((a, b) => toMin(a.time) - toMin(b.time) || (SLOT_ORDER[a.slot] ?? 5) - (SLOT_ORDER[b.slot] ?? 5));
  const date = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  const fuel = t && me.set_up !== false ? `<div class="k-fuel">${ring(e.kcal || 0, t.kcal || 0)}
      <div class="k-bars">${bar("Protein", e.protein_g || 0, t.protein_g || 0, "var(--k-protein)")}${bar("Carbs", e.carbs_g || 0, t.carbs_g || 0, "var(--k-carbs)")}${bar("Fat", e.fat_g || 0, t.fat_g || 0, "var(--k-fat)")}</div>
      ${waterRow(me)}</div>` : setupCard("today");
  const coach = K.today && K.today.coach_note ? `<div class="k-coach dt-${esc(me.day_type || "rest")}">${I.dumbbell}<div><div class="k-eyebrow">From your trainer</div><p>${esc(K.today.coach_note)}</p></div></div>` : "";
  const cook = (K.cook_now || []).filter(c => D(c.dish) && D(c.dish).taste !== "dislike");
  s.innerHTML = `<div class="k-today-grid">
    <div class="k-col">
      <div class="k-hero"><span class="k-eyebrow">${esc(date)}</span><h2>Training Table</h2>${me.day_type ? dayTypeChip(me.day_type, me.day_label) : ""}</div>
      ${fuel}${coach}
    </div>
    <div class="k-block" style="gap:var(--s-6)">
      <div class="k-block"><div class="k-head"><h2>Today's plates</h2><span class="k-aside">${meals.filter(m => m.done).length} of ${meals.length} eaten</span></div>
        ${meals.length ? `<div class="k-plates">${meals.map(bigCard).join("")}</div>` : empty("No meals planned for today", "Open Week and tap Replan, or ask Jarvis to plan the week.")}</div>
      <div class="k-block"><div class="k-head"><h2>Cook now</h2><span class="k-aside">From what's in your pantry</span></div>
        ${cook.length ? `<div class="k-rail">${cook.map(c => smallCard(D(c.dish), { have: c.have || [], missing: c.missing || [] })).join("")}</div>`
          : `<p class="k-sub">Nothing matches the pantry yet. Snap your groceries and the chef will find meals.</p>`}</div>
    </div></div>`;
  bindCards(s); animateMeters(s); bindWeightForms(s);
  const w = $("#waterAdd"); if (w) w.onclick = addWater;
}
function clock12(t) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(t || ""); if (!m) return t || "";
  const h = +m[1]; return `${h % 12 || 12}:${m[2]} ${h < 12 ? "AM" : "PM"}`.replace(":00 ", " ");
}
async function markEaten(id, slot) {
  const d = D(id); if (!d) return;
  const meal = ((K.today && K.today.meals) || []).find(m => m.dish === id && m.slot === slot);
  if (meal && meal.done) return unEat(d, meal);
  return logMeal(d, meal, slot);
}
async function logMeal(d, meal, slot) {
  const id = d.id; slot = slot || (meal && meal.slot) || "";
  const before = { ...(K.me.eaten || {}) }, m = M(d), e = K.me.eaten = { ...before };
  for (const k of ["kcal", "protein_g", "carbs_g", "fat_g"]) e[k] = (e[k] || 0) + (m[k] || 0);
  if (meal) meal.done = true;
  renderToday();
  try {
    const r = await write("/api/kitchen/eat", { dish: id, slot });
    if (r && r.eaten) K.me.eaten = r.eaten;
    toast(`${d.name} logged: +${int(m.kcal)} kcal, +${m.protein_g || 0} g protein${demoNote()}`, "ok");
  } catch (err) { K.me.eaten = before; if (meal) meal.done = false; toast("Not logged: " + err.message, "danger"); }
  renderToday();
}
async function unEat(d, meal) {
  const before = { ...(K.me.eaten || {}) }, m = M(d), e = K.me.eaten = { ...before };
  for (const k of ["kcal", "protein_g", "carbs_g", "fat_g"]) e[k] = Math.max(0, (e[k] || 0) - (m[k] || 0));
  meal.done = false; renderToday();
  try {
    const r = await write("/api/kitchen/uneat", { dish: d.id, slot: meal.slot });
    if (r && r.eaten) K.me.eaten = r.eaten;
    toast(`${d.name} taken off today's log${demoNote()}`);
  } catch (err) { K.me.eaten = before; meal.done = true; toast("Not changed: " + err.message, "danger"); }
  renderToday();
}
async function addWater() {
  const e = K.me.eaten = K.me.eaten || {}, before = e.water_l || 0;
  e.water_l = Math.round((before + .25) * 100) / 100; renderToday();
  try { const r = await write("/api/kitchen/water", { liters: .25 }); if (r && r.water_l != null) e.water_l = r.water_l; toast(`Water: ${e.water_l.toFixed(2)} L${demoNote()}`); }
  catch (err) { e.water_l = before; toast("Water not saved: " + err.message, "danger"); }
  renderToday();
}
function bindWeightForms(root) {
  $$("form[data-weight-form]", root).forEach(f => f.onsubmit = async ev => {
    ev.preventDefault(); const lbs = parseFloat(f.querySelector("input").value);
    if (!(lbs >= 70 && lbs <= 500)) { toast("Enter a weight in pounds, between 70 and 500."); return; }
    const btn = f.querySelector("button"); btn.disabled = true;
    try {
      const r = await write("/api/kitchen/weight", { lbs });
      const me = K.me; me.weight_lbs = lbs;
      me.weighins = [...(me.weighins || []).filter(w => w.date !== isoDay()), (r && r.weighin) || { date: isoDay(), lbs }];
      if (r && r.targets) { me.targets = r.targets; me.set_up = true; }
      toast(`Weigh-in saved: ${lbs} lb${demoNote()}`, "ok"); renderToday(); renderYou();
    } catch (err) { btn.disabled = false; toast("Not saved: " + err.message, "danger"); }
  });
}

/* ---------- SNAP ---------- */
const KINDS = [["auto", "Auto"], ["groceries", "Groceries"], ["receipt", "Receipt"], ["plate", "Plate"], ["label", "Label"]];
function renderSnap() {
  const s = $("#snap");
  s.innerHTML = `<div class="k-block">
      <div class="k-hero"><span class="k-eyebrow">The chef's eyes</span><h2 class="k-h2" style="font-size:var(--fs-2xl);line-height:1.1">Snap what you've got</h2>
        <p class="k-sub">Groceries on the counter, a receipt, a plate, a label. The chef reads it and tells you what to cook tonight.</p></div>
      <div class="k-kinds" role="group" aria-label="What is in the photo">${KINDS.map(([k, l]) => `<button type="button" class="ds-chip" data-kind="${k}" aria-pressed="${ui.snapKind === k}">${l}</button>`).join("")}</div>
      <div id="snapStage">${snapTarget()}</div>
      <div id="snapOut" aria-live="polite"></div>
    </div>
    <div class="k-block"><div class="k-head"><h2>Recent snaps</h2></div>
      ${(K.snaps || []).length ? `<div class="k-snaps">${K.snaps.map(sn => `<figure><div class="k-photo r11 k-snap-ph"${imgURL(sn.thumb) ? ` data-src="${esc(imgURL(sn.thumb))}" data-alt="${esc(cap(sn.kind))} photo"` : ""}>${snapGlyph(sn.kind)}</div>
        <figcaption>${esc(sn.kind)} · ${esc(when(sn.at))}</figcaption></figure>`).join("")}</div>` : `<p class="k-sub">Your photos show up here.</p>`}</div>`;
  $$("[data-kind]", s).forEach(b => b.onclick = () => { ui.snapKind = b.dataset.kind; $$("[data-kind]", s).forEach(x => x.setAttribute("aria-pressed", x === b)); });
  bindSnapInputs(s); hydrate(s);
  if (ui.snap) showSnapResult(ui.snap);
}
function snapGlyph(kind) {
  const ic = { receipt: I.receipt, plate: I.bowl, label: I.layers }[kind] || I.cam;
  return `<div style="position:absolute;inset:0;display:grid;place-items:center;color:var(--text-3)">${ic}</div>`;
}
function when(at) {
  const d = new Date(at); if (isNaN(d)) return "";
  const days = Math.round((new Date(isoDay()) - new Date(isoDay(d))) / 864e5);
  return days <= 0 ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : days === 1 ? "yesterday" : d.toLocaleDateString([], { month: "short", day: "numeric" });
}
const snapTarget = () => `<label class="k-drop" id="snapDrop">
    <input type="file" id="snapCam" accept="image/*" capture="environment" aria-label="Take a photo with the camera">
    <span class="k-drop-ic">${I.cam}</span><b>Take a photo</b><span>Lay it all out in good light. One photo is enough.</span></label>
  <div class="k-actions" style="margin-top:var(--s-3)"><label class="ds-btn k-filebtn">${I.image}Choose from gallery
    <input type="file" id="snapGal" accept="image/*" aria-label="Choose a photo from the gallery"></label></div>`;
function bindSnapInputs(root) {
  const cam = $("#snapCam", root), gal = $("#snapGal", root), drop = $("#snapDrop", root);
  [cam, gal].forEach(inp => inp && (inp.onchange = e => { const f = e.target.files[0]; e.target.value = ""; if (f) runSnap(f); }));
  if (drop) {
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("drag"); };
    drop.ondragleave = () => drop.classList.remove("drag");
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove("drag"); const f = e.dataTransfer.files[0]; if (f && f.type.startsWith("image/")) runSnap(f); };
  }
}
async function runSnap(file) {
  if (file.size > 25 * 1024 * 1024) { toast("That photo is too big (keep it under 25 MB)."); return; }
  const stage = $("#snapStage"), out = $("#snapOut"), url = URL.createObjectURL(file);
  ui.snap = null;
  stage.innerHTML = `<div class="k-scan"><img src="${url}" alt="Your photo, being read"><span class="k-scan-label k-pill"><span class="ds-spinner" style="width:14px;height:14px"></span>Reading your photo…</span></div>`;
  out.innerHTML = `<div class="k-block"><div class="ds-skeleton" style="height:20px;width:50%"></div><div class="k-items">${"<div class='ds-skeleton' style='height:44px;width:110px;border-radius:999px'></div>".repeat(4)}</div></div>`;
  try {
    let r;
    if (DEMO) { await new Promise(res => setTimeout(res, 1400)); r = demoSnap(); }
    else { const fd = new FormData(); fd.append("file", file); fd.append("kind", ui.snapKind); r = await apiForm("/api/kitchen/snap", fd); }
    r.preview = url; ui.snap = r;
    if (r.id) K.snaps = [{ id: r.id, kind: r.kind || ui.snapKind, at: new Date().toISOString(), thumb: r.thumb || null }, ...(K.snaps || []).filter(x => x.id !== r.id)].slice(0, 12);
    showSnapResult(r);
  } catch (err) {
    stage.innerHTML = snapTarget(); bindSnapInputs(stage);
    out.innerHTML = `<div class="k-note">${I.warn} <b>Could not read that photo.</b> ${esc(err.message)} Try a well-lit photo with everything in view.</div>`;
  }
}
function showSnapResult(r) {
  const stage = $("#snapStage"), out = $("#snapOut");
  stage.innerHTML = `<div class="k-scan done"><img src="${esc(r.preview || "")}" alt="Your photo"><span class="k-scan-label k-pill ok">${I.check}Read</span></div>
    <div class="k-actions" style="margin-top:var(--s-3)"><button class="ds-btn" type="button" id="snapAgain">${I.cam}Snap another</button></div>`;
  $("#snapAgain").onclick = () => { ui.snap = null; stage.innerHTML = snapTarget(); bindSnapInputs(stage); out.innerHTML = ""; };
  // register any dishes the response carries so cards and the sheet can open them
  for (const m of r.meals || []) {
    const id = typeof m === "string" ? m : (m.dish && typeof m.dish === "object" ? m.dish.id : m.dish);
    if (m && typeof m.dish === "object" && m.dish.id) K.dishes[m.dish.id] = { ...m.dish };
    else if (id && !D(id)) K.dishes[id] = { id, name: m.name || id, photo: m.photo || null, ingredients: [], steps: [], macros: {} };
  }
  if (r.scraps && r.scraps.id) K.dishes[r.scraps.id] = { ...r.scraps, chef_scraps: true };
  const items = (r.items || []).map(i => ({ name: i.name || "", qty: i.qty || "", on: true }));
  r._items = r._items || items;
  if (r.match_dish && r.match_dish.id) K.dishes[r.match_dish.id] = { ...r.match_dish };
  const plate = r.match && D(r.match);
  const label = r.label && typeof r.label === "object"
    ? [r.label.serving && `Per ${r.label.serving}`, r.label.kcal != null && `${r.label.kcal} kcal`, r.label.protein_g != null && `${r.label.protein_g} g protein`,
       r.label.carbs_g != null && `${r.label.carbs_g} g carbs`, r.label.fat_g != null && `${r.label.fat_g} g fat`].filter(Boolean).join(" · ")
    : (r.label || "");
  const meals = (r.meals || []).map(m => { const id = typeof m === "string" ? m : (typeof m.dish === "object" ? m.dish.id : m.dish); return { d: D(id), have: m.have || [], missing: m.missing || [] }; }).filter(x => x.d && x.d.taste !== "dislike");
  out.innerHTML = `<div class="k-block" style="gap:var(--s-5)">
    ${label || r.note ? `<div class="k-note">${label ? `<b>${esc(label)}</b> ` : ""}${esc(r.note || "")}</div>` : ""}
    ${plate ? `<div class="k-block"><div class="k-head"><h2>Looks like ${esc(plate.name)}</h2></div><div class="k-grid">${smallCard(plate)}</div>
      <div class="k-actions"><button class="ds-btn primary k-lg" type="button" id="logPlate">${I.check}Log this plate</button></div></div>` : ""}
    ${r._items.length ? `<div class="k-block"><div class="k-head"><h2>What the chef saw</h2><span class="k-aside">Fix anything, then add</span></div>
      <div class="k-items" id="snapItems">${r._items.map((it, i) => itemChip(it, i)).join("")}
        <button class="ds-chip" type="button" id="itemAdd">${I.plus}Add item</button></div>
      <div class="k-actions"><button class="ds-btn primary k-lg" type="button" id="snapConfirm">${I.check}Add ${r._items.filter(x => x.on && x.name.trim()).length} to pantry</button></div></div>` : ""}
    <div class="k-block"><div class="k-head"><h2>Cook right now</h2><span class="k-aside">Best first</span></div>
      ${meals.length || r.scraps ? `<div class="k-grid">${r.scraps && D(r.scraps.id) ? smallCard(D(r.scraps.id), { badge: `<span class="k-pill warn">${I.chef}Chef's scraps</span>`, note: "Made up from exactly what is there. Macros are estimates." }) : ""}
        ${meals.map(x => smallCard(x.d, { have: x.have, missing: x.missing })).join("")}</div>` : `<p class="k-sub">Nothing from the library fits yet. Add these to the pantry and check Cook now on Today.</p>`}</div></div>`;
  bindCards(out);
  const lp = $("#logPlate"); if (lp) lp.onclick = () => { markEaten(plate.id, guessSlot()); lp.disabled = true; };
  bindItemChips(r);
}
const itemChip = (it, i) => `<span class="k-ichip ${it.on ? "" : "off"}" data-i="${i}">
  <input value="${esc(it.name)}" aria-label="Item ${i + 1} name" size="${Math.max(4, it.name.length)}"><input class="q" value="${esc(it.qty)}" placeholder="qty" aria-label="Item ${i + 1} amount" size="${Math.max(3, it.qty.length)}">
  <button type="button" data-rm-item="${i}" aria-label="${it.on ? "Leave out" : "Put back"} ${esc(it.name || "this item")}">${it.on ? I.x : I.plus}</button></span>`;
function bindItemChips(r) {
  const box = $("#snapItems"); if (!box) return;
  const sync = () => { const btn = $("#snapConfirm"); if (btn) btn.innerHTML = `${I.check}Add ${r._items.filter(x => x.on && x.name.trim()).length} to pantry`; };
  $$(".k-ichip", box).forEach(ch => { const i = +ch.dataset.i, [n, q] = $$("input", ch);
    n.oninput = () => { r._items[i].name = n.value; n.size = Math.max(4, n.value.length); sync(); };
    q.oninput = () => { r._items[i].qty = q.value; q.size = Math.max(3, q.value.length); }; });
  $$("[data-rm-item]", box).forEach(b => b.onclick = () => { const it = r._items[+b.dataset.rmItem]; it.on = !it.on; showSnapResult(r); });
  $("#itemAdd").onclick = () => { r._items.push({ name: "", qty: "", on: true }); showSnapResult(r); const ins = $$("#snapItems .k-ichip input:not(.q)"); ins[ins.length - 1].focus(); };
  $("#snapConfirm").onclick = async () => {
    const picked = r._items.filter(x => x.on && x.name.trim()).map(x => ({ name: x.name.trim(), qty: x.qty.trim(), source: "photo" }));
    if (!picked.length) return;
    const btn = $("#snapConfirm"); btn.disabled = true;
    try {
      const res = await write("/api/kitchen/pantry", { items: picked });
      K.pantry = res && res.items ? res.items : [...(K.pantry || []), ...picked.map(p => ({ ...p, added: isoDay() }))];
      btn.innerHTML = `${I.check}Added to pantry`; toast(`Added ${count(picked.length, "item")} to the pantry${demoNote()}`, "ok"); renderPantry();
    } catch (err) { btn.disabled = false; toast("Not added: " + err.message, "danger"); }
  };
}
function demoSnap() {
  const scraps = { id: "chef-scraps-demo", name: "Diri Kole ak Ze ak Chou", english: "Fried rice with eggs, cabbage and carrot", cuisine: "Haitian",
    category: "main", meals: ["lunch", "dinner"], servings: 5, portion: "Adults 2 cups; toddler 1/2 cup", active_min: 15, total_min: 20, difficulty: 1,
    cost_per_serving_usd: 0.95, chef_scraps: true, estimate: true, photo: null, taste: null, pantry_match: 1,
    ingredients: [{ item: "cooked rice (leftover)", qty: "5 cups", grams: 900, key: true, aisle: "grains" }, { item: "eggs", qty: "8", grams: 400, key: true, aisle: "dairy" },
      { item: "green cabbage", qty: "1/4 head", grams: 250, key: true, aisle: "produce" }, { item: "carrots", qty: "2", grams: 120, key: true, aisle: "produce" },
      { item: "epis", qty: "2 tbsp", grams: 30, key: false, aisle: "pantry" }, { item: "scallion", qty: "3", grams: 45, key: false, aisle: "produce" }],
    steps: ["Shred the cabbage and grate the carrots while the pan heats.", "Fry epis 30 s in a little oil, add the vegetables, 3 min.",
      "Push to the side, scramble the eggs in the space, then fold everything together.", "Add the cold rice, press it flat, let it crisp 2 min, turn, repeat.",
      "Toddler portion out, then scallions and hot sauce for the rest."],
    macros: { kcal: 470, protein_g: 21, carbs_g: 66, fat_g: 13, fiber_g: 4, sodium_mg: 520 }, tags: ["scraps", "quick", "budget", "toddler-ok"],
    toddler: "Plain, before the hot sauce; cut the egg small.", swap: "Add the leftover shredded chicken: plus 15 g protein.", pairs_with: ["pikliz"] };
  return { id: "s-demo-" + Date.now(), kind: ui.snapKind === "auto" ? "groceries" : ui.snapKind, thumb: null, label: "Groceries on the counter",
    note: "Five things I'm sure about. Check the amounts.",
    items: [{ name: "green plantains", qty: "4" }, { name: "eggs", qty: "12" }, { name: "red onion", qty: "2" }, { name: "chicken thighs", qty: "3 lb" }, { name: "green cabbage", qty: "1 head" }],
    meals: (K.cook_now || []).slice(0, 4).map(c => ({ dish: c.dish, name: (D(c.dish) || {}).name, photo: null, have: c.have, missing: c.missing })), scraps };
}

/* ---------- WEEK ---------- */
function renderWeek() {
  const s = $("#week"), week = K.week || [];
  if (!week.length) { s.innerHTML = empty("No meal plan yet", "Ask Jarvis: “plan this week's meals”, or tap Replan.", `<button class="ds-btn primary" type="button" id="replan0">${I.refresh}Plan the week</button>`); $("#replan0").onclick = () => replan(false); return; }
  const today = isoDay();
  if (!ui.day || !week.some(w => w.date === ui.day)) ui.day = (week.find(w => w.date === today) || week[0]).date;
  const day = week.find(w => w.date === ui.day);
  const people = (K.household && K.household.size) || 5, budget = (K.household && K.household.budget_week_usd) || 0;
  const planned = week.reduce((a, w) => a + Object.values(w.meals || {}).reduce((b, id) => b + ((D(id) || {}).cost_per_serving_usd || 0) * people, 0), 0);
  const pct = budget ? Math.min(planned / budget * 100, 100) : 0;
  const over = budget && planned > budget;
  s.innerHTML = `<div class="k-hero"><span class="k-eyebrow">Week of ${esc(new Date(week[0].date + "T12:00").toLocaleDateString("en-US", { month: "long", day: "numeric" }))}</span>
      <h2 class="k-h2" style="font-size:var(--fs-2xl);line-height:1.1">The week's table</h2></div>
    <div class="k-week-grid">
    <div class="k-block" style="gap:var(--s-5)">
      <div class="k-block">
        <div class="k-strip" role="group" aria-label="Pick a day">${week.map(w => { const d = new Date(w.date + "T12:00");
          return `<button type="button" class="k-day dt-${esc(w.day_type || "rest")} ${w.date === today ? "is-today" : ""}" data-day="${esc(w.date)}" aria-pressed="${w.date === ui.day}"
            aria-label="${esc(d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" }))}, ${esc(w.day_type || "")} day${w.date === today ? ", today" : ""}">
            <small>${esc(w.date === today ? "Today" : d.toLocaleDateString("en-US", { weekday: "short" }))}</small><b>${d.getDate()}</b><em>${esc(w.day_type || "")}</em></button>`; }).join("")}</div>
        <div class="k-legend"><span class="dt-heavy"><i></i>Heavy</span><span class="dt-run"><i></i>Run</span><span class="dt-rest"><i></i>Rest</span></div>
      </div>
      <div class="k-block"><div class="k-head"><h2>${esc(new Date(day.date + "T12:00").toLocaleDateString("en-US", { weekday: "long" }))}</h2>${dayTypeChip(day.day_type, day.label)}</div>
        <div class="k-daymeals">${["breakfast", "lunch", "drink", "dinner"].filter(slot => slot !== "drink" || (day.meals || {}).drink).map(slot => listCard(day, slot)).join("")}</div></div>
      <div class="k-actions"><button class="ds-btn" type="button" id="replanBtn">${I.refresh}Replan the week</button>
        <button class="ds-btn" type="button" id="replanTight">${I.leaf}Tight budget plan</button></div>
    </div>
    <div class="k-block" style="gap:var(--s-5)">
      <div class="k-budget"><div class="k-budget-n"><span class="k-eyebrow">Planned meals this week</span><span class="k-sub">${esc(count(people, "person", "people"))}</span></div>
        <div class="k-budget-n"><b>${esc(money(planned))}</b><span class="k-sub">of ${esc(money(budget))} budget</span></div>
        <div class="k-track ${over ? "over" : ""}" role="meter" aria-label="Weekly budget used" aria-valuemin="0" aria-valuemax="${Math.round(+budget || 0)}" aria-valuenow="${Math.round(planned)}"><i data-w="${pct.toFixed(1)}" style="--c:var(--ok)"></i></div>
        <span class="k-sub">${over ? `About ${esc(money(planned - budget))} over. Try the tight budget plan.` : `About ${esc(money(budget - planned))} left for staples and snacks.`}</span></div>
      <div class="k-block" id="shop"></div>
    </div></div>`;
  $$("[data-day]", s).forEach(b => b.onclick = () => { ui.day = b.dataset.day; renderWeek(); $(`[data-day="${ui.day}"]`).focus(); });
  $$("[data-swap]", s).forEach(b => b.onclick = () => swapMeal(b.dataset.date, b.dataset.swap));
  $("#replanBtn").onclick = () => replan(false); $("#replanTight").onclick = () => replan(true);
  bindCards(s); animateMeters(s); renderShop();
}
function listCard(day, slot) {
  const d = D((day.meals || {})[slot]);
  if (!d) return `<div class="k-panel"><span class="k-slot">${esc(slot)}</span><p class="k-sub">Nothing planned.</p></div>`;
  return `<article class="k-card k-lcard"><button class="k-hit" type="button" data-open="${esc(d.id)}" aria-label="Open ${esc(d.name)}, ${esc(slot)}"></button>
    ${photo(d, "r11")}
    <div class="k-card-body"><span class="k-slot">${esc(slot)}</span><div class="k-card-name">${esc(d.name)}</div>${metaLine(d)}
      <button class="ds-btn sm ghost k-swap" type="button" data-swap="${esc(slot)}" data-date="${esc(day.date)}" aria-label="Swap ${esc(slot)} for another dish">${I.swap}Swap</button></div></article>`;
}
async function swapMeal(date, slot) {
  const w = K.week.find(x => x.date === date); if (!w) return;
  try {
    let r = await write("/api/kitchen/swap", { date, slot });
    if (!r) { // demo: the next dish that fits the slot, not disliked
      const cur = w.meals[slot], pool = Object.values(K.dishes).filter(d => d.id !== cur && d.taste !== "dislike" && (d.meals || []).includes(slot));
      if (pool.length) w.meals[slot] = pool[(pool.findIndex(d => d.id > cur) + pool.length) % pool.length].id;
    } else if (r.week) { K.week = r.week; if (r.dishes) Object.assign(K.dishes, r.dishes); }
    toast(`${cap(slot)} swapped to ${(D(K.week.find(x => x.date === date).meals[slot]) || {}).name || "another dish"}${demoNote()}`);
  } catch (err) { toast("Not swapped: " + err.message, "danger"); }
  renderWeek(); renderToday();
}
async function replan(tight) {
  const s = $("#week"); const btns = $$("#replanBtn, #replanTight, #replan0", s); btns.forEach(b => b.disabled = true);
  toast(tight ? "Planning a tighter week…" : "Replanning the week…");
  try {
    const r = await write("/api/kitchen/replan", { budget_tight: !!tight });
    if (r && r.dishes) { K = normalise(r); } else if (!r) toast("Demo: replanning needs Jarvis; the plan stays as it is.");
    renderAll();
  } catch (err) { btns.forEach(b => b.disabled = false); toast("Could not replan: " + err.message, "danger"); }
}
function renderShop() {
  const s = $("#shop"); if (!s) return;
  const items = [...(K.shopping || []).map(i => ({ ...i, qty: Array.isArray(i.qty) ? totalQty(i.qty) : i.qty })), ...extraShopping()];
  const key = "jarvis_kitchen_got_" + weekKey(), got = store.get(key, {});
  const route = K.route;
  const head = `<div class="k-head"><h2>Shopping list</h2><span class="k-aside">${items.filter(i => !got[slug(i.item)]).length} to get</span></div>`;
  if (!items.length) { s.innerHTML = head + empty("Nothing to buy yet", "The list builds itself from the week's plan.", "", I.cart); return; }
  const groups = {}; items.forEach(i => { const g = i.store || i.aisle || "other"; (groups[g] = groups[g] || []).push(i); });
  const ORDER = ["produce", "meat", "seafood", "dairy", "grains", "bakery", "pantry", "spices", "frozen", "other"];
  const ordered = Object.entries(groups).sort((a, b) => ((ORDER.indexOf(a[0]) + 1) || 99) - ((ORDER.indexOf(b[0]) + 1) || 99));
  const routeCard = route && route.stops ? `<div class="k-panel"><div class="k-row"><b class="grow">Shopping route</b><span class="k-sub">about ${esc(route.minutes)} min, ${esc(route.miles)} mi</span></div>
      ${route.stops.map((p, i) => `<div class="k-sub">${i + 1}. ${esc(p.store)} · ${esc(p.address)}</div>`).join("")}
      <a class="ds-btn primary" href="${esc(route.link)}" target="_blank" rel="noopener">Start navigation</a></div>` : "";
  s.innerHTML = head + routeCard + `<div class="k-shop">${ordered.map(([g, list]) => `<div class="k-aisle"><h3>${esc(g)}</h3>${list.map(i => {
      const id = slug(i.item), forNames = (i.for || []).map(x => (D(x) || {}).name).filter(Boolean);
      return `<label class="k-check ${got[id] ? "got" : ""}"><input type="checkbox" data-id="${esc(id)}" ${got[id] ? "checked" : ""}>
        <span class="grow"><span>${esc(i.item)}</span>${forNames.length ? `<small>for ${esc(forNames.join(", "))}</small>` : i.local ? `<small>added from a recipe</small>` : ""}</span><span class="qty">${esc(i.qty || "")}</span></label>`; }).join("")}</div>`).join("")}</div>`;
  $$("input[type=checkbox]", s).forEach(c => c.onchange = () => {
    got[c.dataset.id] = c.checked; store.set(key, got); c.closest(".k-check").classList.toggle("got", c.checked);
    s.querySelector(".k-aside").textContent = `${items.filter(i => !got[slug(i.item)]).length} to get`;
  });
}

/* ---------- PANTRY ---------- */
const SHELF = [[/chicken|pork|beef|goat|fish|shrimp|sausage|meat|turkey/, 3], [/milk/, 7], [/yogurt|cheese/, 14], [/\beggs?\b/, 21], [/banana|avocado|mango/, 5],
  [/spinach|lettuce|herb|cilantro|parsley|scallion|watercress/, 5], [/plantain/, 8], [/tomato|pepper|scotch|eggplant|chayote|squash|lime|orange|lemon/, 10],
  [/cabbage|carrot/, 18], [/onion|garlic|potato|yam|malanga/, 30], [/epis/, 30], [/rice|bean|pea|cornmeal|flour|spaghetti|pasta|can|vinegar|oil|sugar|salt|spice|paste/, 365]];
function fresh(p) {
  const name = String(p.name || "").toLowerCase(), shelf = (SHELF.find(([re]) => re.test(name)) || [, 14])[1];
  if (!p.added) return { shelf, left: null, cls: shelf >= 365 ? "long" : "", pct: 100, label: "" };
  const age = Math.max(0, Math.round((new Date(isoDay()) - new Date(p.added)) / 864e5)), left = shelf - age;
  const pct = Math.max(4, Math.min(100, left / shelf * 100));
  if (shelf >= 365) return { shelf, left, cls: "long", pct: 100, label: "" };
  if (left <= 0) return { shelf, left, cls: "danger", pct, label: "Use today" };
  if (left <= 2) return { shelf, left, cls: "warn", pct, label: left === 1 ? "Use by tomorrow" : "Use soon" };
  return { shelf, left, cls: "", pct, label: "" };
}
function renderPantry() {
  const s = $("#pantry");
  s.innerHTML = `<div class="k-hero"><span class="k-eyebrow">What's in the house</span><h2 class="k-h2" style="font-size:var(--fs-2xl);line-height:1.1">Pantry</h2></div>
    <div class="k-block">
      <div class="k-search">${I.search}<label class="k-sr" for="pq">Search the pantry</label><input class="ds-input" id="pq" type="search" placeholder="Search the pantry" value="${esc(ui.pq)}" autocomplete="off"></div>
      <form class="k-addrow" id="addForm"><label class="k-sr" for="addName">Item to add</label><input class="ds-input" id="addName" placeholder="Add an item, e.g. yams" autocomplete="off" required>
        <label class="k-sr" for="addQty">Amount</label><input class="ds-input" id="addQty" placeholder="Qty" autocomplete="off">
        <button class="ds-btn primary" type="submit">${I.plus}Add</button></form>
      <div class="k-actions"><label class="ds-btn k-filebtn">${I.receipt}Scan receipt<input id="rcpt" type="file" accept="image/*" capture="environment" aria-label="Scan a grocery receipt"></label>
        <button class="ds-btn" type="button" id="cookBtn">${I.chef}Ask the chef</button></div>
      <div id="scanOut" aria-live="polite"></div><div id="recipeOut" aria-live="polite"></div>
    </div>
    <div class="k-block" id="useUp"></div>
    <div class="k-block"><div class="k-head"><h2>In the pantry</h2><span class="k-aside" id="pantryCount"></span></div><div id="pantryList"></div></div>`;
  $("#pq").oninput = e => { ui.pq = e.target.value; pantryList(); };
  $("#addForm").onsubmit = addTyped;
  $("#rcpt").onchange = scanReceipt; $("#cookBtn").onclick = cook;
  if (!K.pantry) loadPantry(); else { pantryList(); useItUp(); }
}
async function loadPantry() {
  const el = $("#pantryList"); el.innerHTML = `<div class="k-pgrid">${"<div class='ds-skeleton' style='height:112px;border-radius:var(--r-3)'></div>".repeat(6)}</div>`;
  try { K.pantry = (await apiGet("/api/kitchen/pantry")).items || []; pantryList(); useItUp(); }
  catch (e) {
    el.innerHTML = `<div class="ds-empty">${I.warn}<b>The pantry did not load</b><span>${esc(e.message)}</span><button class="ds-btn sm" type="button" id="pantryRetry">Try again</button></div>`;
    $("#pantryRetry").onclick = loadPantry;
  }
}
function pantryList() {
  const el = $("#pantryList"), q = ui.pq.trim().toLowerCase(), all = K.pantry || [];
  const items = all.map(p => ({ p, f: fresh(p) })).filter(x => !q || x.p.name.toLowerCase().includes(q))
    .sort((a, b) => (a.f.left ?? 999) - (b.f.left ?? 999) || a.p.name.localeCompare(b.p.name));
  $("#pantryCount").textContent = count(all.length, "item");
  if (!all.length) { el.innerHTML = `<div class="ds-empty">${I.bowl}<b>The pantry is empty</b><span>Snap your groceries or scan a receipt and the chef fills it in.</span></div>`; return; }
  if (!items.length) { el.innerHTML = `<p class="k-sub">Nothing called “${esc(q)}”. Add it above.</p>`; return; }
  el.innerHTML = `<div class="k-pgrid">${items.map(({ p, f }) => `<div class="k-ptile"><b>${esc(p.name)}</b><span class="q">${esc(p.qty || "")}${p.added ? `${p.qty ? " · " : ""}added ${esc(when(p.added + "T12:00"))}` : ""}</span>
      ${f.label ? `<span class="ds-badge ${f.cls}">${esc(f.label)}</span>` : f.cls === "long" ? `<span class="ds-badge">Keeps</span>` : `<span class="ds-badge ok">Fresh</span>`}
      <div class="k-fresh ${f.cls}" title="Estimated freshness"><i style="width:${f.pct}%"></i></div>
      <button class="ds-iconbtn ghost sm" type="button" data-rm="${esc(p.name)}" aria-label="Remove ${esc(p.name)} from the pantry">${I.x}</button></div>`).join("")}</div>
    <p class="k-sub" style="margin-top:var(--s-2)">Freshness is an estimate from the day each item was added.</p>`;
  $$("button[data-rm]", el).forEach(b => b.onclick = () => removeItem(b.dataset.rm));
}
function useItUp() {
  const el = $("#useUp"); if (!el) return;
  const soon = (K.pantry || []).filter(p => { const f = fresh(p); return f.left != null && f.left <= 3 && f.cls !== "long"; });
  const picks = Object.values(K.dishes || {}).filter(d => d.taste !== "dislike" && !d.chef_scraps).map(d => {
    const uses = soon.filter(p => (d.ingredients || []).some(i => { const a = toks(i.item), b = toks(p.name); return b.length && (b.every(w => a.includes(w)) || a.every(w => b.includes(w))); }));
    return { d, uses };
  }).filter(x => x.uses.length).sort((a, b) => b.uses.length - a.uses.length || (b.d.pantry_match || 0) - (a.d.pantry_match || 0)).slice(0, 6);
  el.innerHTML = `<div class="k-head"><h2>Use it up</h2><span class="k-aside">${soon.length ? esc(soon.map(p => p.name).slice(0, 3).join(", ")) + (soon.length > 3 ? "…" : "") : "Nothing urgent"}</span></div>
    ${picks.length ? `<div class="k-rail">${picks.map(x => smallCard(x.d, { note: "Uses " + x.uses.map(p => p.name).join(", ") })).join("")}</div>`
      : `<p class="k-sub">${soon.length ? "No library dish uses those yet. Ask the chef for something from the pantry." : "Everything is fresh. The chef will flag food here a couple of days before it turns."}</p>`}`;
  bindCards(el);
}
async function addTyped(e) {
  e.preventDefault();
  const name = $("#addName").value.trim(), qty = $("#addQty").value.trim(); if (!name) return;
  const btn = e.submitter || $("#addForm button"); btn.disabled = true;
  try {
    const r = await write("/api/kitchen/pantry", { items: [{ name, qty, source: "typed" }] });
    K.pantry = r && r.items ? r.items : [...(K.pantry || []), { name, qty, added: isoDay(), source: "typed" }];
    toast(`Added ${name}${demoNote()}`, "ok"); ui.pq = ""; renderPantry(); $("#addName").focus();
  } catch (err) { btn.disabled = false; toast("Not added: " + err.message, "danger"); }
}
async function scanReceipt(e) {
  const f = e.target.files[0]; e.target.value = ""; if (!f) return;
  const out = $("#scanOut"); out.innerHTML = `<div class="k-panel">${busy("Reading your receipt…")}</div>`;
  try {
    let j;
    if (DEMO) { await new Promise(r => setTimeout(r, 1000)); j = { items: [{ name: "yams", qty: "2 lb" }, { name: "avocado", qty: "2" }, { name: "evaporated milk", qty: "3 cans" }] }; }
    else {
      const fd = new FormData(); fd.append("file", f);
      const r = await fetch("/api/kitchen/scan-receipt", { method: "POST", headers: authH(), body: fd });
      j = await readJson(r);
      if (!r.ok || !(j.items || []).length) { out.innerHTML = `<div class="k-note">${esc(failure(r, j, "") || "No items found. Try a flatter, well-lit photo.")}</div>`; return; }
    }
    out.innerHTML = `<div class="k-panel"><p class="k-sub">Found ${count(j.items.length, "item")}. Untick any that are wrong, then add:</p><div>` +
      j.items.map((it, i) => `<label class="k-pick"><input type="checkbox" data-i="${i}" checked><span>${esc(it.name)}${it.qty ? ` <span class="k-sub">${esc(it.qty)}</span>` : ""}</span></label>`).join("") +
      `</div><button class="ds-btn primary" type="button" id="addBtn">Add to pantry</button></div>`;
    $("#addBtn").onclick = async () => {
      const picked = $$("input[data-i]:checked", out).map(c => ({ ...j.items[+c.dataset.i], source: "receipt" }));
      if (!picked.length) return;
      const btn = $("#addBtn"); btn.disabled = true;
      try {
        const r = await write("/api/kitchen/pantry", { items: picked });
        K.pantry = r && r.items ? r.items : [...(K.pantry || []), ...picked.map(p => ({ ...p, added: isoDay() }))];
        out.innerHTML = `<div class="k-note">${I.check} Added ${count(picked.length, "item")} to the pantry${esc(demoNote())}.</div>`; pantryList(); useItUp();
      } catch (err) { btn.disabled = false; toast("Not added: " + err.message, "danger"); }
    };
  } catch (err) { out.innerHTML = `<div class="k-note">Could not read that receipt (${esc(err.message)}). Check your connection and try again.</div>`; }
}
async function removeItem(name) {
  const before = K.pantry; K.pantry = (K.pantry || []).filter(p => p.name !== name); pantryList(); useItUp();
  try { const r = await write("/api/kitchen/pantry/remove", { name }); if (r && r.items) K.pantry = r.items; toast(`Removed ${name}${demoNote()}`); }
  catch (e) { K.pantry = before; toast("Not removed: " + e.message, "danger"); }
  pantryList(); useItUp();
}
async function cook() {
  const out = $("#recipeOut"), soon = (K.pantry || []).filter(p => { const f = fresh(p); return f.left != null && f.left <= 3 && f.cls !== "long"; }).map(p => p.name);
  out.innerHTML = `<div class="k-panel">${busy("The chef is thinking up a Haitian dish from what you have…")}</div>`;
  try {
    const j = DEMO ? (await new Promise(r => setTimeout(r, 900)), { recipe: "Demo mode: the chef answers here with a recipe built from the pantry, using up " + (soon.join(", ") || "what is oldest") + " first." })
      : await apiPost("/api/kitchen/cook", { focus: soon.join(", ") });
    out.innerHTML = `<div class="k-panel"><div class="k-row">${I.chef}<b class="grow">From the chef</b></div><div class="k-recipe-text">${esc(j.recipe || "No recipe came back.")}</div></div>`;
  } catch (e) { out.innerHTML = `<div class="k-note">Could not create a recipe right now (${esc(e.message)}). Try again in a moment.</div>`; }
}

/* ---------- YOU ---------- */
function spark(ws) {
  if (!ws || ws.length < 2) return "";
  const v = ws.map(w => +w.lbs), lo = Math.min(...v) - .5, hi = Math.max(...v) + .5;
  const pts = v.map((x, i) => [i / (v.length - 1) * 100, 36 - (x - lo) / (hi - lo) * 32]);
  return `<svg class="k-spark" viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label="Weight trend: ${esc(v[0])} to ${esc(v[v.length - 1])} lb">
    <polyline points="${pts.map(p => p.map(n => n.toFixed(1)).join(",")).join(" ")}"/></svg>`;
}
function renderYou() {
  const s = $("#you"), me = K.me || {}, t = me.targets, hh = K.household || {}, ws = me.weighins || [];
  const all = Object.values(K.dishes || {}), likes = all.filter(d => d.taste === "like"), dislikes = all.filter(d => d.taste === "dislike");
  const theme = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  const tasteChips = list => list.length ? `<div class="k-taste">${list.map(d => `<button class="ds-chip" type="button" data-untaste="${esc(d.id)}" aria-label="Remove ${esc(d.name)} from this list">${esc(d.name)}${I.x}</button>`).join("")}</div>` : "";
  const last = ws[ws.length - 1], first = ws[0];
  s.innerHTML = `<div class="k-me"><span class="k-avatar" aria-hidden="true">${esc((me.name || "?").charAt(0))}</span>
      <div><h2>${esc(me.name || "You")}</h2><div class="k-row" style="margin-top:6px"><span class="ds-badge accent">${I.dumbbell}${esc(me.goal || "muscle + speed")}</span>
      ${K.library_size ? `<span class="ds-badge">${esc(K.library_size)} dishes in the library</span>` : ""}</div></div></div>
    <div class="k-you-grid">
    <div class="k-block" style="gap:var(--s-6)">
      <div class="k-block"><div class="k-head"><h2>Daily targets</h2>${me.day_type ? dayTypeChip(me.day_type, "today") : ""}</div>
        ${t ? `<div class="k-tgrid">${[["Calories", t.kcal, "kcal"], ["Protein", t.protein_g, "g"], ["Carbs", t.carbs_g, "g"], ["Fat", t.fat_g, "g"], ["Water", t.water_l, "L"]]
          .map(([l, v, u]) => `<div class="k-stat"><small>${l}</small><b>${u === "L" ? esc(v) : int(v)} <small>${u}</small></b></div>`).join("")}</div>
          <p class="k-sub">Set by the nutritionist from your weight and training; they move with heavy, run and rest days. Not medical advice.</p>` : setupCard("you")}</div>
      <div class="k-block"><div class="k-head"><h2>Weigh-in</h2>${last ? `<span class="k-aside">${esc(last.lbs)} lb${first && first !== last ? ` · ${(last.lbs - first.lbs >= 0 ? "+" : "")}${(last.lbs - first.lbs).toFixed(1)} since ${esc(when(first.date + "T12:00"))}` : ""}</span>` : ""}</div>
        <div class="k-panel">${spark(ws)}
          <form class="k-row" data-weight-form><label class="k-sr" for="w-you2">Today's weight in pounds</label>
            <input class="ds-input grow" id="w-you2" type="number" inputmode="decimal" min="70" max="500" step="0.1" placeholder="Today's weight (lb)" value="" required>
            <button class="ds-btn primary" type="submit">${I.scale}Log</button></form></div></div>
    </div>
    <div class="k-block" style="gap:var(--s-6)">
      <div class="k-block"><div class="k-head"><h2>Household</h2></div>
        <form class="k-panel" id="hhForm"><div class="k-row">${I.users}<span class="grow">${esc(count(hh.size || 5, "person", "people"))}${hh.toddler ? ", including a toddler (every recipe has a toddler note)" : ""}</span></div>
          <div class="k-form2"><div class="ds-field"><label for="hhSize">People</label><input class="ds-input" id="hhSize" type="number" min="1" max="20" value="${esc(hh.size || 5)}"></div>
            <div class="ds-field"><label for="hhBudget">Weekly budget ($)</label><input class="ds-input" id="hhBudget" type="number" min="0" step="5" value="${esc(hh.budget_week_usd || "")}"></div></div>
          <button class="ds-btn" type="submit">Save household</button></form></div>
      <div class="k-block"><div class="k-head"><h2>Dishes you like</h2><span class="k-aside">${likes.length}</span></div>
        ${tasteChips(likes) || `<p class="k-sub">Tap Like on any dish and it comes up more often.</p>`}</div>
      <div class="k-block"><div class="k-head"><h2>Not for you</h2><span class="k-aside">${dislikes.length}</span></div>
        ${tasteChips(dislikes) || `<p class="k-sub">Disliked dishes never show up in plans or suggestions.</p>`}</div>
      <div class="k-block"><div class="k-head"><h2>Appearance</h2></div>
        <div class="k-seg" role="group" aria-label="Theme"><button type="button" data-theme-set="dark" aria-pressed="${theme === "dark"}">${I.moon}Dark</button><button type="button" data-theme-set="light" aria-pressed="${theme === "light"}">${I.sun}Light</button></div></div>
    </div></div>`;
  bindWeightForms(s);
  $$("[data-untaste]", s).forEach(b => b.onclick = () => setTaste(b.dataset.untaste, "clear"));
  $$("[data-theme-set]", s).forEach(b => b.onclick = () => {
    const v = b.dataset.themeSet; if (v === "light") document.documentElement.setAttribute("data-theme", "light"); else document.documentElement.removeAttribute("data-theme");
    try { localStorage.setItem("jarvis_kitchen_theme", v); } catch (e) {} renderYou();
  });
  $("#hhForm").onsubmit = async e => {
    e.preventDefault(); const size = parseInt($("#hhSize").value, 10), budget = parseFloat($("#hhBudget").value);
    const body = {}; if (size > 0) body.size = size; if (budget >= 0) body.budget_week_usd = budget;
    try { const r = await write("/api/kitchen/household", body); K.household = (r && r.household) || { ...K.household, ...body }; toast("Household saved" + demoNote(), "ok"); renderYou(); renderWeek(); }
    catch (err) { toast("Not saved: " + err.message, "danger"); }
  };
}

/* ---------- taste (optimistic, with undo) ---------- */
async function setTaste(id, vote, quiet = false) {
  const d = D(id); if (!d) return;
  const prev = d.taste || null; d.taste = vote === "clear" ? null : vote;
  refreshTaste(id);
  try {
    await write("/api/kitchen/taste", { dish: id, vote });
    if (vote === "dislike" && !DEMO) refreshQuietly();   // the server swapped it out of this week's plan
    if (!quiet) toast(vote === "like" ? `Liked ${d.name}. It comes up more often.` : vote === "dislike" ? `${d.name} is off your plans.` : `${d.name}: vote cleared.`, "",
      { label: "Undo", run: () => setTaste(id, prev || "clear", true) });
  } catch (err) { d.taste = prev; refreshTaste(id); toast("Not saved: " + err.message, "danger"); }
}
function refreshTaste(id) {
  const d = D(id);
  $$(`.k-vote[data-dish="${CSS.escape(id)}"] button`).forEach(b => b.setAttribute("aria-pressed", String(d.taste === b.dataset.vote)));
  renderYou(); renderToday();
}

/* ---------- dish sheet ---------- */
function openDish(id, opener) {
  const d = D(id); if (!d) return;
  if (!ui.sheet) { ui.opener = opener || document.activeElement; history.pushState({ k: "dish" }, ""); }
  ui.sheet = id;
  const root = $("#sheetRoot"), m = M(d), est = d.estimate || d.chef_scraps;
  const ings = d.ingredients || [], st = ings.map(i => ingStatus(d, i)), need = ings.filter((_, i) => st[i] === "need");
  const pairs = (d.pairs_with || []).map(D).filter(Boolean);
  const tags = d.tags || [];
  root.innerHTML = `<div class="k-sheet" role="dialog" aria-modal="true" aria-labelledby="dishTitle" tabindex="-1">
   <div class="k-sheet-in">
    <div class="k-hero-photo">${photo(d, "", d.name)}
      <button class="ds-iconbtn k-sheet-close" type="button" id="sheetClose" aria-label="Close ${esc(d.name)}">${I.back}</button></div>
    <div class="k-sheet-body">
      <div class="k-title">
        <div class="k-tags">${d.chef_scraps ? `<span class="ds-badge warn">${I.chef}Chef's scraps</span>` : ""}${d.cuisine ? `<span class="ds-badge">${esc(d.cuisine)}</span>` : ""}${d.category ? `<span class="ds-badge">${esc(cap(d.category))}</span>` : ""}
          ${tags.filter(x => /high-protein|budget|quick|training-day|rest-day|toddler-ok|post-workout|pre-workout/.test(x)).map(x => `<span class="ds-badge ${x === "high-protein" ? "accent" : ""}">${esc(x.replace("-", " "))}</span>`).join("")}</div>
        <h2 id="dishTitle">${esc(d.name)}</h2>${d.english ? `<p>${esc(d.english)}</p>` : ""}
        ${metaLine(d, `${d.active_min ? `<span>${esc(d.active_min)} min hands-on</span>` : ""}${d.servings ? `<span>Serves ${esc(d.servings)}</span>` : ""}${d.difficulty ? `<span>${["", "Easy", "Medium", "Project"][d.difficulty] || ""}</span>` : ""}`)}
      </div>
      ${m.kcal != null ? `<div class="k-sec"><div class="k-macros">
        <div class="k-macro"><b>${int(m.kcal)}</b><small>kcal${est ? " (est.)" : ""}</small><i style="--c:var(--accent)"></i></div>
        <div class="k-macro"><b>${esc(m.protein_g)}g</b><small>protein</small><i style="--c:var(--k-protein)"></i></div>
        <div class="k-macro"><b>${esc(m.carbs_g)}g</b><small>carbs</small><i style="--c:var(--k-carbs)"></i></div>
        <div class="k-macro"><b>${esc(m.fat_g)}g</b><small>fat</small><i style="--c:var(--k-fat)"></i></div></div>
        <p class="k-sub">Per adult portion${d.portion ? `: ${esc(d.portion)}` : ""}${est ? ". Estimates for a made-up dish." : ""}${d.cost_per_serving_usd != null ? ` · about ${esc(money(d.cost_per_serving_usd))} a serving` : ""}</p></div>` : ""}
      <div class="k-vote" data-dish="${esc(d.id)}" role="group" aria-label="Your taste">
        <button class="ds-btn k-lg like" type="button" data-vote="like" aria-pressed="${d.taste === "like"}">${I.up}Like</button>
        <button class="ds-btn k-lg dislike" type="button" data-vote="dislike" aria-pressed="${d.taste === "dislike"}">${I.down}Not for me</button></div>
      ${ings.length ? `<div class="k-sec"><h3>Ingredients <small>${ings.length - need.length} of ${ings.length} in the house</small></h3>
        <ul class="k-ings">${ings.map((i, n) => `<li><span class="st ${st[n]}" role="img" aria-label="${st[n] === "have" ? "Have it" : st[n] === "need" ? "Need it" : "Kitchen staple"}">${st[n] === "have" ? I.check : st[n] === "need" ? "" : ""}</span>
          <span class="n">${esc(i.item)}<small class="${st[n]}">${st[n] === "have" ? "Have it" : st[n] === "need" ? "Need it" : "Kitchen staple"}</small></span><span class="q">${esc(i.qty || "")}</span></li>`).join("")}</ul>
        ${need.length ? `<button class="ds-btn" type="button" id="addMissing">${I.cart}Add ${count(need.length, "missing item")} to shopping</button>` : ""}</div>` : ""}
      ${(d.steps || []).length ? `<div class="k-sec"><h3>Method <small>${esc(d.total_min || "")}${d.total_min ? " min total" : ""}</small></h3>
        <ol class="k-steps">${d.steps.map(x => `<li><span>${esc(x)}</span></li>`).join("")}</ol></div>` : ""}
      <div class="k-callouts">
        ${d.toddler ? `<div class="k-callout">${I.baby}<div><b>For the toddler</b><p>${esc(d.toddler)}</p></div></div>` : ""}
        ${d.swap ? `<div class="k-callout">${I.swap}<div><b>Healthier swap</b><p>${esc(d.swap)}</p></div></div>` : ""}
        ${d.batch_tip ? `<div class="k-callout">${I.layers}<div><b>Batch it</b><p>${esc(d.batch_tip)}</p></div></div>` : ""}
        ${d.leftovers ? `<div class="k-callout">${I.refresh}<div><b>Leftovers</b><p>${esc(d.leftovers)}</p></div></div>` : ""}
      </div>
      ${pairs.length ? `<div class="k-sec"><h3>Goes with</h3><div class="k-taste">${pairs.map(p => `<button class="ds-chip" type="button" data-pair="${esc(p.id)}">${esc(p.name)}${I.chevR}</button>`).join("")}</div></div>` : ""}
    </div>
    <div class="k-dock-actions ds-glass">
      ${(d.steps || []).length ? `<button class="ds-btn primary k-lg" type="button" id="cookStart">${I.play}Cook mode</button>` : ""}
      <button class="ds-btn k-lg" type="button" id="addToday">${I.plus}Add to today</button></div>
   </div></div>`;
  $("#main").inert = true; $(".k-dock").inert = true; $(".k-top").inert = true;
  document.body.style.overflow = "hidden"; document.body.classList.add("k-modal");
  const sheet = $(".k-sheet", root); sheet.scrollTop = 0; sheet.focus({ preventScroll: true });
  hydrate(root);
  $("#sheetClose").onclick = closeSheet;
  $$(".k-vote button", root).forEach(b => b.onclick = () => setTaste(d.id, d.taste === b.dataset.vote ? "clear" : b.dataset.vote));
  $$("[data-pair]", root).forEach(b => b.onclick = () => openDish(b.dataset.pair));
  const am = $("#addMissing"); if (am) am.onclick = () => addMissing(d, need, am);
  const cs = $("#cookStart"); if (cs) cs.onclick = () => openCook(d);
  $("#addToday").onclick = () => addToday(d);
}
function closeSheet() { if (history.state && history.state.k === "dish") history.back(); else teardownSheet(); }
function teardownSheet() {
  if (!ui.sheet) return;
  closeCook(); ui.sheet = null; $("#sheetRoot").innerHTML = "";
  $("#main").inert = false; $(".k-dock").inert = false; $(".k-top").inert = false; document.body.style.overflow = "";
  let back = ui.opener;   // the card may have re-rendered while the sheet was open: find its replacement
  if (back && !document.contains(back) && back.dataset && back.dataset.open) back = $(`#${ui.tab} [data-open="${CSS.escape(back.dataset.open)}"]`);
  if (back && document.contains(back)) back.focus({ preventScroll: true });
  document.body.classList.remove("k-modal");
}
addEventListener("popstate", () => { if (ui.sheet) teardownSheet(); });
async function addMissing(d, need, btn) {
  const key = "jarvis_kitchen_extra_" + weekKey(), extra = store.get(key, []);
  const have = new Set([...(K.shopping || []), ...extra].map(i => slug(i.item)));
  const add = need.filter(i => !have.has(slug(i.item))).map(i => ({ item: i.item, qty: i.qty || "", aisle: i.aisle || "other", for: [d.id] }));
  btn.disabled = true; btn.innerHTML = `${I.check}On the shopping list`;
  if (!add.length) { toast("Already on the shopping list", "ok"); return; }
  try {
    if (DEMO) throw new Error("demo");
    await write("/api/kitchen/shopping", { items: add });
    K.shopping = [...(K.shopping || []), ...add.map(i => ({ ...i, aisle: "on the list", note: true }))];
    toast(`Added ${count(add.length, "item")} to the family shopping list`, "ok");
  } catch (err) {
    store.set(key, [...extra, ...add.map(i => ({ ...i, local: true }))]);
    toast(`Added ${count(add.length, "item")} to the shopping list (on this phone)`, "ok");
  }
  renderShop();
}
async function addToday(d) {
  const slot = guessSlot(), now = new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: false });
  K.today = K.today || { meals: [] };
  const meal = { slot, time: now, dish: d.id, done: false, added: true };
  K.today.meals.push(meal);
  await logMeal(d, meal);
  const b = $("#addToday"); if (b) { b.disabled = true; b.innerHTML = `${I.check}Logged for today`; }
}

/* ---------- cook mode ---------- */
async function wakeOn() { try { if ("wakeLock" in navigator) { ui.wake = await navigator.wakeLock.request("screen"); return true; } } catch (e) {} return false; }
function wakeOff() { try { ui.wake && ui.wake.release(); } catch (e) {} ui.wake = null; }
document.addEventListener("visibilitychange", () => { if (ui.cook && document.visibilityState === "visible") wakeOn(); });
async function openCook(d) {
  ui.cook = { d, i: 0 };
  const el = document.createElement("div"); el.className = "k-cook"; el.id = "cook"; el.tabIndex = -1;
  el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true"); el.setAttribute("aria-label", `Cook mode: ${d.name}`);
  document.body.appendChild(el); $(".k-sheet").inert = true;
  const ok = await wakeOn(); ui.cook.awake = ok; drawCook(); el.focus();
}
function drawCook() {
  const c = ui.cook, el = $("#cook"); if (!c || !el) return;
  const steps = c.d.steps || [], i = c.i, last = i === steps.length - 1;
  el.innerHTML = `<div class="k-cook-top"><button class="ds-iconbtn" type="button" id="cookClose" aria-label="Leave cook mode">${I.x}</button>
      <div class="grow"><b>${esc(c.d.name)}</b><span class="k-sub">${c.awake ? "Screen stays on while you cook" : "Keep your screen on: this browser cannot hold it awake"}</span></div></div>
    <div class="k-progress" aria-hidden="true">${steps.map((_, n) => `<i class="${n <= i ? "on" : ""}"></i>`).join("")}</div>
    <div class="k-cook-step" aria-live="polite"><small>Step ${i + 1} of ${steps.length}</small><p>${esc(steps[i])}</p></div>
    <div class="k-cook-nav"><button class="ds-btn" type="button" id="cookPrev" ${i === 0 ? "disabled" : ""}>${I.chevL}Back</button>
      <button class="ds-btn primary" type="button" id="cookNext">${last ? I.check + "Done" : "Next step" + I.chevR}</button></div>`;
  $("#cookClose").onclick = closeCook;
  $("#cookPrev").onclick = () => { c.i = Math.max(0, c.i - 1); drawCook(); };
  $("#cookNext").onclick = () => { if (last) { closeCook(); toast(`Enjoy the ${c.d.name}. Tap Add to today to log it.`, "ok"); } else { c.i++; drawCook(); $("#cookNext").focus(); } };
}
function closeCook() { const el = $("#cook"); if (!el) return; el.remove(); wakeOff(); ui.cook = null; const s = $(".k-sheet"); if (s) { s.inert = false; const b = $("#cookStart"); if (b) b.focus(); } }
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { if (ui.cook) closeCook(); else if (ui.sheet) closeSheet(); return; }
  if (ui.cook && (e.key === "ArrowRight" || e.key === "ArrowLeft")) { const b = $(e.key === "ArrowRight" ? "#cookNext" : "#cookPrev"); if (b && !b.disabled) b.click(); return; }
  if (e.key === "Tab" && (ui.cook || ui.sheet)) { // keep focus inside the open dialog
    const box = ui.cook ? $("#cook") : $(".k-sheet"), f = $$("button:not([disabled]), a[href], input, [tabindex='0']", box);
    if (!f.length) return; const a = f[0], z = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === a || document.activeElement === box)) { e.preventDefault(); z.focus(); }
    else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
  }
});

/* ---------- tabs ---------- */
const TABS = ["today", "snap", "week", "pantry", "you"];
function show(tab, focus = false) {
  if (!TABS.includes(tab)) tab = "today";
  ui.tab = tab;
  $$(".k-tab").forEach(b => { const on = b.dataset.tab === tab; b.setAttribute("aria-selected", on); b.tabIndex = on ? 0 : -1; });
  $$("main > section").forEach(sec => { const on = sec.id === tab; if (on && sec.hidden) { sec.classList.remove("k-enter"); void sec.offsetWidth; sec.classList.add("k-enter"); } sec.hidden = !on; });
  if (location.hash.slice(1) !== tab) history.replaceState(history.state, "", location.pathname + location.search + "#" + tab);
  scrollTo(0, 0);
  if (focus) $("#tab-" + tab).focus();
  const sec = $("#" + tab); if (sec) animateMeters(sec);
}
$$(".k-tab").forEach(b => b.onclick = () => show(b.dataset.tab));
addEventListener("hashchange", () => { const t = location.hash.slice(1); if (K && TABS.includes(t) && t !== ui.tab) show(t); });
$(".k-dock").addEventListener("keydown", e => {
  const i = TABS.indexOf(ui.tab), n = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 }[e.key];
  if (n == null) return; e.preventDefault(); show(TABS[(n + TABS.length) % TABS.length], true);
});

/* ---------- load ---------- */
function normalise(j) {
  j.dishes = j.dishes || {}; for (const [id, d] of Object.entries(j.dishes)) d.id = d.id || id;
  j.me = j.me || {}; j.me.eaten = j.me.eaten || {}; j.today = j.today || { meals: [] }; j.today.meals = j.today.meals || [];
  j.week = j.week || []; j.cook_now = j.cook_now || []; j.shopping = j.shopping || []; j.snaps = j.snaps || [];
  return j;
}
/* An older server (no /v2 yet): map /api/kitchen + /api/kitchen/pantry onto the v2 shape so nothing is lost. */
async function legacy() {
  const r = await fetch("/api/kitchen", { headers: { Authorization: `Bearer ${token}` } });
  if (r.status === 401) { location.href = "/"; return null; }
  if (!r.ok) throw new Error((await readJson(r)).detail || "Jarvis answered with error " + r.status);
  const L = await r.json(), dishes = {};
  for (const [k, rc] of Object.entries(L.recipes || {})) dishes[k] = { id: k, name: rc.name, servings: rc.servings, total_min: rc.minutes, category: "main", cuisine: "Haitian",
    cost_per_serving_usd: rc.cost && rc.servings ? rc.cost / rc.servings : null, ingredients: rc.ingredients || [], steps: rc.steps || [], macros: rc.macros || {}, toddler: rc.toddler || L.toddler_rules, photo: null, taste: null };
  const p = L.plan || {}, start = /^\d{4}-\d\d-\d\d$/.test(p.week_of || "") ? new Date(p.week_of + "T12:00") : new Date();
  const week = (p.days || []).map((d, i) => { const dt = new Date(start); dt.setDate(dt.getDate() + i); const meals = {};
    (d.meals || []).forEach(m => { const id = slug(m.recipe); if (!dishes[id]) dishes[id] = { id, name: m.recipe, ingredients: [], steps: [], macros: {}, photo: null }; meals[String(m.meal || "").toLowerCase()] = id; });
    return { date: isoDay(dt), day_type: null, label: d.day, meals }; });
  const t = L.targets || {}, today = L.today || [], sum = k => today.reduce((a, x) => a + (+x[k] || 0), 0);
  const tw = week.find(w => w.date === isoDay());
  let pantry = null; try { pantry = (await apiGet("/api/kitchen/pantry")).items || []; } catch (e) {}
  return { me: { goal: t.goal, targets: t.kcal ? { kcal: t.kcal, protein_g: t.protein_g, carbs_g: t.carbs_g, fat_g: t.fat_g, water_l: 3.5 } : null,
      eaten: { kcal: sum("kcal"), protein_g: sum("protein_g"), carbs_g: sum("carbs_g"), fat_g: sum("fat_g"), water_l: 0 }, set_up: !!t.kcal },
    household: L.household && typeof L.household === "object" ? L.household : { size: 5, toddler: true },
    today: { meals: tw ? Object.entries(tw.meals).map(([slot, dish]) => ({ slot, time: { breakfast: "7:00", lunch: "12:30", dinner: "18:30" }[slot] || "", dish, done: false })) : [] },
    week, dishes, cook_now: [], pantry, shopping: (L.shopping || []).map(i => ({ ...i, qty: Array.isArray(i.qty) ? totalQty(i.qty) : i.qty })), snaps: [], route: L.route };
}
function renderAll() { renderToday(); renderSnap(); renderWeek(); renderPantry(); renderYou(); topMeta(); }
function topMeta() { $("#topMeta").innerHTML = DEMO ? `<span class="ds-badge warn">Demo</span>` : ""; }
async function refreshQuietly() {
  try {
    const r = await fetch("/api/kitchen/v2", { headers: { Authorization: `Bearer ${token}` } });
    if (r.ok) { K = normalise(await r.json()); renderAll(); }
  } catch (e) {}
}
async function load() {
  if (!token && !DEMO) { for (const id of TABS) $("#" + id).innerHTML = empty("Link this phone first", "Open the Jarvis app and pair this device, then come back."); return; }
  try {
    let j;
    if (DEMO) { const r = await fetch("/kitchen-demo.json", { cache: "no-store" }); if (!r.ok) throw new Error("the demo data is missing (" + r.status + ")"); j = await r.json(); }
    else {
      const r = await fetch("/api/kitchen/v2", { headers: { Authorization: `Bearer ${token}` } });
      if (r.status === 401) { location.href = "/"; return; }
      if (r.status === 404) j = await legacy();
      else if (!r.ok) throw new Error((await readJson(r)).detail || "Jarvis answered with error " + r.status);
      else j = await r.json();
      if (!j) return;
    }
    K = normalise(j); renderAll(); show(TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : ui.tab);
  } catch (e) {
    $("#today").innerHTML = empty("The kitchen did not load", `Could not reach Jarvis (${e.message}). Check your connection, then try again.`, `<button class="ds-btn" type="button" id="retry">Try again</button>`, I.warn);
    $("#retry").onclick = load; show("today");
  }
}
load();
