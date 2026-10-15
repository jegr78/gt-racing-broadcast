#!/usr/bin/env python3
"""The telemetry map retains the selected position and resets with its lap."""
import test_ui_server as fixtures


def t_position_stays_after_leaving_and_redrawing_the_graph():
    out=fixtures._tm_node("""
const trace=Array.from({length:101},(_,i)=>({d:i*5,t:i*.1,x:i,z:i*2,speed_kmh:100,throttle:50,brake:0,steer_deg:0,gear:4}));
tmState.lapA={...lap('A',2,10,7),trace};tmState.lapB={...lap('B',3,10,7),trace,
 track_definition:{reviewed:true,corners:[{id:'c7',number:7,apex:{x:50,z:100}}]},
 corner_metrics:[{id:'c7',number:7,entry_m:200,apex_m:250,exit_m:300}]};
tmRenderCharts();tmRenderMap();tmHover({clientX:tmState.chart.X(250)});
const position=$('tm-map-position').textContent;
const before=tmState.map.dotB.attrs.cx;tmLeave();
const retained=tmState.map.dotB.attrs.visibility==='visible'&&tmState.map.dotB.attrs.cx===before;
tmRenderMap();const redrawn=tmState.map.dotB.attrs.visibility==='visible'&&tmState.map.dotB.attrs.cx===before;
tmState.lapB={...tmState.lapB,lap:4};tmRenderMap();
console.log([position,retained,redrawn,tmState.positionD].join('|'));
""")
    if out is not None:
        assert out.strip()=='T7 · 250 m|true|true|0',out


def t_missing_position_does_not_reuse_a_previous_dot():
    out=fixtures._tm_node("""
tmState.lapA=null;tmState.lapB={...lap('R',2,10,7),trace:[{d:0,t:0,x:0,z:0},{d:5,t:1,x:1,z:2}]};
tmRenderCharts();tmRenderMap();tmHover({clientX:tmState.chart.X(0)});
tmState.lapB={...tmState.lapB,lap:3,trace:[{d:0,t:0,x:null,z:null},{d:5,t:1,x:null,z:null}]};
tmRenderMap();console.log(tmState.map===null,$('tm-map-position').textContent);
""")
    if out is not None:
        assert out.strip()=='true Position unavailable for this lap.',out


def t_failed_or_empty_lap_clears_previous_position_and_numbering():
    out=fixtures._tm_node("""
for(const missing of [null,{...lap('R',3,10,7),trace:[]}]){
 tmState.lapA=null;tmState.lapB={...lap('R',2,10,7),trace:[{d:0,t:0,x:0,z:0},{d:5,t:1,x:1,z:2}],track_definition:{reviewed:true,corners:[{number:7,apex:{x:.5,z:1}}]}};
 tmRenderCharts();tmRenderMap();tmHover({clientX:tmState.chart.X(5)});
 tmState.lapB=missing;tmRenderMap();
 console.log([tmState.map===null,$('tm-map-position').textContent,$('tm-map-numbering').textContent,$('tm-map-legend').hidden].join('|'));
}
""")
    if out is not None:
        assert out.strip().splitlines()==['true|Position unavailable for this lap.||true']*2,out


if __name__=='__main__':
    for name,test in list(globals().items()):
        if name.startswith('t_'):test();print('PASS '+name)
