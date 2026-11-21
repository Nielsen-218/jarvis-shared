"""One thread across machines (plan upgrade 10): what was just said on the phone is known at the PC, and the other way.

Each finished exchange with Jarvis goes into this machine's own file, per person: data/users/<uid>/thread-<machine>.jsonl
(machine "pc" or "server", as core.awareness and core.journal name them). Syncthing shares data/ between the PC and the
laptop, and every file has one writer, so it never has two versions of one file to choose between. Rows are appended;
the file is rewritten only by its own machine, to drop rows past RETENTION or what he asked Jarvis to forget.

A new conversation's <background> (tools.memory.context_block, built once per conversation, so the cached prompt prefix
stays stable) carries the OTHER machine's last exchanges: "Recently on your phone (...)". What is said there later in the
same conversation (he switches device inside its ten minutes) comes as a <recently-elsewhere> block on that turn's
message (news(), brain.llm), after the cached prefix. Per person: only that person's own folder is ever read, so one
household member's thread never reaches another's prompt.

The laptop's Syncthing sends its changes only on its hourly rescan (its file watcher cannot start), so the PC also
fetches the laptop's own rows over the PC-to-laptop link it already has (refresh(), /api/thread/recent); and the laptop, whose
phone chat needs the PC's voice turns, fetches them over the pcbridge link (/api/pc/thread, same owner auth).

The same words are in the 24-hour Daily Awareness log (core.awareness), which honours "forget the last ten minutes" and
"delete these lines": core.awareness.delete calls forget() / forget_lines() here too, and nothing here is kept longer than
that log keeps it. Developer verification turns and the acceptance harness are never recorded (recording()).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime

from core import storage, users
from core.words import plural

WINDOW_S = 2 * 3600        # how far back the other machine's exchanges are shown
LIMIT = 6                  # at most this many of them
KEEP_CHARS = 300           # each side of an exchange, as stored
SHOW_CHARS = 160           # each side, as shown in the background
BACKGROUND_CHARS = 2000    # the whole block in the background, ~470 tokens (core.contextfit: outside the core budget)
RETENTION = 24 * 3600      # nothing older is kept (as core.awareness.RETENTION)
COMPACT_AFTER = 3600       # the oldest row may be this much past RETENTION before the file is rewritten
MATCH_S = 30 * 60          # a deleted Daily Awareness line and the exchange holding it were said this close together
# A card number as people say or type it, grouped ("4111 1111 1111 1111", "4111-1111-...") or unbroken.
_SECRET = re.compile(r"(password|passcode|api[ _-]?key|\btoken\b|secret|\b\d(?:[ -]?\d){11,18}\b|\bsk-[a-z0-9-]{10,})", re.I)
# Every note an app server puts before his words (typed chat, voice notes and live mode, the camera, Alexa, Telegram):
# instructions to Jarvis about the channel, never what he said.
_CHANNEL_NOTE = re.compile(r"^\s*\[(?:Sent from their |Spoken into their |Spoken to Jarvis through |Live camera from their )"
                           r"[^\]]*\]\s*")
_lock = threading.Lock()


def machine() -> str:
    from core import pcbridge
    return "pc" if pcbridge.local_pc() else "server"


def _other(name: str) -> str:
    return "server" if name == "pc" else "pc"


def _path(uid: str, name: str):
    return storage.user_dir(uid) / f"thread-{name}.jsonl"


def _clip(text, limit: int) -> str:
    text = " ".join(_CHANNEL_NOTE.sub("", str(text or "")).split())
    if _SECRET.search(text):        # the whole line, not only the word: "my wifi password is ..." keeps nothing
        return "[not kept: it named a password, key or card number]"
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _rows(path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue                    # a line cut short by a crash or caught mid-sync: skipped, never fatal
        if isinstance(row, dict) and isinstance(row.get("ts"), (int, float)):
            out.append(row)
    return out


PATIENT_S = 2.0            # a forget waits this long for a reader holding the file (Syncthing hashing it, an antivirus scan)


def _rewrite(path, rows: list[dict], patient: bool = False) -> None:
    """With _lock held: this machine's own file, rewritten in one step (LF endings, as every machine writes them).
    `patient` (a forget: he is told it is gone): a reader holding the file without sharing delete is waited out for up
    to PATIENT_S, not only core.fileio's ~150 ms. A rewrite that still fails leaves no .tmp copy in the synced folder."""
    from core import fileio
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8"))
    deadline = time.monotonic() + (PATIENT_S if patient else 0.0)
    while True:
        try:
            fileio.replace_atomic(tmp, path)
            return
        except PermissionError:
            if time.monotonic() < deadline:
                time.sleep(0.1)
                continue
            try:
                tmp.unlink()
            except OSError:
                pass
            raise


def recording() -> bool:
    """Whether finished exchanges go into the ledger. Not a developer's verification turn (a 'claude-verify' device:
    core.gateway.TEST_TURN, as its misses are not the owner's experience either), and not while JARVIS_THREAD=off (the
    acceptance harnesses that run Brain.ask on the real data folder set it)."""
    if _off():
        return False
    import os
    if os.environ.get("JARVIS_TEST_TRAFFIC") == "1":
        return False                     # a harness or live check marked as test traffic (core.turntrace.test_traffic)
    try:
        from core import gateway
        return not getattr(gateway.TEST_TURN, "on", False)
    except Exception:  # noqa: BLE001  (no gateway here: nothing marks a test turn)
        return True


def append(uid: str, said: str, reply: str, channel: str, cut: bool = False, now: float | None = None) -> dict | None:
    """One finished exchange of `uid`'s on this machine: their words, Jarvis's reply (KEEP_CHARS each, secrets hidden),
    the channel ('voice'; 'phone': the laptop's app server; 'typed': the PC's, the companion; 'text': Telegram) and when.
    `cut`: he cut the reply off; `reply` is then what he heard of it (the voice) or what was still delivered (the phone)."""
    if not recording():
        return None
    said, reply = _clip(said, KEEP_CHARS), _clip(reply, KEEP_CHARS)
    if not said and not reply:
        return None
    now = time.time() if now is None else now
    row = {"ts": round(now, 1), "channel": str(channel or "app")[:12], "said": said, "reply": reply}
    if cut:
        row["cut"] = True
    path = _path(uid, machine())
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        try:
            with open(path, encoding="utf-8") as f:
                first = json.loads(f.readline() or "{}")
        except ValueError:
            first = {}
        if float(first.get("ts") or now) < now - RETENTION - COMPACT_AFTER:
            _rewrite(path, [r for r in _rows(path) if r["ts"] >= now - RETENTION])
    try:              # the core scorecard keeps the owner's everyday commands longer than this day-long ledger
        from core import scorecard
        scorecard.note(uid, said, reply, row["channel"], cut=cut, now=now)
    except Exception as error:  # noqa: BLE001
        print(f"[thread] scorecard: {type(error).__name__}: {error}")
    return row


def amend(uid: str, row: dict, heard: str) -> bool:
    """He cut a reply off while it was still being spoken, after its row was written: the row keeps what he heard of it,
    marked cut (the other machine is never told he heard sentences he did not)."""
    path = _path(uid, machine())
    with _lock:
        rows = _rows(path)
        for r in reversed(rows):
            if r.get("ts") == row.get("ts") and r.get("said") == row.get("said"):
                r["reply"], r["cut"] = _clip(heard, KEEP_CHARS), True
                _rewrite(path, rows)
                return True
    return False


def _norm(text) -> str:
    return " ".join(_CHANNEL_NOTE.sub("", str(text or "")).split()).casefold().rstrip("…").strip()


def _holds(row: dict, text: str) -> bool:
    """Whether this row holds that Daily Awareness line (his words, or Jarvis's reply or one sentence of it)."""
    line = _norm(text)
    if len(line) < 3:
        return False
    for field in (row.get("said"), row.get("reply")):
        kept = _norm(field)
        if kept and (kept == line or line in kept or (len(kept) >= 20 and line.startswith(kept))):
            return True
    return False


_owed: dict = {}       # this machine's thread file -> the forgets its file hold blocked, each still to be finished


def _asked(row: dict, ask: dict) -> bool:
    """Whether one forget asks for this row. An owed forget reaches only the rows there were when it was asked
    ('until': the newest row then): what he said after it, he never asked to forget."""
    if ask.get("until") is not None and row["ts"] > ask["until"]:
        return False
    if ask["everything"] or (ask["after"] is not None and row["ts"] >= ask["after"]):
        return True
    return any(abs(row["ts"] - t) <= MATCH_S and _holds(row, text) for t, text in ask["lines"])


def _drop(path, after: float | None = None, everything: bool = False, lines: list | tuple = ()) -> int:
    """Drop this machine's rows in `path`: all of them, those from `after` on, or those holding one of `lines` (said
    within MATCH_S of it), together with what an earlier forget the file hold blocked still had to drop. When the
    rewrite fails each forget is kept (_owed) with the rows it covered, and the error raised: the awareness lines it was
    asked for are already gone, so asking again ('forget the last ten minutes' a minute later, the Memory page's delete)
    finishes what was ASKED, never only what is left to find, and never what was said since (review 2026-11-11)."""
    key = str(path)
    with _lock:
        asks = [*_owed.pop(key, ()), {"after": after, "everything": everything, "lines": list(lines), "until": None}]
        asks = [a for a in asks if a["everything"] or a["after"] is not None or a["lines"]]
        if not asks:
            return 0
        rows = _rows(path)
        keep = [r for r in rows if not any(_asked(r, a) for a in asks)]
        if len(keep) != len(rows):
            try:
                _rewrite(path, keep, patient=True)
            except BaseException:
                newest = max(r["ts"] for r in rows)
                _owed[key] = [a if a["until"] is not None else {**a, "until": newest} for a in asks]
                raise
        return len(rows) - len(keep)


def forget_lines(uid: str | None = None, lines: list | tuple = ()) -> int:
    """Drop this machine's rows that hold any of these deleted Daily Awareness lines ((time, text) pairs: the
    awareness page's 'delete these lines'), said within MATCH_S of the line. Called for every such delete, even with
    no line of this machine's: the other machine's lines were deleted there, so no older copy of its rows is shown."""
    lines = [(float(t or 0), str(text or "")) for t, text in lines if text]
    uid = uid or users.current()
    gone = _drop(_path(uid, machine()), lines=lines)
    _forgotten(uid)
    return gone


# -- the other machine's rows -----------------------------------------------------------------------------------------
# The laptop's Syncthing sends its changes only on its hourly rescan (2026-11-11: its file watcher cannot start, the data
# folder has more directories than the inotify limit), so a line said on the phone could reach the PC an hour late. The
# PC therefore also fetches the laptop's own recent rows over the PC-to-laptop link it already has (tools.vault: the
# JARVIS_SERVER_URL the PC calls for the vault and Daily Awareness), in the background, and a fetched copy newer than the
# synced file is the one shown. After a forget, an older copy of the other machine's rows is never shown.
PULL_EVERY = 15.0      # at most one fetch per person this often
PULL_TTL = 600.0       # a fetched copy older than this is not used
PULL_TIMEOUT = 4.0
_pulled: dict[str, tuple[float, list]] = {}    # uid -> (when fetched, the other machine's rows)
_pull_started: dict[str, float] = {}
_forgot_at: dict[str, float] = {}             # uid -> when this machine was last told to forget something
_pull_lock = threading.Lock()


def _off() -> bool:
    return os.getenv("JARVIS_THREAD", "on").strip().lower() in ("off", "0", "false", "no")


def _remote():
    """The link to the other machine, or None. The PC calls the laptop (tools.vault: JARVIS_SERVER_URL); the laptop calls
    the PC over the existing pcbridge (JARVIS_PC_URL, the PC owner's token) - 2026-11-13, so a line said at the desk is on
    the phone's side promptly instead of an hour later."""
    if _off():
        return None
    try:
        if machine() == "pc":
            from tools import vault
            return vault._remote()
        from core import pcbridge
        return pcbridge._address()
    except Exception:  # noqa: BLE001 - not configured
        return None


def _linked_for(uid: str) -> bool:
    """The PC link carries the PC OWNER's token, so over it only the owner's own thread can be fetched."""
    if machine() == "pc":
        return True
    return users.all_users().get(uid, {}).get("role") == "owner"


def _fetch(uid: str) -> list | None:
    """The laptop's own rows of `uid`'s, as it holds them now (None when it cannot be asked)."""
    remote = _remote()
    if not remote:
        return None
    if machine() != "pc":
        from core import pcbridge
        with users.acting_as(uid):
            data = pcbridge.json_request("/api/pc/thread")        # short timeout (pcbridge.SLOW_TIMEOUTS), PCError when asleep
        return _clean(data, uid)
    url, token = remote
    req = urllib.request.Request(url + "/api/thread/recent", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=PULL_TIMEOUT) as response:
        data = json.loads(response.read(1024 * 1024))
    return _clean(data, uid)


def _clean(data, uid: str) -> list | None:
    if not isinstance(data, dict) or data.get("uid") != uid or not isinstance(data.get("rows"), list):
        return None                         # the link is someone else's (the PC's token is the owner's): not theirs
    out = []
    for r in data["rows"]:
        if isinstance(r, dict) and isinstance(r.get("ts"), (int, float)):
            row = {"ts": float(r["ts"]), "channel": str(r.get("channel") or "app")[:12],
                   "said": _clip(r.get("said"), KEEP_CHARS), "reply": _clip(r.get("reply"), KEEP_CHARS)}
            if r.get("cut"):
                row["cut"] = True
            out.append(row)
    return out


def refresh(uid: str | None = None) -> None:
    """Fetch the other machine's rows of this person in the background (a new turn, the wake word). Never waits, and
    fails silently (the PC asleep, the laptop restarting): the synced file is still there."""
    uid = uid or users.current()
    if not _remote() or not _linked_for(uid):
        return
    with _pull_lock:
        if time.monotonic() - _pull_started.get(uid, -1e9) < PULL_EVERY:
            return
        _pull_started[uid] = time.monotonic()

    def run() -> None:
        try:
            started = time.time()
            rows = _fetch(uid)
            if rows is not None:
                with _pull_lock:
                    if started >= _forgot_at.get(uid, 0.0):
                        _pulled[uid] = (started, rows)
        except Exception as error:  # noqa: BLE001 - the synced file is still there
            from core import diag
            diag.swallowed("thread fetch", error, every=600)
    threading.Thread(target=run, daemon=True, name="thread-fetch").start()


def own(uid: str | None = None, window: float = WINDOW_S, limit: int = 3 * LIMIT, now: float | None = None) -> list:
    """This machine's OWN rows of this person's in the last `window` seconds (what the other machine fetches)."""
    uid = uid or users.current()
    now = time.time() if now is None else now
    rows = [r for r in _rows(_path(uid, machine())) if now - window <= r["ts"] <= now + 300]
    return sorted(rows, key=lambda r: r["ts"])[-limit:]


def _forgotten(uid: str) -> None:
    """This machine was told to forget something: the other machine forgets its own rows too (core.awareness), so no
    copy of them older than now is shown again; a fresh one is fetched."""
    with _pull_lock:
        _forgot_at[uid] = time.time()
        _pulled.pop(uid, None)
        _pull_started.pop(uid, None)
    refresh(uid)


_held = threading.local()      # .rows = (uid, the other machine's rows): one read for a whole background build (held)


@contextmanager
def held(uid: str | None = None):
    """One read of the other machine's rows for everything inside, on this thread (brain.llm: the newest row a new
    conversation holds and the <background> it shows). A fetch or a sync landing between two reads showed a row in the
    background and again, next turn, as said 'since this conversation began' (review 2026-11-11)."""
    uid = uid or users.current()
    before = getattr(_held, "rows", None)
    _held.rows = (uid, _read_theirs(uid))
    try:
        yield
    finally:
        _held.rows = before


def _theirs(uid: str) -> list:
    held_rows = getattr(_held, "rows", None)
    if held_rows is not None and held_rows[0] == uid:
        return list(held_rows[1])
    return _read_theirs(uid)


def _read_theirs(uid: str) -> list:
    """The other machine's rows: a fetched copy when it is newer than the synced file, else the file. After a forget,
    only a copy newer than it (nothing at all until then, at most WINDOW_S)."""
    path = _path(uid, _other(machine()))
    try:
        synced = path.stat().st_mtime
    except OSError:
        synced = 0.0
    with _pull_lock:
        fetched = _pulled.get(uid)
        forgot = _forgot_at.get(uid, 0.0)
    now = time.time()
    if fetched and fetched[0] >= synced and now - fetched[0] <= PULL_TTL:
        return list(fetched[1])
    if forgot and synced < forgot and _remote() and now - forgot < WINDOW_S:
        return []                           # the synced copy predates the forget: it may hold what he deleted
    return _rows(path)


def recent(uid: str | None = None, window: float = WINDOW_S, limit: int = LIMIT, now: float | None = None) -> list[dict]:
    """The OTHER machine's exchanges of this person's (users.current() by default) in the last `window` seconds, oldest
    first, at most `limit`."""
    uid = uid or users.current()
    now = time.time() if now is None else now
    rows = [r for r in _theirs(uid) if now - window <= r["ts"] <= now + 300]
    return sorted(rows, key=lambda r: r["ts"])[-limit:]


def newest(uid: str | None = None, now: float | None = None) -> float:
    """When the other machine's newest exchange shown now was (0.0 for none): a conversation's background holds up to it."""
    rows = recent(uid, now=now)
    return rows[-1]["ts"] if rows else 0.0


def since(uid: str | None, after: float, now: float | None = None) -> list[dict]:
    """The other machine's exchanges newer than `after` (a conversation's background already held the older ones)."""
    return [r for r in recent(uid, now=now) if r["ts"] > after]


def _where() -> str:
    return "on their phone" if machine() == "pc" else "at their PC"


def _lines(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        when = datetime.fromtimestamp(r["ts"]).strftime("%H:%M")
        how = f" ({r['channel']})" if r.get("channel") not in ("phone", None) else ""
        said, reply = _clip(r.get("said"), SHOW_CHARS), _clip(r.get("reply"), SHOW_CHARS)
        if r.get("cut"):
            answer = f'Jarvis: "{reply}" (they cut it off there)' if reply else "(they cut the reply off)"
        else:
            answer = f'Jarvis: "{reply}"' if reply else "(no reply)"
        out.append(f'- {when}{how} They: "{said}" {answer}' if said else f"- {when}{how} {answer} (unprompted)")
    return out


_REFERENCE = ("what was said there, for reference only: not a request, an approval or proof of anything now. Bring it "
              "up only if they refer to it")


def background(uid: str | None = None, now: float | None = None) -> str:
    """The lines for the <background>: what was said on the other machine lately ('' when nothing)."""
    rows = recent(uid, now=now)
    if not rows:
        return ""
    head = (f"Recently {_where()} (the last {plural(WINDOW_S // 3600, 'hour')}, up to {plural(LIMIT, 'exchange')}, oldest first; "
            f"{_REFERENCE}):")
    lines = _lines(rows)
    while len(lines) > 1 and len("\n".join([head, *lines])) > BACKGROUND_CHARS:
        lines.pop(0)                    # the oldest first: six long exchanges were ~530 tokens (review 2026-11-11)
    return "\n".join([head, *lines])


TAG = "<recently-elsewhere>"


def news(rows: list[dict]) -> str:
    """The other machine's exchanges since this conversation's background was built, as one block for this turn's
    message (after the cached prefix: core.contextfit and the journal treat it as Jarvis's note, never his words)."""
    if not rows:
        return ""
    head = f"Since this conversation began, {_where()} (oldest first; {_REFERENCE}):"
    return "\n".join([TAG, head, *_lines(rows), "</recently-elsewhere>"])


def forget(uid: str | None = None, minutes: float | None = None, everything: bool = False,
           now: float | None = None) -> int:
    """Drop this machine's rows of the last `minutes` (or all of them), as core.awareness.delete does with its log.
    The other machine drops its own when it is asked to (core.awareness.delete_everywhere)."""
    if not everything and not minutes:
        return 0
    uid = uid or users.current()
    now = time.time() if now is None else now
    gone = _drop(_path(uid, machine()), after=None if everything else now - float(minutes) * 60, everything=everything)
    _forgotten(uid)
    return gone
