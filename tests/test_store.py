import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cairn.claim import Claim
from cairn.store import Store, StoreError

RT = "2021-01-01T00:00:00+00:00"


def tmpdb():
    return os.path.join(tempfile.mkdtemp(), "db")


def c(subject, predicate, value, recorder="r"):
    return Claim.create(subject, predicate, value, "2020-01-01", recorder,
                        recorded_at=RT)


class TestStore(unittest.TestCase):
    def test_create_open(self):
        p = tmpdb()
        s = Store.create(p)
        self.assertTrue(os.path.isdir(os.path.join(p, "segments")))
        s2 = Store.open(p)
        self.assertEqual(s2.root, s.root)

    def test_refuse_clobber(self):
        p = tmpdb()
        Store.create(p)
        with self.assertRaises(StoreError):
            Store.create(p)

    def test_append_dedupe(self):
        p = tmpdb()
        s = Store.create(p)
        self.assertTrue(s.append(c("a", "p", "v")))
        self.assertFalse(s.append(c("a", "p", "v")))
        self.assertEqual(s.count(), 1)

    def test_load(self):
        p = tmpdb()
        s = Store.create(p)
        claim = c("a", "p", "v")
        s.append(claim)
        claims = s.load()
        self.assertIn(claim.cid, claims)
        self.assertEqual(claims[claim.cid], claim)

    def test_compact_canonical(self):
        p = tmpdb()
        s = Store.create(p)
        for i in range(10):
            s.append(c("a", "p", i))
        self.assertEqual(s.compact(), 10)
        s2 = Store.open(p)
        self.assertEqual(s2.count(), 10)

    def test_verify_clean(self):
        p = tmpdb()
        s = Store.create(p)
        for i in range(5):
            s.append(c("a", "p", i))
        self.assertEqual(s.verify(), [])

    def test_segment_rotation(self):
        p = tmpdb()
        s = Store.create(p, segment_bytes=200)
        for i in range(50):
            s.append(c("subject-long-name", "predicate", f"value-{i}", "recorder-x"))
        self.assertGreater(len(s.segment_paths()), 1)

    def test_merge_dir_idempotent(self):
        p1 = tmpdb()
        p2 = tmpdb()
        s1 = Store.create(p1)
        s2 = Store.create(p2)
        claim = c("a", "p", "v")
        s1.append(claim)
        s2.append(claim)
        self.assertEqual(s1.merge_dir(p2), 0)  # already present
        self.assertEqual(s1.count(), 1)


if __name__ == "__main__":
    unittest.main()
