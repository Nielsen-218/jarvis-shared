"""Jarvis crash watchdog: restarts him after a native crash. Standard library only; see core/watchdog.py.
Started by Jarvis himself at launch (core.watchdog.ensure_running); JARVIS_WATCHDOG=off disables it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    from core import watchdog
    sys.exit(watchdog.run_forever())
