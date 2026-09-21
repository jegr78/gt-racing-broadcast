#!/usr/bin/env python3
"""Pure-Python Salsa20 decryption for the GT7 "Simulator Interface" UDP telemetry.

Vendored (no pip dependency): the toolkit ships as a single frozen binary and the
model is stdlib + external binaries only. Salsa20 is a stream cipher, so the same
XOR routine encrypts and decrypts. Reference: Nenkai/PDTools (MIT), the GT7
community field docs. See docs/superpowers/specs/2026-07-08-gt7-telemetry-pov-hud-design.md
"""
import struct

KEY = b"Simulator Interface Packet GT7 ver 0.0"[:32]
# Per-packet-version XOR constant for packet type 'A'.
IV_XOR_A = 0xDEADBEAF
# Decrypted magic (little-endian uint32 at offset 0), the "0S7G" bytes.
MAGIC = 0x47375330

_SIGMA = struct.unpack("<4I", b"expand 32-byte k")
_MASK = 0xFFFFFFFF


def _rotl(v, n):
    v &= _MASK
    return ((v << n) | (v >> (32 - n))) & _MASK


def _quarter(x, a, b, c, d):
    x[b] ^= _rotl(x[a] + x[d], 7)
    x[c] ^= _rotl(x[b] + x[a], 9)
    x[d] ^= _rotl(x[c] + x[b], 13)
    x[a] ^= _rotl(x[d] + x[c], 18)


def _block(key, nonce8, counter8):
    k = struct.unpack("<8I", key)
    n = struct.unpack("<2I", nonce8)
    b = struct.unpack("<2I", counter8)
    state = [
        _SIGMA[0], k[0], k[1], k[2],
        k[3], _SIGMA[1], n[0], n[1],
        b[0], b[1], _SIGMA[2], k[4],
        k[5], k[6], k[7], _SIGMA[3],
    ]
    x = list(state)
    for _ in range(10):                      # 20 rounds = 10 double-rounds
        _quarter(x, 0, 4, 8, 12)             # columns
        _quarter(x, 5, 9, 13, 1)
        _quarter(x, 10, 14, 2, 6)
        _quarter(x, 15, 3, 7, 11)
        _quarter(x, 0, 1, 2, 3)              # rows
        _quarter(x, 5, 6, 7, 4)
        _quarter(x, 10, 11, 8, 9)
        _quarter(x, 15, 12, 13, 14)
    out = [(x[i] + state[i]) & _MASK for i in range(16)]
    return struct.pack("<16I", *out)


def salsa20_xor(key, nonce8, data):
    """XOR `data` with the Salsa20/20 keystream (256-bit key, 64-bit nonce, counter 0)."""
    out = bytearray(len(data))
    for off in range(0, len(data), 64):
        ks = _block(key, nonce8, struct.pack("<Q", off // 64))
        chunk = data[off:off + 64]
        for j, byte in enumerate(chunk):
            out[off + j] = byte ^ ks[j]
    return bytes(out)


# Minimum accepted packet length. The nonce is derived from offset 0x40, but the
# parser (gt7_telemetry.parse_packet) reads fixed fields up to offset 0x92 (brake).
# Requiring the full field span drops a short datagram carrying a valid magic, which
# any LAN host can forge because the key is the fixed public string, instead of
# over-reading and raising struct.error/IndexError deep in the parser.
MIN_PACKET_LEN = 0x94


def decrypt_packet(data):
    """Decrypt a received GT7 packet. Returns the plaintext, or None if the packet
    is too short or the magic does not match (foreign/corrupt datagram)."""
    if len(data) < MIN_PACKET_LEN:
        return None
    iv1 = struct.unpack_from("<I", data, 0x40)[0]
    nonce = struct.pack("<II", iv1 ^ IV_XOR_A, iv1)
    plain = salsa20_xor(KEY, nonce, data)
    if struct.unpack_from("<I", plain, 0x00)[0] != MAGIC:
        return None
    return plain
