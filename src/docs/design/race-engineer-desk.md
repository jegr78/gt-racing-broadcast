# Race Engineer desk

Requirements and evidence for #826.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Measured 2 Hz instruments and receipt age | Snapshot poll and local monotonic age display | Synthetic GT7 values | Missing channels, pause/menu/stale and browser disconnection | Signed Console in Chromium |
| Independent current/prior/reference selection and Follow live | Browser-local selection state | Two independent browsers, manual older lap | New lap keeps manual selection, incompatible session clears reference | Actual shared preparation API |
| Shared charts, map, mini-sectors and context | Prepared lap/compare routes | Control Center math and displayed metrics | Provisional future empty, pending/evicted detail | Synthetic recording and authored definition |
| Revocation and delayed response protection | Per-request access and browser epoch | Granted reference then current role | Role/feature loss clears and stops, reference revoked in flight | Controlled HTTP responses |
| Reconnect and generation lifecycle | Retained selection/zoom, generation identity | Connection restored retains selected lap | Relay generation change resets view visibly | Two generations of real live store |
| Desktop/tablet/phone and existing Crew chat | Responsive layout and existing chat routes | Three rendered viewports, chat roundtrip | No overflow, no operational controls, safe text rendering | Screenshots and visual rules |
| Producer activation and operational limits | Operator wiki and matched screenshots | Published content and images | Volatile/evicted/recovery states explained | Published wiki browser verification |


## Browser lifecycle evidence

The real signed Console fixture uses shared synthetic GT7 packets and independent
browsers. It verifies measured instruments, provisional empty sectors, manual
selection and zoom, new-lap notifications, producer note revisions, shared reference
revocation during a held reply, pause/menu/stale/missing channels, chat, network
reconnect, new relay generation and role/feature/source access loss during held
detail responses. Instrument refresh continues while analysis responses are pending.

Live values poll approximately twice per second independently of larger detail and
comparison replies. Local monotonic age continues during network interruptions.
Authoritative role, opt-in and source access loss clears all protected state, aborts
requests and stops polling. Technical analysis errors retry without pretending to
have received new values. The shared server workers prepare comparisons and recovery;
selection and zoom remain local to each browser.

Desktop, tablet and compact phone screenshots were rendered and inspected. The
existing visual probe's six rules pass at 1440, 1024 and 390 px, and all twenty
ordinary application surfaces pass their visual run. The existing producer access
browser workflow still passes. Independent security review reproduced the source
authorization distinction; its correction has a failing-then-passing runtime proof
and the final review has no open findings.
