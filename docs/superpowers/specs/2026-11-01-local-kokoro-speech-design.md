# Local Kokoro speech for the driver radio

## Goal

Offer a local alternative to Google Studio-Q for AI Race Engineer and AI Coach
replies: no per-request cost, no key, no answer text leaving the machine. The voices
are Michael (default), Fenrir, Heart and George.

## Evaluation (Windows, i9-11900KF, CPU only)

| Runtime | Fuel warning | Result |
|---|---|---|
| `kokoro-onnx` + ONNX Runtime, `int8` model, 4 threads | 2.3-3.2 s for 6.4 s audio | chosen |
| `sherpa-onnx` v2.0.1, `int8` model, 1/4/8 threads | 8.0-8.5 s for 4.9 s audio | rejected: slower than real time, audible whine, shorter audio |

The producer judged the `int8` samples clean. Four threads are as fast as the default
and use about half the cores. The shipped pipeline installed in 65 s and, with the model
loaded, took 0.9-1.1 s for 2.0-2.8 s of audio. Load during a stream with a running game
is unmeasured.

## Design

- **Private runtime, not part of the binary.** The binary has no Python packages, and
  `phonemizer-fork` and eSpeak NG are GPL. `racecast radio provision --kokoro` installs
  into `runtime/radio-kokoro/`: python-build-standalone CPython 3.12, the hash-pinned
  `src/assets/driver-radio/kokoro-requirements.txt` (`tools/kokoro-lock.py`) and the two
  checksummed model files. It builds in a scratch directory, synthesizes a test sentence
  and only then publishes. Relay start downloads nothing. The relay talks to this runtime
  only through a child process; whether that separation meets the GPL is the maintainer's
  call, not settled by this document.
- **One long-lived worker.** `driver_radio_kokoro_worker.py` runs under the private
  interpreter with `-I`, so it sees neither the relay's modules nor its environment
  packages. It loads the model once and answers JSON lines. It runs at below-normal
  priority with four threads so OBS and the game come first.
- **`KokoroSpeech`** has the same `synthesize` contract as `GoogleSpeech`: bounded,
  cancellable, validated WAV, bounded cache. A late reply is discarded by request id.
  A crashed worker restarts after a 5 s backoff.
- **Settings.** `providers` gains `engine` (`kokoro` default, `google`) and `voice`
  (`am_michael` default). A profile without a stored engine uses Kokoro, including one
  that had speech on with Google; Google is a deliberate choice. No fallback between
  engines: a failed or uninstalled Kokoro leaves the text without audio.
- **Control Center.** The Voice card selects engine and voice and offers **Install Kokoro
  voices** and **Test Kokoro**, the same CLI actions as jobs.

## Not included

- Starting playback after the first sentence. A long answer plays once it is complete.
- Intel macOS: ONNX Runtime publishes no wheel for it, so provisioning refuses.
