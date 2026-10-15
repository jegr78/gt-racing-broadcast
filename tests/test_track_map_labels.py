#!/usr/bin/env python3
"""Saved turn labels remain safe and consistent across report maps."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import ai_driver_review as driver
import ai_report_charts as charts
import report_telemetry
import ai_reports
import track_map_labels


def t_marker_layout_retains_numbers_and_escapes_names():
    corners=[dict(number=n,name='<script>bad</script>',apex=dict(x=50,z=50+n)) for n in range(1,20)]
    svg='<svg>'+track_map_labels.render(corners,lambda p:(p['x'],p['z']),320,320,reviewed=True)+'</svg>'
    root=ET.fromstring(svg)
    labels=root.findall('.//text')
    assert sorted(e.text for e in labels)==sorted('T'+str(n) for n in range(1,20))
    assert '<script>' not in svg and '&lt;script&gt;' in svg
    assert len({e.attrib['y'] for e in labels})==19
    provisional=track_map_labels.render(corners[:1],lambda p:(p['x'],p['z']),320,320,reviewed=False)
    assert '>P1<' in provisional and '>T1<' not in provisional
    assert track_map_labels.render([dict(number=1,apex=dict(x=None,z=1))],lambda p:(p['x'],p['z']),320,320,reviewed=True)==''



def t_invalid_marker_numbers_and_projection_are_omitted():
    project=lambda p:(p['x'],p['z'])
    valid=dict(number=7,apex=dict(x=50,z=50))
    invalid=[dict(valid,number=n) for n in (0,-1,True,'7',None)]
    assert track_map_labels.render(invalid,project,320,320)==''
    for value in (None,float('nan'),float('inf'),True):
        assert track_map_labels.render([dict(valid,apex=dict(x=value,z=50))],lambda p:(50,50),320,320)==''
    assert track_map_labels.render([valid,valid],project,320,320).count('class="turn-label"')==1
    for value in (float('nan'),float('inf'),-1,321):
        assert track_map_labels.render([valid],lambda p,value=value:(value,50),320,320)==''


def t_ai_maps_use_saved_markers_and_positive_z_down():
    lap=dict(lap=2,trace=[dict(d=0,x=0,z=0),dict(d=50,x=10,z=100),dict(d=100,x=100,z=200)],
             derived=dict(corner_metrics=[dict(id='one',number=7,apex_m=50)]))
    definition=dict(reviewed=True,corners=[dict(id='one',number=7,apex=dict(x=10,z=100))])
    svg=charts.line_chart(lap,None,0,100,True,overview=True,definition=definition)
    assert '>T7<' in svg
    assert 'Kurvennummern' in svg
    provisional=charts.line_chart(lap,None,0,100,False,definition=dict(definition,reviewed=False))
    assert '>P7<' in provisional and '>T7<' not in provisional
    missing=charts.line_chart(lap,None,0,100,True)
    assert 'Keine gespeicherten Kurvennummern' in missing
    # The line must have the same z-down orientation as the ordinary telemetry map.
    import re
    path=re.search(r'<path d="(M[^\"]+)" fill="none"',svg).group(1)
    coordinates=re.findall(r'[ML]([0-9.]+),([0-9.]+)',path)
    assert float(coordinates[0][1])<float(coordinates[-1][1])


def t_post_event_map_keeps_turn_numbers():
    data=dict(sectors=[dict(points=[[0,0],[100,100]],gap_s=0,i=1,color='#aaa')],
              corners=[dict(number=7,apex=dict(x=50,z=50))],reviewed=True)
    svg=report_telemetry.svg_track_map(data)
    assert '>T7<' in svg
    data['reviewed']=False
    assert '>P7<' in report_telemetry.svg_track_map(data)
    data['corners']=[]
    assert 'No saved turn numbering' in report_telemetry.svg_track_map(data)


def t_passage_matches_saved_corner_identity():
    package=dict(track_definition_snapshots={'saved':dict(reviewed=True,corners=[dict(id='one',number=7),dict(id='two',number=8)])})
    lap=dict(derived=dict(definition_fingerprint='saved',corner_metrics=[dict(id='one',number=7,entry_m=100,apex_m=150,exit_m=200),dict(id='two',number=8,entry_m=300,apex_m=350,exit_m=400)]))
    assert driver.passage(package,lap,150)=='T7 · 150 m'
    assert driver.passage(package,lap,250)=='T7–T8 · 250 m'
    package['track_definition_snapshots']['saved']['reviewed']=False
    assert driver.passage(package,lap,150)=='P7 · 150 m'
    assert driver.passage({},lap,150)=='150 m'
    lap['derived']['corner_metrics'][0]['number']=99
    assert driver.passage(package,lap,150)=='150 m'
    assert driver.passage(package,lap,float('nan'))==''


def t_fact_labels_use_frozen_turn_number_instead_of_array_index():
    identity='a'*32+'/session/1/lap/2'
    corner=dict(id='one',number=7,entry_m=100,apex_m=150,exit_m=200)
    lap=dict(id=identity,rec='recording',derived=dict(definition_fingerprint='saved',corner_metrics=[corner]))
    definition=dict(reviewed=True,corners=[dict(id='one',number=7)])
    package=dict(language='de',laps=[lap],references=[],track_definition_snapshots={'saved':definition})
    fact=identity+'/derived/corner_metrics/0/time_s'
    assert 'T7 · Abschnittszeit' in ai_reports.fact_label(fact,package)
    assert 'Kurvenabschnitt 1' not in ai_reports.fact_label(fact,package)
    definition['reviewed']=False
    assert 'P7 · Abschnittszeit' in ai_reports.fact_label(fact,package)
    definition['corners'][0]['number']=8
    assert 'P7' not in ai_reports.fact_label(fact,package)
    assert 'Kurvenabschnitt 1' not in ai_reports.fact_label(fact,package)
    package['track_definition_snapshots']={}
    assert 'T7' not in ai_reports.fact_label(fact,package)
    assert 'Kurvenabschnitt 1' not in ai_reports.fact_label(fact,package)


if __name__=='__main__':
    for name,test in list(globals().items()):
        if name.startswith('t_'):test();print('PASS '+name)
