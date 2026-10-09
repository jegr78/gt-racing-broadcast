#!/usr/bin/env python3
"""Real-packet CI fixtures: genuine GT7 "A" and extended "~" packets captured from a PS5 session with
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


# Extended '~' packets (344 bytes), captured 2026-09-29 on the Nurburgring GP in a
# Gr.4 car with the relay requesting the extended format (#711): a hard left turn, a
# hard right turn, the traction control cutting a floored throttle, and threshold
# braking. They pin the extended offsets (steering, driver inputs, accelerations).
EXT_LEFT_HEX = (
    "ad9e34d2d4cd1a52a246770bfa3ec66a34045ba7603d5c41b57e0477ab6ff361b68a125b11"
    "11769b9358d38d43eed18b112586da4db22ec7331a1c63cc90a4760f710d8388797f33e1b4"
    "cdac60a9fe838f413eae70c5f4d69d9ff4c0722a7f4fc0052044fb5a82faa93bb94170d346"
    "1b895c2a13f7a701c3d76611ff1f14c926077b875922d832bc12222943cb35953ed410c70b"
    "c5066a7c031119d6763f3f59857f9adfce1b7ffa139abfd69ebbe6267e7a382b3b8f581122"
    "b00d9f03a07fd93c55fcbe31290ff91fb58b29c392226c982bec8f4ea42ca33bfc60414e37"
    "bd26615239c0d6d2a11e1e6d71d1d27ceb9a99578a4e25bc1e20dccd20912beebbc4d4b097"
    "91094c7e93b8c87666d2cc419ecb2298db1f24fe1b8e9889e8669157ea48902ffc202cf709"
    "8d35e41d25aaa8c62d677ad09b83006f8345709a4a7d8a7bbed5a943a8f8d8cc121bc36e12"
    "80ecaf7b2c434abe77c46c"
)

EXT_RIGHT_HEX = (
    "4c3a91f77c064a27b852c2020f19addcc5b93b4c8e8b3a1263da80365ab1cbc32fb805b1bd"
    "5dc7c6fd5cf1c0dc569d1b862800b393ea8a235150110cfb1e113814534da00115ae6163ef"
    "0cc432b1fb5e6136f1ee109cf8b4aaebeaf63a3370b0ba99691e74d5ca7db94adc7218e402"
    "457ced05338429c1492d735a6be4570ebfc3edfb1e9394fec1f1c19471b53d3f18a14e907e"
    "aac63eae374b8296088a55ded3486ba1c0fcef0af83d3394dc9422606b138d7cc60a96784c"
    "d4c5d2ed742ea868628cf659693a7d95c6d1299c71a2dd0dbda4729f8dfc091a42119e26bc"
    "f3d266ab7e4f32f9645cf1cec38a98acf39faf56c2abb6e482be0a3226e8cac50b9b7d9ed4"
    "a493e2c658879d30bb4f65fcfedec8dcb9561426ca27b7655bbff5731bf2581bfce436b18c"
    "f81c8668f2b1301bcd70022afab4ccd25a03b36dde0c15e835ba1c10cedf80652397e6d77f"
    "a4c6a9aab39a42a50ba3c3"
)

EXT_TCS_HEX = (
    "96f391b5bb338adf717d22db97fcdcaf45ec9498c196ae0aa736e9be2555999a5a0757be81"
    "4beafb26f48fa6fca4b114fbe7e2f4f7f93fd19cca3223201360dc621f821ddf319a6a10f9"
    "dd52f5a3becbbd2e2edb4697a80e6cd8a7a7a96ab211d37356be7f6064ce8ef1f3d8ebbe05"
    "b47af9aa72fd2e05b9230d41b63744309ea73c891b7a48a612e7e2f45e5a04c35d425ef5f4"
    "9de655d80506312d25510d1043196bd2c0e19b41a0e137788ec8da47b98f671b4f4d8eddf3"
    "45fc1e368e0e38ecc2a76093485a6104e97526fb9290ab9ddb9e8d82e1124aae70f77349f9"
    "bf70c206fef3902cc371dc01fe84a0e514d44af7f860f937f798e7b27dc4b23e81fd4cfcb9"
    "472173944a01729ed11f68acc6c05fbf765b7951416004d12b13b456d1bb20354c922721d4"
    "142b69f014ea64338a0845c547313141a428ef63c5b6272d06e03d10f5637c4ee4ce7fb517"
    "ac89013c93acf80cf50048"
)

EXT_BRAKE_HEX = (
    "8fd153a52a66a77ea71bc06f00689bab5911d94f936a2e424a5049951b1dc5d4722f5742a4"
    "5de40a7d7fdcf765d50c95a57b67a535339e196c1a916288f06e8eb6a9a4976ba6b80b2e28"
    "c4ff50a2680d61a92af646ce221a689d7175e6f39d9f4b436f26db246ec2b82f71743194c2"
    "72156cb7c5c132188777838fe96f2276e743aeb0efb8d439f81f53e7f90d9a28b3361627b5"
    "d605c49fb33560d09378514b5cb36c83572d3f5d5f4ba6f7030c4e85023d8971411810db30"
    "53e289f304966af94d2511785ab90070f5b93029b56ed982dab92fced418cb4c41da6b3b4f"
    "94076614e3b8802dfbe51924838128613be4c7ffee7828be7355032a5d4db596f69430c4fc"
    "b4e6c01b87fcc1b77bc6a3c8347cf1f53d6840abd643e6dc19f019250b04cc6190d17d5e64"
    "174368cbb6cf4720c2ce62b2a0adebede4b43abe60b9741cc6eaf970fc81ef6d022d54d50b"
    "2da0e8749e1835ab191310"
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


def _parse_ext(h):
    data = bytes.fromhex(h)
    assert len(data) == 344
    kind, plain = gc.decrypt_typed(data)
    assert kind == "~"                   # the console streamed the extended format
    return tm.parse_packet(plain)


def t_real_extended_steering_sign():
    """Positive steering is a LEFT turn: the car turning left swings the lateral
    acceleration (sway) negative, turning right swings it positive."""
    left, right = _parse_ext(EXT_LEFT_HEX), _parse_ext(EXT_RIGHT_HEX)
    assert left.steer_rad > 1.0 and right.steer_rad < -1.0
    assert left.sway < 0 < right.sway


def t_real_extended_driver_input_vs_applied():
    """0x13C/0x13D carry the driver's pedal input, 0x91/0x92 what the car applies:
    traction control cuts a floored throttle, brake assist tops up the brake."""
    tcs = _parse_ext(EXT_TCS_HEX)
    assert tcs.throttle_input == 255 and tcs.throttle < 100
    brk = _parse_ext(EXT_BRAKE_HEX)
    assert brk.brake_input > 200 and brk.brake == 255
    assert brk.surge < -10                  # hard deceleration


def t_real_extended_keeps_base_fields():
    p = _parse_ext(EXT_BRAKE_HEX)
    assert p.lap == 1 and p.on_track is True
    assert 40.0 < p.speed_mps < 60.0        # ~185 km/h
    assert all(0.0 < t < 200.0 for t in p.tyre_temp)


def t_real_extended_car_id():
    """Every packet of the session names the car: 365, the Alfa Romeo 155 (#713)."""
    for h in (EXT_LEFT_HEX, EXT_RIGHT_HEX, EXT_TCS_HEX, EXT_BRAKE_HEX):
        assert _parse_ext(h).car_id == 365


def t_real_base_packet_has_no_extended_fields():
    p = _parse(PKT_THROTTLE_HEX)
    assert p.steer_rad is None and p.throttle_input is None and p.sway is None


def t_fixture_gear_rpm_position():
    expect = {
        "PKT_THROTTLE_HEX": (5, 7796.0, (-627.39, 12.26, -229.95)),
        "PKT_BRAKE_HEX": (6, 6859.0, (-775.91, 17.54, -417.9)),
        "PKT_LAP_HEX": (5, 7932.0, (-655.33, 12.87, -245.04)),
        "EXT_TCS_HEX": (4, 11757.0, (-997.69, 144.8, 1802.85)),
    }
    for name, (gear, rpm, pos) in expect.items():
        p = tm.parse_packet(gc.decrypt_packet(bytes.fromhex(globals()[name])))
        assert p.gear == gear, (name, p.gear)
        assert abs(p.rpm - rpm) < 1.0, (name, p.rpm)
        got = (round(p.pos_x, 2), round(p.pos_y, 2), round(p.pos_z, 2))
        assert got == pos, (name, got)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
