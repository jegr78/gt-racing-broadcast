"""Bounded voice-question routing and deterministic, current-fact follow-ups."""
import re
from driver_radio_assessment import number


QUICK = ('position','to_go','fuel_finish','consumption','tyre_age','stint')


def plan(text, previous=None):
    if (not isinstance(text,str) or not text.strip() or len(text)>400
            or any(ord(c)<32 for c in text)):
        raise ValueError('Invalid voice question')
    clean=text.casefold()
    rejected=bool(re.search(r'\b(?:mute|unmute|disconnect|execute|delete|shutdown|switch scene|box now|pit now|stumm|lösche|ausschalten)\b',clean))
    extra=bool(re.search(r'\b(?:one more lap|extra lap|another lap|noch eine runde|eine runde länger|weitere runde)\b',clean))
    recognized=True
    if rejected:preset='status'
    elif re.search(r'\b(?:(?:fastest|best) lap|(?:schnellste|beste) runde)\b',clean):preset='fastest_lap'
    elif re.search(r'\b(?:(?:last|previous|latest) lap|letzte(?:n|r)? (?:runde|rundenzeit))\b',clean):preset='last_lap'
    elif re.search(r'\b(?:enough fuel|fuel to (?:the )?(?:end|finish)|make it to the (?:end|finish)|fuel for the race'
                   r'|reicht (?:der |das )?(?:sprit|benzin)|(?:sprit|benzin) bis (?:ins ziel|zum ende)|genug (?:sprit|benzin))\b',clean):preset='fuel_finish'
    elif re.search(r'\b(?:fuel per lap|consumption|verbrauch|(?:sprit|benzin) pro runde)\b',clean):preset='consumption'
    elif re.search(r'\b(?:position|where am i|what place|platzierung|welche position|wo liege ich|welcher platz)\b',clean):preset='position'
    elif re.search(r'\b(?:stint|since (?:my |the )?(?:last )?stop|seit (?:dem |meinem )?(?:letzten )?stopp)\b',clean):preset='stint'
    elif re.search(r'\b(?:tyre age|tire age|how old (?:are )?(?:my |the )?(?:tyres|tires)|reifenalter'
                   r'|wie alt (?:sind )?(?:die |meine )?reifen)\b',clean):preset='tyre_age'
    elif re.search(r'\b(?:fuel|range|reserve|finish|sprit|benzin|tank|treibstoff|reichweite)\b',clean):preset='fuel'
    elif re.search(r'\b(?:laps? (?:to go|left|remaining)|how many laps|runden (?:noch|übrig|verbleibend)|wie viele runden)\b',clean):preset='to_go'
    elif re.search(r'\b(?:tyres?|tires?|temperature|temperatures|reifen|reifentemperatur|reifentemperaturen)\b',clean):preset='tyres'
    elif re.search(r'\b(?:pace|slower|faster|lap time|lap times|langsamer|schneller|tempo|rundenzeit)\b',clean):preset='pace'
    elif previous in ('fuel','pace','tyres','fastest_lap','last_lap') and (extra or re.match(r'\s*(?:and|what about|und|was wäre)',clean)):preset=previous
    elif re.search(r'\b(?:status|summary|overview|how am i doing|überblick|zusammenfassung)\b',clean):preset='status'
    else:preset='status';recognized=False
    return dict(preset=preset,extra_lap=extra and preset=='fuel',rejected=rejected,recognized=recognized)


def answer(text, assessment, previous=None):
    route=plan(text,previous)
    answers=(assessment['lap_answers'] if route['preset'] in ('fastest_lap','last_lap')
             else assessment['quick_answers'] if route['preset'] in QUICK else assessment['answers'])
    result=answers[route['preset']]
    if route['rejected']:
        return route,'I can explain current telemetry. Vehicle and broadcast control commands are unavailable.'
    if not route['recognized']:
        return route,"I can't answer that yet. Ask about fuel, pace, tyres, position, laps to go or your fastest lap."
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
