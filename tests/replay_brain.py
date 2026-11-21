"""A scripted-model replay of the real Brain (core path: Brain.ask -> intelligence.request -> Polisher), shared by the
conversation tests (tests/test_conv_master.py). The model is a stand-in: `script` maps what he said to what the model
writes (a string, a list of strings used in turn, or a callable of the request body). Everything else is the real code:
routing, tool choice, the chat path, the polisher, the one more try. The PC front doors (quick answers, pcfast, the skill
shortcuts) are switched off so a test is about the conversation, not the PC."""
import json

from brain import llm
from core import intelligence, pcfast, quick


class Replay:
    def __init__(self, monkeypatch, script, shortcuts=True):
        monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
        monkeypatch.delenv("JARVIS_LOCAL_ONLY", raising=False)
        monkeypatch.delenv("JARVIS_PC_URL", raising=False)
        monkeypatch.setattr(quick, "handle", lambda text: None)
        monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
        monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
        monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
        self.script, self.calls, self.bodies = script, [], []
        self.used: dict = {}
        monkeypatch.setattr(intelligence, "complete_local", self._complete)
        self.brain = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
        if shortcuts:
            for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
                monkeypatch.setattr(self.brain, shortcut, lambda text, emit: False)
        self.said_now = ""

    def _complete(self, body, cancelled=lambda: False, on_text=None):
        self.bodies.append(body)
        newest = body["messages"][-1]
        text = newest["content"] if isinstance(newest["content"], str) else " ".join(
            p.get("text", "") for p in newest["content"] if isinstance(p, dict))
        self.calls.append((body.get("role"), body.get("profile"), text[-300:]))
        pick = self.script.get(self.said_now) if isinstance(self.script, dict) else self.script
        if callable(pick):
            pick = pick(body)
        if isinstance(pick, (list, tuple)):
            n = self.used.get(self.said_now, 0)
            self.used[self.said_now] = n + 1
            pick = pick[min(n, len(pick) - 1)]
        if isinstance(pick, Exception):
            raise pick
        if on_text and isinstance(pick, str) and not body.get("tools"):
            on_text(pick + " ")
        return {"model": intelligence.model(body.get("role", "fast")),
                "choices": [{"message": {"role": "assistant", "content": pick}, "finish_reason": "stop"}]}

    def say(self, line: str) -> list:
        """He says `line`; what Jarvis said back, sentence by sentence."""
        self.said_now = line
        out: list = []
        self.brain.ask(line, out.append)
        return out
