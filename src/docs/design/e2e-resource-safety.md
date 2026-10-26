# Bounded E2E ownership and encoder generations

An unattended synthetic inspection left relay processes alive after its harness
exited. Repeated cover-encoder exits also left old stdin pumps reading an empty
live ring. These are separate lifecycle defects; test containment must remain
effective if either cleanup or an application worker regresses.

| Requirement | Production/tool path | Positive test | Failure test | Runtime evidence |
|---|---|---|---|---|
| One pump per encoder generation | `FeedCoverTap` supervisor | Empty-ring restarts retire each pump before replacement | Dead encoder, EOF, blocked old pump and Stop cannot create another pump | Bounded fake encoders; few real threads under a kernel task limit |
| Foreground finite inspection | E2E keep lease | Owned CSV servers remain alive during inspection | Failure or lease end always enters cleanup | Tiny owned subprocesses; no live broadcast inputs |
| Owner death closes test descendants | E2E child supervisor | Ordinary child exit is reaped | Parent pipe EOF, deadline and termination release descendant listeners | Real controlled child/grandchild on each CI OS |
| Kernel resource ceiling | Local Linux E2E admission | Current cgroup has verified task/memory/CPU limits | Missing or weakened limits fail closed; competing run is refused | Owned user systemd unit, finite runtime and explicit property readback |
| One heavy local run | Fixed machine-user E2E unit | One bounded run completes and releases admission | A second run cannot add another relay/browser fleet | Two tiny overlapping admission probes |
| Diagnostic retention | Cleanup failure reporting | Successful cleanup removes owned temporary files | Failure retains logs and reports failure | Isolated temporary directories |

Local Linux E2E runs use one transient user service with at most two CPU cores,
512 tasks, 2 GiB RAM and fifteen minutes of lifetime. The service kills its whole
cgroup at exit, including descendants that created another process group. It
does not depend on the agent connection staying alive. Local admission fails
closed when this containment is unavailable. A separate watcher inherits a stable
pidfd for the caller and a write descriptor opened inside the verified test cgroup.
Caller death terminates that pinned cgroup, including on an uncatchable abort.
It never resolves a kill target by process or service name. GitHub-hosted Actions
uses disposable runners with finite E2E job timeouts; self-hosted Linux runners
still require kernel containment. Runner identity follows GitHub's documented
[`RUNNER_ENVIRONMENT`](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)
values. The portable per-service supervisor remains active on all runner platforms.

`--keep` stays in the foreground for a finite inspection lease. Closing the
harness closes its control pipes; independent child supervisors then stop their
owned process trees. No process-name matching or system-wide kill is used.
Resource-heavy local verification is sequential and bounded. A passing test result
is insufficient until its owned services and worker threads have also stopped.
