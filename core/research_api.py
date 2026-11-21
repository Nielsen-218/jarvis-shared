"""Owner-authenticated additive routes, using the existing teaching body limit."""
from fastapi import Depends, HTTPException
from core import research, tutorial_learning, users


def install(app, owner):
    @app.get('/api/teaching/research')
    def status(uid=Depends(owner)):
        with users.acting_as(uid):
            return research.status()

    @app.get('/api/teaching/research/sources')
    def sources(q: str='', uid=Depends(owner)):
        with users.acting_as(uid):
            return {'sources': research.sources(q[:300])}

    @app.post('/api/teaching/research/{operation}')
    def operation(operation: str, body: dict, uid=Depends(owner)):
        calls={'search': research.search, 'open': research.open_page, 'follow': research.follow, 'remote_page':research.remote_page,'browse':research.browse,
               'study': tutorial_learning.study, 'pause': research.stop}
        if operation not in calls:
            raise HTTPException(400, 'Select a supported research operation')
        try:
            with users.acting_as(uid):
                return calls[operation](**body)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)[:200]) from exc
        except PermissionError as exc:
            raise HTTPException(403, 'Owner authorization required') from exc
        except InterruptedError as exc:
            raise HTTPException(409, 'Research is paused') from exc
        except Exception as exc:
            raise HTTPException(503, 'Research provider unavailable; existing Jarvis capabilities remain available') from exc
