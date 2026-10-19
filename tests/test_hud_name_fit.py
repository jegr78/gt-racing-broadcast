#!/usr/bin/env python3
"""Team-name auto-fit uses text width even when flex overflow is on the left."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def t_fit_uses_text_width_and_retains_the_minimum_and_maximum():
    node = shutil.which('node')
    if not node:
        print('SKIP node unavailable')
        return
    source = (ROOT / 'src/obs/hud.html').read_text(encoding='utf-8')
    function = source[source.index('  function fitName('):source.index('  // Per-tile colours')]
    script = r'''
const el={style:{fontSize:''},clientWidth:100,scrollWidth:100,textContent:''};
const getComputedStyle=()=>({getPropertyValue:key=>key==='--team-name-max'?'22':'12',paddingLeft:'0px',paddingRight:'0px'});
const document={createRange:()=>({selectNodeContents(){},getBoundingClientRect(){return {width:el.textContent.length*parseFloat(el.style.fontSize)*.6};}})};
''' + function + r'''
const sizes=[];for(const text of ['CLOCKWORK MOTORSPORTS','GT','X'.repeat(500),'']){
el.textContent=text;fitName(el);sizes.push(parseFloat(el.style.fontSize));}
console.log(sizes.join(','));
'''
    result = subprocess.run([node, '-'], input=script, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', check=True)
    sizes = [int(value) for value in result.stdout.strip().split(',')]
    assert 12 <= sizes[0] < 22, sizes
    assert sizes[1:] == [22, 12, 22], sizes


def _fit_source():
    source = (ROOT / 'src/obs/hud.html').read_text(encoding='utf-8')
    return source, source[source.index('  function fitName('):source.index('  // Per-tile colours')]


def t_round_title_fits_its_slot_only_when_the_profile_opts_in():
    # #944: "TEC 21 - Round 2 - Trial Mountain" in Orbitron Bold 40 px is 748 px wide in
    # a 714 px slot; it was clipped on both sides because it is centred.
    node = shutil.which('node')
    if not node:
        print('SKIP node unavailable')
        return
    _source, function = _fit_source()
    script = r'''
let vars={};
const el={style:{fontSize:''},clientWidth:714,textContent:''};
const getComputedStyle=()=>({getPropertyValue:key=>vars[key]||'',paddingLeft:'0px',paddingRight:'0px'});
const document={createRange:()=>({selectNodeContents(){},getBoundingClientRect(){
  return {width:el.textContent.length*parseFloat(el.style.fontSize||'40')*.567};}})};
''' + function + r'''
const out=[];
el.textContent='TEC 21 - Round 2 - Trial Mountain'; fitText(el); out.push(el.style.fontSize||'css');
vars={'--fit-max':'40','--fit-min':'26'};
fitText(el); out.push(parseFloat(el.style.fontSize));
el.textContent='Round 2'; fitText(el); out.push(parseFloat(el.style.fontSize));
el.textContent='X'.repeat(200); fitText(el); out.push(parseFloat(el.style.fontSize));
vars={}; fitText(el); out.push(el.style.fontSize||'css');
console.log(out.join(','));
'''
    result = subprocess.run([node, '-'], input=script, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', check=True)
    plain, long_, short, huge, back = result.stdout.strip().split(',')
    assert plain == 'css', 'without --fit-max the profile font size stays'
    assert 26 <= float(long_) < 40, long_
    assert float(short) == 40 and float(huge) == 26, (short, huge)
    assert back == 'css', 'turning the fit off hands the size back to the CSS'


def t_round_title_is_wired_to_the_fit():
    source, _fn = _fit_source()
    tag = source[source.index('<div id="round-top"'):]
    tag = tag[:tag.index('>')]
    assert 'data-edit-props="fitMax,fitMin"' in tag, tag
    tick = source[source.index('setText("round-top"'):]
    assert 'fitText(document.getElementById("round-top"))' in tick[:300], tick[:300]


if __name__ == '__main__':
    t_fit_uses_text_width_and_retains_the_minimum_and_maximum()
    t_round_title_fits_its_slot_only_when_the_profile_opts_in()
    t_round_title_is_wired_to_the_fit()
    print('PASS team name and round title auto-fit')
