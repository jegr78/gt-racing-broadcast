#!/usr/bin/env python3
"""Deterministic post-session packages built from controlled recordings."""
import copy
import json
import struct
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import ai_package as a
import gt7_recording as rec
import gt7_context as context
import gt7_laps


def expect(fn, code):
    try: fn()
    except a.PackageError as e: assert e.code == code, (e.code, code)
    except Exception as e: raise AssertionError('expected semantic failure '+code+', got '+type(e).__name__) from e
    else: raise AssertionError('expected ' + code)


def row(lap, session=1, track='known', time=100):
    return dict(rec='synthetic', session=session, lap=lap, time_s=time,
                gt7_time_s=time, relay_time_s=time+.1, car_id=485,
                car='Fixture', track_id=track, layout='Forward', track='Fixture track',
                status='counted', capture_complete=True, trace_complete=True,
                time_valid=True, pace_eligible=True, data_quality='ok', reasons=[],
                lap_role='regular', length_m=1000, start_t_s=lap*100, end_t_s=(lap+1)*100,
                fuel_used_l=5, top_speed_kmh=200,
                trace=[dict(d=0,t=0,speed_kmh=100,throttle=80,brake=0),
                       dict(d=500,t=time*.5,speed_kmh=150,throttle=100,brake=0),
                       dict(d=1000,t=time,speed_kmh=100,throttle=80,brake=0)],
                sectors=[time*.5,time*.5])


def source(root, rows=None, completed=True):
    root=Path(root); root.mkdir(parents=True, exist_ok=True)
    w=rec.RecordingWriter(str(root),'Fixture','dev',queue_max=0)
    pkt=bytearray(296);struct.pack_into('<hh',pkt,0x74,4 if completed else 2,3)
    w.put(1000,'A',bytes(pkt));w.close()
    p=Path(w.path); st=p.stat()
    rows=rows or [row(2,time=101),row(3,time=100)]
    for r in rows:r['rec']=rec.recording_stem(str(p))
    idx=dict(rec=rec.recording_stem(str(p)),name=p.name,version=gt7_laps.INDEX_VERSION,
             size=st.st_size,mtime=st.st_mtime,data_version='fixture',laps=rows,
             sessions={str(r['session']):{} for r in rows},open_lap=None)
    ctx=context.Store.for_recording(str(p));ctx.save({'sessions':{'1':{
        'confirmed':True,'settings':{'bop':True,'fixed_setup':True},
        'stints':[{'id':'first','start_lap':1,'compound':'RM','confirmed':True,'warmup_laps':0}]}},
        'notes':[{'id':'note','scope':'session','session':1,'text':'Practice smooth exits'},
                 {'id':'other','scope':'session','session':2,'text':'unrelated note'}]},expected=0)
    return a.load_source(root,p.name,idx)


def t_selection_identity_and_facts():
    with tempfile.TemporaryDirectory() as d:
        s=source(Path(d)/'profile'/'telemetry-recordings')
        package=a.build(s,1,template='consistency',goal='Smooth exits',language='de')
        assert package['selection']['session']==1 and len(package['laps'])==2
        assert package['summary']['best_time_s']==100
        assert package['summary']['mean_time_s']==100.5
        assert package['summary']['population_stddev_s']==.5
        assert package['summary']['selected_gt7_total_s']==201
        assert package['facts']['selection/mean_time_s']['value']==100.5
        assert package['language']=='de' and 'Smooth exits' in a.prompt(package)
        assert a.preview(package,'codex')['lap_contexts'][0]['car']=='Fixture'
        assert len({r['id'] for r in package['laps']})==2
        assert 'unrelated note' not in json.dumps(package)
        assert package['context']['revision']==1
        assert all(r['context']['compound']=='RM' for r in package['laps'])
        assert package['facts'][package['laps'][0]['id']+'/time_s']['value']==101
        frozen=json.dumps(package,sort_keys=True)
        context.Store.for_recording(s.path).save({'notes':[],'sessions':{}},expected=1)
        assert json.dumps(package,sort_keys=True)==frozen
        assert a.build(s,1,laps=[3])['summary']['best_time_s']==100
        expect(lambda:a.build(s,99),'invalid_selection')
        expect(lambda:a.build(s,1,laps=[99]),'invalid_selection')
        expect(lambda:a.build(s,1,template='strategy'),'invalid_selection')
        expect(lambda:a.build(s,True),'invalid_selection')
        repeated=source(Path(d)/'profile'/'telemetry-recordings', [row(2,1,time=102),row(2,2,time=100)])
        assert a.build(repeated,1)['laps'][0]['id'] != a.build(repeated,2)['laps'][0]['id']


def t_live_incomplete_profile_and_stale_guards():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'profile'/'telemetry-recordings';s=source(root)
        expect(lambda:a.load_source(root,'../else.gt7rec',s.index),'invalid_source')
        expect(lambda:a.load_source(Path(d)/'other',s.name,s.index),'invalid_source')
        expect(lambda:a.load_source(root,s.name,dict(s.index,size=0)),'stale_source')
        Path(s.path).rename(s.path+'.part')
        expect(lambda:a.load_source(root,s.name+'.part',s.index),'incomplete_session')
        unfinished=source(Path(d)/'unfinished',completed=False)
        expect(lambda:a.build(unfinished,1),'incomplete_session')
        changed=source(Path(d)/'changed')
        with open(changed.path,'ab') as f: f.write(b'changed')
        expect(lambda:a.build(changed,1),'stale_source')


def t_quality_and_compatible_references():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'telemetry-recordings';s=source(root)
        r=source(root, [row(2,time=99),row(3,time=100)])
        assert a.suggest_references(s,1,[r])
        p=a.build(s,1,candidates=[r])
        assert p['references']==[]
        p=a.build(s,1,references=[(r,1,2)])
        assert len(p['references'])==1 and p['comparisons'][0]['delta_s']==2
        assert p['comparisons'][0]['sections'][0]['delta_s']==.4
        own=a.build(s,1,references=[(s,1,3)])
        assert own['external_reference'] is False
        assert any('No external reference' in msg for msg in own['limitations'])
        unknown=source(root,[row(2,track=None),row(3,track=None)])
        expect(lambda:a.build(unknown,1,references=[(s,1,2)]),'incompatible_reference')
        other=source(Path(d)/'other', [row(2,time=99)])
        expect(lambda:a.build(s,1,references=[(other,1,2)]),'invalid_source')
        bad=copy.deepcopy(s.index);bad['laps'][0]['trace'][1]['t']=-1
        mixed=source(root,bad['laps'])
        p=a.build(mixed,1)
        assert len(p['excluded_laps'])==1 and len(p['laps'])==1
        assert p['limitations']
        interrupted=source(root,[dict(row(2),data_quality='interrupted'),row(3)])
        assert len(a.build(interrupted,1)['excluded_laps'])==1
        changed=source(root,[row(2,track='different')])
        expect(lambda:a.build(s,1,references=[(changed,1,2)]),'incompatible_reference')
        badrows=[dict(row(2),capture_complete=False),dict(row(3),time_valid=False)]
        unusable=source(root,badrows)
        expect(lambda:a.build(unusable,1),'no_usable_laps')


def t_templates_preview_and_package_limits():
    with tempfile.TemporaryDirectory() as d:
        s=source(Path(d)/'telemetry-recordings')
        for template in a.TEMPLATES:
            p=a.build(s,1,template=template,ui_language='de')
            assert p['language']=='de'
            prompt=a.prompt(p)
            assert 'three prioritized exercises' in prompt and 'measured' in prompt
            assert 'optimal braking' in prompt and 'unknown' in prompt
            preview=a.preview(p,'codex')
            assert preview['provider']=='codex' and preview['transmission']
            assert preview['laps']==[r['id'] for r in p['laps']]
        p=a.build(s,1)
        dest=Path(d)/'package';a.write(p,dest,'claude')
        assert json.loads((dest/'manifest.json').read_text())['references']==[]
        assert (dest/'detail.json').is_file() and (dest/'summary.md').is_file()
        assert json.loads((dest/'result-schema.json').read_text())['properties']['exercises']['minItems']==3
        assert str(Path(d)) not in (dest/'detail.json').read_text()
        expect(lambda:a.preview(p,'other'),'invalid_selection')
        huge=copy.deepcopy(p);huge['context']['data']['notes'][0]['text']='x'*a.LIMITS['codex']['detail_bytes']
        expect(lambda:a.preview(huge,'codex'),'package_too_large')
        assert not (Path(d)/'too-large').exists()
        expect(lambda:a.write(huge,Path(d)/'too-large','codex'),'package_too_large')


def t_only_selected_context_and_complete_note_preview():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'telemetry-recordings';s=source(root)
        store=context.Store.for_recording(s.path);doc=store.read()['data']
        doc['sessions']['1']['stints'][0]['note']='SELECTED_STINT_NOTE'
        doc['sessions']['1']['stints'].append({'id':'later','start_lap':50,'compound':'RH',
                                              'confirmed':True,'note':'UNSELECTED_LAP_SECRET'})
        doc['sessions']['1']['lap_roles']={'2':'regular','50':'pit'}
        doc['track_definition']={'selections':{'1':{'variant_id':'wanted'},'2':{'variant_id':'unrelated'}}}
        store.save(doc,expected=1);s=a.load_source(root,s.name,s.index)
        reference=source(root,[row(2,time=99)])
        rstore=context.Store.for_recording(reference.path);rdoc=rstore.read()['data']
        rdoc['notes'][0]['text']='SELECTED_REFERENCE_NOTE';rstore.save(rdoc,expected=1)
        reference=a.load_source(root,reference.name,reference.index)
        p=a.build(s,1,laps=[2],references=[(reference,1,2)])
        raw=json.dumps(p)
        assert 'UNSELECTED_LAP_SECRET' not in raw
        assert 'unrelated' not in raw
        assert '50' not in p['context']['data']['sessions']['1']['lap_roles']
        notes=json.dumps(a.preview(p,'codex')['notes'])
        assert 'SELECTED_STINT_NOTE' in notes and 'SELECTED_REFERENCE_NOTE' in notes
        assert 'UNSELECTED_LAP_SECRET' not in notes


def t_copied_lap_identity_cannot_overwrite_selected_facts():
    import copy
    import dataclasses
    with tempfile.TemporaryDirectory() as d:
        original=source(Path(d)/'telemetry-recordings')
        index=copy.deepcopy(original.index);index['laps'][0]['time_s']=999
        index['laps'][0]['trace'][1]['t']=499.5;index['laps'][0]['trace'][2]['t']=999
        conflicting=dataclasses.replace(original,index=index)
        expect(lambda:a.build(original,1,references=[(conflicting,1,2)]),'invalid_source')
        assert a.build(original,1,references=[(original,1,2)])['references']


def t_optional_shift_enrichment_only_reads_selected_usable_laps():
    import dataclasses
    with tempfile.TemporaryDirectory() as d:
        origin=source(Path(d)/'profile/telemetry-recordings')
        calls=[]
        def enrich(original,session,laps):
            calls.append((session,laps));index=copy.deepcopy(original.index)
            for row in index['laps']:
                if row['session']==session and row['lap'] in laps:
                    row['shift_analysis']={'reference_id':'synthetic-reference','reference':{'source':'synthetic'},'observed_count':2}
            index['shift_reference_snapshots']={'synthetic-reference':{'source':'synthetic'}}
            return dataclasses.replace(original,index=index)
        package=a.build(origin,1,laps=[2],enrich_source=enrich)
        assert calls==[(1,[2])]
        assert package['laps'][0]['derived']['shift_analysis']['observed_count']==2
        assert package['shift_reference_snapshots']=={'synthetic-reference':{'source':'synthetic'}}
        assert 'shift_analysis' not in origin.index['laps'][0]
        assert a.build(origin,1,laps=[3])['shift_reference_snapshots']=={}
        for session,lap in ((True,2),(1,True),(0,2),(1,0)):
            expect(lambda session=session,lap=lap:a.build(origin,1,references=[(origin,session,lap)]),'invalid_selection')


if __name__=='__main__':
    for n,f in sorted(globals().copy().items()):
        if n.startswith('t_'):f();print('PASS',n)
