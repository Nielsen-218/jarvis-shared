"""Local-only dev harness for app/captain.html against the REAL Captain America backend, on THROWAWAY data.

    python scripts/pca_dev_server.py 8792                       # a fresh temp data folder, today's date
    python scripts/pca_dev_server.py 8792 --date 2026-11-23     # pin the trainer clock to that day (time of day stays real)
    python scripts/pca_dev_server.py 8792 --seed                # onboarding pre-answered (pain 0, everything complete)
    python scripts/pca_dev_server.py 8792 --keep <folder>       # reuse a folder an earlier run of this harness created
    python scripts/pca_dev_server.py 8792 --stub-music          # Jarvis's music picks search a fixed fake catalogue (no network)
    python scripts/pca_dev_server.py 8792 --stub-demo           # exercise demo videos come from a fake catalogue (no network)
    python scripts/pca_dev_server.py 8792 --stub-link           # a fake phone running Jarvis Link (paired, connected)
    python scripts/pca_dev_server.py 8792 --stub-health         # a fake phone's Health Connect sync: 10 nights, resting HR, 3 runs
    python scripts/pca_dev_server.py 8792 --stub-health --short-night 5.1 --rhr-today 60   # last night short, resting HR up

Serves app/ statically on 127.0.0.1 and mounts GET /api/pca, POST /api/pca/action, POST /api/fitness/checkin and a stub
POST /api/say (no audio) by calling tools.pca / tools.fitness directly. No auth: any token (or none) is accepted.
Jarvis Link: GET /api/link/apk (the committed APK), POST /api/link/paircode (a sandbox code) and GET /api/link/status are
served too, and GET /api/health/status. --stub-health posts one made-up Health Connect sync (tools/pca_health.ingest,
exactly what POST /api/link/health stores): 10 nights of sleep with stages, resting heart rate, steps, and 3 runs (one
of them this morning, so a run day offers it). --stub-link pairs a fake "jarvis-link" device and runs a fake phone in a thread (stub_link below): it takes
the queued commands, answers them, reports what plays every 2 s, moves on to a "Metrolist queue" track 8 s after each of
Jarvis's songs starts, and runs the auto-DJ the way the real server does on each report.

Safety: every data path is redirected into a temp folder this script created (the same overrides tests/conftest.py
uses), and it refuses to serve unless the storage root resolves inside that folder. It never reads or writes the real
data folder, never speaks, never opens a browser, never starts a timer thread."""
import argparse
import json
import mimetypes
import os
import sys
import tempfile
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
MARKER = ".pca-dev-sandbox"
SEED = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
        "equipment": "trap_bar,barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
        "runs_per_week": 1, "miles_per_week": 4, "weeks_since_last_run": 2, "weeks_since_last_heavy_lift": 1, "pain_level": 0,
        "medical_restriction": "no", "sickle_cell": "no", "sleep_hours": 7, "unit_pt_days": 3, "unit_pt_kind": "mixed"}


def sandbox(keep: str = "") -> Path:
    """The throwaway folder, with every Jarvis data location pointed inside it BEFORE the tools are imported."""
    if keep:
        box = Path(keep).resolve()
        temp = Path(tempfile.gettempdir()).resolve()
        if not (box / MARKER).is_file() or temp not in box.parents:
            sys.exit(f"--keep must be a folder this harness created under {temp} (it has a {MARKER} file).")
    else:
        box = Path(tempfile.mkdtemp(prefix="pca-dev-")).resolve()
        (box / MARKER).write_text("throwaway data for scripts/pca_dev_server.py\n", encoding="utf-8")
    for var, sub in {"JARVIS_PC_ROOT": "jarvis_pc", "JARVIS_COMPANION_DATA_DIR": "companion", "JARVIS_VAULT": "vault",
                     "JARVIS_PC_STATE_DIR": "pc-control", "JARVIS_OUTBOX_DIR": "outbox", "JARVIS_MISSIONS_DIR": "missions",
                     "JARVIS_STUDIO_DIR": "studio", "JARVIS_USERS_STATE_DIR": "users-state", "JARVIS_LIVE_ROOT": "live"}.items():
        os.environ[var] = str(box / sub)
    return box


def wire(box: Path, pinned: date | None):
    sys.path.insert(0, str(ROOT))
    from core import storage
    storage.DATA = box / "data"
    storage.DATA.mkdir(exist_ok=True)
    from tools import fitness, pca, trainer
    probe = storage.path("fitness.json").resolve()
    real = (ROOT / "data").resolve()
    assert box in probe.parents and real not in probe.parents, f"storage escaped the sandbox: {probe}"
    assert Path(storage.DATA).resolve().parent == box, f"storage root is not inside the sandbox: {storage.DATA}"
    if pinned:
        trainer.clock = lambda: datetime.combine(pinned, datetime.now().time())
    trainer.REST = lambda seconds, line, uid: None          # no spoken rest timers
    trainer.SAY = lambda seconds, line, uid: None           # no spoken cues
    pca._open_link = lambda url: "not opened (dev harness)"  # never opens a playlist
    from tools import pca_health
    if pinned:                                              # the health data's clock follows the trainer's day
        pca_health.clock = lambda: datetime.combine(pinned, datetime.now().time()).timestamp()
    return fitness, pca, trainer


def stub_health(today: date, last_sleep: float = 7.4, rhr_today: int = 52):
    """One made-up phone sync (what Jarvis Link would read from Health Connect): never real data."""
    from datetime import timedelta
    from tools import pca_health

    tz = datetime.now().astimezone().strftime("%z")
    tz = tz[:3] + ":" + tz[3:]
    at = lambda d, h, m=0: datetime.combine(d, datetime.min.time()).replace(hour=h, minute=m).isoformat() + tz   # noqa: E731
    g = pca_health.GARMIN_PACKAGE
    days = [today - timedelta(days=i) for i in range(10)]
    sleep = []
    for i, d in enumerate(days):
        hours = last_sleep if i == 0 else 7.0 + (i % 3) * 0.3
        start = datetime.combine(d, datetime.min.time()).replace(hour=6) - timedelta(hours=hours)
        sleep.append({"start": start.isoformat() + tz, "end": at(d, 6), "deep_min": round(hours * 60 * .18),
                      "rem_min": round(hours * 60 * .22), "light_min": round(hours * 60 * .55), "awake_min": round(hours * 60 * .05), "src": g})

    def run(d, h, minutes, seconds, miles, avg):
        start = datetime.combine(d, datetime.min.time()).replace(hour=h)
        end = start + timedelta(minutes=minutes, seconds=seconds)
        return {"start": start.isoformat() + tz, "end": end.isoformat() + tz, "type": "running", "type_id": 33,
                "title": "Springfield Running", "distance_m": miles * 1609.344, "avg_hr": avg, "max_hr": avg + 22, "kcal": 300, "src": g}
    body = {"v": 1, "app": "1.2", "final": True, "garmin_found": True, "sources": [g], "sleep": sleep,
            "resting_hr": [{"date": d.isoformat(), "bpm": rhr_today if i == 0 else 50 + (i % 3)} for i, d in enumerate(days)],
            "steps": [{"date": d.isoformat(), "count": 8200 + 450 * i} for i, d in enumerate(days)],
            "exercise": [run(today, 6, 28, 10, 2.6, 148), run(days[3], 17, 40, 0, 3.6, 142), run(days[5], 7, 24, 30, 2.2, 151)],
            "weight": [{"time": at(days[1], 7), "kg": 88.5}]}
    print("stub health:", pca_health.ingest(body), flush=True)


def stub_music():
    """Music search answers from a small made-up catalogue (tools/pca_music never reaches YouTube): titles and durations
    shaped like the real thing, so the page's PLAY/SKIP/LIKE can be driven end to end. Ids are fake."""
    import hashlib
    from tools import pca_music

    def backend(query, n):
        rows = []
        for i in range(6):
            vid = hashlib.md5(f"{query}|{i}".encode()).hexdigest()[:11]
            if "speech" in query:
                rows.append({"id": vid, "title": f"Discipline motivational speech {i + 1}", "channel": "Stub speeches", "duration_s": 240 + 120 * i})
            elif "cadence" in query:
                rows.append({"id": vid, "title": f"Army running cadence {i + 1}", "channel": "Stub cadence", "duration_s": 300 + 240 * i})
            else:
                rows.append({"id": vid, "title": f"{query.title()} - Stub Song {i + 1}", "channel": "Stub Artist", "duration_s": 170 + 15 * i})
        return rows[:n]
    pca_music._backend = backend


def stub_demo():
    """Exercise-video search (tools/pca_demo) answers from a made-up catalogue: three titles per exercise that pass the
    rules, with fake 11-character ids, so "Watch video demo" and "Different video" can be driven without the network.
    Any other search (the music picks) goes to the backend that was there before."""
    import hashlib
    from tools import pca_demo, pca_music
    before = pca_music._backend
    by_query = {q: k for k, qs in pca_demo.QUERIES.items() for q in qs}

    def backend(query, n):
        key = by_query.get(query)
        if not key:
            return before(query, n)
        words = " ".join(g[0] for g in pca_demo.required(key)).title()
        return [{"id": hashlib.md5(f"{query}|{i}".encode()).hexdigest()[:11], "title": f"{words} proper form, part {i + 1}",
                 "channel": "Stub Coach", "duration_s": 60 + 30 * i} for i in range(3)][:n]
    pca_music._backend = backend


def stub_link(stop=None):
    """A fake phone with Jarvis Link: paired as jarvis-link, polling every second, reporting every 2 s."""
    import base64
    import threading
    import time

    from core import users
    from tools import pca_link, pca_music

    entry = {"id": "stub01", "token_hash": "stub" * 16, "name": pca_link.DEVICE, "added": "2026-11-20T07:00", "push": None}
    users.mutate_devices("owner", lambda devices: [d for d in devices if d.get("id") != "stub01"] + [entry])
    b64 = lambda v: base64.urlsafe_b64encode(v.encode()).decode().rstrip("=")      # noqa: E731
    queue = [("Rich Flex", "Drake", "RichFlex001", 239000), ("Country Roads", "John Denver", "CountryRd01", 190000),
             ("Jimmy Cooks", "Drake", "JimmyCooks1", 218000)]
    ph = {"track": None, "playing": False, "pos": 0, "liked": False, "since": 0.0, "q": 0, "mine": False}

    def state():
        t = ph["track"]
        if not t:
            return {"playing": False, "session": True, "package": pca_link.PACKAGE, "ts": time.time()}
        return {"playing": ph["playing"], "session": True, "title": t[0], "artist": t[1], "media_id": f"auto:queue:{ph['q']}:{b64(t[2])}",
                "duration_ms": t[3], "position_ms": ph["pos"], "liked": ph["liked"], "package": pca_link.PACKAGE, "ts": time.time()}

    def play(track, mine):
        ph.update(track=track, playing=True, pos=0, liked=False, since=time.monotonic(), mine=mine)

    def loop():
        last = 0.0
        while not (stop and stop.is_set()):
            with users.acting_as("owner"):
                for c in pca_link.take()["commands"]:
                    n, a, ok, detail = c["name"], c["args"], True, "done"
                    if n == "play_id":
                        play((a.get("title") or "?", a.get("artist") or "", a["video_id"], 200000), True)
                        detail = "Playing (media id)."
                    elif n in ("pause", "play", "toggle"):
                        ok = ph["track"] is not None
                        ph["playing"] = ok and (n == "play" or (n == "toggle" and not ph["playing"]))
                    elif n == "like":
                        ok = ph["track"] is not None
                        ph["liked"] = ok
                    elif n == "next":
                        ph["q"] += 1
                        play(queue[ph["q"] % len(queue)], False)
                    pca_link.report_result({"id": c["id"], "ok": ok, "detail": detail if ok else "Nothing is playing.", "state": state()})
                now = time.monotonic()
                if ph["playing"]:
                    ph["pos"] = min(ph["pos"] + 1000, ph["track"][3])
                    if ph["mine"] and now - ph["since"] > 8:                # Metrolist moves on to its own queue
                        ph["q"] += 1
                        play(queue[(ph["q"] - 1) % len(queue)], False)
                if now - last >= 2:
                    pca_link.report_state(state())
                    pca_music.tick_soon("owner")                            # as core/appserver does on every report
                    last = now
            time.sleep(1)
    threading.Thread(target=loop, daemon=True, name="stub-link-phone").start()


def make_handler(fitness, pca, trainer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.command, self.path))

        def _json(self, obj, code=200):
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            return body if isinstance(body, dict) else {}

        def do_GET(self):
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            if path == "/api/pca":
                return self._json(pca.page_data())
            if path == "/api/fitness":                        # the Trainer page (app/fitness.html) on the same sandbox
                from tools import fitness
                return self._json(fitness.app_data())
            if path == "/api/health/status":
                from tools import pca_health
                return self._json(pca_health.page_status())
            if path == "/api/link/status":
                from tools import pca_link
                return self._json(pca_link.status("owner"))
            if path == "/api/link/apk":
                from tools import pca_link
                apk = pca_link.apk_path()
                if apk is None:
                    return self._json({"detail": "The Jarvis Link app has not been built on this machine yet."}, 404)
                raw = apk.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", 'attachment; filename="JarvisLink.apk"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return None
            target = (APP / (path.lstrip("/") or "index.html")).resolve()
            if APP.resolve() not in target.parents or not target.is_file():
                return self._json({"detail": "Not found"}, 404)
            raw = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            path, body = self.path.split("?", 1)[0], self._body()
            if path == "/api/pca/action":
                message = pca.page_action(body)
                if isinstance(message, dict):
                    return self._json({"ok": True, **message})
                return self._json({"ok": True, "message": message, "data": pca.page_data()})
            if path == "/api/fitness/coach":
                from tools import fitness, trainer
                return self._json({"ok": True, "message": trainer.page_action(body), "data": fitness.app_data()})
            if path == "/api/fitness/checkin":
                def num(k, cast):
                    try:
                        return cast(body[k]) if body.get(k) not in (None, "") else None
                    except (TypeError, ValueError):
                        return None
                msg = fitness.log_checkin(num("sleep_hours", float) or 0, num("energy", int) or 0, num("soreness", int) or 0,
                                          date=trainer._today().isoformat())        # the pinned day, like the trainer
                return self._json({"ok": True, "message": msg})
            if path == "/api/link/paircode":                  # a sandbox code (the real route writes manage.py's codes)
                import secrets
                return self._json({"code": f"{secrets.randbelow(900000) + 100000}", "expires_in": 900})
            if path == "/api/say":
                return self._json({"audio": None})
            return self._json({"detail": "Not found"}, 404)
    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", type=int)
    ap.add_argument("--date", default="", help="YYYY-MM-DD: pin the trainer clock to this day")
    ap.add_argument("--seed", action="store_true", help="pre-answer onboarding (complete, pain 0)")
    ap.add_argument("--keep", default="", help="reuse a sandbox folder an earlier run created")
    ap.add_argument("--stub-music", action="store_true", help="music search answers from a fake catalogue (no network)")
    ap.add_argument("--stub-demo", action="store_true", help="exercise-video search answers from a fake catalogue (no network)")
    ap.add_argument("--stub-link", action="store_true", help="a fake phone running Jarvis Link (paired, connected)")
    ap.add_argument("--stub-health", action="store_true", help="a fake Health Connect sync from the phone (10 days, 3 runs)")
    ap.add_argument("--short-night", type=float, default=7.4, help="with --stub-health: hours slept last night")
    ap.add_argument("--rhr-today", type=int, default=52, help="with --stub-health: today's resting heart rate")
    args = ap.parse_args(argv)
    pinned = date.fromisoformat(args.date) if args.date else None
    box = sandbox(args.keep)
    fitness, pca, trainer = wire(box, pinned)
    if args.stub_music:
        stub_music()
    if args.stub_demo:
        stub_demo()
    if args.seed:
        for qid, value in SEED.items():
            pca.page_action({"action": "answer", "id": qid, "value": value})
        pca.page_action({"action": "answer", "id": "music_prefs",
                         "value": "Dababy baby chief sleepy hollow post Malone and more idc I like good music not country tho lol"})
        pca.page_action({"action": "answer", "id": "music_service", "value": "metrolist"})
        pca.page_action({"action": "audio", "prefs": {"service": "metrolist"}})
    if args.stub_link:
        stub_link()
    if args.stub_health:
        stub_health(pinned or date.today(), args.short_night, args.rhr_today)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(fitness, pca, trainer))
    print(f"PCA dev harness on http://127.0.0.1:{args.port}/captain.html  data={box}  date={pinned or 'real'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
