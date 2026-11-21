"""End-to-end proof of the Skill Installer in an ISOLATED environment (temporary skills folder and data folder; production data and the running
JARVIS are never touched, nothing is deployed). Real sandbox, real Brain.ask, real separate processes for each "restart"; no model, no network.

    .venv\\Scripts\\python.exe scripts\\skill_demo.py

Stages: external package -> review -> (refused without approval) -> approved install -> process restart #1 chat -> process restart #2 chat ->
update + rollback -> tampered package refused. Exit code 1 if any check failed."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
work = Path(tempfile.mkdtemp(prefix="jskill-demo-"))
env = {**os.environ, "JARVIS_SKILLS_DIR": str(work / "skills"), "JARVIS_LOCAL_ONLY": "1", "JARVIS_DEMO_DATA": str(work / "data"),
       "JARVIS_SKILLS_LAB": str(Path(tempfile.gettempdir()) / "jarvis-skill-test-lab")}      # one reusable AppContainer lab: no new Windows profile per run
fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def cli(*a):
    p = subprocess.run([PY, "manage.py", "skill", *a], cwd=ROOT, capture_output=True, text=True, env=env, timeout=600)
    return p.returncode, p.stdout.strip()


CHAT = r'''
import os, sys, json, socket
sys.path.insert(0, os.getcwd())
from pathlib import Path
from core import storage
storage.DATA = Path(os.environ["JARVIS_DEMO_DATA"]); storage.DATA.mkdir(parents=True, exist_ok=True)
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
attempts = []
_c = socket.socket.connect
def spy(self, *a, **k):
    attempts.append(str(a)); raise OSError("network is closed in this demo")
socket.socket.connect = spy
from brain.llm import Brain
from core import users, craftjobs
from tools import load_all, registry
load_all(); users.set_current("owner")
ran = []
_run = registry.run
registry.run = lambda n, a: (ran.append(n), _run(n, a))[1]
b = Brain("demo", "low", "system")
out = {}; per = {}
for key, phrase in json.loads(os.environ["PHRASES"]).items():
    said = []; n0 = len(attempts)
    b.ask(phrase, said.append)
    out[key] = " ".join(said); per[key] = len(attempts) - n0
print("@@" + json.dumps({"said": out, "tools": ran, "net": attempts, "per": per, "job": craftjobs.get() is not None}))
'''


def chat(phrases):
    p = subprocess.run([PY, "-c", CHAT], cwd=ROOT, capture_output=True, text=True, env={**env, "PHRASES": json.dumps(phrases)}, timeout=600)
    if "@@" not in p.stdout:
        print(p.stdout[-800:], p.stderr[-1500:])
        return {"said": {}, "tools": [], "net": ["crashed"], "job": False}
    return json.loads(p.stdout.split("@@", 1)[1])


print("work folder:", work)
# 1. an external author's package (a separate folder, packed with the author tool) -------------------------------------------------------------
author = work / "author" / "util.split_bill"
shutil.copytree(ROOT / "skillkit" / "examples" / "util.split_bill", author)
pkg = work / "util.split_bill-1.1.0.jskill"
rc, _ = cli("pack", str(author), str(pkg))
check("package built by the author tool", rc == 0 and pkg.exists())

rc, out = cli("review", str(pkg))
fp = next(l.split(":")[1].strip() for l in out.splitlines() if "fingerprint:" in l)
check("review shows scope, permissions and fingerprint without running anything", rc == 0 and "permissions requested: compute" in out and len(fp) == 64, out)
rc, out = cli("install", str(pkg))
check("install without approval installs nothing", rc == 1 and "Nothing has been run or installed" in out and "util.split_bill" not in cli("list")[1])
rc, out = cli("install", str(pkg), "--approve", fp[:24])
check("approved install passes the host's independent verification", rc == 0 and "independent checks passed" in out, out)
print("   ", out)

# 2. restart #1 and #2: fresh interpreters, real Brain.ask ------------------------------------------------------------------------------------------
PH = {"use": "jarvis, split a $87.50 dinner bill 3 ways with 18% tip", "ask": "split the bill for $60", "zero": "split a $0 bill 3 ways", "neg_screen": "split the screen into two windows", "neg_pdf": "split this pdf into pages",
      "dragon": "[dry-run] rig my dragon", "dry_skill": "[dry-run] split the $90 bill among 3 people",
      "existing": "what have you learned lately"}
for n in (1, 2):
    r = chat(PH)
    s, t = r["said"], r["tools"]
    check(f"restart {n}: installed skill answers from plain chat", "$103.25" in s["use"] and "Checked 5 independent rules" in s["use"], s.get("use"))
    check(f"restart {n}: missing essential is asked, not invented", s["ask"] == "How many people are splitting it?", s.get("ask"))
    check(f"restart {n}: named error refused plainly", "nothing to split" in s["zero"], s.get("zero"))
    check(f"restart {n}: unsupported requests do not activate the skill", t.count("run_installed_skill") == 3 and "split" not in " ".join(t).replace("run_installed_skill", ""))
    check(f"restart {n}: dry-run of the dragon phrase executes nothing", "DRY RUN" in s["dragon"] and "rig_my_character" in s["dragon"] and not r["job"] and "rig_my_character" not in t)
    check(f"restart {n}: dry-run of the skill executes nothing", "DRY RUN" in s["dry_skill"] and "util.split_bill" in s["dry_skill"])
    check(f"restart {n}: existing skills still answer", "what_have_you_learned" in t)
    skill_turns = ("use", "ask", "zero", "dragon", "dry_skill", "existing")
    check(f"restart {n}: skill, dry-run and built-in turns made no network connection", all(r["per"][k] == 0 for k in skill_turns), r["per"])
    check(f"restart {n}: the only connections anywhere were to the local model (127.0.0.1:11434), never the cloud", set(r["net"]) <= {"(('127.0.0.1', 11434),)"}, set(r["net"]))
    print("    said:", s["use"])

# 3. update, rollback, tamper ---------------------------------------------------------------------------------------------------------------------
v2 = work / "author2"
shutil.copytree(author, v2)
m = json.loads((v2 / "skill.json").read_text(encoding="utf-8"))
m["version"] = "1.2.0"
m["reply"] = "V2 answer: ${total_with_tip} for {people_text}: {share_text}."
(v2 / "skill.json").write_text(json.dumps(m), encoding="utf-8")
pkg2 = work / "util.split_bill-1.2.0.jskill"
cli("pack", str(v2), str(pkg2))
fp2 = next(l.split(":")[1].strip() for l in cli("review", str(pkg2))[1].splitlines() if "fingerprint:" in l)
check("update 1.2.0 installs", cli("install", str(pkg2), "--approve", fp2[:24])[0] == 0)
check("update is live in a fresh process", chat({"u": "split the $50 bill among 2 people"})["said"]["u"].startswith("V2 answer"))
check("rollback restores 1.1.0", cli("rollback", "util.split_bill")[0] == 0 and chat({"u": "split the $50 bill among 2 people"})["said"]["u"].startswith("Split $50.00"))

bad = work / "tampered.jskill"
import zipfile
with zipfile.ZipFile(pkg2) as zi, zipfile.ZipFile(bad, "w") as zo:
    for i in zi.infolist():
        d = zi.read(i)
        zo.writestr(i.filename, d + b"\n# changed in transit\n" if i.filename == "procedure.py" else d)
rc, out = cli("install", str(bad), "--approve", fp2[:24])
check("tampered package refused", rc == 1 and "hash does not match" in out, out)
check("library unchanged by the refused install", "1.1.0" in cli("list")[1])
# 4. the installed copy is re-verified against the approved package: edit it and the skill stops being used
src = work / "skills" / "library" / "util.split_bill" / "v1" / "source.py"
src.write_text(src.read_text(encoding="utf-8") + "\n# edited after approval\n", encoding="utf-8")
check("a skill edited after approval is refused, not run", "$103.25" not in chat({"u": "split a $87.50 dinner bill 3 ways with 18% tip"})["said"]["u"])
events = [json.loads(l)["event"] for l in (work / "skills" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
check("audit log recorded the lifecycle", {"review", "approved", "install", "update", "rollback", "run"} <= set(events), events)

print(f"\n{'ALL CHECKS PASSED' if not fails else 'FAILED: ' + ', '.join(fails)}  (work folder kept: {work})")
sys.exit(1 if fails else 0)
