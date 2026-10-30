"""Per-profile AI Race Engineer and AI Coach switches, and revisioned, volatile Solo race declarations."""

import copy
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import gt7_context
import driver_radio_voice_config
import driver_radio_assessment

MAX_BYTES = 16384
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


_AGENT = r'[a-z0-9][a-z0-9_-]{0,63}'
FEATURES = ('engineer', 'coach')
# What the producer must switch on, as the relay reports a disabled feature.
DISABLED = {'engineer': 'Enable the AI Race Engineer for this profile',
            'coach': 'Enable the AI Coach for this profile'}


def _agent(value):
    return value is None or isinstance(value, str) and re.fullmatch(_AGENT, value) is not None


def _provider_settings(value):
    if (not isinstance(value, dict) or set(value) - {'speech'}
            or type(value.get('speech', False)) is not bool):
        raise ValueError('Invalid radio service selection')
    return dict(speech=value.get('speech', False))


def _feature_settings(value, current=None):
    """Both AI feature switches, each with its own optional agent. A partial update
    changes only the fields it names; anything unknown is invalid."""
    out = copy.deepcopy(current) if current else {f: dict(enabled=False, agent=None) for f in FEATURES}
    if not isinstance(value, dict) or set(value) - set(FEATURES):
        raise ValueError('Invalid radio feature switches')
    for feature, fields in value.items():
        if not isinstance(fields, dict) or set(fields) - {'enabled', 'agent'}:
            raise ValueError('Invalid radio feature switches')
        out[feature].update(fields)
    for fields in out.values():
        if type(fields['enabled']) is not bool or not _agent(fields['agent']):
            raise ValueError('Invalid radio feature switches')
    return out


def _legacy(doc):
    """Version 1 had one switch, a per-driver identity list and one agent. Both
    features inherit the switch and the agent; the identity list has no successor."""
    if not (isinstance(doc, dict) and doc.get('version') == 1 and type(doc.get('enabled')) is bool
            and isinstance(doc.get('drivers'), list)
            and not set(doc) - {'version', 'revision', 'enabled', 'drivers', 'voice', 'limits', 'providers', 'native'}):
        return doc
    providers = doc.get('providers', {})
    if not isinstance(providers, dict) or set(providers) - {'speech', 'agent'} or not _agent(providers.get('agent')):
        raise ValueError('schema')
    feature = dict(enabled=doc['enabled'], agent=providers.get('agent'))
    out = {k: v for k, v in doc.items() if k not in ('enabled', 'drivers')}
    out.update(version=2, features={f: dict(feature) for f in FEATURES},
               providers={k: v for k, v in providers.items() if k != 'agent'})
    return out


def _valid(doc):
    try:
        features = doc['features'] == _feature_settings(doc['features'])
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return (isinstance(doc, dict)
            and {'version', 'revision', 'features', 'voice'} <= set(doc)
            and not set(doc) - {'version', 'revision', 'features', 'voice', 'limits', 'providers', 'native'}
            and type(doc['version']) is int and doc['version'] == 2
            and type(doc['revision']) is int and doc['revision'] >= 0
            and features and _voice(doc['voice']))


class Store:
    """Producer-owned profile settings: one switch per AI feature. Any verified
    Console sign-in may use an enabled feature; there is no per-driver grant."""

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
            return dict(version=2, revision=0, features=_feature_settings({}),
                        voice=dict(enabled=False, microphone=None, input_language='en'),
                        limits=driver_radio_assessment.settings(), providers=_provider_settings({}),
                        native=driver_radio_voice_config.settings())
        except OSError as exc:
            raise RadioError('settings_unavailable', 'Radio settings are unreadable; repair locally') from exc
        try:
            doc = _legacy(json.loads(raw))  # Migrated on read, written on the next update.
            if len(raw) > MAX_BYTES or not _valid(doc):
                raise ValueError('schema')
            doc['limits'] = driver_radio_assessment.settings(doc.get('limits'))
            doc['providers'] = _provider_settings(doc.get('providers', {}))
            doc['native'] = driver_radio_voice_config.settings(doc.get('native'))
            return doc
        except (ValueError, TypeError, RecursionError) as exc:
            raise RadioError('settings_unavailable', 'Radio settings are unreadable; repair locally') from exc

    def update(self, body):
        _require(self.available, 'source_unavailable', 'Radio requires a Solo POV telemetry profile')
        _require(isinstance(body, dict) and not set(body) - {'expected', 'features', 'voice', 'limits', 'providers', 'native'},
                 'invalid_settings', 'Unsupported radio settings')
        try:
            with _LOCK, gt7_context._file_lock(str(self.path)):
                doc = self.read()
                _require(type(body.get('expected')) is int and body['expected'] == doc['revision'],
                         'conflict', 'Radio settings changed; reload before saving')
                changes = copy.deepcopy({k: v for k, v in body.items() if k != 'expected'})
                try:
                    if 'features' in changes:
                        changes['features'] = _feature_settings(changes['features'], doc['features'])
                    doc.update(changes)
                    doc['limits'] = driver_radio_assessment.settings(doc.get('limits'))
                    doc['providers'] = _provider_settings(doc.get('providers', {}))
                    doc['native'] = driver_radio_voice_config.settings(doc.get('native'))
                except ValueError as exc:
                    raise RadioError('invalid_settings', 'Invalid radio features, limits or service selection') from exc
                doc['revision'] += 1
                _require(_valid(doc) and len(json.dumps(doc).encode()) <= MAX_BYTES,
                         'invalid_settings', 'Invalid or oversized radio settings')
                gt7_context._atomic(str(self.path), doc)
                return doc
        except gt7_context.Conflict as exc:
            raise RadioError('conflict', 'Radio settings are busy; retry explicitly') from exc

    def enabled(self, feature):
        """Whether one AI feature is switched on for this Solo POV profile."""
        return (self.available and feature in FEATURES
                and self.read()['features'][feature]['enabled'] is True)

    def authorize(self, feature):
        """The caller has already verified the Console token; this checks only the
        profile and the feature switch."""
        _require(feature in FEATURES, 'invalid_body', 'Unknown radio feature')
        _require(self.available, 'source_unavailable', 'Radio requires a Solo POV telemetry profile')
        doc = self.read()
        _require(doc['features'][feature]['enabled'], 'feature_disabled', DISABLED[feature])
        return dict(version=2, enabled=True, revision=doc['revision'], feature=feature)


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
