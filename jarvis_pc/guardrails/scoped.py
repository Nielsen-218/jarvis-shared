"""An approver that says yes only to the exact prompts a person has already been shown and agreed to.

When the user approves a plan in chat ("yes, install it"), the gate's own T2 prompts during that run (the plan, each
download, each delete) must not turn into a blank cheque. ScopedApprover approves a request only if its command
matches one the plan declared; anything else is refused (fail closed) and recorded so it can be reported.
"""
from __future__ import annotations

from typing import Iterable, List, Tuple


class ScopedApprover:
    def __init__(self, exact: Iterable[str] = (), prefixes: Iterable[str] = ()):
        self.exact = set(exact)
        self.prefixes: Tuple[str, ...] = tuple(prefixes)
        self.approved: List[str] = []
        self.refused: List[str] = []

    def __call__(self, req) -> bool:
        cmd = req.command
        ok = cmd in self.exact or any(cmd.startswith(p) for p in self.prefixes if p)
        (self.approved if ok else self.refused).append(cmd)
        return ok
