"""Small paired-device integration into the existing AppServer."""
import hashlib
import mimetypes
import socket
from fastapi import Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field, StrictInt, StrictBool
from core import storage
from .rooms import Rooms, RoomError, identity
from .imposter import ImposterGames


class RoomRequests:
    """Bound JSON input before parsing; authenticated snapshots are never cached."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/playhouse/"):
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > 2048:
                from starlette.responses import JSONResponse
                return await JSONResponse({"detail": "request_too_large"}, 413,
                                          headers={"Cache-Control": "no-store"})(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        async def no_cache(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"cache-control", b"no-store")]
            await send(message)
        await self.app(scope, replay, no_cache)


class Join(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern="^[A-Z2-9]{6}$")


class Action(BaseModel):
    round: StrictInt = Field(ge=0, le=10000)
    choice: StrictInt | None = Field(default=None, ge=0, le=5)


class ImposterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=20)


class ImposterJoin(ImposterCreate):
    code: str = Field(min_length=6, max_length=6)


class ImposterAction(BaseModel):
    revision: StrictInt = Field(ge=0)
    target: StrictInt | None = None
    use: StrictBool = False
    confirm: StrictBool | None = None
    prediction: str | None = None
    settings: dict | None = None


def install(app, auth):
    # Windows registry MIME mappings vary; these assets/modules must be portable.
    mimetypes.add_type("image/webp", ".webp")
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("application/json", ".json")
    app.add_middleware(RoomRequests)
    def store():
        # Separate machine files avoid live SQLite replication between PC/laptop.
        machine = hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12]
        return Rooms(storage.DATA / "playhouse" / (machine + ".sqlite"))

    def imposter_store():
        machine = hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12]
        return ImposterGames(storage.DATA / "playhouse" / ("imposter-" + machine + ".sqlite"))

    def guest(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer ") or len(authorization) > 160:
            raise HTTPException(401, "seat_token_required")
        return authorization[7:]

    def run(fn):
        try:
            return fn()
        except RoomError as exc:
            raise HTTPException(exc.status, exc.code) from exc

    @app.post("/api/playhouse/rooms")
    def create(who=Depends(auth)):
        return run(lambda: store().create(identity(who)))

    @app.post("/api/playhouse/join")
    def join(body: Join, who=Depends(auth)):
        return run(lambda: store().join(body.code, identity(who)))

    @app.get("/api/playhouse/rooms/{rid}")
    def state(rid: str, who=Depends(auth)):
        return run(lambda: store().state(rid, identity(who)))

    @app.post("/api/playhouse/rooms/{rid}/{action}")
    def act(rid: str, action: str, body: Action, who=Depends(auth)):
        return run(lambda: store().act(rid, identity(who), action, body.round, body.choice))

    @app.post("/api/playhouse/imposter/rooms")
    def imposter_create(body: ImposterCreate, request: Request):
        actor = request.client.host if request.client else "unknown"
        return run(lambda: imposter_store().create(body.name, actor))

    @app.post("/api/playhouse/imposter/join")
    def imposter_join(body: ImposterJoin, request: Request):
        actor = request.client.host if request.client else "unknown"
        return run(lambda: imposter_store().join(body.code.upper(), body.name, actor))

    @app.get("/api/playhouse/imposter/rooms/{rid}")
    def imposter_state(rid: str, token=Depends(guest)):
        return run(lambda: imposter_store().state(rid, token))

    @app.post("/api/playhouse/imposter/rooms/{rid}/{action}")
    def imposter_act(rid: str, action: str, body: ImposterAction, token=Depends(guest)):
        return run(lambda: imposter_store().act(rid, token, action, body.revision,
                                                 target=body.target, use=body.use, confirm=body.confirm,
                                                 prediction=body.prediction, settings=body.settings))
