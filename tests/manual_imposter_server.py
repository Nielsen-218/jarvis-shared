"""Loopback-only Imposter browser fixture; never serves the shared JARVIS database."""
from pathlib import Path
import os
import sys
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.playhouse import api

api.storage.DATA = Path(os.environ.get("PLAYHOUSE_IMPOSTER_TEST_DATA", ROOT / "data" / "playhouse-imposter-browser"))
app = FastAPI()


def not_paired():
    raise HTTPException(401, "JARVIS pairing is irrelevant to guest Imposter seats")


api.install(app, not_paired)
app.mount("/", StaticFiles(directory=ROOT / "app", html=True), name="app")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PLAYHOUSE_IMPOSTER_TEST_PORT", "8876")), log_level="error")
