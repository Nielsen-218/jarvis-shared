"""Bounded local holographic drawing; no screenshots, scripts or external sharing."""
import json
import math
import os
from pathlib import Path
import tempfile
from core.companion import data_directory


def validate_board(value):
    if not isinstance(value,dict) or set(value)!={"schema","points"} or type(value["schema"]) is not int or value["schema"]!=1:
        raise ValueError("Invalid board schema")
    points=value["points"]
    if not isinstance(points,list) or len(points)>512: raise ValueError("Board holds at most 512 points")
    if any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in p) for p in points):
        raise ValueError("Drawing points must be finite normalized pairs")
    return {"schema":1,"points":[list(p) for p in points]}


def load_board(path=None):
    path=Path(path) if path is not None else data_directory()/"companion-board.json"
    try:
        if path.stat().st_size>40000: return {"schema":1,"points":[]}
        return validate_board(json.loads(path.read_text(encoding="utf-8")))
    except (OSError,ValueError,TypeError): return {"schema":1,"points":[]}


def save_board(value,path=None):
    value=validate_board(value)
    path=Path(path) if path is not None else data_directory()/"companion-board.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,suffix=".tmp",delete=False) as f:
            temporary=Path(f.name);json.dump(value,f,allow_nan=False);f.flush();os.fsync(f.fileno())
        from core.fileio import replace_atomic
        replace_atomic(temporary,path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    return value
