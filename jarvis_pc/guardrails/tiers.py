"""Permission tiers: classify a shell command as T0 / T1 / T2 / T3.

T0 read-only, automatic. T1 reversible change (restore point + log, proceed).
T2 always ask. T3 never.

Design rule: anything not positively recognised is T2 (ask). Only a short list
of known-safe commands gets a lower tier. Compound commands take the highest
tier of their parts.

LIMITS (read this): this is a heuristic gate on command text, not a sandbox.
It cannot see what a script or program does once running. Real containment
comes from also running JARVIS under a limited account / VM and keeping
restore points. Use it as a seatbelt, not a vault.
"""
from __future__ import annotations

import enum
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .allowlist import PathAllowlist, extract_paths


class Tier(enum.IntEnum):
    T0 = 0  # read-only
    T1 = 1  # reversible change
    T2 = 2  # ask first
    T3 = 3  # never


@dataclass
class Classification:
    tier: Tier
    reasons: List[str] = field(default_factory=list)
    segments: List[str] = field(default_factory=list)


_FLAGS = re.I | re.S

# ---- T3: never, matched against the whole command text -----------------------
T3_PATTERNS: List[Tuple[str, str]] = [
    (r"set-mppreference\b.*-disable\w+\s+\$?(true|1)\b", "disables Defender protection"),
    (r"add-mppreference\b.*-exclusion\w*", "adds a Defender exclusion"),
    (r"\b(stop-service|sc(\.exe)?\s+(stop|config)|net\s+stop)\b.*\b(windefend|wdnissvc|sense|mpssvc)\b",
     "stops a security service"),
    (r"netsh\s+advfirewall\s+set\s+\w+\s+state\s+off", "turns the firewall off"),
    (r"set-netfirewallprofile\b.*-enabled\s+(false|0)", "turns the firewall off"),
    (r"disable(antispyware|realtimemonitoring|behaviormonitoring|ioavprotection)",
     "disables antivirus protection"),
    (r"\b(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b[^|;&\n]*\|\s*(sudo\s+)?"
     r"(sh|bash|zsh|iex|invoke-expression|powershell|pwsh|python3?)\b",
     "pipes a download straight into an interpreter"),
    (r"\b(iex|invoke-expression)\b.*\b(downloadstring|downloadfile|iwr|irm|invoke-webrequest|"
     r"invoke-restmethod|webclient)\b", "runs downloaded code"),
    (r"\b(powershell|pwsh)(\.exe)?\b.*\s-(e|ec|enc|encodedcommand)\s+[a-z0-9+/=]{16,}",
     "encoded PowerShell command (cannot be inspected)"),
    (r"frombase64string\b", "base64-decoded payload"),
    (r"\brm\s+(-\w+\s+)*-\w*[rf]\w*\s+(--\s+)?(/|~|/\*)(\s|$)", "recursive delete of / or home"),
    (r"\b(remove-item|del|erase|rd|rmdir|ri)\b[^\n;|&]*\s[a-z]:[\\/]?\*?(\s|$)",
     "deletes a drive root"),
    (r"\bformat(\.com)?\s+[a-z]:", "formats a drive"),
]

# ---- T3: protected data, never touched ---------------------------------------
PROTECTED_PATTERNS: List[Tuple[str, str]] = [
    (r"google[\\/]+chrome[\\/]+user data", "browser profile data"),
    (r"microsoft[\\/]+edge[\\/]+user data", "browser profile data"),
    (r"mozilla[\\/]+firefox[\\/]+profiles", "browser profile data"),
    (r"\b(login data|logins\.json|key[34]\.db|cookies\.sqlite)\b", "browser credential store"),
    (r"\.kdbx?\b", "password manager database"),
    (r"[\\/]\.?1password", "password manager data"),
    (r"[\\/]bitwarden", "password manager data"),
    (r"[\\/]\.ssh[\\/]+id_", "SSH private key"),
    (r"[\\/]\.aws[\\/]+credentials", "cloud credentials"),
    (r"ntds\.dit", "domain credential database"),
    (r"system32[\\/]+config[\\/]+(sam|system|security)\b", "Windows credential hive"),
    (r"\blsass\b", "credential process"),
    (r"/etc/(shadow|gshadow|sudoers)\b|[\\/]\.gnupg\b", "system secrets"),
    (r"\b(cmdkey\s+/list|vaultcmd|mimikatz)\b", "credential dumping"),
]

# ---- T2 bump: sensitive files -------------------------------------------------
SENSITIVE_PATTERNS: List[Tuple[str, str]] = [
    (r"(?<![\w.])\.env(\.\w+)?(?!\w)", "secrets file (.env)"),
    (r"\.npmrc\b|\.netrc\b|\.git-credentials\b|\.pypirc\b", "credentials config file"),
    (r"[\\/]\.docker[\\/]config\.json", "docker credentials"),
    (r"\bid_(rsa|ed25519|ecdsa)\b", "private key file"),
]

ELEVATION = {"sudo", "doas", "runas", "su", "pkexec"}

T2_CMDS = {
    "rm", "del", "erase", "rmdir", "rd", "remove-item", "ri", "format", "format-volume",
    "diskpart", "bcdedit", "reg", "regedit", "set-itemproperty", "sp", "new-itemproperty",
    "remove-itemproperty", "clear-itemproperty", "sc", "set-service", "new-service",
    "stop-service", "start-service", "restart-service", "net", "net1", "netsh",
    "new-netfirewallrule", "set-netfirewallrule", "remove-netfirewallrule",
    "enable-netfirewallrule", "disable-netfirewallrule", "shutdown", "restart-computer",
    "stop-computer", "reboot", "poweroff", "halt", "dism", "sfc", "pnputil", "schtasks",
    "register-scheduledtask", "unregister-scheduledtask", "set-scheduledtask", "setx",
    "set-executionpolicy", "takeown", "icacls", "cacls", "chmod", "chown", "kill", "killall",
    "pkill", "taskkill", "stop-process", "curl", "wget", "iwr", "irm", "invoke-webrequest",
    "invoke-restmethod", "start-bitstransfer", "certutil", "bitsadmin", "mshta", "rundll32",
    "regsvr32", "wmic", "invoke-expression", "iex", "invoke-command", "icm", "start-process",
    "start", "systemctl", "service", "apt", "apt-get", "dpkg", "yum", "dnf", "snap", "mount",
    "umount", "fdisk", "mkfs", "dd", "crontab", "usermod", "useradd", "passwd", "visudo",
    "add-type",
}

SHELLS = {"powershell", "pwsh", "cmd", "bash", "sh", "zsh", "wsl", "fish", "dash"}

T0_CMDS = {
    "ls", "dir", "cat", "type", "echo", "pwd", "whoami", "uname", "df", "du", "ps", "tasklist",
    "ping", "nslookup", "tracert", "traceroute", "netstat", "where", "which", "head", "tail",
    "grep", "rg", "wc", "sort", "findstr", "systeminfo", "cd", "set-location", "sl", "pushd",
    "popd", "select-string", "test-path", "where-object", "select-object", "sort-object",
    "measure-object", "format-table", "format-list", "out-string", "out-host", "out-null",
    "write-output", "write-host", "convertto-json", "convertfrom-json", "resolve-path",
    "split-path", "join-path", "test-connection", "test-netconnection", "nproc", "uptime",
    "free", "lsblk", "id", "true", "false", "start-mpscan",
}
GET_BLOCK = {"get-credential", "get-clipboard", "get-secret", "get-storedcredential", "get-secretinfo"}

T0_BANNED_ARGS = {
    "sort": ("-o", "--output"),
    "rg": ("--pre",),
}

T1_FILE_CMDS = {
    "mkdir", "md", "new-item", "ni", "copy-item", "cp", "copy", "touch", "move-item", "mv",
    "move", "rename-item", "ren", "rename", "set-content", "add-content", "out-file",
    "expand-archive", "unzip", "tar", "compress-archive",
}

VERSION_FLAGS = {"--version", "-version", "-v", "version", "--help", "-h", "-help", "/?", "-?"}


# ---- command splitting ----------------------------------------------------------
def split_segments(command: str) -> Tuple[List[str], bool]:
    """Split on ; && || | & and newlines, respecting quotes.

    Returns (segments, balanced). A backslash escapes a following quote
    character (bash semantics); unbalanced quotes make the caller escalate.
    """
    segs: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    i, n = 0, len(command)

    def flush() -> None:
        s = "".join(buf).strip()
        if s:
            segs.append(s)
        buf.clear()

    while i < n:
        c = command[i]
        if c == "\\" and i + 1 < n and command[i + 1] in "\"'" and quote != "'":
            buf.append(c)
            buf.append(command[i + 1])
            i += 2
            continue
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            buf.append(c)
        elif c in ";\n\r":
            flush()
        elif c in "&|":
            if i + 1 < n and command[i + 1] == c:
                i += 1
                flush()
            elif c == "&" and ((buf and buf[-1] == ">") or (i + 1 < n and command[i + 1] == ">")):
                buf.append(c)  # 2>&1 or &>
            else:
                flush()
        else:
            buf.append(c)
        i += 1
    flush()
    return segs, quote is None


def _first_token(seg: str) -> Tuple[str, str]:
    s = seg.strip()
    if s and s[0] in "\"'":
        end = s.find(s[0], 1)
        raw = s[: end + 1] if end != -1 else s
    else:
        parts = s.split(None, 1)
        raw = parts[0] if parts else ""
    name = re.split(r"[\\/]", raw.strip("\"'"))[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return raw, name


def _strip_quoted(s: str) -> str:
    return re.sub(r'"[^"]*"|\'[^\']*\'', '""', s)


def _has_redirect(s: str) -> bool:
    t = _strip_quoted(s)
    t = re.sub(r"\d*>&\d", "", t)
    t = re.sub(r"\d*>>?\s*(/dev/null|nul|\$null)\b", "", t, flags=re.I)
    return ">" in t


Result = Tuple[Tier, str, bool]  # tier, reason, check_paths


_GIT_CAN_WRITE_OR_RUN = ("--output", "--ext-diff", "--open-files-in-pager", "--exec-path", "--upload-pack", "--receive-pack")


def _git(rest: List[str], orig: Optional[List[str]] = None) -> Result:
    # Global options come before the subcommand. `-C <folder>` only says WHERE to look, so it is skipped (it used to be mistaken for the
    # subcommand, which made every `git -C folder status` ask). `-c key=value` can make git run a program (core.pager, core.sshCommand,
    # diff drivers...), and a few options write files or run helpers, so those are never read-only. The gate lowercases tokens, so the
    # original-case ones (`orig`) are what tell `-C` from `-c`.
    orig = orig if orig is not None and len(orig) == len(rest) else list(rest)
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        a = orig[i]
        if a == "-C":
            i += 2
            continue
        if a == "-c" or (a.startswith("-c") and len(a) > 2 and a[2] != "-") or a.startswith("--config-env"):
            return Tier.T2, "git -c can run a program", False
        i += 1
    orig_rest = orig[i:]
    rest = rest[i:]
    if any(a == o or a.startswith(o + "=") for a in rest for o in _GIT_CAN_WRITE_OR_RUN) or any(a == "-O" or (a.startswith("-O") and len(a) > 2) for a in orig_rest):
        return Tier.T2, "git option that writes a file or runs a helper", False
    sub = next((a for a in rest if not a.startswith("-")), "")
    if sub in {"status", "log", "diff", "show", "rev-parse", "rev-list", "shortlog", "show-ref", "ls-files", "ls-remote", "describe", "blame"}:
        return Tier.T0, "read-only git", False
    if sub == "branch":
        if any(a in {"-d", "-m", "-c", "--delete", "--move", "--copy"} for a in rest):
            return Tier.T2, "git branch delete/rename", False
        return Tier.T0, "read-only git", False
    if sub == "remote":
        if any(a in {"add", "remove", "rm", "set-url", "rename"} for a in rest):
            return Tier.T2, "git remote change", False
        return Tier.T0, "read-only git", False
    if sub in {"clone", "init", "fetch", "add", "commit"}:
        return Tier.T1, f"git {sub}", True
    return Tier.T2, f"git {sub or '(unknown)'} can change or discard work", False


def _winget(rest: List[str]) -> Result:
    sub = next((a for a in rest if not a.startswith("-")), "")
    if sub in {"search", "show", "list", "view", "hash"} or (sub == "source" and "list" in rest):
        return Tier.T0, "winget read-only", False
    if sub in {"install", "add", "upgrade", "update", "uninstall", "remove", "rm"}:
        return Tier.T1, f"winget {sub}", False
    return Tier.T2, f"winget {sub or '(unknown)'}", False


def _remote_source(rest: List[str]) -> bool:
    return any(
        re.search(r"(https?://|git\+|github:|--index-url|--extra-index-url|--trusted-host|^-i$)", a)
        for a in rest
    )


def _pip(rest: List[str]) -> Result:
    sub = next((a for a in rest if not a.startswith("-")), "")
    if sub in {"list", "show", "freeze", "check", "index", "inspect"}:
        return Tier.T0, "pip read-only", False
    if sub in {"install", "uninstall"}:
        if _remote_source(rest):
            return Tier.T2, "pip install from a custom URL/VCS/index", False
        return Tier.T1, f"pip {sub}", False
    return Tier.T2, f"pip {sub or '(unknown)'}", False


def _npm(rest: List[str]) -> Result:
    sub = next((a for a in rest if not a.startswith("-")), "")
    if sub in {"ls", "list", "view", "info", "outdated", "ping", "doctor"} or (
        sub == "audit" and "fix" not in rest
    ):
        return Tier.T0, "npm read-only", False
    if sub in {"install", "i", "ci", "update", "uninstall", "remove", "rm", "audit"}:
        if any(a in {"-g", "--global"} for a in rest):
            return Tier.T2, "npm global change", False
        if _remote_source(rest):
            return Tier.T2, "npm install from URL/VCS", False
        return Tier.T1, f"npm {sub}", False
    return Tier.T2, f"npm {sub or '(unknown)'} runs project code", False


def _choco(rest: List[str]) -> Result:
    sub = next((a for a in rest if not a.startswith("-")), "")
    if sub in {"list", "search", "info", "outdated", "find"}:
        return Tier.T0, "choco read-only", False
    if sub in {"install", "upgrade", "uninstall"}:
        return Tier.T1, f"choco {sub}", False
    return Tier.T2, f"choco {sub or '(unknown)'}", False


def _docker(rest: List[str]) -> Result:
    sub = next((a for a in rest if not a.startswith("-")), "")
    if sub in {"ps", "images", "version", "info", "inspect", "logs"}:
        return Tier.T0, "docker read-only", False
    return Tier.T2, f"docker {sub or '(unknown)'}", False


def _ipconfig(rest: List[str]) -> Result:
    if all(a in {"/all"} for a in rest):
        return Tier.T0, "ipconfig read-only", False
    return Tier.T2, "ipconfig changes network state", False


SUBCOMMAND = {
    "git": _git, "winget": _winget, "pip": _pip, "pip3": _pip, "npm": _npm,
    "choco": _choco, "docker": _docker, "ipconfig": _ipconfig,
}


_LOOPBACK_URL = re.compile(r"^https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d{1,5})?(?:/[A-Za-z0-9._~\-/%]*)?(?:\?[A-Za-z0-9._~\-=&%]*)?$", re.I)
_FETCH_FLAGS = {"curl": {"-s", "-S", "-sS", "-Ss", "-f", "--silent", "--show-error", "--fail"},
                "iwr": {"-usebasicparsing"}, "irm": set(), "invoke-webrequest": {"-usebasicparsing"}, "invoke-restmethod": set()}


def _loopback_fetch(cmd: str, tokens: List[str]) -> bool:
    """True for a plain GET of ONE http(s) page on this very PC (127.0.0.1, localhost, [::1]): nothing is sent, saved or followed.

    Every other form stays T2: any other host, a flag that writes a file (-o, -O, -OutFile), sends data (-d, -Method, -Body),
    follows redirects to somewhere else (-L), or goes through a proxy. Used so the school can practise research against a stand-in
    web server on the same PC without a permission prompt per page."""
    allowed = _FETCH_FLAGS.get(cmd)
    if allowed is None:
        return False
    urls = []
    for t in tokens:
        t = t.strip("\"'")
        if t.lower() in {f.lower() for f in allowed}:
            continue
        if t.lower() == "-uri":
            continue
        urls.append(t)
    return len(urls) == 1 and bool(_LOOPBACK_URL.match(urls[0]))


_FFPROBE_FLAG = {"-hide_banner", "-show_format", "-show_streams", "-show_chapters", "-pretty", "-count_frames", "-sexagesimal", "-bitexact"}
_FFPROBE_VALUE = {"-v", "-loglevel", "-show_entries", "-of", "-print_format", "-output_format", "-select_streams", "-i"}
_FFPROBE_SAFE = re.compile(r"^[A-Za-z0-9_=,.:|*+\-]+$")
_FFPROBE_PATH = re.compile(r"^(?:[A-Za-z]:)?[^:*?\"<>|]+$")


def _ffprobe_read_only(args: str) -> bool:
    """ffprobe only describes a local media file. Anything that could write (-o), reach a network or protocol (rtmp://, concat:, lavfi),
    or is not on this short list of flags keeps asking."""
    toks = [t.strip("\"'") for t in re.findall(r"\"[^\"]*\"|'[^']*'|\S+", args)]
    paths, i = 0, 0
    while i < len(toks):
        t = toks[i]
        if t.lower() in _FFPROBE_FLAG:
            i += 1
        elif t.lower() in _FFPROBE_VALUE and i + 1 < len(toks):
            v = toks[i + 1]
            if t.lower() == "-i":
                if not _FFPROBE_PATH.match(v):
                    return False
                paths += 1
            elif not _FFPROBE_SAFE.match(v) or "://" in v:
                return False
            i += 2
        elif not t.startswith("-") and _FFPROBE_PATH.match(t):
            paths += 1
            i += 1
        else:
            return False
    return paths == 1


_WSL_LIST_FLAGS = {"--list", "-l", "--verbose", "-v", "--quiet", "-q", "--all", "--running", "--online", "-o"}


def _wsl_list_only(rest: List[str]) -> bool:
    """`wsl --list` (with its display flags) only prints which Linux distributions exist; it never starts one."""
    return bool(rest) and all(a in _WSL_LIST_FLAGS for a in rest) and any(a in {"--list", "-l"} for a in rest)


_WSL_INSIDE_READS = {"cat /etc/os-release", "uname -r", "hostname", "nproc", "whoami"}


def _wsl_inside_read(rest: List[str]) -> bool:
    """`wsl -d <distro> -- <one fixed read-only command>`: asks the Linux inside WSL a question that prints a fact. The command list is
    fixed, so nothing can write, install or reach the Windows side."""
    if len(rest) < 4 or rest[0] not in {"-d", "--distribution"} or not re.fullmatch(r"[a-z0-9._-]{1,40}", rest[1]) or rest[2] != "--":
        return False
    return " ".join(rest[3:]) in _WSL_INSIDE_READS


LAPTOP_HOST = "jarvis@jarvis-laptop"
_LAPTOP_READS = {"hostname", "uptime -p", "systemctl is-active jarvis.service", "df -h /", "cat /proc/loadavg", "nproc", "free -m"}


def _laptop_read(args: str) -> bool:
    """`ssh -i <key> -o BatchMode=yes [-o ConnectTimeout=N] jarvis@jarvis-laptop <one fixed read-only command>`: the owner's own laptop,
    key-only login, and only a short list of commands that print a fact. Another host, another option, a shell trick or any other
    remote command keeps asking."""
    toks = [t.strip("\"'") for t in re.findall(r"\"[^\"]*\"|'[^']*'|\S+", args)]
    i, key, batch = 0, False, False
    while i < len(toks) and toks[i].startswith("-"):
        if toks[i] == "-i" and i + 1 < len(toks) and re.fullmatch(r"[A-Za-z0-9_:./\\~-]*jarvis_laptop", toks[i + 1]):
            key = True
        elif toks[i] == "-o" and i + 1 < len(toks) and toks[i + 1] == "BatchMode=yes":
            batch = True
        elif toks[i] == "-o" and i + 1 < len(toks) and re.fullmatch(r"ConnectTimeout=\d{1,2}", toks[i + 1]):
            pass
        else:
            return False
        i += 2
    if not (key and batch) or i >= len(toks) or toks[i] != LAPTOP_HOST:
        return False
    return " ".join(toks[i + 1:]).strip() in _LAPTOP_READS


def _classify_segment(seg: str, cwd: Optional[str], allowlist: Optional[PathAllowlist]) -> Tuple[Tier, str]:
    s = seg.strip()
    raw, cmd = _first_token(s)
    low = s.lower()
    rest = low[len(raw):].split()
    unq = _strip_quoted(s)

    if re.search(r"\$\(|`", re.sub(r"'[^']*'", "''", s)):
        return Tier.T2, "command substitution"
    if "{" in unq:
        return Tier.T2, "script block / brace group"
    if _has_redirect(s):
        return Tier.T2, "output redirection writes a file"
    if cmd in ELEVATION:
        return Tier.T2, "elevation"
    if cmd == "start-process" and re.search(r"-verb\s+runas", low):
        return Tier.T2, "elevation"
    if cmd in _FETCH_FLAGS and _loopback_fetch(cmd, s[len(raw):].split()):
        return Tier.T0, f"{cmd}: plain GET of a page on this PC only"
    if cmd == "ffprobe" and _ffprobe_read_only(s[len(raw):]):
        return Tier.T0, "ffprobe describes one local media file"
    if cmd == "ssh" and _laptop_read(s[len(raw):]):
        return Tier.T0, "read-only fact from the owner's laptop"
    if cmd == "wsl" and (_wsl_list_only(rest) or _wsl_inside_read(rest)):
        return Tier.T0, "wsl --list only lists distributions"
    if cmd == "remove-item":
        # Deleting is reversible only inside the folders Jarvis may work in, so it is T1 there and T2 everywhere else. Wildcards and
        # the allowed folder itself always ask, and the path check below sends any path outside the allowed folders to T2.
        args = s[len(raw):].split()
        if not [a for a in args if not a.startswith("-")]:
            return Tier.T2, "remove-item without a path"
        if any(ch in a for a in args for ch in "*?[") or (allowlist is not None and any(
                os.path.normcase(os.path.abspath(a.strip("\"'"))) in {os.path.normcase(os.path.abspath(r)) for r in allowlist.roots}
                for a in args if not a.startswith("-"))):
            return Tier.T2, "remove-item with a wildcard or on an allowed folder itself"
    elif cmd in T2_CMDS:
        return Tier.T2, f"{cmd} is a risky command"
    if rest and all(a in VERSION_FLAGS for a in rest):
        return Tier.T0, "version/help"
    if cmd in SHELLS:
        return Tier.T2, "nested shell; inner command not inspected"

    tier: Tier
    reason: str
    check_paths = False
    if cmd in SUBCOMMAND:
        tier, reason, check_paths = _git(rest, s[len(raw):].split()) if cmd == "git" else SUBCOMMAND[cmd](rest)
    elif cmd in {"checkpoint-computer", "update-mpsignature"}:
        tier, reason = Tier.T1, f"{cmd} (reversible system maintenance)"
    elif cmd in T1_FILE_CMDS or cmd == "remove-item":
        tier, reason, check_paths = Tier.T1, f"{cmd} modifies files", True
    elif cmd in T0_CMDS or (cmd.startswith("get-") and cmd not in GET_BLOCK):
        banned = T0_BANNED_ARGS.get(cmd, ())
        if any(a.startswith(b) for a in rest for b in banned):
            return Tier.T2, f"{cmd} with a writing/executing flag"
        return Tier.T0, "read-only"
    else:
        return Tier.T2, f"unrecognised command '{cmd}'"

    if tier == Tier.T1 and check_paths:
        if allowlist is None or not allowlist.roots:
            return Tier.T2, f"{reason}; no allowed folders configured"
        if cwd is None or not allowlist.is_allowed(cwd):
            return Tier.T2, f"{reason}; working folder not allowed"
        bad = allowlist.first_violation(extract_paths(s[len(raw):]), cwd)
        if bad is not None:
            return Tier.T2, f"{reason}; touches path outside allowed folders: {bad}"
    return tier, reason


def classify(command: str, cwd: Optional[str] = None, allowlist: Optional[PathAllowlist] = None) -> Classification:
    reasons: List[str] = []
    tier = Tier.T0
    if not command.strip():
        return Classification(Tier.T0, ["empty command"], [])

    for pat, why in T3_PATTERNS + PROTECTED_PATTERNS:
        if re.search(pat, command, _FLAGS):
            reasons.append(why)
            tier = Tier.T3
    if tier == Tier.T3:
        return Classification(tier, reasons, [])

    segs, balanced = split_segments(command)
    if not balanced:
        tier = Tier.T2
        reasons.append("unbalanced quotes; cannot parse reliably")
    for seg in segs:
        t, why = _classify_segment(seg, cwd, allowlist)
        if t > tier:
            tier = t
        reasons.append(f"{why}: {seg[:60]}")
    for pat, why in SENSITIVE_PATTERNS:
        if re.search(pat, command, _FLAGS):
            tier = max(tier, Tier.T2)
            reasons.append(why)
    return Classification(tier, reasons, segs)
