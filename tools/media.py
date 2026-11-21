"""Media keys, volume and clipboard."""
import ctypes

from tools.registry import tool

_KEYS = {  # Windows virtual-key codes
    "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
    "mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
}


def _press(vk: int) -> None:
    from jarvis_pc.guardrails.stepfence import fence
    fence("press a media key")              # a PC step past its time limit presses nothing
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


@tool(
    "Control whatever media is playing (Spotify, YouTube, etc.) or toggle mute.",
    {"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop", "mute"]}},
)
def media_control(action: str) -> str:
    _press(_KEYS[action])
    return f"Sent {action.replace('_', '/')}."


def _endpoint():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def _window_titles() -> list[tuple[str, str]]:
    """(process name, title) of every visible top-level window."""
    import ctypes.wintypes as wt

    import psutil
    user32, out = ctypes.windll.user32, []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    out.append((psutil.Process(pid.value).name().lower(), buf.value))
                except Exception:
                    pass
        return True
    user32.EnumWindows(each, 0)
    return out


def now_playing() -> dict | None:
    """What's playing on this PC, read from the player's window: Spotify shows 'Artist - Song' while playing,
    a browser tab shows 'Video title - YouTube'."""
    for proc, title in _window_titles():
        if proc == "spotify.exe" and " - " in title and not title.lower().startswith("spotify"):
            artist, _, song = title.partition(" - ")
            return {"app": "Spotify", "title": song.strip(), "artist": artist.strip()}
    for proc, title in _window_titles():
        if title.endswith(" - YouTube") or " - YouTube - " in title:
            return {"app": "YouTube", "title": title.split(" - YouTube")[0].lstrip("(0123456789) ").strip(), "artist": ""}
    return None


def state() -> dict:
    """Now playing + volume, for the Command Center's media card (COM must be set up on server threads)."""
    import comtypes
    comtypes.CoInitialize()
    try:
        ep = _endpoint()
        vol, muted = round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())
    except Exception:
        vol, muted = None, None
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass
    try:
        playing = now_playing()
    except Exception:
        playing = None
    return {"playing": playing, "volume": vol, "muted": muted}


def set_volume(percent: int) -> None:
    from jarvis_pc.guardrails.stepfence import fence
    fence("set the volume")
    import comtypes
    comtypes.CoInitialize()
    try:
        ep = _endpoint()
        ep.SetMasterVolumeLevelScalar(max(0, min(int(percent), 100)) / 100, None)
        if ep.GetMute() and percent > 0:
            ep.SetMute(0, None)
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


@tool(
    "Get or set the PC's master volume.",
    {"percent": {"type": "integer", "description": "0-100 to set an absolute level; -1 to read; -2 through -100 to lower the current level by that many points"}},
)
def volume(percent: int) -> str:
    ep = _endpoint()
    current = round(ep.GetMasterVolumeLevelScalar() * 100)
    if percent < -1:
        # Local models naturally emit -50 for "lower it by 50%".  Previously
        # every negative value was treated as the -1 read sentinel, so Jarvis
        # could claim success while leaving the level unchanged.
        target = max(0, current + percent)
        ep.SetMasterVolumeLevelScalar(target / 100, None)
    elif percent >= 0:
        ep.SetMasterVolumeLevelScalar(max(0, min(percent, 100)) / 100, None)
        if ep.GetMute():
            ep.SetMute(0, None)
    return f"Volume is {round(ep.GetMasterVolumeLevelScalar() * 100)}%{' (muted)' if ep.GetMute() else ''}."


@tool("Read the text the user has copied to the clipboard (to translate, summarize, explain, fix...).")
def read_clipboard() -> str:
    import pyperclip

    text = pyperclip.paste()
    if not text.strip():
        return "The clipboard is empty or doesn't contain text."
    return text[:20000]


@tool(
    "Put text on the clipboard so the user can paste it (a translation, a draft, a fixed paragraph...).",
    {"text": {"type": "string"}},
)
def write_clipboard(text: str) -> str:
    import pyperclip

    pyperclip.copy(text)
    return "Copied to the clipboard."
