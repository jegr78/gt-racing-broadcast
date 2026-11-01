# Third-party licenses

racecast itself is under the MIT License (`LICENSE`). This page lists third-party
components racecast ships or downloads. It is a factual inventory, not legal advice. Each
component's own license text is authoritative.

## Shipped with racecast

| Component | Where | License |
|---|---|---|
| uPlot | `src/assets/vendor/uplot/` | MIT |
| reveal.js | `src/docs/slides/vendor/reveal/` | MIT |
| GT7 track catalogue (`index.json`) | `src/assets/gt7/` | CC0 1.0 |
| GT7 car tables (gt7info) | `src/assets/gt7/` | MIT-0 |

## Downloaded only on an explicit install

Nothing below is part of the racecast binary or package. Each item comes directly from
its upstream source, pinned by version and checksum, when you choose the install action.

**Voice models** (**Install voice models**, `racecast radio provision`):

| Component | License |
|---|---|
| Vosk library and models | Apache-2.0 |

**Local Kokoro speech** (**Install Kokoro voices**, `racecast radio provision --kokoro`),
installed into `runtime/radio-kokoro/`:

| Component | Source | License |
|---|---|---|
| CPython 3.12 (python-build-standalone) | [astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone) | PSF-2.0; the build's tooling is MPL-2.0 |
| Kokoro-82M model and voices | [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Apache-2.0 |
| kokoro-onnx | [thewh1teagle/kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | MIT |
| ONNX Runtime | [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime) | MIT |
| **phonemizer-fork** | [PyPI](https://pypi.org/project/phonemizer-fork/) | **GPL-3.0-or-later** |
| **eSpeak NG**, loaded through espeakng-loader (MIT) | [espeak-ng/espeak-ng](https://github.com/espeak-ng/espeak-ng) | **GPL-3.0** |
| Other Python packages | `src/assets/driver-radio/kokoro-requirements.txt` | MIT, BSD, Apache-2.0, PSF-2.0 and similar, per package |

### How the GPL components are used

phonemizer-fork and eSpeak NG convert text to phonemes for Kokoro. They run only inside
the Kokoro worker process, under the private Python runtime. The relay talks to that
process over a pipe and exchanges text and audio. The worker script
`src/scripts/driver_radio_kokoro_worker.py` is the one racecast file that loads them.

The source code of these components is available from the links above. If you pass the
`runtime/radio-kokoro/` folder on to someone else, you are distributing GPL software and
its obligations apply to you. `racecast profile export` and racecast's own package do not
include that folder. To stay clear of these components, do not install Kokoro and select
Google Studio-Q instead.
