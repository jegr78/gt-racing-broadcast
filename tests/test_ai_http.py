#!/usr/bin/env python3
"""Analysis HTTP origin/admission errors must include their semantic identity."""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/ui'))
import test_ui_server as fixtures


def request(port,path,body=None,headers=None):
    req=urllib.request.Request('http://127.0.0.1:'+str(port)+path,
                               data=json.dumps(body).encode() if body is not None else None,
                               headers=dict({'Content-Type':'application/json'},**(headers or {})))
    try:
        with fixtures._urlopen(req) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:return e.code,json.load(e)


def t_route_and_foreign_origin():
    calls=[];ctx=fixtures._ctx()
    def callback(op,payload):
        calls.append((op,payload))
        if op=='start':return {'ok':False,'error':{'code':'busy','message':'Already running'}}
        return {'ok':True,'enabled':False}
    ctx['ai_request']=callback
    server,port=fixtures._serve(ctx)
    try:
        code,doc=request(port,'/api/ai/settings')
        assert code==200 and doc['enabled'] is False
        code,doc=request(port,'/api/ai/start',{'confirm_preview':'x'})
        assert code==409 and doc['error']['code']=='busy'
        before=len(calls)
        code,doc=request(port,'/api/ai/start',{},headers={'Origin':'https://foreign.example.test'})
        assert code==403 and doc['error']['code']=='foreign_origin'
        assert len(calls)==before
        code,doc=request(port,'/api/ai/job?id=abc',headers={'Host':'foreign.example.test'})
        assert code==403 and doc['error']['code']=='foreign_origin'
        assert len(calls)==before
        code,doc=request(port,'/api/ai/preview',[])
        assert code==400 and doc['error']['code']=='invalid_json'
    finally:server.shutdown();server.server_close()


if __name__=='__main__':
    t_route_and_foreign_origin();print('PASS t_route_and_foreign_origin')
