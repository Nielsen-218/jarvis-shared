"""Free local vision: describe / answer about images with a local VLM in Ollama.

Keeps "Eyes", fridge/food recognition and image questions on the free local model instead
of a paid cloud call. Callers fall back to paid vision only when this is unavailable.
Nothing is written to disk; images are sent to the loopback model and discarded.
"""
import json
import os
import re
import urllib.request

MODEL = os.getenv("JARVIS_VISION_MODEL", "qwen3.5:9b")  # fallback (the one brain sees); the best installed model is preferred


def best_model() -> str:
    """The strongest installed local vision model (Qwen3-VL reads labels far better than llava).
    An explicit JARVIS_VISION_MODEL still wins."""
    if os.getenv("JARVIS_VISION_MODEL"):
        return MODEL
    try:
        from core import engeyes
        return engeyes.model() or MODEL
    except Exception:
        return MODEL


def _base() -> str:
    """Ollama's native API base (strip the OpenAI-compat /v1 suffix from JARVIS_LOCAL_URL)."""
    from core import offline
    return re.sub(r"/v1/?$", "", offline.URL)


def available(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(_base() + "/api/tags", timeout=timeout) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return False
    model = best_model()
    want = model.split(":")[0]
    return any(n == model or n.split(":")[0] == want for n in names)


def _b64(image: str) -> str:
    return image.split(";base64,", 1)[1] if image.startswith("data:") and ";base64," in image else image


def describe(images: list, question: str = "", system: str = "", timeout: float = 120.0) -> str:
    """Answer about one or more images on the local VLM. Raises if it can't be reached."""
    imgs = [_b64(i) for i in images if i][:8]
    if not imgs:
        raise ValueError("no images")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "images": imgs,
                     "content": question.strip() or "Describe what you see. Be concise and specific."})
    model = best_model()
    from core import warmth
    # The brain's own num_ctx (another size reloads it), its keep-alive, and thinking off (qwen3.5 thinks by default).
    body = {"model": model, "messages": messages, "stream": False, **warmth.ollama_fields(model, "20m")}
    body["options"]["temperature"] = 0.2
    req = urllib.request.Request(_base() + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())["message"]["content"].strip()
