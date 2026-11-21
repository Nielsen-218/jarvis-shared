"""Additive owner-only Hub routes; mentor tokens are CLI-only in v1."""
from fastapi import Depends, File, Form, HTTPException, UploadFile
import subprocess
import threading
from core import teaching, users
from core.teaching_import import MAX_FILE, extract, fetch


class TeachingBodyLimit:
    """Cap streamed bodies before FastAPI's JSON/multipart parsers allocate them."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/teaching"):
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_FILE + 65536:
                from starlette.responses import JSONResponse
                return await JSONResponse({"detail": "Teaching request exceeds limit"}, 413)(scope, receive, send)
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
        await self.app(scope, replay, send)


def install(app, auth):
    app.add_middleware(TeachingBodyLimit)
    practice_lock = threading.Lock()
    project_cancel = threading.Event()
    def owner(who=Depends(auth)):
        if users.get(who[0]).get("role") != "owner":
            raise HTTPException(403, "Learning Library is available to the owner")
        return who[0]

    from core.research_api import install as install_research
    install_research(app, owner)

    @app.get("/api/teaching")
    def library(q: str = "", uid=Depends(owner)):
        return {"lessons": teaching.search(q[:300], owner=True), **teaching.status()}

    @app.post("/api/teaching/lesson")
    def lesson(body: dict, uid=Depends(owner)):
        try:
            return teaching.submit(body, owner=True)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/teaching/file")
    def upload(file: UploadFile = File(...), title: str = Form(...), topic: str = Form(...),
               license: str = Form(...), uid=Depends(owner)):
        try:
            data = file.file.read(MAX_FILE+1)
            content = extract(file.filename or "", data)
            return teaching.submit({"title": title, "topic": topic, "license": license,
                "source": "upload:"+(file.filename or "document")[:200], "version": "1", "content": content}, owner=True)
        except (ValueError, UnicodeError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(400, "Source could not be indexed: "+str(exc)[:180]) from exc

    @app.post("/api/teaching/url")
    def url(body: dict, uid=Depends(owner)):
        try:
            source = body.get("source", "")
            if not isinstance(source, str) or len(source) > 500:
                raise ValueError("Invalid source URL")
            data, kind = fetch(source)
            from urllib.parse import urlsplit
            content = extract(urlsplit(source).path, data, kind)
            return teaching.submit({"title": body.get("title", ""), "topic": body.get("topic", ""),
                "source": source, "license": body.get("license", ""), "version": "1", "content": content}, owner=True)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, "URL could not be indexed: "+str(exc)[:180]) from exc

    @app.post("/api/teaching/revoke")
    def revoke(body: dict, uid=Depends(owner)):
        mentor = body.get("mentor")
        if not isinstance(mentor, str) or len(mentor) > 64:
            raise HTTPException(400, "Invalid mentor")
        teaching.revoke(mentor)
        return {"revoked": mentor}

    @app.post('/api/teaching/retry-index')
    def retry_index(body: dict,uid=Depends(owner)):
        lid=body.get('lesson_id')
        if not isinstance(lid,str) or len(lid)!=32: raise HTTPException(400,'Select a retained lesson')
        try: return {'indexed':teaching.project(lid)}
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc

    @app.post("/api/teaching/practice")
    def practice(body: dict, uid=Depends(owner)):
        from core import practice_agent, storage
        if set(body)-{'task','language','lesson_ids','retention','seed_bug'}:
            raise HTTPException(400,'Unknown practice fields')
        task, language = body.get('task'), body.get('language')
        if not isinstance(task,str) or task not in practice_agent.TASKS or language not in ('cpp','python'):
            raise HTTPException(400,'Select a supported scalar practice task/language')
        if not isinstance(body.get('lesson_ids'),list) or any(not isinstance(x,str) for x in body['lesson_ids']):
            raise HTTPException(400,'Select retained lesson IDs')
        if any(type(body.get(k,False)) is not bool for k in ('retention','seed_bug')):
            raise HTTPException(400,'Practice flags must be boolean')
        if body.get('seed_bug') and task!='range_sum':
            raise HTTPException(400,'Controlled bug fixture is supported only for range_sum')
        if not practice_lock.acquire(blocking=False): raise HTTPException(409,'Practice already running')
        stop = storage.path('household/teaching/control/STOP')
        try:
            stop.unlink(missing_ok=True)
            return practice_agent.run(task,language,body['lesson_ids'],retention=body.get('retention',False),
                seed_bug=body.get('seed_bug',False),stop_file=stop)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        finally:
            practice_lock.release()

    @app.post("/api/teaching/practice/stop")
    def stop_practice(uid=Depends(owner)):
        from core import storage
        project_cancel.set()
        storage.path('household/teaching/control/STOP').touch()
        return {'stop_requested':True,'meaning':'Compiler/runtime cancel promptly; in-flight local inference is bounded by its request timeout.'}

    @app.get('/api/teaching/project/methods')
    def project_methods(uid=Depends(owner)):
        from core import project_agent
        return {'methods':project_agent.methods(),'mastered':False}

    @app.post('/api/teaching/project')
    def project(body:dict,uid=Depends(owner)):
        import os
        from core import project_agent, project_tasks
        if not os.environ.get('JARVIS_PROJECT_LAB_ROOT'):
            raise HTTPException(404,'Isolated project workspace is disabled')
        if set(body)-{'task','replay','procedure_id','lesson_id'} or body.get('task') not in project_tasks.TASKS:
            raise HTTPException(400,'Select a registered project task')
        if type(body.get('replay',False)) is not bool:
            raise HTTPException(400,'Replay flag must be boolean')
        procedure=body.get('procedure_id')
        if procedure is not None and (not isinstance(procedure,str) or len(procedure)!=32 or any(x not in '0123456789abcdef' for x in procedure)):
            raise HTTPException(400,'Select a verified procedure ID')
        if not practice_lock.acquire(blocking=False):raise HTTPException(409,'Practice already running')
        try:
            project_cancel.clear()
            options={'replay':body.get('replay',False),'procedure_id':procedure,'cancel':project_cancel}
            if body.get('lesson_id') is not None:options['lesson_id']=body['lesson_id']
            return project_agent.run(body['task'],**options)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        finally:practice_lock.release()
