"""Private PC routes shared by the phone server and existing elevated desktop Jarvis."""
import json
import threading
import re

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response

from core import pcbridge, pctasks, users


def install(app, auth):
    jobs = pctasks.Jobs()

    @app.post("/api/pc/intelligence")
    def local_intelligence(body: dict, request: Request, who=Depends(auth)):
        # The authenticated PC-owner link is reused. This is inference, not a shell
        # or a remote tool executor. The originating Jarvis registry executes calls.
        from core import intelligence
        try:
            pcbridge.require_owner(who[0])
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        if not pcbridge.local_pc():
            raise HTTPException(503, "GPU inference is available on the Windows PC only.")
        if len(json.dumps(body)) > 6 * 1024 * 1024:
            raise HTTPException(413, "Inference input too large.")
        try:
            with users.acting_as(who[0]):
                return intelligence.complete_local(body)
        except intelligence.LocalFailure as error:
            raise HTTPException(503, str(error)) from None

    @app.post("/api/pc/cloud")
    def pc_cloud(body: dict, request: Request, who=Depends(auth)):
        """The API brain for the laptop (phone turns): the PC makes the Claude call with ITS key and counts it in ITS
        budget (core.cloudmodel.claude_complete), so one meter covers both machines and no key lives on the laptop. The
        laptop's own registry and guards still judge the reply, as for /api/pc/intelligence."""
        from core import cloudmodel, intelligence
        try:
            pcbridge.require_owner(who[0])
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        if not pcbridge.local_pc():
            raise HTTPException(503, "The API brain answers from the Windows PC only.")
        if len(json.dumps(body)) > 6 * 1024 * 1024:
            raise HTTPException(413, "Input too large.")
        if cloudmodel.provider() != "claude" or not cloudmodel.available():
            raise HTTPException(503, "cloud_provider_unconfigured")
        try:
            with users.acting_as(who[0]):
                return cloudmodel.claude_complete(body)
        except intelligence.LocalFailure as error:
            raise HTTPException(503, str(error)) from None

    @app.post("/api/pc/speak")
    def pc_speak(body: dict, request: Request, who=Depends(auth)):
        """A phone reply's audio, made here on the graphics card for the laptop (core.pcbridge.speak): the same voices
        Jarvis uses out loud, as MP3 bytes. Nothing is played or said on the PC. A refusal (too long, the voice failed,
        not the PC) sends the laptop back to making it itself."""
        try:
            pcbridge.require_owner(who[0])
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        if not pcbridge.local_pc():
            raise HTTPException(503, "The voice is made on the Windows PC only.")
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise HTTPException(400, "Nothing to say, or too long to voice in one piece.")
        try:
            from audio import tts
            data = tts.mp3_voiced(text)              # an error when no voice said it: never silence sent as the reply
        except Exception as error:  # noqa: BLE001 - the laptop makes it instead
            print(f"[pc] phone voice failed: {type(error).__name__}")
            raise HTTPException(503, "The voice failed on the PC.") from None
        return Response(data, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    def call(who, request, path, method="GET", body=None, local=None):
        uid, _ = who
        try:
            pcbridge.require_owner(uid)
            with users.acting_as(uid):
                if pcbridge.local_pc():
                    return local(uid)
                if request.headers.get("X-Jarvis-PC-Hop"):
                    raise pcbridge.PCError("PC routing points to a server, not the Windows PC. Relay loop stopped.", 508)
                return pcbridge.json_request(path, method, body)
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None

    @app.get("/api/pc/status")
    def pc_status(request: Request, who=Depends(auth)):
        return call(who, request, "/api/pc/status", local=jobs.status)

    @app.post("/api/pc/tasks", status_code=202)
    def pc_start(body: dict, request: Request, who=Depends(auth)):
        name, arguments, rid = body.get("name"), body.get("arguments"), body.get("request_id")
        if not isinstance(name, str) or name not in pcbridge.TOOLS or not isinstance(arguments, dict):
            raise HTTPException(400, "Choose an allowed PC tool and its arguments.")
        if not isinstance(rid, str) or not re.fullmatch(r"[0-9a-fA-F-]{32,36}", rid):
            raise HTTPException(400, "A valid request_id is required.")
        message = body.get("user_message", "")
        if not isinstance(message, str) or len(message) > 8000 or len(json.dumps(arguments)) > 12000:
            raise HTTPException(400, "PC request is too large or invalid.")
        return call(who, request, "/api/pc/tasks", "POST", body,
            local=lambda uid: jobs.start(uid, name, arguments, rid, message))

    @app.get("/api/pc/tasks/{jid}")
    def pc_job(jid: str, request: Request, who=Depends(auth)):
        if not re.fullmatch(r"[0-9a-fA-F-]{32,36}", jid):
            raise HTTPException(400, "Invalid task ID.")
        return call(who, request, "/api/pc/tasks/" + jid, local=lambda uid: jobs.get(uid, jid))

    @app.post("/api/pc/cancel")
    def pc_cancel(request: Request, who=Depends(auth)):
        return call(who, request, "/api/pc/cancel", "POST", {}, local=lambda uid: jobs.cancel(uid))

    @app.post("/api/pc/policy/approve")
    def pc_approve(body: dict, request: Request, who=Depends(auth)):
        # Trusted authenticated owner ingress, deliberately absent from all model tool schemas.
        kind, rid = body.get("kind"), body.get("id")
        if kind not in ("plan", "cloud") or not isinstance(rid, str) or not re.fullmatch(r"[0-9a-f]{32}", rid):
            raise HTTPException(400, "Choose an exact pending plan or cloud-budget request.")
        def approve(uid):
            from core import pcledger
            try:
                pcledger.approve(rid, kind, body.get("usd"))
                return {"ok": True, "note": "Approved this exact request once for five minutes. Run the same task to continue."}
            except ValueError as error:
                raise pcbridge.PCError(str(error), 400) from None
        return call(who, request, "/api/pc/policy/approve", "POST", body, local=approve)

    @app.get("/api/pc/policy/pending")
    def pc_pending(request: Request, who=Depends(auth)):
        def pending(uid):
            from core import pcledger
            return {"pending": pcledger.pending()}
        return call(who, request, "/api/pc/policy/pending", local=pending)

    @app.post("/api/pc/workflows")
    def pc_save_workflow(body: dict, request: Request, who=Depends(auth)):
        def save(uid):
            from core import pcledger
            try:
                wid = pcledger.save_workflow(body.get("run_id"), body.get("name"), body.get("trigger"), by_owner=True)
                return {"ok": True, "id": wid, "trigger": body["trigger"].strip().casefold()}
            except ValueError as error:
                raise pcbridge.PCError(str(error), 400) from None
        result = call(who, request, "/api/pc/workflows", "POST", body, local=save)
        if not pcbridge.local_pc() and result.get("trigger"):
            from core import storage
            with users.acting_as(who[0]):
                hints = storage.load("pc_workflow_triggers.json", [])
                storage.save("pc_workflow_triggers.json", sorted(set(hints + [result["trigger"]])))
        return result

    @app.get("/api/pc/usage")
    def pc_usage(request: Request, who=Depends(auth)):
        def accounting(uid):
            from core import pcledger, usage
            return {"pc_events": pcledger.history(), "latency": pcledger.latency_summary(), "assistant_month_usd": usage.spent(),
                "note": "Local automation and local model computation cost $0 in API tokens. Ordinary assistant chat is accounted separately; cloud USD is calculated from provider token usage and the configured rate card."}
        result = call(who, request, "/api/pc/usage", local=accounting)
        if not pcbridge.local_pc():
            from core import usage
            result["phone_assistant_month_usd"] = usage.spent()
        return result

    @app.get("/api/pc/screenshot")
    def pc_screenshot(request: Request, monitors: str = "main", who=Depends(auth)):
        if monitors not in ("main", "all"):
            raise HTTPException(400, "Choose main or all monitors.")
        uid, _ = who
        try:
            pcbridge.require_owner(uid)
            with users.acting_as(uid):
                if not pcbridge.local_pc() and request.headers.get("X-Jarvis-PC-Hop"):
                    raise pcbridge.PCError("PC screenshot relay loop stopped.", 508)
                from tools.pcview import _grab
                return Response(_grab(monitors == "all"), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        except Exception:
            raise HTTPException(503, "PC screen unavailable. Unlock Windows and check the interactive Jarvis session.") from None

    @app.post("/api/pc/avatar/tryon")
    def pc_avatar_tryon(body: dict, request: Request, who=Depends(auth)):
        """Dress a standard avatar person in the requesting owner's real wardrobe photos on the GPU and
        return the PNG. The phone server relays here."""
        from tools import studio
        uid, _ = who
        try:
            pcbridge.require_owner(uid)
            if not pcbridge.local_pc():
                raise pcbridge.PCError("Try-on rendering runs on the Windows PC (GPU).", 503)
            person = str(body.get("person", ""))
            if person not in studio.AV_SEX:
                raise pcbridge.PCError("Unknown person.", 400)
            ids = body.get("garment_ids")
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 3
                    or any(not isinstance(g, str) or not g or len(g) > 100 for g in ids)):
                raise pcbridge.PCError("Pick one to three of your wardrobe items.", 400)
            wearer = str(body.get("garment_owner") or uid)       # each person's own closet (the Wardrobe page); default: the owner's
            if wearer != uid and wearer not in studio.AV_SEX:
                raise pcbridge.PCError("Unknown closet.", 400)
            try:
                path = studio.render_wardrobe_tryon(person, wearer, ids, str(body.get("notes", ""))[:200])
            except ValueError as error:
                raise pcbridge.PCError(str(error), 400) from None
            except RuntimeError as error:
                msg = str(error)
                raise pcbridge.PCError(msg if "busy" in msg.lower() else "Try-on render failed on the PC.", 503) from None
            if not path:
                raise pcbridge.PCError("No photo for that person yet — change the face first.", 400)
            return Response(path.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"})
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except Exception as error:
            msg = str(error)
            raise HTTPException(503, msg if "busy" in msg.lower() else
                                "Try-on render failed on the PC. Check the GPU/Jarvis session.") from None

    @app.post("/api/pc/eng")
    def pc_engineering(body: dict, request: Request, who=Depends(auth)):
        """Engineering Eyes on the GPU PC (identify / guided project steps). The phone server relays here."""
        from core import engeyes
        uid, _ = who
        try:
            pcbridge.require_owner(uid)
            if not pcbridge.local_pc():
                raise pcbridge.PCError("Engineering Eyes runs on the Windows PC (GPU).", 503)
            with users.acting_as(uid):
                return engeyes.handle(body)
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        except engeyes.EngError as error:
            raise HTTPException(error.status, str(error)) from None

    @app.post("/api/pc/eyes")
    def pc_eyes(body: dict, request: Request, who=Depends(auth)):
        """Free local vision on the PC for the phone's Eyes (the laptop has no vision model)."""
        from core import vision
        uid, _ = who
        try:
            pcbridge.require_owner(uid)
            if not pcbridge.local_pc() or not vision.available():
                raise pcbridge.PCError("Local vision isn't available on the PC.", 503)
            frames = [f for f in (body.get("images") or []) if isinstance(f, str) and f.startswith("data:image/")][:8]
            if not frames:
                raise pcbridge.PCError("Send at least one captured frame.", 400)
            return {"reply": vision.describe(frames, str(body.get("text", ""))[:2000], system=str(body.get("system", ""))[:1000])}
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        except Exception:
            raise HTTPException(503, "Local vision on the PC failed.") from None

    @app.get("/api/pc/thread")
    def pc_thread(who=Depends(auth)):
        """The PC's own recent voice exchanges with its owner (core.thread): the LAPTOP fetches them here, since its
        Syncthing sends the PC's changes only on an hourly rescan. The same auth and owner check as the routes around it."""
        from core import thread
        uid, _ = who
        try:
            pcbridge.require_owner(uid)
            if not pcbridge.local_pc():
                raise pcbridge.PCError("The PC's voice turns are held on the Windows PC.", 503)
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        with users.acting_as(uid):
            return {"uid": uid, "rows": thread.own(uid)}

    @app.post("/api/pc/awareness")
    def pc_awareness(body: dict, request: Request, who=Depends(auth)):
        """Daily Awareness on the PC (it owns the microphone and the session): status / session / delete."""
        from core import awareness
        uid, _ = who
        try:
            pcbridge.require_owner(uid)
            if not pcbridge.local_pc():
                raise pcbridge.PCError("Room listening runs on the Windows PC.", 503)
            op = body.get("op")
            with users.acting_as(uid):
                if op == "status":
                    return {"session": awareness.session()}
                if op == "session":
                    return {"session": awareness.set_session(str(body.get("action", "")), bool(body.get("guests")),
                                                             bool(body.get("consent")))}
                if op == "delete":
                    return awareness.delete_answer(body.get("ids"), body.get("minutes"), bool(body.get("everything")))
            raise pcbridge.PCError("Unknown awareness request.", 400)
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.post("/api/pc/announce")
    def pc_announce(body: dict, request: Request, who=Depends(auth)):
        """The always-on server fires reminders; the PC says them out loud (it has the speakers)."""
        from tools import system
        uid, _ = who
        try:
            pcbridge.require_owner(uid)
            if not pcbridge.local_pc():
                raise pcbridge.PCError("Speaking happens on the Windows PC.", 503)
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        text = str(body.get("text", "")).strip()[:400]
        if not text:
            raise HTTPException(400, "Nothing to say.")
        from core import attention
        urgency = str(body.get("urgency") or attention.HIGH)
        if urgency not in (attention.CRITICAL, attention.HIGH, attention.NORMAL, attention.LOW):
            urgency = attention.HIGH
        from core import users
        listener = str(body.get("for") or "owner")      # whose reminder it is: noted in that person's conversation
        listener = listener if listener in users.all_users() else "owner"

        def deliver() -> None:
            with users.acting_as(listener):
                attention.deliver(text, urgency)
        threading.Thread(target=deliver, daemon=True).start()
        return {"spoken": True, "judged": True}

    def dress_person(uid, body):
        pcbridge.require_owner(uid)
        if not pcbridge.local_pc():
            raise pcbridge.PCError("The dressing room's closet work runs on the PC.", 503)
        person = str(body.get("person") or uid)
        return person if person in users.all_users() else uid

    @app.post("/api/pc/dress/closet")
    def pc_dress_closet(body: dict, who=Depends(auth)):
        """Closet changes for the 3D dressing room: the PC owns wardrobe.json and makes the 3D models."""
        from tools import dressing
        try:
            with users.acting_as(dress_person(who[0], body)):
                return dressing.closet_op(str(body.get("op", "")), body.get("args") or {})
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        except (ValueError, KeyError) as error:
            raise HTTPException(400, str(error)) from None

    @app.post("/api/pc/dress/discover")
    def pc_dress_discover(body: dict, who=Depends(auth)):
        """Find a look online (store search + product photos made wearable); 'status' polls it."""
        from tools import dressing
        try:
            person = dress_person(who[0], body)
        except pcbridge.PCError as error:
            raise HTTPException(error.status, str(error)) from None
        if body.get("op") == "status":
            return dressing.discover_status()
        query = str(body.get("query", "")).strip()[:160]
        if not query:
            raise HTTPException(400, "Describe what to look for.")
        dressing.discover_async(query, person)
        return dressing.discover_status()

    @app.post("/api/pc/control")
    def pc_control(body: dict, request: Request, who=Depends(auth)):
        """Compatibility for earlier clients; new clients use resumable /tasks."""
        task = body.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > 4000:
            raise HTTPException(400, "Say what to do on the PC (up to 4000 characters).")
        import uuid
        rid = uuid.uuid4().hex
        payload = {"name": "control_computer", "arguments": {"task": task}, "request_id": rid, "user_message": task}
        job = call(who, request, "/api/pc/tasks", "POST", payload,
            local=lambda uid: jobs.start(uid, "control_computer", {"task": task}, rid, task))
        # Older clients retain a human-readable result, but never receive a false "Done".
        return {**job, "result": "PC task accepted. Check PC control for its verified result or required review.", "task_id": job["id"]}
