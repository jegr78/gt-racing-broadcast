# Native driver radio voice

Epic #969, delivery #976. The relay on the driver's PC owns microphone capture,
local wake detection, question handling and native output. Browser views only
control and inspect it. External services and native capture remain initially off.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Explicit persistent profile consent | existing radio access plus native supervisor | enabled profile starts one worker | disabled/unavailable profile never captures | fake native process factory counts |
| Independent local microphone | FFmpeg native input and isolated worker | machine mic or profile override, three OS argument builders | missing device/worker/permission; bounded restart | controlled subprocess IPC, no OBS/browser dependency |
| Prepared local wake phrase | provisioned Vosk grammar recognizer | Hey Jeff/Hey Engineer, question in same utterance | no phrase, wrong phrase, unprovisioned models | synthetic PCM and actual engine smoke |
| Replaceable transcription | local Vosk, optional whisper.cpp or explicit Google STT | selected English/German, bounded utterance | uncertain/empty result, timeout, no automatic fallback | synthetic audio and fake provider requests |
| Grounded free questions and follow-ups | current service assessment and short volatile conversation | fuel finish, pace cause, extra lap | unsupported commands, old session/context, unavailable evidence | current-fact question tests |
| One native playback owner | service ticket plus cancellable FFplay process | text precedes one spoken answer | urgency/expiry/pause/revocation/stop cancels output | owned fake player and real service lifecycle |
| No ambient upload or raw recording | local bounded PCM ring; cloud only after wake | selected utterance only | silence timeout, excessive audio, disabled cloud | IPC/payload inspection and scratch-directory checks |
| Bounded status and recovery | supervisor state and authenticated controls | pause/resume, language, volume/mute | bad mutations, stale binding, closed supervisor | signed HTTP tests and bounded worker state |

## Engine research and validation

Vosk's Apache-2.0 C API and small English/German models support local streaming
recognition with a prepared phrase grammar. Its native libraries are available
for Windows x64, macOS universal and Linux x64/arm64. The small English model
requires about 300 MB at runtime according to its publisher. Downloads are
explicit, bounded and hash checked; no model download happens at relay start.
[API](https://github.com/alphacep/vosk-api),
[models and licences](https://alphacephei.com/vosk/models).

Synthetic eSpeak input on the development machine detected "Hey Jeff" after
0.64 seconds of audio. Unrestricted Vosk transcribed the following fuel question
poorly. It is a lightweight local option with uncertain-transcription handling,
not evidence of reliable free dictation. A larger dynamic English model took
5.66 seconds for 2.47 seconds of audio and also missed words in this example.

whisper.cpp provides an optional multilingual local transcription adapter through
its native CLI. Tiny weights are about 75 MiB and base weights about 142 MiB.
Prepared wake detection remains local regardless of the selected transcriber.
[CLI contract](https://github.com/ggml-org/whisper.cpp/tree/master/examples/cli),
[models](https://github.com/ggml-org/whisper.cpp/tree/master/models).

openWakeWord requires an inference runtime and a suitable prepared phrase model;
its supplied model licence differs from its code licence. Porcupine requires its
own access key and platform-specific prepared models. Neither supplies a validated
Hey Jeff model in this repository. Do not train or silently generate a new model.
[openWakeWord](https://github.com/dscripka/openWakeWord),
[Porcupine](https://picovoice.ai/docs/quick-start/porcupine-python/).

Synthetic audio validates mechanics, not microphone accuracy or shared-device
support. Real OS permissions, device sharing with OBS/Discord and acoustic feedback
remain explicit hardware acceptance checks. No live microphone, paid service or
model-training run is part of automated validation.

## Runtime contract

`racecast radio provision` explicitly downloads pinned Vosk libraries and both
small language models, rejects archive traversal/links and verifies the prepared
phrase fixtures in an isolated process. Only then does it publish the selectable
catalogue. `--whisper` also downloads multilingual tiny weights; the operator
installs whisper.cpp separately. `RACECAST_RADIO_WHISPER_EXE` and
`RACECAST_RADIO_WHISPER_MODEL` are optional machine settings. Browser clients
cannot supply executable names, model paths or command arguments.

Default transcription is local Vosk. Selecting `whisper` runs its CLI with two CPU
threads, an eight-second deadline and bounded stdin/stdout. WAV bytes travel through
stdin; no raw microphone file is written. Synthetic English tiny/base runs took
about 1.1/2.3 seconds, including model loading. Both performed poorly on the first
synthetic German continuous phrase, so these results are not a German accuracy
claim. Selected-language routing and failure behavior have separate fake-service
tests. Real-device and speech-quality acceptance remains an explicit operator check.

Selecting `google` requires the separate machine key `RACECAST_GCLOUD_STT_KEY`.
Only a locally wake-gated utterance, including at most two seconds of pre-roll,
is sent to the fixed Google Speech endpoint. Ambient listening is never uploaded
continuously. There is no provider fallback. Input is 16 kHz mono PCM, bounded to
16 seconds, with an initial 0.8-second silence boundary. A transcriber failure
keeps preset text available and resumes wake listening after a bounded retry.

FFmpeg opens the selected native microphone directly using existing device
resolution. FFplay owns default-system-device output, with session volume/mute.
It does not modify OBS routing or mute. The supervisor stops capture before taking
an output ticket, stops obsolete output before acknowledging that ticket, and
allows a short output-drain interval before reopening capture. No spoken barge-in
is enabled. Failed cleanup retains ownership rather than granting a replacement.
The process-owner loop has a separate lock from public state/control access.
Blocking process startup or termination never holds the radio access/state locks.
Admission rechecks current consent and the radio epoch after process I/O, including
control changes received while a previous worker was stopping.

`GET/POST /console/driver-radio/native` requires a current Console sign-in and either AI switch (AI Race Engineer or AI Coach).
Controls are revisioned and bound to the current radio context. Local producer
administration uses `/driver-radio/admin` and `/driver-radio/admin/native` with the
existing loopback/Host/Origin boundary. Persistent enablement and provider selection
are producer settings; pause, language, mute and volume are temporary controls.
Read responses contain fixed state/error codes and no credentials or device paths.

Free questions are internal native requests, not arbitrary browser prompts. A
four-turn volatile question/answer context supports follow-ups; all numerical answers use
refreshed deterministic evidence. Session/context changes discard that context.
Bounded messages retain recognized questions and answers. Vehicle and broadcast
commands are refused. Optional AI receives only that small question context and
current allowlisted facts under the existing isolated live adapter policy.

## Review evidence

The security pass identified and corrected two lifecycle failures. Nested Whisper
initially created a new POSIX session; its bounded runner now inherits the outer
native ownership tree. A controlled real-process test proves it is alive before
outer cancellation and gone afterward. Unreadable consent settings initially left
capture active; fail-closed cleanup now stops capture/output and retains ownership
when termination fails. Both listening and playback failure scenarios are tested.

A synthetic FFmpeg source, real Vosk wake model and real whisper.cpp base model
exercised the shipped private worker from listening through transcription to the
English fuel question in 3.24 seconds. The input was the bundled eSpeak fixture,
not a microphone. Ordinary tests do not download models or invoke cloud services.

The full #969 review also exercised the pinned packages on actual macOS 27.0.1
ARM64 and Windows 11 build 26300 AMD64, using Python 3.14.9 and 3.14.7. The macOS
wheel contains `libvosk.dyld`; both discovery and phrase validation now use that
filename. Before correction the real Mac failed with a missing-library error.
After correction both systems loaded their native libraries and validated both
prepared wake phrases plus the negative sample through the isolated worker.
These tests used only bundled synthetic audio, with no microphone capture.
