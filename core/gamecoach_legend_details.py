"""Source-scoped additions to the existing 28-Legend reference, not another roster.

Publisher statements and tactical deductions stay distinct. Undocumented EVO
choices and numerical interactions are unknown, never filled by a model.
"""
from datetime import date
from core.gamecoach_meta import describe,get_legend,SEASON,PATCH_REVIEW_BY
from core.gamecoach_upgrades import upgrades,hotfixes,crosscheck_reference

GUIDE="https://help.ea.com/en/articles/apex-legends/abilities/"
MODES="https://help.ea.com/en/articles/apex-legends/game-modes/"
LAUNCH="https://www.ea.com/games/apex-legends/apex-legends/news/marked-patch-notes"

# Original concise paraphrases. Removed supply-bin perks in the older EA help
# guide are explicitly excluded using the newer dated Season 30 launch notes.
CLASS_PERKS={
 "assault":"Extra ammo/grenade capacity; faster weapon handling; temporary reload/movement boosts after shield break. Extended weapon supply bins were removed in the Aug 3 patch.",
 "controller":"Ring Console access; 25 extra shield HP inside the ring; remote retrieval of nearby placed equipment.",
 "recon":"Survey Beacon scans are faster and do not expose the scanner; repeated nearby position reveals. ADS threat vision is constrained by visibility and range.",
 "skirmisher":"Care Package scouting; faster movement with consumables; allies gain approach speed toward the Skirmisher.",
 "support":"Faster healing movement and revives; ally death boxes provide Mobile Respawn Beacons when the squad has Support. Extended support bins were removed in the Aug 3 patch.",
}
SECOND_PASSIVES={
 "Ash":"Dash: extra directional movement; upgraded double dash is mentioned by EA but exact upgrade level/effect is not confirmed here.",
 "Caustic":"Field Research: poisoning enemies and inspecting death boxes earns research upgrades; EA describes up to four simultaneous research upgrades.",
 "Gibraltar":"Momentum Boost: sustained maximum sprint grants extra speed; doors can be broken with one hit.",
 "Lifeline":"Combat Glide: use D.O.C. for a brief glide.",
}
# Counterplay is a tactical deduction from known kit constraints, not verified
# matchup statistics or an assertion about opponents in the current match.
COUNTERPLAY={
 "Ballistic":"Deny coordinated squad pressure using cover and separation of angles.",
 "Bangalore":"Avoid blind pushes through smoke; use cover outside artillery pressure.",
 "Fuse":"Leave clustered cover when explosives arrive; maintain a second escape lane.",
 "Mad Maggie":"Avoid staying on one drilled piece of cover; defend alternate angles.",
 "Revenant":"Avoid isolated duels during his Ultimate; force a squad crossfire.",
 "Alter":"Scout portal exits and expect return routes; do not chase blindly.",
 "Ash":"Watch one-way portal exits; avoid grouping where a snare can stop entry.",
 "Axle":"Watch gate destinations and destroy a visible drone when safe; gates can aid either team.",
 "Horizon":"Hold angles on predictable lift paths; keep a route away from the Ultimate.",
 "Octane":"Expect fast pursuit and pad landings; punish separation from his squad.",
 "Pathfinder":"Watch exposed zipline travel and grapple landing positions.",
 "Wraith":"Watch both portal ends; expect repositioning rather than assuming disappearance means retreat.",
 "Bloodhound":"Do not assume scans reveal all enemies; firing and damage affect cloak, with upgrade exceptions.",
 "Crypto":"Pressure a visible drone or an isolated operator; avoid stacking defensive equipment.",
 "Seer":"Move out of telegraphed detection areas and avoid relying on a single concealed position.",
 "Sparrow":"Avoid monitored sightlines; preserve an escape lane from electrical area denial.",
 "Valkyrie":"Watch exposed flight and launch preparation; cover anticipated landing routes.",
 "Vantage":"Break long sightlines and avoid repeat peeks after a sniper mark.",
 "Catalyst":"Use alternate entries around doors and spikes; do not assume another Catalyst is immune after Sep 14.",
 "Caustic":"Avoid confined gas-covered entries; scout barrels before committing.",
 "Rampart":"Deny a settled firing angle; pressure the setup from multiple safe angles.",
 "Wattson":"Identify fence entries and pylon coverage before committing utility.",
 "Conduit":"Avoid prolonged frontal trades into temporary shields; reposition around jammers.",
 "Gibraltar":"Avoid committing all pressure through one protected firing angle.",
 "Lifeline":"Prevent a free revive with a safe new angle rather than a blind push.",
 "Loba":"Watch bracelet destinations; contest positioning rather than assuming loot advantage wins fights.",
 "Mirage":"Confirm the visible player before shooting; do not equate a decoy with an enemy location.",
 "Newcastle":"Use multiple angles to contest protected recoveries without overextending.",
}

def details(name:str,mode:str="core",as_of:str|None=None)->dict:
    if mode not in {"core","ranked","unranked","wildcard"}:
        raise ValueError("Specify core, ranked, unranked or wildcard")
    base=describe(name)
    legend=get_legend(name)
    core=mode!="wildcard"
    today=date.fromisoformat(as_of) if as_of else date.today()
    evo=upgrades(name)
    return {**base,"mode":mode,"reference_season":SEASON,
        "class_perks":{"applicable":core,"summary":CLASS_PERKS[legend.cls] if core else
            "Core class perks do not apply in Wildcard.","sources":[GUIDE,MODES,LAUNCH]},
        "additional_passives":[{"summary":SECOND_PASSIVES[legend.name],"source":GUIDE}]
            if legend.name in SECOND_PASSIVES else [],
        "evo":evo if core else {"choices":[],"applicable":False,
            "note":"Wildcard EVO is not the Core blue/purple Legend upgrade system.","source":MODES},
        "dated_hotfixes":hotfixes(name),
        "unofficial_source_crosscheck":crosscheck_reference(name),
        "counterplay":{"advice":COUNTERPLAY[legend.name],"confidence":"tactical_deduction_not_matchup_tested"},
        "squad_synergy":"Existing terrain/style/team-role recommendations are qualitative; no measured win-rate claim.",
        "current_build_verified":False,"review_by":PATCH_REVIEW_BY,
        "reference_expired":today>=date.fromisoformat(PATCH_REVIEW_BY),
        "unknowns":["Complete current blue/purple upgrade tree","Unpublished cooldowns/ranges",
                    "Unverified later hotfix interactions"],
        "conflicts":(["EA help guide lists an old class here; prefer the current EA character roster."] if legend.name in {"Ash","Revenant"} else [])+
                    (["EA help still describes Catalyst spike immunity; Sep 14 patch removes it."] if legend.name=="Catalyst" else [])+
                    (["EA help supply-bin perk text is superseded by the Aug 3 patch."] if legend.cls in {"assault","support"} else [])}

def summary(name:str,mode:str="core")->str:
    d=details(name,mode)
    extra=" ".join(x["summary"] for x in d["additional_passives"])
    later=" ".join(f"EA hotfix {r['published_date']} (platform {r['platform_scope']}, not game-tested): {r['effect']} [{r['source']}]" for r in d["dated_hotfixes"])
    return (f"Mode {mode}. Class perks: {d['class_perks']['summary']} "
            f"Dated Season 30 reference; review deadline {d['review_by']}, expired: {d['reference_expired']}. "
            f"{later} Later dated hotfixes supersede older claims about the same property; console fixes must not be applied to PC. "
            f"{extra} Counterplay (tactical deduction): {d['counterplay']['advice']} "
            f"Source caveats: {' '.join(d['conflicts'])} "
            f"Class/mode sources: {GUIDE}, {MODES}. Full current EVO tree is unknown; "
            "no live readiness, cooldown or opponent location was observed.")

def focused(name:str,focus:str,mode:str="core")->str|None:
    """Deterministic narrow source reads avoid model mixing ability/card names."""
    key=''.join(c for c in focus.casefold() if c.isalnum())
    if key in {'all','latest'}:return None
    d=details(name,mode)
    if key in {'upgrades','upgradenames','evoupgrades'} and mode=='wildcard':
        return 'Core blue/purple Legend upgrades do not apply in Wildcard.'
    data=upgrades(d['legend'])
    if key in {'upgrades','upgradenames','evoupgrades'}:
        confirmed='; '.join(f"Level {x['level']} {x['name']}: {x['effect']}" for x in data['choices'])
        candidates=data['unofficial_crosscheck_choices']
        other='; '.join(x['name']+(': '+x['effect'] if key!='upgradenames' else '') for x in candidates)
        source=candidates[0]['source'] if candidates else ''
        if key=='upgradenames':
            return f"{d['legend']} unofficial cross-check upgrade names: {other or 'not cached'}. Blue/purple levels are unconfirmed by that source. Source: {source}"
        return (f"{d['legend']} upgrades. Dated EA details: {confirmed or 'not individually verified'}. "
                f"Unofficial cross-check upgrade names: {other or 'not cached'} [{source}]. "
                'The cross-check does not establish blue/purple levels; full current tree is not certified.')
    for row in data['choices']+data['unofficial_crosscheck_choices']:
        if ''.join(c for c in row['name'].casefold() if c.isalnum())==key:
            if mode=='wildcard':return 'Core blue/purple Legend upgrades do not apply in Wildcard.'
            level=f"Level {row['level']}" if row.get('level') else 'Level unconfirmed'
            date_label=row.get('verified_patch_date',row.get('source_date'))
            confidence='dated EA patch fact' if row.get('verified_patch_date') else 'unofficial cross-check, not current verified'
            return f"{d['legend']} — {row['name']} ({level}; {confidence}, {date_label}): {row['effect']} Source: {row['source']}"
    if key in {'abilities','baseabilities'}:
        return f"{d['legend']}, dated {d['patch']}: Passive {d['passive']}; Tactical {d['tactical']}; Ultimate {d['ultimate']}. Current build is not verified."
    if key in {'class','classperks'}:return d['class_perks']['summary']+f" Sources: {GUIDE}, {MODES}."
    if key=='counterplay':return 'Tactical deduction, not matchup-tested: '+d['counterplay']['advice']
    if key=='hotfixes':return ' '.join(x['published_date']+': '+x['effect']+' ['+x['source']+']' for x in d['dated_hotfixes']) or 'No reviewed hotfix snapshot for this Legend.'
    # A free-form property request such as "Nitro Gate speed" must fall back
    # to the dated source, rather than falsely claiming no evidence exists.
    return None

def requested_focus(name:str,focus:str)->str:
    """Use only actual ingress words when a model omits the optional focus.

    No approval context is changed and no action is inferred; this is a read.
    Desktop callers without ingress context retain their supplied focus.
    """
    from core import pcbridge
    said=pcbridge.original_message().casefold()
    if not said:return focus
    normalized=''.join(c for c in said if c.isalnum())
    data=upgrades(name)
    matches={''.join(c for c in x['name'].casefold() if c.isalnum()):x['name']
             for x in data['choices']+data['unofficial_crosscheck_choices']
             if ''.join(c for c in x['name'].casefold() if c.isalnum()) in normalized}
    if len(matches)==1:return next(iter(matches.values()))
    if 'upgrade' in said and 'abilit' not in said:
        return 'upgrade_names' if 'names' in said else 'upgrades'
    return focus
