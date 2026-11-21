"""Local image generation/editing on the GPU with FLUX.2 [klein] 4B (Apache 2.0).

The model (~15 GB on disk) is loaded on demand and unloaded after a few idle minutes.

How it uses the graphics card (measured 2026-11-12 on the RTX 5070 Ti, one try-on at ~1 megapixel, 8 steps):
the old "whole-model offload" kept the text encoder (8 GB), the VAE's activations and the transformer (7.5 GB)
fighting over the same VRAM; with the card nearly full Windows spilled into shared memory and a single step
took minutes. Staged use needs only the transformer on the card:

  1. text encoder -> GPU, encode the prompt, text encoder -> CPU, free the cache      (~6 s)
  2. transformer + VAE -> GPU (tiled VAE), run the steps with the encoded prompt     (~2.4 s per step at 1 MP)
  3. park the transformer and VAE back on the CPU                                   (the card is free again)

Peak VRAM is about 9-10 GB. The pipeline stays in RAM between jobs (warm) and is dropped when idle.
JARVIS_FLUX_LOWVRAM=1 keeps the old layer-by-layer mode for a card that cannot give 10 GB (very slow).
"""
import os
import threading
import time

from PIL import Image

MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"
IDLE_UNLOAD_SECONDS = 600
NEED_FREE_MIB = 10500          # what the staged run needs free on the card (transformer + activations)

_pipe = None
_low = False
_lock = threading.Lock()
_last_used = 0.0
on_progress = None             # optional callable(str): "what is happening now" for a progress bar


def _say(text: str) -> None:
    print(f"[imaging] {text}", flush=True)
    cb = on_progress
    if cb:
        try:
            cb(text)
        except Exception:  # noqa: BLE001  (progress must never break a render)
            pass


def _load(low_vram: bool | None = None):
    """Load the pipeline (on the CPU unless low-VRAM mode). low_vram (or JARVIS_FLUX_LOWVRAM=1): stream layers
    to the GPU one at a time (~3 GB VRAM, very slow); otherwise the staged mode described above."""
    global _pipe, _low
    if _pipe is None:
        import torch
        from diffusers import Flux2KleinPipeline

        low = os.getenv("JARVIS_FLUX_LOWVRAM") == "1" if low_vram is None else low_vram
        _say("Loading the image model" + (" (low-VRAM mode, slow)" if low else ""))
        t = time.time()
        _pipe = Flux2KleinPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16)
        _low = bool(low)
        if low:
            _pipe.enable_sequential_cpu_offload()
        else:
            _pipe.vae.enable_tiling()
        print(f"[imaging] model ready in {time.time() - t:.0f}s")
        threading.Thread(target=_unload_when_idle, daemon=True).start()
    return _pipe


def _unload_when_idle() -> None:
    global _pipe
    while True:
        time.sleep(60)
        with _lock:
            if _pipe is None:
                return
            if time.time() - _last_used > IDLE_UNLOAD_SECONDS:
                import torch

                _pipe = None
                torch.cuda.empty_cache()
                print("[imaging] model unloaded (idle)")
                return


def unload() -> None:
    """Free the GPU memory now (e.g. a game just started)."""
    global _pipe
    with _lock:
        if _pipe is not None:
            import torch

            _pipe = None
            torch.cuda.empty_cache()
            print("[imaging] model unloaded")


def preload() -> None:
    """Start loading the model in the background (a try-on is probably coming)."""
    def run():
        global _last_used
        with _lock:
            _load()
            _last_used = time.time()
    threading.Thread(target=run, daemon=True).start()


def _park(pipe) -> None:
    """Give the card back: the transformer and VAE go to the CPU, the cache is emptied."""
    import torch
    for part in (pipe.transformer, pipe.vae, pipe.text_encoder):
        part.to("cpu")
    torch.cuda.empty_cache()


def _run(pipe, prompt: str, steps: int, seed, **kw):
    """One generation. Staged on the GPU unless the pipeline is in low-VRAM mode."""
    import torch

    generator = torch.Generator(device="cuda").manual_seed(seed) if seed is not None else None
    total = max(1, int(steps))

    def on_step(_pipe, i, _t, cb_kwargs):
        _say(f"Drawing, step {i + 1} of {total}")
        return cb_kwargs

    if _low:
        return pipe(prompt=prompt, num_inference_steps=steps, guidance_scale=1.0, generator=generator,
                    callback_on_step_end=on_step, **kw).images[0]
    try:
        with torch.no_grad():
            _say("Reading the request")
            pipe.text_encoder.to("cuda")
            embeds, _ids = pipe.encode_prompt(prompt=prompt, device="cuda")
            pipe.text_encoder.to("cpu")
            torch.cuda.empty_cache()
            _say("Moving the image model to the graphics card")
            pipe.transformer.to("cuda")
            pipe.vae.to("cuda")
            _say("Drawing")
            return pipe(prompt=None, prompt_embeds=embeds, num_inference_steps=steps, guidance_scale=1.0,
                        generator=generator, callback_on_step_end=on_step, **kw).images[0]
    finally:
        _park(pipe)


def _guarded(prompt: str, steps: int, seed, **kw) -> Image.Image:
    global _last_used, _pipe
    import torch

    with _lock:
        _last_used = time.time()
        try:
            result = _run(_load(), prompt, steps, seed, **kw)
        except torch.cuda.OutOfMemoryError:
            # Another app took graphics memory mid-render. Layer-by-layer mode is the only thing that still
            # fits; it is slow, so say so.
            _pipe = None
            torch.cuda.empty_cache()
            _say("The graphics card ran short of memory; switching to the slow low-memory mode")
            result = _run(_load(low_vram=True), prompt, steps, seed, **kw)
        _last_used = time.time()
        return result


def edit(prompt: str, images: list[Image.Image], width: int = 832, height: int = 1216,
         steps: int = 4, seed: int | None = None) -> Image.Image:
    """Generate an image guided by one or more reference images."""
    return _guarded(prompt, steps, seed, image=images if len(images) > 1 else images[0], width=width, height=height)


def generate(prompt: str, width: int = 1024, height: int = 1024, steps: int = 4, seed: int | None = None) -> Image.Image:
    """Text to image (no reference), same staging and low-VRAM retry as edit()."""
    return _guarded(prompt, steps, seed, width=width, height=height)
