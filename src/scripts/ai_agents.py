"""Opt-in machine settings and subscription CLI adapters. No inference on probe.

Adapters return argument arrays; the job runner owns process lifetime and files.
Unsupported isolation is an actionable error, never a permission escalation.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SETTINGS_VERSION = 1
PROVIDERS = ('codex', 'claude')
TEMPLATES = ('driving-technique', 'consistency', 'session-overview')
REQUIRED_FLAGS = {
    'codex': ('--ignore-user-config', '--ignore-rules', '--strict-config', '--output-schema', '--json', '--ephemeral'),
    'claude': ('--restricted', '--safe-mode', '--json-schema', '--strict-mcp-config', '--permission-mode', '--tools'),
}
AUTH_ENV = {
    'codex': ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'CODEX_ACCESS_TOKEN'),
    'claude': ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
               'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY'),
}


class AgentError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _text(value, name, limit=200, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise AgentError('invalid_settings', 'Invalid ' + name)
    return value.strip()


def agent_config(doc):
    if not isinstance(doc, dict) or set(doc) - {'id', 'name', 'provider', 'executable', 'model', 'timeout', 'mode', 'personal'}:
        raise AgentError('invalid_settings', 'Unknown agent configuration fields')
    ident = _text(doc.get('id'), 'agent ID', 64)
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', ident):
        raise AgentError('invalid_settings', 'Agent ID must be a slug')
    provider = doc.get('provider')
    if provider not in PROVIDERS:
        raise AgentError('invalid_settings', 'Unsupported provider')
    timeout = doc.get('timeout', 600)
    if type(timeout) is not int or not 10 <= timeout <= 3600:
        raise AgentError('invalid_settings', 'Timeout must be 10..3600 seconds')
    mode = doc.get('mode', 'isolated')
    personal = doc.get('personal', {})
    if mode not in ('isolated', 'personal') or not isinstance(personal, dict) or set(personal) - {'instructions', 'skills', 'mcp'} or any(type(v) is not bool for v in personal.values()):
        raise AgentError('invalid_settings', 'Invalid personal configuration selection')
    personal = {k: personal.get(k, False) for k in ('instructions', 'skills', 'mcp')}
    if mode == 'isolated' and any(personal.values()):
        raise AgentError('invalid_settings', 'Personal extensions require personal mode')
    return dict(id=ident, name=_text(doc.get('name'), 'agent name'), provider=provider,
                executable=_text(doc.get('executable', ''), 'executable', 4096, empty=True),
                model=_text(doc.get('model', ''), 'model', empty=True), timeout=timeout,
                mode=mode, personal=personal)


def settings(doc):
    if not isinstance(doc, dict) or set(doc) - {'version', 'enabled', 'agents', 'last'} or doc.get('version', 1) != 1:
        raise AgentError('invalid_settings', 'Invalid AI settings schema')
    if type(doc.get('enabled', False)) is not bool or not isinstance(doc.get('agents', []), list) or len(doc.get('agents', [])) > 32:
        raise AgentError('invalid_settings', 'Invalid AI settings')
    agents = [agent_config(c) for c in doc.get('agents', [])]
    if len({c['id'] for c in agents}) != len(agents):
        raise AgentError('invalid_settings', 'Duplicate agent IDs')
    last = doc.get('last', {})
    if not isinstance(last, dict) or set(last) - {'agent', 'model', 'template'}:
        raise AgentError('invalid_settings', 'Invalid last selection')
    last = {k: _text(v, k, empty=True) for k, v in last.items()}
    if last.get('agent') and last['agent'] not in {c['id'] for c in agents}:
        raise AgentError('invalid_settings', 'Last agent no longer exists')
    if last.get('template') and last['template'] not in TEMPLATES:
        raise AgentError('invalid_settings', 'Invalid last template')
    return dict(version=SETTINGS_VERSION, enabled=doc.get('enabled', False), agents=agents, last=last)


class Settings:
    """Only machine runtime root, never profile.env or profile runtime."""
    def __init__(self, machine_runtime):
        self.path = str(Path(machine_runtime) / 'ai-agents.json')

    def read(self):
        if not os.path.exists(self.path):
            return settings({})
        try:
            with open(self.path, encoding='utf-8') as f:
                raw = f.read(131073)
            if len(raw) > 131072:
                raise ValueError('oversized')
            return settings(json.loads(raw))
        except (ValueError, OSError, RecursionError) as e:
            raise AgentError('invalid_settings', 'AI settings are unreadable; restore or correct the machine settings') from e

    def save(self, doc):
        validated = settings(doc)
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.ai-agents-', dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(validated, f, ensure_ascii=True, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return validated


@dataclass(frozen=True)
class Invocation:
    argv: list
    cwd: str
    output: str
    timeout: int
    provider: str


def _probe_command(argv, run, env):
    completed = run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, encoding='utf-8', errors='replace', timeout=10, env=env)
    return completed.returncode, (completed.stdout or completed.stderr).strip()


class Adapter:
    def __init__(self, config, which=shutil.which):
        self.config = agent_config(config)
        self.provider = self.config['provider']
        self.executable = which(self.config['executable'] or self.provider)

    def probe(self, run=subprocess.run, env=None):
        """Non-billable version/help/login only; never return raw account details."""
        env = dict(os.environ if env is None else env)
        out = dict(status='missing_executable', auth='unknown', version=None,
                   capabilities=[], models=[], manual_models=True,
                   guidance='Install the provider CLI externally and configure its executable.')
        if not self.executable:
            return out
        if any(env.get(k) for k in AUTH_ENV[self.provider]):
            return dict(out, status='auth_conflict', guidance='Remove API/gateway authentication overrides from the Racecast process environment; log in with the provider subscription CLI.')
        try:
            code, version = _probe_command([self.executable, '--version'], run, env)
            if code or not re.search(r'\d+\.\d+\.\d+', version):
                return dict(out, status='unsupported_version', guidance='Provider version could not be verified.')
            out['version'] = re.search(r'\d+\.\d+\.\d+', version).group()
            minimum = (0, 162, 1) if self.provider == 'codex' else (2, 1, 248)
            if tuple(int(n) for n in out['version'].split('.')) < minimum:
                return dict(out, status='unsupported_version', guidance='Update the CLI externally. This version predates the verified isolation configuration.')
            help_args = [self.executable, 'exec', '--help'] if self.provider == 'codex' else [self.executable, '--help']
            code, help_text = _probe_command(help_args, run, env)
            out['capabilities'] = [flag for flag in REQUIRED_FLAGS[self.provider] if flag in help_text]
            if code or len(out['capabilities']) != len(REQUIRED_FLAGS[self.provider]):
                return dict(out, status='unsupported_version', guidance='Update the CLI externally. Required isolation/output flags are missing.')
            cmd = ['login', 'status'] if self.provider == 'codex' else ['auth', 'status']
            code, auth = _probe_command([self.executable] + cmd, run, env)
            if self.provider == 'codex':
                if 'using ChatGPT' in auth and not code:
                    out['auth'] = 'subscription'
                elif 'API key' in auth:
                    out['auth'] = 'conflict'
                elif 'Not logged in' in auth or 'not logged in' in auth:
                    out['auth'] = 'logged_out'
            else:
                try:
                    doc = json.loads(auth)
                except ValueError:
                    doc = {}
                if isinstance(doc, dict):
                    if doc.get('loggedIn') is False:
                        out['auth'] = 'logged_out'
                    elif doc.get('loggedIn') is True and doc.get('authMethod') == 'claude.ai' and doc.get('apiProvider') == 'firstParty' and not code:
                        out['auth'] = 'subscription'
                    elif doc.get('loggedIn') is True:
                        out['auth'] = 'conflict'
            status = {'subscription': 'ready', 'conflict': 'auth_conflict', 'logged_out': 'logged_out'}.get(out['auth'], 'auth_unknown')
            return dict(out, status=status, guidance='' if status == 'ready' else 'Run the provider login/status command externally. A subscription login must be established before analysis; no API fallback is used.')
        except (OSError, subprocess.TimeoutExpired):
            return dict(out, status='auth_unknown', guidance='CLI check failed or timed out. Check the installation and login externally.')

    def discover_models(self, run=subprocess.run, env=None):
        """Codex catalogue suggestions, not a guarantee of entitlement or quota.

        Claude has no stable non-billable catalogue command. Manual names remain
        available for both providers. No fallback or inference is performed.
        """
        if self.provider != 'codex' or not self.executable:
            return []
        env = dict(os.environ if env is None else env)
        if self.probe(run=run, env=env)['status'] != 'ready':
            return []
        try:
            code, raw = _probe_command([self.executable, 'debug', 'models'], run, env)
            if code:
                return []
            doc = json.loads(raw)
            models = doc.get('models', []) if isinstance(doc, dict) else doc
            if not isinstance(models, list):
                return []
            return sorted({m['slug'] for m in models if isinstance(m, dict)
                           and isinstance(m.get('slug'), str) and len(m['slug']) <= 200})
        except (ValueError, OSError, subprocess.TimeoutExpired, RecursionError):
            return []  # Catalogue failure never starts an inference or changes the model.

    def invocation(self, package, output, schema, probe, model=None):
        """Fail closed; caller must re-probe immediately before spawning."""
        if probe.get('status') != 'ready' or probe.get('auth') != 'subscription':
            raise AgentError(probe.get('status', 'auth_unknown'), probe.get('guidance', 'Check subscription login'))
        if not self.executable or any(flag not in probe.get('capabilities', []) for flag in REQUIRED_FLAGS[self.provider]):
            raise AgentError('unsupported_version', 'Required CLI flags are unavailable')
        model = _text(self.config['model'] if model is None else model, 'model')
        if probe.get('models') and model not in probe['models']:
            raise AgentError('unavailable_model', 'Requested model is unavailable; choose another explicitly')
        package, output, schema = Path(package).resolve(), Path(output).resolve(), Path(schema).resolve()
        if not package.is_dir() or not output.is_dir() or package == output or package in output.parents or output in package.parents or schema.parent != package or not schema.is_file():
            raise AgentError('invalid_paths', 'Use separate package and output directories and a package-local schema')
        personal = self.config['personal']
        if personal['mcp']:
            raise AgentError('isolation_unavailable', 'Personal MCP servers can access files outside CLI sandboxing. Disable MCP for the background job; use the exported package manually if these servers are required.')
        result = output / 'result.json'
        if self.provider == 'codex':
            argv = [self.executable, 'exec', '--ignore-user-config', '--ignore-rules', '--strict-config',
                    '--skip-git-repo-check', '--ephemeral', '--json', '--color', 'never',
                    '--model', model, '--cd', str(package), '--output-schema', str(schema),
                    '--output-last-message', str(result)]
            overrides = ['model_provider="openai"', 'forced_login_method="chatgpt"',
                         'approval_policy="on-request"', 'default_permissions="racecast"',
                         'permissions.racecast={filesystem={":root"="deny",":minimal"="read",'
                         + json.dumps(str(package)) + '="read",' + json.dumps(str(output))
                         + '="write"},network={enabled=false}}', 'web_search="disabled"',
                         'features.hooks=false', 'features.plugins=false', 'features.apps=false',
                         'features.multi_agent=false', 'shell_environment_policy.inherit="none"']
            if not personal['instructions']:
                overrides.append('project_doc_max_bytes=0')
            if not personal['skills']:
                overrides.extend(['features.skip_host_skill_discovery=true', 'skills.include_instructions=false'])
            for value in overrides:
                argv += ['-c', value]
            argv.append('-')
        else:
            if any(personal.values()):
                raise AgentError('isolation_unavailable', 'Selective personal Claude configuration is not verified with restricted execution. Disable personal extensions or run the exported package manually.')
            argv = [self.executable, '--print', '--restricted', '--safe-mode', '--no-session-persistence',
                    '--no-chrome', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                    '--permission-mode', 'dontAsk', '--tools', 'Read', '--allowedTools', 'Read',
                    '--model', model, '--output-format', 'json', '--json-schema', schema.read_text(encoding='utf-8'),
                    '--settings', '{"disableAllHooks":true}']
        return Invocation(argv, str(package), str(result), self.config['timeout'], self.provider)

    def extract(self, raw, events):
        """Return unvalidated structured data, never a normal telemetry report."""
        actual = None
        if any(e.get('type') in ('error', 'turn.failed') for e in events if isinstance(e, dict)):
            raise AgentError('provider_failed', 'Provider reported a failed turn; inspect local diagnostics')
        try:
            doc = json.loads(raw)
        except (ValueError, RecursionError) as e:
            raise AgentError('invalid_output', 'Provider output is not structured JSON') from e
        if not isinstance(doc, dict):
            raise AgentError('invalid_output', 'Provider result must be an object')
        if self.provider == 'claude':
            if doc.get('permission_denials'):
                raise AgentError('approval_required', 'Interactive permission required. Export the package and run the provider manually with scoped permissions.')
            if doc.get('is_error'):
                raise AgentError('provider_failed', 'Provider reported an error; inspect local diagnostics')
            usage = doc.get('modelUsage', {})
            if isinstance(usage, dict) and len(usage) == 1:
                actual = next(iter(usage))
            doc = doc.get('structured_output')
            if not isinstance(doc, dict):
                raise AgentError('invalid_output', 'Provider omitted structured output')
        return dict(result=doc, actual_model=actual)
