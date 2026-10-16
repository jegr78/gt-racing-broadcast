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


if __name__ == '__main__':
    t_fit_uses_text_width_and_retains_the_minimum_and_maximum()
    print('PASS team name auto-fit')
