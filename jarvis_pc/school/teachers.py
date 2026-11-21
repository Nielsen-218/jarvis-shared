"""Teachers: plain (task, context) -> text callables, so any model can sit in either seat and tests can use a fake.

  high school  = the free local models (Ollama), tried smallest first;
  college      = the paid API, used only for subjects the local models could not learn, under a daily call budget.

A teacher only ever returns TEXT: one command per line, optional `# expect: ...`. It never runs anything. The School parses it,
the permission gate classifies every line, and nothing is kept unless it passes the ground-truth exam.
"""
from __future__ import annotations

from typing import Callable, List, Optional

Teacher = Callable[[str, str], str]

RULES = """You write Windows PowerShell commands for a PC assistant. Reply with ONLY the commands, one per line, no explanations, no code fences.

Rules that the permission gate enforces (a command that breaks them is rejected):
- Use full cmdlet names (Get-ChildItem, Get-Content, Select-String, Get-FileHash, Measure-Object, Sort-Object, Select-Object,
  Where-Object, Get-Item, Get-Process, Get-Service, Get-CimInstance, Get-Date). Never aliases like ls, cat, dir, gci, ?, %.
- One pipeline per line. No script blocks { }, no $variables, no $( ), no ( ) sub-expressions, no semicolons, no &&.
- Where-Object takes a property and operator: Where-Object Length -gt 100 or Where-Object Name -like *.log
- Values the task gives in {braces}, like {dir} or {file}, must be written exactly like that: the real value is filled in later.
  Never write a real path.
- A web page or service running on this PC (a {url}) is read with Invoke-WebRequest -UseBasicParsing {url} | Select-Object -ExpandProperty Content
  (the raw page text) or Invoke-RestMethod {url} (JSON), with no other options.
- A program's version is read by running the program with --version (python --version, git --version, node --version).
- A media file (audio/video) is described with ffprobe -v error -show_entries format=duration (or stream=sample_rate, stream=channels) -of default=noprint_wrappers=1:nokey=1 {file}; print only the one value asked for.
- The owner's laptop {host} is asked with ssh -i {key} -o BatchMode=yes {host} <command>, where <command> is exactly one of: hostname, uptime -p, systemctl is-active jarvis.service, df -h /, cat /proc/loadavg, nproc, free -m.
- The Linux inside WSL is asked with wsl -d {distro} -- <command>, and the two dashes are required; the only commands allowed are cat /etc/os-release, uname -r, hostname, nproc, whoami, with nothing piped or added (read the answer from the whole printed text). Which Linux is installed: wsl --list --quiet.
- Graphics facts come from Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name (or DriverVersion, CurrentHorizontalResolution).
- Select-String -Path {file} -Pattern <word> -Context 2,3 also prints 2 lines before and 3 lines after each match; use it to read a record around a name.
- To search a file for a line, Select-String -Path {file} -Pattern <word> prints only the matching lines; Select-String ... | Measure-Object counts them.
- To get a number or a name, print it with Select-Object -ExpandProperty, Measure-Object, or let the cmdlet print it.
{write_rules}
Examples (different questions from yours; do not copy them, write what YOUR task needs):
Task: how many .md files are in {folder}
Get-ChildItem -Path {folder} -Filter *.md | Measure-Object

Task: who made this computer
Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty Manufacturer
"""

READ_ONLY = "- This task only READS. Do not create, change, move, rename or delete anything.\n"
MAY_WRITE = ("- This task may change files, but ONLY inside the folders the task names in {braces}. Use New-Item, Copy-Item, "
             "Move-Item, Rename-Item, Set-Content, Compress-Archive or Expand-Archive with -Path/-Destination built from those values, and Remove-Item -Path <value> -Recurse to remove a folder named in {braces}.\n")


def build_prompt(task: str, context: str, writes: bool = False) -> str:
    """`writes` is only a default: the School puts READ_ONLY or MAY_WRITE into the context for each task. Web, unzip and remove-folder guidance lives in RULES and MAY_WRITE."""
    if "UI TASK" in context:                      # mouse/keyboard/window subjects use the UI action language, not PowerShell
        from ..uiact import lang
        return f"{lang.rules_for_teacher()}\nTask: {task}\n{context.strip()}\n"
    rules = RULES.replace("{write_rules}", "" if "This task " in context else (MAY_WRITE if writes else READ_ONLY))
    return f"{rules}\nTask: {task}\n{context.strip()}\n".rstrip() + "\n"


def local_teacher(ask_text: Callable[..., str], model_name: str, *, writes: bool = False) -> Teacher:
    """A teacher backed by a local model. `ask_text(prompt, model_name=...)` is core.learn_llm.ask_text in production."""
    def teach(task: str, context: str) -> str:
        return ask_text(build_prompt(task, context, writes), model_name=model_name)
    teach.__name__ = f"local:{model_name}"
    return teach


def api_teacher(ask_api: Callable[[str], str], label: str = "api", *, writes: bool = True) -> Teacher:
    """A teacher backed by a paid API. `ask_api(prompt) -> text`. Counting calls and the daily cap are the School's job."""
    def teach(task: str, context: str) -> str:
        return ask_api(build_prompt(task, context, writes))
    teach.__name__ = label
    return teach


def gemini_ask() -> Optional[Callable[[str], str]]:
    """The paid API the repo already uses (core.cloudmodel, Gemini), or None when it is not configured or local-only is on."""
    try:
        from core import cloudmodel
        if not cloudmodel.available():
            return None
    except Exception:  # noqa: BLE001
        return None

    def ask(prompt: str) -> str:
        reply = cloudmodel.complete({"role": "advanced", "messages": [{"role": "user", "content": prompt}]})
        return (((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return ask


def local_models(preferred: Optional[List[str]] = None, installed: Optional[Callable[[], List[str]]] = None) -> List[str]:
    """Installed local models to try for high school, smallest/fastest first. Empty when Ollama is not answering."""
    order = preferred or ["qwen3.5:9b", "gemma4:12b-it-qat"]     # the one brain, then the kept backup (2026-11-09)
    try:
        have = set((installed or _ollama_installed)())
    except Exception:  # noqa: BLE001
        return []
    return [m for m in order if m in have]


def _ollama_installed() -> List[str]:
    from . import local
    return local.installed()
