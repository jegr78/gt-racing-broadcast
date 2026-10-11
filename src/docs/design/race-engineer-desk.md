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
