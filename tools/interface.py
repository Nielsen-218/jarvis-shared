"""Jarvis's own interface: the HUD companion on the PC screen and the Command Center dashboard."""
from core import hud
from tools.registry import tool
from core.companion import ROBOT_ACTIONS


@tool(
    "Show or hide the HUD overlay (the on-screen orb, transcript and image panel).",
    {"visible": {"type": "boolean"}},
)
def set_hud_visible(visible: bool) -> str:
    if not hud.running():
        return "The HUD isn't running (started with --no-hud or in text mode)."
    hud.show() if visible else hud.hide()
    return "HUD shown." if visible else "HUD hidden. Say 'show the HUD' to bring it back."


@tool(
    "Open a Jarvis app page on this PC's screen, already signed in: 'dashboard' (the Command Center: clock, weather, "
    "agenda, training, meals, PC and homelab stats), 'fitness' (workouts, plan, progress, AFT), 'kitchen' (meal plan, "
    "recipes, shopping list, macros) or 'chat' (the phone app's chat). Use when they ask to see the dashboard, the "
    "fitness app, the plan, the meal plan, etc. on the PC.",
    {"page": {"type": "string", "enum": ["dashboard", "fitness", "kitchen", "chat"]}},
    direct=True,
)
def open_jarvis_page(page: str) -> str:
    import webbrowser

    from core import users
    from core.appserver import pc_page_url
    from tools.software import is_admin, launch_as_user

    if page == "dashboard" and hud.dashboard():
        return "Opened the Command Center on the PC."
    url = pc_page_url(users.current(), {"dashboard": "dashboard.html", "fitness": "fitness.html", "kitchen": "kitchen.html",
                                        "chat": ""}[page])
    if is_admin():
        launch_as_user(url)  # the browser opens with normal rights
    else:
        webbrowser.open(url)
    return f"Opened the {page} page on the PC."


def open_recorder() -> bool:
    """Open the Creole voice recorder page in this PC's browser, signed in (the way open_jarvis_page opens the other
    pages; the Command Center window has no per-page entry, its sidebar reaches the same page). -> opened?"""
    try:
        import webbrowser

        from core import users
        from core.appserver import pc_page_url
        from tools.software import is_admin, launch_as_user

        url = pc_page_url(users.current(), "voice-record.html")
        if is_admin():
            launch_as_user(url)
        else:
            webbrowser.open(url)
        return True
    except Exception:                                     # noqa: BLE001 - the words still say where to tap
        return False


OPEN_RECORDER = open_recorder       # tests replace this


@tool(
    "The animated Jarvis companion on the PC screen: 'workout' starts today's guided workout there (Jarvis calls out "
    "each set, times the rests and logs it), 'play' opens the games (trivia, rock-paper-scissors, reflex test), "
    "'joke' has him tell a joke on screen, 'stretch' runs a guided 2-minute stretch break, 'focus' shows the focus "
    "timer, 'chat' goes back to the conversation view. Use for 'start my workout on the screen', 'let's play a game', "
    "'coach me through today's session', 'give me a stretch break'. For the LITTLE ROBOT, 'home' opens his miniature room; 'sleep/wake/cook/workshop/exercise/read/pet' use it; 'taskbar/search/folder/window/favorite/leave' choose actual desktop hangouts; 'jetpack/dance/catch/juggle/kick' start physical play; 'quiet' calms play for today; 'desktop' exits his room/play scene.",
    {"action": {"type": "string", "enum": ["workout", "play", "joke", "stretch", "focus", "chat", *ROBOT_ACTIONS]}},
    required=["action"], direct=True,
)
def companion(action: str) -> str:
    if action in ROBOT_ACTIONS:
        if not hud.companion_action(action):
            return "The desktop robot isn't running in companion mode."
        return f"Asked the desktop robot to {action}."
    if not hud.mode(action):
        return "The on-screen companion isn't running (Jarvis was started with --no-hud or in text mode)."
    return {"workout": "Today's workout is up on the screen. I'll call out each set and time your rests.",
            "play": "Games are up on the screen: trivia, rock-paper-scissors or a reflex test.",
            "joke": "Check the screen.", "stretch": "Stretch break on the screen: two minutes, six moves. Follow along.",
            "focus": "The focus timer is up on the screen.", "chat": "Back to the conversation view."}[action]
