# Update ordering for versioned previews

The latest-release check compares the release number with the number embedded
in a versioned Racecast preview, for example `1.15.0-preview.main.8508c3e`.
An older release is not an update. The matching final release, or a later
release, is an update. The preview ref and commit identify a build, not its
release ordering.

The strict `vX.Y.Z` release-tag parser remains unchanged. Frozen builds without
a comparable version, including legacy previews and local `dev` builds, retain
their existing latest-release fallback. An explicitly selected `--tag` still
means install that tag, including a deliberate downgrade.

## Requirement matrix, issue #1066

| Criterion | Production path | Positive check | Failure-path check | Runtime evidence |
|---|---|---|---|---|
| Never offer an older release to a versioned preview | `update.classify` | Main, PR and branch previews on all platforms report up to date | A missing older asset must not become a building/update offer | CLI check and implicit install return without download |
| Allow promotion to a final release | `update.classify` | Matching and newer releases are offered | A matching/newer missing asset reports building | Real CLI entrypoint with injected release metadata |
| Keep the Control Center consistent | `racecast.update_check_data`, `checkUpdate` | Newer final release advertises an update | An older release hides a previously visible sidebar offer | HTTP update provider and actual sidebar JavaScript under Node |
| Preserve explicit selection and unknown-build recovery | `update.main`, `classify_tag` | Explicit older tag is installable; frozen dev/legacy fallback works | Source checkout and malformed release tags retain refusal/error behavior | CLI entrypoint with fake download, no binary replacement |

The shared classifier determines both CLI and Control Center availability.
Neither the check-only path nor the Control Center downloads a binary.
