"""Generate a reusable development lesson and submit or queue it locally.

Developer explicitly selects source files and a test report. Report is attributed
instructor evidence, never trusted-runner certification. No test commands execute.
"""
import argparse
import json
import subprocess
import sqlite3
from pathlib import Path
from core import storage, teaching


def build(task, files, report, command, techniques, mistakes, dependencies, exercise):
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    examples = []
    root = Path.cwd().resolve()
    for file in files:
        path = file.resolve()
        if not path.is_relative_to(root) or path.suffix not in {".py", ".js", ".ts", ".cpp", ".h", ".md", ".html"}:
            raise ValueError("Select repository source files only")
        if path.stat().st_size > 100000:
            raise ValueError("Source file too large")
        examples.append(str(path.relative_to(root))+"\n"+path.read_text(encoding="utf-8"))
    if report.stat().st_size > 40000:
        raise ValueError("Test report too large")
    return teaching.validate({"title": task, "topic": "Jarvis development", "source": "git:"+revision,
        "license": "owner-authorized private repository", "version": revision,
        "content": task, "architecture": "Repository source snapshot at "+revision,
        "techniques": techniques, "mistakes": mistakes, "dependencies": dependencies,
        "examples": "\n\n".join(examples), "exercises": exercise,
        "tests": "Instructor-reported execution evidence, not skill certification.\nCommand: "+command+"\n"+report.read_text(encoding="utf-8")})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("task", "command", "techniques", "mistakes", "dependencies", "exercise"):
        parser.add_argument("--"+flag, required=True)
    parser.add_argument("--file", type=Path, action="append", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    if args.data_dir:
        storage.DATA = args.data_dir.resolve()
    payload = build(args.task, args.file, args.report, args.command, args.techniques,
                    args.mistakes, args.dependencies, args.exercise)
    args.outbox.parent.mkdir(parents=True, exist_ok=True)
    args.outbox.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.token_file:
        try:
            result = teaching.submit(payload, args.token_file.read_text(encoding="utf-8").strip())
            print(json.dumps({"submitted": result["id"], "outbox_retained": True}))
        except (OSError, ValueError, PermissionError, sqlite3.Error):
            print(json.dumps({"queued": True, "reason": "Gateway unavailable or rejected submission; inspect locally"}))
    else:
        print(json.dumps({"queued": True}))
