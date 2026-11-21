"""Owner filesystem CLI: inspect/enable/disable the optional bounded study queue."""
import argparse
import json
from pathlib import Path
from core import tutorial_idle

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--enabled',choices=['yes','no'])
    p.add_argument('--topic')
    p.add_argument('--browser-runtime',type=Path)
    p.add_argument('--disable-browser',action='store_true')
    p.add_argument('--grant-site',type=Path)
    p.add_argument('--revoke-site')
    a=p.parse_args()
    from core import research_browser
    if a.disable_browser:
        from core import storage
        storage.save_global('research-browser.json',{})
    if a.browser_runtime:research_browser.configure(json.loads(a.browser_runtime.read_text()))
    if a.grant_site:print(json.dumps(research_browser.grant(json.loads(a.grant_site.read_text()))))
    if a.revoke_site:research_browser.revoke(a.revoke_site)
    print(json.dumps(tutorial_idle.configure(None if a.enabled is None else a.enabled=='yes',a.topic),indent=2))
