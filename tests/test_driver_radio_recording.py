#!/usr/bin/env python3
"""The radio setup and messages kept with the telemetry recording (#1079)."""
import copy,json,os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_access as access
import driver_radio_recording as recording
import driver_radio_service as radio
import gt7_context
from test_driver_radio_assessment import snapshot
from test_driver_radio_service import features


def prepared(d):
    return gt7_context.Store.prepared(d)


def link(d):
    return recording.Link(d,lambda:None,clock=lambda:1000.0)


def t_radio_setup_fills_only_empty_context_fields():
    with tempfile.TemporaryDirectory() as d:
        store=prepared(d)
        store.save({'sessions':{'1':{'settings':{'laps':10}}}},expected=0)
        setup=dict(laps=12,session='race',fuel_reserve_l=2.0,mandatory_stops=2,mandatory_compounds=['RM','RH'],
                   starting_compound='RM')
        assert link(d).apply_setup(snapshot(),setup,'radio voice')
        doc=store.read();session=doc['data']['sessions']['1']
        assert session['settings']==dict(laps=10,session_type='race',fuel_reserve_l=2.0,pit_stops=2,
                                         required_tyres=['RM','RH']),session['settings']
        assert session['stints']==[dict(id='radio-start',start_lap=1,compound='RM',confirmed=True,tyre_service=False,
                                        warmup_laps=0,strategy={},source='radio voice')],session['stints']
        assert doc['operation']=='radio setup (radio voice)'
        # Everything is declared now: a different spoken value overwrites nothing.
        assert not link(d).apply_setup(snapshot(),dict(laps=20,session='practice',starting_compound='RH'),'driver desk')
        assert store.read()['revision']==doc['revision']


def t_one_race_length_mode_stays_in_the_context():
    with tempfile.TemporaryDirectory() as d:
        store=prepared(d)
        store.save({'sessions':{'1':{'settings':{'duration_min':30}}}},expected=0)
        assert not link(d).apply_setup(snapshot(),dict(laps=12),'radio voice')
        assert store.read()['data']['sessions']['1']['settings']==dict(duration_min=30)


def t_a_tyre_change_becomes_a_confirmed_stint_at_the_lap_of_the_stop():
    with tempfile.TemporaryDirectory() as d:
        snap=snapshot((90,)*6);snap['laps'][4]['lap_role']='pit'
        assert link(d).confirm_service(snap,'RH','radio voice')
        stints=prepared(d).read()['data']['sessions']['1']['stints']
        assert stints==[dict(id='radio-5-1000000',start_lap=5,compound='RH',confirmed=True,tyre_service=True,
                             warmup_laps=1,strategy={},source='radio voice')],stints
        # Declared twice, or already entered in the Control Center: one stint stays.
        assert not link(d).confirm_service(snap,'RS','driver desk')
        assert len(prepared(d).read()['data']['sessions']['1']['stints'])==1
        # Without a pit lap the stint starts in the running lap.
        other=snapshot((90,)*3)
        assert link(d).confirm_service(other,'RM','driver desk')
        assert prepared(d).read()['data']['sessions']['1']['stints'][-1]['start_lap']==4


def t_the_context_prefills_the_radio_setup():
    with tempfile.TemporaryDirectory() as d:
        prepared(d).save({'sessions':{'1':{'settings':{'laps':12,'duration_min':30,'session_type':'qualifying',
                                                        'pit_stops':1,'required_tyres':[],'fuel_reserve_l':1.5},
                                           'stints':[{'id':'a','start_lap':1,'compound':'RS'}]}}},expected=0)
        assert link(d).prefill(snapshot())==dict(laps=12,session='qualifying',mandatory_stops=1,fuel_reserve_l=1.5,
                                                  starting_compound='RS'),link(d).prefill(snapshot())
        assert link(d).prefill(dict(snapshot(),laps=[]))['laps']==12
        session=access.Session(prefill=lambda:link(d).prefill(snapshot()))
        binding={'generation':'g','session_id':'s','context_revision':0}
        assert session.read(binding)['setup']['session']=='qualifying'
        # A context the radio cannot read leaves the setup undeclared.
        broken=access.Session(prefill=lambda:{'laps':'many'})
        assert broken.read(binding)['setup']=={}


def t_own_context_writes_keep_the_radio_binding():
    with tempfile.TemporaryDirectory() as d:
        l=link(d);snap=snapshot();snap['context_revision']=0
        assert l.mask(snap)['context_revision']==0
        assert l.apply_setup(snap,dict(laps=12),'radio voice')
        own=prepared(d).read()['revision']
        assert l.mask(dict(snap,context_revision=own))['context_revision']==0
        # A Control Center edit is someone else's revision and resets the radio as before.
        store=prepared(d);doc=store.read()
        store.save(dict(doc['data'],notes=[{'id':'n1','scope':'recording','text':'Edited'}]),expected=doc['revision'])
        assert l.mask(dict(snap,context_revision=own+1))['context_revision']==own+1


def t_the_journal_belongs_to_the_recording():
    with tempfile.TemporaryDirectory() as d:
        capture=os.path.join(d,'20261102-1800.gt7rec')
        l=recording.Link(d,lambda:capture+'.part')
        assert l.journal(dict(event='message',text='Copy, 12 laps.'))
        assert l.journal(dict(event='answer',text='P3.'))
        with open(recording.journal_path(capture),'ab') as handle:handle.write(b'not json\n')
        assert [e['text'] for e in recording.read_journal(capture)]==['Copy, 12 laps.','P3.']
        assert not recording.Link(d,lambda:None).journal(dict(event='message'))
        size=os.path.getsize(recording.journal_path(capture))
        old=recording.JOURNAL_MAX_BYTES;recording.JOURNAL_MAX_BYTES=size+10
        try:assert not l.journal(dict(event='message',text='x'*40))
        finally:recording.JOURNAL_MAX_BYTES=old
        gt7_context.delete_recording(capture)
        assert not os.path.exists(recording.journal_path(capture))


def t_context_schema_accepts_the_radio_fields():
    data={'sessions':{'1':{'settings':{'session_type':'race','fuel_reserve_l':2.5},
                           'stints':[{'id':'a','start_lap':1,'source':'radio voice'}]}}}
    assert gt7_context.validate_data(data)['sessions']['1']['settings']['session_type']=='race'
    for bad in ({'settings':{'session_type':'sprint'}},{'settings':{'fuel_reserve_l':-1}},
                {'stints':[{'id':'a','start_lap':1,'source':'someone'}]}):
        try:gt7_context.validate_data({'sessions':{'1':bad}})
        except ValueError:continue
        raise AssertionError(bad)


class Radio:
    """A radio service on a prepared context, binding through the link like the relay."""
    def __init__(self,d):
        self.snap=snapshot((90,)*6);self.snap['context_revision']=0;self.at=100.0
        self.access=access.Store(d,True)
        self.access.update({'expected':0,'features':features(True,True),
                            'voice':{'enabled':True,'microphone':None,'input_language':'en'}})
        self.link=recording.Link(d,lambda:self.capture,clock=lambda:1000.0);self.capture=None
        source=lambda:self.link.mask(copy.deepcopy(self.snap))
        self.session=access.Session(prefill=lambda:self.link.prefill(source()))
        self.service=radio.Service(self.access,self.session,source,clock=lambda:self.at,recording=self.link)
    def poll(self):return self.service.refresh(force=True)
    def engineer_reads_context(self,store):self.snap['context_revision']=store.read()['revision']


def t_spoken_setup_reaches_the_context_and_keeps_the_radio_epoch():
    with tempfile.TemporaryDirectory() as d:
        r=Radio(d);state=r.poll();binding=state['binding']
        assert r.service.question('race 12 laps',binding)['text']=='Copy, 12 laps.'
        store=prepared(d)
        assert store.read()['data']['sessions'].get('1',{}).get('settings',{}).get('laps')==12,store.read()['data']
        r.engineer_reads_context(store)
        after=r.poll()
        assert after['epoch']==state['epoch'] and after['assessment']['race_laps']==12
        r.service.question('now on racing hard',after['binding'])
        assert store.read()['data']['sessions']['1']['stints'][-1]['compound']=='RH'
        r.engineer_reads_context(store);assert r.poll()['epoch']==state['epoch']
        # A Control Center correction resets the radio, which then reads the context.
        doc=store.read();data=copy.deepcopy(doc['data']);data['sessions']['1']['settings']['laps']=15
        store.save(data,expected=doc['revision']);r.engineer_reads_context(store)
        reset=r.poll()
        assert reset['epoch']!=state['epoch'] and reset['assessment']['race_laps']==15
        assert r.session.read(reset['binding'])['setup']['laps']==15


def t_radio_messages_are_journalled_with_the_question():
    with tempfile.TemporaryDirectory() as d:
        r=Radio(d);r.capture=os.path.join(d,'rec.gt7rec.part');binding=r.poll()['binding']
        r.service.question('How many laps to go?',binding)
        r.service.request('fuel',binding)
        events=recording.read_journal(r.capture)
        assert [(e['kind'],e['preset']) for e in events]==[('question','to_go'),('requested','fuel')],events
        assert events[0]['question']=='How many laps to go?' and events[0]['text']=='Race length is not set.'
        assert events[0]['lap']==6 and events[0]['source']=='engineer' and isinstance(events[0]['at'],float)
        json.dumps(events)


def t_messages_merge_a_late_answer_and_keep_one_session():
    events=[dict(id='a',at=1.0,lap=2,kind='question',source='engineer',question='Balance?',text='Copy, checking.',session=1),
            dict(id='a',at=5.0,text='Rear is sliding.',session=1),
            dict(id='b',at=2.0,lap=3,kind='lap',source='engineer',text='Lap 4.',session=2),
            dict(id='c',at=3.0,kind='coach',source='coach',text='Old line.'),
            dict(id=None,text='broken'),dict(id='d',text=5)]
    assert recording.messages(events)==[
        dict(at=1.0,lap=2,kind='question',source='engineer',question='Balance?',text='Rear is sliding.'),
        dict(at=2.0,lap=3,kind='lap',source='engineer',question=None,text='Lap 4.'),
        dict(at=3.0,lap=None,kind='coach',source='coach',question=None,text='Old line.')]
    assert [m['text'] for m in recording.messages(events,session=1)]==['Rear is sliding.']
    assert [m['text'] for m in recording.messages(events,session=1,unknown_session=True)]==['Rear is sliding.','Old line.']
    odd=recording.messages([dict(id='n',at=float('nan'),lap=float('inf'),text='Hand-edited.')])
    assert odd[0]['at'] is None and odd[0]['lap'] is None,odd
    many=[dict(id=str(i),text='x') for i in range(recording.MAX_MESSAGES+5)]
    assert len(recording.messages(many))==recording.MAX_MESSAGES


def t_journal_lines_carry_the_recording_session():
    with tempfile.TemporaryDirectory() as d:
        r=Radio(d);r.capture=os.path.join(d,'rec.gt7rec.part')
        r.snap['current']['recording_session']=2;r.snap['current']['session_id']='s'
        binding=r.poll()['binding'];r.service.request('fuel',binding)
        assert recording.read_journal(r.capture)[-1].get('session')==2,recording.read_journal(r.capture)[-1]


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)
