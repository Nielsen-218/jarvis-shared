"""A security checkup for this PC — spot the fingerprints of unwanted or persistent software.

This is the skill version of the hunt that found the disguised "Edge updater" task running a renamed
Windows binary (`...\\AppData\\Roaming\\riighhh`) out of the owner's Roaming folder. It looks at the
places software arranges to start itself — scheduled tasks, the Run keys, the Startup folders, WMI
event consumers — plus loose program files hiding in Roaming, and flags the tells of the common
persistence tricks: a task named like a real updater but launched from a personal folder, a Windows
program renamed to a random string, a hidden/encoded PowerShell command, that kind of thing.

READ-ONLY. It reads task definitions, registry values, file names and signatures. It never deletes,
quarantines, moves or runs anything — if it finds something, it tells the owner so they can decide.
"""
from __future__ import annotations

import json
import os
import subprocess

from core.words import plural
from tools.registry import tool

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Program names a task or autostart entry borrows to look like it belongs — a real updater lives in
# Program Files or System32, so one of these names launching from a personal folder is the giveaway.
_UPDATER_WORDS = ("update", "updater", "edge", "chrome", "google", "mozilla", "firefox", "adobe",
                  "acrobat", "java", "oracle", "office", "onedrive", "teams", "nvidia", "geforce",
                  "intel", "realtek", "dropbox", "spotify", "driver", "sync", "defender", "windows")

# Folders anyone can write to without admin. Legit auto-start programs almost never launch from the
# first group; the second (Roaming) has some real apps, so there it's only a hint, corroborated below.
_TEMP_DIRS = ("\\appdata\\local\\temp", "\\windows\\temp", "\\temp\\", "\\downloads\\",
              "\\users\\public", "\\public\\", "\\appdata\\locallow\\")
_ROAMING = "\\appdata\\roaming"

# Extensions a *launched* file normally has. Anything executed with none of these — or with a
# data-looking one — is a program wearing a disguise (the renamed-binary trick).
_EXE_EXTS = (".exe", ".com", ".bat", ".cmd", ".scr", ".pif", ".vbs", ".vbe", ".js", ".jse",
             ".wsf", ".wsh", ".ps1", ".msi", ".lnk", ".dll", ".cpl", ".msc", ".hta")
_DISGUISE_EXTS = ("", ".dat", ".tmp", ".bin", ".log", ".old", ".txt", ".dll.", ".jpg", ".png", ".ini")

# Command fragments that mean "fetch-and-run" or "run a payload you can't read". Kept to high-signal
# ones only: a hidden window or -NoProfile on its own is common in legit background launchers (Syncthing,
# updaters), so those are deliberately NOT here — they cause false alarms without catching real threats.
_BAD_ARGS = ("-enc ", "-e jab", "-encodedcommand", "frombase64string", "iex(", "iex ", "invoke-expression",
             "downloadstring", "downloadfile", "webclient", "bitsadmin", "certutil -urlcache",
             "certutil.exe -urlcache", "mshta http", "mshta vbscript", "mshta javascript",
             "regsvr32 /s /u /i:http", "regsvr32 /u /s /i:http", "-decode http")


def _norm(path: str) -> str:
    """A path as lower-case, env-expanded, backslash form for substring matching."""
    p = str(path or "").strip().strip('"').strip("'")
    try:
        p = os.path.expandvars(p)
    except Exception:
        pass
    return p.replace("/", "\\").lower()


def _exe_from_command(cmd: str) -> str:
    """The program a command line actually launches: the quoted head, else the first whitespace token."""
    s = str(cmd or "").strip()
    if s.startswith('"'):
        end = s.find('"', 1)
        return s[1:end] if end > 0 else s[1:]
    return s.split(" ", 1)[0] if s else ""


def _looks_like_updater(name: str) -> bool:
    n = str(name or "").lower()
    return any(w in n for w in _UPDATER_WORDS)


def _suspicious_exe_name(path: str) -> bool:
    """True when a *launched* file's name is a disguise: no extension, or a data-looking one."""
    base = os.path.basename(_norm(path))
    if not base:
        return False
    root, ext = os.path.splitext(base)
    if not root:
        return False
    return ext not in _EXE_EXTS and ext in _DISGUISE_EXTS


def _suspicious_args(args: str) -> bool:
    a = str(args or "").lower()
    return any(frag in a for frag in _BAD_ARGS)


def _launch_reasons(name: str, author: str, exec_path: str, args: str = "") -> list[str]:
    """Why a scheduled task / Run entry that launches `exec_path` looks wrong (empty = looks fine)."""
    p = _norm(exec_path)
    reasons: list[str] = []
    if any(frag in p for frag in _TEMP_DIRS):
        reasons.append("it launches a program from a temporary or Downloads folder")
    if _ROAMING in p and os.path.basename(os.path.dirname(p)) == "roaming":
        reasons.append("it launches a loose program straight out of your Roaming folder")
    # "Renamed binary" is only claimed when the file really exists and is a Windows program — the way
    # riighhh was confirmed. That avoids flagging a mis-parsed path (an unquoted "C:\\Riot Games\\...").
    if _suspicious_exe_name(p) and _is_pe(p):
        reasons.append("the program has no normal name — a file renamed to hide what it is")
    if _suspicious_args(args):
        reasons.append("it runs a hidden or encoded command")
    # A real-updater NAME is only damning when the launch is ALREADY suspicious — i.e. a decoy. On its
    # own it isn't: plenty of legit apps (Spotify in Roaming\Spotify, OneDrive updater) match these words
    # yet run from their own named folder with a real .exe. So impersonation aggravates; it never triggers.
    trusted = any(w in str(author or "").lower() for w in ("microsoft", "windows"))
    if reasons and _looks_like_updater(name) and not trusted:
        reasons.append("and it's named like a system updater")
    return reasons


def _is_pe(path: str) -> bool:
    """Read-only peek at the first two bytes: 'MZ' marks a Windows program, whatever the name says."""
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"MZ"
    except Exception:
        return False


def _ps_json(script: str, timeout: int = 45):
    """Run a PowerShell snippet that emits JSON; return a list (always), or None if PowerShell failed."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW,
            encoding="utf-8", errors="replace",
        )
    except Exception:
        return None
    text = (out.stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, list) else [data]


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


# --------------------------------------------------------------------------------------- the checks
def _scheduled_task_findings() -> list[str]:
    rows = _ps_json(
        "$ErrorActionPreference='SilentlyContinue';"
        "@(Get-ScheduledTask | ForEach-Object { $t=$_;"
        " foreach($a in @($t.Actions)){ if($a.Execute){ [pscustomobject]@{"
        " n=[string]$t.TaskName; au=[string]$t.Author; st=[string]$t.State;"
        " ex=[string]$a.Execute; ar=[string]$a.Arguments } } }"
        "}) | ConvertTo-Json -Depth 3 -Compress"
    )
    out = []
    for r in rows or []:
        ex = r.get("ex", "?")
        reasons = _launch_reasons(r.get("n", ""), r.get("au", ""), ex, r.get("ar", ""))
        if not reasons:
            continue
        note = " — this task is currently disabled" if (r.get("st") or "").lower() == "disabled" else ""
        # Expand env vars before the existence check so %APPDATA%\\x resolves like Task Scheduler sees it.
        try:
            gone = bool(ex) and not os.path.exists(os.path.expandvars(ex.strip('"')))
        except Exception:
            gone = False
        if gone:
            note += " and the program it points to is already gone (a leftover worth removing)" if note \
                else " — the program it points to is already gone (a leftover worth removing)"
        out.append(f"Scheduled task “{r.get('n', '?')}” — {reasons[0]} (runs {ex}){note}.")
    return out


def _run_key_findings() -> list[str]:
    try:
        import winreg
    except Exception:
        return []
    locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "your account's"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "your account's"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "this PC's"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "this PC's"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run", "this PC's"),
    ]
    out = []
    for hive, subkey, where in locations:
        try:
            key = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    vname, vdata, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                exe = _exe_from_command(str(vdata))
                reasons = _launch_reasons(vname, "", exe, str(vdata))
                if reasons:
                    out.append(f"Startup entry “{vname}” in {where} Run list — {reasons[0]} "
                               f"(runs {exe}).")
        finally:
            winreg.CloseKey(key)
    return out


def _script_is_fishy(path: str) -> bool:
    """A startup script that fetches-and-runs or hides a window/encoded payload (read-only, first 8 KB)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return _suspicious_args(fh.read(8192))
    except Exception:
        return False


def _startup_folder_findings() -> list[str]:
    folders = [os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"),
               os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\Startup")]
    # Scripts in Startup are normal for power users (the owner starts the local model this way), so a bare
    # .cmd isn't news. Flag only the genuinely suspicious: a renamed Windows program, or a fetch-and-run script.
    _SCRIPT_EXTS = (".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".ps1", ".hta", ".wsf")
    out = []
    for folder in folders:
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for name in entries:
            full = os.path.join(folder, name)
            ext = os.path.splitext(name)[1].lower()
            if _suspicious_exe_name(name) and _is_pe(full):
                out.append(f"Hidden program “{name}” runs automatically at sign-in from your Startup "
                           f"folder — a program renamed to hide what it is.")
            elif ext in _SCRIPT_EXTS and _script_is_fishy(full):
                out.append(f"Startup script “{name}” runs a hidden or encoded command when you sign in "
                           f"— worth a look.")
    return out


def _roaming_loose_binary_findings() -> list[str]:
    roaming = os.path.expandvars(r"%APPDATA%")
    out = []
    try:
        entries = os.listdir(roaming)
    except OSError:
        return []
    for name in entries:
        full = os.path.join(roaming, name)
        if not os.path.isfile(full):
            continue
        # The tell: a real program sitting loose in Roaming under a disguised name (riighhh had none).
        if _suspicious_exe_name(name) and _is_pe(full):
            sig = _ps_json("$ErrorActionPreference='SilentlyContinue';"
                           f"$s=Get-AuthenticodeSignature -LiteralPath {_ps_quote(full)};"
                           " [pscustomobject]@{ status=[string]$s.Status;"
                           " signer=[string]$s.SignerCertificate.Subject } | ConvertTo-Json -Compress", timeout=20)
            tail = ""
            if sig and isinstance(sig[0], dict) and sig[0].get("status", "").lower() == "valid":
                signer = sig[0].get("signer", "")
                who = "Microsoft" if "microsoft" in signer.lower() else "a vendor"
                tail = f" It's actually a {who}-signed Windows program renamed to hide — a classic trick."
            out.append(f"Hidden program “{name}” is sitting loose in your Roaming folder "
                       f"({full}).{tail}")
    return out


def _wmi_findings() -> list[str]:
    rows = _ps_json(
        "$ErrorActionPreference='SilentlyContinue';"
        "@(Get-WmiObject -Namespace root\\subscription -Class CommandLineEventConsumer;"
        " Get-WmiObject -Namespace root\\subscription -Class ActiveScriptEventConsumer) |"
        " ForEach-Object { [pscustomobject]@{ n=[string]$_.Name;"
        " cmd=([string]$_.CommandLineTemplate + [string]$_.ScriptText) } } | ConvertTo-Json -Depth 3 -Compress",
        timeout=25,
    )
    out = []
    for r in rows or []:
        cmd = (r.get("cmd") or "").strip()
        out.append(f"WMI startup entry “{r.get('n', '?')}” — WMI event consumers are a stealthy place "
                   f"to hide a startup command; worth checking{(' (runs: ' + cmd[:120] + ')') if cmd else ''}.")
    return out


@tool(
    "Run a quick security checkup on this PC and report anything that looks like unwanted or hidden "
    "software. It inspects how programs start themselves — scheduled tasks, the startup/Run entries, "
    "WMI event consumers — and loose programs hiding in your Roaming folder, and flags the classic "
    "persistence tricks: a task disguised as a system updater but launched from a personal folder, a "
    "Windows program renamed to a random name, a hidden or encoded command. Read-only: it only looks, "
    "it never deletes or runs anything. Use it for a routine checkup, when the PC feels off, or right "
    "after clicking a suspicious link or download.",
    slow=True,
)
def security_checkup() -> str:
    if os.name != "nt":
        return ("I can only run the security checkup on the Windows PC — that's where I can read the "
                "scheduled tasks, startup entries and files I'd need to check.")

    checks = (
        ("scheduled tasks", _scheduled_task_findings),
        ("startup / Run entries", _run_key_findings),
        ("the startup folders", _startup_folder_findings),
        ("your Roaming folder", _roaming_loose_binary_findings),
        ("WMI startup hooks", _wmi_findings),
    )
    findings: list[str] = []
    skipped: list[str] = []
    for label, fn in checks:
        try:
            result = fn()
        except Exception:
            skipped.append(label)
            continue
        if result is None:
            skipped.append(label)
        else:
            findings.extend(result)

    note = ""
    if skipped:
        note = f" (I couldn't check {', '.join(skipped)} this time.)"

    if not findings:
        return ("Security checkup done — nothing looks out of place. No scheduled tasks or startup "
                "entries disguised as updaters, no programs hiding under odd names in your Roaming "
                "folder, and your autostart list is clean." + note)

    lead = (f"Security checkup — I found {plural(len(findings), 'thing')} worth a look:" if len(findings) > 1
            else "Security checkup — one thing is worth a look:")
    body = "\n".join(f"• {f}" for f in findings[:12])
    tail = ("\n\nIf you set these up yourself, they're fine. If any looks unfamiliar — especially one "
            "pretending to be an updater — don't open it; tell me and I'll help you shut it down safely.")
    return f"{lead}\n{body}{tail}{note}"
