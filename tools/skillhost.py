"""Installed skill packages as one Jarvis tool: the owner's words -> core/skillhost -> sandboxed, verified execution. No skill names to remember."""
from core import skillhost
from tools.registry import tool


@tool("Run a skill the owner has installed from a verified skill package (for example splitting a restaurant bill). Use when the request matches an "
      "installed skill's verified scope; it asks for any missing essential detail instead of guessing. Pure local computation: no network, files, "
      "apps or cloud calls. Pass the owner's words as `request`.",
      {"request": {"type": "string"}}, direct=True)
def run_installed_skill(request: str) -> str:
    return skillhost.run(request)
