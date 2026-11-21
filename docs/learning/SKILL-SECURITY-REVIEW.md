# Security and architecture review of the Skill SDK (commit 6ab2bb8) and its corrections

Reviewer: an independent pass over my own earlier work, treating the package as hostile and the first implementation as unproven. Everything below was
reproduced or tested; nothing is asserted from reading code alone. Test names are in `tests/test_skill_security.py`, `tests/test_skillsandbox_adversarial.py`
and `tests/test_skillkit.py`.

## Findings in 6ab2bb8 and what was done

| # | Severity | Finding in 6ab2bb8 | Correction |
|---|---|---|---|
| 1 | **Critical** | Package code ran in a plain child process with JARVIS's own OS rights (elevated in production). The only containment was Python-level (AST rules + an audit hook), which a determined author can defeat. | Package code now runs **only** inside JARVIS's existing Windows AppContainer broker (`core/project_sandbox.py`, the same one the Python dojo uses): zero-capability token, read-only lab, Job object (1 process, 256 MB, CPU cap, kill-on-close), two-variable environment, quota-limited pipes, token verified before the first instruction. The Python rules stay as a second layer. **Fails closed**: no AppContainer, no execution. |
| 2 | High | Approval bound only to the *parsed* manifest. `install()` re-read the path several times (review, quarantine reload, archive copy), so a file swapped between steps could be installed or archived while a different one was approved. | Package is read **once** (`skillpkg.snapshot`); fingerprint = sha256 of the exact `skill.json` bytes (which pin every other file); verification, registration and the archive all use those bytes. `test_swap_during_verification_cannot_change_what_gets_installed` swaps the file mid-verification and shows the installed source and archive are the approved bytes. |
| 3 | High | After install, only `source.py` was hash-checked. Edits to the stored `manifest.json` (reply text, invariants, triggers, params, recovery) were not detected. | `Installer.verify_installed` compares the stored entry to the archived approved package (fingerprint, source, and every routing/run/verify field) before each use and before rollback. Source, manifest and archive tampering each make the skill stop running (`test_tampering_with_an_installed_skill_...`). |
| 4 | High | Package-supplied `from_text` regexes run in the **host** process on every chat turn: a catastrophic-backtracking pattern could freeze JARVIS. | Static rejection of back-references, repeat-in-repeat and repeated alternations; length and group caps; input clipped to 400 characters; a timing probe on hostile inputs in a throwaway process (`> 0.25 s` rejects). |
| 5 | Medium | Approval accepted a 12-character prefix (48 bits). | 24 characters (96 bits) minimum; the approval must be a prefix of the full fingerprint. Quarantine folders use the same length. |
| 6 | Medium | Skill output went straight into the spoken answer and the conversation. | Output is treated as untrusted text: control and bidi characters stripped, 400-character clip, list joins capped. Success is only spoken when the host invariants hold. |
| 7 | Medium | A recovery rule could set a parameter outside its declared range. | Recovered parameters pass the same range check as the owner's. |
| 8 | Medium | Trigger words were unconstrained, so a package could claim "the" or "you" and hijack ordinary chat. | At least two word groups, no stop-words, no words under 3 letters, length caps; narrow exclusions advised. Installed skills are consulted **last** in both the chat front door and the model-side selector, so built-in routes always win. |
| 9 | Medium | Duplicate or case-colliding zip entries were silently overwritten. | Rejected. |
| 10 | Medium | Money was handled as a float-derived string. | New exact `decimal` parameter type (text, at most 2 places, range-checked); the example does integer-cent / `Decimal` arithmetic with an explicit half-up policy. 300 random inputs plus edge cases match an independent `Fraction` reference. |
| 11 | Low | The model-side selector put an installed skill ahead of the built-in routes. | Moved to last. |
| 12 | Low | Skill execution was one process per case. | One sandbox process per acceptance run (batch), with per-call timeouts. |

## Priority 2 — what an installed package cannot do (adversarial, real AppContainer, Python rules OFF)

Each probe is hostile code that only reports "denied/ALLOWED"; host-side sentinels and a host-side network listener give the ground truth.
A control step proves the listener works (an ordinary client is seen) so "nothing arrived" means something.

| Attack | Result |
|---|---|
| Read `.env`, `data/users.json`, `data/settings.json`, owner memory JSON, `~/.claude` files | **denied** |
| List the repo, `data/`, `data/users/`, the home folder, `~/.claude` | **denied** |
| Write / append / delete / rename / mkdir in the repo, `data/`, `%TEMP%`, home, the lab, the runtime, a user-owned victim folder | **denied** (sentinels do not exist; the victim file is intact) |
| TCP to a host loopback listener, TCP to the internet, DNS | **denied**; UDP `sendto` raises nothing inside the sandbox but the host receiver got **0** datagrams |
| `subprocess`, `os.system`, `startfile`, three child `Popen`s (fork bomb) | **denied**; the marker file the command would create does not exist |
| `OpenProcess` on the JARVIS-side process (to read its memory) | **denied** |
| Registry write under HKCU | **denied** (key absent afterwards) |
| Environment | only `LOCALAPPDATA`, `SystemRoot`, `WINDIR`, and the `TEMP`/`TMP` Windows adds for the package; no keys or tokens |
| 2 GB memory bomb, endless loop, 5 MB output flood | stopped / failed, host unaffected |
| Token | AppContainer, 0 capabilities, package identity verified, Job assigned before resume |
| Lab unusable | install and run **refuse** (no weaker mode) |

## Priority 5 — independent verification of lifecycle (not taken from the package's claims)

Activation, update, rollback, disable/enable, dry-run isolation (through the real `Brain.ask`, with the tool registry and model trapped), source/manifest/archive integrity, and
restart persistence (two fresh interpreters, and `scripts/skill_demo.py` with real separate processes) are all asserted by the host's own checks. The package's `judge.py` can
only veto; a package whose own judge says "all good" still fails when the host invariants or fuzzing disagree.

## Priority 6 — existing systems

No new registry: installed skills reuse `core/procedures.Library` (extended backward-compatibly: whole-word triggers, decimal/min/max checks, an `ACTIVE`/`DISABLED` pointer, number words).
The Blender two-bone skill's files are byte-identical before and after installs (`test_installed_skills_use_their_own_library...`), its routing is unchanged, and the full suite is green.

## Residual risks (not fixed, stated)

1. **Elevation.** All sandbox evidence was gathered from a non-elevated shell. An AppContainer token should be identical when launched from an elevated parent, but this was **not
   observed**. Run `python manage.py skill selftest` from JARVIS's elevated context before deployment (it prints `SANDBOX HOLDS` or fails).
2. **Kernel exploits** and any Windows AppContainer escape are out of scope for this layer.
3. **Authorship.** Hashes pin bytes, not authors. Approval of a fingerprint obtained out-of-band is the only defence against a swapped package; there are no signatures.
4. **Right invariants.** The judge proves behaviour against the package's stated invariants and cases plus host fuzzing; it cannot know whether those are the *right* rules. Owner review of `scope`/`verify`
   is part of the gate. The owner can add private cases (`extra_cases`).
5. **CPU time.** A skill can use one core until its timeout (5 s per call, 60 s per batch).
6. **Windows only.** The laptop cannot run installed skills (the tool is desk-only and nothing matches there).
7. **Test environment.** Tests share one AppContainer lab (stable temp path) to avoid piling up Windows profiles; production uses `data/skills/lab`.

## Recommendation

See the final report: GO for merging `claude/skill-sdk` after the elevated `skill selftest` passes in the real JARVIS context; production deployment of the feature itself needs separate authorization.
