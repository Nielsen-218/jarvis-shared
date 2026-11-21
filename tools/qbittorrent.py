"""qBittorrent on the homelab: see, add and manage torrents through its Web API.

The WebUI login goes in the settings file (QBIT_USER / QBIT_PASS); Jarvis never
asks for it out loud. Downloads land in the media drive's downloads folder
(/downloads inside the container). Removing a torrent together with its files
needs the user's spoken yes, like every other delete.
"""
import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from tools.homelab import HOST
from tools.registry import tool

QBIT = os.getenv("QBIT_URL", f"http://{HOST}:8082").rstrip("/")
_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))


def _login() -> None:
    user, pw = os.getenv("QBIT_USER", ""), os.getenv("QBIT_PASS", "")
    if not user or not pw:
        raise RuntimeError("qBittorrent login isn't set up: add QBIT_USER and QBIT_PASS to the settings file (.env).")
    body = urllib.parse.urlencode({"username": user, "password": pw}).encode()
    req = urllib.request.Request(f"{QBIT}/api/v2/auth/login", data=body, headers={"Referer": QBIT})
    with _opener.open(req, timeout=10) as r:
        # Older versions answer "Ok.", qBittorrent 5 answers 204 with no body (cookie QBT_SID_<port>); failures say "Fails."
        if r.read().decode().strip() not in ("Ok.", "") or not any("SID" in c.name for c in _jar):
            raise RuntimeError("qBittorrent refused the login in the settings file (wrong user/password, or too many tries).")


def _api(path: str, data: dict | None = None, retry: bool = True) -> str:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(f"{QBIT}/api/v2/{path}", data=body, headers={"Referer": QBIT})
    try:
        with _opener.open(req, timeout=15) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 403 and retry:
            _login()
            return _api(path, data, retry=False)
        raise


def _find(name: str) -> list[dict]:
    torrents = json.loads(_api("torrents/info"))
    key = name.strip().lower()
    if key in ("all", "everything"):
        return torrents
    return [t for t in torrents if key in t["name"].lower() or t["hash"].startswith(key)]


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


@tool("qBittorrent: list torrents with progress, speed, state and time left.")
def qbit_list() -> str:
    torrents = json.loads(_api("torrents/info?sort=added_on&reverse=true"))
    if not torrents:
        return "No torrents in qBittorrent."
    lines = []
    for t in torrents[:30]:
        eta = "" if t["eta"] >= 8640000 or t["progress"] >= 1 else f", {t['eta'] // 60} min left"
        lines.append(f"{t['name']} - {t['progress'] * 100:.0f}% of {_size(t['size'])}, {t['state']}, "
                     f"down {_size(t['dlspeed'])}/s{eta}")
    return "\n".join(lines)


@tool(
    "qBittorrent: add a magnet link or .torrent URL the user gives you. Optional sub-folder inside "
    "downloads (e.g. 'anime/Show Name') and category.",
    {"link": {"type": "string"}, "folder": {"type": "string"}, "category": {"type": "string"}},
)
def qbit_add(link: str, folder: str, category: str) -> str:
    link = link.strip()
    if not (link.startswith("magnet:") or urllib.parse.urlparse(link).scheme in ("http", "https")):
        return "That isn't a magnet link or a torrent URL."
    data = {"urls": link}
    sub = folder.strip().strip("/")
    if sub:
        if ".." in sub.split("/"):
            return "The folder has to stay inside downloads."
        data["savepath"] = f"/downloads/{sub}"
    if category.strip():
        data["category"] = category.strip()
    result = _api("torrents/add", data).strip()
    if result == "Fails.":
        return f"qBittorrent didn't take it: {result}"
    return f"Added to qBittorrent{' in downloads/' + sub if sub else ''}."


@tool(
    "qBittorrent: pause, resume, recheck or remove torrents matching a name ('all' for every one). "
    "remove keeps the files; remove_with_files deletes them too: call it with confirmed=false first, read the "
    "NEEDS_CONFIRMATION to the user, and only after they say yes call again with confirmed=true (the app checks their yes).",
    {"name": {"type": "string"},
     "action": {"type": "string", "enum": ["pause", "resume", "recheck", "remove", "remove_with_files"]},
     "confirmed": {"type": "boolean"}},
)
def qbit_control(name: str, action: str, confirmed: bool) -> str:
    matches = _find(name)
    if not matches:
        return f"No torrent matches '{name}'."
    names = ", ".join(t["name"] for t in matches[:5]) + (f" and {len(matches) - 5} more" if len(matches) > 5 else "")
    hashes = "|".join(t["hash"] for t in matches)
    if action == "remove_with_files":
        from core import approvals, users

        said_so = approvals.direct(users.current(), "delete", names)
        if not (said_so or (confirmed and approvals.consume(users.current(), f"qbit-delete:{hashes}"))):
            size = _size(sum(t["size"] for t in matches))
            return approvals.request(users.current(), f"qbit-delete:{hashes}",
                                     f"remove {names} and delete its files ({size})")
    if action in ("pause", "resume"):
        new = {"pause": "stop", "resume": "start"}[action]  # qBittorrent 5 renamed these
        try:
            _api(f"torrents/{new}", {"hashes": hashes})
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            _api(f"torrents/{action}", {"hashes": hashes})
    elif action == "recheck":
        _api("torrents/recheck", {"hashes": hashes})
    else:
        _api("torrents/delete", {"hashes": hashes, "deleteFiles": str(action == "remove_with_files").lower()})
    return f"{action.replace('_', ' ').capitalize()}: {names}."


@tool(
    "qBittorrent: set global download/upload speed limits in KB/s (0 = unlimited). Handy before gaming.",
    {"download_kbs": {"type": "integer"}, "upload_kbs": {"type": "integer"}},
)
def qbit_speed_limit(download_kbs: int, upload_kbs: int) -> str:
    _api("transfer/setDownloadLimit", {"limit": max(0, download_kbs) * 1024})
    _api("transfer/setUploadLimit", {"limit": max(0, upload_kbs) * 1024})
    fmt = lambda v: f"{v} KB/s" if v > 0 else "unlimited"  # noqa: E731
    return f"Speed limits: down {fmt(download_kbs)}, up {fmt(upload_kbs)}."
