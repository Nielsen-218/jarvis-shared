"""Dated, source-confirmed Season 30 EVO upgrade changes.

This is NOT a complete current upgrade tree for all 28 Legends. Unknown choices
must remain unknown until verified; do not recycle old public guides. These
specific patch notes are from EA's August 3 and September 14, 2026 updates.
"""
from __future__ import annotations
from core.gamecoach_meta import get_legend, LEGENDS
from core import storage

CROSSCHECK_STORE="apex-legend-crosschecks.json"

def crosscheck_reference(name:str)->dict:
    """Private, explicitly unofficial source snapshots never upgrade confidence."""
    title=get_legend(name).name
    row=storage.load_global(CROSSCHECK_STORE,{}).get('legends',{}).get(title,{})
    return row if isinstance(row,dict) else {}

def crosscheck_choices(name:str)->list[dict]:
    return crosscheck_reference(name).get('choices',[])

SOURCE="https://www.ea.com/games/apex-legends/apex-legends/news/marked-midseason-patch-notes"
DATE="2026-09-14"

# Reviewed official staff posts; the unattended PC fetch currently gets HTTP403.
# These dated snapshots must not be represented as a successful live feed scan.
HOTFIXES=[
 {"published_date":"2026-10-01","legends":["Axle"],"platform_scope":"not_specified",
  "source":"https://forums.ea.com/blog/apex-legends-game-info-hub-en/apex-legends-latest-update---011026/13764756",
  "effect":"Slide steering reduced 50% for the passive and 25% for Nitro Gate users. Nitro Gate disables aim assist and initial speed drops from 700 to 650. Kickstart maximum knock-up drops to 650; chase duration drops from 10 to 8 seconds; immediate lock-on yields a smaller launch."},
 {"published_date":"2026-10-01","legends":["Alter"],"platform_scope":"not_specified",
  "source":"https://forums.ea.com/blog/apex-legends-game-info-hub-en/apex-legends-latest-update---011026/13764756",
  "effect":"EA reports a fix for unreliable Tactical/Ultimate behavior near large moving structures."},
 {"published_date":"2026-10-07","legends":["Alter"],"platform_scope":"not_specified",
  "source":"https://forums.ea.com/blog/apex-legends-game-info-hub-en/apex-legends-latest-update-07102026/13781292",
  "effect":"EA reports a fix for Ultimate teleports failing in cramped locations."},
 {"published_date":"2026-10-08","legends":["Ash","Sparrow","Revenant","Lifeline"],"platform_scope":"console",
  "source":"https://forums.ea.com/blog/apex-legends-game-info-hub-en/apex-legends-latest-update---8102026/13783962",
  "effect":"Console-only movement fixes cover Ash dashes/jumps, Sparrow jumps, Revenant climbing/mantling and Lifeline running animation. This post does not establish a PC movement change."},
]

def hotfixes(name:str,platform:str="pc")->list[dict]:
    title=get_legend(name).name
    if platform not in {"pc","console"}:raise ValueError("Unknown platform")
    return [{**row,"applies_to_requested_platform":False if row["platform_scope"]=="console" and platform=="pc" else None,
             "verification":"reviewed_official_staff_text_not_game_tested"}
            for row in sorted(HOTFIXES,key=lambda x:x["published_date"],reverse=True) if title in row["legends"]]

# Short paraphrases of individually named EA patch-note upgrade updates.
# The exact values below appear in that dated official note.
VERIFIED={
 "Bloodhound":{
    "_source":"https://www.ea.com/games/apex-legends/apex-legends/news/marked-patch-notes",
    "_date":"2026-08-03",
    2:{"Hidden Sight":"Ultimate refreshes Tactical; Tactical scan visuals are hidden while cloaked.",
       "Bird's Eye":"White ravens split toward the nearest two enemies on different squads."},
    3:{"True Predator":"Firing briefly interrupts cloak instead of ending it."},
    "retired":["Taste of Blood","Raven's Flight (integrated into base kit)"]},
 "Loba":{
    "_source":"https://www.ea.com/games/apex-legends/apex-legends/news/marked-patch-notes",
    "_date":"2026-08-03",
    3:{"Brand New Bag":"Provides purple backpacks; gold backpacks were removed."}},
 "Rampart":{
    "_source":"https://www.ea.com/games/apex-legends/apex-legends/news/marked-patch-notes",
    "_date":"2026-08-03",
    2:{"Combat Reserve":"Adds reloading while stowed; red weapon access removed. Other effects not established here."}},
 "Valkyrie":{
    "_source":"https://www.ea.com/games/apex-legends/apex-legends/news/marked-patch-notes",
    "_date":"2026-08-03",
    2:{"Eye In the Sky":"Through-wall scan range becomes 100m; line-of-sight scan remains 250m."},
    3:{"Supersonic":"Launch time is reduced 40%, previously 50%."}},
 "Axle":{
    "_source":"https://www.ea.com/games/apex-legends/apex-legends/news/marked-patch-notes",
    "_date":"2026-08-03",
    2:{"Long Haul":"Restores maximum boosted slide duration to 5 seconds."},
    "retired":["Automata"]},
 "Seer":{
    "_source":"https://www.ea.com/games/apex-legends/apex-legends/news/marked-patch-notes",
    "_date":"2026-08-03",
    2:{"Energizing Focus":"Shield regeneration on Tactical hit; detailed amount not verified here."},
    3:{"Home Base":"Tactical cooldown becomes 8 seconds inside the Ultimate zone (modifier 2.5)."}},
 "Conduit":{
    2:{
      "Concurrency":"Refire Energy Barricade once within 10 seconds with no cooldown; two fewer jammers, packed closer.",
      "Jammed Up":"Barricade jammer radius increased 25%; enemies damaged by them have their HUD jammed for 10 seconds.",
    },
    "retired":["Battpack","Bigger Jam"]},
 "Fuse":{
    2:{
      "Pyro Techniques":"Persistent flame radius doubled.",
      "Reignition":"Ultimate recharges from explosive damage at 1 second per 3 explosive damage.",
    },
    3:{
      "Big Bang":"Knuckle Cluster explosions cover 50% more area.",
      "Sticky Bomb":"The initial projectile is 25% larger, sticks to enemies; every 7 enemy hits reduces cooldown by 1 second.",
    },
    "retired":["Knuckle Jumper","Wreckless"]},
 "Catalyst":{
    2:{
      "Ferro-Door":"Maximum reinforced doors increases from 4 to 6.",
      "Resilient Veil":"Ultimate cooldown decreases from 150 seconds to 120 seconds.",
    },
    3:{
      "Ferro-Frenzy":"One additional Spike placement; spikes are 50% smaller but tick damage faster.",
      "Hidden Spikes":"Moved from level 2 to level 3; other effects require separate verification.",
    },
    "retired":["Sole Piercer"]},
}
def upgrades(name:str)->dict:
    """Return only patch-verified choices, never invented missing options."""
    legend=get_legend(name)
    known=VERIFIED.get(legend.name,{})
    choices=[]
    for level in (2,3):
        for upgrade,effect in known.get(level,{}).items():
            choices.append({"level":level,"name":upgrade,"effect":effect,
                            "verified_patch_date":known.get("_date",DATE),"source":known.get("_source",SOURCE)})
    return {"legend":legend.name,"verified_patch_date":known.get("_date",DATE) if choices else None,
            "choices":choices,"retired_in_patch":known.get("retired",[]),
            "unofficial_crosscheck_choices":crosscheck_choices(name),
            "unknown_levels":[level for level in (2,3) if len(known.get(level,{}))<2],
            "complete_current_tree_verified":False,
            "caution":"Verified in Sep 2026 notes only. Check newer EA hotfixes and game build."}
def upgrade_summary(name:str)->str:
    data=upgrades(name)
    candidates=data['unofficial_crosscheck_choices']
    cross=(" Unofficial game-file cross-check, NOT confirmed current; source does not establish blue/purple levels: "+
           "; ".join(x['name']+": "+x['effect'] for x in candidates[:5])+
           f". Source: {candidates[0]['source']} (snapshot {candidates[0]['source_date']}). Official dated notes take precedence.") if candidates else ""
    if not data["choices"]:
        return ("EVO upgrade options: not yet verified from current official "
                "sources; don't invent their effects or levels."+cross)
    parts=[f"Level {x['level']} {x['name']}: {x['effect']}"
           for x in data["choices"]]
    return (f"Confirmed changes in EA patch {data['verified_patch_date']}: "+"; ".join(parts)
            +". The full current upgrade tree is NOT verified. "
            +"Source: "+data["choices"][0]["source"]+cross)
