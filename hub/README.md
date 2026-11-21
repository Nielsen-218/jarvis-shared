# JARVIS Universal Control Hub

One shared set of PC-control tools that **JARVIS (phone/brain), Claude Code and Codex**
all use — instead of a separate integration per AI. The hub is the core
(`controlhub.py`); `mcp_server.py` exposes it over **MCP** so AI clients can connect,
and JARVIS can call `controlhub.run_tool(...)` directly.

> **Honest limitation (as noted in the proposal):** MCP does not magically control every
> app. Each program needs a supported connection — an API, an automation interface, or
> approved input automation. The hub wires up what's actually reachable; e.g. Blender is
> driven through its own `bpy` API via `hub/blender_addon.py`.

## Tools (v1)
| Tool | Kind | Reuses |
|---|---|---|
| `get_pc_status` | read | psutil |
| `take_screenshot` | read | PIL ImageGrab |
| `list_apps` | read | `tools.apps` |
| `get_volume` | read | `tools.media` |
| `open_app` | action | `tools.apps` |
| `set_volume` | action | `tools.media` |
| `open_url` | action | `tools.system` |
| `blender_render` | action | `hub/blender_addon.py` |
| `close_app` | **action (destructive)** | `tools.apps` |
| `run_pc_task` | **action (destructive)** | Same hybrid controller as voice/phone |

## Permission model
- **Read** tools run automatically (status, screenshot, list, volume).
- **Action** MCP tools require a consumed, short-lived, exact-argument owner grant from
  `hub/approval.py`. Tool annotations and model-provided booleans are not authorization.
  `run_tool(confirm=...)` is a Python-core convenience, not a remote security boundary.
- Local operations use no paid AI. `run_pc_task` reuses hybrid routing: known actions,
  verified workflows, local AI planning, then a separately budgeted cloud plan only if
  explicitly authorized. MCP action approval does **not** grant cloud spending.
  The old `HUB_ENABLE_PC_TASK` switch no longer enables a paid click loop; that loop is retired.
- See [hybrid policy and validation](../HYBRID-PC-CONTROL.md) for costs, owner grants,
  supported operations and safe stopping. `close_app` still refuses protected processes.

## Connect an AI client
Install the SDK once: `pip install mcp` (added to `requirements.txt`).

**Claude Code / Codex** — add to `.mcp.json` at the repo root (an example ships as
`jarvis-hub.mcp.json`):
```json
{ "mcpServers": { "jarvis-hub": { "command": "python", "args": ["hub/mcp_server.py"] } } }
```
Run standalone to test: `python hub/mcp_server.py` (stdio).

**Phone JARVIS** — the brain can call `hub.controlhub.run_tool(...)` in-process (same
machine) or, from the laptop, reuse the existing `/api/pc/*` Tailscale relay to the PC.
This is also the clean path to fix "phone JARVIS can't control the PC": one hub, one
auth, surfaced to the phone over Tailscale with the same read/action split.

## Blender
`blender_render` connects to `hub/blender_addon.py`, which you run **inside Blender**
(Scripting tab → Run Script). It listens on `127.0.0.1:9436` and supports
`status` / `render` / `export`. If it isn't running, the tool says so plainly rather
than pretending. (Reference quality — verify in your Blender version.)

## Roadmap (consent-gated, not built here)
Files (scoped, read-auto / write-approve), homelab (reuse `tools.homelab`), a browser
bridge (reuse Claude-in-Chrome / the existing browser tools), and richer Windows window
management. Each is added as another hub tool with the same permission split.

## Tests
`python -m pytest hub/ -q` — fully mocked (no app launches, no volume changes, no real
computer-use or screen capture). Proves the read/action split and the confirmation gate.
Live read smoke test: `python hub/controlhub.py get_pc_status`.
