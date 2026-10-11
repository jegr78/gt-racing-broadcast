"""Shared distance-station comparisons using the existing lap and context rules."""
import math

import gt7_context
import gt7_laps


def delta(a, b):
    """B minus A at matching stations, as in Control Center tmDelta()."""
    return [{'d': p['d'], 'delta': math.floor((q['t'] - p['t']) * 1000 + .5) / 1000}
            for p, q in zip(a, b, strict=False) if p['d'] == q['d']]


def compare(a, b):
    provisional = a.get('state') == 'provisional' or b.get('state') == 'provisional'
    # A current lap can be inspected against a confirmed eligible reference. Its
    # observed prefix never enters a confirmed best/theoretical pool.
    ref, candidate = (b, a) if a.get('state') == 'provisional' else (a, b)
    candidates = gt7_laps.pool([{'laps': [candidate]}], ref.get('track_id'),
                               ref.get('car_id'), ref.get('rec'), ref.get('session'))
    compatible = bool(candidates and gt7_context.compatible(ref, candidate)
                      and (ref.get('provisional_comparison_eligible') if provisional else ref.get('comparison_eligible')))
    warnings = list(dict.fromkeys(a.get('context_warnings', []) + b.get('context_warnings', [])
                                 + a.get('track_definition_warnings', []) + b.get('track_definition_warnings', [])))
    if not compatible:
        warnings.append('These laps are outside the default comparable pool')
    sa, sb = a.get('sectors', []), b.get('sectors', [])
    if provisional:
        length = gt7_laps.lap_length_m(candidate)
        if a.get('state') == 'provisional':
            sa = gt7_laps.sectors(a.get('trace', []), length)
        if b.get('state') == 'provisional':
            sb = gt7_laps.sectors(b.get('trace', []), length)
    return {'compatible': compatible, 'provisional': provisional, 'warnings': warnings,
            'delta': delta(a.get('trace', []), b.get('trace', [])) if compatible else [],
            'lap_delta_s': round(b['time_s'] - a['time_s'], 3) if compatible and not provisional and a.get('time_s') is not None and b.get('time_s') is not None else None,
            'mini_sectors': {'a': sa, 'b': sb,
                            'delta': [round(y-x, 3) if x is not None and y is not None and compatible else None
                                      for x, y in zip(sa, sb, strict=False)]},
            'definition_compatible': bool(a.get('definition_fingerprint') and
                a.get('definition_fingerprint') == b.get('definition_fingerprint') and
                a.get('sector_variant_id') == b.get('sector_variant_id'))}
