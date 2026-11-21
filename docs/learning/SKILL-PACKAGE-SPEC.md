# JARVIS skill package specification — `jarvis-skill/1`

A skill package is how any developer or any capable AI hands JARVIS a new, verifiable ability. The format names no model and no vendor;
the installer (`core/skillinstall.py`) reads it, and nothing in it is trusted merely because the package says so.

## Layout (a folder, or the same files zipped as `<id>-<version>.jskill`)

```
skill.json            manifest (below)
procedure.py          the executable; defines the entry function (default `run`)
cases/train.json      \
cases/dev.json         }  acceptance cases: execution cases and routing cases (all three files are required)
cases/holdout.json    /
judge.py              OPTIONAL extra checks by the author. UNTRUSTED: may only veto
docs/*.md             optional documentation (INSTALL / ROLLBACK notes may live here)
```
Only those names are allowed. No links, no absolute or `..` paths, no other file types, at most 40 files / 2 MB.

## Manifest (`skill.json`) — every field is required

| field | meaning |
|---|---|
| `format` | `"jarvis-skill/1"` |
| `id`, `version` | `group.skill_name` (lowercase), `major.minor.patch`. An update must be strictly newer |
| `title`, `description`, `author{name,contact}` | identification |
| `scope` | the **verified scope**, in words: what it was verified for and what it is NOT for |
| `permissions` | must contain `compute`. **Only `compute` can be granted.** `network`, `fs.read`, `fs.write`, `process`, `blender`, `pc_control`, `secrets`, `gpu`, `paid_api`, `clipboard`, `screen` are known but refused; anything else is unknown and refused. Installing never grants an OS capability |
| `dependencies{python, modules[]}` | standard-library modules the code imports. Allowed: `math cmath decimal fractions statistics json re itertools functools collections string datetime textwrap heapq bisect operator typing dataclasses enum unicodedata numbers copy`. Third-party software is **never installed automatically**; asking for it is a rejection |
| `entry` | function name in `procedure.py` |
| `params` | typed parameter schema (below) |
| `triggers{match:"word", all:[[words]], exclude:[words]}` | every group needs one of its words (whole words); any exclude word vetoes. Keep it narrow |
| `recovery` | named error → `{"set": {param: value}}` (one bounded retry) or `{"ask_user": true, "why": "..."}` |
| `errors` | the named errors the procedure may raise, each with a description |
| `verify{invariants[]}` | measurable acceptance rules, `{"name", "expr"}`, evaluated by the **host** on `input` and `output` (see below). At least one |
| `reply` | answer template; `{key}` is replaced from the output |
| `provenance` | who/what made it, source URL, date. Informational: it is a claim, not proof |
| `files` | sha256 of every file except `skill.json` (written by `python manage.py skill pack`) |

### Parameters
`type` is `string | number | integer | decimal | strings | numbers`. Numeric types **must** declare `min` and `max`; the host rejects values outside them and fuzzes inside them.
Use `decimal` for money: the value travels as exact text (`"87.50"`, at most `places` decimals, default 2), never a float, and the procedure does integer or `Decimal` arithmetic.
`number_words: true` lets "three ways" be read as `3 ways` for extraction (one to twenty only).
`from_text` is one regex with **one** capture group that pulls the value from the owner's sentence. `default` makes a parameter optional; otherwise it needs an `ask`
question, which JARVIS speaks when the value is missing (he never invents an essential value). `enum` restricts strings.

### The procedure
Plain Python, standard library only, pure computation. It defines its own error class and raises named errors:
```python
class SkillError(Exception):
    def __init__(self, code, message=""): super().__init__(message or code); self.code = code
def run(**params): ...   # return a JSON-serialisable dict; raise SkillError("declared_error_name") for expected failures
```
Anything else that fails (any other exception, timeout, non-JSON output) is an **unnamed failure** and counts against the skill.

### Cases
Execution case: `{"id", "params", "expect": {"output": {key: value}}}` or `{"id", "params", "expect": {"error": "declared_name"}}`.
Routing case: `{"id", "text", "route": true|false, "params": {extracted values}}`: does the owner's sentence reach this skill, and with which values?
Minimums: 12 execution cases, ≥3 holdout, ≥2 expecting a named error, ≥3 routing cases that must match and ≥3 that must **not**.
A routing case may say `"asks": ["total"]` to assert that the request is ours but lacks that essential, so JARVIS must ask for it ("split this three ways"). An execution case may expect `bad_params`:
the host rejects the values before any package code sees them.

### Invariants
Small expressions over `input` (parameters) and `output`, e.g. `sum(output['shares_cents']) == output['total_cents']`. Allowed: comparisons, arithmetic,
`len sum min max abs round sorted all any int float str range isinstance list set tuple zip enumerate bool dict`, `math.*`, comprehensions. No attributes (other than `math`), no imports.
A malformed or failing invariant is a failure, never a pass. They are re-checked on **every real use**.

## Trust model

1. **Quarantine.** `review` parses the package, checks every hash, statically inspects the code (imports, dynamic code, dunder tricks), checks permissions and
   dependencies, and prints a fingerprint. **No package code runs.** Rejected packages are not kept.
2. **Owner approval.** `install --approve <fingerprint>` (at least the first 24 characters) is the owner reading the report and accepting *that exact* package. The fingerprint is the sha256
   of the exact bytes of `skill.json`, which lists the sha256 of every other file. The package is read from disk **once**: review, approval, verification, registration and the archive
   copy all use those bytes, so swapping the file mid-install cannot change what is installed. Before every use, the stored copy is re-checked against its archived package.
3. **Host verification.** Only then the host's judge (`core/skilljudge.py`) runs the package in the sandbox: all author cases, routing cases, 24 seeded fuzz runs inside the
   declared ranges, every invariant, and the optional package judge (veto only). The author's own judge can never make a skill pass. The owner may add private cases.
   **Package code only ever runs inside the Windows AppContainer** (JARVIS's existing `core/project_sandbox.py` broker), never in JARVIS's own process or with its token.
4. **Registration** in the existing `core/procedures.Library` with host evidence. Anything less is refused, and a failed install changes nothing.
5. **Use.** Chat matches narrowly, asks for missing essentials, runs in the sandbox, re-checks invariants, and says "verified" only when they hold.

Hashes detect corruption and edits. They do not prove authorship; there is no signature scheme yet.

## What the operating system enforces
Skill code runs in an AppContainer token with **zero capabilities**, a read-only lab, a Job object (one process, 256 MB, CPU cap, kill-on-close), a two-variable environment and
size-capped pipes. The tests in `tests/test_skillsandbox_adversarial.py` run hostile code with all Python rules switched off and show it cannot read JARVIS's files (`.env`, `users.json`,
owner memory), list its folders, write/delete/rename anything, reach the network (a host-side listener sees nothing), start a process, open the host process, write the registry,
or see any secret in its environment; memory bombs, endless loops, output floods and fork bombs are stopped. `python manage.py skill selftest` repeats the key checks from any shell.

## Limits (stated plainly)
* AppContainer defends against ordinary-user attack paths, not Windows kernel exploits. It is verified here from a NON-elevated shell; JARVIS in production is elevated. An AppContainer
  token is the same either way, but run `python manage.py skill selftest` from the elevated context before relying on it.
* Without the sandbox (non-Windows, or the lab fails) nothing is verified or run: there is no weaker fallback. The laptop therefore never matches installed skills.
* Skills that need Blender, files, the network or apps are not installable yet; they need a permission broker that does not exist.
* The host judge verifies behaviour against the package's own stated invariants and cases. It cannot tell you the invariants are the *right* ones; the owner's review of `scope`,
  `verify` and the cases is part of the gate.
