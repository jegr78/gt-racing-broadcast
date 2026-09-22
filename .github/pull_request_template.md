<!-- Keep changes under src/. Dist/ and runtime/ are generated and gitignored. -->

## What & why

<!-- One or two sentences. Link any related issue. -->

## Checklist

- [ ] Edited only under `src/` (or `tools/` for maintainer scripts). No hand edits to `dist/`/`runtime/`
- [ ] English only in scripts and docs
- [ ] No secrets or machine paths committed (secrets live in the gitignored `.env`)
- [ ] Ran the relevant tests: `python3 tests/test_pov.py` (+ `test_hud` / `test_preflight` / `test_standby` as needed)
- [ ] Ran `python3 tools/build.py` for anything that ships (its verify step is the CI gate)
- [ ] No `.sh`/`.bat` added (Python-only tooling by design)
