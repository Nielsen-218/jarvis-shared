"""One-time on-screen HUD badge calibration for local Nolagin sensor.

Usage:
  python scripts/gamecoach_badge_calibrate.py --game apex --field zone_moving
Run on Windows with a VISIBLE badge, in a training/replay menu. The user selects
the icon and presses Enter; the pixel template stays under data/gaming/.
"""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.gamecoach_badges import TEMPLATE_FACTS

def calibrate(game:str,field:str,folder:Path):
    if game not in ("apex","cod") or field not in TEMPLATE_FACTS:
        raise ValueError("Unsupported game or HUD status field")
    from core.gamecoach_capture import ScreenSource
    from PIL import Image
    import numpy as np
    import cv2
    source=ScreenSource()
    try:
        frame=None
        for _ in range(10):
            frame=source.grab()
            if frame is not None:break
            time.sleep(.05)
        if frame is None:
            raise RuntimeError("Screen capture unavailable")
        x,y,w,h=(int(v) for v in cv2.selectROI(
            f"Nolagin: select VISIBLE {field} ICON then Enter",
            cv2.cvtColor(frame,cv2.COLOR_RGB2BGR),fromCenter=False))
        cv2.destroyAllWindows()
        if not 8<=w<=400 or not 8<=h<=200:
            raise ValueError("Badge ROI must be between 8x8 and 400x200")
        patch=frame[y:y+h,x:x+w,:3]
        if float(patch.std())<18:
            raise ValueError("Icon patch is too flat to identify reliably")
        folder.mkdir(parents=True,exist_ok=True)
        name=f"badge-{game}-{field}"
        image_name=name+".png"
        Image.fromarray(patch).save(folder/image_name)
        meta={"field":field,"rect":[x,y,x+w,y+h],"image":image_name,
              "min_similarity":.96,"confirmations":3,
              "reviewed":True,"note":"Fixed pixel pattern; recalibrate for HUD changes"}
        (folder/(name+".json")).write_text(json.dumps(meta,indent=2),encoding="utf-8")
        print(f"Local {game} {field} icon saved at {folder/name}.json; "
              "review false-positive performance on a game recording before use.")
    finally:
        source.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--game",choices=["apex","cod"],required=True)
    ap.add_argument("--field",choices=sorted(TEMPLATE_FACTS),required=True)
    ap.add_argument("--folder",type=Path,default=ROOT/"data"/"gaming")
    a=ap.parse_args()
    calibrate(a.game,a.field,a.folder)
