"""Draw hub/rig_acceptance.py's bones2d JSON onto its render (system python + Pillow)."""
import json, sys
from PIL import Image, ImageDraw

for png in sys.argv[1:]:
    im = Image.open(png).convert("RGB")
    dr = ImageDraw.Draw(im)
    for ln in json.load(open(png + ".bones.json")):
        col = (255, 40, 40) if ln["deform"] else (60, 160, 255)
        dr.line([*ln["a"], *ln["b"]], fill=col, width=2)
        x, y = ln["a"]
        dr.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 230, 0))
    im.save(png.replace(".png", "_bones.png"))
