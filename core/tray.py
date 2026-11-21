"""System-tray icon: show/hide the HUD, mute the mic, eyes off, start with
Windows, admin mode, open the Jarvis folder, quit."""
import os
from typing import Callable

from PIL import Image, ImageDraw

from core import autostart, hud
from core.config import ROOT


def _open_page(page: str) -> None:
    """Tray shortcut: the owner's fitness/kitchen page in the browser, already signed in."""
    try:
        from core import users
        from tools.interface import open_jarvis_page

        with users.acting_as("owner"):
            open_jarvis_page(page)
    except Exception as e:
        print(f"[tray] couldn't open {page}: {e}")


def _icon_image(muted: bool = False) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (255, 107, 107, 255) if muted else (79, 195, 247, 255)
    d.ellipse((4, 4, 60, 60), outline=color, width=5)
    d.ellipse((18, 18, 46, 46), outline=color, width=4)
    d.ellipse((27, 27, 37, 37), fill=(255, 255, 255, 255))
    return img


class Tray:
    def __init__(self, listener, on_quit: Callable[[], None], on_eyes_off: Callable[[], None]):
        import pystray

        self.listener = listener
        self.on_quit = on_quit
        item = pystray.MenuItem
        menu = pystray.Menu(
            item("Show HUD", lambda: hud.show(), default=True),
            item("Hide HUD", lambda: hud.hide()),
            item("Companion on screen", lambda: hud.set_companion_visible(not hud.companion_visible()),
                 checked=lambda _: hud.companion_visible()),
            pystray.Menu.SEPARATOR,
            item("Mute microphone", self._toggle_mute, checked=lambda _: bool(listener and listener.muted)),
            item("Turn eyes off", lambda: on_eyes_off()),
            item("Start with Windows", self._toggle_autostart, checked=lambda _: autostart.is_enabled(),
                 enabled=lambda _: not autostart.admin_mode_on()),
            item("Admin mode (always run as administrator)", lambda: autostart.toggle_admin_mode(),
                 checked=lambda _: autostart.admin_mode_on()),
            item("Command Center (dashboard)", lambda: hud.dashboard() or _open_page("dashboard")),
            item("Start today's workout", lambda: hud.mode("workout")),
            item("Play a game with Jarvis", lambda: hud.mode("play")),
            item("Stretch break (2 minutes)", lambda: hud.mode("stretch")),
            pystray.Menu.SEPARATOR,
            item("Fitness app", lambda: _open_page("fitness")),
            item("Kitchen (meals, groceries)", lambda: _open_page("kitchen")),
            item("Open Jarvis folder", lambda: os.startfile(ROOT)),
            pystray.Menu.SEPARATOR,
            item("Quit Jarvis", self._quit),
        )
        self.icon = pystray.Icon("jarvis", _icon_image(), "J.A.R.V.I.S.", menu)
        from core import notify

        notify.tray_icon = self.icon

    def start(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

    def _toggle_mute(self) -> None:
        if self.listener is None:
            return
        self.listener.muted = not self.listener.muted
        self.icon.icon = _icon_image(self.listener.muted)
        hud.badges(muted=self.listener.muted)
        hud.state("muted" if self.listener.muted else "sleeping")

    def _toggle_autostart(self) -> None:
        autostart.disable() if autostart.is_enabled() else autostart.enable()

    def _quit(self) -> None:
        self.stop()
        self.on_quit()
