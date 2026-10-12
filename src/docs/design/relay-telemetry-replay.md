# Relay telemetry replay

Implementation plan for #857. Replay preserves recorded analysis timestamps;
speed changes monotonic playback deadlines only. UDP continues recording independently.

| Requirement | Production path | Positive test | Error test | Runtime evidence |
|---|---|---|---|---|
| Profile-only finalized source | Replay controller start | Valid recording | Traversal, separators, symlink, extension | Real HTTP start |
| Solo POV telemetry only | Relay handler | Available controller | Disabled 404 JSON | Synthetic e2e |
| On-air confirmation | Relay handler, CLI, UI operation | Explicit confirmation | Streaming refusal naming flag | HTTP and browser |
| Pacing and start position | Replay worker | Fake clock, bounded prefeed | Invalid speed/time, malformed input | Advancing HUD/Engineer/status |
| Separate pools and no self-recording | Source lock and Store reset | Switch generations, UDP recorder | Concurrent UDP suppression | Offline lap/sector parity |
| Stop and EOF return to UDP | Controller stop/finally | UDP resumes | Repeated stop/start | Synthetic e2e |
| Label privacy | Engineer snapshot | Replay label and speed | Filename absent | Signed desk |
| Replay marker persistence | Telemetry view settings | Default on, saved toggle | Invalid settings fallback | HUD screenshot |
| Replay controls | CLI and ui_ops | Valid argv and status | Invalid options/profile | Control Center browser |
| Import recording and index | CLI import and ui_ops | Header and both copies | Collision, invalid header | Control Center browser |

Checklist

- [x] Relay, reset, pacing and CLI
- [x] Control Center, Director, HUD and Engineer labels
- [x] Import and collision handling
- [x] Tests, e2e, screenshots and operator documentation
- [x] Guard falsification and independent review

Finalization follows `next-issue`: complete local gates and CI, verify squash merge,
then reconcile the tracker and publish the committed wiki.

The source lock orders update/reset against replay and UDP. UDP display updates
use a nonblocking acquisition during a switch; raw recording happens before this
check and continues while analysis drains. Source resets run as acknowledged
commands on the Engineer worker. Its existing enable-flip recording recovery
semantics remain separate. Replay retains recorded wall time; idle wall-clock
confirmation is suspended for that source. Batches of 64 packets drain the bounded
analysis queue before prefeed enters the paced interval. Every stop and EOF resets
the HUD engine and retires the replay generation before UDP consumers resume.

The public source label uses the validated capture date and never the private
filename or free-form profile metadata. Unreadable dates retain a generic label.
On-air start checks fresh OBS health, falling back to the known streaming state if
OBS is unreachable. Control Center obtains status through ordinary CLI jobs and
confirms before passing --on-air. No new relay proxy is added.

Review corrections bound both reset queue admission and acknowledgement. Expired
commands are cancelled. During a source switch the old Engineer API is detached
until acknowledgement. If reset fails, HUD/UDP resume with analysis unavailable;
a later Stop retries the live reset. Status retains the error without a ghost
active replay. Reachable OBS with missing streaming evidence requires explicit
confirmation, including when the last known streaming state was active.
