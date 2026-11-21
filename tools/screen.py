"""Vision: lets Jarvis look at your screen. The screenshot goes to whichever brain tier is answering (the local
vision model first; on an LM Studio setup its own model) only when you ask for something that needs seeing;
nothing is saved to disk."""
import base64
import io

from tools.registry import tool

# Measured on the owner's PC with qwen3-vl:8b-instruct (scripts/probe_vision_turn.py): the same screen at 1568 px
# cost 10.2 s of prompt processing cold / 6.2 s warm; at 1024 px, 3.3 s cold / 3.1 s warm, and it still read the PR
# number off the page. Text that needs reading exactly goes through read_screen (OCR at full resolution) anyway.
MAX_SIDE = 1024


@tool(
    "Take a screenshot and LOOK at it with a vision model. Prefer read_screen (local OCR + UI Automation) first "
    "for text, errors, menus and controls; use this when they ask what you see or when only seeing it answers: "
    "images, icons, colours, layout, a video or game, visual judgment. Describe only what is actually in the "
    "picture, never what you expect to be there.",
    {"all_monitors": {"type": "boolean", "description": "true to capture every monitor, false for the main one"}},
)
def look_at_screen(all_monitors: bool) -> list:
    from PIL import ImageGrab

    img = ImageGrab.grab(all_screens=all_monitors).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.b64encode(buf.getvalue()).decode()}},
        {"type": "text", "text": f"Screenshot ({img.width}x{img.height})."},
    ]
