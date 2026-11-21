"""J.A.R.V.I.S. - run with:  python main.py   (see README.md for options)"""
import argparse
import contextlib
import faulthandler
import os
import re
import sys
import threading
import time

from core.config import ROOT, Config, load_personality
from core import diag  # also loads .env
from audio import echo

# "Hey Jarvis" at the start of a command, incl. how Whisper mishears the name ("hey charvis") and a
# leading filler ("No, hey Jarvis"). Only stripped when the name itself is there.
WAKE_PREFIX = re.compile(r"^\W*((no|yo|um|uh|so|oh)\W+)?((hey|hi|ok|okay|yo)\W+)?"
                         r"(jarvis|charvis|jervis|javis|jarvas|jarves|jarvus)\b\W*", re.IGNORECASE)


def without_wake(text: str) -> str:
    """The command without the wake word, however many times he said it: "Hey Jarvis. Hey Jarvis. Minimize Claude." kept
    one "Hey Jarvis." and the line was decided as a close (live 2026-11-08)."""
    out = (text or "").strip()
    for _ in range(5):
        cut = WAKE_PREFIX.sub("", out, count=1).strip()
        if cut == out:
            break
        out = cut
    return out


# Whole-command mishears seen in the log. "pair" is short and gets heard as similar words; Jarvis
# can't repair a phone, so these always mean pairing. Kept exact so nothing else is rewritten.
MISHEARD = [(re.compile(r"^(?:re\s*pair|prepare|pierre|hear|pear|bear|pare)\s+(my|the|a)\s+(phone|iphone)\W*$", re.I),
             r"pair \1 \2"),
            # "Yes, we zoom YouTube" (holdout h004): Whisper's "resume". Only with what is playing named, the whole line.
            (re.compile(r"^(?:(?:yes|yeah|okay|ok)[,.]?\s+)?(?:we|re|ree)[\s-]?zoom\s+(youtube|the video|the song|the music|spotify)\W*$", re.I),
             r"resume \1"),
            # "And stream." / "And the stream." (2026-11-20, four times in a row, then "Hey Jarvis. End. The stream."): Whisper's
            # "end"; the whole line only ("And streaming protocol." is his order to START it)
            (re.compile(r"^(?:and|end)[.,]?\s+(?:the\s+)?stream[.!]?$", re.I), "end the stream")]


def fix_misheard(command: str) -> str:
    for pattern, replacement in MISHEARD:
        if pattern.match(command.strip()):
            return pattern.sub(replacement, command.strip())
    return command
SHUTDOWN = {"goodbye", "shut down", "power down"}
ECHO_WINDOW_S = 1.5  # a command that starts this soon after he stops talking is checked against his own words
STOP_WORDS = {"stop", "wait", "hold on", "shh", "quiet", "jarvis stop", "okay stop", "ok stop",
              "never mind", "nevermind", "never mind jarvis", "cancel", "cancel that", "forget it",
              "tann", "rete", "kanpe", "tann yon ti moman", "kite sa", "pa bezwen", "anile",
              "arrête", "attends", "chut", "laisse tomber", "annule"}
TRAILING_WORDS = {"to", "the", "a", "an", "and", "or", "but", "of", "for", "with", "my", "your",
                  "in", "on", "at", "um", "uh", "so", "because", "like", "if", "is", "me", "that",
                  "this", "can", "could", "would", "about", "from", "then", "what", "how", "um,"}
INTERPRETER_STOP = ("stop translating", "stop interpreting", "stop the interpreter", "interpreter off",
                    "sispann tradui", "fini tradui", "arrête de traduire", "arrêtez de traduire")
# "...thank you jarvis you can go to sleep for now" is for Jarvis, not the PC (the brain once made it a PC sleep).
GO_TO_SLEEP = re.compile(r"\b(?:you can |go ahead and )?go (?:back )?to sleep(?: now| for now)?$")
# ... but "make the PC go to sleep" / "let the laptop go to sleep" is the machine's sleep, never his goodbye (review 2026-11-21)
_MACHINE_SLEEP = re.compile(r"\b(?:pc|computer|laptop|machine|monitors?|screens?|display|desktop|tv)\b[^.!?]{0,20}"
                            r"\bgo (?:back )?to sleep\b")
DISMISS = {"thanks", "thank you", "thanks jarvis", "thank you jarvis", "go to sleep",
           "stand by", "that's it", "nothing", "no", "no thanks", "nope",
           "mèsi", "mesi", "mèsi anpil", "mesi anpil", "sa se tout", "sa bon", "oke mèsi", "ok mèsi",
           "merci", "merci beaucoup", "c'est tout", "ça ira", "ça va aller", "non merci"}
LANG_BADGE = {"en": "EN", "fr": "FRANÇAIS", "ht": "KREYÒL"}


def unprompted_for() -> str:
    """Who an unprompted line is for: the person the caller is acting as (core.scheduler delivers each household member's
    reminder and price alert inside users.acting_as(uid), and the PC's /api/pc/announce does the same for the server's),
    else the owner (timers, game mode, household reminders). Never simply the last person who spoke: one person's reminder
    landed in another's awareness log, conversation and journal (review 2026-11-10)."""
    from core import users
    uid = users.current()
    return uid if uid in users.all_users() else "owner"


def quiet_start(now=None) -> bool:
    """Night (22:00-07:00 local, JARVIS_QUIET_START_HOURS="22-7" to change it, "off" to always speak): the start-up
    greeting is shown, not spoken. 2026-11-10: updates are deployed while the owner sleeps."""
    hours = os.getenv("JARVIS_QUIET_START_HOURS", "22-7").strip().lower()
    if hours in ("off", "0", "no", ""):
        return False
    try:
        start, end = (int(x) for x in hours.split("-", 1))
    except ValueError:
        start, end = 22, 7
    h = (now or time.localtime()).tm_hour
    return (start <= h or h < end) if start > end else (start <= h < end)


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. voice assistant")
    parser.add_argument("--text", action="store_true", help="type instead of talking")
    parser.add_argument("--mute", action="store_true", help="print replies instead of speaking")
    parser.add_argument("--no-wake", action="store_true", help="press Enter instead of saying 'Hey Jarvis'")
    parser.add_argument("--no-hud", action="store_true", help="don't show the on-screen HUD")
    parser.add_argument("--companion", action="store_true",
                        help="turn the transparent desktop companion on (off by default since 2026-11-19; JARVIS_COMPANION=1 in .env "
                             "turns it on every launch; see DEVELOPMENT-HANDOFF.md 'Living Desktop Companion')")
    parser.add_argument("--no-companion", action="store_true", help="panel only, no desktop companion this run")
    parser.add_argument("--background", action="store_true", help="no console (used by start-with-Windows)")
    args = parser.parse_args()

    if args.background or sys.stdout is None:
        log = open(ROOT / "data" / "jarvis.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"\n===== Jarvis started {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    if not args.text and not _single_instance():
        print("Jarvis is already running (see the tray icon).")
        return
    if not args.text:
        _start_watchdog()
    beside_live = args.text and _another_instance()
    if beside_live:
        _second_process()

    cfg = Config()

    from audio.tts import Speaker
    from brain.llm import Brain
    from core import hud
    from tools import eyes, languages, load_all, memory, system

    if sys.platform == "win32":
        # UI Automation's one COM object must live in the MTA before any thread touches pywinauto, or a UI step on a
        # worker thread can wait forever on a busy STA thread (jarvis_pc/uiact/uia.py ensure_mta).
        from jarvis_pc.uiact import uia as _uia
        _uia.ensure_mta()
    load_all()
    if not args.text and not args.no_hud:
        # --companion opts into the transparent desktop character; the
        # default stays "panel" so an unflagged launch is byte-for-byte
        # identical to prior behavior.
        hud.start(mode="companion" if (args.companion or (cfg.companion and not args.no_companion)) else "panel")

    speaker = Speaker(cfg.voice, cfg.voice_rate, muted=args.mute, kreyol_voice=cfg.kreyol_voice,
                      french_voice=cfg.french_voice, other_voice=cfg.other_voice)
    # Back from sleep: the GPU voice's CUDA context and the local model's warm cache start over (core/resume.py).
    from audio import tts as _tts
    from core import intelligence, resume
    resume.on_resume("kokoro", _tts.kokoro_reset)
    resume.on_resume("local-model-cache", intelligence._last_local.clear)
    resume.start()
    try:   # changed OLLAMA_* options in .env (cache slots...) need a server restart, which only this elevated process can do
        intelligence.apply_ollama_settings()
    except Exception as e:  # noqa: BLE001 - the old server keeps working
        print(f"[intelligence] could not apply the local model server's settings: {e}")
    # VRAM budget: models the local tiers never use (the pinned planner model) go now, not at the first wake word.
    threading.Thread(target=lambda: intelligence.reclaim_vram(), daemon=True, name="vram-reclaim").start()
    # Keep his own models loaded (the first sentence after a break was 14 s cold, 4 s warm) and free the card for games.
    from core import warmth
    warmth.start()
    if sys.platform == "win32":        # the first screen read paid 14 s for OCR model load; pay it now, off the turn
        from core import pcvision
        threading.Thread(target=pcvision.warm_up, daemon=True, name="ocr-warm-up").start()
        # The tab bridge for the owner's Edge (core/browserbridge): listening early so the extension is connected before a
        # request needs it. Inert until he loads the extension; never fatal.
        from core import browserbridge
        browserbridge.start()
        # The phone's journal and thread reach this PC only while Syncthing runs; closing its console stopped it for two
        # days (2026-11-09). Started again from its own scheduled task when it is gone (core/syncwatch).
        if not beside_live:      # the running Jarvis already watches it
            from core import syncwatch
            syncwatch.start()

    def say(text: str) -> None:
        if text.strip().startswith("[ignore]"):  # overheard speech not meant for Jarvis
            print("  (ignored: not addressed to Jarvis)")
            return
        print(f"JARVIS: {text}")
        hud.line("jarvis", text)
        speaker.speak(text)

    system.announce = lambda msg: say(f"{cfg.user_title.capitalize()}, {msg[0].lower()}{msg[1:]}")
    system.speak = say   # a finished sentence, as is (reminders relayed by the always-on server)
    languages.speaker = speaker
    from core import storage

    storage.migrate_single_user()  # data from before Jarvis knew several people -> the owner's folder
    brains: dict = {}

    def earcon() -> None:
        """A soft, low, wordless cue while a PC request is still being understood (not the higher 'listening' chime)."""
        try:
            from audio.tts import beep
            beep(freq=520, duration=0.09, wait=False)
        except Exception as error:  # noqa: BLE001 - a missing cue never costs the turn
            diag.swallowed("earcon", error)

    def brain_for(uid: str) -> Brain:
        """Each household member gets their own conversation, addressed to them."""
        if uid not in brains:
            b = Brain(cfg.model, cfg.effort, load_personality(cfg, uid), cfg.conversation_timeout, uid=uid)
            b.attach = eyes.eyes.attachment  # while eyes are on, every message carries a live frame
            b.context = memory.context_block  # date + their memories at the start of each conversation
            b.earcon = None if (args.text or args.mute) else earcon   # the voice only: the phone and typed chat have no cue
            brains[uid] = b
        return brains[uid]

    brain = brain_for("owner")
    brain.brain_for = brain_for

    # PC commands must not wait for Whisper, wake-word or microphone initialization.
    app_server = None
    if not args.text:
        from core.appserver import AppServer

        # One Brain per person on this machine (plan upgrade 10): the companion's typed chat and /api/chat on the PC are
        # the same conversation as his voice, serialized by that Brain's lock ("close it" after a typed "open Paint").
        def verify_brain(uid: str) -> Brain:
            """A developer's verification device ('claude-verify', docs/ENGINEERING-PROTOCOL.md) talks to a Brain of its
            own: its turns never enter his voice conversation, never hold his spoken turn, and never ask what his next
            'yes' or 'no' answers (review 2026-11-11)."""
            b = Brain(cfg.model, cfg.effort, load_personality(cfg, uid), cfg.conversation_timeout, uid=uid)
            b.context = memory.context_block
            return b

        app_server = AppServer(brain_for, verify_brain=verify_brain)
        app_server.start()

    try:
        if args.text:
            text_loop(brain, speaker, say)
        else:
            try:
                voice_loop(cfg, brain, speaker, say, use_wake_word=not args.no_wake, app_server=app_server)
            except Exception as error:
                if app_server is None:
                    raise
                print(f"[voice] unavailable ({type(error).__name__}: {error}); PC remote control remains online.")
                threading.Event().wait()  # quit-flag watcher can still stop the process
    finally:
        eyes.eyes.stop()
        hud.stop()


def _start_watchdog() -> None:
    """He is up on purpose: clear the stopped marker, tell him about a crash restart, and make sure the crash watchdog runs
    (detached, so it outlives him). JARVIS_WATCHDOG=off disables the launch."""
    try:
        from core import watchdog
        watchdog.clear_stopped()
        watchdog.ensure_running()
        threading.Timer(25, lambda: watchdog.announce_pending()).start()    # after the HUD and tray are up
    except Exception as error:
        print(f"[watchdog] {type(error).__name__}: {error}")


MUTEX = "Local\\JarvisAssistantMutex"


def _single_instance() -> bool:
    """Only one voice Jarvis at a time (e.g. start-with-Windows plus a manual start)."""
    if sys.platform != "win32":
        return True
    import ctypes

    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX)
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _another_instance(name: str = MUTEX) -> bool:
    """A voice Jarvis runs now (its single-instance mutex exists), seen WITHOUT taking the mutex: a --text session must
    never stop the voice Jarvis from starting after it. Held by the elevated Jarvis, opening it is denied: it exists."""
    if sys.platform != "win32":
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.restype = ctypes.c_void_p
    handle = kernel32.OpenMutexW(0x00100000, False, name)          # SYNCHRONIZE
    if handle:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return True
    return ctypes.get_last_error() == 5                             # ERROR_ACCESS_DENIED


def _second_process() -> None:
    """main.py --text beside the running Jarvis (review 2026-11-11): the thread ledger has one writer per machine
    (core.thread rewrites thread-pc.jsonl in place), so this console session writes no rows; the live one does."""
    os.environ["JARVIS_THREAD"] = "off"
    print("[text] Jarvis is already running: this console session is not written to the conversation thread.")


def text_loop(brain, speaker, say) -> None:
    print("Text mode. Type 'exit' to quit.")
    while True:
        try:
            user = input("\nYOU: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user.lower() in {"exit", "quit"}:
            break
        if user:
            # typed at the console, never 'voice' in the thread ledger the other machine reads (core.thread)
            gathered = getattr(brain, "gathered", None)
            with (gathered("typed") if gathered else contextlib.nullcontext()):
                brain.ask(user, say)
            speaker.wait()


class Status:
    """Feeds the HUD: the loop sets the current state; a background thread adds
    live levels (mic while listening, voice while speaking) and the badges."""

    def __init__(self, speaker, listener, stt):
        self.speaker, self.listener, self.stt = speaker, listener, stt
        self.value = "sleeping"
        threading.Thread(target=self._run, daemon=True).start()

    def set(self, value: str) -> None:
        from core import hud

        self.value = value
        hud.state(value)  # publish the cue before warm-up or the next polling tick

    def _run(self) -> None:
        from core import hud
        from tools import eyes, languages

        shown = None
        tick = 0
        while True:
            speaking = self.speaker.busy() and self.speaker._playing is not None
            if self.listener.muted:
                state = "muted"
            elif speaking:
                state = "speaking"
            else:
                state = self.value
            if state != shown:
                hud.state(state)
                shown = state
            if state == "speaking":
                hud.level(self.speaker.output_level(0.15) / 6000)
            elif state == "listening":
                hud.level(self.listener.level)
            else:
                hud.level(0)
            tick += 1
            if tick % 10 == 0:
                interp = languages.interpreter
                hud.badges(eyes=eyes.eyes.active, muted=self.listener.muted,
                           interp=f"{interp['names'][0]} ⇄ {interp['names'][1]}" if interp else "",
                           lang=LANG_BADGE.get(self.stt.forced_language or self.stt.last_language, "EN"))
            time.sleep(0.07)


def voice_loop(cfg, brain, speaker, say, use_wake_word: bool, app_server=None) -> None:
    import numpy as np

    from audio.listener import Listener
    from audio.stt import Transcriber
    from audio.tts import beep
    from core import hud, progress
    from tools import eyes, languages, system

    from audio.voiceid import VoiceID
    from brain.llm import Brain
    from core import users
    from tools import voice

    running = ROOT / "data" / "jarvis.running"  # stop-jarvis.bat waits until this is gone
    running.write_text(str(os.getpid()))

    def quit_jarvis() -> None:
        speaker.stop()
        try:  # write today's conversations into the journal before going
            from brain.llm import archive_all

            archive_all(wait=8)
        except Exception as e:
            print(f"[journal] {e}")
        eyes.eyes.stop()
        hud.stop()
        from core import watchdog
        watchdog.mark_stopped()          # on purpose: the crash watchdog must not restart him
        running.unlink(missing_ok=True)
        os._exit(0)

    def watch_quit_flag() -> None:
        """stop-jarvis.bat asks Jarvis to quit by creating data/quit.flag; this works even when Jarvis
        runs as administrator (admin mode), where a normal program isn't allowed to end it."""
        flag = ROOT / "data" / "quit.flag"
        flag.unlink(missing_ok=True)
        while True:
            time.sleep(1)
            if flag.exists():
                flag.unlink(missing_ok=True)
                print("[jarvis] quit requested (stop/restart shortcut)")
                quit_jarvis()

    threading.Thread(target=watch_quit_flag, daemon=True, name="quit-flag").start()

    stt = Transcriber(cfg.whisper_model, cfg.whisper_device)
    languages.transcriber = stt

    def warm_stt() -> None:      # his first words after a start waited 1-2 s for the recogniser's first run (Transcriber.warm)
        took = stt.warm()
        print(f"[stt] warmed in {took:.1f}s" if took else "[stt] warm-up did not run (the first line will be slower)")

    threading.Thread(target=warm_stt, daemon=True, name="stt-warm").start()
    if app_server is not None:
        app_server.stt = stt
    listener = Listener(cfg.wake_threshold, use_wake_word, cfg.barge_in_ratio)
    from audio import bargein, endturn
    # Smart Turn end-of-turn (CPU, ~40 ms a check): until it has loaded, or if it can't, a 0.6 s pause ends a command.
    endturn.load_in_background(lambda model: setattr(listener, "end_of_turn", model))
    if app_server is not None:
        app_server.on_listen = listener.wake_event.set   # the companion's Talk -> Listen: like the wake word
        app_server.on_stop = speaker.stop                 # the Command Center's speaker button

        def _toggle_mute() -> bool:
            listener.muted = not listener.muted
            return listener.muted
        app_server.on_mute = _toggle_mute
    voice.voiceid, voice.listener, voice.speaker = VoiceID(cfg.whisper_device), listener, speaker
    if voice.active():
        print("[voice] voice lock on: only the enrolled voice is obeyed")
        threading.Thread(target=voice.voiceid._encoder, daemon=True).start()
    status = Status(speaker, listener, stt)
    try:  # Daily Awareness room listening: idle unless a session is on (opt-in, see core/awareness.py)
        from core import awareness
        awareness.RoomListener(listener, stt, voice.voiceid,
                               idle=lambda: status.value == "sleeping" and not speaker.busy()).start()
    except Exception as e:
        print(f"[awareness] room listening unavailable: {e}")
    try:  # plan upgrade 9 (core/notice): unprompted offers only while the loop sleeps and his voice is quiet, announced once heard
        from core import notice
        notice.attach(idle=lambda: notice_idle(status, speaker, listener),
                      played=lambda line, since: "heard" if speaker.muted else speaker.outcome(line, since))
    except Exception as e:  # noqa: BLE001 - Jarvis works as before without it
        print(f"[notice] unavailable: {e}")

    try:
        from core.tray import Tray

        Tray(listener, on_quit=quit_jarvis, on_eyes_off=eyes.eyes.stop).start()
    except Exception as e:
        print(f"[tray] unavailable: {e}")
    try:
        from core.hotkeys import COMPANION_MENU, SUMMON, Hotkeys

        Hotkeys(listener, stt).start()
        print(f"[keys] {SUMMON} = summon Jarvis; {COMPANION_MENU} = companion menu; hold Right Ctrl = dictate into any app")
    except Exception as e:
        print(f"[keys] hotkeys unavailable: {e}")

    # Proactive prompts (the morning briefing) wake Jarvis up and are answered
    # as if the user had asked.
    import queue

    from core.scheduler import Scheduler

    pending: queue.Queue = queue.Queue()

    def proactive(prompt: str) -> None:
        pending.put(prompt)
        listener.wake_event.set()

    try:  # fallback for callers that invoke voice_loop directly
        from core.appserver import AppServer

        def app_brain(uid: str):
            b = Brain(cfg.model, cfg.effort, load_personality(cfg, uid), cfg.conversation_timeout, uid=uid)
            b.context = brain.context
            return b

        if app_server is None:   # the voice's own brains when it has them (one Brain per person per machine)
            AppServer(getattr(brain, "brain_for", None) or app_brain, stt, verify_brain=app_brain).start()
    except Exception as e:
        print(f"[app] couldn't start the phone app server: {e}")

    if os.getenv("TELEGRAM_BOT_TOKEN"):
        try:
            from core.phone import PhoneBot

            def phone_brain(uid: str):
                b = Brain(cfg.model, cfg.effort, load_personality(cfg, uid), cfg.conversation_timeout, uid=uid)
                b.context = brain.context
                return b

            PhoneBot(os.environ["TELEGRAM_BOT_TOKEN"], phone_brain, stt).start()
        except Exception as e:
            print(f"[phone] couldn't start the Telegram bot: {e}")

    t = cfg.user_title
    from datetime import datetime
    from core import daypart
    greeting = daypart.start_greeting(t, datetime.now())      # the hour's greeting, in his own voice (core/character.md)
    if quiet_start():
        # A restart at night (an update while the house sleeps) must not wake anyone: the greeting goes to the screen only.
        print(f"JARVIS: {greeting} (not spoken: night)")
        hud.line("jarvis", greeting)
    else:
        say(greeting)
    speaker.wait()

    speaking = {"uid": "owner"}  # who said the last thing

    def remember_line(uid: str, who: str, text: str) -> None:
        """Both sides of a voice conversation go into that person's 24-hour Daily Awareness log."""
        try:
            from core import awareness
            with users.acting_as(uid):
                awareness.add(who, text, "jarvis")
        except Exception as error:
            diag.swallowed("awareness line", error)

    outer_say = say

    def say(text: str) -> None:
        outer_say(text)
        if "stopped" in timing and "said" not in timing:
            timing["said"] = time.monotonic()  # the first sentence is ready: the rest of the wait is the voice
            try:
                from core import pcledger
                pcledger.latency("voice_response_ready", timing["stopped"],
                    stt_ms=round(timing.get("stt", 0) * 1000, 2))
            except Exception:
                pass
        if not text.strip().startswith("[ignore]"):
            remember_line(speaking["uid"], "Jarvis", text)

    def say_unprompted(text: str) -> None:
        """Speech nobody asked for (reminders, held-news digests, game mode, timers): said, written to the awareness log
        and noted in the conversation as Jarvis's words, so "what?" right after a reminder has an answer. Before, these
        went through the outer say and reached neither."""
        outer_say(text)
        if text.strip().startswith("[ignore]"):
            return
        uid = unprompted_for()                    # whose reminder it is, not whoever spoke last
        remember_line(uid, "Jarvis", text)
        try:
            brain.brain_for(uid).note_unprompted(text, "unprompted")
        except Exception as error:  # noqa: BLE001 - the line is already out loud
            diag.swallowed("unprompted line", error)

    system.speak = say_unprompted     # reminders relayed by the always-on server (core.attention's fallback voice)
    system.announce = lambda msg: say_unprompted(f"{cfg.user_title.capitalize()}, {msg[0].lower()}{msg[1:]}")   # timers, focus
    Scheduler(say=say_unprompted, ask=proactive).start()   # every attention.deliver goes through it (core.attention)

    from tools.gaming import GameWatcher

    GameWatcher(say=say_unprompted).start()  # game mode: Jarvis steps aside while you play

    def hear(start_timeout: float, initial=None) -> str | None:
        """Record and transcribe one utterance. A short pause ends it unless what
        you said so far sounds unfinished ("open the..."), then it waits longer.
        With the voice lock on, anyone but the owner gets "" (ignored)."""
        status.set("listening")
        began = time.monotonic()
        speaking["heard_upto"] = None
        # What had been asked when his words BEGAN (Brain.ask's heard_upto, core.turnstate): taken at his first word, never
        # after his end-of-turn pause or Whisper (seconds in which a typed turn can end and ask something he never heard).
        # A cut-in began in respond(), which counted as it was heard (before its own Whisper).
        counts = speaking.pop("cut_in_counts", None) if initial else None

        def words_began() -> None:
            nonlocal counts
            if counts is not None:
                return
            try:
                from core import turnstate
                counts = turnstate.all_exchanges()
            except Exception as error:  # noqa: BLE001 - unknown: ask() counts as it begins, as before
                diag.swallowed("exchange count", error)
        if initial:
            words_began()                 # his speech began before this call
        # Smart Turn waits through a mid-sentence pause ("Scan my PC and look for... things that are wasting my memory");
        # Kreyol is not one of its languages, so a Kreyol speaker keeps the plain pause rule.
        audio = listener.record_command(start_timeout=start_timeout, initial=initial,
                                        smart=stt.last_language != "ht", not_before=speaker.last_play_end + 0.25,
                                        on_speech=words_began)
        if audio is None:
            return None
        words_began()                     # a listener that did not report his first word: as his audio is captured
        status.set("thinking")
        timing["stopped"] = time.monotonic()  # they finished speaking (plus the short pause)
        ended = dict(getattr(listener, "last_endpoint", None) or {})
        timing["endpoint"] = ended.get("silence_s", 0.0)    # his last word -> the turn committed
        timing.pop("said", None)
        text = stt.transcribe(endturn.pad_tail(audio))
        timing["stt"] = time.monotonic() - timing["stopped"]
        timing["text"] = text
        if text and text.strip():
            try:
                from core import inputwatch
                inputwatch.note_turn()      # talking to Jarvis is presence: practice steps aside at once
            except Exception:
                pass
        # A cut-in was recorded over his voice; a command that began right after it may carry its tail. Either way the
        # sentences he played (this reply's and a "Yes, sir?" just before it) are what the mic may also have heard.
        near = initial is not None or began - speaker.last_play_end < ECHO_WINDOW_S
        mine, earlier = echo.context(speaker) if near else ([], [])
        # Over his voice (a cut-in) long runs of his words are his too; right after it, only "sir" runs ("Yes, sir?").
        cleaned = echo.without_mine(text, mine, earlier, long_runs=initial is not None)   # "Go live. Understood, sir."
        if text.strip() and not cleaned:
            return ""                                      # all of it was his own voice
        text = cleaned
        try:
            from core import pcledger
            pcledger.latency("voice_stt", timing["stopped"])
        except Exception:
            pass
        # The trailing-word guess ("open the...") still breaks ties when the end-of-turn model is not in use or judged
        # the sentence finished; when the model already waited for him, waiting again would only add a second.
        while not ended.get("waited") and sounds_unfinished(text):
            status.set("listening")
            more = listener.record_command(start_timeout=1.0)
            status.set("thinking")
            if more is None:
                break
            audio = np.concatenate([audio, more])
            again = stt.transcribe(endturn.pad_tail(audio))
            text = echo.without_mine(again, mine, earlier, long_runs=initial is not None)
            if again.strip() and not text:
                return ""
        speaking["uid"] = voice.manual_speaker or "owner"  # "this is Maya" (switch_person)
        if text and voice.voiceid.enrolled:
            uid, score = voice.voiceid.identify(audio)
            ok = uid is not None or not voice.lock_on
            speaking["uid"] = uid or voice.manual_speaker or "owner"
            if uid:
                hud.badges(who=(users.get(uid).get("name") or "").upper())
            if not ok:
                print(f"  (ignored a voice that isn't yours: {text!r}, match {score:.2f})")
                hud.line("sys", "(ignored someone else's voice)")
                return ""
        speaking["heard_upto"] = None if counts is None else counts.get(speaking["uid"], 0)
        return text

    timing: dict = {}

    def log_timing() -> None:
        """One line per reply in the log: where the wait went (speech-to-text, thinking, voice)."""
        if "stopped" in timing and speaker.first_audio_at:
            total, heard = speaker.first_audio_at - timing["stopped"], timing.get("stt", 0)
            said = timing.get("said", speaker.first_audio_at)
            thinking = max(0.0, said - timing['stopped'] - heard)
            print(f"[timing] first words {total:.1f}s after you stopped (speech-to-text {heard:.1f}s, "
                  f"thinking {thinking:.1f}s, voice {max(0.0, speaker.first_audio_at - said):.1f}s)")
            try:
                from core import turntrace

                def wall(mono: float) -> float:
                    return time.time() - (time.monotonic() - mono)
                stopped, first = timing["stopped"], speaker.first_audio_at
                endpoint = float(timing.get("endpoint") or 0.0)
                # Per-stage ledger (Pipecat-style): his last word -> commit -> text -> first sentence -> first audio.
                # The gateway, the meaning checks before the brain and the model's first token come from the spans.
                marks = {"endpoint": (stopped - endpoint, stopped), "stt": (stopped, stopped + heard),
                         "first_sentence": (stopped, said), "tts_first_audio": (said, first), "total": (stopped, first)}
                where = turntrace.record("voice", timing.get("text", ""), wall(stopped), thinking, total,
                                         marks={k: (wall(a), wall(b)) for k, (a, b) in marks.items()},
                                         began=wall(stopped - endpoint))
                if where:
                    print(f"[timing] thinking = {where}")
            except Exception:
                pass
            try:
                from core import pcledger
                pcledger.latency("voice_tts_to_play", said)
            except Exception:
                pass
        timing.clear()

    def respond(command: str, task=None, on=None, heard_upto=None):
        """Reply in the background while watching for the user to cut in.
        Returns the audio frames of an interruption, or None.
        on: the Brain the task runs on, which an interruption cancels (default: the speaker's own; interpreter mode
        translates on the owner's brain whoever speaks, so a cut must never land on the speaker's conversation).
        heard_upto: what hear() counted when his words came in (Brain.ask: a question asked since is not his to answer)."""
        speaker.reset_turn()
        status.set("thinking")
        runs_on = on if on is not None or task is not None else brain.brain_for(speaking["uid"])
        task = task or (lambda: runs_on.ask(command, say, heard_upto=heard_upto))
        worker = threading.Thread(target=task, daemon=True)
        worker.start()
        while True:
            interruption = listener.watch_for_interruption(
                lambda: worker.is_alive() or speaker.busy(), speaker.output_level)
            if interruption is None:
                status.set("listening")
                log_timing()
                listener.discard(0.25)  # tail of Jarvis's voice still in the mic
                return None
            # When his words began (the frames that tripped the detector are his first ~0.2-0.5 s) and whether Jarvis's
            # first sentence had played whole by then: a cut this early is a premature reply he takes back (graceful yield).
            from audio.listener import FRAME_SECS
            from time import monotonic
            began_at = monotonic() - len(interruption) * FRAME_SECS
            first_done = bool(getattr(speaker, "heard", None))
            first_seen = speaker.first_audio_at          # None: he began before Jarvis's first sound
            try:   # what had been asked as he began to cut in, before this cut-in's Whisper (hear() keeps it: heard_upto)
                from core import turnstate
                cut_in_counts = turnstate.all_exchanges()
            except Exception as error:  # noqa: BLE001 - unknown: hear() counts as it begins
                diag.swallowed("exchange count", error)
                cut_in_counts = None
            # On speakers the mic hears HIM too (owner 2026-11-07: "he picks up his own voice"). Duck, then decide: his
            # voice drops at once (an audible reaction within ~100 ms) while what cut in is heard out; only a real
            # interruption stops him. His own words, a cough, "mhm" / "yeah" while he talks, or "what's the hold up?"
            # while he works restore the volume and he carries on (audio/bargein.classify_interruption).
            # Talking = this reply's voice has started and is not finished (the gap between two sentences included).
            talking = speaker.busy() and speaker.first_audio_at is not None
            speaker.duck(0.3)
            try:
                audio = listener.record_command(start_timeout=0.5, max_seconds=4.0, initial=interruption)
                asked = stt.transcribe(endturn.pad_tail(audio)) if audio is not None else ""
                # Still mid-reply? An answer given as his last word plays ("Shall I?" "Yes.") is a turn, not a stray word.
                talking = talking and speaker.busy()
                mine, earlier = echo.context(speaker)
                kind = cut_in_kind(asked, bargein.classify_interruption(asked, talking, worker.is_alive(), mine,
                                                                        STOP_WORDS, earlier))
                # Opt-in: the TV / the room can't cut him off mid-sentence. A stop word or his name always gets through:
                # a short "Stop!" over his ducked voice may not match the voiceprint, and it must still stop the task.
                if (kind == "interrupt" and talking and audio is not None and bargein.owner_only()
                        and voice.voiceid.enrolled and not bargein.is_stop(asked, STOP_WORDS)):
                    uid, _score = voice.voiceid.identify(audio)
                    if uid != speaking["uid"]:
                        kind = "other_voice"
            except BaseException:
                speaker.restore()       # a failure here (Whisper, the mic) must not leave his voice at 30 %
                raise
            if kind != "interrupt":
                speaker.restore()
                if kind == "status":
                    print(f"YOU: {asked}  (status check; task continues)")
                    hud.line("you", asked)
                    from audio.language import detect

                    say(progress.report({"ht": "ht", "fr": "fr"}.get(detect(asked), "en")))
                elif asked.strip():
                    print(f"  (kept talking: {kind}: {asked!r})")
                continue
            interruption = [audio]
            break
        speaking["cut_in_counts"] = cut_in_counts    # hear(initial=interruption) answers as of when he cut in
        speaker.stop()
        (runs_on or brain.brain_for(speaking["uid"])).cancel(speaker.heard_text(), speaker.unsaid_text())
        print("  (interrupted)")
        hud.line("sys", "(interrupted)")
        # Seconds of Jarvis's voice before his words began: 0 when he began first (its first sound came while he talked).
        played = None if speaker.first_audio_at is None else (0.0 if first_seen is None else max(0.0, began_at - first_seen))
        his_voice = True                 # no voiceprint enrolled: the voice cannot decide the yield
        try:   # evidence (2026-11-15): 43% of his turns end '(interrupted)' and the log cannot say whose voice cut in. The match
            #    also keeps the TV / Jarvis's own echo from being merged into his words (core/followthrough.yielded).
            if audio is not None and voice.voiceid.enrolled and voice.voiceid.prints:
                vector = voice.voiceid.embed(audio)
                match = max(float(v @ vector) for v in voice.voiceid.prints.values())
                from core import selfreview
                his_voice = match >= selfreview.CUT_MATCH
                print(f"  (cut-in: {len(asked.split())} words, voice match {match:.2f}, "
                      f"{'while he talked' if talking else 'between sentences'})")
        except Exception as error:  # noqa: BLE001 - a diagnostic never costs a turn; an unknown voice is never merged
            his_voice = False
            print(f"  (cut-in voice match failed: {type(error).__name__})")
        speaking["cut"] = {"played": played, "first_done": first_done, "his_voice": his_voice,
                           "said": " ".join(getattr(speaker, "spoken", None) or []), "working": worker.is_alive}
        # Its own line (core/selfreview parses the one above exactly): how far into his voice the cut came, for turn-taking work.
        print(f"  (cut-in timing: {'before my voice' if first_seen is None else f'{played:.1f}s into my voice'}, "
              f"first sentence {'done' if first_done else 'not done'})")
        return interruption

    while True:
        try:
            status.set("sleeping")
            listener.wait_for_wake_word()
            status.set("listening")
            first = True
            fragment = None          # his last words whose reply he cut off before hearing any of it (core/followthrough.carry)
            interruption = None
            if not pending.empty():  # Jarvis has something to say (morning briefing)
                interruption = respond(pending.get_nowait())
                first = False
            else:
                beep(wait=False)  # instant cue; keep recording so you can talk straight through
                brain.warm_up()

            # Conversation mode: keep listening for follow-ups until you go quiet
            # for a while or dismiss Jarvis - no need to repeat the wake word.
            while True:
                window = followup_window(first, cfg.followup_seconds, eyes_on=eyes.eyes.active,
                                         interpreting=bool(languages.interpreter))
                from_cut = interruption is not None
                previous, fragment = fragment, None    # only the turn just before can be carried onto (core/followthrough)
                heard = hear(window, initial=interruption)
                interruption = None
                if heard is None:
                    print("[mic] no follow-up, going to sleep")
                    voice.manual_speaker = None  # next conversation starts with the owner again
                    break
                command = fix_misheard(without_wake(heard))
                norm = command.lower().strip(" .!?,")
                if not norm:  # only the wake word itself, or noise
                    if heard.strip(" .!?,"):  # he called for attention mid-conversation: show we heard
                        say(f"Yes, {t}?")
                        speaker.wait()
                    continue
                print(f"YOU: {heard}" + (f"  (-> {command})" if command != without_wake(heard) else ""))
                hud.line("you", heard)
                remember_line(speaking["uid"], users.get(speaking["uid"]).get("name") or "You", heard)
                try:      # who is here, and his answer to an unprompted offer ('not now' quiets it: core/notice)
                    from core import notice
                    notice.heard_reply(speaking["uid"], norm)
                except Exception as error:  # noqa: BLE001
                    diag.swallowed("notice reply", error)
                if languages.interpreter:
                    if norm.startswith(INTERPRETER_STOP):
                        languages.stop_interpreter()
                        say(f"Interpreter off, {t}.")
                        speaker.wait()
                    else:
                        interruption = respond(command, task=lambda: interpret(brain, stt, command, say), on=brain)
                    first = False
                    continue
                try:      # what is waiting on him: 'done', 'no', 'yes', 'the second one' are its answers (core.turnstate)
                    from core import turnstate
                    items = turnstate.open_items(speaking["uid"], brain.brain_for(speaking["uid"]))
                except Exception as error:  # noqa: BLE001 - unknown: the filters work as they did
                    diag.swallowed("open items", error)
                    items = []
                route = pre_brain_route(norm, items)
                if route == "shutdown":
                    say(f"Powering down. Goodbye, {t}.")
                    speaker.wait()
                    return
                if route == "dismiss":
                    say(dismiss_line(t))
                    speaker.wait()
                    break
                if route == "stop":  # "stop" / "wait" just silences him
                    if system.power_pending():  # ...and stops a shutdown/restart countdown
                        say(system.cancel_power())
                    first = False
                    continue
                if route == "decline" and system.power_pending():   # "cancel" also stops a countdown, as it always did
                    say(system.cancel_power())
                # "decline": the brain declines what waits ("Okay, I won't.", core.turnstate.decline), and he stays heard
                asked_now = command
                try:       # his message in fragments, each cutting off the reply to the one before unheard: one turn (core/followthrough)
                    from core import followthrough
                    asked_now = followthrough.turn_words(previous, command, from_cut=from_cut, route=route, items=items)
                except Exception as error:  # noqa: BLE001 - unknown: his new words alone, as before
                    followthrough = None
                    diag.swallowed("fragment carry", error)
                if asked_now != command:
                    print(f"  (one turn with his words before: {asked_now!r})")
                taken, taken_wall = time.monotonic(), time.time()
                interruption = respond(asked_now, heard_upto=speaking.get("heard_upto"))
                if followthrough is not None:
                    cut = speaking.pop("cut", None) or {}      # set by respond() only when he cut this reply off
                    fragment = followthrough.keep_fragment(asked_now, interruption is not None, route=route, items=items, taken=taken,
                                                           heard_any=speaker.first_audio_at is not None,
                                                           played=cut.get("played"), first_done=cut.get("first_done", True),
                                                           since=taken_wall, his_voice=cut.get("his_voice", True),
                                                           said=cut.get("said", ""), working=cut.get("working"))
                first = False
                if voice.pending_enrollment and interruption is None:
                    speaker.wait()
                    status.set("listening")
                    voice.run_enrollment(say)
                    speaker.wait()
                    listener.flush()
        except KeyboardInterrupt:
            speaker.stop()
            print("\nExiting.")
            return
        except Exception:  # never let one bad request take Jarvis (and the phone app) down
            import traceback

            traceback.print_exc()
            hud.line("sys", "(something went wrong; recovered)")
            say("Sorry, something went wrong on my side. I'm still here.")
            speaker.wait()
            listener.flush()


def interpret(brain, stt, text: str, say) -> None:
    """Translate one utterance into the other language of the interpreter pair."""
    from tools import languages

    pair = languages.interpreter
    src = stt.last_language
    if src == pair["b"]:
        source, target = pair["names"][1], pair["names"][0]
    else:
        source, target = pair["names"]
    print(f"  [interpreter] {source} -> {target}")
    brain.translate(text, source, target, say)


_ASKS_OR_COMMANDS = re.compile(
    r"^(?:open|close|play|pause|search|find|show|tell|turn|set|check|start|launch|run|go to|look|read|write|type|send|call|add|"
    r"remind|what|what's|whats|how|who|when|where|why|which|can|could|would|will|is|are|do|does|did|please|hey|jarvis|"
    # PC verbs the list missed (2026-11-13: "Mute it." was guessed to be a goodbye 2 runs in 3, so the mute never ran):
    r"mute|unmute|volume|louder|quieter|minimi[sz]e|maximi[sz]e|focus|switch|scroll|hide|resume|skip|next|previous|bring|pull|fire|boot|load|"
    r"a (?:bit|little) (?:louder|quieter))\b", re.I)


def followup_window(first: bool, followup_seconds: float, eyes_on: bool = False, interpreting: bool = False) -> float:
    """How long the mic waits for his next line without the wake word. Eyes / interpreter on: until told to stop. While he
    demonstrates a task (core.demonstrate), at least the whole recording: he works in silence, and his 'done' must still be
    heard (review 2026-11-10: a quiet 45 s demonstration slept at 30 s and the 'Done.' was lost)."""
    if eyes_on or interpreting:
        return 600.0
    window = 6.0 if first else float(followup_seconds)
    try:
        from core import demonstrate
        if demonstrate.pending():
            return max(window, float(demonstrate.MAX_SECONDS))
    except Exception as error:  # noqa: BLE001 - unknown: the usual window
        diag.swallowed("demonstration window", error)
    return window


def cut_in_kind(asked: str, kind: str) -> str:
    """audio/bargein's verdict on what cut in, except that 'done' / 'cancel' while a demonstration waits is always his turn:
    a one-word 'Done.' said over a reply was a backchannel and thrown away, so the demonstration never finished."""
    if kind != "backchannel":
        return kind
    try:
        from core import demonstrate
        return "interrupt" if demonstrate.ends_it(asked) else kind
    except Exception as error:  # noqa: BLE001
        diag.swallowed("demonstration cut-in", error)
        return kind


# "Be quiet" said while something is open: the meaning model is skipped then (see pre_brain_route), so these are fixed.
HUSH = {"shut up", "be quiet", "hush", "shush", "zip it", "quiet please", "please be quiet", "silence", "quiet down"}


def notice_idle(status, speaker, listener) -> bool:
    """core/notice may speak unprompted: the loop sleeps, the speaker is quiet, and he is not dictating (holding Right Ctrl
    records the mic into his document without waking the loop, core.hotkeys -> Listener.start_tap: a line said then would
    be typed into what he is writing)."""
    return status.value == "sleeping" and not speaker.busy() and getattr(listener, "_tap", None) is None


def dismiss_line(title: str, now=None) -> str:
    """What he hears when he ends the conversation: a goodbye that claims nothing (live 2026-11-20: "End it." -> "Of course, sir."
    sounded like the stream would end, and nothing did), in the character sheet's voice (core/character.py), good night late."""
    from datetime import datetime
    from core import character
    hour = (now or datetime.now()).hour
    return character.line("dismiss_night" if hour >= 22 or hour < 5 else "dismiss", title)


def _dismissal(norm: str) -> bool:
    return (norm in DISMISS or norm.startswith(("that's all", "that will be all", "never mind")) or bool(GO_TO_SLEEP.search(norm) and not _MACHINE_SLEEP.search(norm))
            or _sleep_sentence(norm))


# "Jarvis, go to sleep. I'm not asking anything from you. Okay." (holdout h057): "go to sleep" is its own first sentence and
# the rest asks for nothing. Not "go to sleep. Open Notepad first." (a request after it), not "go to sleep?" (a question),
# and not "make the PC go to sleep" (the sentence must START with it).
_SLEEP_FIRST = re.compile(r"^(?:(?:ok(?:ay)?|alright|now|jarvis)[,\s]+)*(?:you can |go ahead and )?go (?:back )?to sleep"
                          r"(?: now| for now)?[.!]+\s*(?P<rest>.*)$")


def _sleep_sentence(norm: str) -> bool:
    m = _SLEEP_FIRST.match(norm)
    if not m or "?" in norm:
        return False
    from core import gateway
    rest = [s.strip(" ,") for s in re.split(r"[.!;]+", m.group("rest")) if s.strip(" ,")]
    return not any(gateway._COMMAND_CLAUSE.match(s) or gateway._ASKING_START.match(s) or gateway._ASKS_OF_HIM.search(s)
                   or re.match(r"(?:what|what's|whats|who|where|when|why|how)\b|i(?:'d| would)? (?:need|want|like)\b", s)
                   for s in rest)


def pre_brain_route(norm: str, items=(), meaning=None) -> str:
    """What the voice loop does with one line before the brain: 'shutdown' | 'dismiss' | 'stop' | 'decline' | 'respond'.

    `items`: what is waiting on him (core.turnstate.open_items). Live 2026-11-10: "watch me save this on Blender" ...
    "Done." -> "Of course, sir." and the mic slept, because the dismiss filter took 'done' before core.demonstrate saw it;
    a bare "no" to a plan ended the conversation and left the plan approvable. So while something is open:
      - 'done' / 'cancel' / 'never mind' while a demonstration waits are its own answers ('respond');
      - a bare no-word with a plan, an approval or a guide waiting is 'decline' (the brain declines it, or answers the
        question just asked with it, and he stays heard);
      - his explicit dismissals still end the conversation ("thanks", "sa bon", "stand by", "go to sleep", "that's all":
        review 2026-11-10, they never answer a waiting plan), except a no-word, which may answer the open question;
        the stop words and "be quiet" silence him;
      - any other short line (six words or fewer) is 'respond': the open question gets it ('yes', 'the second one'), never
        the local meaning call (echo_free_meaning, which read 'done' as a dismissal).
    With nothing open, exactly the filters as before."""
    items = list(items or ())
    kinds = {i.get("kind") for i in items}
    if norm in SHUTDOWN:
        return "shutdown"
    if items:
        from core import demonstrate, turnstate
        if "demonstrate" in kinds and (demonstrate._DONE.match(norm) or demonstrate._CANCEL.match(norm)):
            return "respond"                # "done" / "cancel" / "never mind": the demonstration's own answers
        if kinds & set(turnstate.DECLINABLE) and turnstate.bare_no(norm):
            return "decline"
        if _dismissal(norm) and not turnstate.bare_no(norm):
            return "dismiss"
        if norm in STOP_WORDS or norm in HUSH:
            return "stop"
        if len(norm.split()) <= 6:
            return "respond"
    if _dismissal(norm):
        return "dismiss"
    meant = None if items else (meaning or echo_free_meaning)(norm)  # "hush", "zip it": their words, not a list
    if meant == "dismiss":
        return "dismiss"
    if norm in STOP_WORDS or meant == "stop":
        return "stop"
    return "respond"


def echo_free_meaning(norm: str):
    """"stop" / "dismiss" / None for a short sentence the fixed STOP_WORDS / DISMISS lists missed (owner 2026-11-07:
    "I will not remember exact lines"). Only short, non-command sentences are asked of the local model, so a request
    never waits on it; anything already in the lists, or a question or command, returns None at once."""
    if (not norm or norm in STOP_WORDS or norm in DISMISS or norm in SHUTDOWN or len(norm.split()) > 6
            or _ASKS_OR_COMMANDS.match(norm)):
        return None
    from audio import bargein
    from core import speech
    if bargein.asks_to_resume(norm) or (speech.SPOKEN_BUDGET and bargein.asks_for_more(norm)):
        return None      # "go on" / "continue": the brain picks up a cut-off reply at once (Brain._resume), no model call
    if _plain_command(norm):
        return None      # "focus notepad", "scroll down", "volume down a bit": the gateway's own exact matcher reads them as PC actions
    try:                 # "end the stream" / "Gaming Protocol.": a skill's own order, never a goodbye (2026-11-20: "End. Stream." -> "Standing by.")
        from core import obsstream, protocols
        if obsstream.kind(norm) or protocols.sounds_like_order(norm):
            return None
    except Exception as error:  # noqa: BLE001 - unknown: the guess runs as before
        print(f"[meaning] skill check: {type(error).__name__}: {error}")
    from core import meaning
    return meaning.choose(norm, {"stop": "stop talking right now / be quiet",
                                 "dismiss": "the conversation is over for now; they are done talking to the assistant"},
                          "The assistant is in a voice conversation with the person.", max_words=6)


def _plain_command(norm: str) -> bool:
    """Is this short line a PC command, so the stop / dismiss guess has nothing to decide? Two cases, both read by code:
    the gateway's deterministic matcher (core.gateway.exact) reads it as PC actions ("focus notepad", "scroll down", "hide
    everything"), or it names an installed app (core.pcsemantic.mentioned_apps: "shut notepad down for me", "I'm finished with
    paint"). Measured 2026-11-13: the guess cost 0.2 to 0.5 s before each of these and, being a model, answered 'dismiss' to
    "shut notepad down for me" 3 runs of 3 (the conversation ended and the command was lost). A goodbye that happens to
    name an app is the rare case; there the conversation simply continues, which is the safe direction (a command taken for a
    goodbye is lost, talk taken for a command only costs a reply). Power actions (shutdown, sleep) keep the guess."""
    try:
        from core import gateway, pcsemantic
        hit = gateway.exact(norm)
        if hit is None:
            return bool(pcsemantic.mentioned_apps(norm))
    except Exception:  # noqa: BLE001 - unknown: the guess runs as before
        return False
    if not hit.intents or any(i.kind not in ("actions", "fact") for i in hit.intents):   # a question about the PC is no goodbye either
        return False
    return not any(step.get("tool") == "power" for i in hit.intents for step in i.steps)


def sounds_unfinished(text: str) -> bool:
    """Heuristic: does this transcript trail off mid-thought?"""
    text = text.strip()
    if not text:
        return False
    if text.endswith(("...", "…", "-", ",")):
        return True
    last = re.sub(r"[^\w']", "", text.split()[-1].lower())
    return last in TRAILING_WORDS


if __name__ == "__main__":
    # If a native library (audio, camera) ever crashes the process, record where.
    (ROOT / "data").mkdir(exist_ok=True)
    faulthandler.enable(open(ROOT / "crash.log", "a"))
    sys.exit(main())
