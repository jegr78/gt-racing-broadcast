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
| Darwin group retirement | terminate_tree | Transient EPERM then proven absence | Permanent EPERM, live group and exhausted shared budget fail | Synthetic E2E teardown on macOS |
| Host memory before a run | Local Linux E2E admission | A calm host, or one that calms down, starts | Low MemAvailable or high memory PSI waits, then refuses with its values; missing PSI never blocks | Fixture `/proc` files |
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

Before it starts the unit, local admission checks the host's memory. A run started
while the host swaps stalls on IO inside its own limits: a red-flag screenshot timed out
after 8 s while the unit waited 2.2 s on IO and the host was at 54 % IO pressure with
8 GB in zram swap, at 0.86 of its two CPUs. Admission needs MemAvailable of at least
the unit's memory cap plus 512 MiB and memory PSI avg10 of at most 20 % (some) and 5 %
(full). Under more pressure it waits up to 120 seconds, then refuses with "host under
memory pressure:" and the values. It never starts a run silently under pressure, a
kernel without PSI is judged by MemAvailable alone, and a hosted runner, which enters no
unit, is not checked. A unit busy with another run is named at once: that run's memory
is no pressure to wait out. The event simulation also classes a failure whose window up
to the failure the unit spent 20 % or more waiting on IO or memory. With the host's own
stall over the same share it is "host starved"; with new memory.max or memory.high
events of the unit it is "unit at its memory cap". Either stays a hard failure.

`--keep` stays in the foreground for a finite inspection lease. Closing the
harness closes its control pipes; independent child supervisors then stop their
owned process trees. No process-name matching or system-wide kill is used.
Resource-heavy local verification is sequential and bounded. A passing test result
is insufficient until its owned services and worker threads have also stopped.

On macOS, a reaped leader can leave a zombie-only process group briefly visible
as `EPERM`. Cleanup repeats only the non-destructive group-absence probe within
the existing ten-second leader-reap budget. It never signals the released group
identity again. A persistent permission error or a remaining live group still
fails cleanup and retains the diagnostics.
