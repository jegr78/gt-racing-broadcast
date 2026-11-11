#!/usr/bin/env python3
"""Race setup by voice: a fixed English and German vocabulary with an English read-back."""
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_access as access
import gt7_context
import driver_radio_setup_voice as voice
from test_driver_radio_assessment import snapshot,review
from test_driver_radio_native import Harness
from test_driver_radio_service import Fixture


def t_session_type_in_english_and_german():
    for text,session,readback in (('Hey Jeff, practice session.','practice','Copy, practice session.'),
                                  ('Training','practice','Copy, practice session.'),
                                  ('qualifying','qualifying','Copy, qualifying.'),
                                  ('Qualifikation','qualifying','Copy, qualifying.'),
                                  ('Race.','race','Copy, race.'),('Rennen','race','Copy, race.')):
        result=voice.plan(text)
        assert result==dict(setup=dict(session=session),readback=readback),(text,result)


def t_race_length_in_laps_or_minutes_clears_the_other_mode():
    assert voice.plan('race 12 laps')==dict(setup=dict(laps=12,duration_min=None),readback='Copy, 12 laps.')
    assert voice.plan('Race twelve laps')['setup']['laps']==12
    assert voice.plan('Rennen 12 Runden')['setup']['laps']==12
    assert voice.plan('race one lap')['readback']=='Copy, 1 lap.'
    assert voice.plan('race 30 minutes')==dict(setup=dict(duration_min=30,laps=None),readback='Copy, 30 minutes.')
    assert voice.plan('Rennen dreißig Minuten')['setup']['duration_min']==30
    assert voice.plan('race twenty five minutes')['setup']['duration_min']==25
    assert voice.plan('Rennen fünfundzwanzig Runden')==dict(setup=dict(laps=25,duration_min=None),readback='Copy, 25 laps.')
    assert voice.plan('race 0 laps') is None and voice.plan('race many laps') is None


def t_compounds_map_english_and_german_names_to_gt7_codes():
    for words,code in (('racing medium','RM'),('racing hard tyres','RH'),('sports soft','SS'),('comfort hard','CH'),
                       ('rennen weich','RS'),('rennreifen mittel','RM'),('renn hart','RH'),('rennreifen hart','RH'),
                       ('sport weich','SS'),('sportreifen mittel','SM'),('komfort hart','CH'),('komfortreifen weich','CS'),
                       ('intermediate','IM'),('wet','W'),('regenreifen','W'),('weiche rennreifen','RS')):
        assert voice.compound(words)==code,(words,voice.compound(words))
    assert voice.compound('racing') is None and voice.compound('medium') is None


def t_starting_compound_reserve_and_tyre_change():
    assert voice.plan('starting on racing medium')==dict(setup=dict(starting_compound='RM'),
                                                         readback='Copy, starting on racing medium.')
    assert voice.plan('Start auf Rennreifen mittel')['setup']==dict(starting_compound='RM')
    assert voice.plan('reserve 2 litres')==dict(setup=dict(fuel_reserve_l=2.0),readback='Copy, reserve 2 litres.')
    assert voice.plan('Reserve 1,5 Liter')['setup']==dict(fuel_reserve_l=1.5)
    assert voice.plan('reserve one point five liters')['readback']=='Copy, reserve 1.5 litres.'
    assert voice.plan('now on racing hard')==dict(service='RH',readback='Copy, now on racing hard.')
    assert voice.plan('jetzt auf Rennreifen hart')['service']=='RH'
    assert voice.plan('starting on something') is None and voice.plan('now on the grid') is None


def t_mandatory_stops_and_compounds():
    assert voice.plan('two mandatory stops')==dict(setup=dict(mandatory_stops=2),readback='Copy, 2 mandatory stops.')
    assert voice.plan('one mandatory stop')['readback']=='Copy, 1 mandatory stop.'
    assert voice.plan('zwei Pflichtstopps')['setup']==dict(mandatory_stops=2)
    assert voice.plan('no mandatory stops')==dict(setup=dict(mandatory_stops=0),readback='Copy, no mandatory stops.')
    assert voice.plan('mandatory racing medium and racing hard')==dict(
        setup=dict(mandatory_compounds=['RM','RH']),readback='Copy, mandatory racing medium and racing hard.')
    assert voice.plan('Pflicht Rennreifen mittel und Rennreifen hart')['setup']==dict(mandatory_compounds=['RM','RH'])
    assert voice.plan('mandatory none')['setup']==dict(mandatory_compounds=[])
    assert voice.plan('mandatory racing medium and pizza') is None
    assert voice.plan('fifty mandatory stops') is None


def t_questions_and_other_commands_are_not_setup():
    for text in ('How many laps to go?','What is my position?','start training','Training status','race position',
                 'Reicht der Sprit?','box now','',' ','x'*401,'race\n12 laps',None,12):
        assert voice.plan(text) is None,text


def t_spoken_setup_is_read_back_and_keeps_the_radio_session():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=f.poll();binding=state['binding']
        earlier=f.request('status')
        for text,readback in (('Hey Jeff, race 12 laps','Copy, 12 laps.'),
                              ('starting on racing medium','Copy, starting on racing medium.'),
                              ('reserve 2 litres','Copy, reserve 2 litres.'),
                              ('two mandatory stops','Copy, 2 mandatory stops.'),
                              ('mandatory racing medium and racing hard','Copy, mandatory racing medium and racing hard.'),
                              ('qualifying','Copy, qualifying.'),('race','Copy, race.')):
            message=f.service.question(text,binding)
            assert message['text']==readback and message['preset']=='setup' and message['state']=='queued',message
            assert message['ai_state']=='not_requested' and message['question']==voice.strip_wake(text)
        setup=f.session.read(binding)['setup']
        assert setup==dict(laps=12,starting_compound='RM',fuel_reserve_l=2.0,mandatory_stops=2,
                           mandatory_compounds=['RM','RH'],session='race'),setup
        after=f.poll()
        assert after['epoch']==state['epoch'] and after['assessment']['race_laps']==12
        assert earlier['id'] in [m['id'] for m in f.service.history()['messages']]
        f.service.question('race 30 minutes',binding)
        assert f.session.read(binding)['setup'].get('laps') is None and f.session.read(binding)['setup']['duration_min']==30
        # A desk correction still starts a new radio epoch.
        declared=f.session.read(binding)
        f.session.update(binding,dict(binding=binding,expected=declared['revision'],setup={'laps':5}))
        assert f.poll()['epoch']!=state['epoch']


def t_spoken_tyre_change_confirms_the_fitted_compound_at_the_current_lap():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);binding=f.poll()['binding']
        message=f.service.question('jetzt auf Rennreifen hart',binding)
        assert message['text']=='Copy, now on racing hard.',message
        service=f.session.read(binding)['service']
        assert service['compound']=='RH' and service['lap_id']=='current' and service['fitted']==['RH'],service
        assert f.poll()['assessment']['tyres']['compound']=='RH'


def t_spoken_setup_without_a_telemetry_session_says_so():
    with tempfile.TemporaryDirectory() as d:
        snap=snapshot();snap['session_id']=None
        f=Fixture(d,snap);binding=f.poll()['binding']
        assert binding is None
        message=f.service.question('race 12 laps',binding)
        assert message['text']=='No telemetry session yet. Say it again once you are on track.',message
        assert message['state']=='text_only'


def t_practice_and_qualifying_drop_the_race_distance():
    snap=snapshot((90,90,91));snap['instruments']['race_position']=3
    practice=review(snap,{'setup':{'laps':4,'session':'practice'}})
    assert practice['answers']['status']==practice['lap_text']=='Lap 4, last lap was a 1:31.000, fuel for 5.0 laps.',practice['lap_text']
    assert practice['race_laps'] is None and practice['position'] is None
    assert practice['quick_answers']['to_go']=='No race distance in practice.'
    # Asked directly, the position is named in every session; only the calls leave it out.
    assert practice['quick_answers']['position']=='P3.',practice['quick_answers']['position']
    qualifying=review(snap,{'setup':{'laps':4,'session':'qualifying'}})
    assert qualifying['lap_text']=='Lap 4, P3, last lap was a 1:31.000, fuel for 5.0 laps.',qualifying['lap_text']
    assert qualifying['race_laps'] is None and qualifying['quick_answers']['to_go']=='No race distance in qualifying.'
    race=review(snap,{'setup':{'laps':4,'session':'race'}})
    assert race['lap_text']=='Lap 4, P3, last lap was a 1:31.000, final lap, fuel for 5.0 laps.',race['lap_text']
    assert review(snap,{'setup':{'laps':4}})['lap_text']==race['lap_text']


def t_open_mandatory_rules_are_named_in_the_status_and_lap_update():
    snap=snapshot((90,90,91))
    rules={'mandatory_stops':2,'mandatory_compounds':['RM','RH']}
    data=review(snap,{'setup':dict(rules)})
    tail='fuel for 5.0 laps, 2 mandatory stops left, racing hard still required.'
    assert data['lap_text'].endswith(tail) and data['answers']['status'].endswith(tail),data['lap_text']
    snap['laps'][1]['analysis_role']='pit'
    data=review(snap,{'setup':dict(rules),'service':{'compound':'RH','lap_id':'L2','fitted':['RH']}})
    assert data['mandatory']==dict(stops_done=1,stops_left=1,compounds_missing=[],text='1 mandatory stop left'),data['mandatory']
    # The stop restarts the fuel measurement, so the call ends with the open rule.
    assert data['lap_text']=='Lap 4, last lap was a 1:31.000, 1 mandatory stop left.',data['lap_text']
    snap['current']['lap_role']='pit'
    done=review(snap,{'setup':dict(rules),'service':{'compound':'RH','lap_id':'L2','fitted':['RH']}})
    assert done['mandatory']['stops_left']==0 and 'mandatory' not in done['lap_text'],done['lap_text']
    assert 'mandatory' not in review(snap,{'setup':dict(rules,session='practice')})['lap_text']


def t_native_voice_hands_the_coach_the_text_without_the_wake_remnant():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step()
        claimed=[];h.n._coaching=lambda text,signature:claimed.append(text) or True
        h.workers[0].events=[{'event':'question','text':'hey sheriff start training'}]
        h.n.step()
        assert claimed==['start training'],claimed
        h.n.close()


def t_native_voice_routes_a_setup_command_before_the_coaching_vocabulary():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step()
        # "Training" alone is the coach's clarification otherwise: a coach that claims
        # every text must never see a setup command.
        claimed=[];h.n._coaching=lambda text,signature:claimed.append(text) or True
        h.workers[0].events=[{'event':'question','text':'Training'}]
        h.n.step()
        assert claimed==[],claimed
        message=h.f.service.history()['messages'][-1]
        assert message['text']=='Copy, practice session.' and message['source']=='engineer',message
        assert h.f.session.read(message['binding'])['setup']=={'session':'practice'}
        h.n.close()


def t_every_gt7_compound_code_has_a_spoken_name():
    # The read-back and the mandatory-compound text name every code the setup accepts.
    assert sorted(voice.COMPOUND_NAMES)==sorted(gt7_context.COMPOUNDS)


def t_setup_validation_bounds_the_new_fields():
    assert access.validate_setup({'session':'race','mandatory_stops':0,'mandatory_compounds':['RM','RH']})
    for bad in ({'session':'sprint'},{'mandatory_stops':21},{'mandatory_stops':1.5},{'mandatory_compounds':['RM','RM']},
                {'mandatory_compounds':['XX']},{'mandatory_compounds':'RM'},{'mandatory_compounds':['RM']*9}):
        try:access.validate_setup(bad)
        except access.RadioError:continue
        raise AssertionError(bad)
    assert access.merge_setup({'laps':12,'session':'race'},{'duration_min':30,'laps':None})=={'duration_min':30,'session':'race'}


def t_the_opening_lap_is_declared_in_english_and_german():
    formation=dict(setup=dict(opening_lap='formation'),readback='Copy, formation lap first.')
    box=dict(setup=dict(opening_lap='to_box'),readback='Copy, first lap to the box.')
    normal=dict(setup=dict(opening_lap=None),readback='Copy, lap 1 is a race lap.')
    for text,result in (('formation lap',formation),('Hey Jeff, we have a formation lap',formation),
                        ('with formation lap',formation),('Einführungsrunde',formation),
                        ('wir haben eine Einführungsrunde',formation),('mit Formationsrunde',formation),
                        ('box start',box),('follow-up lobby',box),('first lap to the box',box),
                        ('Boxenstart',box),('Folgelobby',box),
                        ('no formation lap',normal),('keine Einführungsrunde',normal),('normal start',normal)):
        assert voice.plan(text)==result,(text,voice.plan(text))
    assert voice.plan('hey sheriff formation lap')==formation,'the wake remnant is dropped before a formation lap'
    assert access.merge_setup({'laps':12,'opening_lap':'formation'},{'opening_lap':None})=={'laps':12}


def t_setup_validation_accepts_only_known_opening_laps():
    for good in ('formation','to_box'):
        assert access.validate_setup({'opening_lap':good})=={'opening_lap':good}
    for bad in ('rolling',1,True,''):
        try:access.validate_setup({'opening_lap':bad})
        except access.RadioError:continue
        raise AssertionError(bad)


def t_a_misheard_wake_phrase_is_dropped_before_routing():
    # Transcripts from a live session: the wake phrase "hey jeff" stays in the text, misheard.
    for text,clean in (('hey sheriff race','race'),('hey chair of race','race'),('hey hf pace','pace'),
                       ('hate sheriff current session race','current session race'),
                       ('hade have no mandatory pit stops','have no mandatory pit stops'),
                       ('Hey Jeff, what is my position?','what is my position?'),('hey engineer tyres','tyres'),
                       ('jeff how many laps to go','how many laps to go'),('race','race'),
                       ('how many laps to go','how many laps to go'),('a quick question about fuel','a quick question about fuel')):
        assert voice.strip_wake(text)==clean,(text,voice.strip_wake(text))


def t_natural_setup_phrases_from_the_track():
    for text,setup,readback in (
            ('hey sheriff race',dict(session='race'),'Copy, race.'),
            ('hey chair of race',dict(session='race'),'Copy, race.'),
            ('hate sheriff current session race',dict(session='race'),'Copy, race.'),
            ('hey hf current session is the race six laps',dict(session='race',laps=6,duration_min=None),'Copy, race, 6 laps.'),
            ('this is qualifying',dict(session='qualifying'),'Copy, qualifying.'),
            ('Die aktuelle Session ist das Rennen',dict(session='race'),'Copy, race.'),
            ('hade have no mandatory pit stops',dict(mandatory_stops=0),'Copy, no mandatory stops.'),
            ('we have two mandatory pit stops',dict(mandatory_stops=2),'Copy, 2 mandatory stops.'),
            ('wir haben zwei Pflichtstopps',dict(mandatory_stops=2),'Copy, 2 mandatory stops.'),
            ('the race is 20 minutes',dict(duration_min=20,laps=None),'Copy, 20 minutes.')):
        result=voice.plan(text)
        assert result==dict(setup=setup,readback=readback),(text,result)
    # A question about the race is still a question.
    for text in ('hey sheriff how many laps in the race','what is the race position','is this the race'):
        assert voice.plan(text) is None,text


def t_the_service_answers_the_question_without_the_wake_remnant():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);binding=f.poll()['binding']
        message=f.service.question('hey sheriff position',binding)
        assert message['preset']=='position' and message['question']=='position',message
        assert f.service.question('hey chair of race',binding)['text']=='Copy, race.'


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)
