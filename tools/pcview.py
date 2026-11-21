"""Phone -> always-on laptop -> existing Windows Jarvis, over authenticated Tailscale."""
import base64
import json
import uuid

from core import hud, pcbridge, storage
from tools.registry import tool


def _grab(all_monitors=False):
    pcbridge.require_owner()
    if not pcbridge.local_pc():
        return pcbridge.request("/api/pc/screenshot?monitors=" + ("all" if all_monitors else "main"))[0]
    from io import BytesIO
    from PIL import ImageGrab
    image = ImageGrab.grab(all_screens=all_monitors).convert("RGB")
    image.thumbnail((1600, 1600))
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def publish_images(blocks):
    for block in blocks:
        if block.get("type") != "image":
            continue
        source = block.get("source", {})
        if source.get("type") != "base64" or source.get("media_type") not in ("image/jpeg", "image/png"):
            continue
        try:
            data = base64.b64decode(source["data"], validate=True)
        except (ValueError, KeyError):
            continue
        if len(data) > 8 * 1024 * 1024:
            continue
        folder = storage.user_dir() / "screens"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("pc-" + uuid.uuid4().hex + (".png" if source["media_type"] == "image/png" else ".jpg"))
        path.write_bytes(data)
        hud.media(path=str(path), caption="Your PC screen")


@tool("See the real desk PC screen from the phone. Return its screenshot so you can describe it. "
      "Use for 'show my PC', 'read my screen', or checking the result of a PC command.",
      {"all_monitors": {"type": "boolean"}}, required=[])
def see_pc_screen(all_monitors=False):
    try:
        data = _grab(all_monitors)
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(data).decode()}}, {"type": "text", "text": "The real PC screen, also sent to your phone."}]
        publish_images(blocks)
        return blocks
    except Exception as error:
        return [{"type": "text", "text": f"PC screen unavailable: {error}"}]


@tool("Operate the user's Windows PC from their phone: click, type and scroll in its apps or browser. "
      "The always-on laptop forwards this to the existing elevated PC Jarvis. "
      "Uses hybrid local/workflow execution and checked local AI plans. Cloud planning needs an exact-task owner budget. "
      "There is no automatic paid per-click loop. Prefer pc_command for direct apps/media/power. "
      "Never repeat a task reported as still running or lost contact: check pc_status first.",
      {"task": {"type": "string"}}, slow=True)
def control_pc(task):
    return pc_command("control_computer", {"task": task})


@tool("Run a direct existing Windows Jarvis tool on the PC, including from the phone. "
      "Arguments by operation: open_app/close_app {app}; list_apps {contains}; open_website {url}; "
      "get_system_status {}; media_control {action:play_pause|next|previous|stop|mute}; volume {percent:-1..100}; "
      "list_programs {query}; install_program/uninstall_program {name}; "
      "power {action:shutdown|restart|sleep|lock|sign_out|cancel,delay_seconds:30}; control_computer {task}. "
      "These are real PC actions, not actions on the Linux laptop. Existing confirmation rules still apply.",
      {"name": {"type": "string", "enum": list(pcbridge.TOOLS)}, "arguments": {"type": "object"}})
def pc_command(name, arguments):
    try:
        pcbridge.require_owner()
        if name not in pcbridge.TOOLS:
            raise pcbridge.PCError("Unknown PC operation.", 400)
        if not pcbridge.local_pc():
            return pcbridge.run(name, arguments)
        import importlib
        from tools import registry
        importlib.import_module("tools." + pcbridge.TOOLS[name])
        result, _ = registry.run(name, arguments)
        return result if isinstance(result, list) else [{"type": "text", "text": result}]
    except pcbridge.PCError as error:
        goal = pcbridge.original_message() or (arguments or {}).get("task") or name
        return [{"type": "text", "text": pcbridge.explain(error, goal)}]


def wake_pc() -> str:
    """Compatibility entry point; homelab registers the single wake_pc skill."""
    from core import pcwake
    try:
        return pcwake.wake()
    except pcbridge.PCError as error:
        return str(error)


@tool("Check whether the PC is connected, whether its Jarvis has admin rights, and its real running/recent tasks.", {})
def pc_status():
    try:
        pcbridge.require_owner()
        if pcbridge.local_pc():
            from tools.software import is_admin
            return json.dumps({"connected": True, "admin": is_admin(), "note": "This is the desk PC."})
        return json.dumps(pcbridge.json_request("/api/pc/status"))
    except pcbridge.PCError as error:
        return pcbridge.explain(error, offer=False)


@tool("Request a stop for the current remote PC task. Reports stop requested, not a guarantee that an "
      "already-started Windows operation can be undone. Use when the owner says stop/cancel the PC task.", {})
def stop_pc_control():
    try:
        pcbridge.require_owner()
        if pcbridge.local_pc():
            from tools import computer
            computer.abort()
            return "Stop requested for computer control."
        return json.dumps(pcbridge.json_request("/api/pc/cancel", "POST", {}))
    except pcbridge.PCError as error:
        return pcbridge.explain(error, offer=False)
