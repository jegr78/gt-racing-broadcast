"""Kokoro speech worker. Runs under the private runtime's interpreter, never in the relay.

Stdlib plus the private runtime's packages only: it cannot import racecast modules.
Arguments: <model dir> <threads> [--check]. Requests on stdin and replies on stdout are
JSON lines; the first reply is {"ready": true}.
"""
import base64
import io
import json
import os
import sys
import wave

MODEL = "kokoro-v1.0.int8.onnx"
VOICES = "voices-v1.0.bin"
CHECK_TEXT = "Racecast driver radio speech check."
CHECK_VOICE = "am_michael"


def to_wav(samples, rate):
    import numpy
    pcm = (numpy.clip(samples, -1, 1) * 32767).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm.tobytes())
    return out.getvalue()


def load(models, threads):
    import onnxruntime
    from kokoro_onnx import Kokoro
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = threads
    session = onnxruntime.InferenceSession(os.path.join(models, MODEL), sess_options=options,
                                           providers=["CPUExecutionProvider"])
    return Kokoro.from_session(session, os.path.join(models, VOICES))


def reply(doc):
    sys.stdout.write(json.dumps(doc) + "\n")
    sys.stdout.flush()


def main(argv):
    if len(argv) not in (2, 3) or argv[2:] not in ([], ["--check"]) or not argv[1].isdigit():
        reply({"error": "invalid_request"})
        return 2
    engine = load(argv[0], int(argv[1]))
    if argv[2:]:
        samples, rate = engine.create(CHECK_TEXT, voice=CHECK_VOICE, lang="en-us")
        reply({"ok": True, "audio_seconds": len(samples) / rate})
        return 0
    reply({"ready": True})
    for line in sys.stdin:
        ident = None
        try:
            request = json.loads(line)
            ident = request["id"]
            samples, rate = engine.create(request["text"], voice=request["voice"], lang="en-us")
            reply({"id": ident, "audio": base64.b64encode(to_wav(samples, rate)).decode("ascii")})
        except Exception:  # noqa: BLE001 - one bad request must not end the worker
            reply({"id": ident, "error": "speech_failed"})
    return 0


if __name__ == "__main__":
    if hasattr(os, "nice"):
        os.nice(10)
    raise SystemExit(main(sys.argv[1:]))
