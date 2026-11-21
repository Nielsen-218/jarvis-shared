"""Voice/chat control of the World Intelligence globe (PC Hub). Every action is VERIFIED: success is reported only when the
open workspace acknowledges it; otherwise the owner is told the workspace isn't open or didn't respond."""
from core import worldintel as wi
from tools.registry import tool


def _run(action: str, **args) -> str:
    if not wi.workspace_open():
        return "World Intelligence isn't open in the Hub. Ask me to open it first (or click World Intelligence under Systems)."
    item = wi.queue_action(action, **args)
    ack = wi.wait_ack(item["id"], 10.0)
    if ack is None:
        return "I sent that to the globe but it didn't confirm, so I can't say it worked."
    if ack["ok"]:
        return ack["detail"] or "Done."
    return ack["detail"] or "The globe couldn't do that, and it didn't say why."


@tool("Control the World Intelligence globe in the PC Hub. action: goto {place: name or 'lat, lon', altitude_km?}, reset (whole world), "
      "layers {show: [aircraft|maritime|satellites|weather|cyclones|earthquakes|wildfire|alerts|cables|rail|cameras], only: true to hide all others}, "
      "hide {layers: [...]}, mode {mode: 2d|3d} (flat map or globe), rotate, zoom {direction: in|out}, tools (open the Tools tab: simulation, visual lab, world monitor), close (back to Home). "
      "Reports success only if the globe confirms. Use for 'show the world', 'take me to Haiti', 'show earthquakes', 'hide everything except weather'.",
      {"action": {"type": "string", "enum": ["open", "goto", "mode", "reset", "layers", "hide", "rotate", "zoom", "tools", "close"]},
       "place": {"type": "string"}, "altitude_km": {"type": "number"}, "show": {"type": "array", "items": {"type": "string"}},
       "layers": {"type": "array", "items": {"type": "string"}}, "only": {"type": "boolean"}, "direction": {"type": "string"},
       "hours": {"type": "integer"}, "mode": {"type": "string", "enum": ["2d", "3d"]}}, required=["action"])
def world_map(action, place="", altitude_km=None, show=None, layers=None, only=False, direction="in", hours=None, mode="3d"):
    if action == "open":
        if wi.workspace_open():
            return "World Intelligence is already open."
        item = wi.queue_action("open")
        ack = wi.wait_ack(item["id"], 10.0)
        return "World Intelligence is open." if ack and ack["ok"] else "I couldn't confirm the Hub opened World Intelligence (is the Hub window running?)."
    if action == "goto":
        found = wi.search_place(place)
        if not found.get("items"):
            return f"I couldn't find '{place}' on the map" + (f" ({found['error']})." if found.get("error") else ".")
        top = found["items"][0]
        return _run("goto", lat=top["lat"], lon=top["lon"], title=top["title"], altitude_km=altitude_km)
    if action in ("layers", "hide"):
        want = [x for x in (show if action == "layers" else layers) or [] if x in wi.LAYER_IDS]
        if not want:
            return "Which layers? Options: " + ", ".join(sorted(wi.LAYER_IDS)) + "."
        return _run("layers", **({"show": want, "only": bool(only)} if action == "layers" else {"hide": want}))
    if action in ("reset", "rotate", "tools", "close"):
        return _run(action)
    if action == "mode":
        return _run("mode", mode="2d" if str(mode).lower().startswith("2") else "3d")
    if action == "zoom":
        return _run("zoom", direction="out" if str(direction).startswith("out") else "in")
    return f"Unknown globe action {action}."


@tool("Read what is on the World Intelligence globe right now: the selected place or event, where the camera is, which layers are on. "
      "Use for 'what's happening here', 'where did that come from'. Then research the selection and cite sources; a marker is not proof.", {}, required=[])
def world_context():
    c = wi.get_context()
    if not wi.workspace_open():
        return "World Intelligence isn't open, so there is no map selection."
    return wi.context_prefix() or "World Intelligence is open but nothing is selected yet."
