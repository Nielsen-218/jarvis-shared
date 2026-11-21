"""Loopback-only browser fixture; synthetic devices, isolated DB, no brain."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import storage
storage.DATA = ROOT / "data" / "playhouse-browser-fixture"
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from core.playhouse.api import install

app = FastAPI()
def auth(authorization: str = Header(default="")):
    allowed = {f"Bearer playhouse-test-{name}" for name in ("host", "one", "two")}
    if authorization not in allowed:
        raise HTTPException(401, "Not paired")
    return ("fixture", {"id": authorization})
install(app, auth)
app.mount("/", StaticFiles(directory=ROOT / "app", html=True))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8875, log_level="warning")
