"""Bounded voice-question routing and deterministic, current-fact follow-ups."""
import re
from driver_radio_assessment import number


def plan(text, previous=None):
    if (not isinstance(text,str) or not text.strip() or len(text)>400
            or any(ord(c)<32 for c in text)):
        raise ValueError('Invalid voice question')
    clean=text.casefold()
    rejected=bool(re.search(r'\b(?:mute|unmute|disconnect|execute|delete|shutdown|switch scene|box now|pit now|stumm|lösche|ausschalten)\b',clean))
    extra=bool(re.search(r'\b(?:one more lap|extra lap|another lap|noch eine runde|eine runde länger|weitere runde)\b',clean))
    if rejected:preset='status'
    elif re.search(r'\b(?:fuel|range|reserve|finish|consumption|sprit|benzin|tank|treibstoff|verbrauch|reichweite)\b',clean):preset='fuel'
    elif re.search(r'\b(?:tyres?|tires?|temperature|temperatures|reifen|reifentemperatur|reifentemperaturen)\b',clean):preset='tyres'
    elif re.search(r'\b(?:pace|slower|faster|lap time|lap times|langsamer|schneller|tempo|rundenzeit)\b',clean):preset='pace'
    elif previous in ('fuel','pace','tyres') and (extra or re.match(r'\s*(?:and|what about|und|was wäre)',clean)):preset=previous
    else:preset='status'
    return dict(preset=preset,extra_lap=extra and preset=='fuel',rejected=rejected)


def answer(text, assessment, previous=None):
    route=plan(text,previous)
    result=assessment['answers'][route['preset']]
    if route['rejected']:
        return route,'I can explain current telemetry. Vehicle and broadcast control commands are unavailable.'
    if route['extra_lap']:
        fuel=assessment['fuel']
        current=number(fuel.get('current_l'));burn=number(fuel.get('consumption_l'),True)
        reserve=number(fuel.get('reserve_l'))
        if current is None or burn is None or reserve is None:
            result='Fuel after an extra full lap is unavailable without current fuel and measured consumption.'
        else:
            left=current-burn
            result=(f'After an extra full lap at measured consumption, estimated fuel would be {left:.1f} litres. '
                    + ('That is below the declared reserve.' if left<reserve else 'That preserves the declared reserve.')
                    + ' This is a conditional estimate, not a pit instruction.')
            if left<0:result='Fuel is estimated to run out before completing an extra full lap at measured consumption.'
            if fuel.get('state')=='provisional':result+=' The consumption estimate is provisional.'

    return route,result
