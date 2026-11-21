"""Image -> untextured GLB with the official Hunyuan3D-2 base shape model (runs in the ISOLATED Hunyuan venv).

  <hunyuan-venv>/python scripts/image_to_glb.py IMAGE OUT.glb [--steps 30] [--octree 256] [--max-faces 150000]

Never import this from the JARVIS venv: it needs the isolated torch/hy3dgen environment. Offline: uses the
already-downloaded weights only. Licence: Tencent Hunyuan non-commercial.
"""
import argparse
import os
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("image")
ap.add_argument("out")
ap.add_argument("--steps", type=int, default=30)
ap.add_argument("--octree", type=int, default=256)
ap.add_argument("--max-faces", type=int, default=150000)
ap.add_argument("--repo", default=os.getenv("JARVIS_HUNYUAN_REPO", ""))
ap.add_argument("--hf-home", default=os.getenv("JARVIS_HUNYUAN_HF", ""))
a = ap.parse_args()
if a.hf_home:
    os.environ["HF_HOME"] = a.hf_home
os.environ.setdefault("HF_HUB_OFFLINE", "1")
if a.repo:
    sys.path.insert(0, a.repo)

import torch  # noqa: E402
from PIL import Image  # noqa: E402
from hy3dgen.rembg import BackgroundRemover  # noqa: E402
from hy3dgen.shapegen import (DegenerateFaceRemover, FaceReducer, FloaterRemover,  # noqa: E402
                              Hunyuan3DDiTFlowMatchingPipeline)

t0 = time.time()
img = Image.open(a.image).convert("RGBA")
if img.getchannel("A").getextrema()[0] == 255:      # no transparency yet: cut out the subject
    img = BackgroundRemover()(img.convert("RGB"))
pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained("tencent/Hunyuan3D-2", subfolder="hunyuan3d-dit-v2-0",
                                                        use_safetensors=True, device="cuda")
mesh = pipe(image=img, num_inference_steps=a.steps, octree_resolution=a.octree, num_chunks=20000,
            generator=torch.manual_seed(1), output_type="trimesh")[0]
mesh = FloaterRemover()(mesh)
mesh = DegenerateFaceRemover()(mesh)
if len(mesh.faces) > a.max_faces:
    mesh = FaceReducer()(mesh, max_facenum=a.max_faces)
Path(a.out).parent.mkdir(parents=True, exist_ok=True)
mesh.export(a.out)
print(f"IMAGE_TO_GLB ok verts={len(mesh.vertices)} faces={len(mesh.faces)} seconds={time.time()-t0:.1f} out={a.out}")
