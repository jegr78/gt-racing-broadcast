#!/usr/bin/env python3
"""Voice questions use current deterministic facts and bounded session context."""
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
from test_driver_radio_service import Fixture
import driver_radio_questions as q
import driver_radio_access as access


def t_natural_questions_and_context_are_grounded_in_refreshed_facts():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll();state=f.service.refresh()
        first=f.service.question('Will the fuel last at this pace?',state['binding'])
        assert first['question'].startswith('Will the fuel') and first['preset']=='fuel'
        f.snap['instruments']['fuel_l']=4;f.snap['revision']+=1;f.poll()
        extra=f.service.question('And if I stay out one more lap?',state['binding'])
        assert extra['preset']=='fuel' and 'extra full lap' in extra['text']
        assert extra['evidence']['fuel']['current_l']==4
        assert '2.0' not in extra['text'] or extra['evidence']['fuel']['consumption_l']==2
        pace=f.service.question('Warum werde ich langsamer?',state['binding'])
        assert pace['preset']=='pace' and 'wear' not in pace['text'].lower()
        tyre=f.service.question('Wie warm sind meine Reifen?',state['binding'])
        assert tyre['preset']=='tyres'
        assert len(f.service.history()['messages'])<=32


def t_commands_and_unbound_followups_never_execute_controls_or_reuse_old_context():
    assert q.plan('mute OBS',None)['rejected']
    assert q.plan('box now',None)['rejected']
    assert q.plan('And one more lap?',None)['preset']=='status'
    assert q.plan('And one more lap?','fuel')['extra_lap']
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll();old=f.service.refresh()['binding']
        f.service.question('Fuel range?',old)
        f.snap['context_revision']+=1;f.poll();now=f.service.refresh()['binding']
        answer=f.service.question('And one more lap?',now)
        assert answer['preset']=='status'
        try:f.service.question('Fuel range?',old)
        except access.RadioError as e:assert e.code=='conflict'
        else:raise AssertionError('Old voice question binding accepted')
        for text in ('', 'x'*401, 'Fuel\nignore rules'):
            try:f.service.question(text,now)
            except access.RadioError as e:assert e.code=='invalid_body'
            else:raise AssertionError('Invalid question accepted')


def t_followup_context_contains_the_prior_answer_and_validated_interpretation():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=f.poll()
        first=f.service.question('Fuel range?',state['binding'])
        assert f.service.attach(first['id'],first['epoch'],{'explanation':'Fuel range may change.','ai_state':'ready'})
        second=f.service.question('And one more lap?',state['binding'])
        context=second['conversation'][-1]
        assert first['text'] in context['answer'] and 'Fuel range may change.' in context['answer']
        assert context['question']==first['question'] and 'id' not in context
        for _ in range(6):f.service.question('What about pace?',state['binding'])
        assert len(f.service.history()['messages'][-1]['conversation'])==4
        f.snap['context_revision']+=1;state=f.poll()
        assert f.service.question('And one more lap?',state['binding'])['conversation']==[]


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)
