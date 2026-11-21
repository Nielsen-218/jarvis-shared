"""OS-local mentor CLI. Run from the repo: python -m scripts.teach_jarvis --help.

Tokens travel in private files, never command arguments, stdout or lesson data.
Administration is an OS-owner operation; this is not a remote admin service.
"""
import argparse
import json
import os
from pathlib import Path

from core import storage, teaching


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="Explicit isolated data root for demonstrations")
    sub = parser.add_subparsers(dest="command", required=True)
    grant = sub.add_parser("grant")
    grant.add_argument("mentor")
    grant.add_argument("--token-file", type=Path, required=True)
    grant.add_argument("--hours", type=float, default=24)
    grant.add_argument("--scope",choices=sorted(teaching.SCOPES),action="append")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("mentor")
    for name in ("submit", "search"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--token-file", type=Path, required=True)
        cmd.add_argument("value", help="Lesson JSON path or search words")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.data_dir:
        storage.DATA = args.data_dir.resolve()
    if args.command == "grant":
        # Exclusive creation prevents accidentally overwriting an existing secret.
        with args.token_file.open("x", encoding="utf-8") as file:
            os.chmod(args.token_file, 0o600)
            file.write(teaching.grant(args.mentor, scopes=args.scope or ('submit','read'),hours=args.hours))
        result = {"mentor": args.mentor, "credential_saved": True}
    elif args.command == "revoke":
        teaching.revoke(args.mentor)
        result = {"revoked": args.mentor}
    elif args.command == "status":
        result = teaching.status()
    else:
        token = args.token_file.read_text(encoding="utf-8").strip()
        if args.command == "submit":
            path = Path(args.value)
            if path.stat().st_size > teaching.MAX_TEXT*2:
                raise ValueError("Bundle too large")
            result = teaching.submit(json.loads(path.read_text(encoding="utf-8-sig")), token)
        else:
            result = teaching.search(args.value, token)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
