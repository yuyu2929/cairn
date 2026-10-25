import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cairn.crypto import (chacha20poly1305_decrypt, chacha20poly1305_encrypt,
                          derive_key, ed25519_keypair, ed25519_sign,
                          ed25519_verify, _chacha_block, _poly1305_mac)


class TestChaCha20(unittest.TestCase):
    def test_rfc8439_block_counter0(self):
        key = bytes(range(32))
        nonce = bytes.fromhex("000000090000004a00000000")
        self.assertEqual(
            _chacha_block(key, 0, nonce).hex(),
            "8adc91fd9ff4f0f51b0fad50ff15d637e40efda206cc52c783a74200503c1582cd9833367d0a54d57d3c9e998f490ee69ca34c1ff9e939a75584c52d690a35d4")

    def test_rfc8439_block_counter1(self):
        key = bytes(range(32))
        nonce = bytes.fromhex("000000090000004a00000000")
        self.assertEqual(
            _chacha_block(key, 1, nonce).hex(),
            "10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4ed2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e")


class TestPoly1305(unittest.TestCase):
    def test_rfc8439_vector(self):
        k = bytes.fromhex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
        self.assertEqual(
            _poly1305_mac(k, b"Cryptographic Forum Research Group").hex(),
            "a8061dc1305136c6c22b8baf0c0127a9")


class TestAEAD(unittest.TestCase):
    def test_rfc8439_vector(self):
        key = bytes(range(0x80, 0xa0))
        nonce = bytes.fromhex("070000004041424344454647")
        aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
        pt = (b"Ladies and Gentlemen of the class of '99: If I could offer "
              b"you only one tip for the future, sunscreen would be it.")
        ct = chacha20poly1305_encrypt(key, nonce, pt, aad)
        self.assertTrue(ct.startswith(bytes.fromhex("d31a8d34648e60db7b86afbc53ef7ec2")))
        self.assertEqual(ct[-16:].hex(), "1ae10b594f09e26a7e902ecbd0600691")
        self.assertEqual(chacha20poly1305_decrypt(key, nonce, ct, aad), pt)

    def test_tamper_detected(self):
        key = os.urandom(32)
        nonce = os.urandom(12)
        pt = os.urandom(50)
        aad = os.urandom(5)
        ct = chacha20poly1305_encrypt(key, nonce, pt, aad)
        bad = bytearray(ct)
        bad[-1] ^= 1
        with self.assertRaises(ValueError):
            chacha20poly1305_decrypt(key, nonce, bytes(bad), aad)

    def test_wrong_key_detected(self):
        key = os.urandom(32)
        nonce = os.urandom(12)
        pt = os.urandom(64)
        ct = chacha20poly1305_encrypt(key, nonce, pt)
        with self.assertRaises(ValueError):
            chacha20poly1305_decrypt(os.urandom(32), nonce, ct)


class TestEd25519(unittest.TestCase):
    def test_rfc8032_vector1(self):
        sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        pk = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = ed25519_sign(sk, b"")
        self.assertEqual(
            sig.hex(),
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e0652249015"
            "55fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        self.assertTrue(ed25519_verify(pk, b"", sig))
        self.assertFalse(ed25519_verify(pk, b"x", sig))

    def test_rfc8032_vector2(self):
        sk = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
        pk = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
        sig = ed25519_sign(sk, bytes.fromhex("72"))
        self.assertEqual(
            sig.hex(),
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")
        self.assertTrue(ed25519_verify(pk, bytes.fromhex("72"), sig))

    def test_rfc8032_vector3(self):
        sk = bytes.fromhex("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7")
        pk = bytes.fromhex("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025")
        sig = ed25519_sign(sk, bytes.fromhex("af82"))
        self.assertEqual(
            sig.hex(),
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
            "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a")
        self.assertTrue(ed25519_verify(pk, bytes.fromhex("af82"), sig))

    def test_random_roundtrip(self):
        for i in range(25):
            sk, pk = ed25519_keypair()
            msg = os.urandom(20 + i * 7)
            sig = ed25519_sign(sk, msg)
            self.assertTrue(ed25519_verify(pk, msg, sig))
            self.assertFalse(ed25519_verify(pk, msg + b"!", sig))
            _, pk2 = ed25519_keypair()
            self.assertFalse(ed25519_verify(pk2, msg, sig))


class TestDeriveKey(unittest.TestCase):
    def test_deterministic(self):
        m = os.urandom(32)
        self.assertEqual(derive_key(m, b"x", 32), derive_key(m, b"x", 32))
        self.assertNotEqual(derive_key(m, b"x", 32), derive_key(m, b"y", 32))
        self.assertEqual(len(derive_key(m, b"x", 16)), 16)


if __name__ == "__main__":
    unittest.main()
