#!/usr/bin/env python3
"""Unit checks for the Funnel-setup policy logic. Run: python3 tests/test_funnel_setup.py"""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# funnel_setup imports its sibling `http_util`, which is always on sys.path in
# production, so mirror that for the loader.
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fs = _load("funnel_setup", ("src", "scripts", "funnel_setup.py"))


def t_magicdns_enabled():
    assert fs.magicdns_enabled({"magicDNS": True}) is True
    assert fs.magicdns_enabled({"magicDNS": False}) is False
    assert fs.magicdns_enabled({}) is False
    assert fs.magicdns_enabled(None) is False


def t_acl_has_funnel():
    assert fs.acl_has_funnel({"nodeAttrs": [{"target": ["x"], "attr": ["funnel"]}]}) is True
    assert fs.acl_has_funnel({"nodeAttrs": [{"target": ["x"], "attr": ["nextdns"]}]}) is False
    assert fs.acl_has_funnel({"nodeAttrs": []}) is False
    assert fs.acl_has_funnel({}) is False


def t_add_funnel_nodeattr_appends_and_is_idempotent():
    acl = {"acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]}
    new, changed = fs.add_funnel_nodeattr(acl, target="autogroup:member")
    assert changed is True
    assert new["nodeAttrs"] == [{"target": ["autogroup:member"], "attr": ["funnel"]}]
    assert new["acls"] == acl["acls"]            # other keys preserved
    assert "nodeAttrs" not in acl                # input not mutated
    # second pass: no duplicate, no change
    again, changed2 = fs.add_funnel_nodeattr(new)
    assert changed2 is False
    assert len(again["nodeAttrs"]) == 1


def t_add_funnel_preserves_existing_nodeattrs():
    acl = {"nodeAttrs": [{"target": ["a"], "attr": ["nextdns"]}]}
    new, changed = fs.add_funnel_nodeattr(acl, target="tag:broadcast")
    assert changed is True
    assert len(new["nodeAttrs"]) == 2
    assert {"target": ["tag:broadcast"], "attr": ["funnel"]} in new["nodeAttrs"]


def t_setup_plan():
    assert fs.setup_plan({"magicDNS": True},
                         {"nodeAttrs": [{"attr": ["funnel"]}]}) == []
    plan = fs.setup_plan({"magicDNS": False}, {})
    assert "enable MagicDNS" in plan[0]
    assert any("funnel" in s for s in plan)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
