"""Local self-repair candidate evaluation, scoped to voice/learning code.

User-authorized coding is possible without a paid model. A local LM Studio
coder proposes ONE literal source replacement, tested in a NEW git worktree.
No shell commands from models, no arbitrary paths, no hidden main mutations,
no skipping tests, and no model claim that a test passed.
"""
from __future__ import annotations
import json
import re
import urllib.request
from urllib.parse import urlsplit

ALLOWED={
 "voice":{"core/local_stream.py","core/offline.py","core/speech.py"},
 "learning":{"core/autolearn.py","tools/learning.py"},
}
TESTS=("tests/test_local_stream.py","tests/test_autolearn.py")
def validate_patch(raw:dict,source:str,category:str)->tuple[str,str]:
    if category not in ALLOWED:raise ValueError("Unsupported code improvement category")
    if not isinstance(raw,dict):raise ValueError("Patch must be structured JSON")
    if set(raw)-{"path","old","new","rationale"}:
        raise ValueError("Patch includes unexpected fields")
    path=raw.get("path")
    if path not in ALLOWED[category]:raise ValueError("Unapproved source path")
    before,after=raw.get("old"),raw.get("new")
    if (not isinstance(before,str) or not isinstance(after,str) or
        not 20<=len(before)<=2500 or not 1<=len(after)<=3500 or
        before==after or source.count(before)!=1):
        raise ValueError("Patch must exactly replace one existing source region")
    return path,source.replace(before,after,1)

def endpoint(url:str)->str:
    parsed=urlsplit(url)
    if (parsed.scheme!="http" or parsed.hostname not in ("localhost","127.0.0.1")
        or parsed.path.rstrip("/")!="/v1" or parsed.username or parsed.password
        or parsed.query or parsed.fragment):
        raise ValueError("Only a loopback local LM Studio endpoint is allowed")
    return url.rstrip("/")

def propose(*,task:str,category:str,path:str,source:str,url:str,
            model:str,timeout:int=180)->dict:
    if category not in ALLOWED or path not in ALLOWED[category]:
        raise ValueError("File is outside a controlled improvement area")
    if not 12<=len(task)<=900:raise ValueError("Specific improvement goal required")
    if len(source)>65000:raise ValueError("Source too long for a one-shot change")
    if not isinstance(model,str) or not 3<=len(model)<=120:
        raise ValueError("An explicitly installed local coder model is required")
    payload={
        "model":model,"stream":False,"temperature":0.1,"max_tokens":3000,
        "messages":[{"role":"system","content":
            "You are a local code maintenance assistant. Reply ONLY strict JSON "
            "with keys path,old,new,rationale. Make one small textual replacement "
            "in the given Python source. Never ask to execute commands or touch tests. "
            "Do not alter security, API routing or data paths. If unsolvable, "
            "reply {}."},
            {"role":"user","content":
               "File: "+path+"\nGoal: "+task+"\nCurrent source:\n"+source[:65000]}]
    }
    request=urllib.request.Request(endpoint(url)+"/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        result=json.loads(response.read(600_000))
    answer=result["choices"][0]["message"]["content"]
    if not isinstance(answer,str):raise ValueError("Non-text patch")
    patch=json.loads(answer.strip())
    if patch.get("path")!=path:raise ValueError("Local model changed target file")
    validate_patch(patch,source,category)
    return patch

def patch_filename(name:str)->str:
    if name not in set.union(*(set(v) for v in ALLOWED.values())):
        raise ValueError("Patch file is outside supported local source modules")
    return name
