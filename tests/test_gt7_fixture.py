#!/usr/bin/env python3
"""Real-packet CI fixtures: genuine GT7 "A" packets captured from a PS5 session with
`tools/gt7-telemetry-probe.py --capture`. Decrypting and parsing them validates the
field offsets against reality rather than just the internal wiring, across three
states: full throttle with no lap yet, hard braking, and a completed lap. A GT7
packet-layout change fails these loudly.
Run: python3 tests/test_gt7_fixture.py

The packets carry game telemetry only, no PII and no secrets, and the Salsa20 key is
a public constant.
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


gc = _load("gt7_crypto", ("src", "scripts", "gt7_crypto.py"))
tm = _load("gt7_telemetry", ("src", "scripts", "gt7_telemetry.py"))

# Full throttle, early in the run: no lap time set yet (-1 sentinel), nearly full tank.
PKT_THROTTLE_HEX = (
    "26ac820369a6f362f5b0c1ed7fb5afce411fb9f3666dc88f2a6392586944842398639ed362"
    "0c295a13d7da0b7891f47ad69f85bc8331185f9b4c156c3aaf0df5714b28abb63f9d1c1197"
    "0513fcb5f10d1cd2a91d41773d01e7dfdec4ca68379acdf0bcda48effafe33e18891342778"
    "bcef3cc8a40011de1bc4ff0a49393ee2ba2e1dde03529ede7253d72c9b1fdd00f51c4dd3bd"
    "3c624c4f3176214c044515a58b8a7bda37566e9ae0e2e89f7bf9bef1791c4e9abdb3be4e8b"
    "d823996e67af0ff91bc7e8df23b19d2eb63b849a78f2d07d96399b8582a926e130ff370a64"
    "12f39a48f539d708c478d7043f30999fb901192b251558d2a612203cba8283627f907a2a71"
    "1ef870b387617256ced231440f5b292407b918d3b2a5c5bd53f3fb13c57792552b17988fd1"
)
# Hard braking: brake == 255 with throttle == 0, so the two adjacent bytes are read
# distinctly and not swapped.
PKT_BRAKE_HEX = (
    "2c51d48c3dff46c5b99df06916e0c91d518df9561df81e23bebad8ded28b01d9c095d35198"
    "d4617b431eb915c2a00555eaeb58b8aaa481bfce236106ac06b367adb94fd8ddeeea07f816"
    "5907ef06a065ba0b935df83ab23b002ee066c4c31c307d236a79465931de967d8a57568fa6"
    "18d16b02ab40d8e25c8f1f3be5f25e0a49ce337891428aae1e78b9b8a0359262e3db092eda"
    "42a8f95a2514c3b5594f51c743744f6fc2f5b4960165b8066a5e981da0875cc423a0626128"
    "916828ab06920ee13efa0334af722b788439e0316855d04fbed62a5232623421dc5a95ff0c"
    "2ec3daa630a2744060e8cd76a2a2791c2476402c9afb9d50624ca7cb308b6315e3f2c230b2"
    "842092f6c03b8a318e831022e3e2996ff8f273b01fa3cd52a88df01cac7620dafb35e1172a"
)
# A completed lap: best/last lap time populated (real milliseconds).
PKT_LAP_HEX = (
    "da00bbb9eefbf91cbdf7b9efa02b7f46cea79889c601b18f5de482ac4a4df3e267ec5b912c"
    "abee048a2233887e49b794b29dfd198fb3f7a5eb7092146cbad92cf7c863dd1e583b5aa187"
    "55d1e3ed704b3197393f81df564d71e66381f940142d5b9cae931cfc159054f4b0eba26be2"
    "3ab289ec23530a58bb0299a2346693c2904806990adc306dcbe45207baecafaae38fe9cf3d"
    "1ad88aea223f9b7f8fc7d98fb69f5d2706d7b9c94355f4fd434b8a1f5ceabb8b34c8f29536"
    "10ae9a1d96150863db148d8e04cfbc387d6d8b1486e622ae1f593e2b4ec576cafb5651ca39"
    "4f66a8c7aaa4d8be36acbb4b3bd10f436b9a860245932597100c97a7dd3a2a5d642e46e03f"
    "947cf574da44aa86f17702fdce00f9a4605b706d88885c9c07e520fd385ba9f766aa2be276"
)


def _parse(h):
    data = bytes.fromhex(h)
    assert len(data) == 296
    plain = gc.decrypt_packet(data)
    assert plain is not None            # magic matches on a REAL packet
    return tm.parse_packet(plain)


def t_real_full_throttle_no_lap_yet():
    p = _parse(PKT_THROTTLE_HEX)
    assert p.throttle == 255 and p.brake == 0
    assert p.best_ms == -1 and p.last_ms == -1     # no lap set yet -> -1 sentinel
    assert abs(p.fuel_capacity - 100.0) < 0.5 and p.fuel_level > 0
    assert p.on_track is True and p.paused is False
    assert all(0.0 < t < 200.0 for t in p.tyre_temp)


def t_real_hard_braking():
    p = _parse(PKT_BRAKE_HEX)
    assert p.brake == 255 and p.throttle == 0      # brake/throttle offsets distinct
    assert p.brake > 0
    assert p.on_track is True


def t_real_completed_lap():
    p = _parse(PKT_LAP_HEX)
    assert p.best_ms == 109724 and p.best_ms > 0       # real lap time in ms
    assert p.last_ms > 0
    assert p.lap == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
