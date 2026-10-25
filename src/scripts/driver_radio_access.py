"""Explicit driver-radio grants and revisioned, volatile Solo race declarations."""

import copy
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import gt7_context

MAX_BYTES = 16384
MAX_DRIVERS = 128
_LOCK = threading.RLock()


class RadioError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _require(condition, code='invalid_body', message='Invalid radio declaration'):
    if not condition:
        raise RadioError(code, message)


def _voice(value):
    return (isinstance(value, dict)
            and set(value) == {'enabled', 'microphone', 'input_language'}
            and type(value['enabled']) is bool
            and value['input_language'] in ('en', 'de')
            and (value['microphone'] is None or isinstance(value['microphone'], str)
                 and 0 < len(value['microphone']) <= 256
                 and all(ord(c) >= 32 for c in value['microphone'])))


def _valid(doc):
    return (isinstance(doc, dict)
            and set(doc) == {'version', 'revision', 'enabled', 'drivers', 'voice'}
            and type(doc['version']) is int and doc['version'] == 1
            and type(doc['revision']) is int and doc['revision'] >= 0
            and type(doc['enabled']) is bool
            and isinstance(doc['drivers'], list) and len(doc['drivers']) <= MAX_DRIVERS
            and all(isinstance(s, str) and re.fullmatch(r'[a-z0-9-]{1,160}', s) for s in doc['drivers'])
            and len(set(doc['drivers'])) == len(doc['drivers']) and _voice(doc['voice']))


class Store:
    """Producer-owned profile settings. Console roles never imply assignment."""

    def __init__(self, profile_runtime, available=False):
        self.path = Path(profile_runtime).resolve() / 'driver-radio-access.json'
        self.available = bool(available)

    def read(self):
        try:
            if self.path.is_symlink():
                raise OSError('Foreign settings link')
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return dict(version=1, revision=0, enabled=False, drivers=[],
                        voice=dict(enabled=False, microphone=None, input_language='en'))
        except OSError as exc:
            raise RadioError('settings_unavailable', 'Radio settings are unreadable; repair locally') from exc
        try:
            doc = json.loads(raw)
            if len(raw) > MAX_BYTES or not _valid(doc):
                raise ValueError('schema')
            return doc
        except (ValueError, TypeError, RecursionError) as exc:
            raise RadioError('settings_unavailable', 'Radio settings are unreadable; repair locally') from exc

    def update(self, body):
        _require(self.available, 'source_unavailable', 'Radio requires a Solo POV telemetry profile')
        _require(isinstance(body, dict) and not set(body) - {'expected', 'enabled', 'drivers', 'voice'},
                 'invalid_settings', 'Unsupported radio settings')
        try:
            with _LOCK, gt7_context._file_lock(str(self.path)):
                doc = self.read()
                _require(type(body.get('expected')) is int and body['expected'] == doc['revision'],
                         'conflict', 'Radio settings changed; reload before saving')
                doc.update(copy.deepcopy({k: v for k, v in body.items() if k != 'expected'}))
                doc['revision'] += 1
                _require(_valid(doc) and len(json.dumps(doc).encode()) <= MAX_BYTES,
                         'invalid_settings', 'Invalid or oversized radio settings')
                gt7_context._atomic(str(self.path), doc)
                return doc
        except gt7_context.Conflict as exc:
            raise RadioError('conflict', 'Radio settings are busy; retry explicitly') from exc

    def authorize(self, subject):
        _require(self.available, 'source_unavailable', 'Radio requires a Solo POV telemetry profile')
        doc = self.read()
        _require(doc['enabled'], 'feature_disabled', 'The producer has disabled driver radio')
        _require(subject in doc['drivers'], 'forbidden', 'Explicit driver radio assignment is required')
        return dict(version=1, enabled=True, revision=doc['revision'])


def validate_setup(value):
    _require(isinstance(value, dict) and not set(value) - {'laps', 'duration_min', 'starting_compound', 'fuel_reserve_l'})
    for key, limit in (('laps', 100000), ('duration_min', 10080), ('fuel_reserve_l', 1000)):
        v = value.get(key)
        if v is not None:
            _require(type(v) in (int, float) and 0 <= v <= limit)
            if key == 'laps':
                _require(type(v) is int and v > 0)
            if key == 'duration_min':
                _require(v > 0)
    _require(value.get('laps') is None or value.get('duration_min') is None)
    _require(value.get('starting_compound') is None or value['starting_compound'] in gt7_context.COMPOUNDS)
    return copy.deepcopy(value)


class Session:
    """One bounded in-memory setup, invalidated by current evidence identity."""

    def __init__(self):
        self._lock = threading.RLock()
        self._binding = None
        self._revision = 0
        self._setup = {}
        self._service = None

    def read(self, binding):
        with self._lock:
            if binding != self._binding:
                if self._binding is not None:
                    self._revision += 1
                self._binding = copy.deepcopy(binding)
                self._setup, self._service = {}, None
            return copy.deepcopy(dict(version=1, binding=self._binding, revision=self._revision,
                                      setup=self._setup, service=self._service))

    def update(self, binding, body, service=False, lap_id=None):
        with self._lock:
            self.read(binding)
            _require(binding is not None, 'session_unavailable', 'No active telemetry session')
            field = 'compound' if service else 'setup'
            _require(isinstance(body, dict) and set(body) == {'binding', 'expected', field})
            _require(type(body['expected']) is int and body['expected'] == self._revision
                     and body['binding'] == binding, 'conflict', 'Radio session or setup changed; reload')
            if service:
                _require(body['compound'] in gt7_context.COMPOUNDS)
                self._service = dict(compound=body['compound'], lap_id=lap_id,
                                     confirmed_at=time.time(), revision=self._revision + 1)
            else:
                self._setup = validate_setup(body['setup'])
            self._revision += 1
            return self.read(binding)


def same_origin(headers):
    """Allow native clients without Origin, reject cross-site browser mutations."""
    try:
        host = headers.get('Host', '')
        target = urlparse('http://' + host)
        if (not target.hostname or target.username is not None or target.password is not None
                or target.path or target.query or target.fragment):
            return False
        for field in ('Origin', 'Referer'):
            value = headers.get(field)
            if value:
                parsed = urlparse(value)
                if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != host.lower():
                    return False
        return headers.get('Sec-Fetch-Site') in (None, 'none', 'same-origin')
    except (ValueError, TypeError):
        return False
