"""The learning core's local-model helper: free, local, deterministic (temperature 0), cached on disk so a repeat run of the same
prompt costs nothing and gives the same answer. CPU by default (JARVIS_LEARN_GPU=1 to allow the GPU): learning experiments must not
fight a game or the rig jobs for VRAM."""
import hashlib
import json
import os
import urllib.request

DEFAULT_MODEL = "qwen3.5:9b"  # 2026-11-09: the one brain (the old default was deleted)


def model():
    return os.getenv("JARVIS_LEARN_MODEL") or DEFAULT_MODEL


def _fields(name, opts):
    """Request fields. The brain itself runs as loaded (its GPU, its num_ctx, its keep-alive): a CPU copy at 4096 would
    evict and reload it. Any other model keeps the CPU/4096 options. Thinking off for thinking models (qwen3.5)."""
    from core import warmth
    if name in warmth.jarvis_models():
        fields = warmth.ollama_fields(name, "2m")
        fields["options"].update({k: v for k, v in opts.items() if k not in ("num_ctx", "num_gpu")})
        return fields
    fields = {k: v for k, v in warmth.ollama_fields(name, "2m").items() if k == "think"}
    return {**fields, "keep_alive": "2m", "options": dict(opts)}


def _base():
    from core import engeyes
    return engeyes._ollama_base()


def ask(prompt, schema, store=None, temperature=0.0, timeout=300, num_predict=500, model_name=None):
    """-> parsed JSON answer. Raises RuntimeError when the local model cannot answer."""
    opts = {"temperature": temperature, "num_ctx": 4096, "num_predict": num_predict, "seed": 7}
    if os.getenv("JARVIS_LEARN_GPU") != "1":
        opts["num_gpu"] = 0
    key = hashlib.sha256(json.dumps([model_name or model(), prompt, opts, schema], sort_keys=True).encode()).hexdigest()[:20]
    cache = (store.root / "llm_cache") if store is not None else None
    if cache is not None:
        cache.mkdir(exist_ok=True)
        f = cache / (key + ".json")
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    last = None
    for attempt in range(2):                                   # a second try with another seed when the JSON is malformed
        name = model_name or model()
        body = {"model": name, "messages": [{"role": "user", "content": prompt}], "stream": False, "format": schema,
                **_fields(name, {**opts, "seed": opts["seed"] + attempt})}
        req = urllib.request.Request(_base() + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                content = json.loads(r.read().decode())["message"]["content"]
            out = json.loads(content[content.find("{"): content.rfind("}") + 1])
            break
        except Exception as e:
            last = e
    else:
        raise RuntimeError(f"local model failed: {type(last).__name__}")
    if cache is not None:
        f.write_text(json.dumps(out), encoding="utf-8")
    return out


def ask_text(prompt, store=None, temperature=0.0, timeout=600, num_predict=900, model_name=None):
    """-> the model's raw text (for code that does not survive a JSON string). Cached like `ask`."""
    opts = {"temperature": temperature, "num_ctx": 4096, "num_predict": num_predict, "seed": 7}
    if os.getenv("JARVIS_LEARN_GPU") != "1":
        opts["num_gpu"] = 0
    key = hashlib.sha256(json.dumps([model_name or model(), prompt, opts, "text"], sort_keys=True).encode()).hexdigest()[:20]
    cache = (store.root / "llm_cache") if store is not None else None
    if cache is not None:
        cache.mkdir(exist_ok=True)
        f = cache / (key + ".txt")
        if f.is_file():
            return f.read_text(encoding="utf-8")
    name = model_name or model()
    body = {"model": name, "messages": [{"role": "user", "content": prompt}], "stream": False, **_fields(name, opts)}
    req = urllib.request.Request(_base() + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode())["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"local model failed: {type(e).__name__}")
    if cache is not None:
        f.write_text(out, encoding="utf-8")
    return out


def code_block(text):
    """The first fenced code block of an answer (or the whole text when there is no fence)."""
    fence = chr(96) * 3
    text = text or ""
    if text.lstrip().startswith("{"):                     # some small models answer in JSON whatever the prompt says
        try:
            got = json.loads(text[text.find("{"): text.rfind("}") + 1]).get("code")
            if isinstance(got, str):
                return got.strip() + chr(10)
        except ValueError:
            mark = text.find('"code"')                      # truncated or sloppy JSON: take the string after "code":
            q = text.find('"', text.find(":", mark) + 1) if mark >= 0 else -1
            if q >= 0:
                inner = text[q + 1:].rstrip()
                inner = inner[:-2] if inner.endswith('"}') else inner
                inner = inner.replace(chr(92) + "'", "'")
                try:
                    return json.loads('"' + inner.rstrip('"').rstrip() + '"', strict=False).strip() + chr(10)
                except ValueError:
                    pass
    a = text.find(fence)
    if a < 0:
        return text.strip() + chr(10)
    a = text.find(chr(10), a) + 1                      # skip the language tag line
    z = text.find(fence, a)
    return text[a: z if z > 0 else len(text)].strip() + chr(10)
