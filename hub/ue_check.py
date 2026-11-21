"""Host-side Unreal import gate: `python hub/ue_check.py a.fbx[:expected_verts] b.fbx ...` -> JSON verdict.
Needs UE 5.x (JARVIS_UE_ROOT or the default Epic path). Headless, no window. ~60-90 s per call."""
import json, os, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent


def ue_cmd():
    root = Path(os.getenv("JARVIS_UE_ROOT") or r"C:\Program Files\Epic Games\UE_5.8")
    exe = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    return exe if exe.exists() else None


def project_dir():
    """A tiny throwaway project so we never import into the owner's game."""
    d = Path(os.getenv("JARVIS_UE_GATE_PROJECT") or (Path(tempfile.gettempdir()) / "jarvis_ue_gate"))
    d.mkdir(parents=True, exist_ok=True)
    up = d / "Gate.uproject"
    if not up.exists():
        up.write_text(json.dumps({"FileVersion": 3, "EngineAssociation": "5.8", "Category": "", "Description": "",
                                  "Plugins": [{"Name": n, "Enabled": True} for n in ("PythonScriptPlugin", "EditorScriptingUtilities", "GeometryScripting")]}))
    return up


def run(cases, timeout=600, runner=None):
    exe = ue_cmd()
    if not exe:
        return {"ok": False, "error": "Unreal Engine not found (set JARVIS_UE_ROOT)"}
    tmp = Path(tempfile.mkdtemp(prefix="uegate_"))
    cfg, res = tmp / "cfg.json", tmp / "out.json"
    cfg.write_text(json.dumps({"cases": cases}))
    env = dict(os.environ, RIG_CFG=str(cfg), RIG_OUT=str(res))
    cmd = [str(exe), str(project_dir()), "-run=pythonscript", "-script=" + str(HERE / "ue_gate_inside.py"),
           "-unattended", "-nosplash", "-nullrhi", "-stdout", "-NoSound"]
    try:
        p = (runner or subprocess.run)(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Unreal import timed out"}
    if not res.exists():
        return {"ok": False, "error": "Unreal produced no result", "tail": (p.stdout or "")[-600:]}
    d = json.loads(res.read_text())
    d["ok"] = all(c.get("ok") for c in d["cases"])
    return d


if __name__ == "__main__":
    cases = []
    for a in sys.argv[1:]:
        f, _, n = a.partition("@")
        cases.append({"name": Path(f).parent.name.replace("-", "_")[:40], "fbx": str(Path(f).resolve()), "expected_verts": int(n) if n else None})
    print(json.dumps(run(cases), indent=2))
