# OBS launch and shutdown

Issue #830: a graceful shutdown can take longer than the command requesting it.
Both manual launch and event startup must wait for the old OBS process to exit.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Confirm process absence on Windows, macOS and Linux | event.app_running_checked | Successful platform probes | Failed, missing or timed-out probe raises | Fake OS command results exercise actual parser |
| Bound waiting to 30 seconds | event.wait_for_app_exit | Old process exits after 14 seconds | Process remains alive at deadline | Controlled clock records each poll and sleep |
| Prevent overlapping OBS launches | racecast._gui_spawn, manual and event callers | Spawn only after confirmed exit | No spawn on timeout or unknown status | Caller tests record wait before Popen |
| Confirm graceful quit | racecast.app_quit_cmd | Success only after process exit | Still shutting down reports failure | Quit command and exit confirmation recorded separately |
| Never force termination | event.quit_command | Existing graceful OS commands | Windows command excludes /F | Platform command assertions |

The wait uses a monotonic deadline, and each process probe receives only the
remaining budget. Probe failures block launch rather than treating OBS as absent.
The quit request and its subsequent exit wait share the same 30-second budget.
An already running OBS is still reused by event startup. The shared spawn guard
covers OBS appearing between the initial event probe and the actual launch.
