"""Pure Splitscreen builder composition; shared race data stays on /hud/data."""
from html.parser import HTMLParser

import overlay_build as ob

HUD_SOURCE = 'Splitscreen HUD Overlay'


class _EnduranceMarkup(HTMLParser):
    """Keep static HUD markup apart from Solo-only capture/telemetry blocks."""
    _VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img',
                       'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'))

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.depth:
            if tag not in self._VOID:
                self.depth += 1
        elif attrs.get('id') in ('tele', 'tyres-capture'):
            self.depth = 0 if tag in self._VOID else 1
        else:
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if not self.depth and dict(attrs).get('id') not in ('tele', 'tyres-capture'):
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
        else:
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data(f'&{name};')

    def handle_charref(self, name):
        self.handle_data(f'&#{name};')

    def handle_comment(self, data):
        if not self.depth:
            self.parts.append('<!--' + data + '-->')


def builder_html(hud, splitscreen):
    """Offline canvas includes the actual feed/label and Endurance HUD slots."""
    parser = _EnduranceMarkup()
    parser.feed(ob.base_body(hud))
    return ('<!doctype html><html><head><style>' + ob.base_style(hud) + '\n'
            + ob.base_style(splitscreen) + '</style></head><body>'
            + ob.base_body(splitscreen).replace(" hidden", "") + '\n' + ''.join(parser.parts) + '</body></html>')


def hud_html(hud):
    """Same race/timer/asset endpoints, separate scene stylesheet only."""
    return hud.replace('href="/hud/override.css"',
                       'href="/splitscreen/hud/override.css"')


def starter_layouts():
    """Templates change chosen A/B geometry, never CURRENT/NEXT assignment."""
    equal = {'split-feed-a': {'left': 0, 'top': 270, 'width': 960, 'height': 540},
             'split-feed-b': {'left': 960, 'top': 270, 'width': 960, 'height': 540}}
    asymmetric = {'split-feed-a': {'left': 40, 'top': 180, 'width': 1280, 'height': 720},
                  'split-feed-b': {'left': 1360, 'top': 180, 'width': 520, 'height': 293}}
    labels = {key: {'offsetRight': 0, 'offsetTop': 0, 'visible': True}
              for key in ('split-left', 'split-right')}
    return [{'id': 'equal-split', 'label': 'Equal side by side', 'slots': {**equal, **labels}},
            {'id': 'main-secondary', 'label': 'Large A + secondary B', 'slots': {**asymmetric, **labels}}]
