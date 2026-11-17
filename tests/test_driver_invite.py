#!/usr/bin/env python3
"""The event invite link and the driver codes it issues (#1111).
Run: python3 tests/test_driver_invite.py"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import console_auth  # noqa: E402
import driver_invite as di  # noqa: E402


def t_invite_lifecycle_creates_regenerates_and_clears():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "driver-invite.json")
        assert di.current(path) is None, "no invite before the event starts"
        first = di.create(path, now=100.0)
        assert di.current(path) == first and first["created"] == 100.0 and len(first["id"]) >= 20
        second = di.create(path, now=200.0)
        assert second["id"] != first["id"] and di.current(path)["id"] == second["id"], \
            "regenerating replaces the invite"
        di.clear(path)
        assert di.current(path) is None and not os.path.exists(path)
        di.clear(path)  # clearing twice is harmless
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        assert di.current(path) is None, "a corrupt file is no invite"


def t_codes_carry_the_registration_and_die_with_their_invite():
    invite = di.create(os.path.join(tempfile.mkdtemp(), "i.json"))
    code = di.issue(invite, entry="OVO eSports #111", segment="Stint 2", name="Max")
    reg = di.verify(invite, code)
    assert reg["entry"] == "OVO eSports #111" and reg["segment"] == "Stint 2" and reg["name"] == "Max", reg
    assert len(reg["conn"]) >= 8, reg
    again = di.verify(invite, di.issue(invite, entry="OVO eSports #111", name="Max"))
    assert again["conn"] != reg["conn"] and again["segment"] is None, "every registration is its own connection"
    other = di.create(os.path.join(tempfile.mkdtemp(), "j.json"))
    assert di.verify(other, code) is None, "a regenerated invite revokes every code"
    head, _, sig = code.rpartition(".")
    assert di.verify(invite, head + "." + ("A" if sig[0] != "A" else "B") + sig[1:]) is None
    for bad in (None, "", "D1:", "D1:abc", "x" * 5000, code.replace("D1:", "D2:")):
        assert di.verify(invite, bad) is None, bad
    assert di.verify(None, code) is None, "no invite, no code"


def t_codes_never_pass_as_console_tokens():
    invite = di.create(os.path.join(tempfile.mkdtemp(), "i.json"))
    code = di.issue(invite, entry="E", name="N")
    assert code.startswith("D1:") and code.count(".") == 1, code
    assert console_auth.verify_token(invite["key"], code) is None and \
        console_auth.verify_token(invite["id"], code) is None, \
        "a driver code must never authenticate a console page"


def t_the_link_alone_cannot_mint_codes():
    invite = di.create(os.path.join(tempfile.mkdtemp(), "i.json"))
    assert invite["key"] != invite["id"]
    payload = di._b64(b'{"e":"Any Team","s":null,"n":"Max","c":"abcdefghijkl"}')
    forged = di.PREFIX + payload + "." + di._sign(invite["id"], payload)
    assert di.verify(invite, forged) is None, "a code signed with the public link id must not pass"
    signed_bad = di._b64(b'{"e":"E","s":null,"n":"Max; rm -rf ~","c":"abcdefghijkl"}')
    assert di.verify(invite, di.PREFIX + signed_bad + "." + di._sign(invite["key"], signed_bad)) is None, \
        "verify re-checks the fields even of a signed code"
    with open(os.path.join(tempfile.mkdtemp(), "old.json"), "w", encoding="utf-8") as fh:
        fh.write('{"id": "' + "x" * 24 + '", "created": 1}')
    assert di.current(fh.name) is None, "an invite file without a signing key is no invite"


def t_the_cache_follows_create_and_clear():
    path = os.path.join(tempfile.mkdtemp(), "i.json")
    cache = di.Cache(path)
    assert cache.get() is None
    first = di.create(path)
    assert cache.get() == first
    second = di.create(path)
    assert cache.get()["id"] == second["id"], "a regenerated invite replaces the cached one"
    di.clear(path)
    assert cache.get() is None, "event stop ends the cached invite at once"


def t_names_and_entries_are_bounded():
    invite = di.create(os.path.join(tempfile.mkdtemp(), "i.json"))
    for bad in ("", "x" * 33, "Car #7", "a;rm -rf", "Jörg"):
        try:
            di.issue(invite, entry="E", name=bad)
        except ValueError as exc:
            assert "name" in str(exc), exc
        else:
            raise AssertionError(f"{bad!r} must be refused: it lands in a shell command")
    for bad in ("", "x" * 121):
        try:
            di.issue(invite, entry=bad, name="N")
        except ValueError:
            pass  # expected
        else:
            raise AssertionError(f"entry {bad!r} must be refused")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
