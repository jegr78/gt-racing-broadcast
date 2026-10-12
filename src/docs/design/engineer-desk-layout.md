# Approved Engineer desk layout

Requirements and verification for #855 under #850. The producer approved proposal A
at `d0131a9` in the working chat; the approval is recorded in
[the issue](https://github.com/jegr78/gt-racing-broadcast/issues/855#issuecomment-6113132897).
The rendered approval artifact remains under `src/docs/design/engineer-layout-proposal.*`.

| Requirement | Production path | Positive proof | Failure proof | Runtime proof |
| --- | --- | --- | --- | --- |
| Current lap, delta, best, position first | Headline grid and existing instruments | Four ordered desktop/tablet tiles, two phone rows | Missing values retain truthful placeholders | Real signed browser at 1440/1024/390 |
| Supporting fuel/theory/tyres and compact inputs | Support row and input strip | Existing measurements keep their IDs and refresh | Revocation clears every protected value | Existing desk lifecycle workflow |
| Compare B with A before plots | Session/source/B/A/follow controls | Visible selected comparison feedback and functional selections | Incompatible/pending results do not claim a delta | Signed flow and phone summary |
| Controls at charts, map alongside | Plot cards and distance/channel controls | Channel and zoom remain usable on phone | No overflow and no hidden essential charts/map | Three viewports plus production screenshots |
| Larger sections with expandable mini-sectors | Reviewed section cards and details | Three named A/B gain/loss groups, diagnostic disclosures | Missing/unreviewed/mismatched values do not become measured gains | Persisted synthetic reviewed definition plus display workflow |
| Context/notes/chat follow analysis | Context grid and chat | Existing context updates/chat remain usable | Late revoked responses cannot restore data | Existing full browser regression |

A diagnostic 200 m segment crossing a larger-section boundary appears in both
affected disclosures. Section gains use their exact reviewed boundaries; these
segments are inspection aids and are not added to manufacture a section total.
The standalone full mini-sector table remains available in one disclosure.

Disclosure state and keyboard focus survive regular analysis refreshes. The
current definition/variant identifies that state; a new definition or a cleared
selection/access view resets it. A browser regression observes three complete
signed polling cycles while the section remains expanded and focused.

Phone headline labels reserve two lines with a fixed line height. CI exposed
font-dependent wrapping of the best-lap label; a browser regression forces that
label onto two lines and checks that numeric values in each grid row stay aligned.
