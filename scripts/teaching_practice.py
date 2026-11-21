"""Run the owner's fixed C++ evaluator in isolated local data; no inference."""
import argparse
import json
from pathlib import Path
from core import storage
from core.teaching_practice import practice

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lesson_id")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--local-model", help="Installed local Ollama model; no cloud fallback or downloads")
    args = parser.parse_args()
    if args.data_dir:
        storage.DATA = args.data_dir.resolve()
    result = practice(args.lesson_id, args.local_model)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
