"""Workbench by voice (Engineering Eyes, Issue #9): "Jarvis, what am I holding?", guided builds, "check my work".

Uses the PC webcam for a still photo each time — never a claim of live video — and shows the labeled photo on
the HUD. The phone has the same features in Eyes › ENGINEER. Desk-only: it needs this PC's camera and GPU.
"""
import base64

from tools.registry import tool


def _snapshot() -> str | None:
    """One still frame from the webcam as a JPEG data URL, or None if the camera isn't available."""
    import cv2

    from tools.eyes import capture_frame

    frame = capture_frame(high_detail=True)
    if frame is None:
        return None
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode() if ok else None


def _show(image: str, result: dict, caption: str) -> None:
    from core import engeyes, hud
    try:
        hud.media(image=engeyes.annotate(image, engeyes.boxes_of(result)), caption=caption)
    except Exception:
        pass  # the spoken answer still stands


def _run(fn):
    from core import engeyes
    try:
        return fn()
    except engeyes.EngError as error:
        return str(error)


NO_CAMERA = "The camera isn't available (unplugged, or another app is using it)."


@tool(
    "Identify the electronic part, board, tool or object the user is holding up to the webcam ('what am I "
    "holding?', 'what is this chip?'). Takes a still photo, reads its markings, says how sure it is, and shows "
    "the labeled photo on screen. Speak the returned answer as is.",
    {"question": {"type": "string", "description": "Their exact question, if more specific than 'what is this?'"}},
    slow=True,
)
def what_am_i_holding(question: str = "") -> str:
    from core import engeyes

    def go():
        image = _snapshot()
        if image is None:
            return NO_CAMERA
        result = engeyes.identify(image, question)
        _show(image, result, result["item"])
        return result["reply"]
    return _run(go)


@tool(
    "Start a guided hardware build ('help me wire this sensor to my Arduino', 'I want to blink an LED'). Makes a "
    "step-by-step plan — verified wiring for common Arduino builds — and refuses wire-level steps for mains or "
    "high-voltage work. Set use_camera when their parts are in front of the webcam. Speak the returned answer as is.",
    {"goal": {"type": "string"}, "use_camera": {"type": "boolean"}},
    required=["goal"], slow=True,
)
def start_build(goal: str, use_camera: bool = False) -> str:
    from core import engeyes

    def go():
        image = _snapshot() if use_camera else None
        return engeyes.start_project(goal, image)["reply"]
    return _run(go)


@tool(
    "Check the current build step from a fresh webcam photo ('check my work', 'is this right?', 'done'). Only a "
    "photo that clearly shows the step moves the build on. Speak the returned answer as is.",
    slow=True,
)
def check_my_build() -> str:
    from core import engeyes

    def go():
        image = _snapshot()
        if image is None:
            return NO_CAMERA
        result = engeyes.check_step(image)
        _show(image, result, "Step check")
        return result["reply"]
    return _run(go)


@tool(
    "Move around the current guided build: 'back' (previous step), 'skip' (next step without a photo check — "
    "recorded as unchecked), 'stop' (end the build) or 'status' (where are we?).",
    {"action": {"type": "string", "enum": ["back", "skip", "stop", "status"]}},
)
def build_step(action: str) -> str:
    from core import engeyes

    def go():
        if action == "status":
            v = engeyes.view(engeyes._load_project())
            if not v:
                return "There's no build going."
            if v["finished"]:
                return f"{v['title']} is finished."
            return f"{v['title']}: step {v['index'] + 1} of {v['total']}, {v['step']['title']}: {v['step']['instruction']}"
        return engeyes.control(action)["reply"]
    return _run(go)
