"""Set a person's Command Center profile photo from a file.

    python scripts/set_profile_photo.py <user id or name> <image path>

The image is validated and cropped to a square exactly as an upload from the Command Center is, then stored in that
person's data folder (data/users/<id>/profile/photo.jpg)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def find_user(who: str) -> str | None:
    from core import users
    registry = users.all_users()
    if who in registry:
        return who
    low = who.strip().casefold()
    for uid, person in registry.items():
        if str(person.get("name", "")).strip().casefold() in (low, low.split(" ")[0]) and low:
            return uid
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    from core import profile
    uid = find_user(argv[0])
    if uid is None:
        print(f"No such person: {argv[0]}")
        return 1
    path = Path(argv[1])
    try:
        profile.save_photo(uid, path.read_bytes())
    except OSError as e:
        print(f"Cannot read {path}: {e}")
        return 1
    except profile.ProfileError as e:
        print(str(e))
        return 1
    print(f"Profile photo set for {uid}: {profile.photo_path(uid)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
