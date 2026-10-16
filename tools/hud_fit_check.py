#!/usr/bin/env python3
"""Render the production HUD and check aligned team-name glyph bounds."""
import argparse
from pathlib import Path
import shutil

import e2e_visual as V

ROOT = Path(__file__).resolve().parents[1]


def check(browser, outdir):
    page = browser.new_page(viewport={'width': 1920, 'height': 1080})
    shot = 'hud-name-fit.png'
    try:
        source = (ROOT / 'src/obs/hud.html').read_text(encoding='utf-8')
        page.route('**/*', lambda route: route.fulfill(
            body=source if route.request.url.endswith('/hud') else '{}',
            status=200 if route.request.url.endswith(('/hud', '.css')) else 404,
            content_type='text/html' if route.request.url.endswith('/hud') else 'text/css' if route.request.url.endswith('.css') else 'application/json'))
        payload = {'teams': [{'name': 'CLOCKWORK MOTORSPORTS', 'number': str(i+1)} for i in range(3)]}
        page.route('**/hud/data', lambda route: route.fulfill(json=payload))
        page.add_init_script('window.setInterval=()=>0;')
        page.goto('http://hud.test/hud')
        page.wait_for_function('() => document.getElementById("team3-name").textContent.length > 0')
        page.evaluate('() => document.fonts.ready')
        page.add_style_tag(content='body{background:#0b1018}')
        result = page.evaluate('''() => {
            const rows=[];
            for(const [i,align] of ['flex-start','center','flex-end'].entries()){
                const el=document.getElementById('team'+(i+1)+'-name');
                Object.assign(el.style,{fontFamily:'Arial',width:'210px',padding:'0 12px',justifyContent:align});
                el.style.setProperty('--team-name-max','22px');el.style.setProperty('--team-name-min','12px');
                for(const [text,width,kind] of [['CLOCKWORK MOTORSPORTS',210,'long'],['GT',210,'short'],['X'.repeat(500),210,'clamped'],['',210,'empty'],['CLOCKWORK MOTORSPORTS',500,'wide'],['CLOCKWORK MOTORSPORTS',210,'long']]){
                    el.style.width=width+'px';el.textContent=text;fitName(el);
                    const range=document.createRange();range.selectNodeContents(el);
                    const glyph=range.getBoundingClientRect(),box=el.getBoundingClientRect(),css=getComputedStyle(el);
                    rows.push({align,kind,size:parseFloat(css.fontSize),left:glyph.left,right:glyph.right,
                        min:box.left+parseFloat(css.paddingLeft),max:box.right-parseFloat(css.paddingRight),
                        alignment:css.justifyContent,text:el.textContent,children:el.children.length});
                }
            }
            return rows;
        }''')
        for row in result:
            assert row['alignment'] == row['align'] and row['children'] == 0, row
            if row['kind'] == 'clamped':
                assert row['size'] == 12, row
            elif row['kind'] in ('short', 'empty', 'wide'):
                assert row['size'] == 22, row
            else:
                assert 12 <= row['size'] < 22, row
                assert row['left'] >= row['min']-.5 and row['right'] <= row['max']+.5, row
        page.screenshot(path=str(Path(outdir) / shot), clip={'x': 300, 'y': 990, 'width': 1400, 'height': 80})
        return V.SurfaceResult('hud-name-fit', 'desktop', shot, [], 0, None)
    except Exception as exc:  # a failed geometry assertion must fail visual acceptance
        return V.SurfaceResult('hud-name-fit', 'desktop', shot, [], 0, str(exc))
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shots', action='store_true', help='Refresh the synthetic HUD wiki image.')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    out = ROOT / 'runtime/hud-name-fit'
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            result = check(browser, out)
        finally:
            browser.close()
    if result.error:
        raise SystemExit(result.error)
    if args.shots:
        shutil.copy2(out / 'hud-name-fit.png', ROOT / 'src/docs/wiki/images/hud-team-name-fit.png')
    print('PASS HUD names: left, center, right, padding, min/max and resize')


if __name__ == '__main__':
    main()
