"""Explicit public Exa MCP client. No server installation, roots or sampling authority."""
import asyncio
import os
from pathlib import Path
import sys

ENDPOINT = 'https://mcp.exa.ai/mcp'
TOOLS = {'web_search_exa', 'web_fetch_exa'}


class ProviderRateLimited(RuntimeError):
    retry_after_seconds = 3600


async def local_request(name,arguments):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    env={k:v for k,v in os.environ.items() if k.upper() in
         {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','LOCALAPPDATA'}}
    from core import storage,users
    env['JARVIS_RESEARCH_DATA_DIR']=str(storage.DATA.resolve())
    env['JARVIS_RESEARCH_USER']=users.current()
    params=StdioServerParameters(command=sys.executable,args=['-m','scripts.research_mcp'],
        cwd=str(Path(__file__).resolve().parents[1]),env=env)
    async with asyncio.timeout(60 if name=='public_browser' else 30):
        async with stdio_client(params) as streams:
            async with ClientSession(*streams,read_timeout_seconds=55 if name=='public_browser' else 20) as session:
                await session.initialize()
                result=await session.call_tool(name,arguments)
                if result.is_error:raise RuntimeError('Public RSS MCP search unavailable')
                text='\n'.join(c.text for c in result.content if c.type=='text')
                if len(text.encode())>40000:raise ValueError('MCP operation exceeds quota')
                return text


async def request(name, arguments):
    if name not in TOOLS:
        raise ValueError('Unapproved MCP operation')
    # Imported lazily: a missing experimental provider never disables other skills.
    import httpx2
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:       # the laptop has no `mcp` package: the same public endpoint over plain HTTP (see _plain_request)
        return await _plain_request(name, arguments)
    async with asyncio.timeout(40):
        async with httpx2.AsyncClient(trust_env=False, timeout=25, follow_redirects=False) as http:
            async with streamable_http_client(ENDPOINT, http_client=http,
                                               max_sse_event_size=131072) as streams:
                async with ClientSession(*streams, read_timeout_seconds=30) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
                    if result.is_error:
                        raise RuntimeError('Approved MCP provider rejected the operation')
                    text = '\n'.join(c.text for c in result.content if c.type == 'text')
                    if 'rate limit' in text.lower():
                        raise ProviderRateLimited('Exa free MCP quota reached; no paid fallback')
                    if not text or len(text.encode()) > 100000:
                        raise ValueError('MCP result is empty or exceeds quota')
                    return text


_PROTOCOL = "2025-03-26"


def _json_rpc_body(text: str, want_id: int):
    """The JSON-RPC message with `want_id` from a response body that is plain JSON or a server-sent-events stream."""
    import json
    text = (text or "").strip()
    messages = []
    if text.startswith("{") or text.startswith("["):
        loaded = json.loads(text)
        messages = loaded if isinstance(loaded, list) else [loaded]
    else:
        for line in text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    try:
                        messages.append(json.loads(chunk))
                    except ValueError:
                        continue
    for message in messages:
        if isinstance(message, dict) and message.get("id") == want_id:
            return message
    raise RuntimeError("no answer from the MCP endpoint")


async def _plain_request(name, arguments):
    """The same public Exa endpoint and the same two approved tools, over plain HTTP (initialize, then tools/call), for a machine
    without the `mcp` package. Same limits as `request`: public query only, no cookies, no redirects, no env, size cap, free
    quota only (never a paid fallback)."""
    import httpx2
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": _PROTOCOL}
    async with asyncio.timeout(40):
        async with httpx2.AsyncClient(trust_env=False, timeout=25, follow_redirects=False) as http:
            started = await http.post(ENDPOINT, headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL, "capabilities": {}, "clientInfo": {"name": "jarvis-research", "version": "1"}}})
            started.raise_for_status()
            _json_rpc_body(started.text, 1)
            session = started.headers.get("mcp-session-id")
            if session:
                headers["Mcp-Session-Id"] = session
            await http.post(ENDPOINT, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            reply = await http.post(ENDPOINT, headers=headers, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
            reply.raise_for_status()
            if len(reply.content) > 400000:
                raise ValueError("MCP result exceeds quota")
            message = _json_rpc_body(reply.text, 2)
            result = message.get("result") or {}
            if message.get("error") or result.get("isError"):
                raise RuntimeError("Approved MCP provider rejected the operation")
            text = "\n".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict) and c.get("type") == "text")
            if "rate limit" in text.lower():
                raise ProviderRateLimited("Exa free MCP quota reached; no paid fallback")
            if not text or len(text.encode()) > 100000:
                raise ValueError("MCP result is empty or exceeds quota")
            return text


def call(name, arguments):
    # Caller supplies public query/URLs only, never conversation, cookies or files.
    try:
        if name in {'public_search','public_browser'}:return asyncio.run(local_request(name,arguments))
        return asyncio.run(request(name, arguments))
    except Exception as exc:
        def limited(error):
            return isinstance(error,ProviderRateLimited) or any(limited(e) for e in getattr(error,'exceptions',[]))
        if limited(exc):raise ProviderRateLimited('Exa free MCP quota reached; paused for one hour; no paid fallback') from exc
        # Transport exception groups can contain provider data. Keep it private.
        raise RuntimeError('MCP operation unavailable; no paid fallback was attempted') from exc
