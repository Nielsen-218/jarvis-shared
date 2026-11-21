"""core.research_mcp._plain_request: the public Exa endpoint over plain HTTP for a machine without the `mcp` package (the laptop).
Live 2026-11-15 the laptop answered news and scores from Bing/DuckDuckGo snippets only (exa-direct failed on `import mcp`)."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from core import research_mcp


def test_json_rpc_body_reads_plain_json_and_event_streams():
    plain = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": []}})
    assert research_mcp._json_rpc_body(plain, 2)["id"] == 2
    stream = "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n\n" \
             "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"x": 1}}) + "\n\n"
    assert research_mcp._json_rpc_body(stream, 2)["result"] == {"x": 1}
    assert research_mcp._json_rpc_body("data: not json\n" + stream, 1)["id"] == 1
    with pytest.raises(RuntimeError):
        research_mcp._json_rpc_body("event: ping\n", 2)


class _Reply:
    def __init__(self, body, headers=None, status=200):
        self.text, self.content, self.headers, self.status_code = body, body.encode(), headers or {}, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http")


def _fake_client(monkeypatch, tool_text=None, tool_error=False, rpc_error=False, event_stream=False, seen=None):
    posts = seen if seen is not None else []

    class Client:
        def __init__(self, **kw):
            assert kw.get("follow_redirects") is False and kw.get("trust_env") is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posts.append((url, dict(headers or {}), json))
            method = json.get("method")
            if method == "initialize":
                return _Reply(globals()["_dump"]({"jsonrpc": "2.0", "id": 1, "result": {}}), {"mcp-session-id": "abc"})
            if method == "notifications/initialized":
                return _Reply("", status=202)
            if rpc_error:
                body = {"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "no"}}
            else:
                body = {"jsonrpc": "2.0", "id": 2, "result": {"isError": tool_error,
                        "content": [{"type": "text", "text": tool_text}, {"type": "image", "data": "x"}]}}
            text = "event: message\ndata: " + globals()["_dump"](body) + "\n\n" if event_stream else globals()["_dump"](body)
            return _Reply(text)

    monkeypatch.setitem(sys.modules, "httpx2", SimpleNamespace(AsyncClient=Client))
    return posts


def _dump(value):
    return json.dumps(value)


def test_plain_request_sends_initialize_then_the_tool_call_with_the_session(monkeypatch):
    posts = _fake_client(monkeypatch, tool_text="Title: Lakers 123 - Bucks 112")
    text = asyncio.run(research_mcp._plain_request("web_search_exa", {"query": "Lakers", "numResults": 3}))
    assert text == "Title: Lakers 123 - Bucks 112"
    assert [p[2].get("method") for p in posts] == ["initialize", "notifications/initialized", "tools/call"]
    assert posts[2][1].get("Mcp-Session-Id") == "abc"
    assert posts[2][2]["params"] == {"name": "web_search_exa", "arguments": {"query": "Lakers", "numResults": 3}}
    assert all(p[0] == research_mcp.ENDPOINT for p in posts)


def test_plain_request_reads_an_event_stream_answer(monkeypatch):
    _fake_client(monkeypatch, tool_text="Title: X", event_stream=True)
    assert asyncio.run(research_mcp._plain_request("web_search_exa", {"query": "x"})) == "Title: X"


@pytest.mark.parametrize("kw, error", [
    ({"tool_text": "x", "tool_error": True}, RuntimeError), ({"tool_text": "x", "rpc_error": True}, RuntimeError),
    ({"tool_text": ""}, ValueError), ({"tool_text": "y" * 100001}, ValueError),
    ({"tool_text": "Rate limit reached for this key"}, research_mcp.ProviderRateLimited)])
def test_plain_request_refuses_errors_empty_oversize_and_the_free_quota(monkeypatch, kw, error):
    _fake_client(monkeypatch, **kw)
    with pytest.raises(error):
        asyncio.run(research_mcp._plain_request("web_search_exa", {"query": "x"}))


def test_request_falls_back_to_plain_http_without_the_mcp_package(monkeypatch):
    _fake_client(monkeypatch, tool_text="Title: from plain http")
    monkeypatch.setitem(sys.modules, "mcp", None)                 # `from mcp import ...` raises ImportError
    assert research_mcp.call("web_search_exa", {"query": "x"}) == "Title: from plain http"


def test_an_unapproved_tool_is_still_refused():
    with pytest.raises(RuntimeError):
        research_mcp.call("run_anything", {})
