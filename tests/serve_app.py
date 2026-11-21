r"""Run only the phone app server (no microphone), for trying the app in a browser.
    .venv\Scripts\python.exe tests\serve_app.py   -> http://127.0.0.1:8765
Prints a pairing code for the owner."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile  # noqa: E402

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-app-"))  # test data only; your real data is untouched

from audio.stt import Transcriber  # noqa: E402
from brain.llm import Brain  # noqa: E402
from core.appserver import AppServer  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import load_all, memory, voice  # noqa: E402

load_all()
cfg = Config()


def make_brain(uid):
    b = Brain(cfg.model, cfg.effort, load_personality(cfg, uid), uid=uid)
    b.context = memory.context_block
    return b


stt = Transcriber(cfg.whisper_model, cfg.whisper_device) if "--voice" in sys.argv else None
AppServer(make_brain, stt).start()
voice.pair_codes["123456"] = "owner"
print("Pairing code for testing: 123456")
while True:
    time.sleep(60)
