"""Apex Season 30 (2026-09-15 onward) Legend and map reference for Nolagin.

28 Legends, six BR maps. Abilities/roles drawn from current official EA patch
notes and individual character descriptions, supplemented by cross-checked
2026 roster references. Short descriptions are original summaries.

Map fit scores are EXPLICITLY heuristic opportunities, not measured win rates.
No offscreen facts, automatic live map inference, picked ability ready state,
button binding, enemy location or cooldown should be asserted from this file.
Core map rotation is dated, not live. Recheck patch before next season.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Mapping

PATCH_AS_OF="2026-10-25"
SEASON="30: Marked, Split 2"
SOURCES={
 "legends":"https://senseos.gg/wiki/apex-legends/legends/",
 "abilities":"https://apexdb.gg/legends/",
 "official_hub":"https://www.ea.com/games/apex-legends/apex-legends/about/characters",
 "maps":"https://www.ea.com/en/games/apex-legends/apex-legends/maps-hub",
 "patch_aug":"https://www.ea.com/games/apex-legends/apex-legends/news/marked-patch-notes",
 "patch_sep":"https://www.ea.com/games/apex-legends/apex-legends/news/marked-midseason-patch-notes",
 "axle":"https://www.ea.com/ea-studios/ea-sports/news/apex-legends-s29-meet-axle",
}
CLASSES=frozenset(("assault","skirmisher","recon","controller","support"))
# Format: name | class | passive | tactical | ultimate | strengths | weaknesses | terrain tags
# Strengths/weaknesses describe tactical trade-offs, NOT official matchup stats.
_ROWS="""Ballistic|assault|Sling: carries a third weapon|Whistler: disrupts enemy weapon firing|Tempest: buffs nearby squad firepower|Weapon pressure and squad assault tempo|Gunfight ult value drops when teammates are far away|tight,buildings,cover
Bangalore|assault|Double Time: short sprint burst under fire|Smoke Launcher: obscures sightlines|Rolling Thunder: rolling artillery zone pressure|Crossing dangerous lanes and disengaging|Smoke also impairs friendly visibility; smoke can give enemies cover|open,rotation,cover
Fuse|assault|Grenadier: extra grenades and explosive resistance|Knuckle Cluster: explosive damage; base-kit Knuckle Jump|The Motherlode: fast explosive area bombardment|Dislodge entrenched squads and contest chokepoints|Self-exposure and poor placement squander explosives|tight,chokepoints,buildings
Mad Maggie|assault|Warlord's Ire: highlights damaged enemies and aids shotgun movement|Riot Drill: burns through cover|Wrecking Ball: displaces enemies and leaves speed pads|Breach room holds and press close fights|Hard to apply full pressure at long range|tight,buildings,chokepoints
Revenant|assault|Assassin's Instinct: low-health threat cues and enhanced climbing|Shadow Pounce: powered leap|Forged Shadows: protective damage-absorbing shroud|Aggressive isolated-target pursuit|Extended solo leaps can separate you from teammates|vertical,tight,buildings
Alter|skirmisher|Gift from the Rift: retrieves death-box items or banners remotely|Void Passage: short portal through surfaces|Void Nexus: return portal for squad reset|Creative building traversals and emergency squad resets|Portal destinations can be unsafe if not scouted|buildings,vertical,tight
Ash|skirmisher|Predator's Pursuit: recent death box intelligence and dash|Arc Snare: tether a target|Phase Breach: one-way portal for squad engagement|Catch retreating targets and make decisive pushes|One-way entries can strand a disorganized squad|vertical,chokepoints,tight
Axle|skirmisher|Drift: faster, steerable slides|Nitro Gate: boosts player sliding; also usable by enemies|Kickstart: target-seeking drone knocks/displaces enemies|Fast ground approaches and lateral pressure|Bad gate placement aids enemies; drone can affect allies and self|open,rotation,cover
Horizon|skirmisher|Spacewalk: soft landing and aerial steering|Gravity Lift: vertical lift for squad|Black Hole: pulls enemies toward a point|High-ground access and vertical fight resets|Predictable lift lines can expose users to fire|vertical,buildings,cover
Octane|skirmisher|Swift Mend: passive out-of-combat recovery|Stim: speed with health tradeoff|Launch Pad: team jump traversal|Rapid entry and chase / crossing space|Outrunning team support causes isolated deaths|rotation,open,vertical
Pathfinder|skirmisher|Insider Knowledge: map interactions charge/improve zipline|Grappling Hook: mobile grapple with charges|Zipline Gun: rapid team repositioning|Access elevated platforms and maintain tactical escape options|Grapple/ziplines expose predictable travel paths|vertical,rotation,open
Wraith|skirmisher|Voices from the Void: warns of danger|Into the Void: temporary safety while repositioning|Dimensional Rift: connected portals for squad|Safe disengagements and crossing exposed approaches|Phasing protects only for a limited window; portal ends can be camped|rotation,open,chokepoints
Bloodhound|recon|Tracker: clues, red trails and long-range white ravens|Eye of the Allfather: brief scan plus snapshots|Allfather's Cloak: squad cloaking area, speed and threat vision|Find nearby enemy routes and coordinate ambushes|Cloak can break on combat damage or firing; ring damage does not break it; upgrades modify firing behavior|cover,buildings,rotation
Crypto|recon|Neurolink: drone marks nearby detected enemies|Surveillance Drone: remote scouting and interactions|Drone EMP: shield pressure, slow and trap disruption|Pre-fight intel and dismantling defensive holds|Drone operation costs attention and can separate Crypto|open,chokepoints,buildings
Seer|recon|Heart Seeker: senses close heartbeats while aiming|Focus of Attention: delayed through-wall reveal|Exhibit: motion/shooting detection within dome|Close-quarters intel and pre-push enemy awareness|Reveal coverage is limited; tactical no longer adds turn slow|tight,buildings,chokepoints
Sparrow|recon|Double Jump: air mobility and extra arrows|Tracker Dart: line-of-sight detection|Stinger Bolt: slowing electrical area denial|Vertical recon and contest entry lanes|Dart only monitors its effective sightlines|vertical,chokepoints,buildings
Valkyrie|recon|VTOL Jets: tactical short flights, threat vision while airborne|Missile Swarm: rockets damage/disorient|Skyward Dive: long squad redeployment|Cross open rotations and scout heights|Jetpack flight creates exposed predictable movement; launch height nerfed|vertical,open,rotation
Vantage|recon|Spotter's Lens: enemy health and bullet drop visibility|Echo Relocation: leap to bat position|Sniper's Mark: marks target to amplify squad damage|Open sightline scouting and distant poke damage|Long sightlines weaken in confined buildings|open,long_range,vertical
Catalyst|controller|Barricade: reinforce doors and doorways|Piercing Spikes: place denial and door reinforcement|Dark Veil: fast deployable sightline wall|Lock down buildings and create safer crossings|Spikes and walls require placement; enemy Catalysts also affect her|buildings,tight,chokepoints
Caustic|controller|Nox Vision / Field Research: threats in gas; upgrade gains|Nox Gas Trap: proximity/triggered gas traps|Nox Gas Grenade: broad gas denial|Punish pushes into confined rooms and choke holds|Traps give less value in open spaces with several exits|buildings,tight,chokepoints
Rampart|controller|Modded Loader: improved LMG handling and hop-up progress|Amped Cover: fortified one-way firing setup|Mobile Minigun Sheila: portable/deployed heavy firepower|Build strong firing positions and control approach angles|Stationary setup can be flanked and repositioning is costly|buildings,chokepoints,cover
Wattson|controller|Spark of Genius: fully charges ultimate with accelerants|Perimeter Security: stun/damage fences and doors|Interception Pylon: protects against ordnance and restores shields|Hold rooms and survive enemy grenade pressure|Setup needs time; exposed open-area fences are bypassable|buildings,tight,chokepoints
Conduit|support|Savior's Speed: faster toward distant teammates|Radiant Transfer: temporary shields for allies and self, two charges|Energy Barricade: quick deployed jammer line, can aim forward|Heal shield deficits and discourage enemy pushes|Temporary shields are not permanent HP; jammers need correct terrain|cover,buildings,chokepoints
Gibraltar|support|Gun Shield / Momentum Boost: ADS shield and sprint speed burst|Dome of Protection: shot-blocking dome|Defensive Bombardment: controlled area mortar strike|Protect and rescue squad during exposed fights|Dome blocks shots both ways; dome edges can be pushed|open,cover,chokepoints
Lifeline|support|Combat Glide and D.O.C. revive: glide and hands-free allied revives|D.O.C. Heal Drone: heals nearby ally|D.O.C. Halo: protective fast-healing field|Recover teammates without stopping all offensive pressure|Revive/healing positions can be rushed or surrounded|cover,buildings,tight
Loba|support|Eye for Quality: sees rare nearby loot|Burglar's Best Friend: bracelet teleport|Black Market Boutique: nearby team looting and access|Reliable loot access and personal escapes|Bracelet travel/landing can be punished|rotation,open,buildings
Mirage|support|Now You See Me: concealed revive and respawn|Psyche Out: decoy clone|Life of the Party: moving clone crowd|Win fights through confusion and revive concealment|Experienced opponents may identify real player from motion|tight,buildings,cover
Newcastle|support|Retrieve the Wounded: shielded dragged teammate revive|Mobile Shield: repositionable protective barrier|Castle Wall: jump to ally/spot and construct a wall|Save allies and establish cover in open ground|Wall and shield still need viable angles; can be flanked|open,cover,chokepoints
"""
# 28 names and 6 maps are a specific S30 snapshot; never implicitly expand.
FEATURES=frozenset(("vertical","open","rotation","chokepoints","long_range",
                   "buildings","cover","tight"))
@dataclass(frozen=True)
class Legend:
    name:str
    cls:str
    passive:str
    tactical:str
    ultimate:str
    strengths:str
    weaknesses:str
    terrain:frozenset[str]
    sources:tuple[str,...]=(SOURCES["legends"],SOURCES["patch_sep"])

LEGENDS:dict[str,Legend]={}
for line in _ROWS.splitlines():
    if not line.strip():continue
    name,role,passive,tactical,ultimate,good,bad,tags=line.split("|")
    if name in LEGENDS:raise RuntimeError("Duplicate legend")
    LEGENDS[name]=Legend(name,role,passive,tactical,ultimate,good,bad,
                         frozenset(tags.split(",")))

@dataclass(frozen=True)
class Map:
    name:str
    terrain:frozenset[str]
    rotation_core:bool
    notes:str
    source_url:str=SOURCES["maps"]

# Qualitative POI terrain descriptors, not claims that *everywhere* has the trait.
MAPS={
 "World's Edge":Map("World's Edge",frozenset(("vertical","buildings","chokepoints","cover","rotation")),True,
      "Reworked Sorting Factory/Fragment plus vertical ziplines and cover; POIs vary"),
 "Olympus":Map("Olympus",frozenset(("open","rotation","vertical","long_range","cover")),True,
      "Exposed connecting lanes with elevated positions and varied interiors"),
 "Broken Moon":Map("Broken Moon",frozenset(("rotation","open","long_range","vertical","chokepoints")),True,
      "Broad rotations and varied sightlines; many contested crossing routes"),
 "Kings Canyon":Map("Kings Canyon",frozenset(("tight","buildings","chokepoints","vertical","cover")),False,
      "Dense POIs and constrained approach lanes; Wildcard can use variant"),
 "Storm Point":Map("Storm Point",frozenset(("open","long_range","rotation","vertical","cover")),False,
      "Broad spaces with long approaches, high ground and exposed rotations"),
 "E-District":Map("E-District",frozenset(("buildings","vertical","tight","cover","chokepoints")),False,
      "Multi-level urban buildings and closer interior transitions"),
}
ROTATION_ANNOUNCED="2026-09-14"
PATCH_REVIEW_BY="2026-11-03"
MODES=frozenset(("ranked","pubs","core"))
ALIASES={"worlds edge":"World's Edge","world's edge":"World's Edge",
         "edistrict":"E-District","e district":"E-District",
         "king's canyon":"Kings Canyon"}
def get_legend(name:str)->Legend:
    if not isinstance(name,str):raise ValueError("Legend name required")
    try:return next(v for key,v in LEGENDS.items() if key.casefold()==name.strip().casefold())
    except StopIteration:
        # Small local models sometimes put an exact ability name in the Legend
        # argument. Resolve only a unique known kit name, never a guessed pick.
        candidates=[v for v in LEGENDS.values() if any(
            part.split(':',1)[0].casefold()==name.strip().casefold()
            for part in (v.passive,v.tactical,v.ultimate))]
        if len(candidates)==1:return candidates[0]
        raise ValueError("Legend not in Season 30 roster")

def get_map(name:str)->Map:
    if not isinstance(name,str):raise ValueError("Map name required")
    key=ALIASES.get(name.strip().casefold(),name.strip())
    for n,v in MAPS.items():
        if n.casefold()==key.casefold():return v
    raise ValueError("Unknown Apex Battle Royale map")

def rotation(mode:str="ranked",as_of:str|None=None)->dict:
    if mode not in MODES:raise ValueError("Core-only 2026-09 map rotation")
    if as_of is None:as_of=date.today().isoformat()
    if not isinstance(as_of,str):raise ValueError("Provide ISO date")
    day=date.fromisoformat(as_of)
    safe=date.fromisoformat(ROTATION_ANNOUNCED)<=day<=date.fromisoformat(PATCH_REVIEW_BY)
    return {"season":SEASON,"patch_date":ROTATION_ANNOUNCED,
            "is_currently_verified":safe,
            "map_names":[m.name for m in MAPS.values() if m.rotation_core] if safe else [],
            "source":SOURCES["patch_sep"],
            "caution":"Published split rotation, not live map timer; recheck after next season."}

def _style_bonus(legend:Legend,style:str)->int:
    if style not in {"mnk_aggressive","mnk_balanced","mnk_defensive","controller"}:
        raise ValueError("Choose a known player style")
    if style=="mnk_aggressive":
        return 2 if legend.cls in {"skirmisher","assault"} else 0
    if style=="mnk_defensive":
        return 2 if legend.cls in {"controller","support"} else 0
    return 1 if legend.cls in {"recon","support"} else 0

def recommend(map_name:str,style:str="mnk_aggressive",squad:tuple[str,...]=(),
              limit:int=5,as_of:str|None=None)->dict:
    """Rank candidate playstyle opportunities; never claim verified win rate.

    Squad names are GIVEN by player; do not infer team from an unseen screen.
    Scores are descriptive feature matches, not tournament pick analytics.
    """
    if as_of is None:as_of=date.today().isoformat()
    target=get_map(map_name)
    if not 1<=limit<=28:raise ValueError("Invalid result count")
    roster=[get_legend(x) for x in squad]
    if len(set(x.name for x in roster))!=len(roster) or len(roster)>2:
        raise ValueError("Up to two DISTINCT known squadmates")
    others={x.name for x in roster}
    class_in_team={x.cls for x in roster}
    picks=[]
    for legend in LEGENDS.values():
        if legend.name in others:continue
        matched=legend.terrain & target.terrain
        bonus=_style_bonus(legend,style)
        diversity=1 if legend.cls not in class_in_team else 0
        # Score is *explanation aid*, NOT empirical legend-map effectiveness.
        score=2*len(matched)+bonus+diversity
        picks.append({"legend":legend.name,"class":legend.cls,"heuristic_score":score,
                      "map_features_matched":sorted(matched),"strength":legend.strengths,
                      "tradeoff":legend.weaknesses,
                      "source":SOURCES["legends"]})
    picks.sort(key=lambda x:(-x["heuristic_score"],x["legend"]))
    rotation_info=rotation(as_of=as_of)
    return {"map":target.name,"map_notes":target.notes,"preferred_input":"mnk"
            if style.startswith("mnk") else "controller",
            "style":style,"season":SEASON,"as_of":as_of,
            "core_rotation_announced":rotation_info["map_names"],
            "is_map_in_published_core_rotation":target.rotation_core if
                    rotation_info["is_currently_verified"] else None,
            "method":"qualitative terrain+style+team-role fit, NOT measured win percentages",
            "recommendations":picks[:limit],
            "warnings":[
                "POI-specific cover, squad abilities, map mode and your skill change matchup.",
                "No current map, Legend selection, ult ready state or enemy position was observed.",
                "Patch-dependent details should be rechecked after "+PATCH_REVIEW_BY,
            ]}

def describe(name:str)->dict:
    v=get_legend(name)
    return {"legend":v.name,"class":v.cls,"passive":v.passive,
            "tactical":v.tactical,"ultimate":v.ultimate,
            "strengths":v.strengths,"weaknesses":v.weaknesses,
            "map_terrain_features":sorted(v.terrain),
            "patch":SEASON,"sources":list(v.sources)}

def validate_reference()->None:
    assert len(LEGENDS)==28 and len(MAPS)==6
    assert all(v.cls in CLASSES and v.terrain<=FEATURES for v in LEGENDS.values())
    assert all(m.terrain<=FEATURES for m in MAPS.values())
    assert sum(m.rotation_core for m in MAPS.values())==3
validate_reference()
