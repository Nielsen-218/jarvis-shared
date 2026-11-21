"""Jarvis local self-improvement: isolated single-file proposal, fixed tests, git branch.

Run only on the user's computer after exiting games. Does not consume paid API
credits or modify running source until explicitly given --merge.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.autoimprove import ALLOWED,TESTS,patch_filename,propose,validate_patch

def git(*args:str,cwd:Path=ROOT,timeout:int=40)->str:
    result=subprocess.run(["git",*args],cwd=cwd,capture_output=True,
                          text=True,timeout=timeout,check=True)
    return result.stdout.strip()

def run(task:str,category:str,target:str,model:str,url:str,merge:bool=False,
        proposal:dict|None=None)->dict:
    if category not in ALLOWED or target not in ALLOWED[category]:
        raise ValueError("Unsupported maintenance target")
    if Path(git("rev-parse","--show-toplevel")).resolve()!=ROOT.resolve():
        raise ValueError("Run in the original Jarvis repository")
    if git("status","--porcelain","--untracked-files=no"):
        raise ValueError("Working tree has changes; preserving all other agent edits")
    if merge and git("branch","--show-current")!="main":
        raise ValueError("Merging is supported only when current branch is main")
    try:
        from scripts.nolagin import _game_running
        if any(_game_running(game) for game in ("apex","cod")):
            raise RuntimeError("Exit the game before generating code and running tests")
    except ImportError:
        if sys.platform=="win32":raise RuntimeError("Cannot verify game mode")
    original=(ROOT/target).read_text(encoding="utf-8")
    patch=proposal if proposal is not None else propose(
        task=task,category=category,path=target,source=original,model=model,url=url)
    patched_path,changed=validate_patch(patch,original,category)
    if patched_path!=target:raise ValueError("Proposal does not match selected target")
    base=git("rev-parse","HEAD")
    branch="jarvis/local-improvement-"+uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix="jarvis-improve-") as temp:
        target_worktree=(Path(temp)/"work").resolve()
        if not target_worktree.is_relative_to(Path(temp).resolve()):
            raise ValueError("Temporary worktree must stay inside its own directory")
        git("worktree","add","-b",branch,str(target_worktree),base)
        succeeded=False
        try:
            file=target_worktree/target
            if file.read_text(encoding="utf-8")!=original:
                raise ValueError("Branch source differs; safe rewrite refused")
            file.write_text(changed,encoding="utf-8")
            subprocess.run([sys.executable,"-m","py_compile",str(file)],
                           cwd=target_worktree,timeout=40,check=True)
            subprocess.run([sys.executable,"-m","pytest","-q","--noconftest",
                            "--basetemp",str(target_worktree/"data"/"self-upgrade-tests"),*TESTS],
                           cwd=target_worktree,timeout=180,check=True)
            git("add","--",target,cwd=target_worktree)
            git("commit","-m","fix(jarvis): locally tested improvement to "+target,
                cwd=target_worktree)
            committed=git("rev-parse","HEAD",cwd=target_worktree)
            succeeded=True
        finally:
            git("worktree","remove","--force",str(target_worktree),timeout=50)
            if not succeeded:
                git("branch","-D",branch)
    if merge:
        if git("rev-parse","HEAD")!=base or git("status","--porcelain","--untracked-files=no"):
            return {"status":"tested_branch_only","branch":branch,"commit":committed,
                    "reason":"main changed during tests; don't overwrite agent work"}
        git("merge","--ff-only",branch,timeout=60)
        return {"status":"locally_merged_tested","branch":branch,"commit":committed,
                "test_scope":list(TESTS),
                "note":"This is a tested source edit, NOT measured speech speed or automatically proven correctness"}
    return {"status":"tested_branch_only","branch":branch,"commit":committed,
            "test_scope":list(TESTS),
            "next":"Review the branch or rerun with --merge on clean latest main"}

def main()->int:
    p=argparse.ArgumentParser(description="Local-code-model offline Jarvis self-upgrade")
    p.add_argument("--task",required=True,help="Specific reproducible bug/improvement")
    p.add_argument("--category",required=True,choices=sorted(ALLOWED))
    p.add_argument("--file",required=True,choices=sorted(set.union(
        *(set(x) for x in ALLOWED.values()))))
    p.add_argument("--model",required=True,help="Exact locally installed LM Studio coding model ID")
    p.add_argument("--url",default="http://127.0.0.1:1234/v1")
    p.add_argument("--merge",action="store_true",
                   help="Fast-forward tested candidate if main is still unchanged")
    p.add_argument("--proposal",type=Path,help="Previously inspected local-model JSON proposal")
    args=p.parse_args()
    try:
        out=run(args.task,args.category,args.file,args.model,args.url,args.merge,
                json.loads(args.proposal.read_text(encoding="utf-8")) if args.proposal else None)
    except (OSError,ValueError,RuntimeError,KeyError,subprocess.CalledProcessError,
            subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        print(json.dumps({"status":"blocked","reason":type(exc).__name__,
                          "message":str(exc)[:220]},indent=2))
        return 2
    print(json.dumps(out,indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
