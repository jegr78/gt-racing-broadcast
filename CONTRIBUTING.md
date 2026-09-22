# Contributing / building from source

This is the developer/maintainer guide. End-user setup and event-day docs live in the
[project wiki](https://github.com/jegr78/gt-racing-broadcast/wiki) and the
[onboarding decks](https://jegr78.github.io/gt-racing-broadcast/); `CLAUDE.md`
holds the deep architecture reference.

## Ground rules

- **Edit only under `src/`.** `dist/` and `runtime/` are generated and gitignored, never
  hand-edit them. `tools/` are maintainer scripts (build, tokenize, sync, helpers) and are
  **not shipped** to producers.
- **English only** in all scripts and docs (the team is international).
- **Never hardcode secrets or machine paths.** Secrets come from `.env` / `profile.env`;
  the OBS collection and scripts are deliberately path/secret-free in git.
- **Python-only tooling**, no `.sh`/`.bat` (the build fails if any are shipped).
- Changed a UI surface? Refresh its wiki screenshot under `src/docs/wiki/images/` in the
  **same** change (see `CLAUDE.md`).

## Tests & lint

```bash
python3 tools/run-tests.py    # the whole stdlib suite (exactly what CI runs)
python3 tools/lint.py         # ruff lint (= the CI lint job); --fix auto-corrects
```

Each `tests/test_*.py` is also a runnable script. Run one function with, e.g.:

```bash
python3 -c "import sys; sys.path.insert(0,'tests'); import test_pov as t; t.t_pov_format_constant()"
```

## Build the distributable

```bash
python3 tools/build.py        # -> dist/GT_Racecast_Package/ + dist/GT_Racecast_Package.zip
```

Its verify step is the closest thing to CI (tokenization, blanked Companion password, no
secrets, preflight present, no shell scripts). Run it after any change that ships.

## Standalone binaries (PyInstaller)

```bash
python3 tools/build-binary.py # -> dist/bin/racecast + dist/bin/racecast-ui (+ smoke test)
```

CI builds all OSes on `v*` tags. Releases: merge the standing release-please Release PR
(or push a `v*` tag).

## After editing the OBS collection in OBS

Re-export from OBS, then fold the change back into the tokenized source:

```bash
python3 tools/tokenize-obs.py /path/to/exported.json src/obs/GT_Racing_Endurance.json
```

## Publishing the docs

```bash
python3 tools/sync-wiki.py    # mirror src/docs/wiki/ to the GitHub wiki (--dry-run to preview)
```

The onboarding decks under `src/docs/slides/` (including the printable role cheat sheet)
publish to GitHub Pages via the dispatch-only `.github/workflows/pages.yml`. Before
publishing, run the slide-overflow guard (`tools/check-slides.py`, needs a Playwright venv).
