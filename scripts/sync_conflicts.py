"""Inventory of Syncthing conflict copies under data/ and what, if anything, they hold that the live file lacks.

    python scripts/sync_conflicts.py            # the repo's data/ folder
    python scripts/sync_conflicts.py /path/to/data [--json]

Read-only: nothing is deleted, moved or merged. A copy is reported with the records (JSON items / SQLite rows) found
only in it and only in the live file, so the owner can decide. Conflict copies are named
<name>.sync-conflict-<date>-<time>-<device>.<ext>; the live file is <name>.<ext>."""
import json
import re
import sqlite3
import sys
from pathlib import Path

CONFLICT = re.compile(r"\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]{7}")


def live_path(copy: Path) -> Path:
    return copy.with_name(CONFLICT.sub("", copy.name))


def _json_records(value) -> set[str]:
    """Every record in a JSON store as a comparable string: list items, or for a dict each (key, item) of its
    list values and each (key, value) of the rest."""
    if isinstance(value, list):
        return {json.dumps(x, sort_keys=True) for x in value}
    if isinstance(value, dict):
        out = set()
        for k, v in value.items():
            if isinstance(v, list):
                out |= {json.dumps({k: x}, sort_keys=True) for x in v}
            else:
                out.add(json.dumps({k: v}, sort_keys=True))
        return out
    return {json.dumps(value)}


def _sqlite_rows(path: Path) -> dict[str, set[str]]:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {t: {repr(r) for r in db.execute(f'SELECT * FROM "{t}"')}
                for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()


def compare(copy: Path) -> dict:
    live = live_path(copy)
    row = {"copy": str(copy), "live": str(live), "live_exists": live.exists(), "kind": copy.suffix.lstrip(".")}
    if not live.exists():
        row["verdict"] = "no live file: the copy is the only version"
        return row
    try:
        if copy.suffix == ".json":
            a, b = _json_records(json.loads(copy.read_text(encoding="utf-8-sig"))), \
                   _json_records(json.loads(live.read_text(encoding="utf-8-sig")))
            row["only_in_copy"], row["only_in_live"] = len(a - b), len(b - a)
            row["sample_only_in_copy"] = sorted(a - b)[:3]
        elif copy.suffix in (".sqlite3", ".sqlite", ".db"):
            a, b = _sqlite_rows(copy), _sqlite_rows(live)
            row["only_in_copy"] = sum(len(a[t] - b.get(t, set())) for t in a)
            row["only_in_live"] = sum(len(b[t] - a.get(t, set())) for t in b)
        else:
            same = copy.read_bytes() == live.read_bytes()
            row["only_in_copy"], row["only_in_live"] = (0, 0) if same else (1, 1)
    except Exception as e:                                       # noqa: BLE001 - a copy that can't be read is reported
        row["verdict"] = f"unreadable: {type(e).__name__}"
        return row
    row["verdict"] = ("identical records" if not row["only_in_copy"] and not row["only_in_live"] else
                      "live file has everything in the copy" if not row["only_in_copy"] else
                      f"COPY HOLDS {row['only_in_copy']} RECORD(S) THE LIVE FILE LACKS")
    return row


def main(argv) -> int:
    args = [a for a in argv if not a.startswith("--")]
    data = Path(args[0]) if args else Path(__file__).resolve().parent.parent / "data"
    copies = sorted(p for p in data.rglob("*") if p.is_file() and CONFLICT.search(p.name) and ".stversions" not in p.parts)
    rows = [compare(p) for p in copies]
    if "--json" in argv:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{Path(r['copy']).relative_to(data)}\n    -> {r['verdict']}")
        unique = [r for r in rows if r.get("only_in_copy")]
        print(f"\n{len(rows)} conflict copies under {data}; {len(unique)} hold records the live file lacks. "
              "Nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
