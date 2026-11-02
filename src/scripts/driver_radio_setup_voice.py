"""Wake-gated race setup by voice: a fixed English and German vocabulary, read back in English."""
import re

from driver_radio_assessment import COMPOUND_NAMES

MAX_MANDATORY_STOPS = 20
SESSION_READBACK = dict(practice='practice session', qualifying='qualifying', race='race')

_FAMILIES = {'racing': 'R', 'rennen': 'R', 'rennreifen': 'R', 'renn': 'R',
             'sports': 'S', 'sport': 'S', 'sportreifen': 'S',
             'comfort': 'C', 'komfort': 'C', 'komfortreifen': 'C'}
_GRADES = {'soft': 'S', 'weich': 'S', 'weiche': 'S', 'medium': 'M', 'mittel': 'M', 'mittlere': 'M',
           'hard': 'H', 'hart': 'H', 'harte': 'H'}
_SINGLE = {'intermediate': 'IM', 'intermediates': 'IM', 'intermediates reifen': 'IM', 'wet': 'W', 'wets': 'W',
           'full wet': 'W', 'regenreifen': 'W', 'regen': 'W'}
_UNITS = dict(zip(('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen '
                   'sixteen seventeen eighteen nineteen').split(), range(1, 20), strict=True))
_UNITS.update(zip(('eins zwei drei vier fuenf sechs sieben acht neun zehn elf zwoelf dreizehn vierzehn fuenfzehn '
                   'sechzehn siebzehn achtzehn neunzehn').split(), range(1, 20), strict=True))
_UNITS.update(ein=1, eine=1, einen=1, a=1, an=1, zwo=2)
_TENS = dict(twenty=20, thirty=30, forty=40, fifty=50, sixty=60, seventy=70, eighty=80, ninety=90,
             zwanzig=20, dreissig=30, vierzig=40, fuenfzig=50, sechzig=60, siebzig=70, achtzig=80, neunzig=90)
_ZERO = {'zero', 'no', 'null', 'keine', 'kein'}


def _clean(text):
    clean = re.sub(r'(\d),(\d)', r'\1.\2', text).casefold().translate(str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss', '-': ' ', ',': ' '}))
    clean = ' '.join(clean.split())
    clean = re.sub(r'^(?:hey jeff|hey engineer)\s*', '', clean)
    return re.sub(r'[.!?]+$', '', clean).strip()


def _integer(words):
    """A spoken whole number up to 999: digits, English or German words."""
    words = words.strip()
    if re.fullmatch(r'\d{1,3}', words):
        return int(words)
    if words in _ZERO:
        return 0
    if words in _UNITS:
        return _UNITS[words]
    if words in _TENS:
        return _TENS[words]
    parts = words.split()
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS and _UNITS[parts[1]] < 10:
        return _TENS[parts[0]]+_UNITS[parts[1]]
    match = re.fullmatch(r'(\w+?)und(\w+)', words)  # fuenfundzwanzig
    if match and match[1] in _UNITS and _UNITS[match[1]] < 10 and match[2] in _TENS:
        return _TENS[match[2]]+_UNITS[match[1]]
    if len(parts) == 2 and parts[1] in ('hundred', 'hundert') and parts[0] in _UNITS:
        return _UNITS[parts[0]]*100
    return None


def _decimal(words):
    words = words.strip()
    if re.fullmatch(r'\d{1,4}(?:[.]\d{1,2})?', words):
        return float(words)
    match = re.fullmatch(r'(.+?) (?:point|komma) (\w+)', words)
    if match:
        whole, fraction = _integer(match[1]), _integer(match[2])
        if whole is not None and fraction is not None and fraction < 10:
            return whole+fraction/10
    value = _integer(words)
    return None if value is None else float(value)


def compound(words):
    """One spoken compound name, English or German, as its GT7 code."""
    words = re.sub(r'\b(?:tyres?|tires?|reifen)$', '', words.strip()).strip()
    if words in _SINGLE:
        return _SINGLE[words]
    parts = words.split()
    if len(parts) == 2 and parts[0] in _FAMILIES and parts[1] in _GRADES:
        return _FAMILIES[parts[0]]+_GRADES[parts[1]]
    if len(parts) == 2 and parts[0] in _GRADES and parts[1] in _FAMILIES:  # weiche Rennreifen
        return _FAMILIES[parts[1]]+_GRADES[parts[0]]
    match = re.fullmatch(r'(renn|sport|komfort)(weich|mittel|hart)', words)
    if match:
        return _FAMILIES[match[1]]+_GRADES[match[2]]
    return None


def _compounds(words):
    codes = []
    for part in re.split(r'\s+(?:and|und|plus)\s+', words.strip()):
        code = compound(part)
        if code is None:
            return None
        if code not in codes:
            codes.append(code)
    return codes or None


def _plural(value, word):
    return f'{value:g} {word}{"" if value == 1 else "s"}'


def _names(codes):
    names = [COMPOUND_NAMES[c] for c in codes]
    return names[0] if len(names) == 1 else ', '.join(names[:-1])+' and '+names[-1]


def plan(text):
    """The setting one spoken command changes, or None when it is no setup command.

    Returns dict(setup={...}) for race declarations or dict(service=CODE) for a tyre
    change, plus the English read-back."""
    if not isinstance(text, str) or not text.strip() or len(text) > 400 or any(ord(c) < 32 for c in text):
        return None
    clean = _clean(text)
    session = {'practice': 'practice', 'practice session': 'practice', 'training': 'practice',
               'trainingssession': 'practice', 'freies training': 'practice',
               'qualifying': 'qualifying', 'qualifying session': 'qualifying', 'qualifikation': 'qualifying',
               'quali': 'qualifying', 'race': 'race', 'race session': 'race', 'rennen': 'race'}.get(clean)
    if session:
        return dict(setup=dict(session=session), readback=f'Copy, {SESSION_READBACK[session]}.')
    match = re.fullmatch(r'(?:race|rennen)\s+(.+?)\s+(laps?|runden?|minutes?|minuten?|mins?)', clean)
    if match:
        value = _integer(match[1])
        if value is None or not 0 < value <= (100000 if match[2][0] in 'lr' else 10080):
            return None
        if match[2][0] in 'lr':
            return dict(setup=dict(laps=value, duration_min=None), readback=f'Copy, {_plural(value, "lap")}.')
        return dict(setup=dict(duration_min=value, laps=None), readback=f'Copy, {_plural(value, "minute")}.')
    match = re.fullmatch(r'(?:starting on|start on|start auf|starten auf|wir starten auf)\s+(.+)', clean)
    if match and compound(match[1]):
        code = compound(match[1])
        return dict(setup=dict(starting_compound=code), readback=f'Copy, starting on {COMPOUND_NAMES[code]}.')
    match = re.fullmatch(r'(?:fuel )?reserve\s+(.+?)\s+(?:litres?|liters?|liter|l)', clean)
    if match:
        value = _decimal(match[1])
        if value is None or not 0 <= value <= 1000:
            return None
        return dict(setup=dict(fuel_reserve_l=value), readback=f'Copy, reserve {value:g} litres.')
    match = re.fullmatch(r'(?:now on|jetzt auf|wir sind jetzt auf|now running)\s+(.+)', clean)
    if match and compound(match[1]):
        code = compound(match[1])
        return dict(service=code, readback=f'Copy, now on {COMPOUND_NAMES[code]}.')
    match = re.fullmatch(r'(.+?)\s+(?:mandatory (?:pit )?stops?|pflichtstopps?|pflicht stopps?|pflichtboxenstopps?)', clean)
    if match:
        value = _integer(match[1])
        if value is None or value > MAX_MANDATORY_STOPS:
            return None
        text = 'no mandatory stops' if value == 0 else _plural(value, 'mandatory stop')
        return dict(setup=dict(mandatory_stops=value), readback=f'Copy, {text}.')
    match = re.fullmatch(r'(?:mandatory|pflicht)\s+(.+)', clean)
    if match:
        if match[1] in ('none', 'no compounds', 'keine', 'keine reifen'):
            return dict(setup=dict(mandatory_compounds=[]), readback='Copy, no mandatory compounds.')
        codes = _compounds(match[1])
        if codes:
            return dict(setup=dict(mandatory_compounds=codes), readback=f'Copy, mandatory {_names(codes)}.')
    return None
