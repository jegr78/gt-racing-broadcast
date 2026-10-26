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
    state = service.snapshot(owner)
    if type(body['expected']) is not int or body['expected'] != state['revision']:
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


def voice(service, text, authorize):
    plan = voice_plan(text)
    if plan is None:
        return None
    if not orchestration._allowed(authorize):
        raise calibration.CoachError('forbidden')
    owner = dict(profile=service.profile, subject='local:producer')
    state = service.view(owner)
    if state['phase'] == 'busy':
        return voice_reply(state, 'status')
    if plan['kind'] == 'clarify':
        return 'Say start training, stop training, objective, focus on a skill, skip focus, keep focus, or training status.'
    if plan['kind'] == 'command':
        try:service.command(owner, plan['action'], state['revision'], plan['options'], authorize=authorize)
        except calibration.CoachError as error:
            if error.code not in ('training_required', 'focus_unavailable', 'busy', 'conflict'):
                raise
            return 'The current training state cannot accept that command. Ask for training status and try again.'
        return 'Training command accepted.'
    if not orchestration._allowed(authorize):
        raise calibration.CoachError('forbidden')
    return voice_reply(state, plan['topic'])
