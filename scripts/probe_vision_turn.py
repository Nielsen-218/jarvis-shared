"""Where do the seconds of a vision turn go? One real screenshot, the real system prompt and tool shortlist, sent to
the local vision model exactly as a turn sends it, with Ollama's own timing breakdown printed.

    .venv\\Scripts\\python.exe scripts\\probe_vision_turn.py            # default: Jarvis's current settings
    JARVIS_PROBE_SIDE=1024 ...                                            # try a smaller screenshot

Prints load, prompt-eval (tokens and s), generation (tokens and s) and total, for a cold and a warm call. Sandboxed
data folder; talks to the local Ollama only."""
import base64
import io
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["JARVIS_LOCAL_ONLY"] = "1"
from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-visionprobe-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
import tools  # noqa: E402
from core import contextfit, intelligence, users  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import memory, registry  # noqa: E402

tools.load_all()
users.set_current("owner")
SIDE = int(os.getenv("JARVIS_PROBE_SIDE", "1568"))


def screenshot(side):
    from PIL import ImageGrab
    img = ImageGrab.grab().convert("RGB")
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return img.size, base64.b64encode(buf.getvalue()).decode()


def body(side):
    (w, h), data = screenshot(side)
    system = load_personality(Config(), "owner")
    history = [{"role": "user", "content": [{"type": "text", "text": "<background>\n" + memory.context_block() + "\n</background>"},
                                            {"type": "text", "text": "Take a look at my screen and tell me what you see."}]},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "s1", "name": "look_at_screen", "input": {"all_monitors": False}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "s1", "content": [
                   {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                   {"type": "text", "text": f"Screenshot ({w}x{h})."}]}]}]
    schemas = intelligence._schemas(intelligence.select_tools(history, registry.schemas()))
    messages = contextfit.fit(intelligence.messages_for_local(system, history), schemas)
    native = intelligence._ollama_body({"model": intelligence.vision_model(), "messages": messages, "temperature": 0.2,
                                        "max_tokens": 1536, "tools": schemas})
    native["stream"] = False
    return native, (w, h), len(data) * 3 // 4


def call(native):
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(native).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read())
    wall = time.monotonic() - t
    ns = 1e9
    return {"wall_s": round(wall, 1), "load_s": round(out.get("load_duration", 0) / ns, 1),
            "prompt_tokens": out.get("prompt_eval_count"), "prompt_s": round(out.get("prompt_eval_duration", 0) / ns, 1),
            "gen_tokens": out.get("eval_count"), "gen_s": round(out.get("eval_duration", 0) / ns, 1),
            "reply": (out.get("message") or {}).get("content", "")[:120]}


def main():
    native, size, image_bytes = body(SIDE)
    print(f"model {native['model']}  screenshot {size[0]}x{size[1]} ({image_bytes // 1024} KB)  tools {len(native.get('tools', []))}  num_ctx {native['options']['num_ctx']}")
    for label in ("cold", "warm"):
        print(label, json.dumps(call(native)))


if __name__ == "__main__":
    main()
