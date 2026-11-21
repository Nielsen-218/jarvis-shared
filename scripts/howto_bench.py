"""How-to mission, phase 1 (READ + PLAN): for real GitHub projects, would Jarvis plan the right install? READ-ONLY.

For each repository it asks the two planners Jarvis really uses, in DRY mode (nothing is downloaded, installed or run
except read-only lookups such as `winget list`):
  * the release pipeline (jarvis_pc.install: a Windows release asset, its checksum, the trust check), and
  * the README follower (core.screenguide.readme_text -> jarvis_pc.guide.plan: the commands in the README, tiered).
A repository counts as PLANNED RIGHT when either planner would install that project in a way a person would accept.
The expected answers were written down before running this (EXPECT), from each project's own install docs.

    python scripts/howto_bench.py            -> runs/howto-bench-<stamp>.json and .md
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# repo -> (what a right plan installs: release asset pattern OR an install command pattern from the README)
EXPECT = {
    "BurntSushi/ripgrep": (r"windows.*\.zip|msvc.*\.zip", r"winget install .*ripgrep|scoop install ripgrep|choco install ripgrep|cargo install ripgrep"),
    "sharkdp/bat": (r"windows.*\.zip|msvc.*\.zip", r"winget install .*bat\b|scoop install bat|choco install bat"),
    "sharkdp/fd": (r"windows.*\.zip|msvc.*\.zip", r"winget install .*fd\b|scoop install fd|choco install fd"),
    "junegunn/fzf": (r"windows.*\.zip", r"winget install .*fzf|scoop install fzf|choco install fzf"),
    "ajeetdsouza/zoxide": (r"windows.*\.zip|msvc.*\.zip", r"winget install .*zoxide|scoop install zoxide|choco install zoxide"),
    "jesseduffield/lazygit": (r"windows.*\.zip", r"winget install .*lazygit|scoop install lazygit|choco install lazygit"),
    "cli/cli": (r"windows.*\.(?:msi|zip)", r"winget install .*(?:gh|github\.cli)|scoop install gh|choco install gh"),
    "astral-sh/uv": (r"windows.*\.zip|msvc.*\.zip", r"winget install .*uv|irm .*install\.ps1|pip install uv|scoop install uv"),
    "yt-dlp/yt-dlp": (r"yt-dlp(?:_win)?\.(?:exe|zip)", r"winget install .*yt-dlp|pip install .*yt-dlp|scoop install yt-dlp|choco install yt-dlp"),
    "ollama/ollama": (r"OllamaSetup\.exe|windows.*\.zip", r"winget install .*ollama|irm .*install\.ps1"),
}


def release_plan(repo: str) -> dict:
    from tools import pcskills
    prepared = pcskills.skills().prepare_install("https://github.com/" + repo)
    plan = getattr(prepared, "plan", None)
    steps = getattr(plan, "steps", []) or []
    asset = next((s.url for s in steps if getattr(s, "kind", "") == "download"), "")
    return {"blocked": prepared.blocked or "", "method": getattr(plan, "method", ""), "asset": asset.rsplit("/", 1)[-1],
            "warnings": list(getattr(plan, "warnings", []) or [])[:3]}


def readme_plan(repo: str) -> dict:
    from core import screenguide
    from jarvis_pc import guide
    text = screenguide.install_section(screenguide.readme_text("https://github.com/" + repo))   # what install_repo follows
    if len(text) < 40:
        return {"readme": False, "blocked": "no README", "steps": []}
    from tools import pcskills
    command = screenguide.readme_install_command(text, "https://github.com/" + repo)
    if not command:                                       # install_repo then says so instead of guessing
        return {"readme": True, "chars": len(text), "blocked": "no Windows install command in the README", "steps": []}
    text = "```\n" + command + "\n```"                    # exactly what install_repo does when there is no release
    prepared =pcskills.skills().prepare_guide("install " + repo.split("/")[1], text)
    plan = getattr(prepared, "plan", None)
    steps = list(getattr(plan, "steps", []) or [])
    shown = prepared.blocked or prepared.summary
    return {"readme": True, "chars": len(text), "blocked": prepared.blocked or "",
            "steps": [s.command for s in steps][:20], "shown": shown[:1200]}


def main():
    stamp = time.strftime("%Y%m%d-%H%M")
    rows = []
    for repo, (asset_rx, cmd_rx) in EXPECT.items():
        t0 = time.monotonic()
        try:
            rel = release_plan(repo)
        except Exception as error:  # noqa: BLE001
            rel = {"blocked": f"{type(error).__name__}: {error}"[:200], "method": "", "asset": ""}
        try:
            rd = readme_plan(repo)
        except Exception as error:  # noqa: BLE001
            rd = {"readme": False, "blocked": f"{type(error).__name__}: {error}"[:200], "steps": []}
        rel_ok = not rel["blocked"] and bool(re.search(asset_rx, rel.get("asset", ""), re.I))
        planned_cmds = rd.get("shown", "") if not rd.get("blocked") else ""
        rd_ok = bool(planned_cmds) and bool(re.search(cmd_rx, planned_cmds, re.I))
        rows.append({"repo": repo, "release": rel, "readme": rd, "release_ok": rel_ok, "readme_ok": rd_ok,
                     "ok": rel_ok or rd_ok, "seconds": round(time.monotonic() - t0, 1)})
        r = rows[-1]
        print(f"{'PASS' if r['ok'] else 'FAIL'} {repo:24s} release[{'ok' if rel_ok else 'no'}: {rel.get('method') or '-'} "
              f"{rel.get('asset') or rel['blocked'][:60]}] readme[{'ok' if rd_ok else 'no'}: "
              f"{(rd.get('blocked') or str(len(rd.get('steps', []))) + ' steps')[:60]}] {r['seconds']}s", flush=True)
    n, ok = len(rows), sum(r["ok"] for r in rows)
    out = ROOT / "runs"
    out.mkdir(exist_ok=True)
    (out / f"howto-bench-{stamp}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    md = [f"# How-to bench {stamp} (read + plan only, nothing installed)", "",
          f"Planned right: {ok}/{n} (release pipeline {sum(r['release_ok'] for r in rows)}, README follower {sum(r['readme_ok'] for r in rows)}).", ""]
    for r in rows:
        md.append(f"- {'PASS' if r['ok'] else 'FAIL'} {r['repo']}: release {r['release'].get('method') or '-'} "
                  f"{r['release'].get('asset') or r['release']['blocked']}; README {r['readme'].get('blocked') or 'planned'}")
    (out / f"howto-bench-{stamp}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nplanned right {ok}/{n}")


if __name__ == "__main__":
    main()
