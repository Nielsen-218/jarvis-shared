# Installing a skill — owner's guide

You need a skill package: a `.jskill` file (or a folder) that someone, or any AI assistant, made following `skillkit/AUTHORING.md`. Open a terminal in the JARVIS folder.

**1. Look before you trust.** This runs none of the package's code:
```bash
.venv\Scripts\python.exe manage.py skill review path\to\util.split_bill-1.1.0.jskill
```
Read: the *scope* (what it claims to do), *permissions requested* (only `compute` can ever be granted), *imports*, and any `PROBLEM` lines. If it says REJECTED, stop; it cannot be installed.
Note the **fingerprint**. If a person or AI sent you the package, ask them for the fingerprint separately; if the two differ, the file was changed on the way.

**2. Install exactly that package.**
```bash
.venv\Scripts\python.exe manage.py skill install path\to\util.split_bill-1.1.0.jskill --approve <first 24+ characters of the fingerprint>
```
(quote at least the first 24 characters of the fingerprint you reviewed). JARVIS now runs the package inside the Windows AppContainer sandbox against his own tests, not the author's. If it prints
`Installed ... N/N independent checks passed`, it is active. If anything fails, **nothing is installed and nothing existing changes**; the message says why.

**3. Use it.** Just talk: *"split a $87.50 dinner bill 3 ways with 18% tip"*. You never name the skill. If something essential is missing JARVIS asks (*"How many people are splitting it?"*).
The first time, try a safe check: start a message with `[dry-run]` (e.g. `[dry-run] split the $90 bill among 3 people`) and JARVIS only says what he *would* do.

**4. Manage.**
```bash
.venv\Scripts\python.exe manage.py skill list
.venv\Scripts\python.exe manage.py skill disable util.split_bill     # off at once; enable puts it back
.venv\Scripts\python.exe manage.py skill rollback util.split_bill    # go back to the previous version after an update
.venv\Scripts\python.exe manage.py skill history util.split_bill     # every review, install, run and failure
```
To update, install a package of the same id with a higher version the same way (steps 1–2). The old version stays for rollback.

**Check the sandbox yourself** (from the same kind of shell JARVIS runs in): `.venv\Scripts\python.exe manage.py skill selftest` must end with `SANDBOX HOLDS`.

**What installing can and cannot do.** A skill is pure calculation. It cannot touch files, the network, apps, Blender, your PC or paid services, and installing never changes that.
Skills that need those abilities are refused for now. Your data is in `data/skills/` (or `JARVIS_SKILLS_DIR`); delete nothing there by hand.

**Applying this to the live JARVIS** is a deployment step: the running app only knows about this feature after the branch is merged and JARVIS is restarted (not done yet; needs your authorisation).
