#!/usr/bin/env python3
"""New public CLI actions must have a reachable Control Center binding."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'src/ui')]
import cli_ui_parity as parity
import ui_ops
import test_racecast as cli


def bindings():
    return json.loads((ROOT/'tools/cli-ui-bindings.json').read_text(encoding='utf-8'))


def missing(module,ops=None):
    return parity.missing_bindings(module,ui_ops.OPS if ops is None else ops,bindings(),ROOT)


def t_every_public_cli_action_has_a_control_center_binding():
    gaps=missing(cli.m)
    assert not gaps,'CLI actions without Control Center bindings: '+', '.join(gaps)


def t_new_service_verb_is_rejected_without_a_ui_binding():
    module=SimpleNamespace(**vars(cli.m))
    module.SERVICE_VERBS+=('new-unmapped-verb',)
    assert missing(module)==['companion new-unmapped-verb','relay new-unmapped-verb','streams new-unmapped-verb']


def t_new_undocumented_root_is_rejected_even_with_a_backend_op_but_no_control():
    module=SimpleNamespace(**vars(cli.m))
    def new_route(argv):
        cmd=argv[0]
        if cmd=='new-ui-gap':return {'kind':'new-ui-gap'}
        raise ValueError('Unknown command')
    module.route=new_route
    assert missing(module)==['new-ui-gap']
    ops=dict(ui_ops.OPS,new_unreachable_operation=['new-ui-gap'])
    assert missing(module,ops)==['new-ui-gap'],'registry presence alone is not a reachable UI action'


def t_new_nested_record_action_is_rejected_without_a_control():
    module=SimpleNamespace(**vars(cli.m))
    def record(rest):
        verb=rest[0] if rest else None
        if verb not in ('start','stop','status','new-record-action'):raise ValueError('Unsupported record action')
    module.telemetry_record_cmd=record
    assert missing(module)==['telemetry record new-record-action']


def t_documented_aliases_and_optional_words_keep_their_own_prefixes():
    usage='''Operator CLI
racecast chat clear | pull <ip> | import <file> | export [--out PATH]
racecast preflight | speedtest | cookies [twitch] [browser]
racecast obs collection [set]
racecast app launch|quit obs|discord
'''
    paths=parity.documented_actions(usage,())
    assert ('chat','export') in paths and ('chat','import') in paths and ('export',) not in paths
    assert ('cookies','twitch') in paths and ('preflight','twitch') not in paths
    assert ('obs','collection') in paths and ('obs','collection','set') in paths
    assert all(('app',verb,app) in paths for verb in ('launch','quit') for app in ('obs','discord'))


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)
