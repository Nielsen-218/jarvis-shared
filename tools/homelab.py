"""Homelab: the NLfam Raspberry Pi (Docker host + media drive) and its apps.

Jarvis reaches the Pi over SSH with its own key (~/.ssh/jarvis_homelab) and can
only do the fixed actions below - never arbitrary commands. Every file path is
kept inside the media drive; deleting anything needs the user's spoken yes.
Downloads: links handed to MeTube, public-domain films from the Internet
Archive, and qBittorrent (tools/qbittorrent.py).
"""
import json
import os
import posixpath
import re
import shlex
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from tools.registry import tool

HOST = os.getenv("HOMELAB_HOST", "192.168.1.50")
USER = os.getenv("HOMELAB_USER", "jarvis")
KEY = os.getenv("HOMELAB_KEY", str(Path.home() / ".ssh" / "jarvis_homelab"))
MEDIA_ROOT = os.getenv("HOMELAB_MEDIA_ROOT", "/srv/dev-disk-by-uuid-d27f110d-5669-4ac0-ac64-d24b36d981f3")
JELLYFIN = os.getenv("JELLYFIN_URL", f"http://{HOST}:8096")
METUBE = os.getenv("METUBE_URL", f"http://{HOST}:8081")
PROTECTED_CONTAINERS = {"portainer"}  # restarting Portainer from itself is a bad idea


def ssh(command: str, timeout: int = 30) -> str:
    """Run one command on the Pi (key login, no password prompts)."""
    result = subprocess.run(
        ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
         "-o", "StrictHostKeyChecking=accept-new", f"{USER}@{HOST}", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0 and not result.stdout:
        raise RuntimeError(result.stderr.strip()[-300:] or f"exit code {result.returncode}")
    return result.stdout.strip()


def media_path(rel: str) -> str:
    """Absolute path on the drive for a folder like 'movies/Classics'; refuses anything outside it."""
    clean = posixpath.normpath("/" + rel.strip().lstrip("/")).lstrip("/")
    if clean.startswith("..") or "\x00" in clean:
        raise ValueError("path outside the media drive")
    return posixpath.join(MEDIA_ROOT, clean) if clean not in ("", ".") else MEDIA_ROOT


# -- status ---------------------------------------------------------------------------------
@tool("Homelab status: which Docker apps are up or down, media drive space, Pi temperature, memory and uptime.")
def homelab_status() -> str:
    out = ssh(
        "echo '## apps'; docker ps -a --format '{{.Names}}: {{.Status}}'; "
        f"echo '## drive'; df -h {shlex.quote(MEDIA_ROOT)} | tail -1; "
        "echo '## pi'; echo temp $(( $(cat /sys/class/thermal/thermal_zone0/temp) / 1000 ))C; "
        "free -h | awk '/Mem/{print \"memory \"$3\" used of \"$2}'; uptime -p")
    return out or "The Pi answered but reported nothing — its status commands may have failed. Try homelab_health."


@tool(
    "Restart a homelab app (Docker container) that's stuck or misbehaving. Ask the user first.",
    {"app": {"type": "string", "description": "Container name, e.g. 'jellyfin', 'metube'"}},
    strict=True,
)
def restart_app(app: str) -> str:
    name = app.strip().lower()
    names = ssh("docker ps -a --format '{{.Names}}'").split()
    if name not in names:
        return f"No app called {app}. Running apps: {', '.join(names)}"
    if name in PROTECTED_CONTAINERS:
        return f"{name} manages the others; restart it from the Pi directly if needed."
    ssh(f"docker restart {shlex.quote(name)}", timeout=90)
    return f"Restarted {name}. " + ssh(f"docker ps --filter name=^{shlex.quote(name)}$ --format '{{{{.Status}}}}'")


@tool(
    "Recent log lines of a homelab app, to diagnose a problem.",
    {"app": {"type": "string"}, "lines": {"type": "integer"}},
)
def app_logs(app: str, lines: int) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]", "", app)
    if not name:
        return "Which app? Give me a container name, e.g. 'jellyfin' or 'immich_server' (homelab_status lists them)."
    out = ssh(f"docker logs --tail {max(5, min(lines, 100))} {shlex.quote(name)} 2>&1")[-4000:]
    return out or f"{name} hasn't written any log lines recently — it's quiet, which usually means it's idle, not broken."


# -- files on the media drive -----------------------------------------------------------------
@tool(
    "List what's in a folder on the media drive (top level: movies, anime, downloads, photos, files).",
    {"folder": {"type": "string", "description": "Relative to the drive, e.g. 'movies' or 'anime/Show Name'; empty for the top"}},
)
def list_folder(folder: str) -> str:
    path = media_path(folder)
    # No size total: adding up a whole media folder takes minutes on a Pi.
    out = ssh(f"cd {shlex.quote(path)} && ls -1p | head -200 && echo \"--- $(ls -1 | wc -l) items\"")
    return out or "(empty)"


@tool(
    "Create a folder on the media drive (e.g. a new show or a movie collection).",
    {"folder": {"type": "string", "description": "Relative path, e.g. 'movies/Classics'"}},
)
def make_folder(folder: str) -> str:
    path = media_path(folder)
    ssh(f"mkdir -p {shlex.quote(path)}")
    return f"Created {folder}."


@tool(
    "Rename or move a file or folder on the media drive. Both paths are relative to the drive.",
    {"source": {"type": "string"}, "destination": {"type": "string"}},
)
def move_item(source: str, destination: str) -> str:
    src, dst = media_path(source), media_path(destination)
    ssh(f"test -e {shlex.quote(src)} && mkdir -p {shlex.quote(posixpath.dirname(dst))} && "
        f"mv -n {shlex.quote(src)} {shlex.quote(dst)}")
    return f"Moved {source} to {destination}."


@tool(
    "Delete a file or folder on the media drive. If the user directly told you to delete it, it just happens. "
    "Otherwise it returns NEEDS_CONFIRMATION: read it to the user, and only if they answer yes call again with "
    "confirmed=true (the app itself checks their reply).",
    {"item": {"type": "string"}, "confirmed": {"type": "boolean"}},
    strict=True,
)
def delete_item(item: str, confirmed: bool) -> str:
    path = media_path(item)
    if path.rstrip("/") == MEDIA_ROOT.rstrip("/") or posixpath.dirname(path) == MEDIA_ROOT and \
            posixpath.basename(path) in ("movies", "anime", "downloads", "photos", "files"):
        return "That's one of the main folders; I won't delete it."
    from core import approvals, users

    uid = users.current()
    said_so = approvals.direct(uid, "delete", posixpath.basename(path.rstrip("/")))  # "delete the old trailer"
    if not (said_so or (confirmed and approvals.consume(uid, f"delete:{path}"))):
        size = ssh(f"du -sh {shlex.quote(path)} 2>/dev/null | cut -f1") or "?"
        return approvals.request(users.current(), f"delete:{path}", f"delete {item} ({size})")
    ssh(f"rm -rf -- {shlex.quote(path)}")
    return f"Deleted {item}."


@tool(
    "Find small junk files (e.g. trailers or samples under a size) in a media folder, to suggest a clean-up.",
    {"folder": {"type": "string"}, "max_mb": {"type": "integer", "description": "Files smaller than this many MB"}},
)
def find_small_videos(folder: str, max_mb: int) -> str:
    path = media_path(folder)
    out = ssh(f"cd {shlex.quote(path)} && find . -type f \\( -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.webm' \\) "
              f"-size -{int(max_mb)}M -printf '%s\\t%p\\n' | sort -n | head -100")
    if not out:
        return "No small video files found."
    return "\n".join(f"{int(s) // 1_000_000} MB  {p[2:]}" for s, p in (l.split("\t", 1) for l in out.splitlines()))


# -- Jellyfin ---------------------------------------------------------------------------------
def _jellyfin_setup_message() -> str:
    return ("Jellyfin isn't configured yet. Create an API key in Jellyfin's "
            "Dashboard > API Keys, then add it to the private settings file.")


def _jf(path: str, method: str = "GET", params: dict | None = None):
    key = (os.getenv("JELLYFIN_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("No Jellyfin API key yet: create one in Jellyfin (Dashboard > API Keys) and add "
                           "JELLYFIN_API_KEY=... to the settings file.")
    url = f"{JELLYFIN}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    # Jellyfin 12 only accepts the "MediaBrowser" Authorization header (not X-Emby-Token or ?api_key=).
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f'MediaBrowser Client="Jarvis", Device="MY-PC", DeviceId="jarvis", Version="1.0", Token="{key}"',
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read()
        return json.loads(body) if body else None


@tool("Jellyfin overview: libraries with item counts, recently added titles, and who is watching what right now.")
def jellyfin_overview() -> str:
    if not (os.getenv("JELLYFIN_API_KEY") or "").strip():
        return _jellyfin_setup_message()
    lines = []
    for lib in _jf("/Library/VirtualFolders") or []:
        lines.append(f"Library {lib['Name']} ({lib.get('CollectionType', '?')}): {', '.join(lib.get('Locations', []))}")
    counts = _jf("/Items/Counts")
    lines.append(f"Counts: {counts.get('MovieCount', 0)} movies, {counts.get('SeriesCount', 0)} series, "
                 f"{counts.get('EpisodeCount', 0)} episodes.")
    recent = _jf("/Items", params={"SortBy": "DateCreated", "SortOrder": "Descending", "Limit": 10,
                                   "Recursive": "true", "IncludeItemTypes": "Movie,Series,Episode"})
    if recent and recent.get("Items"):
        lines.append("Recently added: " + "; ".join(
            f"{i.get('SeriesName') + ' - ' if i.get('SeriesName') else ''}{i['Name']}" for i in recent["Items"]))
    sessions = [s for s in _jf("/Sessions") or [] if s.get("NowPlayingItem")]
    lines.append("Watching now: " + ("; ".join(f"{s.get('UserName')} on {s.get('DeviceName')}: "
                                                f"{s['NowPlayingItem']['Name']}" for s in sessions) or "nobody"))
    return "\n".join(lines)


@tool(
    "Search the Jellyfin library for a movie or show.",
    {"query": {"type": "string"}},
)
def jellyfin_search(query: str) -> str:
    if not (os.getenv("JELLYFIN_API_KEY") or "").strip():
        return _jellyfin_setup_message()
    res = _jf("/Items", params={"searchTerm": query, "Recursive": "true", "Limit": 15,
                                "IncludeItemTypes": "Movie,Series,Episode",
                                "Fields": "ProductionYear,Overview,Path"})
    items = res.get("Items", []) if res else []
    if not items:
        return f"Nothing in Jellyfin matches '{query}'."
    return "\n".join(f"{i['Type']}: {i['Name']} ({i.get('ProductionYear', '?')})" for i in items)


@tool("Make Jellyfin rescan its libraries (after adding or moving files).")
def jellyfin_rescan() -> str:
    if not (os.getenv("JELLYFIN_API_KEY") or "").strip():
        return _jellyfin_setup_message()
    _jf("/Library/Refresh", method="POST")
    return "Jellyfin is rescanning the libraries; new items appear in a minute or two."


# -- downloads ---------------------------------------------------------------------------------
@tool(
    "Send a video link to MeTube on the homelab to download (YouTube and similar sites), e.g. the "
    "user's own videos, free creator content, or anything they're allowed to save.",
    {"url": {"type": "string"}, "audio_only": {"type": "boolean"}},
)
def metube_download(url: str, audio_only: bool) -> str:
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return "Only http(s) links."
    body = json.dumps({"url": url, "quality": "best", "format": "mp3" if audio_only else "any"}).encode()
    req = urllib.request.Request(f"{METUBE}/add", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        res = json.loads(r.read() or b"{}")
    if res.get("status") != "ok":
        return f"MeTube said: {res}"
    return "Queued in MeTube; it downloads in the background."


@tool(
    "Search the Internet Archive for public-domain feature films (classics free to download and keep).",
    {"query": {"type": "string", "description": "Title, genre or actor, e.g. 'Charlie Chaplin' or 'noir'"}},
)
def public_domain_search(query: str) -> str:
    q = f"({query}) AND collection:(feature_films) AND mediatype:(movies)"
    url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(
        {"q": q, "fl[]": ["identifier", "title", "year"], "rows": 12, "output": "json", "sort[]": "downloads desc"},
        doseq=True)
    with urllib.request.urlopen(url, timeout=20) as r:
        docs = json.loads(r.read())["response"]["docs"]
    if not docs:
        return f"No public-domain films found for '{query}'."
    return "\n".join(f"[{d['identifier']}] {d.get('title')} ({d.get('year', '?')})" for d in docs)


@tool(
    "Download a public-domain film from the Internet Archive straight onto the homelab's movies folder "
    "(as 'Title (Year)/Title (Year).mp4' so Jellyfin recognises it), then rescan Jellyfin.",
    {"identifier": {"type": "string", "description": "The [id] from public_domain_search"}},
    slow=True,
)
def public_domain_download(identifier: str) -> str:
    ident = re.sub(r"[^A-Za-z0-9_.-]", "", identifier)
    with urllib.request.urlopen(f"https://archive.org/metadata/{ident}", timeout=20) as r:
        meta = json.loads(r.read())
    md = meta.get("metadata", {})
    if "feature_films" not in str(md.get("collection", "")):
        return "That item isn't in the Internet Archive's public-domain feature films collection."
    files = [f for f in meta.get("files", []) if f.get("name", "").lower().endswith(".mp4")]
    if not files:
        return "No MP4 version of that film on the Archive."
    best = max(files, key=lambda f: int(f.get("size", 0) or 0))
    title = re.sub(r'[\\/:*?"<>|]', "", str(md.get("title", ident)))[:80].strip()
    year = str(md.get("year") or md.get("date", ""))[:4]
    name = f"{title} ({year})" if year.isdigit() else title
    folder = media_path(f"movies/{name}")
    src = f"https://archive.org/download/{ident}/{urllib.parse.quote(best['name'])}"
    ssh(f"mkdir -p {shlex.quote(folder)} && cd {shlex.quote(folder)} && "
        f"nohup wget -q -O {shlex.quote(name + '.mp4')} {shlex.quote(src)} >/dev/null 2>&1 &")
    size = int(best.get("size", 0) or 0) // 1_000_000
    return (f"Downloading '{name}' ({size} MB) onto the homelab in the background. Ask me to rescan Jellyfin "
            "in a few minutes and it'll show up under Movies.")


def snapshot() -> dict:
    """Machine-readable homelab health for dashboards (one quick SSH call)."""
    out = ssh(
        "docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}'; echo '##'; "
        f"df -P {shlex.quote(MEDIA_ROOT)} | tail -1 | awk '{{print $3, $4, $5}}'; echo '##'; "
        "cat /sys/class/thermal/thermal_zone0/temp; free -b | awk '/Mem/{print $3, $2}'; "
        "cut -d. -f1 /proc/uptime; cat /proc/loadavg | cut -d' ' -f1; nproc", timeout=15)
    apps_part, disk_part, pi_part = out.split("##")
    apps = []
    for line in apps_part.strip().splitlines():
        name, state, status = (line.split("|") + ["", ""])[:3]
        apps.append({"name": name, "up": state == "running",
                     "healthy": "unhealthy" not in status, "status": status})
    used, avail, pct = disk_part.split()
    temp, mem, uptime, load, cores = pi_part.strip().splitlines()
    mem_used, mem_total = mem.split()
    return {
        "apps": sorted(apps, key=lambda a: (a["up"], a["name"])),
        "disk": {"used_pct": int(pct.rstrip("%")), "free_gb": round(int(avail) * 1024 / 1e9)},
        "pi": {"temp_c": round(int(temp) / 1000), "mem_pct": round(int(mem_used) / int(mem_total) * 100),
               "cpu_pct": min(100, round(float(load) / int(cores) * 100)), "uptime_days": int(uptime) // 86400},
    }


# -- watchdog ----------------------------------------------------------------------------------
_HEALTH_SCRIPT = r"""
R=%(root)s
mountpoint -q "$R" || echo "PROBLEM|drive|The media drive isn't mounted"
df -P "$R" | awk 'NR==2{gsub("%%","",$5); if ($5+0>=90) print "PROBLEM|disk|The media drive is "$5"%% full"}'
t=$(( $(cat /sys/class/thermal/thermal_zone0/temp) / 1000 )); [ "$t" -ge 75 ] && echo "PROBLEM|temp|The Pi is running hot (${t}C)"
docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}' | while IFS='|' read n s st; do
  [ "$s" != running ] && echo "PROBLEM|down:$n|$n is $s"
  case "$st" in *unhealthy*) echo "PROBLEM|unhealthy:$n|$n is unhealthy";; esac
done
for c in $(docker ps --format '{{.Names}}'); do
  docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}={{.Destination}}{{println}}{{end}}{{end}}' "$c" |
  grep "^$R" | cut -d= -f2- | while read -r d; do
    docker exec "$c" ls "$d" >/dev/null 2>&1; rc=$?
    { [ $rc -eq 1 ] || [ $rc -eq 2 ]; } && echo "PROBLEM|mount:$c|$c lost its connection to the media drive"
  done
done
# Only the drive mounted now counts: a dropped drive's old name (sda/sdb) keeps logging harmless errors.
dev=$(basename "$(findmnt -no SOURCE "$R")" | sed 's/[0-9]*$//')
journalctl -k --since "-15min" -q 2>/dev/null | grep -iE "USB disconnect|\b$dev[0-9]*\b" | grep -qiE "USB disconnect|I/O error|error -5" && echo "PROBLEM|io|The Pi logged drive errors in the last 15 minutes"
true
"""


def health_problems() -> dict[str, str]:
    """{key: message} for everything wrong right now (one SSH call, a few seconds)."""
    out = ssh(_HEALTH_SCRIPT % {"root": shlex.quote(MEDIA_ROOT)}, timeout=60)
    problems = {}
    for line in out.splitlines():
        if line.startswith("PROBLEM|"):
            _, key, msg = line.split("|", 2)
            problems[key] = msg
    return problems


@tool("Homelab health check: drive mounted and reachable from every app, apps up and healthy, disk space, "
      "Pi temperature, recent drive errors. Use for 'is anything wrong with the homelab?'.")
def homelab_health() -> str:
    problems = health_problems()
    if not problems:
        return "All good: drive mounted and reachable from every app, all apps up and healthy, no recent drive errors."
    tip = ""
    if any(k.startswith("mount:") for k in problems):
        tip = "\nApps that lost the drive come back after a restart (repair_drive_mounts, ask the user first)."
    return "Problems:\n- " + "\n- ".join(problems.values()) + tip


@tool(
    "Restart the homelab apps that lost their connection to the media drive (happens after the USB drive "
    "drops out and reconnects). Nothing is deleted. Ask the user first: it briefly interrupts those apps.",
    slow=True,
)
def repair_drive_mounts() -> str:
    broken = sorted(k.split(":", 1)[1] for k in health_problems() if k.startswith("mount:"))
    broken = [c for c in broken if c not in PROTECTED_CONTAINERS]
    if not broken:
        return "Every app can reach the media drive; nothing to repair."
    ssh("docker restart " + " ".join(shlex.quote(c) for c in broken), timeout=180)
    still = sorted(k.split(":", 1)[1] for k in health_problems() if k.startswith("mount:"))
    return f"Restarted {', '.join(broken)}. " + ("All reconnected." if not still else f"Still broken: {', '.join(still)}.")


# -- wake the desk PC from the always-on laptop (Wake-on-LAN) -------------------------------------
@tool(
    "Turn on the configured desk PC with free Wake-on-LAN from the always-on LAN laptop. "
    "Works while the PC is offline; requires Ethernet, BIOS wake support and private laptop configuration. "
    "A sent packet does not confirm startup; check PC status after boot.",
    {"mac": {"type": "string", "description": "Legacy optional value; must match the privately configured PC"}},
    required=[], direct=True,
)
def wake_pc(mac: str = "") -> str:
    from core import pcbridge, pcwake
    try:
        pcbridge.require_owner()
        if mac:
            try:
                matches = pcwake.mac_bytes(mac) == pcwake.mac_bytes(os.getenv("JARVIS_PC_WAKE_MAC", ""))
            except pcwake.WakeError:
                matches = False
            if not matches:
                return "Wake only targets the privately configured owner PC; arbitrary MAC overrides are not supported."
        return pcwake.wake()
    except pcbridge.PCError as error:
        return str(error)
