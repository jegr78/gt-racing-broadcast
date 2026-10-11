# Linux producer requirements

Racecast runs locally on the producer's Linux desktop. AWS/GCP provisioning and
cloud instance-control helpers are no longer maintained. Install and configure
the machine using its normal distribution tools.

| App or library | Purpose |
| --- | --- |
| Python 3.12 or newer | Run Racecast from source; packaged binaries include Python |
| OBS Studio with obs-websocket and Browser Source | Compose the broadcast and display relay overlays |
| FFmpeg | Media conversion and local capture |
| yt-dlp and Streamlink | Resolve and receive commentator streams |
| Deno | JavaScript runtime required by current yt-dlp YouTube extraction |
| Discord | Voice communication when used by the league |
| Tailscale | Connect producer and crew machines |
| PipeWire and obs-pipewire-audio-capture | Capture application audio such as Discord in OBS |
| libatomic1 | Required by the native Companion installer on minimal Ubuntu systems |
| glibc 2.35 or newer | Linux runtime floor for the supported Deno toolchain |
| Desktop session and graphics driver | Run OBS and the other GUI applications |

Bitfocus Companion is optional for button control. A remote desktop application
is optional when operating the machine remotely. ARM64 Linux has no official
Discord desktop package; see the installer guidance before choosing that platform.

`racecast install-tools` and `racecast install-apps` cover the supported application
installers. They do not provision the operating system, create users or join a
cloud account. Use `racecast preflight` to check the resulting machine, then follow
[Set up the broadcast PC](Set-up-the-broadcast-PC) and [Run an event](Run-an-event).

Download GT7 reference data deliberately with `racecast gt7-data update` before an
event. Automatic relay-start downloads are off by default.
