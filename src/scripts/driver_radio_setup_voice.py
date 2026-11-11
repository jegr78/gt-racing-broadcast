"""Wake-gated race setup by voice: a fixed English and German vocabulary, read back in English."""
import re

from driver_radio_assessment import COMPOUND_NAMES

MAX_MANDATORY_STOPS = 20
SESSION_READBACK = dict(practice='practice session', qualifying='qualifying', race='race')
SESSIONS_SPOKEN = {'practice': 'practice', 'practice session': 'practice', 'training': 'practice',
                   'trainingssession': 'practice', 'freies training': 'practice', 'qualifying': 'qualifying',
                   'qualifying session': 'qualifying', 'qualifikation': 'qualifying', 'quali': 'qualifying',
                   'race': 'race', 'race session': 'race', 'rennen': 'race'}

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
OPENING_READBACK = dict(formation='formation lap first', to_box='first lap to the box')
_OPENING = {'formation lap': 'formation', 'formationsrunde': 'formation', 'einfuehrungsrunde': 'formation',
            'box start': 'to_box', 'boxenstart': 'to_box', 'follow up lobby': 'to_box', 'folgelobby': 'to_box',
            'first lap to the box': 'to_box'}
_OPENING_SHORT = {'formation': 'formation', 'box': 'to_box'}  # interview answers only


# The free transcription repeats the wake phrase and often mishears it ("hey sheriff",
# "hey chair of", "hate sheriff", "hey hf", "hade"). A leading "hey"-like word, or the
# name alone, plus at most two words that start no command or question are dropped.
_WAKE = {'hey', 'hay', 'hei', 'hej', 'hi', 'he', 'hate', 'hade', 'heyjeff', 'eh'}
_NAMES = {'jeff', 'geoff', 'chef', 'jeffrey', 'engineer'}
_VOCABULARY = set('''race rennen practice training trainingssession freies qualifying qualifikation quali current
    aktuelle session this it its we were wir are have haben there es is ist the die das der starting start starten
    reserve fuel sprit benzin tank now jetzt mandatory pflicht no keine kein what whats how where which when do does
    can will give tell wie wo was welche welcher welches wann reicht gibt position platzierung lap laps last best
    fastest runde runden pace tempo tyre tyres tire tires reifen status summary overview stint consumption verbrauch
    temperature temperatures and und one more extra noch box pit mute stop ask frag fragen please bitte interview
    starte formation formationsrunde einfuehrungsrunde einführungsrunde boxenstart folgelobby follow first normal
    normaler with mit'''.split())


def _word(token):
    return token.casefold().strip(',.!?;:')


def strip_wake(text):
    """The utterance without a leading, possibly misheard wake phrase."""
    tokens = text.split()
    if not tokens:
        return text
    first = _word(tokens[0])
    if first in _NAMES:
        rest = tokens[1:]
    elif first in _WAKE:
        rest = tokens[1:]
        for _ in range(2):
            if rest and _word(rest[0]) not in _VOCABULARY and not _word(rest[0]).isdigit():
                rest = rest[1:]
    else:
        return text
    return ' '.join(rest) if rest else text


def _clean(text):
    clean = re.sub(r'(\d),(\d)', r'\1.\2', strip_wake(text)).casefold().translate(str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss', '-': ' ', ',': ' '}))
    clean = ' '.join(clean.split())
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


def _opening_plan(kind):
    """The setup change for lap 1; None declares it a race lap again."""
    readback = OPENING_READBACK[kind] if kind else 'lap 1 is a race lap'
    return dict(setup=dict(opening_lap=kind), readback=f'Copy, {readback}.')


def _opening_lap(clean):
    """'formation lap', 'wir haben eine Einführungsrunde', 'box start', 'no formation lap'."""
    if re.fullmatch(r'(?:no|keine|kein) (?:formation lap|formationsrunde|einfuehrungsrunde|box start|boxenstart)'
                    r'|normal(?:er)? start', clean):
        return _opening_plan(None)
    match = re.fullmatch(r'(?:(?:we |wir )?(?:have |haben |start with |starten mit )|with |mit )?'
                         r'(?:a |an |the |eine |einer |der |die )?(.+)', clean)
    kind = _OPENING.get(match[1]) if match else None
    return _opening_plan(kind) if kind else None


def plan(text):
    """The setting one spoken command changes, or None when it is no setup command.

    Returns dict(setup={...}) for race declarations or dict(service=CODE) for a tyre
    change, plus the English read-back."""
    if not isinstance(text, str) or not text.strip() or len(text) > 400 or any(ord(c) < 32 for c in text):
        return None
    clean = _clean(text)
    opening = _opening_lap(clean)
    if opening is not None:
        return opening
    # "We have no mandatory pit stops", "wir haben zwei Pflichtstopps".
    clean = re.sub(r'^(?:we |wir )?(?:have |haben |there are |there is |es gibt )(?=.*(?:mandatory|pflicht))', '', clean)
    # "Current session is the race", "this is qualifying", "die aktuelle Session ist das Rennen".
    declared = re.fullmatch(r'(?:(?:the |die |das )?(?:current |aktuelle )?session (?:is |ist )?|this is |it is |its |'
                            r'we are in |were in |das ist |wir sind im |wir sind in der )'
                            r'(?:a |an |the |ein |eine |das |der |die )?(.+)', clean)
    if declared:
        words = declared[1].split()
        named = 2 if ' '.join(words[:2]) in SESSIONS_SPOKEN else 1 if words[0] in SESSIONS_SPOKEN else 0
        if not named:
            return None
        session = SESSIONS_SPOKEN[' '.join(words[:named])]
        rest = ' '.join(words[named:])
        if not rest:
            return dict(setup=dict(session=session), readback=f'Copy, {SESSION_READBACK[session]}.')
        # "... is the race, six laps": the session and its length in one breath.
        length = plan('race ' + rest) if session == 'race' else None
        if not length or set(length.get('setup', {})) != {'laps', 'duration_min'}:
            return None
        return dict(setup=dict(session=session, **length['setup']),
                    readback=f'Copy, {SESSION_READBACK[session]}, {length["readback"][len("Copy, "):]}')
    session = SESSIONS_SPOKEN.get(clean)
    if session:
        return dict(setup=dict(session=session), readback=f'Copy, {SESSION_READBACK[session]}.')
    match = re.fullmatch(r'(?:the |das )?(?:race|rennen)\s+(?:is\s+|ist\s+|geht ueber\s+)?(.+?)\s+'
                         r'(laps?|runden?|minutes?|minuten?|mins?)', clean)
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


# --- Session interview (#1088): one wake phrase, then one question per setting ------------

INTERVIEW = ('session', 'length', 'opening_lap', 'starting_compound', 'mandatory_stops', 'mandatory_compounds',
             'fuel_reserve')
QUESTIONS = dict(session='Which session is it?', length='How long is the race, laps or minutes?',
                 opening_lap='Is lap 1 a formation lap or a lap to the box?',
                 starting_compound='Which tyres do you start on?', mandatory_stops='How many mandatory stops?',
                 mandatory_compounds='Which compounds are mandatory?', fuel_reserve='What fuel reserve?')
_NONE = ('none', 'no', 'zero', 'nothing', 'keine', 'kein', 'keiner', 'null', 'nichts')
_SKIP = ('skip', 'next', 'pass', 'skip it', 'weiter', 'ueberspringen', 'naechste', 'naechste frage')
_STOP = ('stop', 'cancel', 'end', 'abort', 'that is all', 'thats all', 'abbrechen', 'stopp', 'ende', 'fertig')
# Words an answer may use besides the tables above: declarations ("it is the race"),
# units, connectors and decimals.
_ANSWER_WORDS = '''it is its this the a an current session we are were in das ist die der ein eine aktuelle wir sind im
    lap laps runde runden minute minutes mins minuten litre litres liter liters reserve fuel mandatory pit stop stops
    pflicht pflichtstopp pflichtstopps and und plus point komma hundred hundert tyre tyres tire tires reifen frage'''


def interview_start(text):
    """Whether the driver asks to be interviewed for the session setup. Tolerates the
    free transcription's slips: wake residue, "asked", a misheard "info"."""
    if not isinstance(text, str) or len(text) > 400:
        return False
    clean = _clean(text)
    return bool(re.fullmatch(r'(?:start )?(?:the )?session (?:interview|setup|questions)'
                             r'|session (?:infos?|daten) abfragen|starte (?:das )?session interview', clean)
                or re.search(r'(?:^|\b(?:ask|asks|asked|asking|interview|interviewing) )me (?:for|about|on|with) '
                             r'(?:the )?(?:session|race)\b', clean)
                or re.search(r'\bfrag\w* mich (?:nach|zu|ueber) (?:den |der |die )?session\b', clean))


def _model_spellings(word):
    """A cleaned word as the Vosk models may spell it: with and without umlauts."""
    umlaut = word.replace('ae', 'ä').replace('oe', 'ö').replace('ue', 'ü')
    return {word, umlaut, umlaut.replace('ss', 'ß'), word.replace('ss', 'ß')}


def interview_words():
    """Every single word an interview answer can use, for a grammar-limited recogniser."""
    words = set(_ANSWER_WORDS.split())
    for table in (SESSIONS_SPOKEN, _FAMILIES, _GRADES, _SINGLE, _UNITS, _TENS, _ZERO, _NONE, _SKIP, _STOP,
                  _OPENING, _OPENING_SHORT):
        for entry in table:
            words.update(entry.split())
    # German tens are spoken as one word: fuenfundzwanzig.
    words.update(unit + 'und' + ten for unit in 'ein zwei drei vier fuenf sechs sieben acht neun'.split()
                 for ten in 'zwanzig dreissig vierzig fuenfzig sechzig siebzig achtzig neunzig'.split())
    return tuple(sorted(w for word in words for w in _model_spellings(word) if w.isalpha()))


def interview_reply(step, text):
    """('skip'|'stop'|'unclear', None) or ('set', plan) for one interview answer."""
    if not isinstance(text, str) or not text.strip() or len(text) > 400 or any(ord(c) < 32 for c in text):
        return 'unclear', None
    clean = _clean(text)
    if clean in _SKIP:
        return 'skip', None
    if clean in _STOP:
        return 'stop', None
    none = clean in _NONE
    if step == 'session':
        result = plan(clean)
        result = result if result and set(result.get('setup', {})) == {'session'} else None
    elif step == 'length':
        result = plan('race ' + clean)
    elif step == 'starting_compound':
        code = compound(clean)
        result = dict(setup=dict(starting_compound=code), readback=f'Copy, starting on {COMPOUND_NAMES[code]}.') if code else None
    elif step == 'mandatory_stops':
        result = plan('no mandatory stops' if none else clean if 'mandatory' in clean or 'pflicht' in clean
                      else clean + ' mandatory stops')
    elif step == 'mandatory_compounds':
        result = plan('mandatory none' if none else clean if re.match(r'(?:mandatory|pflicht) ', clean) else 'mandatory ' + clean)
    elif step == 'opening_lap':
        kind = _OPENING_SHORT.get(clean)
        result = _opening_plan(None) if none else _opening_plan(kind) if kind else _opening_lap(clean)
    elif step == 'fuel_reserve':
        amount = '0' if none else clean
        result = plan('reserve ' + amount if re.search(r'(?:litres?|liters?|liter|l)$', amount) else 'reserve ' + amount + ' litres')
    else:
        result = None
    if result is None or 'setup' not in result:
        return 'unclear', None
    return 'set', result


def interview_summary(readbacks):
    """One read-back for every answered setting: 'Copy, race, 12 laps, reserve 2 litres.'"""
    parts = [r[len('Copy, '):].rstrip('.') for r in readbacks if r.startswith('Copy, ')]
    return 'Copy, ' + ', '.join(parts) + '.' if parts else 'Copy, nothing set.'
