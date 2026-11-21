"""One-time Google Calendar sign-in for Jarvis.

    python -m scripts.google_calendar_setup PATH\\TO\\client_secret.json          (the owner)
    python -m scripts.google_calendar_setup PATH\\TO\\client_secret.json --user <name>
    python -m scripts.google_calendar_setup --status
    python -m scripts.google_calendar_setup --disconnect

The client JSON comes from Google Cloud Console: APIs & Services -> Credentials -> Create credentials -> OAuth client ID
-> Desktop app -> Download JSON (Google Calendar API must be enabled on the project). A browser opens; click Allow.
The token is saved in data/users/<id>/google_calendar.json (it syncs to the laptop with data/).
"""
import sys

from core import gcal, users


def main(argv: list[str]) -> int:
    uid = "owner"
    if "--user" in argv:
        i = argv.index("--user")
        uid = users.find(argv[i + 1]) or argv[i + 1]
        del argv[i:i + 2]
    users.set_current(uid)
    if "--status" in argv:
        print("Google Calendar: " + ("connected" if gcal.connected() else "not connected") + f" for {uid}.")
        return 0
    if "--disconnect" in argv:
        print("Disconnected." if gcal.disconnect() else "It wasn't connected.")
        return 0
    if not argv:
        print(__doc__)
        return 2
    try:
        gcal.authorize(gcal.load_client(argv[0]))
        start, end = gcal.day_bounds(gcal.ownertime.now().date())
        n = len(gcal.events(start, end))
    except (gcal.CalendarError, OSError, ValueError) as e:
        print(f"Not connected: {e}")
        return 1
    print(f"Connected for {uid}. Today's calendar has {n} event(s). Try: 'Jarvis, what's on my calendar today?'")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
