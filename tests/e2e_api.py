"""Live end-to-end checks against the Claude API (costs a few cents).

Run all:          .venv\\Scripts\\python.exe tests\\e2e_api.py
Run some groups:  .venv\\Scripts\\python.exe tests\\e2e_api.py core languages

Uses a temporary data folder, so real memories/lists are untouched. Each
scenario checks which tools Jarvis chose and what he said.
"""
import os
import sys
import tempfile
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-e2e-"))

from audio.language import detect  # noqa: E402
from brain.llm import Brain  # noqa: E402
from core import usage  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import fashion, load_all, memory, registry, studio  # noqa: E402

load_all()

calls: list[str] = []
_run = registry.run


def tracking_run(name, args):
    calls.append(name)
    return _run(name, args)


registry.run = tracking_run
opened: list[str] = []
webbrowser.open = lambda url, *a, **k: opened.append(url)  # don't pop windows during tests
cfg = Config()
results = []


def new_brain(uid: str = "owner") -> Brain:
    b = Brain(cfg.model, cfg.effort, load_personality(cfg, uid), uid=uid)
    b.context = memory.context_block
    return b


def ask(brain: Brain, text: str) -> str:
    calls.clear()
    said: list[str] = []
    t = time.time()
    brain.ask(text, said.append)
    reply = " ".join(said)
    shown = text if isinstance(text, str) else " + ".join(
        b["text"] if b["type"] == "text" else f"[{b['type']}]" for b in text)
    print(f"\nYOU: {shown}\n  tools: {calls}\n  JARVIS ({time.time() - t:.1f}s): {reply[:400]}")
    return reply


def check(name: str, ok: bool) -> None:
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")


# -- groups ---------------------------------------------------------------------------------
def core():
    b = new_brain()
    ask(b, "Remember that my sister Marie lives in Miami.")
    check("remembers a fact", "remember" in calls)
    b = new_brain()  # like restarting Jarvis
    r = ask(b, "Where does my sister live?")
    check("recalls after restart", "Miami" in r)
    ask(b, "Add eggs and milk to my shopping list.")
    check("adds to a list", calls.count("add_note") >= 1)
    r = ask(b, "What's on my shopping list?")
    check("reads the list", "eggs" in r.lower() and "milk" in r.lower())
    ask(b, "What's the volume at right now? Don't change it.")
    check("reads the volume", "volume" in calls)
    ask(b, "How much have you cost me this month?")
    check("reports usage", "get_usage" in calls)
    r = ask(new_brain(), "Kisa ou ka fè pou mwen?")
    check("answers in Kreyol", detect(r) == "ht")
    os.environ["JARVIS_MONTHLY_BUDGET"] = "0"
    r = ask(new_brain(), "What time is it?")
    check("enforces the budget", "spending limit" in r and not calls)
    os.environ.pop("JARVIS_MONTHLY_BUDGET")


def languages():
    r = ask(new_brain(), "Bonjour Jarvis, comment ça va aujourd'hui ?")
    check("answers in French", detect(r) == "fr")
    b = new_brain()
    ask(b, "Can you translate between me and my grandmother? I speak English and she speaks Haitian Creole.")
    if "start_interpreter" not in calls:  # he may confirm the languages first
        ask(b, "Yes, exactly, go ahead.")
    check("starts interpreter mode", "start_interpreter" in calls)
    said: list[str] = []
    b.translate("Grandma, did you eat today? I brought you some soup.", "English", "Haitian Creole", said.append)
    print(f"  translation: {' '.join(said)}")
    check("translates English -> Kreyol", detect(" ".join(said)) == "ht")
    said.clear()
    b.translate("Mwen te manje maten an, mèsi pitit mwen.", "Haitian Creole", "English", said.append)
    print(f"  translation: {' '.join(said)}")
    check("translates Kreyol -> English", detect(" ".join(said)) == "en")
    from tools import languages as lang_tools

    lang_tools.stop_interpreter()
    b = new_brain()
    r = ask(b, "Teach me a few basic Spanish greetings. I'm a total beginner.")
    check("starts a tutored lesson", "tutor_status" in calls)
    check("teaches Spanish words", any(w in r.lower() for w in ("hola", "buenos", "buenas")))
    ask(b, "Great, save those words for review and that's it for today.")
    check("saves vocabulary", "tutor_add_words" in calls)
    opened.clear()
    ask(new_brain(), "Explain how a credit score works, and show me something visual.")
    check("opens a lesson page", "show_lesson_page" in calls and bool(opened))


def proactive():
    from tools import proactive as p

    b = new_brain()
    before = datetime.now()
    ask(b, "Remind me in 20 minutes to take the laundry out.")
    items = storage.load(p.REMINDERS, [])
    ok = "set_reminder" in calls and items and \
        abs((datetime.fromisoformat(items[0]["due"]) - (before + timedelta(minutes=20))).total_seconds()) < 120
    check("sets a reminder at the right time", bool(ok))
    ask(b, "Also remind me every weekday at 7 am to stretch.")
    items = storage.load(p.REMINDERS, [])
    check("sets a repeating reminder", any(r["repeat"] == "weekdays" and r["due"][11:16] == "07:00" for r in items))
    memory.remember("The user lives in Miami, Florida.", "personal", False)
    r = ask(new_brain(), "[Automatic morning briefing: the user just sat down at the PC. Greet them for the "
                         "morning and give a short spoken briefing: call briefing_info, get today's weather "
                         "with get_weather, mention today's reminders and to-dos, suggest an outfit from their "
                         "wardrobe for the weather, and any language words due. Keep it under 30 seconds.]")
    check("morning briefing uses briefing_info", "briefing_info" in calls)
    check("briefing mentions the laundry reminder", "laundry" in r.lower())
    check("briefing includes the weather", "get_weather" in calls)


def fashion_group():
    fashion.update_style_profile("body", "Height 6 ft 2.5 in (189 cm), weight 215 lb (97.5 kg).")
    wardrobe = [("black fitted tee with tan trim", "top", "black, tan"), ("grey relaxed sweatpants", "bottom", "grey"),
                ("dark wash straight jeans", "bottom", "dark blue"), ("white leather sneakers", "shoes", "white"),
                ("olive bomber jacket", "outerwear", "olive")]
    for name, cat, colors in wardrobe:
        fashion.add_to_wardrobe(name, cat, colors, "", False)
    b = new_brain()
    r = ask(b, "What size should I get in the Uniqlo Supima cotton crew neck t-shirt?")
    check("checks a size chart on the web", any(c in calls for c in ("web_search", "web_fetch")) or "get_style_profile" in calls)
    check("recommends a size", any(w in r for w in (" L", "XL", "large", "Large")))
    b = new_brain()
    ask(b, "Plan my outfits for tomorrow and the day after, from what I own. Tomorrow is church, the day after is the gym.")
    cal = fashion._load("calendar.json", {})
    check("plans outfits on the calendar", len(cal) >= 2)
    r = ask(new_brain(), "Shop my closet: give me two new outfit ideas using only what I already own. Just tell me, no previews.")
    check("shop my closet uses the wardrobe", "list_wardrobe" in calls and "bomber" in r.lower() + "jeans")


def phone():
    import base64

    tee = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tee.jpg"
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                       "data": base64.b64encode(tee.read_bytes()).decode()}}
    text = {"type": "text", "text": "[Sent from the user's phone via Telegram. Reply in plain text, no markdown; "
                                    "they may be away from the PC.] I'm in the store. What color is this shirt and "
                                    "would it go with dark jeans?"}
    r = ask(new_brain(), [img, text])
    check("understands a photo sent from the phone", "brown" in r.lower() or "olive" in r.lower() or "khaki" in r.lower())
    check("phone reply has no markdown", "**" not in r and "##" not in r)


def household():
    from core import users
    from tools import notes

    b = new_brain()
    r = ask(b, "My wife Marie is going to use you too. She likes to be called madame, in Kreyol madanm, "
               "and in French madame. Please add her to the household.")
    if "add_household_member" not in calls:
        r = ask(b, "Yes, go ahead and add her.")
    wife = users.find("Marie")
    check("adds the wife to the household", wife is not None and users.title(wife) == "madame")
    if not wife:
        return
    w = new_brain(wife)
    r = ask(w, "Hi Jarvis, remember that my favorite color is emerald green. And add plantains to the shopping list.")
    check("wife is addressed as madame", "madame" in r.lower())
    users.set_current(wife)
    shopping = notes.read_list("shopping", False)
    check("wife's item lands on the shared shopping list", "plantain" in shopping.lower())
    r = ask(new_brain(), "What's on our shopping list? And what's my favorite color?")
    check("husband sees the shared shopping list", "plantain" in r.lower())
    check("husband doesn't get the wife's private memory", "emerald" not in r.lower())
    r = ask(new_brain(wife), "What's my favorite color?")
    check("wife's private memory works for her", "emerald" in r.lower())


GROUPS = {"household": household, "core": core, "languages": languages, "proactive": proactive, "fashion": fashion_group, "phone": phone}

if __name__ == "__main__":
    chosen = sys.argv[1:] or list(GROUPS)
    for name in chosen:
        print(f"\n===== {name} =====")
        GROUPS[name]()
    data = storage.load_global("usage.json", {})
    month = next((v for k, v in data.items() if len(k) == 7), {})
    if month.get("requests", 0) > 2:  # a cache can only be reused from the second request on
        check("prompt caching is working", month.get("cache_read", 0) > 0)
    print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed. Cost of this run: ${usage.spent():.3f}")
    sys.exit(0 if all(ok for _, ok in results) else 1)
