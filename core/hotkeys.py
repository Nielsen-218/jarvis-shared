"""Global hotkeys.

- Summon (default Ctrl+Alt+J): wake Jarvis without saying "Hey Jarvis".
- Companion menu (default Ctrl+Alt+K): open or close the desktop companion's menu from the keyboard.
- Dictation (hold Right Ctrl, default): talk while holding it, release, and the
  text is typed into whatever app has focus (any language Jarvis understands).
  Holding it together with another key (e.g. Right Ctrl+C) is ignored.
"""
import os
import threading
import time

from core import hud

SUMMON = os.getenv("JARVIS_SUMMON_HOTKEY", "ctrl+alt+j")
COMPANION_MENU = os.getenv("JARVIS_COMPANION_MENU_HOTKEY", "ctrl+alt+k")   # the desktop companion's menu, no mouse needed
DICTATE = os.getenv("JARVIS_DICTATION_KEY", "right ctrl")
MIN_HOLD = 0.45  # seconds; shorter presses are normal Ctrl use


class Hotkeys:
    def __init__(self, listener, transcriber):
        self.listener = listener
        self.stt = transcriber
        self._down_at: float | None = None
        self._other_key = False
        self.worker: threading.Thread | None = None  # the last dictation being typed

    def start(self) -> None:
        import keyboard

        keyboard.add_hotkey(SUMMON, self._summon)
        keyboard.add_hotkey(COMPANION_MENU, lambda: hud.companion_menu("toggle"))
        keyboard.on_press_key(DICTATE, self._dictate_down, suppress=False)
        keyboard.on_release_key(DICTATE, self._dictate_up, suppress=False)
        keyboard.hook(self._any_key)

    def _summon(self) -> None:
        self.listener.wake_event.set()

    # -- dictation ---------------------------------------------------------------------
    def _any_key(self, event) -> None:
        if self._down_at is not None and event.event_type == "down" and event.name not in (DICTATE, "right ctrl"):
            self._other_key = True  # a shortcut like Right Ctrl+C, not dictation

    def _dictate_down(self, _event) -> None:
        if self._down_at is None:
            self._down_at = time.monotonic()
            self._other_key = False
            self.listener.start_tap()
            threading.Timer(MIN_HOLD, self._show_dictating).start()

    def _show_dictating(self) -> None:
        if self._down_at is not None and not self._other_key:
            hud.badges(dict=True)

    def _dictate_up(self, _event) -> None:
        held = time.monotonic() - (self._down_at or time.monotonic())
        self._down_at = None
        audio = self.listener.stop_tap()
        hud.badges(dict=False)
        if held < MIN_HOLD or self._other_key or audio is None:
            return
        self.worker = threading.Thread(target=self._type, args=(audio,), daemon=True)
        self.worker.start()

    def _type(self, audio) -> None:
        import keyboard
        import pyperclip

        text = self.stt.transcribe(audio).strip()
        if not text:
            return
        hud.line("sys", f"Dictated: {text}")
        print(f"[dictation] {text}")
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None
        pyperclip.copy(text)  # paste rather than type, so accents (è, ò, é) come through exactly
        time.sleep(0.05)
        keyboard.send("ctrl+v")
        if previous is not None:
            time.sleep(0.4)
            pyperclip.copy(previous)
