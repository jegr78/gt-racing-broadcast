"""The driver-radio setup and messages kept with the telemetry recording (#1079).

The radio fills only empty fields of the recording context; values entered in the
Control Center are never overwritten. A prepared context fills the radio setup
whenever the radio session starts. A context revision the radio wrote itself does
not reset the radio session; a Control Center edit still does. Every published
radio message is appended to a recording-owned journal next to the recording.
"""
import copy
import json
import os
import threading
import time

import gt7_context

JOURNAL_SUFFIX = gt7_context.RADIO_JOURNAL_SUFFIX
JOURNAL_MAX_BYTES = 4 * 1024 * 1024
# Radio setup field -> recording context setting.
SETTINGS = dict(laps='laps', duration_min='duration_min', mandatory_stops='pit_stops',
                mandatory_compounds='required_tyres', session='session_type', fuel_reserve_l='fuel_reserve_l')
SOURCES = ('radio voice', 'driver desk')


def journal_path(recording):
    """The journal next to a recording, for a finished or a still-written capture."""
    return gt7_context._stem(recording) + JOURNAL_SUFFIX


def _session_key(snapshot):
    """The recording session of the newest lap, as the context numbers it."""
    rows = [snapshot.get('current') or {}, *reversed((snapshot.get('laps') or [])[-512:])]
    for row in rows:
        if row.get('session_id') == snapshot.get('session_id') and type(row.get('recording_session')) is int:
            return str(max(1, row['recording_session']))
    return '1'


def _service_lap(snapshot):
    """The lap of the stop just made: the newest pit lap of this session, else the running lap."""
    current = snapshot.get('current') or {}
    rows = [current, *reversed((snapshot.get('laps') or [])[-512:])]
    for row in rows:
        if row.get('session_id') == snapshot.get('session_id') and 'pit' in (row.get('lap_role'), row.get('analysis_role')):
            if type(row.get('lap')) is int:
                return row['lap']
    return current.get('lap') if type(current.get('lap')) is int else None


class Link:
    """Owns the radio's writes to one recording context (or the prepared one)."""

    def __init__(self, rec_dir, recording_path, clock=time.time):
        self.rec_dir, self.recording_path, self.clock = rec_dir, recording_path, clock
        self._lock = threading.Lock()
        self._own = set()          # (source_id, revision) the radio wrote itself
        self._external = {}        # source_id -> last revision not written by the radio
        self._sources = {}         # recording path -> context source identity

    def _path(self):
        path = self.recording_path() if callable(self.recording_path) else None
        if path and path.endswith('.part') and not os.path.isfile(path):
            path = path[:-5]  # finalized since the recorder reported it
        return path

    def _store(self):
        path = self._path()
        if path:
            return gt7_context.Store.for_recording(path)
        return gt7_context.Store.prepared(self.rec_dir) if self.rec_dir else None

    def _source(self):
        """The context the engineer reads now, named as gt7_context names it."""
        path = self._path()
        if not path:
            return 'prepared'
        stem = gt7_context._stem(path)
        if stem not in self._sources:
            self._sources = {stem: gt7_context.source_identity(path)}
        return self._sources[stem]

    # --- radio binding: own writes keep the radio session --------------------------------

    def mask(self, snapshot):
        """The snapshot as the radio binds to it: a context revision the radio wrote
        itself reads as the last revision someone else wrote."""
        revision = snapshot.get('context_revision')
        try:
            source = self._source()
        except (OSError, ValueError, TypeError, gt7_context.gt7_recording.RecordingError):
            return snapshot  # An unreadable capture binds as it is.
        with self._lock:
            key = (source, revision)
            if key in self._own and source in self._external:
                return dict(snapshot, context_revision=self._external[source])
            if revision is not None:
                self._external[source] = revision
        return snapshot

    def _wrote(self, doc):
        with self._lock:
            self._own.add((doc['source_id'], doc['revision']))
            if len(self._own) > 256:
                self._own = set(sorted(self._own, key=lambda k: k[1])[-128:])

    # --- context -> radio ----------------------------------------------------------------

    def prefill(self, snapshot):
        """The radio setup a prepared or recording context already declares."""
        try:
            store = self._store()
            if store is None:
                return {}
            data = store.read()['data']
        except (OSError, ValueError, TypeError, gt7_context.gt7_recording.RecordingError):
            return {}
        session = data.get('sessions', {}).get(_session_key(snapshot), {})
        settings = session.get('settings', {})
        setup = {radio: settings[field] for radio, field in SETTINGS.items()
                 if settings.get(field) is not None and settings.get(field) != 'unknown'}
        if 'laps' in setup and 'duration_min' in setup:
            setup.pop('duration_min')
        if setup.get('mandatory_compounds') == []:
            setup.pop('mandatory_compounds')
        first = min(session.get('stints', []), key=lambda s: s['start_lap'], default=None)
        if first and first['start_lap'] <= 1 and first.get('compound'):
            setup['starting_compound'] = first['compound']
        return setup

    # --- radio -> context ----------------------------------------------------------------

    def _edit(self, snapshot, change, operation):
        try:
            store = self._store()
        except (OSError, ValueError, TypeError, gt7_context.gt7_recording.RecordingError):
            return False
        if store is None:
            return False
        for _attempt in range(3):
            try:
                doc = store.read()
                data = copy.deepcopy(doc['data'])
                session = data.setdefault('sessions', {}).setdefault(_session_key(snapshot), {})
                session.setdefault('settings', {})
                session.setdefault('stints', [])
                if not change(session):
                    return False
                saved = store.save(data, expected=doc['revision'], draft=doc.get('draft', {}), operation=operation)
                self._wrote(saved)
                return True
            except gt7_context.Conflict:
                continue  # Someone saved in between: reread and fill again.
            except (OSError, ValueError, TypeError, gt7_context.gt7_recording.RecordingError):
                return False  # Context writes are best effort; the radio setup stands.
        return False

    def apply_setup(self, snapshot, setup, source):
        """Fill the empty context fields from a radio setup; never overwrite."""
        def change(session):
            settings, changed = session['settings'], False
            for radio, field in SETTINGS.items():
                value = setup.get(radio)
                if value is None or value == [] or settings.get(field) not in (None, 'unknown', []):
                    continue
                if field in ('laps', 'duration_min') and any(settings.get(f) is not None for f in ('laps', 'duration_min')):
                    continue  # one race length mode, whichever the context already has
                settings[field] = copy.deepcopy(value)
                changed = True
            compound = setup.get('starting_compound')
            if compound:
                first = min(session['stints'], key=lambda s: s['start_lap'], default=None)
                if first is None:
                    session['stints'].append(dict(id='radio-start', start_lap=1, compound=compound, confirmed=True,
                                                  tyre_service=False, warmup_laps=0, strategy={}, source=source))
                    changed = True
                elif first['start_lap'] <= 1 and not first.get('compound'):
                    first.update(compound=compound, confirmed=True, source=source)
                    changed = True
            return changed
        return self._edit(snapshot, change, f'radio setup ({source})')

    def confirm_service(self, snapshot, compound, source):
        """A confirmed tyre change becomes a confirmed stint at the lap of the stop."""
        lap = _service_lap(snapshot)
        if lap is None:
            return False
        def change(session):
            existing = next((s for s in session['stints'] if s['start_lap'] == lap and lap > 1), None)
            if existing is not None:
                if existing.get('compound') and existing.get('tyre_service') is not None:
                    return False
                existing.setdefault('source', source)
                existing.update(compound=existing.get('compound') or compound,
                                tyre_service=True if existing.get('tyre_service') is None else existing['tyre_service'])
                return True
            session['stints'].append(dict(id=f'radio-{lap}-{int(self.clock()*1000)}', start_lap=lap, compound=compound,
                                          confirmed=True, tyre_service=True, warmup_laps=1, strategy={}, source=source))
            return True
        return self._edit(snapshot, change, f'radio tyre change ({source})')

    # --- journal -------------------------------------------------------------------------

    def journal(self, event):
        """Append one radio event to the journal of the running recording; no recording, no journal."""
        path = self.recording_path() if callable(self.recording_path) else None
        if not path:
            return False
        target = journal_path(path)
        line = (json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
        with self._lock:
            try:
                if os.path.getsize(target) + len(line) > JOURNAL_MAX_BYTES:
                    return False
            except FileNotFoundError:
                pass  # The first event creates the journal.
            with open(target, 'ab') as handle:
                handle.write(line)
        return True


def read_journal(recording):
    """The journal events of one recording, skipping a damaged line."""
    events = []
    try:
        with open(journal_path(recording), encoding='utf-8', errors='replace') as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except FileNotFoundError:
        pass  # A recording without radio has no journal.
    return events
