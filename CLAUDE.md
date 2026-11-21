# CLAUDE.md

This is a personal Windows voice assistant (Python 3.12). Start with `README.md`.

- Run the app: `.venv\Scripts\python.exe main.py` (`--text` to type). Entry points: `main.py` (voice loop), `brain/llm.py`
  (the Brain), `core/intelligence.py` (local-first model routing), `tools/` (every `@tool` skill; a module must be listed
  in `tools.ALL`), `core/appserver.py` (phone web app on port 8765), `core/personality.md` (system prompt).
- Personal data lives in `data/` and secrets in `.env`. Never commit, print or upload either.
- Tests: `.venv\Scripts\python.exe -m pytest -q tests --ignore=tests/live`. Every test gets an empty temporary
  `data/` folder (tests/conftest.py). `tests/live` drives the real PC and is opt-in.
- Never send test sentences to a running Jarvis someone is using; use `scripts/hello_check.py` (throwaway data folder)
  or pytest.
- Commit only the files you changed (no `git add -A`).
