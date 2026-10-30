"""Private coaching acceptance through the actual relay's bounded HTTP interfaces."""
import json
import time
from urllib.parse import urlencode,urlparse

import driver_radio_admin
import e2e_checks as http


def check(base,profile,token,timeout=20):
    """Exercise source/frozen operation during synthetic recording replay, without audio."""
    location=urlparse(base)
    if location.scheme!='http' or location.hostname!='127.0.0.1' or not location.port:
        raise ValueError('Coaching acceptance requires an owned loopback relay')
    began=time.monotonic()
    client=driver_radio_admin.Client(profile,location.port)
    url=base+'/console/driver-radio/coach?'+urlencode({'t':token})
    status,raw,_=http.http_request(url)
    denied=json.loads(raw)
    assert status==403 and denied['error']['code']=='feature_disabled', 'Coaching answered before the AI Coach was switched on'
    settings=client.request()
    on=dict(enabled=True,agent=None)
    changed=client.request(body=dict(expected=settings['revision'],features=dict(engineer=on,coach=on),
                                    voice=dict(settings['voice'],enabled=False),
                                    providers=dict(settings['providers'],speech=False)))
    assert changed['features']['coach']['enabled'] and not changed['voice']['enabled'] and not changed['providers']['speech']
    first=client.request(coach=True)
    assert not first['active'] and not first['history']['entries']
    started=client.request(coach=True,body=dict(expected=first['revision'],action='start',options=dict(allow_ai=False)))
    assert started['active'] and not started['allow_ai']
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        current=client.request(coach=True)
        if not current['pending'] and current.get('history',{}).get('entries'):break
        time.sleep(.05)
    else:raise AssertionError('Replay coaching did not publish its waiting checkpoint')
    assert current['phase']=='paused' and current['calibration'] is None and current['focus'] is None
    assert not current['references'],'Replay offered recorded laps as current live references'
    assert current['latest'] is None and not current['allow_ai'],'Replay produced a live coaching result'
    row=current['history']['entries'][0]
    assert row['baseline'] is None and row['focus'] is None and row['latest'] is None and not row['source_ids']
    status,raw,_=http.http_request(url);remote=json.loads(raw)
    # One driver per Solo POV profile: the Console shows the same training and history.
    assert status==200 and remote['active'] and remote['epoch']==current['epoch'],'The Console missed the profile training'
    assert [r['id'] for r in remote['history']['entries']]==[r['id'] for r in current['history']['entries']]
    assert remote['focus'] is None and remote['latest'] is None,'Replay produced Console coaching'
    stopped=client.request(coach=True,body=dict(expected=0,action='stop',options={}))
    assert not stopped['active']
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        final=client.request(coach=True)
        if not final['pending'] and final.get('history',{}).get('entries') and final['history']['entries'][0]['status']=='stopped':break
        time.sleep(.05)
    else:raise AssertionError('Packaged coaching did not finalize its private checkpoint')
    history=final['history']['entries']
    assert len(history)==1 and history[0]['debrief']['next'] and history[0]['latest'] is None
    raw=json.dumps(history)
    assert all('"'+field+'"' not in raw for field in ('trace','audio','transcript','points'))
    return dict(status='pass',profile=profile,history_entries=len(history),scored_laps=0,active=final['active'],
                elapsed_s=round(time.monotonic()-began,3))
