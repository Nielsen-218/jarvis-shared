"""Every Jarvis skill: loaded, validated at every depth, failing gracefully, and smoke-tested end to end.

Guards the skills audit (2026-10-23):
  * 7 skills (read_screen, pantry x4, dressing x2) were defined but never loaded, so the brain couldn't use them;
  * the registry only validated the top level of arguments, so one malformed call could store bad data and make
    later reads of it crash forever ('str' object has no attribute 'get');
  * dependency outages surfaced as raw exceptions ("URLError: <urlopen error ...>", "RuntimeError: ssh: ...");
  * a few skills raised (or went silent) on expected conditions instead of answering helpfully.
"""
import json
import re
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from tools import ALL, registry

ROOT = Path(__file__).resolve().parent.parent
TOOL_DEF = re.compile(r"@tool\((?:.|\n)*?\)\s*\n(?:@\w+\s*\n)*def\s+(\w+)")  # other decorators may sit under @tool


def _skill_modules() -> dict[str, list[str]]:
    out = {}
    for p in sorted((ROOT / "tools").glob("*.py")):
        names = TOOL_DEF.findall(p.read_text(encoding="utf-8"))
        if names:
            out[p.stem] = names
    return out


# -- every skill is loaded ---------------------------------------------------------------------------
def test_every_skill_module_is_in_load_all():
    missing = sorted(set(_skill_modules()) - set(ALL))
    assert not missing, f"tools modules with @tool skills that load_all() never imports (invisible to the brain): {missing}"


def test_load_all_registers_every_skill_exactly_once():
    defined = [n for names in _skill_modules().values() for n in names]
    assert len(defined) == len(set(defined)), "two skills share a name — the later one silently replaces the earlier"
    assert set(defined) <= set(registry.names()), sorted(set(defined) - set(registry.names()))


def test_the_free_screen_reader_is_available_to_the_brain():
    # look_at_screen (paid vision) tells the model to prefer read_screen first — it must actually exist.
    assert "read_screen" in registry.names() and "look_at_screen" in registry.names()
    for name in ("add_to_pantry", "whats_in_pantry", "add_receipt", "use_up_food", "style_outfit", "discover_outfit"):
        assert name in registry.names(), name


# -- arguments are validated at every depth -----------------------------------------------------------
def test_validator_rejects_malformed_nested_arguments_before_they_are_stored():
    bad = registry.validate("save_meal_plan", {"week_of": "2026-10-26", "days": ["Monday"]})
    assert bad and "days[0]" in bad and "object" in bad
    words = registry.validate("tutor_add_words", {"language": "French",
                                                  "words": [{"word": "bonjour", "translation": "hello"}]})
    assert words and "words[0]" in words and "example" in words          # a required nested field is missing


def test_validator_accepts_valid_nested_arguments():
    ok = registry.validate("tutor_add_words", {"language": "French",
                                               "words": [{"word": "bonjour", "translation": "hello",
                                                          "example": "Bonjour, ça va ?"}]})
    assert ok is None


def test_validator_type_rules():
    from tools.registry import _type_ok
    assert _type_ok(3.0, "integer") and not _type_ok(3.5, "integer")      # a model's 3.0 is still whole
    assert not _type_ok(True, "integer") and not _type_ok(True, "number")  # bools are never numbers
    assert _type_ok(2, "number") and _type_ok(2.5, "number")


def test_malformed_call_is_reported_not_crashed():
    result, is_error = registry.run("save_meal_plan", {"week_of": "2026-10-26", "days": ["Monday"]})
    assert is_error and result.startswith("Invalid call to save_meal_plan") and "days[0]" in result


# -- failures are explained, not dumped ---------------------------------------------------------------
def _err(name, exc):
    from tools.registry import _friendly_error
    return _friendly_error(name, exc)


def test_outages_name_the_service():
    msg = _err("get_weather", urllib.error.URLError(ConnectionRefusedError(10061, "refused")))
    assert msg.startswith("The weather service isn't reachable") and "URLError" not in msg.split("(")[0]
    assert _err("qbit_list", ConnectionRefusedError(10061, "x")).startswith("qBittorrent isn't reachable")  # no "QBittorrent"
    ssh = _err("homelab_status", RuntimeError("ssh: connect to host 192.168.1.5 port 22: Connection timed out"))
    assert ssh.startswith("The homelab Pi isn't reachable")


def test_credential_failures_say_so():
    msg = _err("homelab_status", RuntimeError("user@pi: Permission denied (publickey)."))
    assert "rejected Jarvis's login or key" in msg and "retrying won't help" in msg


def test_http_status_errors_are_explained():
    e = urllib.error.HTTPError("https://x", 503, "Service Unavailable", {}, None)
    assert "having problems on its end (HTTP 503)" in _err("get_weather", e)
    e401 = urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, None)
    assert "login or API key needs fixing" in _err("movie_info", e401)


def test_programming_errors_stay_visible():
    # A malformed URL also raises URLError — that's a bug, not an outage, and must not be dressed up as one.
    assert _err("get_weather", urllib.error.URLError("unknown url type: htp")).startswith("URLError:")
    assert _err("get_weather", KeyError("temp")).startswith("KeyError:")


# -- individual skills that used to raise or go silent -----------------------------------------------
def test_engineering_calc_guides_on_bad_input():
    from tools.engineering import engineering_calc
    assert "couldn't evaluate" in engineering_calc("banana", "")
    assert "divides by zero" in engineering_calc("1/0", "")
    incompatible = engineering_calc("12 V", "kg")
    assert "incompatible" in incompatible and "kg" in incompatible
    assert "isn't recognised" in engineering_calc("12 V", "flurbs")
    assert engineering_calc("12 V / 4 ohm", "") .startswith("12 V / 4 ohm = 3")   # the normal path still works


def test_garmin_sync_explains_when_not_connected(monkeypatch):
    from tools import fitness
    monkeypatch.setattr(fitness, "_garmin", lambda: (_ for _ in ()).throw(
        RuntimeError("Garmin isn't connected yet: double-click garmin-login.bat in the Jarvis folder once.")))
    out = fitness.garmin_sync(7)
    assert out.startswith("Garmin isn't connected yet") and "garmin-login.bat" in out

    class GarminConnectAuthenticationError(Exception):
        pass
    monkeypatch.setattr(fitness, "_garmin", lambda: (_ for _ in ()).throw(GarminConnectAuthenticationError("expired")))
    assert "login has expired" in fitness.garmin_sync(7)


def test_app_logs_and_read_web_pages_never_return_nothing(monkeypatch):
    from tools import homelab, reach
    monkeypatch.setattr(homelab, "ssh", lambda *a, **k: "")
    assert "hasn't written any log lines" in homelab.app_logs("jellyfin", 20)
    assert homelab.app_logs("!!!", 20).startswith("Which app?")
    from core import websearch
    monkeypatch.setattr(reach, "_exa", lambda *a, **k: "")
    # (the readers are stubbed so the suite never reaches the network: direct fetch and both Exa readers fail)
    monkeypatch.setattr(websearch, "open_page", lambda url: (_ for _ in ()).throw(
        websearch.PageUnavailable(url, [("direct", "no connection"), ("exa", "no readable text"), ("exa-direct", "no connection")])))
    assert "couldn't read anything" in reach.read_web_pages(["https://example.com"])


# -- background jobs always close the loop --------------------------------------------------------------
def _run_discover(monkeypatch, search):
    import threading

    from core import notify
    from tools import dressing, reach
    sent = []
    monkeypatch.setattr(notify, "notify", lambda title, body, *a, **k: sent.append((title, body)))
    monkeypatch.setattr(reach, "search_web_free", search)
    dressing.discover_async("black boots online", "owner")
    for t in threading.enumerate():
        if t.name == "discover":
            t.join(15)
    return dressing.discover_status(), sent


def test_discover_outfit_reports_a_search_outage_and_notifies(monkeypatch):
    """The voice reply promises pieces 'in about a minute'. If the web search is down, say THAT (not 'try
    different words') and send a notification — before, the person was left waiting for nothing."""
    def offline(*a, **k):
        raise RuntimeError("mcporter: connect to host failed")
    status, sent = _run_discover(monkeypatch, offline)
    assert status["state"] == "error" and "Couldn't reach the web search" in status["detail"]
    assert sent and sent[-1][0] == "Couldn't find that outfit online" and "black boots" in sent[-1][1]


def test_discover_outfit_with_no_usable_results_also_notifies(monkeypatch):
    status, sent = _run_discover(monkeypatch, lambda *a, **k: "no product links in here")
    assert status["state"] == "error" and "Try different words" in status["detail"]
    assert sent and sent[-1][0] == "Couldn't find that outfit online"


# -- the shopping route never plans from the middle of the country --------------------------------------
def test_shopping_route_without_a_home_says_so(monkeypatch):
    from tools import kitchen, travel
    monkeypatch.setattr(travel, "geocode", lambda *a, **k: [{"lat": "39.8", "lon": "-98.6"}])  # would be Kansas
    out = kitchen.shopping_route(["Walmart"])
    assert out.startswith("I can't plan the route yet") and "ZIP" in out


def test_store_addresses_keep_the_town_anywhere():
    from tools.kitchen import _street
    assert _street("Costco, 100, Main Street, Lakeside, Decatur, Illinois") == "100 Main Street, Decatur"
    assert _street("Walmart Supercenter, 1550, Skibo Road, Fayetteville, Cumberland County, North Carolina, 28303, "
                   "United States") == "1550 Skibo Road, Fayetteville"          # outside the Springfield area: town kept
    assert _street({"address": {"house_number": "9", "road": "Elm Street", "town": "Fort Leonard Wood"}}) == \
        "9 Elm Street, Fort Leonard Wood"


# -- what Jarvis says reads like English -----------------------------------------------------------
def test_plural_helper():
    from core.words import plural
    assert plural(1, "item") == "1 item" and plural(0, "item") == "0 items" and plural(2, "item") == "2 items"
    assert plural(1.0, "day") == "1 day" and plural(2.5, "hour") == "2.5 hours"
    assert plural(2, "person", "people") == "2 people" and plural(1, "new word") == "1 new word"


def test_spoken_counts_are_grammatical(monkeypatch):
    from tools import notes, pantry, tutor
    monkeypatch.setattr(pantry, "list_items", lambda: [{"name": "milk"}])
    assert "1 item" in pantry.add_to_pantry("milk") and "1 items" not in pantry.add_to_pantry("bread")
    assert "(s)" not in tutor.tutor_log_lesson("French", "greetings") and "Streak: 1 day." in tutor.tutor_log_lesson("French", "food")
    assert notes.update_list(["no-such-id"], "done") == "0 items marked done."


_COUNT_NOUN = re.compile(r"\{[^{}]*\}\s+(items|days|words|sessions|weeks|workouts|ingredients|steps|minutes|notes|"
                         r"meals|hours|clips|lessons|badges|watches|reminders|pieces|lines|timers|blocks|sets)\b")
# Reviewed exceptions: a word not a count / a branch that already handles 0 and 1 / a constant that is always >= 3.
_COUNT_NOUN_OK = ("No {category} items", 'else f"in {n} days"', "3 to {MAX_STEPS} steps")


def test_no_count_is_glued_to_a_hardcoded_plural():
    """The smoke run can't make every count equal 1 (that's how assign_stores' "Costco 1 items" slipped through),
    so check the source: a number in front of a fixed plural noun must go through core.words.plural()."""
    offenders = []
    for p in sorted((ROOT / "tools").glob("*.py")) + sorted((ROOT / "core").glob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or "plural(" in line or '"description"' in line or any(ok in line for ok in _COUNT_NOUN_OK):
                continue
            if _COUNT_NOUN.search(line):
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {s[:110]}")
    assert not offenders, "use core.words.plural() so a count of 1 reads right:\n" + "\n".join(offenders)


_SPOKEN_SLIP = re.compile(r"(?<!http)\(s\)|\b1 (items|days|words|sessions|weeks|workouts|ingredients|steps|minutes|"
                          r"notes|meals|hours|clips|lessons|badges|watches|reminders)\b")


# -- the online skills against the REAL services (opt-in: needs internet + the homelab) ---------------------
@pytest.mark.skipif(not __import__("os").getenv("JARVIS_LIVE_TESTS"), reason="set JARVIS_LIVE_TESTS=1 to hit real services")
def test_online_skills_return_correct_content_live(tmp_path):
    """Read-only skills (weather, maps, deals, movies, web, feeds, YouTube, archive.org, the Pi, Jellyfin,
    qBittorrent...) called for real; each output checked for the right content. See scripts/skill_live.py."""
    out = tmp_path / "live.json"
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "skill_live.py"), "--json", str(out)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, cwd=ROOT)
    report = json.loads(out.read_text(encoding="utf-8"))
    fails = {n: r["why"] for n, r in report.items() if isinstance(r, dict) and r.get("verdict") == "FAIL"}
    assert not fails, f"online skills returning wrong content:\n{json.dumps(fails, indent=1)}\n{proc.stdout[-1500:]}"


# -- the whole catalogue, end to end ------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="the desk skills are Windows-only")
def test_every_skill_runs_without_crashing(tmp_path):
    """Run all ~193 skills through registry.run in the safe sandbox (no network, no app launches, no real data,
    an unreachable homelab) and require that none crashes or hangs: each must answer or fail gracefully."""
    out = tmp_path / "smoke.json"
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "skill_smoke.py"), "--json", str(out)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, cwd=ROOT)
    report = json.loads(out.read_text(encoding="utf-8"))
    bad = {n: (s["status"], s["result"][:200]) for n, s in report["skills"].items() if s["status"] in ("crash", "timeout")}
    assert report["total"] >= 193, report["total"]
    assert not bad, f"skills that crashed or hung:\n{json.dumps(bad, indent=1)}"
    empty = [n for n, s in report["skills"].items() if s["status"] == "ok" and not s["result"].strip()]
    assert not empty, f"skills that returned nothing at all: {empty}"
    slips = {n: _SPOKEN_SLIP.search(s["result"]).group(0) for n, s in report["skills"].items()
             if _SPOKEN_SLIP.search(s["result"])}
    assert not slips, f"skills whose spoken result says e.g. '1 items' or 'item(s)': {slips}"
    touched = [n for n, s in report["skills"].items() if s.get("touched_input") is True]
    unsure = [n for n, s in report["skills"].items() if s.get("touched_input") == "inconclusive"]
    if unsure:    # he was using the PC too much to tell (scripts/skill_smoke._touches): the next gate run decides
        import warnings
        warnings.warn(f"input check inconclusive while the owner used the PC: {unsure}")
    assert not touched, f"skills whose real keyboard/mouse input reached the desktop (2026-11-08: reset the owner's idle clock): {touched}"
    assert proc.returncode == 0, proc.stdout[-2000:]


@pytest.mark.skipif(sys.platform != "win32", reason="the desk skills are Windows-only")
def test_the_smoke_run_never_reaches_the_real_speakers(tmp_path):
    """2026-11-11, owner: "every time Jarvis starts, it always put my volume down". It was this run: it called the
    volume skill with its sample value (3) on the real audio endpoint, so every test run before a deploy left his PC at
    3%. The volume skills must reach the sandbox's stand-in (which notes the call), never pycaw's real device."""
    out = tmp_path / "smoke.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "skill_smoke.py"), "--json", str(out), "--only", "volume"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, cwd=ROOT)
    skill = json.loads(out.read_text(encoding="utf-8"))["skills"]["volume"]
    assert skill["status"] == "ok" and skill["result"].startswith("Volume is"), skill
    assert any(a.startswith("audio: set volume") for a in skill["attempted"]), skill["attempted"]


@pytest.mark.skipif(sys.platform != "win32", reason="the desk skills are Windows-only")
def test_the_smoke_run_never_loads_the_real_ocr_models(tmp_path):
    """2026-11-13: the gate failed with "read_screen: no result after 20s" on a branch that never touched it. The smoke
    run loaded the real OCR models (14 s on a quiet PC), so it passed or failed with how busy the owner's PC was. The
    screen skill must reach the sandbox's stand-in (which notes the read) and answer at once."""
    out = tmp_path / "smoke.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "skill_smoke.py"), "--json", str(out), "--only", "read_screen"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, cwd=ROOT)
    skill = json.loads(out.read_text(encoding="utf-8"))["skills"]["read_screen"]
    assert skill["status"] == "ok", skill
    assert any(a.startswith("ocr: read") for a in skill["attempted"]), skill["attempted"]
    assert skill["seconds"] < 5, skill


@pytest.mark.parametrize("skill_types, he_types, quiet_moments, flagged", [
    (False, [True], True, False),                      # his typing during the first run only: the re-run is quiet
    (True, [False, False], True, True),                # the skill itself moves the clock, twice; the windows after stay quiet
    (False, [True, True, True], True, False),          # he keeps typing, but the skill's own re-run is quiet
    (True, [True, True, True], True, "inconclusive"),  # he types in every window after: no confirmation either way
    (True, [False, False], False, "inconclusive"),     # he never stops long enough to re-check at all
])
def test_his_own_typing_while_a_skill_runs_is_not_the_skill_touching_input(monkeypatch, skill_types, he_types,
                                                                           quiet_moments, flagged):
    """Gates run during his work flagged wait_for_window (an 8 s wait that sends nothing) and open_app, 2026-11-11:
    Windows' last-input clock also moves when HE types or moves the mouse. Each re-run waits for a quiet moment; one
    clean re-run clears the skill, two confirmed ones flag it, and too busy to tell is 'inconclusive', not a failure."""
    from scripts import skill_smoke
    from tools import registry
    clock = {"t": 0}
    after = iter(he_types)
    monkeypatch.setattr(skill_smoke, "_input_clock", lambda: clock["t"])
    monkeypatch.setattr(skill_smoke, "_quiet_for", lambda seconds, limit: quiet_moments)
    monkeypatch.setattr(skill_smoke, "_recheck", {"until": None})
    monkeypatch.setattr(skill_smoke.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + next(after, False)))

    def run(name, args):
        if skill_types:
            clock["t"] += 1
        return ("ok", False)
    monkeypatch.setattr(registry, "run", run)
    assert skill_smoke._touches("wait_for_window", {}, 8.0) == flagged
