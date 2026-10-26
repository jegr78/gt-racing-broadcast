"""Fixed local producer proxy for profile-bound radio settings and native controls."""
import json
import http_util

PROTOCOL='racecast-driver-radio-admin-v1'
MAX_COACH_RESPONSE=1024*1024  # 32 compact journal records, debriefs and the current actor view.


class AdminError(ValueError):
    def __init__(self,code):
        self.code=code
        messages={'conflict':'Radio settings changed; reload the current form',
                  'invalid_settings':'Invalid radio settings', 'invalid_body':'Invalid radio command',
                  'source_unavailable':'Driver radio requires a Solo POV telemetry profile',
                  'settings_unavailable':'Radio settings are unreadable; repair them locally',
                  'profile_changed':'Active profile changed; reload the current form',
                  'foreign_origin':'Radio administration requires the local server origin'}
        super().__init__(messages.get(code,'Radio administration unavailable'))


class Client:
    def __init__(self,profile,port):self.profile=profile;self.url=f'http://127.0.0.1:{int(port)}/driver-radio/admin'
    def request(self,native=False,body=None,coach=False):
        if native and coach:raise ValueError('Choose one radio route')
        encoded=None if body is None else json.dumps(dict(body,profile=self.profile),allow_nan=False).encode()
        if encoded is not None and len(encoded)>16384:raise ValueError('Radio settings are too large')
        try:
            try:response=http_util.open_url(self.url+('/coach' if coach else '/native' if native else ''),data=encoded,
                    headers={'Content-Type':'application/json'},timeout=4,allow_redirects=False)
            except http_util.HTTPError as exc:response=exc
            with response:
                limit=MAX_COACH_RESPONSE if coach else 65536
                raw=response.read(limit+1)
                if len(raw)>limit:raise ValueError('Radio response is too large')
                result=json.loads(raw)
        except (OSError,ValueError,TypeError,RecursionError):
            raise ValueError('Radio relay unavailable; start the relay for this profile') from None
        if isinstance(result,dict) and isinstance(result.get('error'),dict):
            code=result['error'].get('code')
            raise AdminError(code if isinstance(code,str) else 'unavailable')
        if not isinstance(result,dict) or result.get('profile')!=self.profile or result.get('protocol')!=PROTOCOL:
            raise ValueError('The local relay does not match this profile and radio protocol')
        return result


def error_status(code):
    if code in ('conflict','busy','training_busy','training_required','focus_unavailable','incompatible_reference','profile_changed'):
        return 409
    if code in ('source_unavailable','settings_unavailable','reference_pending','coaching_unavailable','history_unavailable'):
        return 503
    if code in ('forbidden','feature_disabled','foreign_origin'):
        return 403
    if code == 'reference_unavailable':return 410
    if code == 'rate_limited':return 429
    if code == 'unauthorized':return 401
    return 400
