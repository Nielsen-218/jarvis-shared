"""Run Imposter on a trusted local Wi-Fi without the cloud or JARVIS pairing.

Example: python -m scripts.playhouse_imposter_host --host 192.168.1.23
The owner must explicitly start this process. It never changes shared services.
"""
import argparse
import ipaddress
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
import uvicorn

from core import config
from core.playhouse import api


def build_app(data_dir):
    api.storage.DATA = Path(data_dir)
    app = FastAPI(title="JARVIS Playhouse Imposter LAN host", docs_url=None, redoc_url=None, openapi_url=None)

    def no_jarvis_pairing():
        raise HTTPException(401, "This standalone host only accepts Imposter guest seats")

    api.install(app, no_jarvis_pairing)
    app.mount("/", StaticFiles(directory=config.ROOT / "app", html=True), name="playhouse")
    return app


def main():
    parser = argparse.ArgumentParser(description="Trusted-LAN Imposter host; no Internet or paid API needed")
    parser.add_argument("--host", default="127.0.0.1", help="This PC's private LAN IP; defaults to loopback")
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--data-dir", type=Path, default=config.ROOT / "data" / "playhouse-imposter-lan")
    args = parser.parse_args()
    try:
        address = ipaddress.ip_address(args.host)
    except ValueError as exc:
        parser.error(f"Use a numeric private LAN address: {exc}")
    if address.is_unspecified or address.is_multicast or (not address.is_loopback and not address.is_private):
        parser.error("The host must be loopback or a private LAN address")
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port from 1024 to 65535")
    display_host = f"[{args.host}]" if address.version == 6 else args.host
    print(f"Open http://{display_host}:{args.port}/imposter.html on each phone on the same trusted Wi-Fi.")
    print("Keep this terminal and PC awake during the match. Seat tokens use HTTP on this LAN; use trusted Wi-Fi.")
    uvicorn.run(build_app(args.data_dir), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
