// Jarvis app service worker: installable, fast, shows notifications — and always updates to the latest.
const CACHE = "jarvis-v48-captain";
const SHELL = ["/", "/index.html", "/design-system.css", "/study.html", "/languages.html", "/voice-record.html", "/fitness.html", "/captain.html", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];
// Authored games and artwork can run without the server after one online visit.
SHELL.push("/games.html", "/games.css", "/games-ui.mjs", "/games-story.mjs",
  "/games-story.json", "/games-party.mjs", "/games-rooms.mjs",
  "/games-mystery.mjs", "/games-mystery-ui.mjs", "/games-lantern.json",
  "/games-rivalry.mjs", "/games-rivalry-ui.mjs",
  "/games-know.mjs", "/games-know-ui.mjs",
  "/games-bluff.mjs", "/games-bluff-ui.mjs",
  "/games-would.mjs", "/games-would-ui.mjs",
  "/games-telephone.mjs", "/games-telephone-ui.mjs",
  "/imposter.html", "/imposter.css", "/imposter-ui.mjs",
  ...["family", "couple", "covers", "room"].map(name => "/games-assets/" + name + ".webp"));

self.addEventListener("install", e => {
  // Cache the shell but don't fail install if one asset 404s; take over immediately.
  e.waitUntil(caches.open(CACHE).then(c => Promise.allSettled(SHELL.map(u => c.add(u)))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// The page (HTML) and scripts are ALWAYS fetched fresh from the network (no stale app after an update);
// the cache is only a fallback when offline. API calls are never touched.
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  const isPage = e.request.mode === "navigate" || url.pathname === "/" || url.pathname.endsWith(".html");
  e.respondWith(
    fetch(e.request, isPage ? { cache: "no-store" } : {}).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match(e.request).then(r => r || caches.match("/index.html")))
  );
});

self.addEventListener("message", e => { if (e.data === "skipWaiting") self.skipWaiting(); });

self.addEventListener("push", e => {
  let data = { title: "Jarvis", body: "" };
  try { data = e.data.json(); } catch {}
  const urgent = data.category === "reminders";
  // 2026-11-10 Mark VI C4: a mission step waiting for his OK stays on screen with Approve / Deny. The buttons only OPEN
  // the app's confirm sheet (this worker has no token, and the fingerprint lock must stand before a yes); nothing is
  // answered from the lock screen.
  const approval = data.kind === "approval";
  e.waitUntil(Promise.all([
    self.registration.showNotification(data.title || "Jarvis", {
      body: data.body, icon: "/icons/icon-192.png", badge: "/icons/icon-192.png",
      tag: data.tag || undefined, renotify: !!data.tag, data: { url: data.url || "/", mission: data.mission },
      vibrate: urgent ? [180, 80, 180, 80, 260] : [120], requireInteraction: urgent || approval,
      actions: approval ? [{ action: "approve", title: "Approve" }, { action: "deny", title: "Deny" }] : [],
    }),
    // tell any open app window, so its bell updates without a reload
    self.clients.matchAll({ type: "window" }).then(list => list.forEach(c => c.postMessage({ type: "notification", data }))),
  ]));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const base = (e.notification.data && e.notification.data.url) || "/";
  const url = (e.action === "approve" || e.action === "deny") && base.includes("#mission=") ? base + "&do=" + e.action : base;
  e.waitUntil(self.clients.matchAll({ type: "window" }).then(list => {
    if (list.length) { list[0].postMessage({ type: "open", url }); return list[0].focus(); }
    return self.clients.openWindow(url);
  }));
});
