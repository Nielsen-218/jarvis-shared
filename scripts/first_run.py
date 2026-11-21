"""First-run setup for a new owner: a fresh .env and a fresh data/ folder.

    .venv\\Scripts\\python.exe scripts\\first_run.py --name "Sam" --title sir --model qwen3.5:9b

Safe to run again: an existing .env is kept (only a missing one is created from .env.example), and the owner's name and
title are updated in place. Nothing here talks to the internet.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TITLES = {
    "sir": {"en": "sir", "ht": "mesye", "fr": "monsieur"},
    "ma'am": {"en": "ma'am", "ht": "madanm", "fr": "madame"},
}


def write_env(model: str) -> str:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if env.exists():
        return "kept your existing .env"
    text = example.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^(JARVIS_(?:LOCAL_MODEL|LOCAL_FAST_MODEL|LOCAL_ADVANCED_MODEL|LOCAL_VISION_MODEL|LEARNER_MODEL"
                  r"|TEACHER_LOCAL))=.*$", lambda m: f"{m.group(1)}={model}", text)
    env.write_text(text, encoding="utf-8")
    return "created .env from .env.example"


def set_title_in_env(title: str) -> None:
    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8")
    if re.search(r"(?m)^JARVIS_USER_TITLE=", text):
        text = re.sub(r"(?m)^JARVIS_USER_TITLE=.*$", f"JARVIS_USER_TITLE={title}", text)
    else:
        text += f"\nJARVIS_USER_TITLE={title}\n"
    env.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Set Jarvis up for a new owner")
    ap.add_argument("--name", required=True, help="what Jarvis calls you (your first name)")
    ap.add_argument("--title", default="sir", help="how Jarvis addresses you: sir, ma'am, or your name")
    ap.add_argument("--model", default="qwen3.5:9b", help="the local Ollama model for the brain")
    a = ap.parse_args()
    name = a.name.strip()
    if not name:
        print("A name is needed.")
        return 2
    print(f"[setup] {write_env(a.model)}")
    title = a.title.strip() or "sir"
    set_title_in_env(title)

    (ROOT / "data").mkdir(exist_ok=True)
    from core import users
    titles = TITLES.get(title.lower(), {"en": title, "ht": title, "fr": title})
    users.update("owner", name=name, titles=titles)
    row = users.get("owner")
    print(f"[setup] owner: {row.get('name')!r}, addressed as {row.get('titles', {}).get('en')!r}")
    print(f"[setup] your data folder: {ROOT / 'data'} (private: never share or commit it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
