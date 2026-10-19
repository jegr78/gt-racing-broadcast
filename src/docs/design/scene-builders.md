# Scene layout builders

The Control Center edits each scene independently. The existing HUD layout remains
`layout-hud.json` and `hud.css`; Intermission uses `layout-intermission.json` and
`intermission.css`. Both live under the active profile's `overlay/` directory, so
profile bundles and look backups carry the complete design. Existing CSS migrates
verbatim into the layout's advanced CSS and retains cascade precedence.

Intermission is available in Endurance and both Solo templates. Endurance exposes
public chat; Solo additionally exposes the existing nested webcam and capture
scenes. The editor shows labeled video placeholders, never an unsolicited camera
preview. The background is the profile's existing Intermission graphic. Source
geometry is scoped to the Intermission scene, preserving Program and standalone
device scenes. Video items preserve aspect ratio and stay below the chat overlay.

Saving a layout writes design defaults. Applying it explicitly updates that scene's
mapped OBS transforms and visibility without cutting scenes or changing audio.
Automatic startup/refresh sync only hides items, preserving live operator hiding.
No video sources are added to Endurance Intermission. Legacy Solo POV webcam
placement and visibility remain intact until a layout is saved.

## Requirement and evidence matrix for #930

| Requirement | Production path | Positive test | Failure-path test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Profile-aware scene/slot selection | Overlay slots API and builder selector | Endurance chat; Solo chat/webcam/capture | Invalid page and absent profile return structured errors | Browser switches HUD/Intermission in both kinds |
| Independent layouts and CSS migration | Layout read/write and compiler | Saving Intermission leaves HUD files intact | Page mismatch and invalid layout rejected without writes | Reload restores edited chat geometry |
| Chat appearance and visibility | Intermission marked chat slot | Style/geometry/hidden CSS compilation | Empty legacy CSS preserves defaults | Rendered chat matches canvas geometry |
| Scoped video geometry | Overlay mappings, setup bake, OBS Apply | Intermission bounds match saved design | Program and standalone bounds unchanged | Fake OBS records only Intermission mutations |
| Visibility without camera surprises | Setup defaults, explicit Apply, automatic sync | Explicit apply shows/hides saved video items | Automatic refresh never re-enables operator-hidden items | Fake OBS captures startup versus explicit Apply |
| Source reuse and layering | Solo collection templates | Existing nested sources below chat | No duplicate video devices or Endurance video slots | Collection import inspection and offline preview |
| Background and offline editing | Background API, labeled canvas placeholders | Correct graphic per scene | Missing asset/OBS leaves usable editor and diagnostic | Browser preview with no OBS/devices |
| Portable design and compatibility | Profile bundle and backup workflows | Scene CSS/layout survive export and restore | Older collection missing items reports a useful status | Synthetic round trip; existing profile default screenshot |

Splitscreen will reuse the scene-selection contract in #935. Live controls and
profile-aware starter templates are tracked separately in #931.
