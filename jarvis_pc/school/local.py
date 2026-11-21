"""Talk to the local Ollama server the rest of JARVIS already uses (core.intelligence's loopback-only endpoint).

Small and dependency-free on purpose: the school only needs "give me text from model X". CPU by default so studying never takes
the GPU from a game or the main assistant; set JARVIS_SCHOOL_GPU=1 to allow the GPU. Reasoning ("<think>") text is stripped.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import List, Optional


def _base() -> str:
    from core import intelligence
    url, _opener = intelligence._ollama_endpoint()  # validated: http, loopback only
    return url[: -len("/v1")] if url.endswith("/v1") else url


def installed() -> List[str]:
    with urllib.request.urlopen(_base() + "/api/tags", timeout=5) as r:
        return [m["name"] for m in json.loads(r.read().decode()).get("models", [])]


def ask(prompt: str, model_name: Optional[str] = None, *, num_predict: int = 400, timeout: float = 240.0) -> str:
    opts = {"temperature": 0.0, "num_ctx": 4096, "num_predict": num_predict, "seed": 7}
    if os.getenv("JARVIS_SCHOOL_GPU") != "1":
        opts["num_gpu"] = 0
    from core import learn_llm
    body = {"model": model_name, "messages": [{"role": "user", "content": prompt}], "stream": False,
            **learn_llm._fields(model_name, opts)}       # the brain as loaded (no reload), thinking off
    req = urllib.request.Request(_base() + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = json.loads(r.read().decode())["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
