# Builder read recovery (#1047)

The historical Windows run records only a timeout waiting for the restored Split
layout. It contains no failed-request or builder-state diagnostic, so that run's
underlying transport failure cannot be established from its log alone.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Recover one interrupted read without discarding saved geometry | `loadOverlay` GET sequence | Abort one Split layout read after reload, then restore the saved 1200px width | Persistent read failure stops after two attempts and retains the prior model | Actual browser route aborts, request counts, error text and saved geometry |
| Obsolete failures cannot affect a new selection | Load sequence and requested-page fence | A current selection retains its model and error state | Reject an old read after the page/sequence changes | Executed shipped JS with a controlled rejected promise |
| Business errors stay visible without retry | Slot/layout response handling | Current JSON data loads normally | An explicit `ok:false` response retains its provider error | Fetch counts and the displayed error message |

One failed GET sequence may retry once. The existing profile-change retry shares
that bound. A second failure displays the existing reachability error. No write,
Apply action, test or timeout is retried. The native geometry check deliberately
interrupts one read and verifies recovery; persistent failures report their state
instead of waiting only for a desired width.
