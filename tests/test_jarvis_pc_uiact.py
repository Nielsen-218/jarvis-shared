"""The UI action layer: the language, the executor's safety rules (with a fake UI backend, so they run anywhere), and one
end-to-end school lesson against a real practice window on a Windows PC."""
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jarvis_pc.guardrails import Guardrails
from jarvis_pc.learning import PlaybookStep, PlaybookStore
from jarvis_pc.testing import module_guard
from jarvis_pc.uiact import lang
from jarvis_pc.uiact.executor import Executor, UIError
from jarvis_pc.verify.checks import CheckResult
from jarvis_pc.verify.report import Report

setUpModule, tearDownModule = module_guard()  # no real winget/uninstall/delete from tests


class LanguageTests(unittest.TestCase):
    def test_every_allowed_action_parses(self):
        for line, verb in [('click "Save"', "click"), ('set "Name" "Ada Lovelace"', "set"), ('type "hi"', "type"), ('check "Remember me"', "check"),
                           ('uncheck "x"', "uncheck"), ('select "green"', "select"), ("key enter", "key"), ("hotkey ctrl+s", "hotkey"),
                           ("hotkey ctrl+shift+f5", "hotkey"), ("wait 2", "wait"), ("wait 0.5", "wait"), ('read "Status"', "read"),
                           ("move window 100 80", "move"), ("resize window 640 480", "resize"), ("close window", "close"),
                           ('click "{label}"', "click")]:
            self.assertEqual(lang.parse(line).verb, verb, line)

    def test_everything_dangerous_or_unknown_is_rejected_before_it_can_run(self):
        for bad in ["run notepad", "start calc.exe", "powershell -c Remove-Item x", "click 100,200", 'click "x" and "y"', "click Save",
                    "hotkey alt+f4", "hotkey win+r", "hotkey ctrl+alt+delete", "wait 99", "wait 0", "key banana", "move window abc def",
                    "resize window 100000 5", "close all windows", "focus window", "", "x" * 400]:
            with self.assertRaises(lang.UIParseError, msg=bad):
                lang.parse(bad)
        with self.assertRaises(lang.UIParseError):
            lang.parse('type "a' + chr(10) + 'b"')

    def test_the_hotkey_translation(self):
        self.assertEqual(lang.to_pywinauto_keys("ctrl+s"), "^s")
        self.assertEqual(lang.to_pywinauto_keys("alt+shift+enter"), "%+{ENTER}")
        self.assertEqual(lang.to_pywinauto_keys("ctrl+f5"), "^{F5}")


class FakeUI:
    """Stands in for uiact.uia: records what the executor asked for."""

    def __init__(self, password=False):
        self.calls = []
        self.password = password

    def window(self, title):
        self.calls.append(("window", title))
        return SimpleNamespace(title=title)

    def find(self, win, name, types):
        self.calls.append(("find", name))
        if name == "Missing":
            raise UIError("no button called 'Missing' in this window")
        return SimpleNamespace(name=name)

    def is_password(self, ctl):
        return self.password

    def press(self, ctl):
        self.calls.append(("press", ctl.name))

    def set_text(self, ctl, text):
        self.calls.append(("set", ctl.name, text))

    def set_checked(self, ctl, on):
        self.calls.append(("check", ctl.name, on))

    def select(self, ctl):
        self.calls.append(("select", ctl.name))

    def read(self, win, needle):
        return "Status: ok"

    def focus(self, win):
        self.calls.append(("focus",))

    def send_keys(self, win, keys, literal=False):
        self.calls.append(("keys", keys))

    def move(self, win, x, y):
        self.calls.append(("move", x, y))

    def resize(self, win, w, h):
        self.calls.append(("resize", w, h))

    def close(self, win):
        self.calls.append(("close",))


class ExecutorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ticks = [1000]

    def ex(self, title="Practice abc", ui=None, dry_run=False):
        guard = Guardrails(allowlist_roots=[self.tmp], audit_path=os.path.join(self.tmp, "a.jsonl"), approver=None, dry_run=dry_run)
        ui = ui or FakeUI()
        return Executor(guard, title, input_tick=lambda: self.ticks[0], sleep=lambda s: None, backend=ui), ui

    def test_a_practice_window_is_acted_on_through_the_gate(self):
        ex, ui = self.ex()
        for line in ['click "Save"', 'set "Name" "Ada"', 'check "On"', 'select "x"', "move window 5 6", "resize window 400 300", "close window"]:
            self.assertTrue(ex.run(line).ok, line)
        self.assertIn(("press", "Save"), ui.calls)
        self.assertIn(("set", "Name", "Ada"), ui.calls)
        audit = open(os.path.join(self.tmp, "a.jsonl"), encoding="utf-8").read()
        self.assertIn("ui click", audit)             # every step is an audited gate decision

    def test_a_real_application_window_needs_approval_and_an_unattended_session_cannot_get_it(self):
        ex, ui = self.ex(title="Untitled - Notepad")
        r = ex.run('click "Save"')
        self.assertFalse(r.ran)
        self.assertFalse(r.ok)
        self.assertEqual([c for c in ui.calls if c[0] != "window"], [], "nothing may touch a real app without approval")

    def test_a_window_the_school_launched_itself_is_t1_but_any_other_real_window_stays_t2(self):
        guard = Guardrails(allowlist_roots=[self.tmp], audit_path=os.path.join(self.tmp, "a.jsonl"), approver=None, dry_run=False)
        owned = Executor(guard, "note.txt - Notepad", owned=True, input_tick=lambda: 1, sleep=lambda s: None, backend=FakeUI())
        self.assertTrue(owned.run("hotkey ctrl+s").ok)
        theirs = Executor(guard, "note.txt - Notepad", owned=False, input_tick=lambda: 1, sleep=lambda s: None, backend=FakeUI())
        self.assertFalse(theirs.run("hotkey ctrl+s").ran)

    def test_no_keys_are_sent_when_the_window_cannot_be_brought_to_the_front(self):
        ui = FakeUI()

        def no_focus(win):
            raise UIError("could not bring the window to the front, so I did not send any keys")
        ui.focus = no_focus
        ex, _ = self.ex(ui=ui)
        r = ex.run("hotkey ctrl+s")
        self.assertFalse(r.ok)
        self.assertEqual([c for c in ui.calls if c[0] == "keys"], [], "keys must never land in whatever else is in front")

    def test_dry_run_stops_everything(self):
        ex, ui = self.ex(dry_run=True)
        self.assertFalse(ex.run('click "Save"').ok)
        self.assertEqual(ui.calls, [])

    def test_a_password_field_is_never_typed_into(self):
        ex, ui = self.ex(ui=FakeUI(password=True))
        r = ex.run('set "Password" "hunter2"')
        self.assertFalse(r.ok)
        self.assertIn("password", r.stderr)
        self.assertNotIn(("set", "Password", "hunter2"), ui.calls)

    def test_if_the_person_touches_the_keyboard_the_keyboard_steps_stop(self):
        ex, ui = self.ex()
        self.assertTrue(ex.run("hotkey ctrl+s").ok)      # baseline; the executor remembers its own input
        self.ticks[0] += 5000                            # someone typed or moved the mouse
        r = ex.run("hotkey ctrl+s")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_kind, "user_took_over")
        self.assertEqual([c for c in ui.calls if c[0] == "keys"], [("keys", "^s")])
        self.assertTrue(ex.run('click "Save"').ok, "pattern-based actions never use the keyboard, so they are not blocked")

    def test_errors_are_reported_in_plain_words_and_syntax_errors_never_reach_the_ui(self):
        ex, ui = self.ex()
        r = ex.run('click "Missing"')
        self.assertFalse(r.ok)
        self.assertIn("no button called", r.stderr)
        r = ex.run("run notepad")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_kind, "syntax")
        self.assertEqual([c for c in ui.calls if c[0] not in ("window", "find")], [])

    def test_read_returns_the_text(self):
        ex, _ = self.ex()
        self.assertEqual(ex.run('read "Status"').stdout, "Status: ok")


class PlaybookKindTests(unittest.TestCase):
    def test_a_ui_skill_is_never_offered_to_the_shell_runner(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        store = PlaybookStore(os.path.join(tmp, "p.jsonl"))
        g = Guardrails(allowlist_roots=[tmp], dry_run=False)
        proof = Report("t", [CheckResult("x", True)])
        store.add("press the Save button", [PlaybookStep('click "Save"')], "school:test", g, proof, kind="ui")
        self.assertIsNone(store.find("press the Save button"))
        self.assertEqual(store.all()[0].kind, "ui")
        self.assertEqual(store.all()[0].tier, "T0")


@unittest.skipUnless(os.name == "nt" and os.environ.get("JARVIS_UI_TESTS") == "1",
                     "opens a real practice window and clicks it on the REAL desktop: opt-in, JARVIS_UI_TESTS=1 (2026-11-08)")
class RealWindowLessonTests(unittest.TestCase):
    def _school(self, **kw):
        from jarvis_pc.school import School
        from jarvis_pc.school.ui_subjects import available
        if not available():
            self.skipTest("pywinauto/pywin32 not installed")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        return School(tmp, windows=True, **kw)

    def test_the_school_teaches_clicking_a_button_by_name_and_it_survives_new_layouts(self):
        from jarvis_pc.school import BY_ID
        from jarvis_pc.school.transcript import MASTERED
        calls = []

        def teacher(task, ctx):
            calls.append(ctx)
            return 'click "{label}"'
        teacher.__name__ = "local:fake"
        s = self._school(local_teachers=[teacher])
        s.subjects = lambda: [BY_ID["ui_click_button"]]
        s.study()
        self.assertEqual(s.transcript.state("ui_click_button"), MASTERED)
        self.assertEqual(len(calls), 1, "one lesson; the two exams ran offline on new layouts")
        self.assertIn("UI TASK", calls[0])
        book = s.playbooks.all()[0]
        self.assertEqual((book.kind, book.params), ("ui", ["label"]))
        wrong = s.attempt(BY_ID["ui_click_button"], lambda t, c: 'click "Submit"')
        self.assertFalse(wrong.ok, "pressing a different button must not pass")

    def test_a_teacher_cannot_slip_anything_outside_the_language_past_the_school(self):
        from jarvis_pc.school import BY_ID
        s = self._school()
        for bad in ["run notepad", 'click "{label}"' + chr(10) + "hotkey alt+f4", 'click "x"' + chr(10) + "click 10,20"]:
            a = s.attempt(BY_ID["ui_click_button"], lambda t, c, b=bad: b)
            self.assertFalse(a.ok, bad)



@unittest.skipUnless(os.name == "nt", "Windows only")
class NotepadSafetyTests(unittest.TestCase):
    def test_the_school_never_uses_notepad_when_one_is_already_open(self):
        import random
        from unittest import mock

        from jarvis_pc.school import ui_subjects as u
        from jarvis_pc.school.curriculum import Context, Facts
        if not u.available():
            self.skipTest("pywinauto/pywin32 not installed")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        c = Context(Facts(), random.Random(1), tmp)
        with mock.patch.dict(os.environ, {"JARVIS_SCHOOL_REAL_APPS": "on"}), \
                mock.patch.object(u, "_notepad_windows", return_value=["my notes.txt - Notepad"]), \
                mock.patch.object(u.time, "sleep", lambda s: None), \
                mock.patch.object(u.subprocess, "Popen", side_effect=AssertionError("must not launch Notepad")):
            title, _path = u._spawn_notepad(c)
        self.assertEqual(title, "", "somebody's Notepad is open: the school must not touch it or open files into it")

    def test_real_app_subjects_are_off_unless_the_owner_opts_in(self):
        import random
        from unittest import mock

        from jarvis_pc.school import ui_subjects as u
        from jarvis_pc.school.curriculum import Context, Facts
        if not u.available():
            self.skipTest("pywinauto/pywin32 not installed")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        c = Context(Facts(), random.Random(1), tmp)
        env = {k: v for k, v in os.environ.items() if k != "JARVIS_SCHOOL_REAL_APPS"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(u.subprocess, "Popen", side_effect=AssertionError("must not launch Notepad by default")):
            title, _path = u._spawn_notepad(c)
        self.assertEqual(title, "", "real-app subjects stay off unless JARVIS_SCHOOL_REAL_APPS=on")


if __name__ == "__main__":
    unittest.main()


class TeacherRulesGridHintTests(unittest.TestCase):
    def test_the_ui_teacher_is_told_grids_are_driven_by_keyboard(self):
        from jarvis_pc.uiact import lang
        text = lang.rules_for_teacher()
        self.assertIn("key down/up/left/right moves it", text)
        self.assertIn("a formula starts with =", text)
