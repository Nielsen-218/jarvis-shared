"""Small wording helpers. JARVIS says results aloud, so output must read naturally: '1 file', '3 files', never 'file(s)'."""
from __future__ import annotations


def plural(n: int, base: str, one: str = "", many: str = "s") -> str:
    """plural(1, 'file') -> '1 file'; plural(3, 'file') -> '3 files'; plural(2, 'entr', 'y', 'ies') -> '2 entries'."""
    return f"{n} {base}{one if n == 1 else many}"
