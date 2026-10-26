"""Narrow trusted coaching controls and deterministic wake-gated voice vocabulary."""
import re

import driver_coach_calibration as calibration
import driver_coach_service as orchestration


def command(service, owner, body, authorize, resolve=None):
    if (not isinstance(body, dict) or set(body) != {'expected', 'action', 'options'}
            or not isinstance(body['options'], dict)):
        raise calibration.CoachError('invalid_body')
    if not orchestration._allowed(authorize):
        raise calibration.CoachError('forbidden')
    if body['action']=='delete_history':
        if set(body['options'])!={'id'}:
            raise calibration.CoachError('invalid_body')
        if service.journal is None:
            raise calibration.CoachError('history_unavailable')
        deleted=service.journal.delete(owner,body['expected'],body['options']['id'])
        if not orchestration._allowed(authorize):
            raise calibration.CoachError('forbidden')
        result=service.view(owner);result['_history']=deleted
        return result
    state = service.snapshot(owner)
    if (type(body['expected']) is not int or body['expected'] < 0
            or body['action'] != 'stop' and body['expected'] != state['revision']):
        raise calibration.CoachError('conflict')
    options = body['options']
    if body['action'] == 'reference':
        if set(options) != {'lap_id'} or options['lap_id'] is not None and not calibration._text(options['lap_id']):
            raise calibration.CoachError('invalid_body')
        if service.snapshot(owner)['phase'] == 'busy':
            raise calibration.CoachError('training_busy')
        if options['lap_id'] is None:
            options = dict(row=None)
        else:
            if not callable(resolve):
                raise calibration.CoachError('reference_unavailable')
            row, grant = resolve(options['lap_id'])
            options = dict(row=row, authorize=grant)
    return service.command(owner, body['action'], body['expected'], options, authorize=authorize)


def voice_plan(text):
    if (not isinstance(text, str) or not text.strip() or len(text) > 400
            or any(ord(c) < 32 for c in text)):
        raise calibration.CoachError('invalid_body')
    clean = ' '.join(text.casefold().translate(str.maketrans({'ä':'ae','ö':'oe','ü':'ue','ß':'ss'})).split())
    clean = re.sub(r'^(?:hey jeff|hey engineer)[, ]+', '', clean)
    clean = re.sub(r'[.!?]+$', '', clean).strip()
    commands = {
        'start training':('start', {}), 'training starten':('start', {}),
        'start time trial training':('start', {'mode':'time_trial'}), 'zeitfahren starten':('start', {'mode':'time_trial'}),
        'stop training':('stop', {}), 'training stoppen':('stop', {}), 'training beenden':('stop', {}),
        'skip focus':('skip', {}), 'uebung ueberspringen':('skip', {}),
        'keep focus':('keep', {}), 'fokus behalten':('keep', {}),
        'any skill':('skill', {'skill':None}), 'alle techniken':('skill', {'skill':None}),
    }
    for objective, german in (('balanced','ausgewogen'), ('pace','tempo'), ('consistency','konstanz')):
        commands['objective '+objective] = ('objective', {'objective':objective})
        commands['ziel '+german] = ('objective', {'objective':objective})
    for skill, german in (('braking','bremsen'), ('throttle','gas'), ('steering','lenken'), ('gear','schalten'),
                          ('speed','geschwindigkeit'), ('line','linie'), ('pace','tempo'), ('consistency','konstanz')):
        commands['focus on '+skill] = ('skill', {'skill':skill})
        commands['fokus '+german] = ('skill', {'skill':skill})
    if clean in commands:
        action, options = commands[clean]
        return dict(kind='command', action=action, options=options)
    questions = {'training status':'status', 'trainingsstatus':'status', 'current focus':'focus',
                 'aktueller fokus':'focus', 'training evidence':'evidence', 'trainingsbelege':'evidence'}
    if clean in questions:
        return dict(kind='question', topic=questions[clean])
    if re.search(r'\b(?:training|trainingsstatus|coach|focus|fokus|uebung|ziel)\b', clean):
        return dict(kind='clarify')
    return None


def voice_reply(state, topic):
    if state['phase'] == 'busy':
        return 'Another driver owns this training. Native controls use local training only.'
    if not state['active']:
        return 'Local training is not active. Say start training to begin fresh personal calibration.'
    if state['pending']:
        return 'The training command was accepted. The current exercise is updating.'
    if state['phase'] in ('paused', 'unavailable'):
        return 'Current training evidence is unavailable. Wait for fresh comparable live data.'
    trained = state.get('focus') or {}
    exercise = trained.get('focus')
    if topic == 'focus' and exercise:
        label = 'Reviewed passage. ' if exercise['reviewed'] else 'Provisional exercise. '
        return (label+exercise['label']+'. '+exercise['action'])[:600]
    if topic == 'evidence' and state.get('latest'):
        latest = state['latest']
        return (latest['observation']+' '+latest['interpretation']+' '+latest['check'])[:600]
    cal = state.get('calibration') or {}
    return ('Training is '+state['phase']+'. Objective '+cal.get('objective', 'balanced')+'. '
            +str(len(cal.get('calibration_ids', [])))+' suitable personal calibration laps. '
            +(trained.get('progress') or {}).get('text', 'Collect further comparable laps.'))[:600]


def _spoken(text, state, kind):
    return dict(text=text, kind=kind, epoch=state.get('epoch'), revision=state['revision'], phase=state['phase'])


def voice_result(service, text, authorize, training_authorize=None):
    plan = voice_plan(text)
    if plan is None:
        return None
    if not orchestration._allowed(authorize):
        raise calibration.CoachError('forbidden')
    owner = dict(profile=service.profile, subject='local:producer')
    state = service.view(owner)
    if state['phase'] == 'busy':
        return _spoken(voice_reply(state, 'status'), state, 'question')
    if plan['kind'] == 'clarify':
        return _spoken('Say start training, stop training, objective, focus on a skill, skip focus, keep focus, or training status.', state, 'clarify')
    if plan['kind'] == 'command':
        try:
            state = service.command(owner, plan['action'], state['revision'], plan['options'],
                                    authorize=authorize, training_authorize=training_authorize)
        except calibration.CoachError as error:
            if error.code not in ('training_required', 'focus_unavailable', 'busy', 'conflict'):
                raise
            return _spoken('The current training state cannot accept that command. Ask for training status and try again.', service.view(owner), 'question')
        return _spoken('Training command accepted.', state, 'command')
    if not orchestration._allowed(authorize):
        raise calibration.CoachError('forbidden')
    return _spoken(voice_reply(state, plan['topic']), state, 'question')


def voice(service, text, authorize):
    result = voice_result(service, text, authorize)
    return result['text'] if result is not None else None


class References:
    """Only trusted live rows and separately authorized archive sources."""
    def __init__(self, live, archive):
        self.live, self.archive = live, archive

    def _load(self, ident):
        if ident.startswith('r:'):
            api = getattr(self.live, 'api', None)
            if api is None:
                raise calibration.CoachError('reference_unavailable')
            result = api._detail(ident, self.archive)
        else:
            result = self.live.detail(ident)
        if not isinstance(result, dict) or result.get('state') != 'ready':
            raise calibration.CoachError('reference_pending' if isinstance(result, dict) and result.get('state') == 'pending' else 'reference_unavailable')
        row = result.get('lap')
        if not isinstance(row, dict) or row.get('id') != ident or calibration._prepare(row) is None:
            raise calibration.CoachError('invalid_reference')
        return row

    def resolve(self, ident):
        source = self.live.snapshot()
        if not orchestration._live(source):
            raise calibration.CoachError('source_unavailable')
        anchor = orchestration._stamp(source)
        row = self._load(ident)
        if not ident.startswith('r:') and row.get('session_id') != source.get('session_id'):
            raise calibration.CoachError('reference_unavailable')
        context = dict(calibration._context(source['current']), comparison_eligible=True)
        if not calibration._compatible(row, context):
            raise calibration.CoachError('incompatible_reference')
        facts = orchestration._facts(row)
        def grant():
            current = self.live.snapshot()
            if not orchestration._live(current) or orchestration._stamp(current) != anchor:
                return False
            same = orchestration._facts(self._load(ident)) == facts
            current = self.live.snapshot()
            return same and orchestration._live(current) and orchestration._stamp(current) == anchor
        if not orchestration._allowed(grant):
            raise calibration.CoachError('reference_unavailable')
        return row, grant

    def choices(self, archives):
        doc = self.live.snapshot()
        if not orchestration._live(doc):
            return []
        context = dict(calibration._context(doc.get('current') or {}), comparison_eligible=True)
        rows = [r for r in (doc.get('laps') or [])[-512:] if isinstance(r, dict)
                and r.get('session_id') == doc.get('session_id') and calibration.eligible(r)
                and calibration._compatible(r, context)]
        result = [dict(id=r['id'], label=f"Own live lap {r['lap']} · {r['time_s']:g} s", source='personal') for r in rows[-16:]]
        api = getattr(self.live, 'api', None)
        if api is not None:
            for source in archives[:4]:
                data = api.reference(source['id'], self.archive(source['id']))
                usable = [r for r in data.get('laps', []) if calibration.eligible(r) and calibration._compatible(r, context)]
                result.extend(dict(id=r['id'], label=f"{source['label']} · lap {r['lap']} · {r['time_s']:g} s", source='shared') for r in usable[-4:])
        return result
