#!/usr/bin/env python3
"""Stdlib unit checks for GT7 Salsa20 decryption. Run: python3 tests/test_gt7_crypto.py"""
import importlib.util, os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


gt7 = _load("gt7_crypto", ("src", "scripts", "gt7_crypto.py"))


def t_salsa20_test_vector():
    # ECRYPT Salsa20/20 test vector (256-bit key, set 1 vector 0):
    # key = 0x80 followed by 31 zero bytes, IV = 8 zero bytes -> first 8 bytes of keystream.
    key = bytes([0x80] + [0] * 31)
    nonce = bytes(8)
    ks = gt7.salsa20_xor(key, nonce, bytes(64))   # XOR against zeros = raw keystream
    assert ks[:8] == bytes.fromhex("E3BE8FDD8BECA2E3"), ks[:8].hex()


def t_salsa20_is_symmetric():
    key = bytes(range(32)); nonce = bytes(range(8)); msg = b"hello gt7 telemetry" * 5
    ct = gt7.salsa20_xor(key, nonce, msg)
    assert ct != msg
    assert gt7.salsa20_xor(key, nonce, ct) == msg


def _encrypted(iv1, plaintext):
    """Craft a GT7-style encrypted packet: the IV travels in clear at 0x40."""
    nonce = struct.pack("<II", iv1 ^ 0xDEADBEAF, iv1)
    ct = bytearray(gt7.salsa20_xor(gt7.KEY, nonce, plaintext))
    ct[0x40:0x44] = struct.pack("<I", iv1)
    return bytes(ct)


def t_decrypt_packet_ok():
    plain = bytearray(0x128)
    struct.pack_into("<I", plain, 0x00, 0x47375330)   # magic
    struct.pack_into("<f", plain, 0x4C, 55.0)         # speed m/s marker
    ct = _encrypted(0x12345678, bytes(plain))
    out = gt7.decrypt_packet(ct)
    assert out is not None
    assert struct.unpack_from("<I", out, 0x00)[0] == 0x47375330
    assert abs(struct.unpack_from("<f", out, 0x4C)[0] - 55.0) < 1e-3


def t_decrypt_packet_rejects_garbage():
    assert gt7.decrypt_packet(bytes(0x128)) is None        # wrong magic
    assert gt7.decrypt_packet(b"short") is None            # too short


def t_decrypt_packet_rejects_short_valid_magic():
    """A short packet that decrypts to the correct magic must be dropped BEFORE the
    parser over-reads its fixed offsets (up to 0x92); the key is fixed and public, so
    a LAN host can forge one. Length below MIN_PACKET_LEN returns None. (#324)"""
    plain = bytearray(0x80)                                # 128 B: below MIN_PACKET_LEN
    struct.pack_into("<I", plain, 0x00, 0x47375330)       # valid magic
    ct = _encrypted(0x0BADF00D, bytes(plain))
    assert len(ct) < gt7.MIN_PACKET_LEN
    assert gt7.decrypt_packet(ct) is None                  # rejected on length, never reaches the parser


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
