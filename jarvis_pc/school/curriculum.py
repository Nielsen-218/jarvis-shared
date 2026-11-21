"""The curriculum: what JARVIS has to master before it graduates to the person.

Every subject is a question or job a person would give a PC assistant, plus a way to PROVE the answer without trusting any
model. The proof is ground truth that does not come from the commands being tested:

  * facts about this PC (name, user, RAM, disk, build...) are read with Python, not PowerShell;
  * file subjects build a fresh throw-away sandbox folder with random names and contents, so the right answer is known
    exactly, and every exam uses a different variant than the lesson did (a memorised answer cannot pass).

A subject yields a Task per attempt: the question (with {placeholders} the learned steps must use, because a skill that
only works on one folder is not a skill), the truths the output must contain, and, for jobs that change something, a
state check on the sandbox afterwards.

Stages: high_school = read-only basics (the gate's T0), taught by the free local models; college = harder multi-step jobs
that change files inside the sandbox (T1), taught by the paid API only after the local models gave up on them.
"""
from __future__ import annotations

import ctypes
import getpass
import hashlib
import os
import platform
import random
import re
import shutil
import socket
import string
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

HIGH_SCHOOL = "high_school"
COLLEGE = "college"
STAGES = (HIGH_SCHOOL, COLLEGE)


# ---------------------------------------------------------------------------------------------- how answers are judged
@dataclass(frozen=True)
class AnyOf:
    """At least one of these strings must appear (for example the same date written three ways)."""
    options: Tuple[str, ...]


@dataclass(frozen=True)
class Absent:
    """None of these may appear. Stops a lazy answer ("print everything") from passing a question about a subset."""
    options: Tuple[str, ...]


@dataclass(frozen=True)
class Num:
    """A number must appear whose value, in some common unit, is within `tol` (a fraction) of `value`."""
    value: float
    tol: float = 0.0
    scales: Tuple[float, ...] = (1.0,)


Truth = Union[str, AnyOf, Num, Absent]
# a standalone number: not part of a date (11/4/2026, 2026-11-04) or a time (10:49:20) or a word
_NUM = re.compile(r"(?<![\w./:-])\d[\d,]*(?:\.\d+)?(?![\w/:-]|\.\d)")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


def _numbers(text: str) -> List[float]:
    out = []
    for m in _NUM.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def truth_met(output: str, truth: Truth) -> bool:
    low = _norm(output)
    if isinstance(truth, str):
        return _norm(truth) in low
    if isinstance(truth, AnyOf):
        return any(_norm(o) in low for o in truth.options)
    if isinstance(truth, Absent):
        return not any(_norm(o) in low for o in truth.options if o)
    if isinstance(truth, Num):
        for n in _numbers(output):
            for sc in truth.scales:
                v = n * sc
                if truth.value == 0:
                    if v == 0:
                        return True
                elif abs(v - truth.value) <= truth.tol * abs(truth.value):
                    return True
        return False
    return False


def judge(output: str, truths: Sequence[Truth]) -> Tuple[bool, List[str]]:
    """(all truths present, the ones that were missing). An empty truth list can never pass: no proof, no mastery."""
    if not truths:
        return False, ["no ground truth to check against"]
    missing = [describe(t) for t in truths if not truth_met(output, t)]
    return (not missing), missing


def describe(t: Truth) -> str:
    if isinstance(t, str):
        return repr(t)
    if isinstance(t, AnyOf):
        return "one of " + " / ".join(repr(o) for o in t.options[:3])
    if isinstance(t, Absent):
        return "none of " + " / ".join(repr(o) for o in t.options[:3])
    return f"a number near {t.value:g}"


# ---------------------------------------------------------------------------------------------- facts about this PC
class Facts:
    """Ground truth about this PC, read WITHOUT the shell commands being tested. Tests replace this with a fake."""

    def computer_name(self) -> str:
        return platform.node()

    def user(self) -> str:
        return getpass.getuser()

    def os_build(self) -> str:
        return str(sys.getwindowsversion().build) if hasattr(sys, "getwindowsversion") else platform.release()

    def cpu_logical(self) -> int:
        return os.cpu_count() or 0

    def ram_bytes(self) -> int:
        class MEM(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEM()
        m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
        return int(m.ullTotalPhys)

    def disk(self, drive: str = "C:\\") -> Tuple[int, int]:
        u = shutil.disk_usage(drive)
        return u.total, u.free

    def timezone(self) -> str:
        return time.tzname[0]

    def today(self) -> time.struct_time:
        return time.localtime()

    def ipv4s(self) -> List[str]:
        try:
            return socket.gethostbyname_ex(socket.gethostname())[2]
        except OSError:
            return []

    def git_version(self) -> str:
        try:
            return subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def manufacturer(self) -> str:
        """First word of the PC maker from the registry, read without PowerShell/WMI."""
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as k:
                return str(winreg.QueryValueEx(k, "SystemManufacturer")[0]).split()[0]
        except (OSError, ImportError, IndexError):
            return ""

    def installed_apps(self) -> List[Tuple[str, str]]:
        """(name, version) of installed programs from the 64-bit Uninstall registry key, read without PowerShell."""
        out: List[Tuple[str, str]] = []
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall") as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                            name = str(winreg.QueryValueEx(k, "DisplayName")[0]).strip()
                            ver = str(winreg.QueryValueEx(k, "DisplayVersion")[0]).strip()
                    except OSError:
                        continue
                    if name and ver and re.fullmatch(r"[A-Za-z0-9 ._+-]{3,45}", name) and re.fullmatch(r"\d[\w.+-]*", ver):
                        out.append((name, ver))
        except (OSError, ImportError):
            pass
        return out

    def which(self, name: str) -> str:
        return shutil.which(name) or ""

    def signed_system_file(self) -> str:
        for f in (r"C:\Windows\System32\notepad.exe", r"C:\Windows\System32\cmd.exe", r"C:\Windows\explorer.exe"):
            if os.path.isfile(f):
                return f
        return ""

    def visible_windows(self) -> List[Tuple[int, str]]:
        """(pid, title) of visible top-level windows, read with user32 (no shell)."""
        out: List[Tuple[int, str]] = []
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def cb(hwnd, _lp):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    pid = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    out.append((int(pid.value), buf.value))
                return True
            user32.EnumWindows(proto(cb), 0)
        except Exception:  # noqa: BLE001
            pass
        return out

    def service_state(self, name: str) -> str:
        try:
            out = subprocess.run(["sc", "query", name], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", out)
        return m.group(1).title() if m else ""


# ---------------------------------------------------------------------------------------------- a single attempt
@dataclass
class Task:
    ask: str                                   # what the person says, with the real values filled in
    params: Dict[str, str]                     # {name: value}; learned steps must use {name}, never the value
    truths: List[Truth]
    state_check: Optional[Callable[[], Tuple[bool, str]]] = None  # for jobs that change the sandbox
    max_tier: int = 0                          # highest gate tier the lesson may use (0 = read-only, 1 = sandbox edits)
    kind: str = "shell"                       # "shell" (PowerShell through the gate) or "ui" (the UI action language, see uiact/lang.py)
    ui_title: str = ""                         # for kind=ui: the one practice window the executor is bound to
    ui_owned: bool = False                     # for kind=ui: the school launched this window itself (for example Notepad on a temp file)


@dataclass
class Subject:
    id: str
    title: str
    stage: str
    make: Callable[["Context"], Task]
    aliases: Tuple[str, ...] = ()
    needs: Tuple[str, ...] = ()               # subjects to master first
    needs_windows: bool = False               # ground truth only exists on a real Windows PC
    kind: str = "shell"
    real_app: bool = False                    # drives a real third-party application: only part of the syllabus when the owner opts in


@dataclass
class Context:
    facts: Facts
    rng: random.Random
    sandbox: str                              # a fresh empty folder for this attempt
    cleanups: List[Callable[[], None]] = field(default_factory=list)

    def on_cleanup(self, fn: Callable[[], None]) -> None:
        """Undo something a subject set up outside the sandbox (an environment variable, a listening port, a child process)."""
        self.cleanups.append(fn)

    def run_cleanups(self) -> None:
        while self.cleanups:
            try:
                self.cleanups.pop()()
            except Exception:  # noqa: BLE001
                pass

    def folder(self, name: str = "") -> str:
        p = os.path.join(self.sandbox, name) if name else self.sandbox
        os.makedirs(p, exist_ok=True)
        return p

    def word(self, n: int = 7) -> str:
        return "".join(self.rng.choice(string.ascii_lowercase) for _ in range(n))

    def write(self, path: str, text: str, age_days: float = 0.0) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if age_days:
            t = time.time() - age_days * 86400
            os.utime(path, (t, t))
        return path


# ---------------------------------------------------------------------------------------------- high school: PC facts
def _fact(ask: str, truths: Callable[[Facts], List[Truth]]) -> Callable[[Context], Task]:
    return lambda c: Task(ask, {}, truths(c.facts))


def _date_options(t: time.struct_time) -> Tuple[str, ...]:
    month = time.strftime("%B", t)
    return (f"{month} {t.tm_mday}, {t.tm_year}", time.strftime("%Y-%m-%d", t), f"{t.tm_mon}/{t.tm_mday}/{t.tm_year}",
            f"{t.tm_mon:02d}/{t.tm_mday:02d}/{t.tm_year}")


def _disk_free(c: Context) -> Task:
    return Task("how much free space is on my C drive", {}, [Num(c.facts.disk()[1], 0.03, (1.0, 1e3, 1e6, 1e9, 1e12, 1024.0, 1024.0 ** 2, 1024.0 ** 3, 1024.0 ** 4))])


def _disk_total(c: Context) -> Task:
    return Task("how big is my C drive in total", {}, [Num(c.facts.disk()[0], 0.03, (1.0, 1e3, 1e6, 1e9, 1e12, 1024.0, 1024.0 ** 2, 1024.0 ** 3, 1024.0 ** 4))])


def _ram(c: Context) -> Task:
    return Task("how much RAM does this computer have", {}, [Num(c.facts.ram_bytes(), 0.04, (1.0, 1e3, 1e6, 1e9, 1e12, 1024.0, 1024.0 ** 2, 1024.0 ** 3, 1024.0 ** 4))])


def _process_running(c: Context) -> Task:
    return Task("is explorer running", {}, ["explorer"])


def _git(c: Context) -> Task:
    v = c.facts.git_version()
    return Task("what version of git is installed", {}, [v] if v else [])


def _service(c: Context) -> Task:
    st = c.facts.service_state("EventLog")
    return Task("is the Windows Event Log service running", {}, [st] if st else [])


# ---------------------------------------------------------------------------------------------- high school: files
def _files_dir(c: Context, n: int = 5, exts: Sequence[str] = (".txt", ".log", ".csv", ".md")) -> Tuple[str, List[str]]:
    d = c.folder(c.word(6))
    names = []
    while len(names) < n:
        name = c.word(6) + c.rng.choice(exts)
        if name not in names:
            names.append(name)
            c.write(os.path.join(d, name), c.word(c.rng.randint(5, 40)) + "\n")
    return d, names


def _list_folder(c: Context) -> Task:
    d, names = _files_dir(c)
    return Task("list the files in {dir}", {"dir": d}, list(names))


def _count_files(c: Context) -> Task:
    d, names = _files_dir(c, c.rng.randint(4, 9))
    return Task("how many files are in {dir}", {"dir": d}, [Num(len(names), 0.0)])


def _find_ext(c: Context) -> Task:
    d, names = _files_dir(c, 6, (".txt", ".log"))
    for ext in (".log", ".txt"):  # always one to find AND one decoy, or "list everything" would be a right answer
        if not any(n.endswith(ext) for n in names):
            extra = c.word(6) + ext
            c.write(os.path.join(d, extra), "x" + chr(10))
            names.append(extra)
    logs = [n for n in names if n.endswith(".log")]
    return Task("which .log files are in {dir}", {"dir": d}, [*logs, Absent(tuple(n for n in names if n.endswith(".txt")))])


def _file_size(c: Context) -> Task:
    d = c.folder(c.word(6))
    p = c.write(os.path.join(d, c.word(6) + ".txt"), c.word(c.rng.randint(100, 900)))
    return Task("how many bytes is the file {file}", {"file": p}, [Num(os.path.getsize(p), 0.0)])


def _read_file(c: Context) -> Task:
    d = c.folder(c.word(6))
    secret = c.word(10)
    p = c.write(os.path.join(d, c.word(5) + ".txt"), f"the code word is {secret}\n")
    return Task("what does the file {file} say", {"file": p}, [secret])


def _first_lines(c: Context) -> Task:
    d = c.folder(c.word(6))
    lines = [c.word(8) for _ in range(6)]
    p = c.write(os.path.join(d, c.word(5) + ".txt"), "\n".join(lines) + "\n")
    return Task("show me the first 2 lines of {file}", {"file": p}, [*lines[:2], Absent(tuple(lines[2:]))])


def _search_text(c: Context) -> Task:
    d, names = _files_dir(c, 5, (".txt",))
    word = c.word(9)
    target = names[c.rng.randrange(len(names))]
    c.write(os.path.join(d, target), f"nothing here but {word} in the middle\n")
    return Task("which file in {dir} contains the word {word}", {"dir": d, "word": word},
                [target, Absent(tuple(n for n in names if n != target))])


def _hash(c: Context) -> Task:
    d = c.folder(c.word(6))
    p = c.write(os.path.join(d, c.word(5) + ".bin"), c.word(60))
    with open(p, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return Task("what is the SHA256 hash of {file}", {"file": p}, [digest])


def _newest(c: Context) -> Task:
    d, names = _files_dir(c, 5, (".txt",))
    ages = list(range(10, 10 + len(names)))
    c.rng.shuffle(ages)
    for n, a in zip(names, ages):
        t = time.time() - a * 86400
        os.utime(os.path.join(d, n), (t, t))
    newest = names[ages.index(min(ages))]
    return Task("which file in {dir} was changed most recently", {"dir": d}, [newest, Absent(tuple(n for n in names if n != newest))])


def _largest(c: Context) -> Task:
    d = c.folder(c.word(6))
    sizes = c.rng.sample(range(50, 900, 7), 5)
    best, others = "", []
    for s in sizes:
        n = c.word(6) + ".txt"
        c.write(os.path.join(d, n), "z" * s)
        if s == max(sizes):
            best = n
        else:
            others.append(n)
    return Task("which file in {dir} is the biggest", {"dir": d}, [best, Absent(tuple(others))])


def _folder_size(c: Context) -> Task:
    d, names = _files_dir(c, 6)
    total = sum(os.path.getsize(os.path.join(d, n)) for n in names)
    return Task("how many bytes are in all the files in {dir} together", {"dir": d}, [Num(total, 0.0)])


def _line_count(c: Context) -> Task:
    d = c.folder(c.word(6))
    n = c.rng.randint(8, 40)
    p = c.write(os.path.join(d, c.word(5) + ".log"), "".join(f"{c.word(6)}\n" for _ in range(n)))
    return Task("how many lines are in {file}", {"file": p}, [Num(n, 0.0)])


def _find_deep(c: Context) -> Task:
    d = c.folder(c.word(6))
    sub = c.word(5)
    name = c.word(8) + ".cfg"
    c.write(os.path.join(d, c.word(4), c.word(4), "noise.txt"), "x\n")
    c.write(os.path.join(d, sub, "deeper", name), "x\n")
    return Task("find the file named {name} somewhere under {dir}", {"dir": d, "name": name}, [sub, Absent(("noise.txt",))])


# ---------------------------------------------------------------------------------------------- college: jobs that change files
def _state(fn: Callable[[], Tuple[bool, str]]) -> Callable[[], Tuple[bool, str]]:
    return fn


def _copy_backup(c: Context) -> Task:
    src = c.folder(c.word(6))
    dst = os.path.join(c.sandbox, c.word(6))
    p = c.write(os.path.join(src, c.word(5) + ".txt"), c.word(30))
    want = os.path.join(dst, os.path.basename(p))

    def check() -> Tuple[bool, str]:
        if not os.path.isfile(want):
            return False, f"{want} was not created"
        same = open(want, "rb").read() == open(p, "rb").read()
        return same, "copy matches" if same else "copy differs from the original"
    return Task("copy {file} into a new folder {dest} and keep the original", {"file": p, "dest": dst}, [], check, 1)


def _make_note(c: Context) -> Task:
    d = os.path.join(c.sandbox, c.word(6))
    word = c.word(9)
    want = os.path.join(d, "note.txt")

    def check() -> Tuple[bool, str]:
        if not os.path.isfile(want):
            return False, "note.txt was not created"
        ok = word in open(want, encoding="utf-8", errors="replace").read()
        return ok, "note has the text" if ok else "note does not contain the text"
    return Task("create the folder {dir} and put a file note.txt in it that says {word}", {"dir": d, "word": word}, [], check, 1)


def _move_matching(c: Context) -> Task:
    d = c.folder(c.word(6))
    logs = [c.word(6) + ".log" for _ in range(3)]
    keep = [c.word(6) + ".txt" for _ in range(2)]
    for n in logs + keep:
        c.write(os.path.join(d, n), "x\n")
    dest = os.path.join(d, "logs")

    def check() -> Tuple[bool, str]:
        moved = all(os.path.isfile(os.path.join(dest, n)) and not os.path.exists(os.path.join(d, n)) for n in logs)
        left = all(os.path.isfile(os.path.join(d, n)) for n in keep)
        return moved and left, "logs moved, others untouched" if moved and left else "wrong files moved or left"
    return Task("move every .log file in {dir} into a subfolder called logs inside it", {"dir": d}, [], check, 1)


def _rename(c: Context) -> Task:
    d = c.folder(c.word(6))
    old = c.write(os.path.join(d, c.word(5) + ".txt"), c.word(20))
    new_name = c.word(6) + ".txt"
    other = c.write(os.path.join(d, c.word(5) + ".txt"), c.word(20))

    def check() -> Tuple[bool, str]:
        ok = os.path.isfile(os.path.join(d, new_name)) and not os.path.exists(old) and os.path.isfile(other)
        return ok, "renamed, others untouched" if ok else "the file was not renamed correctly"
    return Task("rename the file {file} to {newname}", {"file": old, "newname": new_name}, [], check, 1)


def _zip_folder(c: Context) -> Task:
    d = c.folder(c.word(6))
    names = [c.word(5) + ".txt" for _ in range(3)]
    for n in names:
        c.write(os.path.join(d, n), c.word(20))
    out = os.path.join(c.sandbox, c.word(6) + ".zip")

    def check() -> Tuple[bool, str]:
        if not os.path.isfile(out):
            return False, "the zip was not created"
        try:
            with zipfile.ZipFile(out) as z:
                got = {os.path.basename(i) for i in z.namelist()}
        except zipfile.BadZipFile:
            return False, "the zip is not a valid archive"
        ok = set(names) <= got
        return ok, "zip holds every file" if ok else "zip is missing files"
    return Task("zip up everything in {dir} into {zip}", {"dir": d, "zip": out}, [], check, 1)


def _append_line(c: Context) -> Task:
    d = c.folder(c.word(6))
    first = c.word(8)
    word = c.word(9)
    p = c.write(os.path.join(d, c.word(5) + ".txt"), first + chr(10))

    def check() -> Tuple[bool, str]:
        text = open(p, encoding="utf-8", errors="replace").read().splitlines()
        ok = text[:1] == [first] and text[-1:] == [word]
        return ok, "line added at the end, the rest kept" if ok else "the file was not extended correctly"
    return Task("add the line {word} to the end of the file {file}", {"file": p, "word": word}, [], check, 1)


def _old_files(c: Context) -> Task:
    d = c.folder(c.word(6))
    old = [c.word(6) + ".txt" for _ in range(c.rng.randint(2, 4))]
    new = [c.word(6) + ".txt" for _ in range(3)]
    for n in old:
        c.write(os.path.join(d, n), "x\n", age_days=40)
    for n in new:
        c.write(os.path.join(d, n), "x\n", age_days=1)
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))
    return Task("how many files in {dir} were last changed before {cutoff}", {"dir": d, "cutoff": cutoff}, [Num(len(old), 0.0)])


def _biggest_folder(c: Context) -> Task:
    d = c.folder(c.word(6))
    subs = [c.word(5) for _ in range(3)]
    sizes = c.rng.sample(range(100, 1200, 50), 3)
    for s, n in zip(sizes, subs):
        c.write(os.path.join(d, n, "f.txt"), "q" * s)
    best = subs[sizes.index(max(sizes))]
    return Task("which folder directly inside {dir} holds the biggest file", {"dir": d}, [best, Absent(tuple(x for x in subs if x != best))])


# ---------------------------------------------------------------------------------------------- ports, processes, environment
def _env_var(c: Context) -> Task:
    name = "SCHOOL_" + c.word(6).upper()
    value = c.word(12)
    os.environ[name] = value                  # child shells inherit it
    c.on_cleanup(lambda: os.environ.pop(name, None))
    return Task("what is the value of the environment variable {name}", {"name": name}, [value])


def _port_owner(c: Context) -> Task:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    c.on_cleanup(sock.close)
    port = sock.getsockname()[1]
    return Task("which process id is listening on TCP port {port}", {"port": str(port)}, [Num(os.getpid(), 0.0)])


def _process_name(c: Context) -> Task:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(180)"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c.on_cleanup(child.kill)
    time.sleep(0.4)
    return Task("what is the name of the program running as process id {pid}", {"pid": str(child.pid)},
                [os.path.splitext(os.path.basename(sys.executable))[0]])


def _word_count(c: Context) -> Task:
    d = c.folder(c.word(6))
    n = c.rng.randint(12, 60)
    p = c.write(os.path.join(d, c.word(5) + ".txt"), " ".join(c.word(5) for _ in range(n)) + chr(10))
    return Task("how many words are in {file}", {"file": p}, [Num(n, 0.0)])


def _manufacturer(c: Context) -> Task:
    m = c.facts.manufacturer()
    return Task("who made this computer", {}, [m] if m else [])


# ---------------------------------------------------------------------------------------------- apps, services, signatures, logs, windows
def _app_version(c: Context) -> Task:
    apps = c.facts.installed_apps()
    words_by_app = [{w for w in re.findall(r"[A-Za-z]{5,}", n)} for n, _v in apps]
    picks = []
    for i, (name, ver) in enumerate(apps):
        others = set().union(*[w for j, w in enumerate(words_by_app) if j != i]) if len(apps) > 1 else set()
        distinct = [w for w in words_by_app[i] if w not in others]
        if distinct:
            picks.append((distinct[0], name, ver))
    if len(picks) < 3:
        return Task("what version of {app} is installed", {"app": "x"}, [])
    token, _name, ver = picks[c.rng.randrange(len(picks))]
    decoys = tuple(v for t, _n, v in picks if t != token and v != ver)[:4]
    return Task("what version of {app} is installed", {"app": token}, [ver, Absent(decoys)])


def _app_path(c: Context) -> Task:
    have = [n for n in ("git", "cmd", "powershell") if c.facts.which(n)]
    if not have:
        return Task("what is the full path of the program {name}", {"name": "git"}, [])
    n = have[c.rng.randrange(len(have))]
    return Task("what is the full path of the program {name}", {"name": n}, [c.facts.which(n)])


def _service_by_name(c: Context) -> Task:
    choices = [(n, c.facts.service_state(n)) for n in ("Dnscache", "Winmgmt", "EventLog", "WinDefend", "Schedule")]  # always-on services only: demand-start ones (BITS, Spooler) change state between the truth and the command
    choices = [(n, st) for n, st in choices if st in ("Running", "Stopped")]
    if not choices:
        return Task("is the Windows service {name} running or stopped", {"name": "EventLog"}, [])
    name, st = choices[c.rng.randrange(len(choices))]
    return Task("is the Windows service {name} running or stopped", {"name": name}, [st, Absent(("Stopped" if st == "Running" else "Running",))])


def _file_signature(c: Context) -> Task:
    signed = c.facts.signed_system_file()
    if signed and c.rng.random() < 0.5:
        return Task("is the file {file} digitally signed", {"file": signed}, ["Valid", Absent(("NotSigned",))])
    d = c.folder(c.word(6))
    p = c.write(os.path.join(d, c.word(5) + ".ps1"), "Write-Output hello" + chr(10))
    return Task("is the file {file} digitally signed", {"file": p}, ["NotSigned", Absent(("Valid",))])


def _log_lines(c: Context) -> Tuple[str, int, List[str]]:
    d = c.folder(c.word(6))
    lines, errors = [], []
    for _ in range(c.rng.randint(14, 30)):
        level = c.rng.choice(["INFO", "INFO", "WARN", "ERROR"])
        msg = c.word(9)
        lines.append(f"{level} {msg}")
        if level == "ERROR":
            errors.append(msg)
    if len(errors) < 2:
        for _ in range(2 - len(errors)):
            msg = c.word(9)
            lines.append(f"ERROR {msg}")
            errors.append(msg)
    p = c.write(os.path.join(d, c.word(5) + ".log"), chr(10).join(lines) + chr(10))
    return p, len(errors), errors


def _log_error_count(c: Context) -> Task:
    p, n, _errors = _log_lines(c)
    return Task("how many ERROR lines are in the log file {file}", {"file": p}, [Num(n, 0.0)])


def _log_last_error(c: Context) -> Task:
    p, _n, errors = _log_lines(c)
    return Task("what is the last ERROR message in the log file {file}", {"file": p}, [errors[-1], Absent(tuple(errors[:-1]))])


def _window_title(c: Context) -> Task:
    title = "School " + c.word(8)
    code = ("import tkinter as t;r=t.Tk();r.title(" + repr(title) + ");r.geometry('200x80+-3000+-3000');"
            "r.after(150000,r.destroy);r.mainloop()")
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c.on_cleanup(child.kill)
    pid = 0
    for _ in range(40):                      # up to ~8 s for the window to appear; its owner may be a child of the launcher
        time.sleep(0.2)
        hit = [p for p, t in c.facts.visible_windows() if t == title]
        if hit:
            pid = hit[0]
            break
    if not pid:
        return Task("what is the title of the window that belongs to process id {pid}", {"pid": "0"}, [])
    return Task("what is the title of the window that belongs to process id {pid}", {"pid": str(pid)}, [title])


# ---------------------------------------------------------------------------------------------- the registry
def _build() -> List[Subject]:
    hs, co = HIGH_SCHOOL, COLLEGE
    return [
        # --- this PC (read-only)
        Subject("pc_name", "computer name", hs, _fact("what is this computer called", lambda f: [f.computer_name()]), ("hostname", "pc name"), needs_windows=True),
        Subject("pc_user", "current user", hs, _fact("who am I logged in as", lambda f: [f.user()]), ("username", "whoami"), needs_windows=True),
        Subject("pc_windows_build", "Windows build", hs, _fact("what Windows build number is this", lambda f: [f.os_build()]), ("windows version", "os version"), needs_windows=True),
        Subject("pc_cpu_threads", "CPU threads", hs, _fact("how many logical processors does this PC have", lambda f: [Num(f.cpu_logical(), 0.0)]), ("cpu cores", "processors"), needs_windows=True),
        Subject("pc_ram", "RAM size", hs, _ram, ("memory", "ram"), needs_windows=True),
        Subject("pc_disk_free", "free disk space", hs, _disk_free, ("disk space", "storage free"), needs_windows=True),
        Subject("pc_disk_total", "disk size", hs, _disk_total, ("drive size",), needs_windows=True),
        Subject("pc_timezone", "time zone", hs, _fact("what time zone is this PC set to", lambda f: [f.timezone()]), ("timezone",), needs_windows=True),
        Subject("pc_date", "today's date", hs, _fact("what is today's date", lambda f: [AnyOf(_date_options(f.today()))]), ("date today",)),
        Subject("pc_ip", "IP address", hs, _fact("what is my IP address on this network", lambda f: ([AnyOf(tuple(f.ipv4s()))] if f.ipv4s() else [])), ("ipv4", "network address"), needs_windows=True),
        Subject("pc_process_running", "is a program running", hs, _process_running, ("process running", "task running"), needs_windows=True),
        Subject("pc_service_state", "service status", hs, _service, ("service running", "windows service"), needs_windows=True),
        Subject("pc_git_version", "git version", hs, _git, ("git installed",), needs_windows=True),
        Subject("app_version", "version of an installed program", hs, _app_version, ("installed software version", "program version"), needs_windows=True),
        Subject("app_path", "where a program lives", hs, _app_path, ("find program path", "exe location"), needs_windows=True),
        Subject("svc_by_name", "service running or stopped", hs, _service_by_name, ("service status by name", "is service running"), needs_windows=True),
        Subject("sec_signature", "is a file digitally signed", hs, _file_signature, ("authenticode", "file signature"), needs_windows=True),
        Subject("log_error_count", "count errors in a log", hs, _log_error_count, ("error lines", "log errors"), needs=("file_read",)),
        Subject("log_last_error", "last error in a log", hs, _log_last_error, ("latest error", "most recent error"), needs=("log_error_count",)),
        Subject("ui_window_title", "title of a program's window", hs, _window_title, ("window title", "what window is open"), needs_windows=True),
        Subject("pc_manufacturer", "who made this PC", hs, _manufacturer, ("pc maker", "computer manufacturer"), needs_windows=True),
        Subject("pc_env_var", "read an environment variable", hs, _env_var, ("env var", "environment variable value"), needs_windows=True),
        Subject("pc_port_owner", "which process owns a port", hs, _port_owner, ("port in use", "listening port"), needs_windows=True),
        Subject("pc_process_name", "name of a process by id", hs, _process_name, ("process id", "what is running as pid"), needs_windows=True),
        # --- files (read-only, fresh sandbox each time)
        Subject("file_list", "list a folder", hs, _list_folder, ("show files", "folder contents")),
        Subject("file_count", "count files", hs, _count_files, ("how many files",), needs=("file_list",)),
        Subject("file_find_ext", "find files by type", hs, _find_ext, ("find log files", "files by extension"), needs=("file_list",)),
        Subject("file_size", "size of a file", hs, _file_size, ("file bytes", "how big file"), needs=("file_list",)),
        Subject("file_read", "read a file", hs, _read_file, ("open file", "show file text"), needs=("file_list",)),
        Subject("file_head", "first lines of a file", hs, _first_lines, ("head of file", "top lines"), needs=("file_read",)),
        Subject("file_search_text", "find text in files", hs, _search_text, ("grep", "which file contains"), needs=("file_read",)),
        Subject("file_hash", "hash a file", hs, _hash, ("sha256", "checksum"), needs=("file_read",)),
        Subject("file_newest", "most recently changed file", hs, _newest, ("latest file", "newest file"), needs=("file_list",)),
        Subject("file_largest", "biggest file", hs, _largest, ("largest file",), needs=("file_list",)),
        Subject("file_folder_size", "total size of files", hs, _folder_size, ("folder size",), needs=("file_size",)),
        Subject("file_line_count", "count lines", hs, _line_count, ("number of lines",), needs=("file_read",)),
        Subject("file_word_count", "count words", hs, _word_count, ("number of words",), needs=("file_read",)),
        Subject("file_find_deep", "find a file in subfolders", hs, _find_deep, ("search subfolders", "locate file"), needs=("file_find_ext",)),
        # --- college: jobs that change files inside the sandbox
        Subject("job_copy", "copy a file", co, _copy_backup, ("backup file", "duplicate file"), needs=("file_read",)),
        Subject("job_make_note", "create a folder and a note", co, _make_note, ("make folder", "write file"), needs=("file_read",)),
        Subject("job_move_matching", "move files by type", co, _move_matching, ("sort files", "organize logs"), needs=("job_copy",)),
        Subject("job_rename", "rename a file", co, _rename, ("change file name", "rename"), needs=("job_copy",)),
        Subject("job_zip", "zip a folder", co, _zip_folder, ("compress", "archive folder"), needs=("job_copy",)),
        Subject("job_append", "add a line to a file", co, _append_line, ("append text", "add text to file"), needs=("job_copy",)),
        Subject("job_old_files", "count files older than a date", co, _old_files, ("stale files", "old files"), needs=("file_list",)),
        Subject("job_biggest_folder", "which folder holds the biggest file", co, _biggest_folder, ("disk hog", "space usage"), needs=("file_folder_size",)),
    ] + _ui_subjects() + _git_subjects() + _office_subjects() + _web_subjects() + _dev_subjects() + _plan_subjects() + _audit_subjects() + _media_subjects() + _laptop_subjects() + _wsl_subjects() + _phone_subjects() + _game_subjects() + _graphics_subjects() + _self_subjects() + _conversation_subjects()


def _phone_subjects() -> List[Subject]:
    from .phone_subjects import subjects
    return subjects()


def _game_subjects() -> List[Subject]:
    from .game_subjects import subjects
    return subjects()


def _graphics_subjects() -> List[Subject]:
    from .graphics_subjects import subjects
    return subjects()


def _self_subjects() -> List[Subject]:
    from .self_subjects import subjects
    return subjects()


def _conversation_subjects() -> List[Subject]:
    from .conversation_subjects import subjects
    return subjects()


def _wsl_subjects() -> List[Subject]:
    from .wsl_subjects import subjects
    return subjects()


def _laptop_subjects() -> List[Subject]:
    from .laptop_subjects import subjects
    return subjects()


def _media_subjects() -> List[Subject]:
    from .media_subjects import subjects
    return subjects()


def _audit_subjects() -> List[Subject]:
    from .audit_subjects import subjects
    return subjects()


def _plan_subjects() -> List[Subject]:
    from .plan_subjects import subjects
    return subjects()


def _dev_subjects() -> List[Subject]:
    from .dev_subjects import subjects
    return subjects()


def _web_subjects() -> List[Subject]:
    from .web_subjects import subjects
    return subjects()


def _office_subjects() -> List[Subject]:
    from .office_subjects import subjects
    return subjects()


def _git_subjects() -> List[Subject]:
    from .git_subjects import subjects
    return subjects()


def _ui_subjects() -> List[Subject]:
    from .ui_subjects import subjects
    return subjects()


SUBJECTS: List[Subject] = _build()
BY_ID: Dict[str, Subject] = {s.id: s for s in SUBJECTS}


def ready(subject: Subject, mastered: Sequence[str]) -> bool:
    return all(n in mastered for n in subject.needs)
