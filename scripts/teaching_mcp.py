"""Narrow stdio mentor ingress. Credential stays in the server, not model context.

Explicit launch only; no listener, filesystem tool, shell, grants or practice API.
"""
import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from core import storage, teaching

server = MCPServer("Teach Jarvis")


@server.tool()
def submit_lesson(title: str, topic: str, content: str, examples: str,
                  techniques: str, exercises: str, dependencies: str) -> str:
    """Submit attributed educational data. Does not run code or certify mastery."""
    token = Path(os.environ["JARVIS_MENTOR_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    result = teaching.submit({"title": title, "topic": topic, "content": content,
        "examples": examples, "techniques": techniques, "exercises": exercises,
        "dependencies": dependencies, "source": "Claude Code local-only teaching session",
        "license": "owner-authorized instructor material", "version": "1",
        "tests": "Proposed exercises; no independent tests run by this instructor."}, token)
    return json.dumps({k: result[k] for k in ("id", "mentor", "digest", "state", "revision")})


if __name__ == "__main__":
    storage.DATA = Path(os.environ["JARVIS_TEACHING_DATA_DIR"]).resolve()
    server.run(transport="stdio")
