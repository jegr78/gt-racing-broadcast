# Build & maintenance

> Technical reference: for the maintainer.

How the repository is built, kept secret-free, and how this wiki is published. Everything
here is **maintainer-only** (not shipped in the operator package).

## Single source: edit only `src/`

`src/` is the only source of truth. `dist/` and `runtime/` are generated and gitignored,
never hand-edit them. `tools/` holds maintainer scripts. After any change that ships, run
`python3 tools/build.py`: its verify step is the closest thing to CI.

## Build the distributable

```bash
python3 tools/build.py      # assembles dist/GT_Racecast_Package/ + .zip and self-verifies
```

The verify step checks: tokens are in place (no raw sheet/timer URLs or machine paths),
the Companion password is blanked, the relay POV endpoint is present, no shell scripts are
shipped, and preflight + `.env.example` are included.

## Releases (standalone binaries)

Operators download `racecast` from GitHub Releases and never need Python.

**Primary flow, merge the Release PR:** a release-please bot maintains a
standing PR that collects every `feat:`/`fix:` commit since the last release,
with the computed next version and changelog. When an event approaches and
`main` is in a good state, **merge that PR**, this creates the `vX.Y.Z` tag,
the GitHub release with notes, and kicks off the binary build that uploads
`racecast-windows.zip` / `racecast-macos.tar.gz` / `racecast-linux.tar.gz` /
`racecast-linux-arm64.tar.gz` (each contains
the `racecast` + `racecast-ui` binaries plus `.env.example`; on first run the binary
copies it to `.env`). The two Linux archives come from the x86-64 (`ubuntu-latest`)
and ARM64 (`ubuntu-24.04-arm`) runners in the build matrix.
No Release PR open = nothing release-worthy happened (`docs:`/`ci:` commits
don't count). The binaries are unsigned: operators see a one-time
SmartScreen/Gatekeeper warning (documented on the setup page).

**Escape hatch, manual tag:** pushing a semver tag still works exactly as
before and skips the bot:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

`CHANGELOG.md` and `version.txt` in the repo root are maintained by the bot;
the authoritative version is always the git tag (stamped into `racecast --version`
at build time).

## Preview builds (test before releasing)

Sometimes a build must be tested *before* a real release, a single PR, or
`main` after several PRs merged with no release yet. The **Preview** workflow
publishes real, downloadable binaries as a GitHub **pre-release** (public
download URLs, but never the "Latest" slot), built green (full test gate runs
first).

- **From a PR:** add the **`preview`** label to the PR. Every push to the
  labeled PR (re)builds a `preview-pr-<N>` pre-release for all three OSes, and a
  PR comment lists the download links. Closing the PR deletes the pre-release.
- **From `main` (or any branch):** Actions → **Preview** → *Run workflow* →
  pick the ref (default `main`). Publishes a rolling `preview-<ref>` pre-release.

Preview tags are `preview-*`, never `v*`, so they never trigger `release.yml` or
disturb the release-please Release PR. The version a preview binary reports leads
with the **probable next release version**: read from the open release-please
Release PR title, or, if none is open yet, the next minor after the latest `v*`
tag: so a tester can tell which release base a preview belongs to. `racecast
--version` prints e.g. `1.1.0-preview.pr42.0123abc` (a valid SemVer prerelease)
and the pre-release is titled `Preview 1.1.0. PR #42 (0123abc)`; the trailing
short SHA still pins the exact commit. (Without any version source it degrades to
the bare `preview-pr42-0123abc` form.) The GitHub releases list orders by
`created_at`, which for a release is the date of the *tagged commit* (not the
build time); because a preview's rolling tag is force-moved to each build's
commit, a fresh preview sorts to the top as expected. Preview binaries are
unsigned, same one-time SmartScreen/Gatekeeper warning as releases.

> One-time setup: the `preview` label must exist in the repo,
> `gh label create preview --color FFA500 --description "Build a downloadable preview binary"`.
>
> Fork PRs cannot publish previews (GitHub gives fork-PR workflows a read-only
> token); the team's same-repo branch workflow is unaffected.

## Round-trips that keep secrets/paths out of git

- **OBS collection.** Edit scenes in OBS, export, then fold back with
  `python3 tools/tokenize-obs.py exported.json src/obs/GT_Racing_Endurance.json` (re-tokenizes
  sheet/timer URLs + asset paths). `src/setup-assets.py` does the reverse for a machine
  (injecting the active profile's values).
- **Companion config.** Export into the gitignored `incoming/` folder, then
  `python3 tools/strip_companion_pass.py` blanks the WebSocket password and writes
  `src/companion/racecast-buttons.companionconfig`. `build.py` re-strips defensively.

## Publish this wiki

These pages are **generated from the repo**, not edited on GitHub. Source: `src/docs/wiki/`.

```bash
python3 tools/sync-wiki.py            # clone/pull the wiki repo, mirror pages, commit, push
python3 tools/sync-wiki.py --dry-run  # show what would change, push nothing
```

`tools/sync-wiki.py` derives the wiki remote (`<origin>.wiki.git`), clones it into
`runtime/wiki/`, mirrors `src/docs/wiki/*.md` + `images/` (adds/updates/**deletes**), and
pushes. Renaming a page therefore cleanly replaces the old one.

**First-time bootstrap (once per repo):** GitHub creates the wiki's Git repo only after the
first page is saved in the web UI. Open the repo's **Wiki** tab → create+save any page,
then run `sync-wiki.py` to overwrite it with the real pages.

## Page & diagram conventions

- `Home.md` is the landing page; `_Sidebar.md` is the left navigation (two tiers:
  operators first, technical reference below).
- Link by page name, no extension: `[Run an event](Run-an-event)`. Spaces in a title map to
  `-` in the file name.
- Diagrams are **Mermaid** in ```` ```mermaid ```` fences. GitHub renders them in a
  sandboxed iframe. **Pitfall:** never put a `;` inside a node/edge/note label. Mermaid
  treats it as a statement separator and the diagram fails with *"Unable to render rich
  display."* After publishing, spot-check the rendered pages.
- Screenshots live in `src/docs/wiki/images/`, referenced relatively:
  `![alt](images/your-file.png)`.

See also: [Architecture](Architecture) for the system design.
