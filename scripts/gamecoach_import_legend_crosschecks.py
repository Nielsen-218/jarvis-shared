"""Import explicitly unofficial public reader snapshots into existing Apex data.

No downloads, model calls or promotion of third-party claims to verified facts.
The owner/developer supplies saved readable pages; blue/purple levels remain
unknown unless independently established by the existing official data.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import storage
from core.gamecoach_meta import LEGENDS,get_legend
from core.gamecoach_upgrades import CROSSCHECK_STORE

def parse(name:str,text:str)->dict:
    title=get_legend(name).name
    slug=title.lower().replace(' ','-')
    url=f'https://apex.source.gs/legends/{slug}/'
    if len(text)>2_000_000 or f'URL Source: {url}' not in text:
        raise ValueError('Expected bounded readable source with matching URL')
    if 'Warning: Target URL returned error' in text or '## Upgrades' not in text:
        raise ValueError('Source unavailable or missing upgrade section')
    body=text.split('## Upgrades',1)[1].split('\n## ',1)[0]
    build=re.search(r'build\s+(\d+)\s*·\s*updated\s+(\d{4}-\d{2}-\d{2})',text)
    if not build:raise ValueError('Explicit dated build metadata required')
    def cards(section):
        result=[]
        for card in re.split(r'^### ',section,flags=re.M)[1:]:
            lines=[line.strip() for line in card.splitlines() if line.strip() and not line.strip().startswith(('![','[![','Base recharge is'))]
            if len(lines)<2:raise ValueError('Unrecognized source card')
            kind=lines[1] if lines[1] in {'Passive','Tactical','Ultimate'} else None
            effect=' '.join(lines[2:] if kind or lines[1]==lines[0] else lines[1:])
            if len(lines[0])>90 or not 3<=len(effect)<=800:raise ValueError('Unexpected source content')
            result.append({'name':lines[0],'ability_type':kind.lower() if kind else None,'effect':effect,
                'level':None,'source':url,'source_date':build.group(2),'claimed_build':build.group(1),
                'verification':'unofficial_game_file_crosscheck_not_current_game_verified'})
        return result
    choices=cards(body)
    abilities=cards(text.split('## Abilities',1)[1].split('\n## ',1)[0]) if '## Abilities' in text else []
    if len(abilities)>6:raise ValueError('Unexpected ability count')
    perks=re.search(r'^## [^\n]+ class perks\n(.*?)(?=\n## |\Z)',text,re.M|re.S)
    class_claims=[line.strip() for line in perks.group(1).splitlines() if line.strip() and not line.strip().startswith(('![','[!['))] if perks else []
    if len(class_claims)>10:raise ValueError('Unexpected class claims')
    if not 3<=len(choices)<=6 or len({x['name'] for x in choices})!=len(choices):
        raise ValueError('Incomplete or duplicated source cards')
    return {'legend':title,'source':url,'source_representation_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'choices':choices,'abilities':abilities,'class_perk_claims':class_claims,'complete_current_tree_verified':False}

def main()->int:
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);args=p.parse_args()
    result=storage.load_global(CROSSCHECK_STORE,{'legends':{}})
    failures={}
    for name in LEGENDS:
        file=args.directory/(name.lower().replace(' ','-')+'.txt')
        try:row=parse(name,file.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:failures[name]=type(exc).__name__;continue
        result.setdefault('legends',{})[name]=row
    storage.save_global(CROSSCHECK_STORE,result)
    print(json.dumps({'cached_legends':len(result['legends']),'candidate_choices':sum(len(x['choices']) for x in result['legends'].values()),'failures':failures,'verified_current_trees':0}))
    return 0 if not failures else 2

if __name__=='__main__':raise SystemExit(main())
