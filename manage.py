"""Household admin from the command line (works even when the AI is offline).

  python manage.py people                               list household members and their phones
  python manage.py add-person "Name" wife madame madanm madame
                                                        add someone (relationship, then titles in
                                                        English, Kreyol, French)
  python manage.py pair "Name"                          make a phone-app pairing code (valid 15 min)
  python manage.py unpair "Name"                        unlink all of that person's phones
  python manage.py skill review|install|list|rollback ...   install verified skill packages (see docs/learning/SKILL-INSTALL-GUIDE.md)
"""
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import storage, users  # noqa: E402

CODES_FILE = "pair_codes.json"  # read by the app server (core/appserver.py)


def file_codes() -> dict:
    """Pairing codes made from the command line: {code: {"uid": ..., "expires": ...}}."""
    codes = storage.load_global(CODES_FILE, {})
    now = time.time()
    return {c: v for c, v in codes.items() if v["expires"] > now}


def take_file_code(code: str) -> str | None:
    codes = file_codes()
    entry = codes.pop(code, None)
    storage.save_global(CODES_FILE, codes)
    return entry["uid"] if entry else None


def main(argv: list[str]) -> None:
    storage.migrate_single_user()
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    if cmd == "people":
        for uid, u in users.all_users().items():
            devices = ", ".join(d["name"] for d in u.get("devices", [])) or "no phone linked"
            print(f"{uid}: {u.get('name') or '(no name yet)'} - {u.get('role')} - called "
                  f"'{u['titles'].get('en')}' - {devices}")
    elif cmd == "add-person":
        name, relationship, en, ht, fr = (args + ["", "", "", "", ""])[:5]
        if not name:
            sys.exit('Usage: python manage.py add-person "Name" wife madame madanm madame')
        uid = users.add(name, {"en": en or "sir", "ht": ht or en or "mesye", "fr": fr or en or "monsieur"},
                        role=relationship or "member")
        print(f"Added {name} as '{uid}'. Next: python manage.py pair \"{name}\"")
    elif cmd == "pair":
        uid = users.find(args[0]) if args else "owner"
        if uid is None:
            sys.exit(f"No household member called {args[0]!r}. See: python manage.py people")
        code = f"{secrets.randbelow(900000) + 100000}"
        codes = file_codes()
        codes[code] = {"uid": uid, "expires": time.time() + 15 * 60}
        storage.save_global(CODES_FILE, codes)
        print(f"Pairing code for {users.get(uid).get('name') or uid}: {code}  (valid 15 minutes, works once)")
    elif cmd == "skill":
        from core import skillinstall
        sys.exit(skillinstall.cli(args))
    elif cmd == "unpair":
        uid = users.find(args[0]) if args else None
        if uid is None:
            sys.exit("Who? See: python manage.py people")
        users.update(uid, devices=[])
        print(f"All phones unlinked for {users.get(uid).get('name') or uid}.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
