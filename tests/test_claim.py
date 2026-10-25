import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cairn.claim import (Claim, canonical_bytes, norm_time, SCOPE_DEFAULT)

RT = "2021-01-01T00:00:00+00:00"


class TestCanonical(unittest.TestCase):
    def test_sorted_keys(self):
        c = Claim.create("a", "p", "v", "2020-01-01", "rec", recorded_at=RT)
        b1 = canonical_bytes(c.to_dict())
        d2 = dict(reversed(list(c.to_dict().items())))
        self.assertEqual(canonical_bytes(d2), b1)

    def test_idempotent_cid(self):
        a = Claim.create("alice", "role", "admin", "2020-01-01", "rec", recorded_at=RT)
        b = Claim.create("alice", "role", "admin", "2020-01-01", "rec", recorded_at=RT)
        self.assertEqual(a.cid, b.cid)

    def test_different_fact_different_cid(self):
        a = Claim.create("alice", "role", "admin", "2020-01-01", "r", recorded_at=RT)
        b = Claim.create("alice", "role", "owner", "2020-01-01", "r", recorded_at=RT)
        self.assertNotEqual(a.cid, b.cid)

    def test_cid_length(self):
        c = Claim.create("a", "p", "v", "2020-01-01", "r", recorded_at=RT)
        self.assertEqual(len(c.cid), 32)  # 16 bytes -> 32 hex chars

    def test_norm_time_iso(self):
        # Offset-aware inputs are converted to UTC and stored naive: with the
        # offset kept, the same instant had two canonical spellings and two
        # devices could order the same pair of claims differently.
        self.assertEqual(norm_time("2020-01-01T00:00:00+00:00"),
                         "2020-01-01T00:00:00")
        self.assertEqual(norm_time("2020-01-01T00:00:00Z"),
                         "2020-01-01T00:00:00")
        self.assertEqual(norm_time("2020-01-01T08:00:00+08:00"),
                         "2020-01-01T00:00:00")          # same instant, other zone
        self.assertEqual(norm_time("2020-01-01T08:00:00+08:00"),
                         norm_time("2020-01-01T00:00:00Z"))
        # ... and an offset really is applied, not just dropped
        self.assertEqual(norm_time("2020-01-01T08:00:00+08:00"),
                         norm_time("2020-01-01T00:00:00"))

    def test_norm_time_unix(self):
        self.assertEqual(norm_time(0), "1970-01-01T00:00:00")

    def test_negative_zero(self):
        c = Claim.create("a", "p", -0.0, "2020-01-01", "r", recorded_at=RT)
        self.assertEqual(c.value, 0.0)
        self.assertNotEqual(repr(c.value), "-0.0")

    def test_nan_rejected(self):
        with self.assertRaises(ValueError):
            Claim.create("a", "p", float("nan"), "2020-01-01", "r", recorded_at=RT)

    def test_nfc_equivalence(self):
        composed = "\u00e9"            # é (precomposed)
        decomposed = "\u0065\u0301"    # e + combining acute
        a = Claim.create("a", "p", composed, "2020-01-01", "r", recorded_at=RT)
        b = Claim.create("a", "p", decomposed, "2020-01-01", "r", recorded_at=RT)
        self.assertEqual(a.cid, b.cid)

    def test_json_roundtrip(self):
        c = Claim.create("alice", "role", "admin", "2020-01-01", "rec", recorded_at=RT)
        c2 = Claim.from_json(c.to_json())
        self.assertEqual(c.cid, c2.cid)
        self.assertEqual(c, c2)

    def test_is_operation(self):
        c = Claim.create("x", "retract", "y", "2020-01-01", "r", recorded_at=RT)
        self.assertTrue(c.is_operation())
        c2 = Claim.create("x", "role", "v", "2020-01-01", "r", recorded_at=RT)
        self.assertFalse(c2.is_operation())


if __name__ == "__main__":
    unittest.main()
