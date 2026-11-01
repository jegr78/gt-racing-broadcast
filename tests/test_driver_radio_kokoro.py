#!/usr/bin/env python3
"""Local Kokoro speech: provisioning publishes only after a check; the worker is owned and bounded."""
import base64
import io
import sys
import tarfile
import tempfile
import textwrap
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/scripts'))
import driver_radio_kokoro as kokoro
import driver_radio_access as access
import driver_radio_providers as providers
from driver_radio_process import ProviderError
from test_driver_radio_service import Fixture


def rejected(fn, code):
    try:
        fn()
    except ProviderError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError('Expected ' + code)


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b'\0\0' * 2400)
    return out.getvalue()


# A stand-in for the private interpreter and worker, speaking the same JSON lines.
FAKE_WORKER = textwrap.dedent("""
    import json, sys, time
    audio = sys.argv[1]
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        if request["text"] == "crash":
            raise SystemExit(3)
        if request["text"] == "slow":
            time.sleep(1.5)
        if request["text"] == "bad" or "5.7" in request["text"]:
            print(json.dumps({"id": request["id"], "error": "speech_failed"}), flush=True)
            continue
        print(json.dumps({"id": request["id"], "audio": audio}), flush=True)
""")


def speech(directory, **kwargs):
    script = Path(directory) / 'fake_worker.py'
    script.write_text(FAKE_WORKER, encoding='utf8')
    argv = [sys.executable, str(script), base64.b64encode(wav()).decode()]
    return kokoro.KokoroSpeech(directory, argv=argv, ready_check=lambda: True, **kwargs)


def t_worker_answers_caches_and_bounds_a_late_reply():
    with tempfile.TemporaryDirectory() as d:
        s = speech(d)
        try:
            stopped = threading.Event()
            first = s.synthesize('Fuel status.', stopped, 30)
            assert first == wav()
            proc = s._proc
            assert s.synthesize('Fuel status.', stopped, 30) == first and s._proc is proc
            rejected(lambda: s.synthesize('bad', stopped, 30), 'speech_failed')
            rejected(lambda: s.synthesize(' ', stopped, 30), 'invalid_text')
            rejected(lambda: s.synthesize('slow', stopped, .2), 'timed_out')
            # The late reply to "slow" must not answer this request.
            assert s.synthesize('Pace status.', stopped, 30) == wav() and s._proc is proc
            cancelled = threading.Event()
            cancelled.set()
            rejected(lambda: s.synthesize('Other.', cancelled, 30), 'cancelled')
        finally:
            s.close()
        assert s._proc is None


def t_numbers_are_spoken_with_their_point():
    assert kokoro.speakable('Fuel for 5.7 laps.') == 'Fuel for 5 point 7 laps.'
    assert kokoro.speakable('Lap 14, 1:23.5. Fuel for 4.5 laps.') == 'Lap 14, 1 minute 23 point 5. Fuel for 4 point 5 laps.'
    assert kokoro.speakable('2:05.1') == '2 minutes 5 point 1'
    assert kokoro.speakable('Lap 8, 1:22.0. P3.') == 'Lap 8, 1 minute 22 point 0. position 3.'
    assert kokoro.speakable('Last lap 83.467 seconds.') == 'Last lap 83 point 4 6 7 seconds.'
    assert kokoro.speakable('Best lap 1:23.467.') == 'Best lap 1 minute 23 point 4 6 7.'
    assert kokoro.speakable('Lap 14 of 20, RM.') == 'Lap 14 of 20, RM.'
    with tempfile.TemporaryDirectory() as d:
        s = speech(d)
        try:
            assert s.synthesize('Fuel for 5.7 laps.', threading.Event(), 30) == wav(), 'the worker gets the spoken form'
        finally:
            s.close()


def t_a_crashed_worker_restarts_after_its_backoff():
    with tempfile.TemporaryDirectory() as d:
        now = [100.0]
        s = speech(d, clock=lambda: now[0])
        try:
            stopped = threading.Event()
            assert s.synthesize('One.', stopped, 30) == wav()
            rejected(lambda: s.synthesize('crash', stopped, 30), 'speech_failed')
            s._proc.wait(10)
            rejected(lambda: s.synthesize('Two.', stopped, 30), 'speech_failed')
            now[0] += kokoro.RETRY_S
            assert s.synthesize('Two.', stopped, 30) == wav()
        finally:
            s.close()


def t_an_uninstalled_runtime_is_unconfigured_and_starts_nothing():
    with tempfile.TemporaryDirectory() as d:
        s = kokoro.KokoroSpeech(d)
        s.prepare()
        rejected(lambda: s.synthesize('Fuel.', threading.Event(), 5), 'unconfigured')
        assert s._proc is None and not kokoro.ready(d)
        s.close()


def tar_with_python():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as archive:
        for name in ('python/python.exe', 'python/bin/python3'):
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b'x'))
    return out.getvalue()


def provision_with(machine, check):
    calls = []

    def fetch(url, digest, maximum):
        calls.append((url, digest))
        return tar_with_python() if url.endswith('.tar.gz') else b'model'

    def run(argv, timeout):
        calls.append(argv)
        return check(argv)
    result = kokoro.provision(machine, fetch=fetch, run=run, platform_key='linux_x64')
    return result, calls


def t_provision_publishes_only_after_the_speech_check():
    ok = SimpleNamespace(returncode=0, stdout='{"ok": true, "audio_seconds": 2.0}\n', stderr='')
    with tempfile.TemporaryDirectory() as d:
        result, calls = provision_with(d, lambda argv: ok)
        assert result['kokoro'] == 'ready' and kokoro.ready(d)
        commands = [c for c in calls if isinstance(c, list)]
        pip = commands[0]
        assert '--require-hashes' in pip and '--no-deps' in pip and '-I' in pip and pip[-1].endswith('kokoro-requirements.txt')
        assert commands[1][-1] == '--check'
        assert [c[0] for c in calls if isinstance(c, tuple)] == [kokoro.RUNTIMES['linux_x64'][0], *[v[0] for v in kokoro.MODEL_FILES.values()]]
        assert kokoro.provision(d, fetch=None, run=None)['kokoro'] == 'ready', 'installed runtime does not download again'
    for bad, text in ((SimpleNamespace(returncode=1, stdout='', stderr='boom\n'), 'boom'),
                      (SimpleNamespace(returncode=0, stdout='{"ok": false}\n', stderr=''), 'no audio'),
                      (SimpleNamespace(returncode=0, stdout='', stderr=''), 'no result')):
        with tempfile.TemporaryDirectory() as d:
            check = lambda argv, bad=bad: bad if argv[-1] == '--check' else ok  # noqa: E731
            try:
                provision_with(d, check)
            except ValueError as exc:
                assert text in str(exc), exc
            else:
                raise AssertionError('A failed check must not publish')
            assert not (Path(d) / 'radio-kokoro').exists() and not kokoro.ready(d)


def t_unsupported_platform_and_pins_are_explicit():
    assert kokoro.target('Windows', 'AMD64') == 'windows' and kokoro.target('Linux', 'aarch64') == 'linux_arm64'
    assert kokoro.target('Darwin', 'arm64') == 'macos_arm64'
    try:
        kokoro.target('Darwin', 'x86_64')
    except ValueError:
        pass  # The expected refusal.
    else:
        raise AssertionError('Intel macOS has no ONNX Runtime wheel')
    for url, digest in [*kokoro.RUNTIMES.values(), *kokoro.MODEL_FILES.values()]:
        assert url.startswith('https://github.com/') and len(digest) == 64, url
    lines = [line for line in kokoro.requirements().read_text(encoding='utf8').splitlines() if not line.startswith('#')]
    assert any(line.startswith('kokoro-onnx==') for line in lines) and all('--hash=sha256:' in line and '==' in line for line in lines)


def t_settings_default_to_kokoro_and_michael_and_reject_unknown_values():
    assert access._provider_settings({}) == {'speech': False, 'engine': 'kokoro', 'voice': 'am_michael'}
    assert access._provider_settings({'speech': True}) == {'speech': True, 'engine': 'kokoro', 'voice': 'am_michael'}
    chosen = {'speech': True, 'engine': 'google', 'voice': 'am_fenrir'}
    assert access._provider_settings(chosen) == chosen
    for bad in ({'engine': 'other'}, {'voice': '../x'}, {'voice': 'am_onyx'}, {'other': 1}):
        try:
            access._provider_settings(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


class Speech:
    def __init__(self):
        self.calls = []

    def prepare(self):
        pass

    def synthesize(self, text, stopped, budget, **kwargs):
        self.calls.append(kwargs)
        return wav()


def t_enricher_uses_the_selected_engine_and_voice_without_fallback():
    with tempfile.TemporaryDirectory() as d:
        f = Fixture(d)
        f.access.update({'expected': 1, 'providers': {'speech': True, 'engine': 'kokoro', 'voice': 'bm_george'}})
        f.poll()
        google, local = Speech(), Speech()
        worker = providers.Enricher(f.service, speech=google, kokoro=local)
        first = f.request('pace')
        worker.step('audio')
        assert local.calls == [{'voice': 'bm_george'}] and google.calls == []
        message = next(m for m in f.service.history()['messages'] if m['id'] == first['id'])
        assert message['audio_state'] == 'ready', message

        class Broken(Speech):
            def synthesize(self, *args, **kwargs):
                raise ProviderError('speech_failed')
        broken = providers.Enricher(f.service, speech=google, kokoro=Broken())
        second = f.request('status')
        broken.step('audio')
        message = next(m for m in f.service.history()['messages'] if m['id'] == second['id'])
        assert message['audio_state'] == 'failed' and google.calls == [], 'no silent fallback to the cloud'


def t_worker_source_stays_free_of_racecast_imports():
    source = (ROOT / 'src/scripts/driver_radio_kokoro_worker.py').read_text(encoding='utf8')
    assert 'import driver_radio' not in source and 'import ai_' not in source and 'http_util' not in source
    assert 'driver_radio_kokoro' in (ROOT / 'tools/build-binary.py').read_text(encoding='utf8')


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):
            fn()
            print('PASS', name)
