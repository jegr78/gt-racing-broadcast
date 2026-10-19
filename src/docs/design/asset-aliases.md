# Asset display aliases

Issue #936 keeps the canonical Sheet label, filename, source and action identity.
Only a trimmed non-empty optional Alias changes the human label. Every surface
keeps its own existing fallback caption when no alias exists.

| Requirement | Production path | Positive test | Error test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Header metadata independent of order | Shared Assets row parser; both downloaders | Unicode alias before/after URL, legacy rows | Alias resembling URL or media label never becomes identity/download | Synthetic graphics/media CLI refresh |
| Optional cached aliases | Per-kind manifest reader/writer | Unknown metadata retained, blank/removal clears owned aliases | Bad CSV/network keeps last-good; malformed manifest falls back | Metadata-only refresh changes labels without asset bytes |
| Atomic and profile-local metadata | Unique temporary manifest replacement | Graphics/media refresh preserves other kind and Internal | Failed replacement keeps exact old file and cleans temporary file | Two profile/runtime fixtures and bundle/Look/takeover paths |
| Canonical actions and rights | Relay galleries, take/request APIs, shared resolver | Duplicate aliases still select correct source | Internal/placeholder/role filtering before public alias projection | Real HTTP permissions and click recording |
| Stable pending requests | Server-resolved label snapshot in Requests | Rename while pending keeps first label for all parties | New caller cannot override or split canonical deduplication | Request, rename, merge, answer through shipped API |
| Crew and Director presentation | Shared crew frame and Director controls | Gallery, preview, flags, controls and feedback show alias | Text-only labels remain inert, generic scenes retain names | Actual desk browser workflows and screenshots |
| Producer presentation | Control Center assets and mapped builder details | Alias prominent, canonical tooltip/details retained | Missing/old metadata preserves previous labels | Actual local UI profile switch and gallery polling |
| Companion labels | Existing template buttons and native Web Buttons | Canonical-key expression resolves alias with legacy caption fallback | Missing map/relay/page-only import yields old caption; text stays literal | Disposable Companion instance and screenshot |
| Portability and refresh | Existing cache export/Look/takeover documentation | Applicable cache transfer carries metadata | No direct Sheet dependency or restart for running desks | Fixture export/import and local metadata replacement |
| Shipping and docs | Sheet template, wiki, build resources | New parser ships in source/binary | Existing headerless download and macro tests pass | Full canonical checks and refreshed development screenshots |

## Ownership and refresh

Graphics aliases and Internal metadata live in graphics/manifest.json. Media
aliases live in media/manifest.json. Each downloader updates its complete owned
metadata after a successful validated Sheet read, including metadata-only runs;
byte-download errors do not erase valid metadata. Failed or unrecognized Sheet
responses leave metadata intact. An empty Alias removes that alias. Manifests
retain unknown fields and are replaced atomically with unique temporary files.
Running desks read local manifests through normal polling, without a Sheet call
or relay restart. Internal and placeholder filtering precede public labels.

Pending graphic requests retain their first server-resolved human label while
canonical source identity continues to drive authorization, merging and actions.
A newly initiated direct take uses the current label. Alias text is never parsed
as a URL, filename, source name, HTML or Companion expression.

## Companion delivery

The native board renders captions into PNGs, so aliases use its own expression
renderer rather than a browser text overlay. Canonical constant lookups use a
guarded custom-variable object and each button's exact legacy caption fallback.
The three-second generic-HTTP trigger and custom-variable schema were created
and exported from an isolated real Companion instance. The variable starts as
an empty object and is not persisted. Returning a string never invokes nested
variable evaluation. Page-only upgrades require adding the variable and trigger
without replacing user connections; the operator guide lists those steps.
Canonical actions remain discoverable in Companion's button editor.
