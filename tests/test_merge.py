import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairn
from cairn.merge import (conflicts, diff, equal, export, import_, merge_into)

RT = "2021-01-01T00:00:00+00:00"


def mkdb(claims):
    p = os.path.join(tempfile.mkdtemp(), "db")
    g = cairn.open(p, create=True)
    for kw in claims:
        g.record(recorded_at=RT, **kw)
    return p


BASE = dict(subject="s", predicate="p", value="v", valid_from="2020-01-01",
            recorder="r")


class TestMerge(unittest.TestCase):
    def test_equal(self):
        a = mkdb([BASE])
        b = mkdb([BASE])
        self.assertTrue(equal(a, b))

    def test_diff(self):
        a = mkdb([])
        b = mkdb([BASE])
        self.assertEqual(len(diff(a, b)), 1)

    def test_merge_into(self):
        a = mkdb([BASE])
        b = mkdb([dict(subject="x", predicate="q", value="w",
                       valid_from="2020-01-01", recorder="r")])
        self.assertEqual(merge_into(a, b), 1)
        g = cairn.open(a)
        self.assertEqual(g.value("x", "q"), "w")
        self.assertEqual(merge_into(a, b), 0)  # idempotent

    def test_export_import_roundtrip(self):
        a = mkdb([BASE])
        bundle = os.path.join(tempfile.mkdtemp(), "bundle")
        manifest = export(a, bundle)
        self.assertEqual(manifest["count"], 1)
        dest = os.path.join(tempfile.mkdtemp(), "imp")
        g = cairn.open(dest, create=True)
        self.assertEqual(import_(dest, bundle), 1)
        g = cairn.open(dest)
        self.assertEqual(g.value("s", "p"), "v")

    def test_conflict_detection(self):
        # two recorders, same valid_from + recorded_at, different value -> conflict
        a = mkdb([])
        g = cairn.open(a)
        g.record("alice", "role", "admin", "2020-01-01", recorder="bob",
                 recorded_at=RT)
        g.record("alice", "role", "owner", "2020-01-01", recorder="carol",
                 recorded_at=RT)
        self.assertEqual(len(conflicts(a, "alice", "role")), 1)

    def test_no_false_conflict_on_history(self):
        a = mkdb([])
        g = cairn.open(a)
        g.record("alice", "role", "admin", "2020-01-01", recorder="bob",
                 recorded_at=RT)
        g.record("alice", "role", "owner", "2026-01-01", recorder="carol",
                 recorded_at=RT)
        self.assertEqual(len(conflicts(a, "alice", "role")), 0)


if __name__ == "__main__":
    unittest.main()
