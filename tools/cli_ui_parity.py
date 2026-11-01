"""Inventory public CLI actions and verify their audited Control Center bindings."""
import ast
import inspect
import re
import textwrap
from pathlib import Path


def documented_actions(usage,services):
    lines=[line.strip()[9:].split('#',1)[0].strip() for line in usage.splitlines()[1:]
           if line.strip().startswith('racecast ')]
    roots={line.split()[0] for line in lines if re.match(r'^[a-z]',line)}
    actions=set()
    for raw in lines:
        if raw.startswith('--'):continue
        variants=([raw.replace('<svc>',service) for service in (*services,'obs','tailscale')]
                  if raw.startswith('<svc>') else [raw])
        for raw_line in variants:
            optional=re.search(r' \[([a-z][a-z-]*)\]',raw_line)
            optional_lines=([raw_line] if not optional else
                [raw_line.replace(optional[0],' '+value,1) for value in ('',optional[1])])
            for line in optional_lines:
                line=re.sub(r'<[^>]+>','@',line)
                line=re.sub(r'\[[^]]*\]','',line).replace('{','').replace('}','')
                expanded=[line]
                for term in re.findall(r'[a-z][a-z-]*(?:\|[a-z][a-z-]*)+',line):
                    expanded=[text.replace(term,value,1) for text in expanded for value in term.split('|')]
                for text in expanded:
                    prefix=()
                    for index,part in enumerate(re.split(r'\s+\|\s+',text)):
                        words=[]
                        for word in part.split():
                            if not re.fullmatch(r'[a-z][a-z0-9-]*',word):break
                            words.append(word)
                        if not words:continue
                        if index==0 or len(words)>=2 and words[0] in roots:
                            path=tuple(words);prefix=path[:-1]
                        else:path=prefix+tuple(words)
                        actions.add(path)
    return actions


def positional_choices(function):
    """Read enum guards for the first positional argument, without executing it."""
    tree=ast.parse(textwrap.dedent(inspect.getsource(function)));names={'verb'};choices=set()
    for node in ast.walk(tree):
        if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                and node.func.attr=='add_argument' and node.args and isinstance(node.args[0],ast.Constant)
                and node.args[0].value in ('action','verb','command')):
            for keyword in node.keywords:
                if keyword.arg=='choices' and isinstance(keyword.value,(ast.Tuple,ast.List)):
                    choices.update(item.value for item in keyword.value.elts
                                   if isinstance(item,ast.Constant) and isinstance(item.value,str))
        if not isinstance(node,ast.Compare):continue
        left=node.left
        positional=(isinstance(left,ast.Name) and left.id in names or
            isinstance(left,ast.Subscript) and isinstance(left.value,ast.Name)
            and left.value.id in ('rest','args') and isinstance(left.slice,ast.Constant) and left.slice.value==0)
        if not positional:continue
        for right in node.comparators:
            values=right.elts if isinstance(right,(ast.Tuple,ast.List,ast.Set)) else [right]
            choices.update(value.value for value in values if isinstance(value,ast.Constant)
                           and isinstance(value.value,str) and not value.value.startswith('-'))
    return choices


def inventory(cli):
    paths=documented_actions(cli.USAGE,cli.SERVICES)
    for service in cli.SERVICES:
        paths.update((service,verb) for verb in cli.SERVICE_VERBS+cli.EXTRA_VERBS.get(service,()))
    for name,verbs in vars(cli).items():
        if not name.endswith('_VERBS') or not isinstance(verbs,tuple) or name=='SERVICE_VERBS':continue
        group='gt7-data' if name=='GT7DATA_VERBS' else name[:-6].lower().replace('_','-')
        if group=='app':
            paths.update((group,verb,app) for verb in verbs for app in cli.APP_CONTROLLED)
        else:
            for verb in verbs:
                if not any(path[:2]==(group,verb) and len(path)>2 for path in paths):paths.add((group,verb))
    paths.update(('profile',verb) for verb in cli.pa.PROFILE_VERBS)
    for verb,function in (('record',cli.telemetry_record_cmd),('replay',cli.telemetry_replay_cmd)):
        children=positional_choices(function)
        if not children:raise ValueError('Unrecognized telemetry '+verb+' CLI grammar')
        paths.discard(('telemetry',verb))
        paths.update(('telemetry',verb,child) for child in children)
    paths.discard(('console','token'))
    paths.update(('console','token',verb) for verb in positional_choices(cli._console_token))
    paths.update(('funnel',verb) for verb in positional_choices(cli.funnel_cmd))
    # A new root remains detectable even if its author forgets to update help.
    roots=set(cli.ONESHOTS)
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(cli.route)))):
        if not isinstance(node,ast.Compare):continue
        left=node.left
        command=(isinstance(left,ast.Name) and left.id=='cmd' or
            isinstance(left,ast.Subscript) and isinstance(left.value,ast.Name)
            and left.value.id=='argv' and isinstance(left.slice,ast.Constant) and left.slice.value==0)
        if not command:continue
        for right in node.comparators:
            values=right.elts if isinstance(right,(ast.Tuple,ast.List,ast.Set)) else [right]
            roots.update(value.value for value in values if isinstance(value,ast.Constant)
                         and isinstance(value.value,str) and not value.value.startswith('-'))
    for root in roots:
        if not any(path[0]==root for path in paths):paths.add((root,))
    return paths


def missing_bindings(cli,ops,bindings,root):
    root=Path(root)
    surfaces=list((root/'src/ui').glob('*.html'))+list((root/'src/ui').glob('*.js'))
    for directory in ('driver-radio','console'):
        radio=root/'src'/directory
        if radio.is_dir():surfaces+=list(radio.glob('*.js'))
    ui='\n'.join(path.read_text(encoding='utf-8') for path in surfaces)
    def referenced(name):return name in ops and bool(re.search(r"['\"]"+re.escape(name)+r"['\"]",ui))
    missing=[]
    for path in sorted(inventory(cli)):
        direct=[name for name,argv in ops.items() if tuple(arg for arg in argv if not arg.startswith('-'))==path]
        if any(referenced(name) for name in direct):continue
        binding=bindings.get(' '.join(path),{})
        if binding.get('op') and referenced(binding['op']):continue
        surface=binding.get('surface','');handler=binding.get('handler','')
        if surface.startswith(('ui/','driver-radio/')) and '..' not in Path(surface).parts:
            file=root/'src'/surface
            text=file.read_text(encoding='utf-8') if file.is_file() else ''
            declared=bool(re.search(r'\bfunction\s+'+re.escape(handler)+r'\s*\(',text)) if handler else False
            used=len(re.findall(r'\b'+re.escape(handler)+r'\b',ui))>1 if handler else False
            action=binding.get('action')
            if declared and used and (not action or re.search(r"interactiveAction\(['\"]"+re.escape(action)+r"['\"]\)",text)):
                continue
        missing.append(' '.join(path))
    return missing
