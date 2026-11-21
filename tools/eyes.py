"""Jarvis's eyes: the webcam.

Two ways of seeing:
- Snapshot: when eyes are off, look_through_camera switches the camera on just
  long enough to take one photo.
- Eyes on ("activate eyes"): the camera runs continuously in the background
  until told to stop. Every time the user speaks, the latest frame is attached
  to their message automatically, so "how about now?" needs no extra step.
  An optional live window shows exactly what Jarvis sees, with a focus meter.

Nothing is ever saved to disk.
"""
import base64
import os
import threading
import time

import numpy as np

from tools.registry import tool

CAMERA_INDEX = int(os.getenv("JARVIS_CAMERA", "0"))
WINDOW = "Jarvis Eyes"


def _open_camera(width: int | None = None, height: int | None = None):
    import cv2

    cam = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cam.isOpened():
        return None
    if width:
        cam.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # full frame rate at HD
        cam.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cam.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cam


def _to_blocks(frame: np.ndarray, note: str) -> list:
    import cv2

    _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    h, w = frame.shape[:2]
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.b64encode(jpg.tobytes()).decode()}},
        {"type": "text", "text": f"{note} ({w}x{h}, not mirrored: the user's right hand appears on the left)."},
    ]


def sharpness(frame: np.ndarray) -> float:
    """Focus score of the central region (variance of the Laplacian; higher = sharper)."""
    import cv2

    h, w = frame.shape[:2]
    center = frame[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    return float(cv2.Laplacian(cv2.cvtColor(center, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


class Eyes:
    """Keeps the camera running in a background thread, with an optional live window."""

    def __init__(self):
        self.active = False
        self.show_view = False
        self._frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self, show_view: bool) -> str:
        self.show_view = show_view
        if self.active:
            return "Eyes were already on." + (" Live view shown." if show_view else "")
        cam = _open_camera(1280, 720)
        if cam is None:
            return "The camera isn't available (unplugged, or in use by another app)."
        self.active = True
        self._thread = threading.Thread(target=self._run, args=(cam,), daemon=True)
        self._thread.start()
        # Wait for the first good frame so an immediate look works.
        for _ in range(50):
            if self.latest() is not None:
                break
            time.sleep(0.05)
        return "Eyes on: the camera is running continuously." + (" Live view is open." if show_view else "")

    def stop(self) -> str:
        if not self.active:
            return "Eyes were already off."
        self.active = False
        if self._thread:
            self._thread.join(timeout=3)
        with self._lock:
            self._frame = None
        return "Eyes off: the camera is switched off."

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def attachment(self) -> list:
        """Content blocks to add to the user's message while eyes are on."""
        frame = self.latest() if self.active else None
        return _to_blocks(frame, "Live camera frame, captured as the user finished speaking") if frame is not None else []

    def _run(self, cam) -> None:
        import cv2

        window_open = False
        skipped = 0
        try:
            while self.active:
                ok, frame = cam.read()
                if not ok:
                    time.sleep(0.05)
                    continue
                skipped += 1
                if skipped > 5:  # first frames are dark while exposure settles
                    with self._lock:
                        self._frame = frame
                if self.show_view:
                    cv2.imshow(WINDOW, self._overlay(frame))
                    if not window_open:
                        window_open = True
                        try:
                            cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
                        except cv2.error:
                            pass
                    cv2.waitKey(1)
                    if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        self.show_view = False  # user closed the window with its X
                        window_open = False
                elif window_open:
                    cv2.destroyWindow(WINDOW)
                    cv2.waitKey(1)
                    window_open = False
        finally:
            cam.release()
            if window_open:
                cv2.destroyWindow(WINDOW)
                cv2.waitKey(1)

    @staticmethod
    def _overlay(frame: np.ndarray) -> np.ndarray:
        import cv2

        view = cv2.flip(frame, 1)  # mirror the preview so it feels like a mirror
        h, w = view.shape[:2]
        score = sharpness(frame)
        label, color = (("SHARP", (80, 220, 80)) if score > 150 else
                        ("SOFT", (0, 200, 255)) if score > 50 else
                        ("BLURRY", (60, 60, 255)))
        # Focus area box and status bar
        cv2.rectangle(view, (w // 4, h // 4), (3 * w // 4, 3 * h // 4), color, 1)
        cv2.rectangle(view, (0, 0), (w, 36), (20, 20, 20), -1)
        cv2.circle(view, (18, 18), 7, (40, 40, 230), -1)
        cv2.putText(view, "JARVIS EYES", (34, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 2)
        bar = int(min(score / 300, 1.0) * 160)
        cv2.rectangle(view, (w - 330, 12), (w - 170, 26), (70, 70, 70), -1)
        cv2.rectangle(view, (w - 330, 12), (w - 330 + bar, 26), color, -1)
        cv2.putText(view, f"FOCUS: {label}", (w - 160, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        return view


eyes = Eyes()


def _show_in_hud(frame: np.ndarray, caption: str) -> None:
    import cv2
    from PIL import Image

    from core import hud

    hud.media(image=Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), caption=caption)


@tool(
    "Turn on continuous vision ('activate eyes', 'eyes on', 'keep watching'). The camera "
    "stays on, and a live frame is attached to every message until deactivate_eyes.",
    {"show_view": {"type": "boolean", "description": (
        "true to also open a live window showing the user what you see, with a focus meter")}},
)
def activate_eyes(show_view: bool) -> list:
    status = eyes.start(show_view)
    frame = eyes.latest()
    if frame is None:
        return [{"type": "text", "text": status}]
    return _to_blocks(frame, f"{status} Current view")


@tool("Turn off continuous vision and switch the camera off ('eyes off', 'stop looking', 'close your eyes').")
def deactivate_eyes() -> str:
    return eyes.stop()


@tool(
    "Show or hide the live camera window that shows the user exactly what you see "
    "('show me what you see', 'hide the camera view'). Showing it turns eyes on.",
    {"show": {"type": "boolean"}},
)
def show_camera_view(show: bool) -> str:
    if show:
        return eyes.start(show_view=True)
    eyes.show_view = False
    return "Live view hidden." + (" Eyes are still on." if eyes.active else "")


@tool(
    "Take a photo with the user's webcam to see them or something they are holding up / "
    "showing ('what am I holding', 'look at this', 'can you see me', 'read this label'). "
    "Not needed while eyes are on: the live frame is already attached to the message.",
    {"high_detail": {"type": "boolean", "description": (
        "true only when fine detail matters, e.g. reading small print (slower, ~2 s extra)")}},
)
def look_through_camera(high_detail: bool) -> list:
    frame = capture_frame(high_detail)
    if frame is None:
        return [{"type": "text", "text": "The camera isn't available (unplugged, or in use by another app)."}]
    _show_in_hud(frame, "What I see")
    return _to_blocks(frame, "Live camera frame" if eyes.active else "Webcam photo")


def capture_frame(high_detail: bool = True) -> np.ndarray | None:
    """Current view: the live frame if eyes are on, otherwise a one-off snapshot."""
    if eyes.active:
        frame = eyes.latest()
        if frame is not None:
            return frame
    cam = _open_camera(1280, 720) if high_detail else _open_camera()
    if cam is None:
        return None
    try:
        ok, frame = False, None
        for _ in range(6):  # skip frames while auto-exposure settles
            ok, frame = cam.read()
    finally:
        cam.release()
    return frame if ok else None
