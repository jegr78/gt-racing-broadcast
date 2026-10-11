#!/usr/bin/env python3
"""Comparison pool selection uses actual recording/index fixtures."""
import unittest
import json
from urllib.parse import urlencode
import test_ui_server as web
import test_racecast as fixture

m=fixture.m


class Selection(unittest.TestCase):
    def test_selected_recordings_control_laps_and_best(self):
        with fixture._telemetry_sandbox() as (root,tgl):
            a=tgl.write_circle_recording(root)
            b=tgl.write_circle_recording(root,lap_secs=(10.,9.,8.),t0=1_700_007_200.)
            selected=m.telemetry_laps_data(track='ring01',car=str(tgl.CAR),recs=fixture._stem(a))
            self.assertTrue(selected['ok'],selected)
            self.assertEqual({l['rec'] for l in selected['laps']},{fixture._stem(a)})
            self.assertEqual(selected['theoretical_best'],16.)
            self.assertEqual(selected['best_sectors'],[3.2]*5)
            self.assertEqual({r['rec'] for r in selected['recordings']},{fixture._stem(a),fixture._stem(b)})
            all_laps=m.telemetry_laps_data(track='ring01',car=str(tgl.CAR))
            self.assertLess(all_laps['theoretical_best'],selected['theoretical_best'])
            none=m.telemetry_laps_data(track='ring01',car=str(tgl.CAR),recs='')
            self.assertEqual(none['laps'],[])
            self.assertEqual(none['best_sectors'],[])
            self.assertIsNone(none['theoretical_best'])
            self.assertIsNone(none['sector_theory'])

    def test_http_route_preserves_explicit_empty_and_rejects_invalid(self):
        with fixture._telemetry_sandbox() as (root,tgl):
            path=tgl.write_circle_recording(root)
            m.telemetry_laps_data(rec=fixture._stem(path))
            ctx=web._ctx();ctx['telemetry_laps']=m.telemetry_laps_data
            server,port=web._serve(ctx)
            try:
                for names,ok in (('',True),('../private',False)):
                    status,body=web._get(port,'/api/telemetry/laps?'+urlencode(dict(
                        track='ring01',car=str(tgl.CAR),recs=names)))
                    result=json.loads(body)
                    self.assertEqual(status,200)
                    self.assertEqual(result['ok'],ok)
                    if ok:self.assertEqual(result['laps'],[])
                    else:self.assertEqual(result['error'],'invalid recording selection: recording unavailable')
            finally:
                server.shutdown();server.server_close()

    def test_invalid_selection_does_not_expand_pool(self):
        with fixture._telemetry_sandbox() as (root,tgl):
            tgl.write_circle_recording(root)
            for names in ('../private','missing','a,,b',['wrong type']):
                result=m.telemetry_laps_data(track='ring01',car=str(tgl.CAR),recs=names)
                self.assertFalse(result['ok'])
                self.assertIn('recording selection',result['error'])


if __name__=='__main__':unittest.main()
