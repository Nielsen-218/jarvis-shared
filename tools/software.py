"""Install and uninstall programs with winget (Windows' package manager): one command,
no clicking through windows, and it works even while the screen is locked or you're
away (phone app). Much faster and more reliable than control_computer for this.

Programs installed for everyone (in Program Files) need Windows' admin approval: Windows
shows its UAC box on a protected screen that no program can see or click, so someone at
the PC has to press Yes, unless Jarvis itself runs as administrator.
"""
import ctypes
import os
import re
import subprocess

from tools.registry import tool

TIMEOUT = 600
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_COMMON = ["--accept-source-agreements", "--disable-interactivity"]
# winget exit codes (HRESULTs as unsigned ints) worth explaining in plain words
_CODES = {
    0x8A150014: "no installed program matches that name",
    0x8A150011: "more than one program matches; be more specific",
    0x8A15002B: "the uninstaller needs a reboot to finish",
    0x8A150056: "Windows' admin approval (UAC) was declined or nobody was at the PC to approve it",
    0x8A150019: "the uninstaller was cancelled",
    0x800704C7: "it was cancelled (the admin approval box was closed or declined)",
    0x8A150030: "the program's own uninstaller failed (usually: it needs Windows' admin approval and nobody pressed Yes)",
}
_NEEDS_ADMIN = {0x8A150056, 0x800704C7, 0x8A150030}


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def launch_as_user(target: str) -> None:
    """Open a program, file or link with normal (non-admin) rights, even when Jarvis runs as administrator.
    Explorer always runs as the signed-in user, so whatever it opens does too.

    Guard: `explorer.exe` with a blank or bare-"explorer" argument opens the "This PC" window. A stray/periodic
    caller passing an empty target was popping that window every few minutes, so we refuse it (and log the caller
    so the real bug can be fixed) instead of launching Explorer with nothing to open."""
    t = str(target or "").strip().strip('"').strip("'")
    if not t or t.lower() in ("explorer", "explorer.exe", "this pc", "computer", "shell:mycomputerfolder"):
        import traceback
        here = "".join(traceback.format_stack()[-4:-1])
        print(f"[software] launch_as_user refused a blank target (would open 'This PC'). Caller:\n{here}")
        return
    from jarvis_pc.guardrails.stepfence import require_foreground
    # A step past its time limit launches nothing; a mission (background mode) never opens a window in front
    # (2026-11-10 review): it refuses with "needs you at the PC".
    require_foreground(f"open {os.path.basename(t) or t}")
    subprocess.Popen(["explorer.exe", t], creationflags=_NO_WINDOW)


def _winget(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["winget", *args, *_COMMON], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=_NO_WINDOW)


def _table(out: str) -> list[dict]:
    """Parse winget's text table (Name / Id / Version / ...) using the header's column positions."""
    lines = [l.rstrip() for l in out.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith("Name") and " Id " in line and i + 1 < len(lines) and set(lines[i + 1]) <= {"-", " "}:
            starts = [m.start() for m in re.finditer(r"\S+", line)]
            names = line.split()
            rows = []
            for row in lines[i + 2:]:
                if not row.strip() or row.lstrip().startswith(("-", "\\", "|", "/")):
                    continue
                cells = [row[s:e].strip() for s, e in zip(starts, starts[1:] + [None])]
                rows.append(dict(zip(names, cells)))
            return rows
    return []


def find_installed(query: str) -> list[dict]:
    r = _winget("list", "--name", query.strip())
    return _table(r.stdout) if r.returncode == 0 else []


_PREVIEW = re.compile(r"\b(nightly|beta|preview|insider|canary|dev|alpha|portable|bridge|plugin)\b", re.I)


def _pick(rows: list[dict], name: str) -> list[dict]:
    """Narrow matches to the one the person most likely means: exact name, else the single stable
    program whose name starts with what they said ("VLC" -> "VLC media player", not the Nightly)."""
    q = name.strip().lower()
    exact = [r for r in rows if r.get("Name", "").lower() == q]
    if exact:
        return exact
    starts = [r for r in rows if r.get("Name", "").lower().startswith(q) and not _PREVIEW.search(r.get("Name", ""))]
    return starts if len(starts) == 1 else rows


def _explain(code: int) -> str:
    return _CODES.get(code & 0xFFFFFFFF, f"winget error 0x{code & 0xFFFFFFFF:08X}")


def _registry_programs(name: str) -> list[dict]:
    """Installed programs from the Windows uninstall registry (all users + this user), matching a name."""
    import winreg

    q, found = name.strip().lower(), []
    path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    roots = [(winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
             (winreg.HKEY_CURRENT_USER, 0)]
    for hive, view in roots:
        try:
            base = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(base)[0]):
            try:
                key = winreg.EnumKey(base, i)
                with winreg.OpenKey(base, key) as k:
                    vals = {}
                    for v in ("DisplayName", "UninstallString", "QuietUninstallString", "WindowsInstaller"):
                        try:
                            vals[v] = winreg.QueryValueEx(k, v)[0]
                        except OSError:
                            pass
            except OSError:
                continue
            if q in str(vals.get("DisplayName", "")).lower() and vals.get("UninstallString"):
                vals["key"] = key
                if all(f.get("DisplayName") != vals["DisplayName"] for f in found):
                    found.append(vals)
    return found


def _silent_uninstall_command(entry: dict) -> str | None:
    """The program's own uninstaller, made silent where the installer type is known."""
    if entry.get("QuietUninstallString"):
        return entry["QuietUninstallString"]
    cmd = str(entry.get("UninstallString", ""))
    low = cmd.lower()
    if entry.get("WindowsInstaller") == 1 or "msiexec" in low:
        guid = re.search(r"\{[0-9A-F-]{36}\}", cmd + entry.get("key", ""), re.I)
        return f"msiexec /x {guid.group(0)} /qn /norestart" if guid else None
    if re.search(r"unins\d{3}\.exe", low):  # Inno Setup (unins000.exe)
        return cmd + " /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
    if "rundll32" in low:  # vendor-specific (e.g. NVIDIA): run it as registered
        return cmd
    if "update.exe" in low and "--uninstall" in low:  # Squirrel apps (Discord, Teams classic...)
        return cmd + " -s"
    return cmd + " /S" if cmd else None  # NSIS-style; worst case the uninstaller shows its window


def _appx(name: str, remove: bool) -> str:
    """Microsoft Store / MSIX apps (these don't appear in the uninstall registry)."""
    q = re.sub(r"[^A-Za-z0-9 ]", "", name).strip().replace(" ", "*")
    if not q:
        return ""
    verb = "| Remove-AppxPackage" if remove else "| Select-Object -ExpandProperty Name"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", f"Get-AppxPackage -Name '*{q}*' {verb}"],
                       capture_output=True, text=True, timeout=180, creationflags=_NO_WINDOW)
    if remove:
        return "ok" if r.returncode == 0 else r.stderr.strip()[-200:]
    return r.stdout.strip()


def _still_there(name: str) -> bool:
    return bool(_registry_programs(name)) or bool(find_installed(name)) or bool(_appx(name, remove=False))


@tool(
    "Uninstall a program from this PC. It tries every method by itself until the program is gone: Windows' "
    "package manager, the program's own silent uninstaller, and Store-app removal, then checks it's really gone. "
    "Use this (not control_computer) whenever the user says to uninstall or remove a program. If they told you "
    "to uninstall it, it just happens; otherwise it asks first.",
    {"name": {"type": "string", "description": "Program name as the user said it, e.g. 'Discord', 'SteelSeries GG'"}},
    slow=True,
)
def uninstall_program(name: str) -> str:
    from core import approvals, progress, users

    matches = find_installed(name)
    picked = _pick(matches, name) if matches else []
    if len(picked) > 1:
        listed = "; ".join(f"{m.get('Name')} ({m.get('Id')})" for m in matches[:8])
        return f"Several programs match '{name}': {listed}. Ask which one, then call again with its full name."
    reg = _registry_programs(picked[0]["Name"] if picked else name)
    store = "" if (picked or reg) else _appx(name, remove=False)
    if not (picked or reg or store):
        try:  # an app JARVIS installed from a GitHub link isn't registered with Windows; hand it to the reversible flow
            from tools import pcskills
            if pcskills.has_install(name):
                return pcskills.pc_uninstall(name, False)
        except Exception:  # noqa: BLE001 - the fallback must never break the normal answer
            pass
        return f"Nothing installed matches '{name}'."
    label = picked[0]["Name"] if picked else (reg[0]["DisplayName"] if reg else store.splitlines()[0])
    key = f"uninstall:{label.lower()}"
    uid = users.current()
    if not (approvals.direct(uid, "uninstall", label) or approvals.consume(uid, key)):
        return approvals.request(uid, key, f"uninstall {label}")
    if picked and "Steam App" in picked[0]["Id"]:
        return f"{label} is a Steam game: uninstall it in Steam (right-click it > Manage > Uninstall)."

    tried = []
    if picked:  # 1. Windows' package manager
        progress.step(f"asking Windows' package manager to remove {label}")
        r = _winget("uninstall", "--id", picked[0]["Id"], "--exact", "--silent", "--force", timeout=TIMEOUT)
        if not _still_there(label):
            return f"Uninstalled {label}."
        tried.append("package manager (" + (_explain(r.returncode) if r.returncode else "said done, but it's still listed") + ")")
    for entry in reg or _registry_programs(label):  # 2. the program's own uninstaller, silently
        cmd = _silent_uninstall_command(entry)
        if not cmd:
            continue
        progress.step(f"running {entry['DisplayName']}'s own uninstaller")
        try:
            code = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, creationflags=_NO_WINDOW).returncode
        except (subprocess.TimeoutExpired, OSError) as e:
            code = "timed out" if isinstance(e, subprocess.TimeoutExpired) else type(e).__name__
        if not _still_there(label):
            return f"Uninstalled {label} (with its own uninstaller)."
        tried.append(f"its own uninstaller (exit {code})")
    if _appx(label, remove=False):  # 3. Store / MSIX app
        progress.step(f"removing {label} as a Store app")
        out = _appx(label, remove=True)
        if not _still_there(label):
            return f"Uninstalled {label} (Store app)."
        tried.append(f"Store-app removal ({out})")
    hint = "" if is_admin() else " Admin mode is off, so steps needing admin rights waited for Windows' approval box."
    return (f"Couldn't remove {label} yet. Tried: {'; '.join(tried) or 'no method applied'}.{hint} "
            "Next: use control_computer through Settings > Apps > Installed apps, without asking again.")


@tool(
    "Install a program on this PC from Windows' package manager (winget / Microsoft Store), silently. "
    "Use for 'install Discord', 'get me VLC'. Shows choices if the name is ambiguous.",
    {"name": {"type": "string", "description": "Program name, e.g. 'VLC', 'Discord', 'OBS Studio'"}},
    slow=True,
)
def install_program(name: str) -> str:
    r = _winget("search", "--name", name.strip(), "--source", "winget")
    found = _table(r.stdout)
    if not found:
        return f"I couldn't find '{name}' in the Windows package catalog."
    picked = _pick(found, name)
    if len(picked) > 1:
        listed = "; ".join(f"{f.get('Name')} ({f.get('Id')})" for f in found[:8])
        return f"Several matches for '{name}': {listed}. Ask which one, then call again with its full name."
    pkg = picked[0]
    from core import progress

    progress.step(f"downloading and installing {pkg['Name']}")
    r = _winget("install", "--id", pkg["Id"], "--exact", "--silent", "--source", "winget",
                "--accept-package-agreements", timeout=TIMEOUT)
    if r.returncode == 0:
        return f"Installed {pkg['Name']}."
    if "already installed" in r.stdout.lower():
        return f"{pkg['Name']} is already installed."
    return f"Couldn't install {pkg['Name']}: {_explain(r.returncode)}."


@tool("List installed programs whose name contains some text (to check what's installed before uninstalling).",
      {"query": {"type": "string"}})
def list_programs(query: str) -> str:
    found = find_installed(query)
    if not found:
        return f"Nothing installed matches '{query}'."
    return "\n".join(f"{m.get('Name')} - {m.get('Version', '')}" for m in found[:25])
