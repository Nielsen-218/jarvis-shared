"""Gemini fallback must fail fast: short timeouts, one retry on a transient error, then a cooldown."""
import io
import json
import urllib.error

import pytest

from core import cloudmodel
from core.intelligence import LocalFailure

PAYLOAD = {"role": "advanced", "messages": [{"role": "user", "content": "hi"}]}
OK = {"choices": [{"finish_reason": "STOP", "message": {"content": "ready"}}]}


@pytest.fixture(autouse=True)
def gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(cloudmodel, "_cooldown_until", 0.0)
    monkeypatch.setattr(cloudmodel.time, "sleep", lambda s: None)


def _http(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"provider text"))


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(script, seen):
    def fake(request, timeout):
        seen.append(timeout)
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _Resp(json.dumps(step).encode())
    return fake


def test_transient_503_is_retried_once_then_succeeds(monkeypatch):
    seen = []
    monkeypatch.setattr(cloudmodel.urllib.request, "urlopen", _urlopen([_http(503), OK], seen))
    out = cloudmodel.complete(PAYLOAD)
    assert out["choices"][0]["finish_reason"] == "stop" and len(seen) == 2
    assert not cloudmodel.cooling_down()


def test_repeated_503_fails_fast_with_its_code_and_starts_cooldown(monkeypatch):
    seen = []
    monkeypatch.setattr(cloudmodel.urllib.request, "urlopen", _urlopen([_http(503), _http(503)], seen))
    with pytest.raises(LocalFailure, match="cloud_http_503"):
        cloudmodel.complete(PAYLOAD)
    assert cloudmodel.cooling_down()
    monkeypatch.setattr(cloudmodel.urllib.request, "urlopen", _urlopen([OK], seen))
    with pytest.raises(LocalFailure, match="cloud_cooling_down"):   # next turn skips straight to Claude
        cloudmodel.complete(PAYLOAD)
    assert len(seen) == 2


def test_bad_request_is_not_retried_and_does_not_cool_down(monkeypatch):
    seen = []
    monkeypatch.setattr(cloudmodel.urllib.request, "urlopen", _urlopen([_http(400)], seen))
    with pytest.raises(LocalFailure, match="cloud_http_400"):
        cloudmodel.complete(PAYLOAD)
    assert len(seen) == 1 and not cloudmodel.cooling_down()


def test_timeout_is_short_and_reported(monkeypatch):
    seen = []
    monkeypatch.setattr(cloudmodel.urllib.request, "urlopen",
                        _urlopen([urllib.error.URLError(TimeoutError("timed out"))], seen))
    with pytest.raises(LocalFailure, match="cloud_timeout"):
        cloudmodel.complete(PAYLOAD)
    assert seen == [20] and cloudmodel.cooling_down()
    seen.clear()
    monkeypatch.setattr(cloudmodel, "_cooldown_until", 0.0)
    monkeypatch.setattr(cloudmodel.urllib.request, "urlopen", _urlopen([OK], seen))
    cloudmodel.complete({**PAYLOAD, "role": "fast"})
    assert seen == [12]


def test_provider_text_never_leaks_into_the_failure(monkeypatch):
    monkeypatch.setattr(cloudmodel.urllib.request, "urlopen", _urlopen([_http(401)], []))
    with pytest.raises(LocalFailure) as info:
        cloudmodel.complete(PAYLOAD)
    assert "provider text" not in str(info.value) and "test-key" not in str(info.value)
