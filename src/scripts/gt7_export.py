#!/usr/bin/env python3
"""GT7 telemetry export: one recording as CSV plus its JSON context snapshots.

Sits above gt7_recording and gt7_context. Stdlib only, so `racecast telemetry export` runs without a relay.
"""
import csv
import json
import math
import os
from contextlib import nullcontext

import gt7_cars
import gt7_channels
import gt7_context
import gt7_recording as gr
import gt7_telemetry

LEGACY_SAMPLE_COLUMNS = (
    "t_s", "session", "lap", "lap_t_s", "lap_dist_m", "on_track", "paused", "speed_kmh",
    "throttle_pct", "brake_pct", "throttle_input_pct", "brake_input_pct", "steer_deg",
    "gear", "rpm", "fuel_l", "tyre_fl_c", "tyre_fr_c", "tyre_rl_c", "tyre_rr_c",
    "pos_x", "pos_y", "pos_z", "car_id")
CONFIRMED_CHANNELS = gt7_channels.descriptors(False)
DIAGNOSTIC_CHANNELS = gt7_channels.descriptors(True)
EXTRA_CHANNEL_COLUMNS = tuple(d['key'] for d in CONFIRMED_CHANNELS if d['key'] not in LEGACY_SAMPLE_COLUMNS)
SAMPLE_COLUMNS = LEGACY_SAMPLE_COLUMNS + EXTRA_CHANNEL_COLUMNS + tuple(d['key']+'__state' for d in CONFIRMED_CHANNELS)
LAP_COLUMNS = (
    "session", "lap", "start_t_s", "end_t_s", "gt7_time_s", "relay_time_s", "status",
    "reason", "fuel_used_l", "top_speed_kmh", "car", "track", "layout")


def _fmt(excel):
    def num(value, digits):
        if value is None or not math.isfinite(value):
            return ""
        text = f"{value:.{digits}f}"
        return text.replace(".", ",") if excel else text
    return num


def _pct(byte):
    return None if byte is None else byte * 100.0 / 255.0


def _lap_dist(eng, pkt, by_session, tracks, prev):
    """Distance along the racing line when the session's track is known, else integrated.
    `prev` holds the last station of the current lap, so the export follows the lap
    index's continuity rule."""
    dist = eng.lap_distance()
    found = by_session.get(eng.session)
    if not found or "id" not in found or pkt.pos_x is None:
        return dist
    length = tracks.line_length(found["id"])
    if not length:
        return dist
    s = tracks.project([(pkt.pos_x, pkt.pos_z)], found["id"])
    lap = (eng.session, eng.lap_started_at())
    driven = dist or 0.0
    expected = prev["s"] + driven - prev["d"] if prev.get("lap") == lap else driven
    out = gr.follow_station(s[0] if s else None, expected, length)
    prev.update(lap=lap, s=out, d=driven)
    return out


def export_csv(path, out_dir, include_all=False, excel=False, cars=None, tracks=None, key=None, diagnostics=False,
               track_analysis=None, shift_analysis=None):
    """Write samples.csv and laps.csv for one recording into out_dir."""
    cars = cars if cars is not None else gt7_cars.CarDB()
    r = gr.Recording(path)
    context_error = None
    try:
        context_snapshot = (shift_analysis.get('context_snapshot') if shift_analysis is not None
                            and shift_analysis.get('context_snapshot') is not None else
                            (track_analysis.get('context_snapshot') if track_analysis else None)
                            or gt7_context.Store.for_recording(path).export())
    except (OSError, ValueError, gr.RecordingError):
        context_error = 'saved context is unavailable; raw measurements are still exported'
        context_snapshot = {'format': gt7_context.FORMAT, 'version': gt7_context.VERSION,
                            'available': False, 'error': context_error}
    num = _fmt(excel)
    eng = gt7_telemetry.TelemetryEngine()
    laps = []
    eng.on_lap = laps.append
    times = gr.LapTimeMatcher()
    _h, all_laps, _d = gr.replay_laps(path) if tracks is not None else (None, [], 0)
    by_session = gr.session_tracks(all_laps, tracks, key)
    os.makedirs(out_dir, exist_ok=True)
    delimiter = ";" if excel else ","
    encoding = "utf-8-sig" if excel else "utf-8"
    t0 = None
    written = 0
    station = {}
    with open(os.path.join(out_dir, "samples.csv"), "w", newline="", encoding=encoding) as fh, \
            (open(os.path.join(out_dir, "diagnostics.csv"), "w", newline="", encoding=encoding)
             if diagnostics else nullcontext()) as raw_fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(SAMPLE_COLUMNS)
        raw_writer = csv.writer(raw_fh, delimiter=delimiter) if raw_fh else None
        if raw_writer:
            raw_writer.writerow(('t_s', 'session', 'lap') + tuple(
                d['key']+suffix for d in DIAGNOSTIC_CHANNELS for suffix in ('', '__state', '__raw')))
        for wall_ts, _kind, plain in r.packets():
            t0 = wall_ts if t0 is None else t0
            pkt = gt7_telemetry.parse_packet(plain)
            closed = len(laps)
            eng.update(pkt, wall_ts)
            for lap in laps[closed:]:
                times.lap_closed(lap, wall_ts)
            times.update(pkt, wall_ts)
            if not include_all and (not pkt.on_track or pkt.paused or pkt.loading):
                continue
            decoded = gt7_channels.decode(plain)
            started = eng.lap_started_at()
            steer = None if pkt.steer_rad is None else math.degrees(pkt.steer_rad)
            w.writerow([
                num(wall_ts - t0, 3), eng.session, pkt.lap,
                num(wall_ts - started if started is not None else None, 3),
                num(_lap_dist(eng, pkt, by_session, tracks, station), 1),
                int(pkt.on_track), int(pkt.paused), num(pkt.speed_mps * 3.6, 1),
                num(_pct(pkt.throttle), 1), num(_pct(pkt.brake), 1),
                num(_pct(pkt.throttle_input), 1), num(_pct(pkt.brake_input), 1),
                num(steer, 1), pkt.gear, num(pkt.rpm, 0), num(pkt.fuel_level, 2),
                *(num(v, 1) for v in pkt.tyre_temp),
                num(pkt.pos_x, 2), num(pkt.pos_y, 2), num(pkt.pos_z, 2),
                "" if pkt.car_id is None else pkt.car_id]
                + [num(decoded[k]['value'], 6) for k in EXTRA_CHANNEL_COLUMNS]
                + [decoded[d['key']]['state'] for d in CONFIRMED_CHANNELS])
            if raw_writer:
                values = [num(wall_ts-t0, 3), eng.session, pkt.lap]
                for descriptor in DIAGNOSTIC_CHANNELS:
                    value = decoded[descriptor['key']]
                    values.extend((num(value['value'], 6), value['state'],
                                   '0x'+value['raw'] if value['raw'] is not None else ''))
                raw_writer.writerow(values)
            written += 1
    with open(os.path.join(out_dir, "laps.csv"), "w", newline="", encoding=encoding) as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(LAP_COLUMNS)
        for lap in laps:
            found = by_session.get(lap["session"])
            known = bool(found and "id" in found)
            w.writerow([
                lap["session"], lap["lap"], num(lap["start"] - t0, 3),
                num(lap["end"] - t0, 3), num(lap["gt7_time_s"], 3),
                num(lap["elapsed"], 3), lap["status"], lap["reason"],
                num(lap["fuel_used"], 2), num(lap["top_speed_mps"] * 3.6, 1),
                gr.car_name(cars, lap["car_id"]),
                found["track"] if known else "", found["layout"] if known else ""])
    with open(os.path.join(out_dir, 'channels.json'), 'w', encoding='utf-8') as schema:
        json.dump({'format': 'racecast-telemetry-channels', 'version': gt7_channels.SCHEMA_VERSION,
                   'channels': CONFIRMED_CHANNELS, 'diagnostics': DIAGNOSTIC_CHANNELS if diagnostics else [],
                   'states': {'value': 'including genuine zero', 'missing': 'packet does not contain the field',
                              'unset': 'declared sentinel recorded in its channel descriptor',
                              'nonfinite': 'NaN or infinity, blank numerical CSV cell'},
                   'axes': {'t_s': 'receiver clock from first recording packet',
                            'lap_t_s': 'receiver clock from recorded lap boundary',
                            'lap_dist_m': 'reference geometry when available, otherwise driven distance'},
                   'compatibility': {k: {'offset': off, 'unit': '% from byte / 255',
                                         'uncertainty': 'Driver vs filtered/assisted input interpretation is disputed'}
                                     for k, off in [('throttle_input_pct', '0x13c'), ('brake_input_pct', '0x13d')]}},
                  schema, ensure_ascii=False, indent=2, allow_nan=False)
    definitions = track_analysis.get('track_definition_snapshots', {}) if track_analysis else {}
    gt7_context._atomic(os.path.join(os.path.realpath(out_dir), 'track-definitions.json'),
                        {'format': 'racecast-track-definition-snapshot', 'version': 1, 'definitions': definitions})
    with open(os.path.join(out_dir, 'sectors.csv'), 'w', newline='', encoding=encoding) as fh:
        writer = csv.writer(fh, delimiter=delimiter)
        writer.writerow(('rec', 'session', 'lap', 'variant_id', 'definition_version', 'definition_fingerprint',
                         'sector', 'start_m', 'end_m', 'time_s', 'compound', 'context_confirmed', 'comparison_eligible'))
        for row in (track_analysis or {}).get('laps', []):
            larger = row.get('larger_sectors')
            if not larger:
                continue
            for i, name in enumerate(larger['names']):
                writer.writerow((row['rec'], row['session'], row['lap'], larger['variant_id'],
                                 row['track_definition']['version'], row['definition_fingerprint'], name,
                                 num(larger['bounds_m'][i], 3), num(larger['bounds_m'][i+1], 3),
                                 num(larger['times_s'][i], 6), row.get('compound') or '',
                                 int(bool(row.get('context_confirmed'))), int(bool(row.get('comparison_eligible')))))
    gt7_context._atomic(os.path.join(os.path.realpath(out_dir), "context.json"), context_snapshot)
    if shift_analysis is not None:
        gt7_context._atomic(os.path.join(os.path.realpath(out_dir), 'shift-references.json'),
                            {'format': 'racecast-shift-reference-snapshot', 'version': 1,
                             'references': shift_analysis.get('shift_reference_snapshots', {})})
        columns = ['rec', 'session', 'lap', 'reference_id', 'stint_id', 'compound', 'from_gear', 'to_gear',
                   'gear_change_t_s', 'last_old_gear_rpm', 'pre_cut_t_s', 'pre_cut_rpm', 'post_t_s', 'post_rpm',
                   'phase_status', 'phase_method', 'acceleration_target_rpm', 'acceleration_deviation_rpm',
                   'economy_target_rpm', 'economy_deviation_rpm', 'intentional_shortshift', 'neutral_bridge']
        with open(os.path.join(os.path.realpath(out_dir), 'shifts.csv'), 'w', newline='', encoding='utf-8-sig' if excel else 'utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=columns, delimiter=';' if excel else ',')
            writer.writeheader()
            for row in shift_analysis.get('laps', []):
                analysis = row.get('shift_analysis') or {}
                for event in analysis.get('shifts', []):
                    values = {k: {'rec': row.get('rec'), 'session': row['session'], 'lap': row['lap'],
                                  'reference_id': analysis.get('reference_id')}.get(k, event.get(k)) for k in columns}
                    if excel:
                        values = {k: num(v, 6) if isinstance(v, float) else v for k, v in values.items()}
                    writer.writerow(values)
    return {"dir": out_dir, "samples": written, "laps": len(laps), "dropped": r.dropped,
            "context_error": context_error}
