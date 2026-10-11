# Race Engineer definition replacement and publication

Issue #846 reproduces the Windows release-PR failure without depending on timing.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Invalidate atomic replacements despite equal size/mtime | Store._refresh_definitions, _record_stamp | Changed corner and fingerprint are published | Equal-stat replacement previously retains old data | Actual definition save with restored mtime and changed inode |
| Keep recording-backed preparation consistent | Store recording recovery | New definition stamp separates recovery cache | Previous cached definition cannot be reused | Existing recovered-source tests plus replacement stamp assertion |
| Await committed public revision in integration tests | API test wait_ready | Expected context/definition values from real worker | Held annotation still exposes previous published detail | Barrier on annotation; old detail during work, updated detail after release |

Definition and recording-context invalidation includes device, inode and ctime alongside size and mtime.
The production worker retains ownership of periodic refresh and publication.
Integration tests wait for the exact public result rather than running private
worker methods concurrently or increasing timeouts.

The visual recording-subset test also awaits the completed asynchronous selection
handler. A non-null pool appears before the A/B detail publication and does not
prove that the selected tyre conditions are visible yet.
