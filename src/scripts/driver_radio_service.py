"""Relay-owned bounded radio messages and one acknowledged native playback slot."""

import collections
import copy
import threading
import time
import uuid
from dataclasses import dataclass

import driver_radio_access as access
import driver_radio_assessment as assessment

HISTORY_MAX = 32
QUEUE_MAX = 4
ROUTINE_TTL = 30
URGENT_TTL = 15
PACE_COOLDOWN = 60


@dataclass
class Ticket:
    id: str
    message: dict
    cancelled: threading.Event


class Service:
    def __init__(self, settings, session, source, clock=time.monotonic, wall=time.time):
        self.settings, self.session, self.source = settings, session, source
        self.clock, self.wall = clock, wall
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._closed = False
        self._key = None
        self._signature = None
        self._read_at = float('-inf')
        self._epoch = uuid.uuid4().hex
        self._revision = 0
        self._messages = collections.OrderedDict()
        self._queue = []
        self._audio = {}
        self._ticket = None
        self._assessment = None
        self._binding = None
        self._live = False
        self._enabled = False
        self._fuel_level = 0
        self._previous_fuel = None
        self._refuel_total = 0
        self._voice_enabled = False
        self._pace_at = float('-inf')
        self._pace_key = None

    def start(self):
        with self._lock:
            if self._closed or self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name='driver-radio', daemon=True)
            self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.refresh(force=True)
            except (access.RadioError, OSError, ValueError, TypeError):
                with self._lock:
                    self._invalidate()
            self._stop.wait(.25)

    def _cancel(self):
        for message in self._messages.values():
            if message['state'] in ('queued', 'playing'):
                message['state'] = 'cancelled'
        self._queue.clear()
        self._audio.clear()
        if self._ticket is not None:
            self._ticket.cancelled.set()
        self._revision += 1

    def _invalidate(self):
        self._cancel()
        self._messages.clear()
        self._assessment = None
        self._signature = None
        self._epoch = uuid.uuid4().hex
        self._fuel_level, self._previous_fuel, self._refuel_total = 0, None, 0
        self._pace_key, self._pace_at = None, float('-inf')
        self._live = False

    def _expire(self, now):
        for message in self._messages.values():
            if message['state'] in ('queued','playing') and not message['_native']:
                valid = {}
                for requester, authorize in message['_requesters'].items():
                    try:
                        if authorize():valid[requester] = authorize
                    except Exception:
                        continue  # An unavailable authorization provider fails closed.
                message['_requesters'] = valid
                if not valid:
                    message['state'] = 'cancelled'
                    self._audio.pop(message['id'], None)
                    if self._ticket and self._ticket.message['id'] == message['id']:
                        self._ticket.cancelled.set()
                    self._revision += 1
            if now >= message['_deadline'] and message['state'] in ('queued', 'playing'):
                message['state'] = 'expired'
                self._audio.pop(message['id'], None)
                if self._ticket and self._ticket.message['id'] == message['id']:
                    self._ticket.cancelled.set()
                self._revision += 1
        self._queue = [mid for mid in self._queue if mid in self._messages and self._messages[mid]['state'] == 'queued']

    def _public(self, message):
        return copy.deepcopy({k:v for k,v in message.items() if not k.startswith('_')})

    def _publish(self, preset, kind, severity, result, now, requester=None, authorization=None):
        # Superseded routine answers cannot form a spoken backlog.
        for mid in list(self._queue):
            old = self._messages[mid]
            if old['preset'] == preset and (old['severity'] != 'urgent' or severity == 'urgent'):
                old['state'] = 'superseded'
                self._queue.remove(mid)
                self._audio.pop(mid, None)
        while len(self._queue) >= QUEUE_MAX:
            disposable = next((mid for mid in self._queue if self._messages[mid]['severity'] != 'urgent'), None)
            if disposable is None:
                raise access.RadioError('rate_limited', 'Radio work queue is full; retry after the current answer')
            self._messages[disposable]['state'] = 'superseded'
            self._queue.remove(disposable)
            self._audio.pop(disposable, None)
        if severity == 'urgent' and self._ticket is not None and self._ticket.message['severity'] != 'urgent':
            self._ticket.cancelled.set()
            old = self._messages.get(self._ticket.message['id'])
            if old is not None:old['state'] = 'cancelled'
        ttl = URGENT_TTL if severity == 'urgent' else ROUTINE_TTL
        mid = uuid.uuid4().hex
        message = dict(id=mid, version=1, epoch=self._epoch, binding=self._binding,
                       kind=kind, preset=preset, severity=severity, text=result['answers'][preset],
                       evidence={k:result[k] for k in ('fuel','pace','tyres','uncertainties')},
                       created_at=self.wall(), expires_at=self.wall()+ttl, state='queued',
                       audio_state='pending', ai_state='pending', _deadline=now+ttl, _created=now,
                       _native=requester is None, _requesters={requester:authorization} if requester is not None else {})
        self._messages[mid] = copy.deepcopy(message)
        self._queue.append(mid)
        self._queue.sort(key=lambda ident:self._messages[ident]['severity'] != 'urgent')
        while len(self._messages) > HISTORY_MAX:
            expired, _ = self._messages.popitem(last=False)
            if self._ticket is not None and self._ticket.message['id'] == expired:
                self._ticket.cancelled.set()
            self._audio.pop(expired, None)
            if expired in self._queue:self._queue.remove(expired)
        self._revision += 1
        return self._public(message)

    def refresh(self, force=False):
        """One cached assessment for an immutable live revision, regardless of readers."""
        with access._LOCK, self._lock:
            if self._closed:
                return self._state()
            config = self.settings.read()
            enabled = self.settings.available and config['enabled'] and bool(config['drivers'] or config['voice']['enabled'])
            now = self.clock()
            if (not force and self._key is not None and config['revision'] == self._key[1]
                    and now-self._read_at < .25):
                self._expire(now)
                return self._state()
            snapshot = self.source() if enabled else {}
            self._read_at = now
            bound = assessment.binding(snapshot)
            declared = self.session.read(bound)
            key = (bound, config['revision'], declared['revision'], enabled)
            if key != self._key:
                self._invalidate()
                self._key = copy.deepcopy(key)
            self._binding, self._enabled = bound, enabled
            self._voice_enabled = config['voice']['enabled'] and enabled
            now = self.clock()
            self._expire(now)
            signature = (key, snapshot.get('revision'), snapshot.get('receipt', {}).get('state'),
                         snapshot.get('replay', {}).get('active'))
            if enabled and signature != self._signature:
                result = assessment.assess(snapshot, declared, config.get('limits'))
                self._assessment = result
                if self._live and not result['live']:
                    self._cancel()
                self._live = result['live']
                self._signature = copy.deepcopy(signature)
                self._revision += 1
                if self._live:
                    self._announcements(result, now)
            return self._state()

    def _cancel_fuel_alerts(self):
        for mid, message in self._messages.items():
            if message['kind'] == 'fuel' and message['state'] in ('queued','playing'):
                message['state'] = 'cancelled'
                self._audio.pop(mid, None)
                if mid in self._queue:self._queue.remove(mid)
                if self._ticket is not None and self._ticket.message['id'] == mid:
                    self._ticket.cancelled.set()
                self._revision += 1

    def _announcements(self, result, now):
        fuel = result['fuel']
        current = fuel['current_l']
        if current is not None and self._previous_fuel is not None:
            if current > self._previous_fuel:
                self._refuel_total += current - self._previous_fuel
            elif current < self._previous_fuel:
                self._refuel_total = 0
        rearmed = (self._refuel_total >= 2 or fuel['usable_laps'] is not None
                   and fuel['usable_laps'] > result['limits']['fuel_warning_laps']+.5)
        level = {'normal':0,'warning':1,'urgent':2}[fuel['severity']]
        if rearmed or level < self._fuel_level:
            self._cancel_fuel_alerts()
        if rearmed:
            self._fuel_level, self._refuel_total = 0, 0
        self._previous_fuel = current
        if level > self._fuel_level:
            self._publish('fuel', 'fuel', fuel['severity'], result, now)
            self._fuel_level = level
        pace_key = tuple(result['pace']['recent_ids'])
        if result['pace']['state'] == 'slower' and pace_key != self._pace_key:
            if now - self._pace_at >= PACE_COOLDOWN:
                self._publish('pace', 'pace', 'normal', result, now)
                self._pace_at = now
            self._pace_key = pace_key

    def _state(self):
        return copy.deepcopy(dict(version=1, revision=self._revision, epoch=self._epoch,
                                  binding=self._binding, enabled=self._enabled and not self._closed,
                                  live=self._live and not self._closed, voice_enabled=self._voice_enabled and not self._closed,
                                  assessment=self._assessment))

    def history(self, since=None):
        self.refresh()
        with self._lock:
            changed = since != self._revision
            return dict(version=1, revision=self._revision, epoch=self._epoch, binding=copy.deepcopy(self._binding),
                        changed=changed, messages=[self._public(m) for m in self._messages.values()] if changed else [])

    def request(self, preset, binding, requester=None, authorization=None):
        with access._LOCK, self._lock:
            self.refresh(force=True)
            if requester is not None and (not callable(authorization) or not authorization()):
                raise access.RadioError('unauthorized', 'Console request authorization was revoked')
            if preset not in assessment.PRESETS:
                raise access.RadioError('invalid_body', 'Choose a supported radio preset')
            if not self._enabled or self._closed:
                raise access.RadioError('feature_disabled', 'Driver radio is not enabled')
            if binding != self._binding:
                raise access.RadioError('conflict', 'Radio session changed; reload before requesting')
            now = self.clock()
            duplicate = next((m for m in reversed(self._messages.values())
                              if m['preset']==preset and m['epoch']==self._epoch and now-m['_created']<2
                              and m['state'] not in ('cancelled','expired','superseded')), None)
            if duplicate is not None:
                if requester is None:
                    duplicate['_native'] = True
                elif len(duplicate['_requesters']) < 16 or requester in duplicate['_requesters']:
                    duplicate['_requesters'][requester] = authorization
                else:
                    raise access.RadioError('rate_limited', 'Too many simultaneous radio requesters')
                return self._public(duplicate)
            result = self._assessment or assessment.assess({})
            message = self._publish(preset, 'requested', 'normal', result, now, requester, authorization)
            if not self._live:
                self._messages[message['id']]['state'] = 'text_only'
                self._queue.remove(message['id'])
                message = self._public(self._messages[message['id']])
            return message

    def pending_count(self):
        with self._lock:return len(self._queue)

    def take_playback(self):
        self.refresh(force=True)
        with self._lock:
            if self._closed or not self._live or not self._voice_enabled or self._ticket is not None:
                return None
            self._expire(self.clock())
            if not self._queue:
                return None
            mid = self._queue.pop(0)
            message = self._messages[mid]
            message['state'] = 'playing'
            self._ticket = Ticket(uuid.uuid4().hex, self._public(message), threading.Event())
            self._revision += 1
            return self._ticket

    def finish_playback(self, ticket_id, outcome='spoken'):
        with self._lock:
            if self._ticket is None or self._ticket.id != ticket_id or outcome not in ('spoken','text_only','failed'):
                return False
            mid = self._ticket.message['id']
            if mid in self._messages:
                self._messages[mid]['state'] = 'cancelled' if self._ticket.cancelled.is_set() else outcome
            self._audio.pop(mid, None)
            self._ticket = None
            self._revision += 1
            return True

    def attach(self, message_id, epoch, fields):
        self.refresh(force=True)
        with self._lock:
            message = self._messages.get(message_id)
            if (self._closed or not self._live or epoch != self._epoch or message is None
                    or message['epoch'] != epoch or self.clock() >= message['_deadline']
                    or message['state'] not in ('queued','playing')):
                return False
            if not isinstance(fields, dict) or set(fields)-{'explanation','audio','audio_state','ai_state','audio_error','ai_error'}:
                return False
            if 'explanation' in fields and (not isinstance(fields['explanation'],str) or len(fields['explanation'])>600):
                return False
            if any(fields.get(k) not in (None,'pending','ready','unconfigured','failed','not_requested') for k in ('audio_state','ai_state')):
                return False
            if any(fields.get(k) is not None and (not isinstance(fields[k],str) or len(fields[k])>64
                   or not all(c.isascii() and (c.isalnum() or c=='_') for c in fields[k])) for k in ('audio_error','ai_error')):
                return False
            if 'audio' in fields:
                if not self._voice_enabled:
                    return False
                if not isinstance(fields['audio'],bytes) or len(fields['audio'])>2*1024*1024:
                    return False
                self._audio[message_id] = fields['audio']
                message['audio_state'] = 'ready'
            message.update({k:v for k,v in fields.items() if k!='audio'})
            self._revision += 1
            return True

    def remaining_for(self, message_id, epoch):
        self.refresh(force=True)
        with self._lock:
            message=self._messages.get(message_id)
            if (self._closed or not self._live or epoch!=self._epoch or message is None
                    or message['state'] not in ('queued','playing')):
                return 0
            return max(0,message['_deadline']-self.clock())

    def enrichment_work(self, kind):
        self.refresh()
        field='audio_state' if kind=='audio' else 'ai_state'
        with self._lock:
            if self._closed or not self._live:return None
            pending=[m for m in self._messages.values() if m['state'] in ('queued','playing') and m[field]=='pending']
            pending.sort(key=lambda message:message['severity']!='urgent')
            return self._public(pending[0]) if pending else None

    def audio_for(self,ticket_id):
        self.refresh(force=True)
        with self._lock:
            if (self._closed or not self._live or not self._voice_enabled or self._ticket is None
                    or self._ticket.id!=ticket_id or self._ticket.cancelled.is_set()):
                return None
            return self._audio.get(self._ticket.message['id'])

    def close(self):
        with self._lock:
            self._closed = True
            self._cancel()
            self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2)
