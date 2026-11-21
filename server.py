r"""Jarvis server mode: the always-on laptop.

Runs everything that doesn't need the desk PC's microphone, speakers, screen or graphics card:
the phone app (chat, voice notes, photos, fitness and kitchen pages), every person's Jarvis brain,
reminders and phone notifications, the Garmin sync, the homelab watchdog and the journal.
The desk PC keeps "Hey Jarvis", the HUD, PC control, try-ons and gaming.

    python server.py            (on the laptop it runs as the systemd service "jarvis")
"""
import os
import signal
import sys
import threading

os.environ.setdefault("JARVIS_ROLE", "server")

from core.config import Config, load_personality  # noqa: E402  (also loads .env)


def main() -> None:
    cfg = Config()
    from tools import load_all, memory

    load_all(server=True)
    from brain.llm import Brain, archive_all
    from core.appserver import AppServer
    from core.scheduler import Scheduler

    stt = None
    if os.getenv("JARVIS_SERVER_STT", "on").lower() != "off":  # phone voice notes -> text, on the CPU
        try:
            from audio.stt import Transcriber

            stt = Transcriber(os.getenv("WHISPER_MODEL_SERVER", "small"), "cpu")
        except Exception as e:
            print(f"[stt] voice notes unavailable: {e}")

    def make_brain(uid: str) -> Brain:
        b = Brain(cfg.model, cfg.effort, load_personality(cfg, uid), cfg.conversation_timeout, uid=uid)
        b.context = memory.context_block
        return b

    from core import vault

    vault.set_transcriber(stt)  # "remember this" recordings share the voice-note Whisper model
    threading.Thread(target=vault.reprocess_pending, daemon=True, name="vault-catchup").start()
    print(f"[vault] memory vault at {vault.root()}")
    try:   # Mark VI: a mission still "running" from before this start was interrupted - never replayed blindly
        from core import missions
        recovered = missions.recover()
        if recovered:
            print(f"[missions] {len(recovered)} interrupted mission(s): queued once more with 'check what was done first', or marked unknown")
    except Exception as e:  # noqa: BLE001
        print(f"[missions] recovery skipped: {e}")
    AppServer(make_brain, stt).start()
    Scheduler(say=lambda text: print(f"[reminder] {text}"), ask=lambda prompt: None).start()

    def stop(*_):
        print("[server] shutting down: saving today's conversations to the journal")
        try:
            archive_all(wait=8)
        finally:
            os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("[server] Jarvis server mode is running", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
