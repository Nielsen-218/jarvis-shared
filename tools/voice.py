"""Household and voice ID: add people, learn each person's voice, the voice lock,
and pairing phones."""
import os
import secrets
import time

from core import storage, users
from tools.registry import tool

voiceid = None   # audio.voiceid.VoiceID, set by main.py in voice mode
listener = None  # audio.listener.Listener
speaker = None   # audio.tts.Speaker
lock_on = storage.load_global("settings.json", {}).get("voice_lock", True)  # only obey known voices
pending_enrollment: str | None = None  # person id; the main loop runs the recording
pair_codes: dict[str, str] = {}  # code -> person id, for linking phones

ENROLL_SECONDS = 25


def _person(person: str) -> str | None:
    return users.current() if person.strip().lower() in ("me", "myself", "") else users.find(person)


@tool(
    "Add a person to the household (e.g. the user's wife) so Jarvis can serve them too, with their "
    "own memories, wardrobe, lessons and voice. Ask how they'd like to be addressed first.",
    {
        "name": {"type": "string", "description": "Their first name"},
        "relationship": {"type": "string", "description": "e.g. 'wife', 'son', 'mother'"},
        "title_english": {"type": "string", "description": "How to address them in English, e.g. 'madame'"},
        "title_kreyol": {"type": "string", "description": "In Kreyol, e.g. 'madanm'"},
        "title_french": {"type": "string", "description": "In French, e.g. 'madame'"},
    },
)
def add_household_member(name: str, relationship: str, title_english: str, title_kreyol: str,
                         title_french: str) -> str:
    existing = users.find(name)
    uid = users.add(name, {"en": title_english, "ht": title_kreyol, "fr": title_french}, role=relationship)
    return (f"{'Updated' if existing else 'Added'} {name} ({relationship}), id '{uid}'. Next: learn their voice "
            "with enroll_voice, and they can link their phone with pair_phone.")


@tool(
    "Learn someone's voice so Jarvis knows who's speaking and ignores strangers. Before calling, tell "
    "the person: after the beep, talk naturally for about twenty-five seconds (their day, anything), and stop "
    "at the second beep. The recording starts right after your reply. Anyone other than 'me' must be "
    "in the household first (add_household_member).",
    {"person": {"type": "string", "description": "'me' for whoever is speaking, or a household member's name"}},
)
def enroll_voice(person: str) -> str:
    global pending_enrollment
    if voiceid is None or listener is None:
        return "Voice ID only works in voice mode."
    uid = _person(person)
    if uid is None:
        return f"I don't know '{person}' yet; add them with add_household_member first."
    pending_enrollment = uid
    who = users.get(uid).get("name") or "your"
    return (f"Recording {who + chr(39) + 's' if who != 'your' else 'your'} voice starts with a beep as soon as "
            "you finish this reply. Keep the reply to one short sentence.")


def run_enrollment(say) -> None:
    """Called by the main loop after Jarvis finishes speaking: beep, record, beep, save."""
    global lock_on, pending_enrollment
    from audio.tts import beep

    uid, pending_enrollment = pending_enrollment, None
    time.sleep(0.4)
    beep(880, 0.15)
    speech = listener.record_speech(ENROLL_SECONDS)
    beep(660, 0.15)
    print(f"[voice] enrollment for {uid}: {len(speech) / 16000:.1f}s of speech captured")
    result = voiceid.enroll(speech, uid)
    if "saved" in result:
        lock_on = True
        settings = storage.load_global("settings.json", {})
        settings["voice_lock"] = True
        storage.save_global("settings.json", settings)
        name = users.get(uid).get("name")
        result += (f" I'll recognize {name} by voice from now on" if name else " I'll recognize you from now on") + \
            ", and ignore voices I don't know."
    say(result)


@tool(
    "Turn the voice lock on or off (on = ignore voices that aren't from the household), or forget "
    "a person's voiceprint.",
    {"setting": {"type": "string", "enum": ["on", "off", "forget"]},
     "person": {"type": "string", "description": "For 'forget': 'me' or a member's name; otherwise empty"}},
)
def voice_lock(setting: str, person: str) -> str:
    global lock_on
    if voiceid is None:
        return "Voice ID only works in voice mode."
    if setting == "forget":
        voiceid.forget(_person(person))
        return "Voiceprint deleted."
    if setting == "on" and not voiceid.enrolled:
        return "I haven't learned anyone's voice yet; use enroll_voice first."
    lock_on = setting == "on"
    settings = storage.load_global("settings.json", {})
    settings["voice_lock"] = lock_on
    storage.save_global("settings.json", settings)
    return "Voice lock on." if lock_on else "Voice lock off: I'll respond to anyone."


@tool(
    "Give a pairing code to link a household member's phone to Jarvis: the Jarvis phone app (enter the "
    "code on its welcome screen) or the Telegram bot (send '/pair <code>'). Say the code clearly; it "
    "also shows on the HUD and works once.",
    {"person": {"type": "string", "description": "'me' or a household member's name"}},
)
def pair_phone(person: str) -> str:
    from core import hud

    uid = _person(person)
    if uid is None:
        return f"I don't know '{person}' yet; add them with add_household_member first."
    name = users.get(uid).get("name") or "the owner"
    server = os.getenv("JARVIS_SERVER_SSH")  # phones link to the always-on laptop: make the code there
    if server:
        return _pair_on_server(server, uid, name)
    if os.getenv("JARVIS_SERVER_URL"):
        # This machine reaches a laptop (the vault, the phone link) and phones connect to THAT app server, which never sees a
        # code kept in this process: spoken, it would always be refused with "That code isn't valid" (cross-device audit 7).
        return ("I can't make a pairing code that your phone will accept from here, because phones link through the "
                "laptop and I have no way set up to make one there. On the laptop, run python manage.py pair "
                f"with {name}'s name, and enter the code it prints on the app's welcome screen.")
    code = f"{secrets.randbelow(900000) + 100000}"
    pair_codes[code] = uid
    hud.line("sys", f"📱 PAIRING CODE: {code}   —  enter it in the Jarvis app")
    hud.show()
    spaced = ", ".join(code)
    tg = ", or send slash pair then the code to the Telegram bot" if os.getenv("TELEGRAM_BOT_TOKEN") else ""
    return (f"Pairing code for {name}: {code}. Say it out loud, reading each digit: \"Your pairing code is "
            f"{spaced}. It's on your screen too — enter it on the Jarvis app welcome screen{tg}.\" Always "
            "speak the full six-digit code; never shorten it or just say it's done.")


def _pair_on_server(server: str, uid: str, name: str) -> str:
    """JARVIS_SERVER_SSH='user@host' (key in JARVIS_SERVER_KEY): the code is made by the laptop's
    manage.py, so the laptop's app server accepts it."""
    import re
    import subprocess

    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if os.getenv("JARVIS_SERVER_KEY"):
        cmd += ["-i", os.path.expanduser(os.environ["JARVIS_SERVER_KEY"])]
    cmd += [server, f"cd ~/jarvis && .venv/bin/python manage.py pair {uid}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return f"I couldn't reach the Jarvis server to make a code: {e}"
    m = re.search(r"\b(\d{6})\b", r.stdout or "")
    if not m:
        return "The Jarvis server didn't give me a code: " + ((r.stderr or r.stdout or "").strip()[-200:])
    from core import hud

    code = m.group(1)
    url = os.getenv("JARVIS_SERVER_URL", "the Jarvis app")
    hud.line("sys", f"📱 PAIRING CODE: {code}   —  enter it in the Jarvis app (valid 15 min)")
    hud.show()
    spaced = ", ".join(code)  # read digit by digit, e.g. "7, 3, 6, 6, 5, 8"
    return (f"Pairing code for {name}: {code} (valid 15 minutes). Say it out loud, reading each digit: "
            f"\"Your pairing code is {spaced}. It's on your screen too — open {url} on your phone and type "
            "it on the welcome screen.\" Always speak the full six-digit code; never shorten it or just say "
            "it's done.")


def active() -> bool:
    return voiceid is not None and voiceid.enrolled and lock_on


@tool(
    "Save the current person's own name and how they like to be addressed (e.g. when they introduce "
    "themselves or ask to be called something else).",
    {"name": {"type": "string"},
     "title_english": {"type": "string", "description": "e.g. 'sir', or their name"},
     "title_kreyol": {"type": "string", "description": "e.g. 'mesye'"},
     "title_french": {"type": "string", "description": "e.g. 'monsieur'"}},
)
def set_my_details(name: str, title_english: str, title_kreyol: str, title_french: str) -> str:
    uid = users.current()
    users.update(uid, name=name.strip(), titles={"en": title_english, "ht": title_kreyol, "fr": title_french})
    return f"Saved: {name}, addressed as '{title_english}'. (Takes full effect in the next conversation.)"


manual_speaker: str | None = None  # set by switch_person; cleared when the conversation ends


@tool(
    "Switch who you're serving in this conversation, when someone says who's talking ('this is "
    "Maya', 'my wife wants to ask you something', 'it's me again'). Their own memories, wardrobe, "
    "avatar and lists are used from their next message until the conversation ends. Voice ID, once "
    "learned, does this automatically.",
    {"person": {"type": "string", "description": "A household member's name or relationship, or 'owner'"}},
)
def switch_person(person: str) -> str:
    global manual_speaker
    uid = "owner" if person.strip().lower() in ("owner", "me", "him", "the owner") else users.find(person)
    if uid is None:
        return f"I don't know '{person}'; add them with add_household_member first."
    manual_speaker = uid
    u = users.get(uid)
    return (f"From the next message I'm serving {u.get('name') or 'the owner'} (addressed as "
            f"'{users.title(uid)}'), with their own memories, wardrobe and avatar.")
