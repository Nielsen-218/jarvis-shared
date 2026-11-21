"""Read-only collectors for troubleshoot and maintenance.

Every PowerShell command here goes through the permission gate and is T0 (read-only) as classified by
guardrails/tiers.py. Nothing in this module changes the PC. The gate vocabulary is respected rather than
loosened: full cmdlet names, no script blocks, and any filtering is done in Python on the JSON that comes back.
"""
from __future__ import annotations

from ..text import plural

import json
import os
import re
import socket
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from ..actions.shell import run
from ..guardrails.gate import Guardrails
from ..guardrails.killswitch import KillSwitchTriggered
from ..verify.journal import ChangeJournal
from .findings import Finding, parse_json_rows, parse_ps_date

# Every command this module can issue. A test asserts each one classifies as T0 so this can never drift.
CMD_DISK = "Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,DriveType,Size,FreeSpace | ConvertTo-Json"
CMD_STARTUP = "Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location,User | ConvertTo-Json"
CMD_ADAPTERS = "Get-NetAdapter | Select-Object Name,Status,LinkSpeed | ConvertTo-Json"
CMD_PING = "Test-NetConnection -ComputerName 1.1.1.1 -InformationLevel Quiet"
CMD_DUMPS = "Get-ChildItem -Path C:\\Windows\\Minidump | Select-Object Name,Length,LastWriteTime | ConvertTo-Json"
CMD_RESTORE = "Get-ComputerRestorePoint | Select-Object SequenceNumber,Description,CreationTime | ConvertTo-Json"
CMD_UPGRADES = "winget list --upgrade-available --accept-source-agreements"
CMD_GPU = "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate | ConvertTo-Json"
CMD_PNP = "Get-PnpDevice -PresentOnly | Select-Object Status,Class,FriendlyName | ConvertTo-Json"
CMD_TOP_PROCS = "Get-Process | Sort-Object WorkingSet -Descending | Select-Object -First 5 Name,Id,WorkingSet,CPU | ConvertTo-Json"
CMD_OS = ("Get-CimInstance Win32_OperatingSystem | "
          "Select-Object LastBootUpTime,FreePhysicalMemory,TotalVisibleMemorySize | ConvertTo-Json")
# No Message column: event messages are huge and the shell runner caps output at 200k characters.
CMD_EVENTS = ("Get-WinEvent -LogName {log} -MaxEvents 500 | "
              "Select-Object TimeCreated,ProviderName,Id,LevelDisplayName | ConvertTo-Json")
ALL_STATIC_COMMANDS = [CMD_DISK, CMD_STARTUP, CMD_ADAPTERS, CMD_PING, CMD_DUMPS, CMD_RESTORE, CMD_UPGRADES, CMD_GPU,
                       CMD_PNP, CMD_TOP_PROCS, CMD_OS, CMD_EVENTS.format(log="System"), CMD_EVENTS.format(log="Application")]

_SUSPECT_LOC = re.compile(r"\\(temp|downloads)\\|\\appdata\\local\\temp\\", re.I)
_UPGRADE_COUNT = re.compile(r"(\d+)\s+upgrades?\s+available", re.I)


def default_path_reader() -> Dict[str, str]:
    """The PATH values as the process, the user and the machine see them. Read-only."""
    out = {"process": os.environ.get("PATH", "")}
    if os.name == "nt":
        try:
            import winreg
            for scope, hive, sub in (
                ("user", winreg.HKEY_CURRENT_USER, r"Environment"),
                ("machine", winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            ):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        out[scope] = str(winreg.QueryValueEx(k, "Path")[0])
                except OSError:
                    out[scope] = ""
        except ImportError:
            pass
    return out


class Diagnostics:
    def __init__(self, guard: Guardrails, *, run_fn: Callable = run, journal: Optional[ChangeJournal] = None,
                 resolver: Callable = socket.getaddrinfo, path_reader: Callable[[], Dict[str, str]] = default_path_reader,
                 dir_exists: Callable[[str], bool] = os.path.isdir, now: Optional[Callable[[], datetime]] = None,
                 timeout: float = 90.0):
        self.guard = guard
        self.run_fn = run_fn
        self.journal = journal
        self.resolver = resolver
        self.path_reader = path_reader
        self.dir_exists = dir_exists
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.timeout = timeout

    # ------------------------------------------------------------------ plumbing
    def _ps(self, command: str, area: str, intent: str, timeout: Optional[float] = None):
        """Run through the gate. Returns (result, None) or (None, check_error_finding)."""
        res = self.run_fn(self.guard, command, intent=f"diagnose: {intent}", timeout=timeout or self.timeout)
        if not res.ran:
            why = "; ".join(res.decision.reasons[-2:]) or "blocked"
            return None, Finding("warning", area, f"could not check {intent}", f"blocked by the gate: {why}",
                                 check_error=True)
        return res, None

    def _rows(self, command: str, area: str, intent: str, timeout: Optional[float] = None):
        res, err = self._ps(command, area, intent, timeout)
        if err:
            return None, err
        if not res.ok:
            kind = res.error_kind or "unknown"
            return None, Finding("warning", area, f"could not check {intent}", f"exit {res.exit_code} ({kind})",
                                 evidence=(res.stderr or "")[-300:], check_error=True)
        if res.truncated:
            return None, Finding("warning", area, f"could not check {intent}",
                                 "output was cut off by the shell runner's size cap, so it cannot be trusted",
                                 check_error=True)
        try:
            return parse_json_rows(res.stdout), None
        except ValueError as e:
            return None, Finding("warning", area, f"could not check {intent}", f"unreadable output: {e}",
                                 evidence=(res.stdout or "")[:200], check_error=True)

    # ------------------------------------------------------------------ collectors
    def disk(self) -> List[Finding]:
        rows, err = self._rows(CMD_DISK, "disk", "disk space")
        if err:
            return [err]
        out: List[Finding] = []
        fixed = [r for r in rows if r.get("DriveType") == 3 and r.get("Size")]
        if not fixed:
            return [Finding("warning", "disk", "could not check disk space", "no fixed drives reported",
                            check_error=True)]
        for r in fixed:
            size, free = float(r["Size"]), float(r.get("FreeSpace") or 0)
            pct = free / size * 100
            gb = free / 1024 ** 3
            label = f"{r['DeviceID']} has {gb:.1f} GB free ({pct:.0f}%)"
            if pct < 5:
                out.append(Finding("critical", "disk", f"{r['DeviceID']} is almost full", label,
                                   hint="free space: clear temp/caches, uninstall unused apps, move big files"))
            elif pct < 15:
                out.append(Finding("warning", "disk", f"{r['DeviceID']} is running low on space", label,
                                   hint="windows needs headroom to update and page; free some space"))
            else:
                out.append(Finding("ok", "disk", f"{r['DeviceID']} space is fine", label))
        return out

    def memory(self) -> List[Finding]:
        rows, err = self._rows(CMD_OS, "memory", "memory and uptime")
        if err:
            return [err]
        if not rows:
            return [Finding("warning", "memory", "could not check memory", "no data", check_error=True)]
        r = rows[0]
        out: List[Finding] = []
        total, free = float(r.get("TotalVisibleMemorySize") or 0), float(r.get("FreePhysicalMemory") or 0)
        if total:
            pct = free / total * 100
            detail = f"{free / 1048576:.1f} GB free of {total / 1048576:.1f} GB ({pct:.0f}%)"
            if pct < 5:
                out.append(Finding("critical", "memory", "memory is nearly exhausted", detail,
                                   hint="close the biggest apps below; restart if it keeps filling"))
            elif pct < 10:
                out.append(Finding("warning", "memory", "memory is tight", detail,
                                   hint="close heavy apps or add RAM if this is normal for your workload"))
            else:
                out.append(Finding("ok", "memory", "memory is fine", detail))
        boot = parse_ps_date(r.get("LastBootUpTime"))
        if boot:
            days = (self.now() - boot).days
            if days >= 14:
                out.append(Finding("info", "memory", f"last restart was {days} days ago",
                                   "a restart clears leaks and finishes pending updates"))
        procs, perr = self._rows(CMD_TOP_PROCS, "memory", "biggest memory users")
        if perr:
            out.append(perr)
        elif procs:
            top = ", ".join(f"{p.get('Name')} ({float(p.get('WorkingSet') or 0) / 1048576:.0f} MB)" for p in procs)
            out.append(Finding("info", "memory", "biggest memory users", top))
        return out

    def startup(self) -> List[Finding]:
        rows, err = self._rows(CMD_STARTUP, "startup", "startup entries")
        if err:
            return [err]
        out: List[Finding] = [Finding("warning" if len(rows) > 30 else "info", "startup",
                                      f"{len(rows)} startup entries",
                                      "many entries slow boot" if len(rows) > 30 else "",
                                      hint="review the list; disabling is reversible and goes through the gate")]
        for r in rows:
            cmd = str(r.get("Command") or "")
            if _SUSPECT_LOC.search(cmd):
                out.append(Finding("warning", "startup", f"startup entry runs from a temp/downloads folder: {r.get('Name')}",
                                   cmd[:200], hint="programs that start from Temp or Downloads are unusual; check the "
                                                   "publisher before leaving it enabled"))
        return out

    def network(self) -> List[Finding]:
        out: List[Finding] = []
        rows, err = self._rows(CMD_ADAPTERS, "network", "network adapters")
        up = []
        if err:
            out.append(err)
        else:
            up = [r for r in rows if str(r.get("Status")).lower() == "up"]
            if not up:
                out.append(Finding("critical", "network", "no network adapter is connected",
                                   ", ".join(f"{r.get('Name')}: {r.get('Status')}" for r in rows) or "no adapters found",
                                   hint="check cable/Wi-Fi switch, then enable the adapter (that change needs approval)"))
            else:
                out.append(Finding("ok", "network", "adapter is up", ", ".join(str(r.get("Name")) for r in up)))
        res, perr = self._ps(CMD_PING, "network", "internet reachability")
        reach: Optional[bool] = None
        if perr:
            out.append(perr)
        elif res.ok:
            reach = res.stdout.strip().lower() == "true"
        else:
            out.append(Finding("warning", "network", "could not check internet reachability",
                               f"exit {res.exit_code} ({res.error_kind})", check_error=True))
        dns_ok: Optional[bool]
        try:
            self.resolver("example.com", 80)
            dns_ok = True
        except OSError:
            dns_ok = False
        if reach is True and dns_ok:
            out.append(Finding("ok", "network", "internet and DNS work"))
        elif reach is True and not dns_ok:
            out.append(Finding("critical", "network", "internet is reachable but DNS lookups fail",
                               "pinging 1.1.1.1 works, names do not resolve",
                               hint="try another DNS server or flush the DNS cache (changing DNS needs approval)"))
        elif reach is False:
            out.append(Finding("critical", "network", "cannot reach the internet",
                               "ping to 1.1.1.1 failed" + ("" if up else " (no adapter is up)"),
                               hint="check the router/modem and the gateway before changing anything on the PC"))
        return out

    def events(self, hours: float = 24.0) -> List[Finding]:
        since = self.now() - timedelta(hours=hours)
        groups: Counter = Counter()
        crit = errs = 0
        reach: List[datetime] = []  # how far back each log's sample goes
        out: List[Finding] = []
        for log in ("System", "Application"):
            rows, err = self._rows(CMD_EVENTS.format(log=log), "events", f"{log} event log", timeout=120)
            if err:
                out.append(err)
                continue
            times = [t for t in (parse_ps_date(r.get("TimeCreated")) for r in rows) if t]
            if times:
                reach.append(min(times))
            for r in rows:
                t = parse_ps_date(r.get("TimeCreated"))
                lvl = str(r.get("LevelDisplayName") or "")
                if not t or t < since or lvl not in ("Error", "Critical"):
                    continue
                groups[(log, str(r.get("ProviderName")), int(r.get("Id") or 0))] += 1
                crit += lvl == "Critical"
                errs += lvl == "Error"
        # Only the newest 500 events per log are sampled. If that does not reach back `hours`, say so.
        covered = bool(reach) and all(t <= since for t in reach)
        window = (f"the last {hours:g} h" if covered else
                  f"the newest events (back to {max(reach):%Y-%m-%d %H:%M} UTC; the logs are too busy to reach {hours:g} h)"
                  if reach else f"the last {hours:g} h")
        total = crit + errs
        if total == 0:
            if reach:
                out.append(Finding("ok", "events", f"no error events in {window}"))
            return out
        top = "; ".join(f"{p} id {i} x{n} ({log})" for (log, p, i), n in groups.most_common(5))
        sev = "critical" if crit else ("warning" if total >= 20 else "info")
        out.append(Finding(sev, "events", f"{plural(total, 'error event')} in {window} ({crit} critical)",
                           f"most frequent: {top}",
                           hint="look up the top provider/event id; compare its time with recent installs or updates"))
        return out

    def crashes(self) -> List[Finding]:
        res, err = self._ps(CMD_DUMPS, "crashes", "crash dumps")
        if err:
            return [err]
        if not res.ok:
            if res.error_kind == "not_found" or "does not exist" in res.stderr.lower() or "cannot find" in res.stderr.lower():
                return [Finding("ok", "crashes", "no crash dumps on this PC")]
            return [Finding("warning", "crashes", "could not check crash dumps",
                            f"exit {res.exit_code} ({res.error_kind})", check_error=True)]
        try:
            rows = parse_json_rows(res.stdout)
        except ValueError as e:
            return [Finding("warning", "crashes", "could not check crash dumps", f"unreadable output: {e}",
                            check_error=True)]
        if not rows:
            return [Finding("ok", "crashes", "no crash dumps on this PC")]
        dated = sorted(((parse_ps_date(r.get("LastWriteTime")), str(r.get("Name"))) for r in rows),
                       key=lambda x: x[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        newest, name = dated[0]
        recent = [d for d, _ in dated if d and (self.now() - d).days <= 30]
        if recent:
            return [Finding("warning", "crashes", f"{plural(len(recent), 'blue-screen dump')} in the last 30 days",
                            f"newest: {name} on {newest:%Y-%m-%d %H:%M} UTC ({len(rows)} total)",
                            hint="the dump names the faulting driver, but JARVIS cannot read dumps yet: open the newest "
                                 "with WinDbg or BlueScreenView, and compare its date with recent driver/app changes")]
        return [Finding("info", "crashes", f"{plural(len(rows), 'old crash dump')}, none in the last 30 days",
                        f"newest: {name} on {newest:%Y-%m-%d}" if newest else "")]

    def path(self) -> List[Finding]:
        scopes = self.path_reader()
        out: List[Finding] = []
        if os.name == "nt":
            self.guard.audit.record("read", what="PATH from environment and registry (read-only)")
        seen: Dict[str, str] = {}
        missing: List[str] = []
        dupes: List[str] = []
        unresolved: List[str] = []
        empties = 0
        for scope in ("user", "machine", "process"):
            if scope == "process" and ("user" in scopes or "machine" in scopes):
                continue  # the process view is just user+machine merged; do not double count
            value = scopes.get(scope, "")
            if value.endswith(";"):
                value = value[:-1]  # one trailing ';' is normal and harmless
            for raw in (value.split(";") if value else []):
                seg = raw.strip().strip('"')
                if not seg:
                    empties += 1  # an empty segment in the middle ';;'
                    continue
                exp = os.path.expandvars(seg)
                if "%" in exp:
                    unresolved.append(f"{scope}: {seg}")
                    continue
                norm = os.path.normcase(os.path.normpath(exp))
                if norm in seen:
                    dupes.append(seg)
                    continue
                seen[norm] = seg
                if not self.dir_exists(exp):
                    missing.append(f"{scope}: {seg}")
        total_len = sum(len(v) for k, v in scopes.items() if k in ("user", "machine"))
        if unresolved:
            out.append(Finding("warning", "path", f"{plural(len(unresolved), 'PATH entr', 'y', 'ies')} with an undefined variable",
                               "; ".join(unresolved[:5]), hint="define the variable or fix the entry (back up PATH first)"))
        if missing:
            out.append(Finding("warning" if len(missing) > 3 else "info", "path",
                               f"{plural(len(missing), 'PATH folder')} missing on disk", "; ".join(missing[:6]),
                               hint="stale entries from uninstalled programs; harmless but worth cleaning. Back up PATH first"))
        if dupes:
            out.append(Finding("info", "path", f"{plural(len(dupes), 'duplicate PATH entr', 'y', 'ies')}", "; ".join(dupes[:6])))
        if total_len > 2047:
            out.append(Finding("warning", "path", f"PATH is {total_len} characters long",
                               "very long PATH values get truncated by some programs", hint="remove stale entries"))
        if empties:
            out.append(Finding("info", "path", f"{plural(empties, 'empty PATH segment')}", "stray ';;' entries"))
        if not out:
            out.append(Finding("ok", "path", "PATH looks healthy", f"{plural(len(seen), 'folder')}, all exist"))
        return out

    def restore(self) -> List[Finding]:
        res, err = self._ps(CMD_RESTORE, "restore", "restore points")
        if err:
            return [err]
        if not res.ok:
            return [Finding("warning", "restore", "could not check restore points",
                            f"exit {res.exit_code} ({res.error_kind}); System Protection may be off",
                            evidence=res.stderr[-200:], check_error=True)]
        try:
            rows = parse_json_rows(res.stdout)
        except ValueError as e:
            return [Finding("warning", "restore", "could not check restore points", f"unreadable output: {e}",
                            check_error=True)]
        if not rows:
            return [Finding("warning", "restore", "no restore points exist",
                            "system changes cannot be rolled back at the Windows level",
                            hint="run jarvis_pc/windows/setup_restore_task.ps1 as admin so JARVIS can create one before changes")]
        stamps = []
        for r in rows:
            m = re.match(r"(\d{14})", str(r.get("CreationTime") or ""))
            if m:
                stamps.append(datetime.strptime(m.group(1), "%Y%m%d%H%M%S"))
        newest = max(stamps) if stamps else None
        return [Finding("ok", "restore", f"{plural(len(rows), 'restore point')}",
                        f"newest: {newest:%Y-%m-%d %H:%M}" if newest else "")]

    def updates(self) -> List[Finding]:
        res, err = self._ps(CMD_UPGRADES, "updates", "available app upgrades", timeout=180)
        if err:
            return [err]
        text = (res.stdout or "")
        m = _UPGRADE_COUNT.search(text)
        if m:
            n = int(m.group(1))
            return [Finding("info", "updates", f"{plural(n, 'app upgrade')} available via winget",
                            hint="upgrades are applied through the gated installer, not from here")]
        if res.ok or "no installed package" in text.lower() or "no applicable" in text.lower():
            return [Finding("ok", "updates", "winget apps are up to date")]
        return [Finding("warning", "updates", "could not check app upgrades", f"exit {res.exit_code} ({res.error_kind})",
                        check_error=True)]

    def drivers(self) -> List[Finding]:
        out: List[Finding] = []
        gpu, err = self._rows(CMD_GPU, "drivers", "graphics driver")
        if err:
            out.append(err)
        else:
            for g in gpu:
                d = parse_ps_date(g.get("DriverDate"))
                out.append(Finding("info", "drivers", f"graphics: {g.get('Name')}",
                                   f"driver {g.get('DriverVersion')}" + (f", dated {d:%Y-%m-%d}" if d else "")))
        pnp, perr = self._rows(CMD_PNP, "drivers", "devices with problems")
        if perr:
            out.append(perr)
        else:
            bad = [p for p in pnp if str(p.get("Status")) in ("Error", "Degraded")]
            if bad:
                out.append(Finding("warning", "drivers", f"{plural(len(bad), 'device')} with a reported problem",
                                   "; ".join(f"{p.get('FriendlyName')} ({p.get('Class')})" for p in bad[:6]),
                                   hint="open Device Manager for the error code; driver changes need approval and a restore point"))
            else:
                out.append(Finding("ok", "drivers", "no devices report a driver problem"))
        return out

    def changes(self, days: float = 7.0) -> List[Finding]:
        """'What changed?' comes first in any diagnosis: JARVIS's own journal of installs/uninstalls."""
        if self.journal is None:
            return []
        cutoff = self.now() - timedelta(days=days)
        groups: Dict[str, List[str]] = {}
        for e in self.journal.entries():
            t = parse_ps_date(e.get("ts"))
            if not t or t < cutoff:
                continue
            groups.setdefault(e.get("group") or e["id"], []).append(
                e["description"] + (" (undone)" if e.get("undone") else ""))
        return [Finding("info", "changes", ", ".join(descs[:3]) + (" ..." if len(descs) > 3 else ""),
                        f"{plural(len(descs), 'journaled change')} in the last {days:g} days")
                for _g, descs in list(groups.items())[-5:]]
