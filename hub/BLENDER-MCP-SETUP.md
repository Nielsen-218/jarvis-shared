# Shared Blender MCP — setup & test results (PR #11)

ONE Blender backend, three clients. The hardened worker runs **inside Blender**; every
client reaches it through the same loopback socket and the same enforced approval gate.

```
Codex ─┐
Claude ─┼──► hub/blender_mcp.py (MCP, stdio) ──► hub/blender_client.py ──► 127.0.0.1:9436 ──► hub/blender_worker.py (inside Blender)
ChatGPT ┘        read: auto                         sends JARVIS_BLENDER_TOKEN            token auth + workspace confinement
                 render/export: owner approval
```

- **Read tools** (`blender_status`, `list_scene_objects`) run automatically.
- **Action tools** (`render_preview`, `export_glb`) are denied until the **owner** approves
  that exact call in a terminal: `python hub/approve.py <id>`. An MCP `confirm`/annotation
  is **not** accepted as authorization.
- The worker authenticates every request (`JARVIS_BLENDER_TOKEN`) and confines all output
  to `JARVIS_BLENDER_WORKSPACE`. Loopback only; no public exposure; no arbitrary Python.

Absolute paths below are for this machine (worktree `C:\Users\you\ECC\jarvis-hub`,
venv `C:\Users\you\ECC\jarvis\.venv`). Adjust if yours differ.

## 1. One-time: pick a token and a workspace

PowerShell (set for your user; the worker and every client must share the same value):

```powershell
setx JARVIS_BLENDER_TOKEN (python -c "import secrets;print(secrets.token_hex(16))")
setx JARVIS_BLENDER_WORKSPACE "C:\Users\you\ECC\jarvis-hub\data\blender-workspace"
```

Open a new terminal afterward so the variables are present. `data/` is git-ignored, so the
token file, renders and exports never get committed.

## 2. Start the worker inside Blender

Open Blender 5.2, go to **Scripting → Open**, load
`C:\Users\you\ECC\jarvis-hub\hub\blender_worker.py`, then in the Python console:

```python
import blender_worker; blender_worker.start()
```

It prints `JARVIS Blender worker on 127.0.0.1:9436 (workspace=...)`. It refuses to start
without `JARVIS_BLENDER_TOKEN`. (The old `hub/blender_addon.py` is now a disabled shim —
it raises instead of opening the old unauthenticated socket.)

## 3. Claude Code

```powershell
claude mcp add-json jarvis-blender '{
  "command": "C:\\Users\\you\\ECC\\jarvis\\.venv\\Scripts\\python.exe",
  "args": ["C:\\Users\\you\\ECC\\jarvis-hub\\hub\\blender_mcp.py"],
  "env": { "JARVIS_BLENDER_TOKEN": "<your token>", "JARVIS_BLENDER_WORKSPACE": "C:\\Users\\you\\ECC\\jarvis-hub\\data\\blender-workspace" }
}'
```

Verify with `claude mcp list` (should show `jarvis-blender`). Then `blender_status` /
`list_scene_objects` work immediately; `render_preview` returns a `denied_needs_approval`
with the exact `python hub/approve.py <id>` line to run.

> Honest note: I could not register this for you from the sandbox — it would write your
> user MCP config, which is yours to change. The command above is verified-correct for
> this repo; run it in your own terminal. `claude` is on PATH here; `codex` is not, so I
> could not run `codex mcp list` either.

## 4. Codex

Codex reads `~/.codex/config.toml`. Add:

```toml
[mcp_servers.jarvis-blender]
command = "C:\\Users\\you\\ECC\\jarvis\\.venv\\Scripts\\python.exe"
args = ["C:\\Users\\you\\ECC\\jarvis-hub\\hub\\blender_mcp.py"]
env = { JARVIS_BLENDER_TOKEN = "<your token>", JARVIS_BLENDER_WORKSPACE = "C:\\Users\\you\\ECC\\jarvis-hub\\data\\blender-workspace" }
```

(or `codex mcp add jarvis-blender -- <python> <path>` on a Codex build that has it, then
set the two env vars in its environment). Same tools, same approval gate as Claude.

## 5. ChatGPT (remote) — honest status

**Not connected, and I will not claim it is.** Reaching this server from ChatGPT needs a
remote MCP transport (e.g. a Secure MCP Tunnel) terminating on this machine and forwarding
to `blender_mcp.py`. Two things must be true first, and I could **not** verify either from
here:

1. Your ChatGPT plan must actually allow custom/remote MCP **tool calls that mutate state**
   (many tiers allow read-style connectors only). Check this in your own ChatGPT account
   before relying on render/export from ChatGPT.
2. A tunnel must be stood up by you; I will not expose anything publicly (your constraint,
   and a hard safety limit for me).

When those hold, keep the remote scope **Blender-only** (this server already exposes
nothing else) and remember the approval gate still applies: ChatGPT can inspect freely, but
every render/export waits for your `hub/approve.py` grant on this PC. Because ChatGPT has no
shell here, it genuinely cannot self-approve — that is the point of the out-of-band gate.

## 6. Test results (this machine, real Blender 5.2.2 LTS)

`pytest hub/ -q` → **30 passed** (approval single-use/expiry/arg-binding, token auth,
path confinement, `handle()` with a fake bpy, MCP wrappers default-deny, `run_pc_task`
off by default).

Headless worker self-test (`blender -b -P hub/blender_selftest.py`), all passed:

| check | result |
|---|---|
| status | ok — Blender 5.2.2 LTS, scene "Scene", objects Camera/Cube/Light |
| list | ok — 3 objects with types + locations |
| render (confined) | ok — `data/blender-workspace/selftest/render.png` written (7.2 KB) |
| export (confined) | ok — `data/blender-workspace/selftest/scene.glb` written (1.9 KB) |
| render to `../../escape.png` | **rejected** — "render path escapes the approved workspace" |
| request with wrong token | **rejected** — "unauthorized" |
| op `delete_everything` | **rejected** — "unsupported op" |

Reproduce:

```bash
JARVIS_BLENDER_TOKEN=<token> JARVIS_BLENDER_WORKSPACE="C:\Users\you\ECC\jarvis-hub\data\blender-workspace" \
  "/c/Program Files/Blender Foundation/Blender 5.2/blender.exe" -b -P hub/blender_selftest.py
```
