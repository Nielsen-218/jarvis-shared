"""Reach Jarvis from your phone through a private Telegram bot.

Setup (one time):
1. In Telegram, message @BotFather, send /newbot, pick a name. It gives you a token.
2. Put it in .env as TELEGRAM_BOT_TOKEN=...   (never paste it into a chat)
3. Restart Jarvis. It shows a pairing code for the owner; send  /pair <code>  to the bot.
   Other household members get their own code ("Jarvis, pair my wife's phone").
   Only paired Telegram accounts can use the bot; each is served as themselves.

What works from the phone: anything you'd say to Jarvis (text or voice notes),
photos ("does this go with my wardrobe?", "what size is this?"), and Jarvis
sends you reminders, price drops and try-on images.
"""
import json
import mimetypes
import secrets
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from core import hud, notify

API = "https://api.telegram.org"


class PhoneBot:
    def __init__(self, token: str, make_brain, transcriber=None):
        from core import users

        self.token = token
        self.make_brain = make_brain  # make_brain(uid) -> Brain; one conversation per person
        self.brains: dict = {}
        self.stt = transcriber
        self.owner = users.get("owner").get("telegram")
        self.pair_code = None
        if not self.owner:  # the owner's first pairing code is shown at startup
            from tools import voice

            self.pair_code = f"{secrets.randbelow(900000) + 100000}"
            voice.pair_codes[self.pair_code] = "owner"
        self._outboxes: dict[int, list] = {}  # images produced while answering, per request thread

    def _brain(self, uid: str):
        if uid not in self.brains:
            b = self.make_brain(uid)
            b.live = False   # one gathered reply per message: no "Right away, sir" / progress lines delivered after the result
            self.brains[uid] = b
        return self.brains[uid]

    # -- Telegram API -------------------------------------------------------------------
    def _call(self, method: str, params: dict | None = None, timeout: int = 40) -> dict:
        data = urllib.parse.urlencode(params or {}).encode()
        with urllib.request.urlopen(f"{API}/bot{self.token}/{method}", data=data, timeout=timeout) as r:
            return json.loads(r.read())

    def send(self, text: str, chat_id=None) -> None:
        chat_id = chat_id or self.owner
        if not chat_id or not text.strip():
            return
        for i in range(0, len(text), 4000):  # Telegram's limit is 4096 characters
            self._call("sendMessage", {"chat_id": chat_id, "text": text[i:i + 4000]})

    def send_photo(self, path: str, caption: str = "", chat_id=None) -> None:
        chat_id = chat_id or self.owner
        boundary = uuid.uuid4().hex
        data = Path(path).read_bytes()
        ctype = mimetypes.guess_type(path)[0] or "image/jpeg"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:1000]}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{Path(path).name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        req = urllib.request.Request(f"{API}/bot{self.token}/sendPhoto", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        urllib.request.urlopen(req, timeout=60).read()

    def _download(self, file_id: str) -> bytes:
        info = self._call("getFile", {"file_id": file_id})["result"]
        with urllib.request.urlopen(f"{API}/file/bot{self.token}/{info['file_path']}", timeout=60) as r:
            return r.read()

    # -- main loop -----------------------------------------------------------------------
    def start(self) -> None:
        me = self._call("getMe")["result"]
        print(f"[phone] Telegram bot @{me['username']} is online")
        if self.pair_code:
            print(f"[phone] To link your phone, send this to @{me['username']}:  /pair {self.pair_code}")
            hud.line("sys", f"Phone pairing: send '/pair {self.pair_code}' to @{me['username']} on Telegram")
        notify.add_channel(self._notify)
        hud.add_media_listener(self._collect_media)
        threading.Thread(target=self._poll, daemon=True, name="phone").start()

    def _poll(self) -> None:
        offset = 0
        while True:
            try:
                updates = self._call("getUpdates", {"timeout": 30, "offset": offset}, timeout=40)["result"]
                for u in updates:
                    offset = u["update_id"] + 1
                    msg = u.get("message")
                    if msg:
                        threading.Thread(target=self._handle, args=(msg,), daemon=True).start()
            except Exception as e:
                print(f"[phone] {type(e).__name__}: {e}")
                time.sleep(5)

    def _notify(self, title: str, msg: str, uid: str | None) -> None:
        """Reminders, price drops...: to that person's phone, or everyone's for household ones."""
        from core import users

        targets = [users.get(uid)] if uid else list(users.all_users().values())
        for person in targets:
            if person.get("telegram"):
                self.send(f"🔔 {title}: {msg}", person["telegram"])

    def _handle(self, msg: dict) -> None:
        from core import users
        from tools import voice

        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or msg.get("caption") or "").strip()
        uid = users.by_telegram(chat_id)
        if uid is None:  # unknown phone: only a valid pairing code is accepted
            code = text[len("/pair "):].strip() if text.startswith("/pair ") else ""
            if code and code in voice.pair_codes:
                uid = voice.pair_codes.pop(code)
                users.update(uid, telegram=chat_id)
                if uid == "owner":
                    self.owner, self.pair_code = chat_id, None
                title = users.title(uid)
                self.send(f"Paired. Hello, {title}. Message me any time: text, voice notes or photos.", chat_id)
                hud.line("sys", f"Phone paired: {users.get(uid).get('name') or 'owner'}")
            else:
                self._call("sendMessage", {"chat_id": chat_id, "text": "This is a private assistant."})
            return

        content = []
        if msg.get("photo"):
            import base64

            img = self._download(msg["photo"][-1]["file_id"])  # largest size
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(img).decode()}})
        if msg.get("voice") or msg.get("audio"):
            heard = self._transcribe(self._download((msg.get("voice") or msg.get("audio"))["file_id"]))
            if not heard:
                self.send(f"I couldn't make out that voice note, {users.title(uid)}.", chat_id)
                return
            text = (text + " " + heard).strip()
            self.send(f"🎙 {heard}", chat_id)
        if not text and not content:
            return
        content.append({"type": "text", "text": "[Sent from their phone via Telegram. Reply in plain "
                                                "text, no markdown; they may be away from the PC.] " + (text or "(photo)")})
        self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        name = users.get(uid).get("name") or ""
        hud.line("you", f"📱 {name + ': ' if name else ''}{text or '(photo)'}")
        said: list[str] = []
        me = threading.get_ident()
        self._outboxes[me] = []
        self._brain(uid).ask(content, said.append)
        reply = " ".join(said)
        self.send(reply, chat_id)
        hud.line("jarvis", f"📱 {reply[:200]}")
        for path, caption in self._outboxes.pop(me, []):
            try:
                self.send_photo(path, caption, chat_id)
            except Exception as e:
                print(f"[phone] photo failed: {e}")

    def _collect_media(self, path, caption: str) -> None:
        box = self._outboxes.get(threading.get_ident())
        if box is not None and path and Path(path).exists():
            box.append((str(path), caption))

    def _transcribe(self, data: bytes) -> str:
        if self.stt is None:
            return ""
        return self.stt.transcribe(decode_to_16k(data))


def decode_to_16k(data: bytes):
    """Decode a voice note (Telegram sends OGG/Opus; MP3/M4A also work) to 16 kHz mono int16."""
    import io

    import av
    import numpy as np

    chunks = []
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.int16) if chunks else np.zeros(0, np.int16)
