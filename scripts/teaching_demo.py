"""Actual multi-process offline gateway + compiler wiring demonstration.

Does NOT impersonate a Claude session or claim model-driven independence.
Uses new isolated data only; keeps credentials outside exported evidence.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=False)
    data = args.workspace/"data"
    records = []

    def run(module, argv, offline=False):
        if offline:
            code = ("import socket,sys,runpy\n"
                    "def no_network(*a,**k): raise RuntimeError('network disabled for offline demonstration')\n"
                    "socket.socket=no_network\n"
                    "sys.argv=[sys.argv[1]]+sys.argv[2:]\n"
                    "runpy.run_module(sys.argv[0],run_name='__main__')\n")
            command = [sys.executable, "-c", code, module, *argv]
        else:
            command = [sys.executable, "-m", module, *argv]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        records.append({"module": module, "exit_code": result.returncode, "network_disabled": offline})
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        return json.loads(result.stdout)

    token = args.workspace/"codex.credential"
    run("scripts.teach_jarvis", ["--data-dir", str(data), "grant", "codex", "--token-file", str(token)])
    from core.teaching_practice import CPP_SOURCE
    lesson = {"title": "Create a basic C++ program", "topic": "C++", "source": "Codex authored audited teaching fixture",
              "license": "owner-authorized", "version": "1", "content": "Write main.cpp; compile with the installed local C++ toolchain; run and check stdout.",
              "architecture": "A single source file with main and standard iostream output.",
              "techniques": "Use a reproducible isolated build and compare stdout and exit code.",
              "mistakes": "Windows cmd batch paths need cmd quoting, not C argv backslash quoting.",
              "dependencies": "Installed Visual Studio MSVC or g++/clang++; no packages installed.",
              "examples": CPP_SOURCE, "exercises": "Reproduce the audited hello-world fixture after restarting the process.",
              "tests": "Compile returns 0; executable returns 0 and prints Hello, Jarvis! with newline."}
    bundle = args.workspace/"cpp-lesson.json"
    bundle.write_text(json.dumps(lesson), encoding="utf-8")
    saved = run("scripts.teach_jarvis", ["--data-dir", str(data), "submit", "--token-file", str(token), str(bundle)], True)
    first = run("scripts.teaching_practice", [saved["id"], "--data-dir", str(data)])
    # A separately granted credential tests the same interface. This is an
    # interface compatibility check driven by Codex, not a real Claude invocation.
    other = args.workspace/"mentor-interface.credential"
    run("scripts.teach_jarvis", ["--data-dir", str(data), "grant", "claude-interface-check", "--token-file", str(other)])
    lesson["title"] = "C++ follow-up exercise"
    lesson["content"] = "Check the actual compiler diagnostics and retain the command and stdout."
    bundle.write_text(json.dumps(lesson), encoding="utf-8")
    run("scripts.teach_jarvis", ["--data-dir", str(data), "submit", "--token-file", str(other), str(bundle)], True)
    retained = run("scripts.teach_jarvis", ["--data-dir", str(data), "search", "--token-file", str(token), "C++"], True)
    assert len(retained) == 2 and any(x["id"] == saved["id"] for x in retained)
    repeated = run("scripts.teaching_practice", [saved["id"], "--data-dir", str(data)])
    report = {"scenario": "Retained lessons and trusted C++ fixture after process restart",
              "first_attempt": first, "after_restart_attempt": repeated,
              "retrieved_lessons": [{"id": x["id"], "mentor": x["mentor"], "state": x["state"]} for x in retained],
              "processes": records, "cloud_inference_calls": 0,
              "local_model_used": False, "actual_claude_session_used": False,
              "full_requested_independent_model_demo_passed": False,
              "remaining": "Local coding-model inference, real Claude Code contribution, full app restart and autonomous generalization are not demonstrated."}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"cpp_passed_twice": first["passed"] and repeated["passed"], "retained_lessons": len(retained),
                      "independent_model_demo": False, "evidence": str(args.evidence)}))


if __name__ == "__main__":
    main()
