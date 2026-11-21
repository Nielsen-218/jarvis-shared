"""ADVERSARIAL proof that package code cannot escape its assigned environment. Every test runs HOSTILE code in the real AppContainer with
`harden=False`: all Python-level rules (import allowlist, audit hook) are switched OFF, so what is tested is the operating system's enforcement, not
our Python rules. The hostile code only ever reports "denied" or "ALLOWED"; it never returns file contents. Real JARVIS files are probed read-only;
write probes target sentinel paths that must still not exist afterwards."""
import os
import socket
import threading
from pathlib import Path

import pytest

from core import skillsandbox

pytestmark = [pytest.mark.skipif(os.name != "nt", reason="the AppContainer sandbox is Windows-only"), pytest.mark.serial]   # AppContainer profile setup isn't safe across parallel workers
ROOT = Path(__file__).resolve().parent.parent

PROBE = r'''
import os, socket, subprocess, sys, ctypes
def attempt(fn):
    try:
        fn()
        return "ALLOWED"
    except BaseException as e:
        return "denied:" + type(e).__name__

def read(p):
    def f():
        with open(p, "rb") as h:
            h.read(16)
    return attempt(f)
def write(p, mode="wb"):
    def f():
        with open(p, mode) as h:
            h.write(b"pwned")
    return attempt(f)
def run(**a):
    out = {}
    for name in a["read"]:
        out["read " + name] = read(name)
    for name in a["list"]:
        out["list " + name] = attempt(lambda: os.listdir(name))
    for name in a["write"]:
        out["write " + name] = write(name)
    out["append existing"] = write(a["existing"], "ab")
    out["delete existing"] = attempt(lambda: os.remove(a["existing"]))
    out["rename existing"] = attempt(lambda: os.rename(a["existing"], a["existing"] + ".x"))
    out["mkdir"] = attempt(lambda: os.mkdir(a["mkdir"]))
    out["tcp loopback"] = attempt(lambda: socket.create_connection(("127.0.0.1", a["port"]), timeout=2))
    def udp():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"leak", ("127.0.0.1", a["udp"]))
    out["udp loopback"] = attempt(udp)
    out["tcp internet"] = attempt(lambda: socket.create_connection(("1.1.1.1", 53), timeout=2))
    out["dns"] = attempt(lambda: socket.getaddrinfo("example.com", 80))
    out["subprocess"] = attempt(lambda: subprocess.run(["cmd", "/c", "echo x > " + a["marker"]], timeout=5))
    def system():
        if os.system("echo x > " + a["marker"]) != 0:
            raise PermissionError("command did not run")
    out["os.system"] = attempt(system)
    out["startfile"] = attempt(lambda: os.startfile(a["marker"] + ".txt"))
    def openproc():
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        h = k.OpenProcess(0x0010 | 0x0400, False, a["host_pid"])
        if not h:
            raise PermissionError("OpenProcess refused")
    out["open host process"] = attempt(openproc)
    def reg():
        import winreg
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\JarvisSkillProbe")
    out["registry write"] = attempt(reg)
    out["environ keys"] = sorted(os.environ)
    adv = ctypes.windll.advapi32
    kern = ctypes.windll.kernel32
    kern.GetCurrentProcess.restype = ctypes.c_void_p
    adv.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
    h = ctypes.c_void_p()
    adv.OpenProcessToken(kern.GetCurrentProcess(), 8, ctypes.byref(h))
    v = ctypes.c_ulong()
    n = ctypes.c_ulong()
    adv.GetTokenInformation(h, 29, ctypes.byref(v), 4, ctypes.byref(n))
    out["is_appcontainer"] = int(v.value)
    return out
'''


@pytest.fixture(scope="module")
def hostile(tmp_path_factory):
    work = tmp_path_factory.mktemp("victim")
    existing = work / "important.txt"
    existing.write_bytes(b"precious")
    secrets = [p for p in (ROOT / ".env", ROOT / "data" / "users.json", ROOT / "data" / "settings.json", Path.home() / ".ssh" / "id_rsa",
                           Path.home() / ".claude" / "settings.json", Path.home() / ".claude.json") if p.exists()]
    owner_files = list((ROOT / "data" / "users" / "owner").glob("*.json"))[:3]
    assert secrets or owner_files, "expected at least one real private JARVIS file to probe"
    lab = skillsandbox.lab_root()
    sentinels = [work / "new.txt", ROOT / "jskill_probe_do_not_exist.txt", ROOT / "data" / "jskill_probe_do_not_exist.txt",
                 Path(os.environ["TEMP"]) / "jskill_probe_do_not_exist.txt", Path.home() / "jskill_probe_do_not_exist.txt",
                 lab / "jskill_probe_do_not_exist.txt", lab / "runtime" / "jskill_probe_do_not_exist.txt"]
    tcp = socket.socket()
    tcp.bind(("127.0.0.1", 0))
    tcp.listen(5)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.3)
    tcp.settimeout(0.3)
    got = {"tcp": 0, "udp": 0}
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                c, _ = tcp.accept()
                got["tcp"] += 1
                c.close()
            except OSError:
                pass
            try:
                udp.recvfrom(64)
                got["udp"] += 1
            except OSError:
                pass
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    # CONTROL: the same listener must register a normal (unsandboxed) client, otherwise "nothing arrived" would prove nothing
    socket.create_connection(("127.0.0.1", tcp.getsockname()[1]), timeout=2).close()
    cs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cs.sendto(b"control", ("127.0.0.1", udp.getsockname()[1]))
    for _ in range(40):
        if got["tcp"] >= 1 and got["udp"] >= 1:
            break
        threading.Event().wait(0.1)
    control = dict(got)
    got["tcp"] = got["udp"] = 0
    marker = work / "proc_marker.txt"
    lists = [str(p) for p in (ROOT, ROOT / "data", Path.home(), Path.home() / ".claude", ROOT / "data" / "users", lab.parent) if p.exists()]
    params = {"read": [str(p) for p in secrets + owner_files], "list": lists, "write": [str(s) for s in sentinels], "existing": str(existing),
              "mkdir": str(work / "newdir"), "port": tcp.getsockname()[1], "udp": udp.getsockname()[1], "marker": str(marker), "host_pid": os.getpid()}
    res = skillsandbox.run(PROBE, "run", params, harden=False, timeout=60)
    stop.set()
    t.join(2)
    yield {"control": control, "res": res, "out": res.get("output", {}), "got": got, "existing": existing, "marker": marker, "sentinels": sentinels, "work": work,
           "reads": params["read"]}
    for s in sentinels:
        if s.exists() and s.name.startswith("jskill_probe"):
            s.unlink()


def test_probe_ran_inside_a_real_appcontainer(hostile):
    r = hostile["res"]
    assert r["ok"], r
    assert r["boundary"] == {"backend": "Windows AppContainer", "app_container": True, "capability_count": 0, "package_identity_verified": True,
                             "job_assigned_before_resume": True}
    assert hostile["out"]["is_appcontainer"] == 1


def test_private_jarvis_files_cannot_be_read(hostile):
    out = hostile["out"]
    assert hostile["reads"]
    for p in hostile["reads"]:
        assert out["read " + p].startswith("denied"), (p, out["read " + p])


def test_directories_holding_jarvis_data_cannot_even_be_listed(hostile):
    listed = [k for k in hostile["out"] if k.startswith("list ")]
    assert listed
    for k in listed:
        assert hostile["out"][k].startswith("denied"), (k, hostile["out"][k])


def test_nothing_can_be_written_created_deleted_or_renamed(hostile):
    out = hostile["out"]
    for k in [k for k in out if k.startswith("write ")] + ["append existing", "delete existing", "rename existing", "mkdir"]:
        assert out[k].startswith("denied"), (k, out[k])
    assert hostile["existing"].read_bytes() == b"precious" and not Path(str(hostile["existing"]) + ".x").exists()
    assert not (hostile["work"] / "newdir").exists()
    assert [s for s in hostile["sentinels"] if s.exists()] == []


def test_no_network_of_any_kind(hostile):
    out = hostile["out"]
    assert hostile["control"] == {"tcp": 1, "udp": 1}      # the listener works: an ordinary client is seen
    for k in ("tcp loopback", "tcp internet", "dns"):
        assert out[k].startswith("denied"), (k, out[k])
    # UDP sendto() does not raise inside the sandbox (the OS drops the datagram silently), so the proof is the HOST-side receiver: nothing arrived
    threading.Event().wait(1.0)
    assert hostile["got"] == {"tcp": 0, "udp": 0}


def test_no_process_can_be_launched(hostile):
    out = hostile["out"]
    for k in ("subprocess", "os.system", "startfile"):
        assert out[k].startswith("denied"), (k, out[k])
    assert not hostile["marker"].exists()                  # the command it tried to run never ran


def test_host_process_and_registry_are_out_of_reach(hostile):
    import winreg
    assert hostile["out"]["open host process"].startswith("denied")
    assert hostile["out"]["registry write"].startswith("denied")
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\JarvisSkillProbe")


def test_environment_carries_no_host_secrets(hostile):
    keys = {k.upper() for k in hostile["out"]["environ keys"]}
    assert keys <= {"LOCALAPPDATA", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}, keys      # TEMP/TMP are added by Windows for the package, pointing at its private folder


def test_memory_bomb_is_killed_not_survived():
    r = skillsandbox.run("def run():\n    x = bytearray(2_000_000_000)\n    return {'n': len(x)}\n", "run", {}, harden=False, timeout=20)
    assert not r["ok"]


def test_infinite_loop_is_stopped():
    r = skillsandbox.run("def run():\n    while True:\n        pass\n", "run", {}, harden=False, timeout=2)
    assert not r["ok"] and r["error"] in ("timeout", "sandbox_failed")


def test_output_flood_is_cut_off():
    r = skillsandbox.run("def run():\n    return {'x': 'A' * 5_000_000}\n", "run", {}, harden=False, timeout=15)
    assert not r["ok"]


def test_fork_bomb_cannot_start_children():
    src = "import subprocess\ndef run():\n    return {'pids': [subprocess.Popen(['cmd','/c','ping -n 5 127.0.0.1']).pid for _ in range(3)]}\n"
    r = skillsandbox.run(src, "run", {}, harden=False, timeout=15)
    assert not r["ok"]


def test_hardened_layer_also_blocks_the_same_attack_independently():
    src = "def run():\n    return {'x': open(r'" + str(ROOT / "data" / "users.json") + "').read()}\n"
    r = skillsandbox.run(src, "run", {}, harden=True)
    assert not r["ok"] and r["kind"] == "PermissionError"


def test_sandbox_fails_closed_when_the_lab_cannot_be_used(monkeypatch, tmp_path):
    from core import project_sandbox as sb
    monkeypatch.setenv("JARVIS_SKILLS_LAB", str(tmp_path / "lab-that-fails"))
    monkeypatch.setattr(skillsandbox, "_ready", {})
    monkeypatch.setattr(sb, "prepare_runtime", lambda *a, **k: (_ for _ in ()).throw(sb.SandboxUnavailable("simulated")))
    with pytest.raises(skillsandbox.Unavailable):
        skillsandbox.run("def run():\n    return {}\n", "run", {})
