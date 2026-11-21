"""Learned skills (core/skillspec) on this PC: list, approve, revoke, and the tab-bridge status.

    python scripts/skills.py list
    python scripts/skills.py status                 # is live JARVIS's tab bridge listening, is the Edge extension connected?
    python scripts/skills.py approve skill.close_site_tab   # OWNER ONLY: lets live JARVIS use that exact code
    python scripts/skills.py revoke skill.close_site_tab
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv):
    from core import browserbridge, skillspec
    cmd = argv[1] if len(argv) > 1 else "list"
    lib = skillspec.library()
    if cmd == "list":
        for sid in lib.ids():
            got = lib.get(sid)
            if not got:
                print(f"{sid}: disabled")
                continue
            m = got[0]
            ok = "APPROVED" if skillspec.approved_skill(sid) else "awaiting owner review"
            print(f"{sid} v{m['version']} sha {m['source_sha']}: {ok}; procedure by {m.get('procedure_by')}, primitives by "
                  f"{m.get('primitives_by')}; sealed {m['evidence']['passed']}/{m['evidence']['total']}, unseen {m['evidence'].get('unseen')}")
    elif cmd == "status":
        # live JARVIS owns the port; from here we can only see whether something answers on it
        folder = browserbridge._home() / "extension"
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{browserbridge.PORT}/poll", timeout=2)
            listening = True
        except urllib.error.HTTPError as e:
            listening = e.code == 403                 # the bridge refuses a caller without the token: it IS listening
        except OSError:
            listening = False
        try:
            live = json.loads((browserbridge._home() / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            live = {}
        print(json.dumps({"bridge_listening": listening, "port": browserbridge.PORT, "extension_folder": str(folder),
                          "extension_packaged": (folder / "config.json").exists(),
                          "edge_extension_connected": live.get("connected"), "reported_by_live_jarvis_at": live.get("at")}, indent=1))
    elif cmd == "approve" and len(argv) > 2:
        print(json.dumps(skillspec.approve(argv[2]), indent=1))
    elif cmd == "revoke" and len(argv) > 2:
        skillspec.revoke(argv[2])
        print("revoked")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
