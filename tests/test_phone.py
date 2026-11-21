"""Offline tests for the Telegram phone bot (fake Telegram API, fake brains)."""
from core import users
from core.phone import PhoneBot


class FakeBrain:
    def __init__(self, uid):
        self.uid = uid
        self.asked = []

    def ask(self, content, on_sentence):
        self.asked.append(content)
        on_sentence(f"Certainly ({self.uid}).")


def make_bot(monkeypatch):
    brains = {}

    def make_brain(uid):
        brains[uid] = FakeBrain(uid)
        return brains[uid]

    bot = PhoneBot("TOKEN", make_brain)
    sent, api = [], []
    monkeypatch.setattr(bot, "_call", lambda method, params=None, timeout=40: api.append((method, params)) or {"result": {}})
    monkeypatch.setattr(bot, "send", lambda text, chat_id=None: sent.append((chat_id, text)))
    return bot, brains, sent, api


def test_owner_pairing_and_strangers(monkeypatch):
    bot, brains, sent, api = make_bot(monkeypatch)
    code = bot.pair_code
    assert code and len(code) == 6
    bot._handle({"chat": {"id": 666}, "text": "/pair 000000"})  # wrong code
    assert users.by_telegram(666) is None and api[-1][1]["text"] == "This is a private assistant."
    bot._handle({"chat": {"id": 42}, "text": f"/pair {code}"})
    assert users.by_telegram(42) == "owner" and "Hello, sir" in sent[-1][1]
    bot._handle({"chat": {"id": 666}, "text": "what's the time?"})  # stranger after pairing
    assert brains == {}


def test_wife_pairs_and_is_served_as_herself(monkeypatch):
    from tools import voice

    users.update("owner", telegram=42)
    out = voice.add_household_member("Marie", "wife", "madame", "madanm", "madame")
    uid = users.find("Marie")
    assert uid and "Added Marie" in out
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    bot, brains, sent, _ = make_bot(monkeypatch)
    out = voice.pair_phone("Marie")
    code = out.split("code for Marie: ")[1].split(".")[0]
    bot._handle({"chat": {"id": 77}, "text": f"/pair {code}"})
    assert users.by_telegram(77) == uid and "Hello, madame" in sent[-1][1]
    bot._handle({"chat": {"id": 77}, "text": "Add rice to the shopping list"})
    assert uid in brains and sent[-1] == (77, f"Certainly ({uid}).")
    bot._handle({"chat": {"id": 42}, "text": "hi"})
    assert "owner" in brains and sent[-1] == (42, "Certainly (owner).")


def test_notifications_go_to_the_right_phone(monkeypatch):
    users.update("owner", telegram=42)
    wife = users.add("Marie", {"en": "madame"}, "wife")
    users.update(wife, telegram=77)
    bot, _, sent, _ = make_bot(monkeypatch)
    bot._notify("Reminder", "dentist", wife)
    assert sent == [(77, "🔔 Reminder: dentist")]
    sent.clear()
    bot._notify("Household reminder", "bins out", None)
    assert sorted(c for c, _ in sent) == [42, 77]


def test_photo_is_attached_and_images_come_back(monkeypatch, tmp_path):
    users.update("owner", telegram=42)
    bot, brains, sent, _ = make_bot(monkeypatch)
    monkeypatch.setattr(bot, "_download", lambda file_id: b"\xff\xd8fakejpeg")
    photos = []
    monkeypatch.setattr(bot, "send_photo", lambda path, caption="", chat_id=None: photos.append((path, chat_id)))
    img = tmp_path / "tryon.jpg"
    img.write_bytes(b"x")
    brain = bot._brain("owner")
    brain.ask = lambda content, say: (brain.asked.append(content), bot._collect_media(img, "Try-on"), say("Here you go."))
    bot._handle({"chat": {"id": 42}, "photo": [{"file_id": "small"}, {"file_id": "big"}], "caption": "Does this match?"})
    assert brain.asked[0][0]["type"] == "image"
    assert photos == [(str(img), 42)]


def test_a_spoken_pairing_code_is_refused_when_phones_link_through_a_laptop_and_no_ssh_is_set(monkeypatch):
    """Cross-device audit 7: a code kept in the PC's memory is never seen by the laptop's /api/pair."""
    from tools import voice

    monkeypatch.delenv("JARVIS_SERVER_SSH", raising=False)
    monkeypatch.setenv("JARVIS_SERVER_URL", "http://laptop.example:8765")
    before = dict(voice.pair_codes)
    out = voice.pair_phone("me")
    assert "manage.py pair" in out and "can't make a pairing code" in out and not any(ch.isdigit() for ch in out.split("manage.py")[0])
    assert voice.pair_codes == before, "no code was stored"
    monkeypatch.delenv("JARVIS_SERVER_URL")
    assert "Pairing code for" in voice.pair_phone("me"), "a single machine still pairs its own phone"
