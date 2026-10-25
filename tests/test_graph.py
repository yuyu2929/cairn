import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairn

RT = "2021-01-01T00:00:00+00:00"


def tmpdb():
    return os.path.join(tempfile.mkdtemp(), "db")


class TestGraph(unittest.TestCase):
    def setUp(self):
        self.path = tmpdb()
        self.g = cairn.open(self.path, create=True)

    def test_record_and_value(self):
        self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        self.assertEqual(self.g.view().value("alice", "role"), "admin")

    def test_readtime_winner_valid_from(self):
        self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        self.g.record("alice", "role", "owner", "2026-06-01", recorder="alice")
        # newer valid_from wins
        self.assertEqual(self.g.view().value("alice", "role"), "owner")

    def test_history_never_overwritten(self):
        self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        self.g.record("alice", "role", "owner", "2026-06-01", recorder="alice")
        rows = self.g.view().timeline("alice")
        self.assertEqual(len(rows), 2)

    def test_retract_reverts_value(self):
        c1 = self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        c2 = self.g.record("alice", "role", "owner", "2026-06-01", recorder="alice")
        self.g.retract(c2.cid, "alice", "correction")
        self.assertEqual(self.g.view().value("alice", "role"), "admin")
        # attribute still has a valid value -> not "retracted"
        self.assertFalse(self.g.view().resolve("alice", "role").retracted)

    def test_full_retract_is_retracted(self):
        c1 = self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        self.g.retract(c1.cid, "alice", "oops")
        self.assertTrue(self.g.view().resolve("alice", "role").retracted)
        self.assertIsNone(self.g.view().value("alice", "role"))

    def test_dispute_flag(self):
        c1 = self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        self.g.dispute(c1.cid, "bob", "never admin")
        r = self.g.view().resolve("alice", "role")
        self.assertTrue(r.disputed)
        self.assertFalse(r.retracted)
        self.assertEqual(self.g.view().value("alice", "role"), "admin")

    def test_verify_op(self):
        c1 = self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        self.g.endorse(c1.cid, "carol", "checked")
        self.assertIn("carol", self.g.view().resolve("alice", "role").verified_by)

    def test_alias_merge(self):
        a = self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        b = self.g.record("alice-2", "role", "cto", "2020-01-01", recorder="alice")
        self.g.alias("alice", "alice-2", "alice")
        # resolving either identity sees both roles
        self.assertEqual(self.g.view().value("alice", "role"), "admin")
        self.assertEqual(self.g.view().value("alice-2", "role"), "cto")

    def test_links_and_traverse(self):
        self.g.record("alice", "member-of", "acme", "2020-01-01", recorder="alice")
        self.g.record("acme", "industry", "mining", "2000-01-01", recorder="acme")
        links = self.g.view().links("alice")
        self.assertIn(("member-of", "acme", links[0][2]), links)
        trav = self.g.view().traverse("alice", max_depth=2)
        self.assertEqual(trav["alice"], {"acme"})

    def test_search_cjk(self):
        self.g.record("bob", "note", "Alice 三月雇佣了 Bob", "2026-03-01",
                      recorder="bob")
        hits = self.g.view().search("三月")
        self.assertTrue(any(c.subject == "bob" for c in hits))

    def test_at_time_bitemporal(self):
        self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        # a query as-of 2019 sees nothing (valid_from in the future)
        live = self.g.view().at_time("2019-06-01")
        self.assertFalse(any(c.subject == "alice" for c in live))

    def test_known_at_recorded(self):
        g2 = cairn.open(self.path)
        # nothing was recorded before 2000
        self.assertEqual(len(g2.known_at("2000-01-01T00:00:00+00:00").subjects()), 0)

    def test_scope_filter(self):
        self.g.record("alice", "salary", 100, "2020-01-01", recorder="alice",
                      scope="company")
        self.assertIsNone(self.g.view("private").value("alice", "salary"))
        self.assertEqual(self.g.view("company").value("alice", "salary"), 100)

    def test_merge_union_idempotent(self):
        p2 = tmpdb()
        g2 = cairn.open(p2, create=True)
        g2.record("carol", "role", "cto", "2020-01-01", recorder="carol")
        self.assertEqual(self.g.merge(p2), 1)
        self.assertEqual(self.g.view().value("carol", "role"), "cto")
        self.assertEqual(self.g.merge(p2), 0)  # idempotent

    def test_compact_and_verify(self):
        self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        self.assertEqual(self.g.compact(), self.g.count())
        self.assertEqual(self.g.verify(), [])

    def test_stats(self):
        self.g.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        st = self.g.stats()
        self.assertEqual(st["subjects"], 1)
        self.assertGreaterEqual(st["claims"], 1)


if __name__ == "__main__":
    unittest.main()
