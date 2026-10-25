"""cairn.crypto -- zero-dependency cryptography (RFC 8032 / RFC 8439).

Everything here is pure Python stdlib (hashlib for the SHA-512/SHA-256
needs, integer arithmetic for the rest).  That is a deliberate
constraint: Cairn ships as a single-file-installable, offline-first tool
with no wheels to audit and no OpenSSL to configure -- and the code is
short enough to actually read (Ed25519 ~120 lines, ChaCha20-Poly1305
~90 lines).

  ed25519_keypair() / sign() / verify()     RFC 8032 (licenses, receipts)
  chacha20poly1305_encrypt() / decrypt()    RFC 8439 AEAD (encrypted segments)

The AEAD is what makes the enterprise edition honest: claims in a
"company" segment are ciphertext at rest, keyed by a key that only exists
on licensed devices, so scope stops being a read-time filter and becomes
a cryptographic boundary (docs/THEORY.md 6).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct

# --------------------------------------------------------------------- #
# Ed25519 (RFC 8032)
# --------------------------------------------------------------------- #

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _sha512(b: bytes) -> bytes:
    return hashlib.sha512(b).digest()


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x % 2 != 0:
        x = _P - x
    return x


_BY = 4 * pow(5, _P - 2, _P)
_BX = _xrecover(_BY)


def _pt_add(p1, p2):
    """Extended-coordinate addition on the twisted Edwards curve."""
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    dd = z1 * 2 * z2 % _P
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _pt_double(p1):
    """Doubling on the a=-1 twisted Edwards curve (ref10 formulation:
    X3=E*F, Y3=G*H, T3=E*H, Z3=F*G with D=-A)."""
    x1, y1, z1, _t1 = p1
    a = x1 * x1 % _P
    b = y1 * y1 % _P
    c = 2 * z1 * z1 % _P
    e = (x1 + y1) * (x1 + y1) % _P
    e = (e - a - b) % _P
    g = (b - a) % _P
    f = (g - c) % _P
    h = (-(a + b)) % _P          # H = D - B with D = -A
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _pt_scalarmult(p1, e: int):
    """Double-and-add; the scalar is reduced mod L by the caller."""
    if e == 0:
        return (0, 1, 1, 0)  # neutral element
    q = (0, 1, 1, 0)
    base = p1
    while e:
        if e & 1:
            q = _pt_add(q, base)
        base = _pt_double(base)
        e >>= 1
    return q


def _pt_compress(p) -> bytes:
    x, y, z, _t = p
    zinv = pow(z, _P - 2, _P)
    x = x * zinv % _P
    y = y * zinv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _pt_decompress(data: bytes):
    if len(data) != 32:
        raise ValueError("bad point length")
    y = int.from_bytes(data, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    if y >= _P:
        return None
    x = _xrecover(y)
    if x & 1 != sign:
        x = _P - x
    # on-curve check: -x^2 + y^2 = 1 + d x^2 y^2
    if (-x * x + y * y - 1 - _D * x * x * y * y) % _P != 0:
        return None
    if x == 0 and y == 1:
        return None  # identity / small order -- reject for verification
    return (x, y, 1, x * y % _P)


_B = (_BX, _BY, 1, _BX * _BY % _P)


def ed25519_keypair() -> tuple[bytes, bytes]:
    """Return (secret_key, public_key), 32 bytes each."""
    sk = os.urandom(32)
    h = _sha512(sk)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    pk = _pt_compress(_pt_scalarmult(_B, a))
    return sk, pk


def ed25519_sign(sk: bytes, msg: bytes) -> bytes:
    if len(sk) != 32:
        raise ValueError("secret key must be 32 bytes")
    h = _sha512(sk)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    prefix = h[32:]
    r = int.from_bytes(_sha512(prefix + msg), "little") % _L
    R = _pt_compress(_pt_scalarmult(_B, r))
    A = _pt_compress(_pt_scalarmult(_B, a))
    k = int.from_bytes(_sha512(R + A + msg), "little") % _L
    s = (r + k * a) % _L
    return R + s.to_bytes(32, "little")


def ed25519_verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    """Strict verification.  Returns False (never raises) on bad input."""
    try:
        if len(pk) != 32 or len(sig) != 64:
            return False
        R = sig[:32]
        s = int.from_bytes(sig[32:], "little")
        if s >= _L:
            return False
        A = _pt_decompress(pk)
        Rp = _pt_decompress(R)
        if A is None or Rp is None:
            return False
        k = int.from_bytes(_sha512(R + pk + msg), "little") % _L
        # check [s]B == R + [k]A
        lhs = _pt_scalarmult(_B, s)
        rhs = _pt_add(Rp, _pt_scalarmult(A, k))
        return _pt_compress(lhs) == _pt_compress(rhs)
    except Exception:  # noqa: BLE001 - verification must not raise
        return False


# --------------------------------------------------------------------- #
# ChaCha20 (RFC 8439 section 2.4)
# --------------------------------------------------------------------- #

_SIGMA = b"expand 32-byte k"
_MASK32 = 0xFFFFFFFF


def _rotl32(x: int, n: int) -> int:
    return ((x << n) | (x >> (32 - n))) & _MASK32


def _qr(x, a, b, c, d):
    x[a] = (x[a] + x[b]) & _MASK32; x[d] ^= x[a]; x[d] = _rotl32(x[d], 16)
    x[c] = (x[c] + x[d]) & _MASK32; x[b] ^= x[c]; x[b] = _rotl32(x[b], 12)
    x[a] = (x[a] + x[b]) & _MASK32; x[d] ^= x[a]; x[d] = _rotl32(x[d], 8)
    x[c] = (x[c] + x[d]) & _MASK32; x[b] ^= x[c]; x[b] = _rotl32(x[b], 7)


def _chacha_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    state = list(struct.unpack("<4I", _SIGMA))
    state += list(struct.unpack("<8I", key))
    state += [counter & _MASK32]
    state += list(struct.unpack("<3I", nonce))
    work = list(state)
    for _ in range(10):
        _qr(work, 0, 4, 8, 12)
        _qr(work, 1, 5, 9, 13)
        _qr(work, 2, 6, 10, 14)
        _qr(work, 3, 7, 11, 15)
        _qr(work, 0, 5, 10, 15)
        _qr(work, 1, 6, 11, 12)
        _qr(work, 2, 7, 8, 13)
        _qr(work, 3, 4, 9, 14)
    out = [(work[i] + state[i]) & _MASK32 for i in range(16)]
    return struct.pack("<16I", *out)


def chacha20_xor(key: bytes, nonce: bytes, data: bytes,
                 counter: int = 1) -> bytes:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes")
    out = bytearray()
    for i in range(0, len(data), 64):
        ks = _chacha_block(key, counter, nonce)
        chunk = data[i:i + 64]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
        counter = (counter + 1) & _MASK32
    return bytes(out)


# --------------------------------------------------------------------- #
# Poly1305 (RFC 8439 section 2.5)
# --------------------------------------------------------------------- #

_P1305 = (1 << 130) - 5


def _poly1305_mac(key: bytes, msg: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("poly1305 key must be 32 bytes")
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:], "little")
    acc = 0
    for i in range(0, len(msg), 16):
        block = msg[i:i + 16]
        n = int.from_bytes(block + b"\x01", "little")
        acc = ((acc + n) * r) % _P1305
    acc = (acc + s) & ((1 << 128) - 1)
    return acc.to_bytes(16, "little")


# --------------------------------------------------------------------- #
# AEAD (RFC 8439 section 2.8)
# --------------------------------------------------------------------- #

def _pad16(n: int) -> bytes:
    return b"\x00" * ((16 - (n % 16)) % 16)


def chacha20poly1305_encrypt(key: bytes, nonce: bytes, plaintext: bytes,
                             aad: bytes = b"") -> bytes:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes")
    poly_key = _chacha_block(key, 0, nonce)[:32]
    ct = chacha20_xor(key, nonce, plaintext, counter=1)
    mac_data = (aad + _pad16(len(aad)) + ct + _pad16(len(ct))
                + struct.pack("<QQ", len(aad), len(ct)))
    tag = _poly1305_mac(poly_key, mac_data)
    return ct + tag


def chacha20poly1305_decrypt(key: bytes, nonce: bytes, ciphertext: bytes,
                             aad: bytes = b"") -> bytes:
    if len(ciphertext) < 16:
        raise ValueError("ciphertext too short (missing tag)")
    ct, tag = ciphertext[:-16], ciphertext[-16:]
    poly_key = _chacha_block(key, 0, nonce)[:32]
    mac_data = (aad + _pad16(len(aad)) + ct + _pad16(len(ct))
                + struct.pack("<QQ", len(aad), len(ct)))
    expected = _poly1305_mac(poly_key, mac_data)
    # constant-time-ish compare
    if not _consteq(expected, tag):
        raise ValueError("authentication failed")
    return chacha20_xor(key, nonce, ct, counter=1)


def _consteq(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0


# --------------------------------------------------------------------- #
# key derivation for the license gate
# --------------------------------------------------------------------- #

def derive_key(secret: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 (RFC 5869), used to derive per-segment keys from the
    licensed master secret.  No external KDF dependency.

    HKDF-Extract: PRK = HMAC-SHA256(salt=0^32, IKM=secret)
    HKDF-Expand : OKM = HMAC-SHA256(PRK, T(i-1) || info || i) ...
    The PRK (bound to the secret) is fed into every expand round, so two
    different master secrets can never yield the same derived key -- that
    property is what makes per-license encryption actually secret."""
    prk = hmac.new(b"\x00" * 32, secret, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]),
                         hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


__all__ = [
    "ed25519_keypair", "ed25519_sign", "ed25519_verify",
    "chacha20_xor", "chacha20poly1305_encrypt", "chacha20poly1305_decrypt",
    "derive_key",
]
