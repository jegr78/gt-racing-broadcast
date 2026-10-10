#!/usr/bin/env python3
"""Machine agent settings and CLI boundaries without provider credentials."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import ai_agents as a


def config(provider='codex', **kw):
    return dict(id='coach', name='Coach', provider=provider, model='manual-model', **kw)


def failure(fn, code):
    try:
        fn()
    except a.AgentError as e:
        assert e.code == code, (e.code, code)
    else:
        raise AssertionError('expected ' + code)


class Fake:
    def __init__(self, provider='codex', auth=None, help_text=None):
        self.provider = provider
        self.auth = auth
        self.help = help_text or ' '.join(a.REQUIRED_FLAGS[provider])
        self.calls = []

    def __call__(self, argv, **kw):
        assert kw['errors'] == 'replace' and kw['timeout'] == 10
        assert kw['stdin'] == subprocess.DEVNULL
        assert kw.get('shell', False) is False
        self.calls.append(argv)
        if '--version' in argv:
            out = 'codex-cli 0.162.1' if self.provider == 'codex' else '2.1.296 (Claude Code)'
        elif '--help' in argv:
            out = self.help
        elif self.provider == 'codex':
            out = self.auth or 'Logged in using ChatGPT'
        else:
            out = self.auth or json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty'})
        return subprocess.CompletedProcess(argv, 0, out, '')


def t_machine_settings():
    with tempfile.TemporaryDirectory() as d:
        store = a.Settings(d)
        assert store.read()['enabled'] is False
        doc = dict(enabled=True, agents=[config(), dict(config(), id='second', name='Second')],
                   last=dict(agent='coach', model='exact', template='consistency'))
        store.save(doc)
        assert store.read()['last'] == doc['last']
        assert len(store.read()['agents']) == 2
        for bad in (dict(doc, tokens='secret'), dict(doc, enabled='true'),
                    dict(doc, agents=[config(timeout=0)]), dict(doc, agents=[config(), config()]),
                    dict(doc, agents=[config(executable='x\n--yolo')]),
                    dict(doc, agents=[config(api_key='secret')])):
            failure(lambda bad=bad: store.save(bad), 'invalid_settings')
        assert store.read()['enabled'] is True
        Path(store.path).write_text('{bad', encoding='utf-8')
        failure(store.read, 'invalid_settings')


def t_probes():
    for provider in ('codex', 'claude'):
        cli = a.Adapter(config(provider), which=lambda name: '/fake/' + name)
        fake = Fake(provider)
        p = cli.probe(run=fake, env={})
        assert p['auth'] == 'subscription' and p['status'] == 'ready'
        assert p['models'] == [] and p['manual_models'] is True
        assert all('-p' not in argv and 'exec' not in argv for argv in fake.calls if '--help' not in argv)
        conflict = cli.probe(run=Fake(provider), env={'OPENAI_API_KEY': 'secret'} if provider == 'codex' else {'ANTHROPIC_API_KEY': 'secret'})
        assert conflict['status'] == 'auth_conflict' and 'secret' not in json.dumps(conflict)
        assert cli.probe(run=Fake(provider, help_text='old'), env={})['status'] == 'unsupported_version'
    assert a.Adapter(config(), which=lambda _: None).probe(env={})['status'] == 'missing_executable'
    assert a.Adapter(config(), which=lambda _: sys.executable).probe(run=Fake(auth='Logged in using an API key'), env={})['status'] == 'auth_conflict'
    assert a.Adapter(config(), which=lambda _: sys.executable).probe(run=Fake(auth='Not logged in'), env={})['status'] == 'logged_out'
    assert a.Adapter(config(), which=lambda _: sys.executable).probe(run=Fake(auth='new unknown output'), env={})['status'] == 'auth_unknown'
    assert a.Adapter(config('claude'), which=lambda _: sys.executable).probe(run=Fake('claude', auth='{}'), env={})['status'] == 'auth_unknown'


def t_invocation_and_guards():
    with tempfile.TemporaryDirectory() as d:
        pkg = Path(d) / 'package'; out = Path(d) / 'output'
        pkg.mkdir(); out.mkdir()
        schema = pkg / 'schema.json'; schema.write_text('{}')
        for provider in ('codex', 'claude'):
            cli = a.Adapter(config(provider), which=lambda _: '/fake/cli')
            p = cli.probe(run=Fake(provider), env={})
            inv = cli.invocation(pkg, out, schema, p)
            assert 'manual-model' in inv.argv
            assert inv.cwd == str(pkg.resolve()) and inv.output == str((out / 'result.json').resolve())
            assert not any('bypass' in x or '--bare' == x for x in inv.argv)
            if provider == 'codex':
                assert any('filesystem={":root"="deny"' in x for x in inv.argv)
                assert 'skills.include_instructions=false' in inv.argv
            else:
                assert '--restricted' in inv.argv and '--safe-mode' in inv.argv
                assert inv.argv[inv.argv.index('--tools') + 1] == 'Read'
            failure(lambda cli=cli, p=p: cli.invocation(pkg, out, schema, dict(p, status='logged_out')), 'logged_out')
            failure(lambda cli=cli, p=p: cli.invocation(pkg, pkg, schema, p), 'invalid_paths')
            failure(lambda cli=cli, p=p: cli.invocation(pkg, out, schema, dict(p, models=['different'])), 'unavailable_model')
            failure(lambda cli=cli, p=p: cli.invocation(pkg, out, schema, dict(p, capabilities=[])), 'unsupported_version')
            personal = a.Adapter(config(provider, mode='personal', personal={'mcp': True}), which=lambda _: sys.executable)
            failure(lambda personal=personal, p=p: personal.invocation(pkg, out, schema, p), 'isolation_unavailable')


def t_output():
    codex = a.Adapter(config(), which=lambda _: sys.executable)
    result = codex.extract('{"ok":true}', [{'type':'turn.completed'}])
    assert result['result'] == {'ok': True} and result['actual_model'] is None
    failure(lambda: codex.extract('not json', []), 'invalid_output')
    failure(lambda: codex.extract('{"value":NaN}', []), 'invalid_output')
    failure(lambda: codex.extract('[]', []), 'invalid_output')
    failure(lambda: codex.extract('{}', [{'type':'turn.failed'}]), 'provider_failed')
    claude = a.Adapter(config('claude'), which=lambda _: sys.executable)
    result = claude.extract(json.dumps({'type':'result', 'is_error':False, 'structured_output':{'ok':True},
                                       'modelUsage':{'actual':{}}}), [])
    assert result['actual_model'] == 'actual'
    failure(lambda: claude.extract('{"is_error":true}', []), 'provider_failed')
    failure(lambda: claude.extract('{"permission_denials":[{}]}', []), 'approval_required')
    failure(lambda: claude.extract('{"result":"plain"}', []), 'invalid_output')


def t_real_fake_cli_process_and_model_discovery():
    with tempfile.TemporaryDirectory() as d:
        script = Path(d) / 'fake.py'
        script.write_text("""import json,sys
from pathlib import Path
args=sys.argv[1:]
if '--version' in args: print('codex-cli 0.162.1')
elif '--help' in args: print(' '.join(sys.argv[1:]))
elif 'login' in args: print('Logged in using ChatGPT')
elif 'models' in args: print(json.dumps({'models':[{'slug':'specific'}]}))
else:
    prompt=sys.stdin.read()
    assert prompt=='synthetic package'
    result={'ok':True}
    if '--output-last-message' in args:
        Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(result))
        print(json.dumps({'type':'turn.completed'}))
    else: print(json.dumps({'is_error':False,'structured_output':result}))
""", encoding='utf-8')
        def run(argv, **kw):
            if '--help' in argv:
                argv = argv + list(a.REQUIRED_FLAGS['codex'])
            return subprocess.run([sys.executable, str(script)] + argv[1:], **kw)
        cli = a.Adapter(config(), which=lambda _: sys.executable)
        assert cli.discover_models(run=run, env={}) == ['specific']
        assert a.Adapter(config('claude'), which=lambda _: sys.executable).discover_models(run=run, env={}) == []
        pkg = Path(d) / 'package'; out = Path(d) / 'output'
        pkg.mkdir(); out.mkdir(); schema = pkg / 'schema.json'; schema.write_text('{}')
        inv = cli.invocation(pkg, out, schema, cli.probe(run=run, env={}))
        completed = subprocess.run([sys.executable, str(script)] + inv.argv[1:],
                                   input='synthetic package', capture_output=True,
                                   text=True, encoding='utf-8', errors='replace', timeout=10)
        assert completed.returncode == 0
        assert cli.extract(Path(inv.output).read_text(), [json.loads(completed.stdout)])['result'] == {'ok':True}


if __name__ == '__main__':
    for name, fn in sorted(globals().copy().items()):
        if name.startswith('t_'):
            fn(); print('PASS', name)
