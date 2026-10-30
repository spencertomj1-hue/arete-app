# Contributing to Arete

Thanks for helping! A few rules keep everyone's personal data safe.

## Never commit real data

Arete is a personal tracker, so a careless commit could publish someone's diary, timetable or calendar link.

- **User data never lives in the repo.** Arete stores it in `~/Library/Application Support/Arete/`, and secrets (the Outlook link) only in the macOS Keychain. Don't add code that writes user data or secrets into the project folder.
- **Don't commit** databases (`*.db`), calendars (`*.ics`), spreadsheets or exports (`*.csv`, `*.xlsx`), recordings, logs, `.env` files, `.streamlit/secrets.toml`, `settings.json` or `arete_config.json`. `.gitignore` blocks these, so please don't force-add them.
- **Use fake data** in tests, fixtures, screenshots and docs: obviously made-up names, places and links (e.g. `example.com`).
  - For screenshots, run the app on a demo data folder: `.venv/bin/python scripts/seed_demo.py`.
  - Tests use temporary folders via `ARETE_DATA_DIR`. Never point them at a real data folder.

## Run gitleaks before every commit

Install [gitleaks](https://github.com/gitleaks/gitleaks) (`brew install gitleaks`), then turn on the pre-commit hook once per clone:

```bash
./scripts/install-hooks.sh
```

The hook scans your staged changes and blocks the commit if it finds a secret. If you use the [pre-commit](https://pre-commit.com) framework instead, `.pre-commit-config.yaml` sets up the same check (`pre-commit install`).

Before opening a pull request, also scan the whole history of your branch:

```bash
gitleaks git . --redact
```

## Making changes

- **Read the guide first:** [`HANDOVER.md`](HANDOVER.md) explains the architecture and the gotchas.
- **Keep things simple and commented,** in the style of the surrounding code.
- **Run the tests:** `.venv/bin/python -m pytest -q -p no:logging tests`
- **Restart the app** after changing anything other than `app.py` (component JS/CSS are read at startup).
