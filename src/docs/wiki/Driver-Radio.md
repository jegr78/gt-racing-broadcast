# Driver radio

Driver radio provides English fuel, pace, tyre-temperature and short-status
answers for a Solo POV profile. It also announces fuel risks and meaningful pace
changes. Optional coaching adds explicit personal training, one retained exercise
and a private debrief. The Director card and driver desk share the same radio lane.

![Driver radio in the Solo POV Director Panel](images/director-panel-solo.png)

## Enable access

Start the relay for the Solo POV profile, then open **Telemetry** in the Control
Center. Under **Driver radio setup**, enable driver radio and enter the driver's
verified Console identity. The identity appears beside their Console sign-in.
It uses lowercase letters, digits and hyphens; saving folds case and surrounding
spaces the way Console writes it (**JeGr** is saved as **jegr**) and names an entry
that still is not a Console identity. Save the settings. Director and Race Engineer roles do not grant radio access.
The existing Engineer archive and producer permissions remain separate.

![Private training history and producer radio settings](images/cc-telemetry.png)

The driver's Console now contains **Driver radio**. A direct Director Panel visit
uses the same Console identity. Sign in through Console, then choose **Connect
driver radio** in the Solo POV card. Existing Director controls keep working when
radio access is unavailable.

![Driver-only Console launcher](images/console-driver-radio-launcher.png)

## Use the desk

The four buttons request **Fuel status**, **Pace**, **Tyre temperatures** and
**Short status**. Text appears before optional speech or AI explanation. The desk
adds bounded session history, measurement evidence, optional race length,
starting compound and fuel reserve. Missing setup limits finish estimates and
strategy conclusions; it does not prevent measured status answers.

Confirm a tyre change only after it has happened. An inferred stop does not
confirm a new tyre stint. Slower comparable laps can support conditional tyre
advice, but tyre wear, traffic and weather causes are not directly measured.
Pace comparisons use completed eligible laps and show their sample basis.

![Driver desk with coaching, private history and shared radio](images/console-driver-radio-desktop.png)

If another view changes the setup while you edit it, the save is refused. Reload
the current form, review its values and enter your changes again. Session/context
changes clear obsolete work. Revoking access removes the open view's radio data.

## Start and steer personal training

Choose **Practice** or **Time trial**, select **Balanced**, **Consistency** or
**Pace**, then explicitly choose **Start training**. Opening a view does not start
training. Calibration counts suitable completed laps after Start; earlier laps and
the partly driven start lap do not become the new personal baseline.

The view distinguishes the original baseline from the working reference. It shows
one exercise, provisional versus reviewed geometry, repeated progress and measured
lap IDs. A faster lap alone does not establish why it improved. Unsuitable laps
retain the exercise without adding a technique score. **View measured evidence**
shows the observation, interpretation, action and checks behind the current review.

Apply a skill constraint, **Skip exercise** or **Keep exercise** deliberately. A
constraint unsupported by current evidence waits for suitable data. An optional
reference must match current context; shared archives additionally require the
separate Engineer role and producer grant. Replay and stale data are not offered
as current live references or scored as live training.

**Stop training** remains available while another change waits or the displayed
revision has become old. Closing the browser does not stop relay-owned training.
Changed car, track, definition or corrected evidence can reset the comparison basis.
If a form conflicts, reload its current values before applying your draft.

Training explanations use the already configured isolated profile agent only when
**Optional AI explanation** is explicitly checked for that training. It defaults
off. Deterministic feedback does not wait for the agent, and private training
history is not uploaded as AI context.

## Private history and the next drive

**Private training history** contains compact checkpoints and end summaries:
worked focus, observed change, interpretation and the next exercise. It keeps the
original provenance and distinguishes provisional results from repeated measured
improvement. Cause remains unknown. A failed final write or interrupted relay
produces a partial summary from the last stored checkpoint.

History is separate for each profile and verified Console identity. Local Control
Center/native training uses the separate local producer identity. Another driver's
active training does not reveal their focus or history. For voice-steered local
training, start from native voice or Control Center; native commands cannot take
over a signed Console driver's training.

The limit is 32 recent segments and 90 days per identity, pruned on that identity's
reads and writes. Raw audio, traces and transcripts are not kept in this journal,
and keeping a recording is not required. **Delete this summary** and **Delete all
private history** affect only your history. Deleting active training also prevents
its later checkpoints from restoring that history until an explicit fresh Start.

A previous skill may be suggested after fresh accepted calibration in a known
compatible car, track, definition and conditions. **Apply suggested skill** is
explicit. Historical times never replace the new baseline. Corrected sources or
unavailable archive permissions remove unproven details from historical views.

## Enable native voice

Native voice runs on the same PC as the relay. The initial topology has the driver
microphone and output on that PC. A phone or another browser can control it and
read text; it does not become a remote microphone or speaker.

1. Install FFmpeg and FFplay on the relay PC. The existing tool setup normally
   provides FFmpeg; ensure FFplay is also available on the executable search path.
2. Explicitly install the pinned local wake and transcription models:
   `racecast radio provision`. Provisioning verifies checksums and the prepared
   phrase fixtures. A relay start downloads nothing.
3. In **Driver radio setup**, deliberately enable native voice for the profile.
   It uses the microphone selected in Control Center unless you enter an Engineer
   override. On Windows/macOS the override is the native device name; on Linux it
   is the PulseAudio source identifier.
4. Choose a validated prepared wake phrase and English or German input. Output
   remains English. Arbitrary typed wake-model generation is unavailable.
5. Configure the optional speech/transcription services described below. Without
   speech configuration, the same answers remain available as text.

Once enabled, native voice starts with that profile's relay and keeps working
with all browser/Control Center views closed and OBS stopped. **Pause listening**
is temporary. Clearing **Enable native voice** persistently disables it for the
profile. The desk and card also expose reply mute, volume and session input
language. Closing a view does not change any of these native settings.

Say the wake phrase for every new question. You can continue directly into the
question, such as "Hey Jeff, will the fuel last at this pace?" A short context
supports follow-ups such as "And if I stay out one more lap?" Each answer uses
refreshed facts. Vehicle and broadcast-control commands are refused. The initial
version does not listen for spoken interruptions while it plays a reply.

Wake-gated coaching vocabulary is deliberately narrow:

| Request | English | German |
|---|---|---|
| Start / stop | `start training` / `stop training` | `Training starten` / `Training stoppen` |
| Time trial | `start time trial training` | `Zeitfahren starten` |
| Objective | `objective balanced`, `objective consistency`, `objective pace` | `Ziel ausgewogen`, `Ziel Konstanz`, `Ziel Tempo` |
| Skill | `focus on braking` or another supported skill | `Fokus Bremsen` or another supported skill |
| Skip / keep | `skip focus` / `keep focus` | `Übung überspringen` / `Fokus behalten` |
| Current state | `training status`, `current focus`, `training evidence` | `Trainingsstatus`, `aktueller Fokus`, `Trainingsbelege` |

Supported English/German skills are braking/Bremsen, throttle/Gas,
steering/Lenken, gear/Schalten, speed/Geschwindigkeit, line/Linie, pace/Tempo and
consistency/Konstanz. Use `any skill` / `alle Techniken` to remove the constraint. Ambiguous
coaching requests ask for clarification and do not guess an action. Output stays
English and uses the shared radio queue; urgent Engineer output has priority.

OBS software mute does not pause Engineer listening. A hardware microphone mute
removes its input signal too. Questions may remain audible through your normal
broadcast microphone. Radio does not alter OBS sources, microphone filters or
routing. Deliberately capture the PC output if you want replies in the broadcast.

## Speech and transcription services

English speech uses Google Cloud Text-to-Speech **en-US-Studio-Q**. Configure the
existing machine setting `RACECAST_GCLOUD_TTS_KEY` and explicitly select Google
speech in the profile's radio setup. Credentials stay on the relay. Only answer
text is sent for synthesis. There is no silent voice substitution.

`racecast radio smoke --allow-cloud` makes two configured synthesis requests and
measures cold, warm and cached latency without playing audio. It incurs normal
provider usage. Run it deliberately; ordinary tests never make paid calls.

Transcription choices are independent:

- **Local Vosk** is the lightweight default after model provisioning. Recognition
  can miss free questions, especially in noise. Repeat the wake and question, or
  use a preset if the transcript is wrong.
- **Local whisper.cpp** uses its separately installed `whisper-cli`. Run
  `racecast radio provision --whisper` to download pinned multilingual tiny
  weights. Optional machine settings `RACECAST_RADIO_WHISPER_EXE` and
  `RACECAST_RADIO_WHISPER_MODEL` select an installed executable/model. Synthetic
  English checks worked with tiny/base; the first German synthetic phrase was
  unreliable. Verify recognition with your own voice before relying on it live.
- **Google Speech** requires explicit profile selection and the separate machine
  key `RACECAST_GCLOUD_STT_KEY`. Only the wake-gated utterance, including at most
  two seconds of pre-roll, is uploaded. Ambient listening is never uploaded
  continuously. There is no automatic local/cloud fallback.

Microphone buffers are bounded to 16 seconds and are not written as raw recordings.
Transcription and synthesis have separate short work budgets. Recognized questions
and answers stay in bounded volatile session history. Session/context changes and
relay shutdown invalidate pending work and old playback.

Optional live AI uses an already configured isolated agent. Its small fact package
contains the current allowed telemetry and short question context. Busy, missing,
invalid or late AI leaves deterministic text intact. Numerical claims are checked
against supplied facts; optional interpretations remain labelled separately.

## When voice is unavailable

The visible native state distinguishes disabled, paused, listening, transcribing,
responding and error conditions. Missing models, device permissions, unplugged
microphones, failed providers or playback do not remove preset text. The service
retries device/worker failures with bounded backoff. Failed cleanup retains the
old playback slot until termination succeeds, preventing a second output owner.

Shared microphone access with OBS/Discord and real acoustic wake accuracy depend
on the OS, device and environment. Verify them on the producer PC. Synthetic CI
covers the software lifecycle; live microphone capture, paid-service latency and
hardware coexistence are separate deliberate checks.
