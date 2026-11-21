"""Background checks: reminders (every 20 s), the homelab watchdog (every 10 min),
price watches (every 6 h), and the morning briefing (the first time you're back at the PC on a morning).

Presence comes from Windows' "last input" time: if the keyboard and mouse were
idle for a couple of hours and you start using the PC between
JARVIS_BRIEFING_FROM and JARVIS_BRIEFING_UNTIL o'clock, Jarvis greets you
with the briefing (once a day).
"""
import ctypes
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable

from core import notify, storage
from core import diag

PRICE_INTERVAL = 6 * 3600
HOMELAB_INTERVAL = 10 * 60
NUDGE_INTERVAL = 5 * 60
LEARN_INTERVAL = 6 * 3600  # one small source per 6h, not continuous GPU work
APEX_PATCH_INTERVAL = 24 * 3600  # small official HTML checks, never during a game
SCHOOL_INTERVAL = 6 * 3600  # after graduation: a review session every 6h (skills are re-tested, not re-taught)
SCHOOL_LEARNING_INTERVAL = 20 * 60  # owner priority (2026-11-04): until JARVIS graduates, study every 20 minutes while he is away
SCHOOL_IDLE_SECONDS = 180
GPU_INTERVAL = 10 * 60  # core/notice: the graphics card's temperature (nvidia-smi), never during a game


OWNER_BACK_SECONDS = 30   # input this recent: he is using the PC, background practice stops


def in_learning_hours(now: datetime | None = None) -> bool:
    """Owner rule (2026-11-05, window 2026-11-21): background learning (school, auto-study, practising what the API taught)
    runs only from JARVIS_LEARN_FROM to JARVIS_LEARN_UNTIL o'clock (default 3-7 am), and only while the PC is idle.
    JARVIS_LEARN_HOURS=always lifts the hour limit."""
    if os.getenv("JARVIS_LEARN_HOURS", "").lower() == "always":
        return True
    start, end = int(os.getenv("JARVIS_LEARN_FROM", "3")), int(os.getenv("JARVIS_LEARN_UNTIL", "7"))
    hour = (now or datetime.now()).hour
    return start <= hour < end if start < end else (hour >= start or hour < end)


def school_graduated() -> bool:
    """Cheap check (reads the report card file; builds nothing, calls no model) so the loop can pick its cadence."""
    try:
        import json
        from tools import pcskills
        with open(os.path.join(pcskills.root(), "school", "school_transcript.json"), encoding="utf-8") as f:
            return bool(json.load(f).get("graduated"))
    except (OSError, ValueError, ImportError):
        return False
AWAY_SECONDS = 2 * 3600


ROLE = os.getenv("JARVIS_ROLE", "all")  # all (one machine) | desk (PC next to an always-on server) | server


def owns_shared_learning() -> bool:
    """Whether this machine is the one writer of the synced learning stores: the server when there is one (the
    desk PC leaves them to it, like prices, nudges and Garmin), otherwise the only machine there is."""
    return ROLE != "desk"


def idle_seconds() -> float:
    """Seconds since the owner last used the keyboard, mouse or a game controller (Windows' own idle timer misses pads)."""
    if not hasattr(ctypes, "windll"):  # the Linux server has no keyboard/mouse presence
        return 0.0
    from core import inputwatch
    return inputwatch.idle_seconds()


class Scheduler:
    def __init__(self, say: Callable[[str], None], ask: Callable[[str], None]):
        """say: speak a fixed sentence. ask: have Jarvis respond to a prompt (briefing)."""
        self.say = say
        self.ask = ask
        if ROLE != "server":
            from core import attention
            attention.set_voice(say)       # every unprompted sentence goes through ONE judge (core.attention)
        self.briefing_enabled = os.getenv("JARVIS_BRIEFING", "on").lower() != "off"
        self.window = (int(os.getenv("JARVIS_BRIEFING_FROM", "5")), int(os.getenv("JARVIS_BRIEFING_UNTIL", "12")))
        self._apex_patch_retry=0.0

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="scheduler").start()

    _jobs: dict = {}

    def _spawn(self, name: str, target) -> bool:
        """One thread per background job at a time: a homelab check that hangs on the network must not stack a new
        thread every 10 minutes behind it (the loop restarted each job on its interval regardless)."""
        previous = self._jobs.get(name)
        if previous is not None and previous.is_alive():
            diag.swallowed(f"scheduler {name} still running", RuntimeError("previous run has not finished"), every=3600)
            return False
        thread = threading.Thread(target=target, daemon=True, name=f"jarvis-{name}")
        self._jobs[name] = thread
        thread.start()
        return True

    def _loop(self) -> None:
        from tools import proactive

        last_price_check = 0.0
        last_homelab_check = 0.0
        last_nudge_check = 0.0
        last_study_check = 0.0
        last_apex_patch_check = 0.0
        last_school_check = 0.0
        was_away = True  # starting Jarvis (e.g. at sign-in) counts as arriving
        while True:
            try:
                self._reminders()
                background = ROLE != "desk"  # with an always-on server, the PC leaves these jobs to it
                if background and time.time() - last_price_check > PRICE_INTERVAL:
                    last_price_check = time.time()
                    self._spawn("prices", self._prices)
                if background and datetime.now().hour >= 5 and storage.load_global("scheduler_garmin.json", {}).get("day") != date.today().isoformat():
                    self._spawn("garmin", self._garmin)
                if background and time.time() - last_homelab_check > HOMELAB_INTERVAL:
                    last_homelab_check = time.time()
                    self._spawn("homelab", self._homelab)
                    self._spawn("links", self._links)
                if background and time.time() - last_nudge_check > NUDGE_INTERVAL:
                    last_nudge_check = time.time()
                    self._spawn("nudges", self._nudges)
                # The study stores (household/autolearn, the engineering library, research/state.sqlite3) are synced
                # between the machines, so ONE machine writes them: the always-on server when there is one, else
                # this one (see owns_shared_learning). Both machines studying at once, at the same 6 h cadence and
                # again at every restart, made a Syncthing conflict copy of each store every time (live 2026-10-30).
                if (owns_shared_learning() and os.getenv("JARVIS_AUTOSTUDY", "on").lower() != "off" and in_learning_hours()
                    and time.time()-last_study_check>LEARN_INTERVAL):
                    last_study_check=time.time()
                    self._spawn("local-study", self._study)
                # Read-only EA news/hotfix discovery, local PC store; never train
                # a model or overwrite verified Legend kits from raw web text.
                if (ROLE != "server" and os.getenv("JARVIS_APEX_PATCH_WATCH", "on").lower() != "off"
                    and (time.time()-last_apex_patch_check>APEX_PATCH_INTERVAL
                         or (self._apex_patch_retry and time.time()>self._apex_patch_retry))):
                    last_apex_patch_check=time.time()
                    self._apex_patch_retry=0.0
                    self._spawn("apex-patch-watch", self._apex_patch_watch)
                idle = idle_seconds()
                # The PC-skill school (jarvis_pc/school): the PC is the machine whose skills these are, so the server never runs
                # it. Only while the person is away, so the local model never competes with them or a game for the machine.
                if (ROLE != "server" and os.getenv("JARVIS_SCHOOL", "on").lower() != "off" and in_learning_hours() and idle > SCHOOL_IDLE_SECONDS
                        and time.time() - last_school_check > (SCHOOL_INTERVAL if school_graduated() else SCHOOL_LEARNING_INTERVAL)):
                    last_school_check = time.time()
                    self._spawn("pc-school", self._school)
                # Mark VI C12: the PC works on the owner's missions from the laptop's queue (core/missionworker: its own gate
                # and backoff decide whether anything runs; one mission at a time).
                self._missions()
                # The laptop (server) coordinates learning: it ranks the owner's real misses into the shared queue every hour;
                # the PC practises the top one when idle (Scheduler._school -> core.acquire.practice_once).
                if (ROLE == "server" and os.getenv("JARVIS_ACQUIRE", "on").lower() != "off"
                        and time.time() - getattr(self, "_acquire_ranked", 0) > 3600):
                    self._acquire_ranked = time.time()
                    try:
                        from core import acquire
                        acquire.save_queue(acquire.discover())
                    except Exception as exc:  # noqa: BLE001
                        diag.swallowed("skill acquisition queue", exc, every=3600)
                returned = False
                if idle > AWAY_SECONDS:
                    was_away = True
                elif was_away and idle < 60:
                    was_away = False
                    returned = True
                    self._maybe_brief()
                if ROLE != "server":
                    self._attention(returned)
                    self._notice(returned)
                    self._protocols(returned)
                    self._partner_report()
                self._commitments()
                self._outside()          # Storm Watch: both machines (the PC speaks, the laptop pings the phone)
            except Exception as e:
                print(f"[scheduler] {type(e).__name__}: {e}")
            time.sleep(20)

    def _missions(self) -> bool:
        """Spawn one mission-worker pass: never on the server (the laptop holds the queue), only when this machine reaches
        the laptop's queue (tools.vault._remote: JARVIS_ROLE=desk + server URL + token). JARVIS_MISSIONS=off switches it off."""
        if ROLE == "server" or os.getenv("JARVIS_MISSIONS", "on").lower() == "off":
            return False
        from tools import vault
        if vault._remote() is None:
            return False
        from core import missionworker
        return self._spawn("missions", missionworker.tick)

    def _school(self) -> None:
        """One short, bounded study session of the PC-skill school: free local models first, the paid API only for what they
        could not learn and only under its daily cap. Unattended and sandboxed (see jarvis_pc/school/school.py)."""
        try:
            from tools import pcskills
            school = pcskills.school()
            was_graduated = bool(school.transcript.graduated())
            report = school.study(max_subjects=4, seconds=900)
            print("[school] " + report.render().replace(chr(10), " | ")[:400])
            try:                                           # then the owner's real misses: re-studied, decide-only, never run
                from core import misslessons
                # stops the moment he is back at the PC (owner 2026-11-21: "when the PC is being used, it will stop")
                rows = misslessons.review(limit=6, seconds=300, cancelled=lambda: idle_seconds() < OWNER_BACK_SECONDS)
                if rows:
                    print("[school] " + misslessons.summary(rows))
                taught = misslessons.teach(limit=2)        # still not understood after two nights: one lesson, local teacher first
                if taught:
                    print(f"[school] lessons asked for {len(taught)} misses; now understood: {sum(1 for t in taught if t.get('taught_as'))}")
            except Exception as exc:  # noqa: BLE001
                diag.swallowed("miss lessons", exc, every=3600)
            if os.getenv("JARVIS_ACQUIRE", "on").lower() != "off":
                try:                                       # then ONE bounded self-teaching cycle on a real miss, in the isolated lab
                    from core import acquire
                    record = acquire.practice_once()
                    if record:
                        print("[acquire] " + acquire.summary(record))
                    from core import learned
                    learned.tell_pending()                 # a learned skill he was not told about yet (the push failed): once
                except Exception as exc:  # noqa: BLE001
                    diag.swallowed("skill acquisition", exc, every=3600)
            try:                                           # the numbers behind "how independent are you?" (dashboard data)
                from core import independence
                independence.save()
            except Exception as exc:  # noqa: BLE001
                diag.swallowed("independence report", exc, every=3600)
            if report.graduated and not was_graduated:
                self.say("I have finished school. Every PC skill on my list is mastered and proven. I am ready to work for you.")
        except Exception as exc:  # noqa: BLE001
            diag.swallowed("pc-school session", exc, every=3600)

    def _study(self) -> None:
        """Index ONE reference using the restricted existing offline library.

        No model training, paid API or GPU work. A downloaded reference is
        marked indexed, never automatically promoted into verified facts.
        """
        from core import autolearn
        try:
            for topic in ("communication","psychology","computers","apex"):
                autolearn.queue_topic(topic)
            autolearn.study_once()
        except (OSError,RuntimeError,ValueError,ImportError) as exc:
            print(f"[autostudy] {type(exc).__name__}")
        # Additive and disabled by default. Failure never disables existing study.
        try:
            from core.tutorial_idle import once
            once()
        except Exception as exc:
            print(f"[tutorial-study] {type(exc).__name__}")

    def _apex_patch_watch(self) -> None:
        from core import gamecoach_patch_watch
        try:
            result=gamecoach_patch_watch.scan()
            if result.get("status")=="deferred_game_running":
                self._apex_patch_retry=time.time()+60
            if result.get("status")=="checked":
                # Also recheck the latest saved article by SHA when queue is empty.
                gamecoach_patch_watch.study_next()
        except (OSError,RuntimeError,ValueError,ImportError) as exc:
            print(f"[apex-patch-watch] {type(exc).__name__}")

    def _reminders(self) -> None:
        """Everyone's personal reminders, then the household's.

        One machine owns them: the always-on server pops each due reminder, notifies the phone and asks the PC
        to say it. (Both machines used to pop the same synced file, so a reminder was either spoken or pushed,
        whichever got there first.) The PC only steps in for reminders over two minutes late: the server is down."""
        from core import users
        from tools import proactive

        now = datetime.now() - (timedelta(minutes=2) if ROLE == "desk" else timedelta(0))
        for uid in users.all_users():
            with users.acting_as(uid):
                for r in proactive.due_reminders(now):
                    self._fire("Reminder", r["text"], uid, f"A reminder, {users.title(uid)}: {r['text']}.")
        with users.acting_as("owner"):
            for r in proactive.due_reminders(now, file=proactive.HOUSEHOLD_REMINDERS):
                self._fire("Household reminder", r["text"], None, f"A household reminder: {r['text']}.")

    def _fire(self, title: str, text: str, uid, spoken: str) -> None:
        notify.notify(title, text, uid, "reminders")   # the PC forwards this to the server when it's the backup
        if ROLE == "server":
            try:
                from core import pcbridge, users
                with users.acting_as("owner"):
                    pcbridge.json_request("/api/pc/announce", "POST", {"text": spoken, "for": uid or "owner"})
                return
            except Exception:
                pass   # the PC is off or asleep: the phone notification is enough
        from core import attention
        attention.deliver(spoken, attention.HIGH, key="reminder:" + text)

    def _prices(self) -> None:
        from core import users
        from tools import proactive

        for uid in users.all_users():
            with users.acting_as(uid):
                for alert in proactive.check_prices():
                    notify.notify("Price drop", alert, uid, "alerts")
                    from core import attention
                    attention.deliver(f"Good news, {users.title(uid)}: {alert}", attention.NORMAL, key="price:" + alert)

    def _garmin(self) -> None:
        """Once a morning: pull last night's sleep, HRV and recent runs for everyone who connected Garmin."""
        from core import users
        from tools import fitness

        storage.save_global("scheduler_garmin.json", {"day": date.today().isoformat()})
        for uid in users.all_users():
            with users.acting_as(uid):
                folder = fitness.token_dir()
                if folder.exists() and any(folder.iterdir()):
                    try:
                        fitness.garmin_sync(3)
                    except Exception as e:
                        print(f"[garmin] {uid}: {type(e).__name__}: {str(e)[:120]}")

    def _homelab(self) -> None:
        """Push new homelab problems (and their recovery) to the owner's phone, once each."""
        from tools import homelab

        try:
            problems = homelab.health_problems()
        except Exception as e:
            problems = {"unreachable": f"Can't reach the homelab Pi ({type(e).__name__})"}
        state = storage.load_global("homelab_watch.json", {})
        known = state.get("problems", {})
        new = {k: v for k, v in problems.items() if k not in known}
        gone = [v for k, v in known.items() if k not in problems]
        if new:
            hint = " Say 'Jarvis, repair the drive' to fix it." if any(k.startswith("mount:") for k in new) else ""
            notify.notify("Homelab problem", "; ".join(new.values()) + "." + hint, "owner", "alerts")
        if gone:
            notify.notify("Homelab OK again", "Fixed: " + "; ".join(gone) + ".", "owner", "alerts")
        if new or gone:
            storage.save_global("homelab_watch.json", {"problems": problems, "checked": datetime.now().isoformat()})

    def _links(self) -> None:
        """A rejected PC<->laptop pairing breaks things silently: tell the owner once (and once it's fixed)."""
        try:
            from core import appserver
            problems = appserver.link_problems()
        except Exception:
            return
        state = storage.load_global("link_watch.json", {})
        if problems and problems != state.get("problems"):
            notify.notify("Jarvis link broken", " ".join(p.removeprefix("Jarvis link: ") for p in problems), "owner", "alerts")
        elif not problems and state.get("problems"):
            notify.notify("Jarvis link OK again", "The PC and the laptop can reach each other again.", "owner", "alerts")
        if problems != state.get("problems"):
            storage.save_global("link_watch.json", {"problems": problems, "checked": datetime.now().isoformat()})

    def _nudges(self, now: datetime | None = None) -> None:
        """Once-a-day training nudges and the Sunday weekly report, for each person (the server runs these)."""
        from core import users
        now = now or datetime.now()
        hm, today = now.strftime("%H:%M"), now.date().isoformat()
        for uid in users.all_users():
            with users.acting_as(uid):
                sent = storage.load("notify_state.json", {})
                due = []
                try:
                    from tools import fitness
                    data = fitness.app_data()
                    plan_today = data.get("today") or {}
                    has_session = bool(plan_today.get("items"))
                    logged = any(str(w.get("date", "")).startswith(today) for w in data.get("workouts", []))
                    if has_session and not data.get("checkin") and "08:30" <= hm < "11:00" and sent.get("checkin") != today:
                        due.append(("checkin", "Morning check-in", "Ten seconds: sleep, energy, soreness. It tunes today's "
                                    f"session ({plan_today.get('title', 'training')})."))
                    if has_session and not logged and "18:00" <= hm < "21:00" and sent.get("workout") != today:
                        due.append(("workout", "Today's workout is waiting",
                                    f"{plan_today.get('title', 'Your session')} isn't logged yet. Still time — or log it if you did it."))
                except Exception as error:
                    diag.swallowed("nudges fitness", error)
                week = now.date().isocalendar()
                week_id = f"{week[0]}-W{week[1]}"
                if now.weekday() == 6 and "18:00" <= hm < "22:00" and sent.get("weekly") != week_id:
                    try:
                        from tools import life
                        due.append(("weekly", "Your week with Jarvis", life.weekly_report()))
                    except Exception as error:
                        diag.swallowed("nudges weekly", error)
                for key, title, body in due:
                    sent[key] = week_id if key == "weekly" else today
                    notify.notify(title, body, uid, "weekly" if key == "weekly" else "training")
                if due:
                    storage.save("notify_state.json", sent)

    def _attention(self, returned: bool) -> None:
        """Say what was held: when he is back, when a game ends, or when the rate limit allows again; plus PC news."""
        from core import attention
        attention.read_inbox()
        sit = attention.situation()
        playing = bool(sit.get("gaming"))
        if getattr(self, "_was_gaming", False) and not playing:
            attention.welcome_back(sit, "game")
        elif returned:
            attention.welcome_back(sit, "away")
        elif sit.get("present") and not playing and time.time() - getattr(self, "_last_flush", 0) > 600:
            if any(h["urgency"] != attention.LOW for h in attention.held()):
                attention.welcome_back(sit, "later")
                self._last_flush = time.time()
        self._was_gaming = playing
        self._sit = sit
        if time.time() - getattr(self, "_disk_check", 0) > 3600:
            self._disk_check = time.time()
            try:
                import shutil
                free = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\").free / 2 ** 30
                from core import notice
                if notice.active():
                    notice.disk(free)     # one keyed slot: said once (not again for every gigabyte), withdrawn when space is back
                elif free < 15:
                    attention.deliver(f"Your system drive is almost full: {free:.0f} GB left. Say 'clean up' and I'll free "
                                      "space.", attention.HIGH, key=f"disk:{int(free)}")
            except OSError:
                pass

    def _notice(self, returned: bool) -> bool:
        """Plan upgrade 9 (core/notice), PC voice process only: on his return his journal's open items (a file read); every
        10 min the graphics card's measured temperature; then one gated pass, which calls the model only when a slot changed
        and every gate passed. JARVIS_NOTICE=off switches it off."""
        from core import notice
        if not notice.active():
            return False
        if returned:
            notice.open_items()
        check_gpu = time.time() - getattr(self, "_gpu_check", 0) > GPU_INTERVAL
        if not check_gpu and not notice.slots():
            return False                   # nothing published, nothing to measure: no thread every 20 s
        if check_gpu:
            self._gpu_check = time.time()
        gaming = bool((getattr(self, "_sit", None) or {}).get("gaming"))

        def run() -> None:
            if check_gpu and gaming:
                notice.gpu(None, gaming=True)      # heat is expected: withdrawn, and nvidia-smi is never started over a game
            elif check_gpu:
                from tools import system
                notice.gpu(system._gpu_reading())
            notice.tick()
        return self._spawn("notice", run)

    def _protocols(self, returned: bool) -> None:
        """The owner's APPROVED protocols with a time-of-day or came-back trigger (core/protocols: data, not a method here).
        An unapproved one never runs on its own. JARVIS_PROTOCOLS=off switches the triggers off (his spoken phrase still runs)."""
        if os.getenv("JARVIS_PROTOCOLS", "on").lower() == "off":
            return
        try:
            from core import protocols
            protocols.tick(returned, spawn=self._spawn)
        except Exception as error:  # noqa: BLE001
            diag.swallowed("protocol triggers", error, every=3600)
        self._habits()

    def _habits(self) -> None:
        """Habit learning (core/habits): every minute note which apps' windows appeared (process names only), once a day
        look for a routine and SUGGEST a protocol for it (it never runs until he approves it). JARVIS_HABITS=off."""
        if os.getenv("JARVIS_HABITS", "on").lower() == "off":
            return
        try:
            from core import habits
            if time.time() - getattr(self, "_habits_seen", 0) >= 60:
                self._habits_seen = time.time()
                habits.observe()
            habits.tick()
        except Exception as error:  # noqa: BLE001
            diag.swallowed("habit learning", error, every=3600)

    def _outside(self) -> None:
        """Protocols triggered by outside data (core/protocols.outside_tick: Storm Watch, every 5 minutes, on its own
        thread). JARVIS_PROTOCOLS=off or JARVIS_STORMWATCH=off switches it off."""
        if os.getenv("JARVIS_PROTOCOLS", "on").lower() == "off":
            return
        try:
            from core import protocols
            protocols.outside_tick(spawn=self._spawn)
        except Exception as error:  # noqa: BLE001
            diag.swallowed("outside-data protocol triggers", error, every=3600)

    def _maybe_brief(self) -> None:
        if ROLE == "server":  # the spoken morning briefing happens at the desk
            return
        from tools import gaming

        if not self.briefing_enabled or gaming.game_mode():
            return
        now = datetime.now()
        if not (self.window[0] <= now.hour < self.window[1]):
            return
        # The PC's morning briefing is for its owner; others can ask "brief me" any time.
        from core import users

        users.set_current("owner")
        state = storage.load("scheduler.json", {})
        if state.get("last_briefing") == date.today().isoformat():
            return
        state["last_briefing"] = date.today().isoformat()
        storage.save("scheduler.json", state)
        self._briefed_at = time.time()       # the goals report (_partner_report) waits until this briefing has been said
        self.ask("[Automatic morning briefing: the user just sat down at the PC. Greet them for the "
                 "morning and give a short spoken briefing: call briefing_info, get today's weather "
                 "with get_weather, mention today's reminders and to-dos, suggest an outfit from their "
                 "wardrobe for the weather, any language words due, and (via homelab_health) any homelab problem in one "
                 "sentence; say nothing about the homelab if it's all good. Say nothing about goals, missions or what you "
                 "promised: a separate report follows. Keep it under 30 seconds.]")

    BRIEF_GAP_S = 75          # the model's briefing (<30 s spoken) goes first; the goals report follows it, never over it
    COMMITMENTS_INTERVAL = 5 * 60

    def _partner_report(self, now: datetime | None = None) -> str:
        """Partner stage 2.3: the owner's morning report, built in code (core/commitments.morning_report): his goals, what
        finished or failed, what's still owed, what waits on him. Once a day, never 22:00-07:00, never during a game, only
        while he is at the PC; spoken through core.attention. It sits beside the model-written briefing rather than inside
        it (a model retelling 'not done' is how results get invented), and waits BRIEF_GAP_S after that briefing was asked
        for. JARVIS_PARTNER_REPORT=off switches it off."""
        if ROLE == "server" or os.getenv("JARVIS_PARTNER_REPORT", "on").lower() == "off":
            return ""
        if time.time() - getattr(self, "_briefed_at", 0) < self.BRIEF_GAP_S:
            return ""
        now = now or datetime.now()
        if now.hour >= 22 or now.hour < 7:
            return ""
        try:
            from core import attention, commitments, protocols, users
            sit = getattr(self, "_sit", None) or attention.situation()
            uid = protocols._owner()
            if uid is None:
                return ""
            with users.acting_as(uid):
                return commitments.deliver_report(now, sit)
        except Exception as error:  # noqa: BLE001
            diag.swallowed("partner report", error, every=3600)
            return ""

    def _commitments(self) -> None:
        """Every 5 min: each person's commitments resolved from their linked records (core/commitments.resolve). Both
        machines: the laptop resolves the missions it queued, the PC what it can read."""
        if os.getenv("JARVIS_PARTNER_REPORT", "on").lower() == "off":
            return
        if time.time() - getattr(self, "_commitments_at", 0) < self.COMMITMENTS_INTERVAL:
            return
        self._commitments_at = time.time()

        def run() -> None:
            from core import commitments, users
            for uid in users.all_users():
                with users.acting_as(uid):
                    try:
                        commitments.resolve()
                    except Exception as error:  # noqa: BLE001
                        diag.swallowed("commitments resolve", error, every=3600)
        self._spawn("commitments", run)
