import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from jarvis_pc.guardrails import Guardrails, KillSwitch, Tier, classify
from jarvis_pc.guardrails.killswitch import KillSwitchTriggered
from jarvis_pc.diagnostics import (
    ALL_CHECKS, ALL_STATIC_COMMANDS, DEFAULT_CHECKS, Diagnostics, compare, diagnose, parse_json_rows, parse_ps_date,
    plan_checks, run_checks,
)
from jarvis_pc.diagnostics import collectors as C
from jarvis_pc.verify import ChangeJournal, UndoStep
from jarvis_pc.testing import module_guard

setUpModule, tearDownModule = module_guard()  # no real winget/uninstall/delete from tests

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def ms(dt):
    return f"/Date({int((dt - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1000)})/"


class FakeShell:
    """Stands in for the gated shell: maps a command to canned output. Records every command it was asked."""

    def __init__(self, table=None, ran=True):
        self.table = table or {}
        self.calls = []
        self.ran = ran

    def __call__(self, guard, command, **kw):
        self.calls.append(command)
        d = SimpleNamespace(reasons=["T2: blocked for test"], id="x", tier=Tier.T0)
        if not self.ran:
            return SimpleNamespace(ran=False, ok=False, decision=d, stdout="", stderr="", exit_code=None,
                                   error_kind=None, truncated=False)
        for key, val in self.table.items():
            if command.startswith(key):
                if isinstance(val, SimpleNamespace):
                    return val
                text = val if isinstance(val, str) else json.dumps(val)
                return SimpleNamespace(ran=True, ok=True, decision=d, stdout=text, stderr="", exit_code=0,
                                       error_kind=None, truncated=False)
        return SimpleNamespace(ran=True, ok=False, decision=d, stdout="", stderr="not found", exit_code=1,
                               error_kind="not_found", truncated=False)


def result(stdout="", ok=True, code=0, kind=None, truncated=False, stderr=""):
    return SimpleNamespace(ran=True, ok=ok, stdout=stdout, stderr=stderr, exit_code=code, error_kind=kind,
                           truncated=truncated, decision=SimpleNamespace(reasons=[], id="x", tier=Tier.T0))


def make(table=None, ran=True, **kw):
    g = Guardrails(allowlist_roots=[os.getcwd()], dry_run=False)
    shell = FakeShell(table, ran)
    kw.setdefault("path_reader", lambda: {"user": "", "machine": "", "process": ""})
    return Diagnostics(g, run_fn=shell, now=lambda: NOW, **kw), shell


class GateInvariantTests(unittest.TestCase):
    def test_every_command_the_module_issues_is_t0(self):
        g = Guardrails(allowlist_roots=[r"C:\Jarvis"])
        for cmd in ALL_STATIC_COMMANDS:
            c = classify(cmd, None, g.allowlist)
            self.assertEqual(c.tier, Tier.T0, f"{cmd} -> {c.tier.name}: {c.reasons}")

    def test_diagnostics_run_in_dry_run_because_they_only_read(self):
        g = Guardrails(allowlist_roots=[r"C:\Jarvis"], dry_run=True)
        for cmd in ALL_STATIC_COMMANDS[:3]:
            self.assertTrue(g.authorize(cmd).allowed, cmd)

    def test_gate_blocking_is_reported_not_hidden(self):
        d, _ = make(ran=False)
        f = d.disk()[0]
        self.assertTrue(f.check_error)
        self.assertIn("blocked", f.detail)
        self.assertEqual(diagnose(d, "disk full").result, "FAIL")  # nothing ran: not "healthy"

    def test_killswitch_is_never_swallowed(self):
        ks = KillSwitch()
        ks.trigger()
        g = Guardrails(allowlist_roots=[os.getcwd()], dry_run=False, killswitch=ks)
        d = Diagnostics(g)  # real runner: the gate raises
        with self.assertRaises(KillSwitchTriggered):
            run_checks(d, ["disk"])


class ParsingTests(unittest.TestCase):
    def test_dates_both_powershell_formats(self):
        self.assertEqual(parse_ps_date("/Date(1793769526652)/"), datetime(2026, 11, 4, 5, 18, 46, 652000, tzinfo=timezone.utc))
        self.assertEqual(parse_ps_date("/Date(1793769526652-0500)/").year, 2026)
        self.assertEqual(parse_ps_date("2026-11-04T05:18:46Z").day, 4)
        self.assertIsNone(parse_ps_date("garbage"))
        self.assertIsNone(parse_ps_date(None))

    def test_json_rows_zero_one_many(self):
        self.assertEqual(parse_json_rows(""), [])
        self.assertEqual(parse_json_rows('{"a": 1}'), [{"a": 1}])
        self.assertEqual(parse_json_rows('[{"a": 1}, {"a": 2}]'), [{"a": 1}, {"a": 2}])
        with self.assertRaises(ValueError):
            parse_json_rows("[...truncated...]")


class DiskMemoryTests(unittest.TestCase):
    def disk(self, free_pct):
        size = 1000 * 1024 ** 3
        return [{"DeviceID": "C:", "DriveType": 3, "Size": size, "FreeSpace": size * free_pct / 100},
                {"DeviceID": "D:", "DriveType": 5, "Size": 0, "FreeSpace": 0}]  # cd-rom ignored

    def test_thresholds(self):
        for pct, sev in ((3, "critical"), (10, "warning"), (40, "ok")):
            d, _ = make({C.CMD_DISK: self.disk(pct)})
            fs = d.disk()
            self.assertEqual([f.severity for f in fs], [sev], pct)

    def test_single_drive_object_not_list(self):
        d, _ = make({C.CMD_DISK: self.disk(50)[0]})
        self.assertEqual(d.disk()[0].severity, "ok")

    def test_no_drives_is_unproven_not_ok(self):
        d, _ = make({C.CMD_DISK: []})
        self.assertTrue(d.disk()[0].check_error)

    def test_memory_low_and_uptime(self):
        boot = NOW - timedelta(days=20)
        os_row = {"LastBootUpTime": ms(boot), "FreePhysicalMemory": 500_000, "TotalVisibleMemorySize": 16_000_000}
        d, _ = make({C.CMD_OS: os_row, C.CMD_TOP_PROCS: [{"Name": "chrome", "Id": 1, "WorkingSet": 2 * 1024 ** 3}]})
        fs = d.memory()
        self.assertEqual(fs[0].severity, "critical")
        self.assertTrue(any("20 days ago" in f.title for f in fs))
        self.assertTrue(any("chrome" in f.detail for f in fs))


class NetworkTests(unittest.TestCase):
    def net(self, up=True, ping="True", dns=True):
        def resolver(host, port):
            if not dns:
                raise OSError("no dns")
        status = "Up" if up else "Disconnected"
        d, _ = make({C.CMD_ADAPTERS: [{"Name": "Wi-Fi", "Status": status}], C.CMD_PING: ping}, resolver=resolver)
        return d.network()

    def test_healthy(self):
        fs = self.net()
        self.assertFalse([f for f in fs if f.severity in ("critical", "warning")])

    def test_dns_only_failure_is_pinpointed(self):
        fs = self.net(dns=False)
        self.assertTrue(any("DNS lookups fail" in f.title for f in fs))

    def test_no_connectivity_and_no_adapter(self):
        fs = self.net(up=False, ping="False")
        titles = " | ".join(f.title for f in fs)
        self.assertIn("no network adapter is connected", titles)
        self.assertIn("cannot reach the internet", titles)


class EventAndCrashTests(unittest.TestCase):
    def ev(self, minutes_ago, level="Error", provider="P", i=1):
        return {"TimeCreated": ms(NOW - timedelta(minutes=minutes_ago)), "ProviderName": provider, "Id": i,
                "LevelDisplayName": level}

    def test_groups_filters_by_time_and_level(self):
        rows = [self.ev(5, provider="Disk", i=7)] * 3 + [self.ev(10, "Warning"), self.ev(60 * 30)] + \
               [self.ev(60 * 30 + 1)]  # old error, outside 24 h window
        rows.append(self.ev(60 * 25 + 5, provider="Old"))  # reaches back past 24 h so the window is fully covered
        d, _ = make({C.CMD_EVENTS.format(log="System"): rows, C.CMD_EVENTS.format(log="Application"): []})
        fs = d.events()
        self.assertEqual(len(fs), 1)
        self.assertIn("3 error events in the last 24 h", fs[0].title)
        self.assertIn("Disk id 7 x3", fs[0].detail)
        self.assertNotIn("Old", fs[0].detail)

    def test_critical_is_critical_and_window_is_honest_when_log_is_busy(self):
        rows = [self.ev(1, "Critical", "Kernel-Power", 41)]
        d, _ = make({C.CMD_EVENTS.format(log="System"): rows, C.CMD_EVENTS.format(log="Application"): []})
        f = d.events()[0]
        self.assertEqual(f.severity, "critical")
        self.assertIn("too busy to reach 24 h", f.title)  # the sample only goes back 1 minute: do not claim 24 h

    def test_truncated_output_is_unproven(self):
        big = result('[...truncated...]\n"x"', truncated=True)
        d, _ = make({C.CMD_EVENTS.format(log="System"): big, C.CMD_EVENTS.format(log="Application"): []})
        fs = d.events()
        self.assertTrue(fs[0].check_error)
        self.assertIn("cut off", fs[0].detail)

    def test_crash_dumps(self):
        recent = {"Name": "a.dmp", "Length": 1, "LastWriteTime": ms(NOW - timedelta(days=3))}
        old = {"Name": "b.dmp", "Length": 1, "LastWriteTime": ms(NOW - timedelta(days=200))}
        d, _ = make({C.CMD_DUMPS: [recent, old]})
        self.assertEqual(d.crashes()[0].severity, "warning")
        d, _ = make({C.CMD_DUMPS: [old]})
        self.assertEqual(d.crashes()[0].severity, "info")
        d, _ = make({C.CMD_DUMPS: ""})
        self.assertEqual(d.crashes()[0].severity, "ok")
        d, _ = make({C.CMD_DUMPS: result("", ok=False, code=1, kind="not_found", stderr="Cannot find path")})
        self.assertEqual(d.crashes()[0].severity, "ok")
        d, _ = make({C.CMD_DUMPS: result("", ok=False, code=1, kind="permissions", stderr="Access denied")})
        self.assertTrue(d.crashes()[0].check_error)


class PathTests(unittest.TestCase):
    def test_findings(self):
        scopes = {"user": r"C:\Tools;C:\Tools;C:\Gone;%NOPE_VAR%\bin;;C:\Real;",
                  "machine": r"C:\Real", "process": "ignored"}
        existing = {os.path.normcase(os.path.normpath(p)) for p in (r"C:\Tools", r"C:\Real")}
        d, _ = make(path_reader=lambda: scopes,
                    dir_exists=lambda p: os.path.normcase(os.path.normpath(p)) in existing)
        fs = {f.title: f for f in d.path()}
        self.assertTrue(any("undefined variable" in t for t in fs))
        self.assertTrue(any("1 PATH folder missing on disk" in t for t in fs))
        self.assertTrue(any("2 duplicate PATH entries" in t for t in fs))  # Tools twice + Real in machine too
        self.assertTrue(any("1 empty PATH segment" in t for t in fs))

    def test_clean_path_and_trailing_semicolon_is_fine(self):
        d, _ = make(path_reader=lambda: {"user": r"C:\A;", "machine": r"C:\B"}, dir_exists=lambda p: True)
        fs = d.path()
        self.assertEqual([f.severity for f in fs], ["ok"])


class OtherCollectorTests(unittest.TestCase):
    def test_startup_flags_temp_and_downloads(self):
        rows = [{"Name": "ok", "Command": r"C:\Program Files\App\a.exe"},
                {"Name": "bad", "Command": r"C:\Users\x\AppData\Local\Temp\evil.exe"},
                {"Name": "dl", "Command": r"C:\Users\x\Downloads\setup.exe"}]
        d, _ = make({C.CMD_STARTUP: rows})
        fs = d.startup()
        self.assertEqual(sum(f.severity == "warning" for f in fs), 2)
        self.assertEqual(fs[0].severity, "info")

    def test_restore_points(self):
        d, _ = make({C.CMD_RESTORE: [{"SequenceNumber": 1, "CreationTime": "20261101123456.000000-300"}]})
        self.assertEqual(d.restore()[0].severity, "ok")
        d, _ = make({C.CMD_RESTORE: ""})
        self.assertEqual(d.restore()[0].severity, "warning")
        d, _ = make({C.CMD_RESTORE: result("", ok=False, code=1, kind="unknown")})
        self.assertTrue(d.restore()[0].check_error)

    def test_upgrades(self):
        d, _ = make({C.CMD_UPGRADES: "Name Id\n----\nFoo a.b\n24 upgrades available.\n"})
        self.assertIn("24", d.updates()[0].title)
        d, _ = make({C.CMD_UPGRADES: result("No installed package found matching input criteria.", ok=False, code=1)})
        self.assertEqual(d.updates()[0].severity, "ok")
        d, _ = make({C.CMD_UPGRADES: result("boom", ok=False, code=5)})
        self.assertTrue(d.updates()[0].check_error)

    def test_drivers(self):
        d, _ = make({C.CMD_GPU: [{"Name": "GPU", "DriverVersion": "1.2", "DriverDate": ms(NOW)}],
                     C.CMD_PNP: [{"Status": "Error", "Class": "Net", "FriendlyName": "Broken NIC"},
                                 {"Status": "OK", "Class": "Net", "FriendlyName": "Fine"}]})
        fs = d.drivers()
        self.assertTrue(any(f.severity == "warning" and "Broken NIC" in f.detail for f in fs))

    def test_changes_come_from_the_journal(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        j = ChangeJournal(os.path.join(tmp, "j.jsonl"), os.path.join(tmp, "s"))
        j.record("download", [UndoStep("note", note="x")], group="g1")
        j.record("extract", [UndoStep("note", note="x")], group="g1")
        d, _ = make(journal=j)
        d.now = lambda: datetime.now(timezone.utc)  # the journal stamps entries with the real clock
        fs = d.changes()
        self.assertEqual(len(fs), 1)
        self.assertIn("download, extract", fs[0].title)
        d2, _ = make()
        self.assertEqual(d2.changes(), [])


class DiagnoseTests(unittest.TestCase):
    def test_routing(self):
        self.assertEqual(plan_checks("my wifi keeps dropping"), ["network"])
        self.assertEqual(plan_checks("PC is slow and crashes")[:2], ["memory", "disk"])
        self.assertIn("crashes", plan_checks("blue screen"))
        self.assertEqual(plan_checks("python is not recognized"), ["path"])
        self.assertEqual(plan_checks(""), DEFAULT_CHECKS)
        self.assertEqual(plan_checks("something weird"), DEFAULT_CHECKS)

    def test_every_planned_check_exists(self):
        d, _ = make()
        for name in ALL_CHECKS + DEFAULT_CHECKS:
            self.assertTrue(callable(getattr(d, name, None)), name)

    def test_one_check_crashing_does_not_hide_the_rest(self):
        d, _ = make({C.CMD_DISK: [{"DeviceID": "C:", "DriveType": 3, "Size": 100, "FreeSpace": 90}]})
        d.memory = lambda: 1 / 0
        r = run_checks(d, ["memory", "disk"])
        self.assertEqual(r.result, "PARTIAL")
        self.assertTrue(any("crashed" in f.title for f in r.findings))
        self.assertTrue(any(f.title.endswith("space is fine") for f in r.findings))

    def test_unknown_check_names_are_reported_not_run(self):
        d, shell = make()
        r = run_checks(d, ["_ps", "nonsense"])
        self.assertEqual(shell.calls, [])
        self.assertIn("skipped unknown", r.notes[0])

    def test_render_and_compare_verify_step(self):
        full = [{"DeviceID": "C:", "DriveType": 3, "Size": 100 * 1024 ** 3, "FreeSpace": 2 * 1024 ** 3}]
        d, shell = make({C.CMD_DISK: full})
        before = run_checks(d, ["disk"], "disk full")
        self.assertEqual(before.problems[0].severity, "critical")
        self.assertIn("next step", before.render())
        shell.table[C.CMD_DISK] = [dict(full[0], FreeSpace=60 * 1024 ** 3)]
        after = run_checks(d, ["disk"], "disk full")
        cmp = compare(before, after)
        self.assertEqual(len(cmp["resolved"]), 1)
        self.assertEqual(cmp["remaining"], [])
        self.assertEqual(after.result, "PASS")

    def test_diagnosis_never_changes_anything(self):
        d, shell = make()
        run_checks(d, ALL_CHECKS)
        g = Guardrails(allowlist_roots=[r"C:\Jarvis"])
        for cmd in shell.calls:
            self.assertEqual(classify(cmd, None, g.allowlist).tier, Tier.T0, cmd)


if __name__ == "__main__":
    unittest.main()
