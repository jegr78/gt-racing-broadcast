"""Private, one-request speech worker. Credentials arrive only through bounded stdin."""

import base64
import json
import sys
import threading

import http_util
import logsetup
from driver_radio_providers import BUDGET, GoogleSpeech, ProviderError

INPUT_MAX = 32768


def main(stdin=None,stdout=None,speech_factory=None):
    logsetup.harden_stdio()
    stream=stdin if stdin is not None else sys.stdin.buffer
    output=stdout if stdout is not None else sys.stdout
    try:
        raw=stream.read(INPUT_MAX+1)
        if len(raw)>INPUT_MAX:raise ProviderError('invalid_request')
        request=json.loads(raw)
        if (not isinstance(request,dict) or set(request)!={'key','text','budget','allow_cloud'}
                or request['allow_cloud'] is not True
                or not isinstance(request['key'],str) or not 1<=len(request['key'])<=256
                or any(ord(c)<32 for c in request['key'])
                or not isinstance(request['text'],str) or not 1<=len(request['text'])<=1200
                or type(request['budget']) not in (int,float) or not 0<request['budget']<=BUDGET):
            raise ProviderError('invalid_request')
        speech=(speech_factory or GoogleSpeech)(key=lambda:request['key'],open_url=http_util.open_url)
        audio=speech.synthesize(request['text'],threading.Event(),request['budget'])
        result={'audioContent':base64.b64encode(audio).decode('ascii')}
    except ProviderError as exc:
        result={'error':exc.code}
    except (ValueError,TypeError,KeyError,RecursionError):
        result={'error':'invalid_request'}
    except Exception:
        result={'error':'speech_failed'}
    output.write(json.dumps(result,ensure_ascii=True)+'\n')
    output.flush()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
