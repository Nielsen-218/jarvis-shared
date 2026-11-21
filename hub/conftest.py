"""Minimal conftest for the hub tests: add the repo root to sys.path so `import
controlhub` and its `from tools import ...` resolve, without the heavy tests/conftest."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
