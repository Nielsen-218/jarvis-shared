"""Proves the install works: one typed chat turn through the real brain on your local model, in a THROWAWAY data folder
(your real memories and settings are never touched, and nothing is sent to a paid API).

    .venv\\Scripts\\python.exe scripts\\hello_check.py
    .venv\\Scripts\\python.exe scripts\\hello_check.py "What is 12 times 7?"

Exit code 0 = Jarvis answered; 1 = no usable answer (the reason is printed).
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["JARVIS_LOCAL_ONLY"] = "1"          # a paid call from a smoke test would be a bug
os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_THREAD"] = "off"
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH"):
    os.environ.pop(_v, None)

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-hello-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
os.environ["JARVIS_COMPANION_DATA_DIR"] = str(storage.DATA / "companion")
os.environ["JARVIS_PC_STATE_DIR"] = str(storage.DATA / "pc-control")
os.environ["JARVIS_PC_ROOT"] = str(storage.DATA / "jarvis_pc")

from brain.llm import Brain  # noqa: E402
from core import users  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import load_all, memory  # noqa: E402

_BAD = ("something went wrong", "try again in a moment", "local intelligence is unavailable", "can't reach my servers")


def main() -> int:
    question = " ".join(sys.argv[1:]) or "Hello Jarvis, can you hear me? Answer in one short sentence."
    load_all()
    users.set_current("owner")
    cfg = Config()
    brain = Brain(cfg.model, cfg.effort, load_personality(cfg, "owner"), uid="owner")
    brain.context = memory.context_block
    said: list[str] = []
    print(f"YOU: {question}")
    t0 = time.monotonic()
    brain.ask(question, lambda text: (said.append(text), print(f"JARVIS: {text}")))
    took = time.monotonic() - t0
    reply = " ".join(said).strip()
    if not reply or any(b in reply.lower() for b in _BAD):
        print(f"[hello] FAILED after {took:.1f}s: no usable answer. Is Ollama running with the model in .env pulled?")
        return 1
    print(f"[hello] OK: answered in {took:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
