"""Render the Kitchen's dish photos on this PC's GPU (FLUX.2 klein, local, free) from each dish's photo_prompt.

    .venv\\Scripts\\python.exe -m scripts.dish_photos              # every dish that has no photo yet
    .venv\\Scripts\\python.exe -m scripts.dish_photos griot legim  # just these (re-render)
    .venv\\Scripts\\python.exe -m scripts.dish_photos --limit 5

Photos go to data/household/dish-photos/<id>.webp (synced to the laptop with data/), 1024x768, one cookbook look.
Stops (and leaves the rest for the next run) when a game starts: the owner's game always wins the GPU. Unloads the
resident Ollama models before each photo so FLUX fits (live Jarvis reloads his brain within the minute and would push
FLUX into shared memory: 20 s became 290 s per photo); Jarvis reloads it on his next turn (a few seconds).
"""
import argparse
import os
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STYLE = ("Editorial cookbook food photography of real rustic home cooking, irregular natural shapes, appetizing, true-to-life colours and textures, served family-style, "
         "soft natural window light from the side, shallow depth of field, slightly overhead three-quarter angle, "
         "simple ceramic or enamel dishware on a warm wooden table, a few real garnishes, no text, no words, no logos, "
         "no hands, no people, no cutlery clutter. ")


def _game() -> str:
    try:
        from core import acquire
        return acquire.running_game() or ""
    except Exception:  # noqa: BLE001
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--steps", type=int, default=8)
    a = ap.parse_args(argv)
    from dotenv import load_dotenv
    from core import nutri, storage
    from core.storage import live_root
    load_dotenv(live_root() / ".env")
    # the LIVE data folder (a worktree's own data/ is empty), unless told otherwise
    storage.DATA = Path(os.environ["JARVIS_DATA_DIR"]) if os.getenv("JARVIS_DATA_DIR") else live_root() / "data"
    lib = nutri.library()
    todo = [lib[i] for i in a.ids if i in lib] if a.ids else [d for d in lib.values() if not nutri.photo_path(d["id"]).exists()]
    todo = [d for d in todo if d.get("photo_prompt")]
    if a.limit:
        todo = todo[:a.limit]
    print(f"[photos] {len(todo)} to render", flush=True)
    if not todo:
        return 0
    if _game():
        print(f"[photos] {_game()} is running; not touching the GPU", flush=True)
        return 2
    from tools import studio
    studio._free_gpu_for_flux()
    from imaging import engine
    done = 0
    for d in todo:
        if _game():
            print(f"[photos] {_game()} started; stopping after {done}", flush=True)
            break
        t = time.time()
        if done:
            studio._free_gpu_for_flux()   # live Jarvis may have reloaded his brain since the last photo: make room again
        prompt = STYLE + str(d["photo_prompt"])
        img = engine.generate(prompt, width=1024, height=768, steps=a.steps, seed=zlib.crc32(d["id"].encode()))
        out = nutri.photo_path(d["id"])
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.webp")
        img.save(tmp, "WEBP", quality=82, method=5)
        os.replace(tmp, out)
        done += 1
        print(f"[photos] {done}/{len(todo)} {d['id']} {time.time() - t:.1f}s", flush=True)
    engine.unload()
    return 0


if __name__ == "__main__":
    sys.exit(main())
