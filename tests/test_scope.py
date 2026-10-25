import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cairn.scope import (ScopeError, ScopePolicy, can_view, highest_visible,
                         rank)


class TestScope(unittest.TestCase):
    def test_rank_order(self):
        self.assertLess(rank("private"), rank("family"))
        self.assertLess(rank("family"), rank("company"))
        self.assertLess(rank("company"), rank("public"))

    def test_can_view(self):
        self.assertTrue(can_view("private", "private"))
        self.assertTrue(can_view("company", "family"))
        self.assertFalse(can_view("private", "company"))
        self.assertFalse(can_view("family", "company"))

    def test_highest_visible(self):
        self.assertEqual(highest_visible("family", ["private", "company", "family"]),
                         "family")

    def test_policy_default_fallback(self):
        pol = ScopePolicy(default_scope="private")
        self.assertEqual(pol.check(""), "private")
        self.assertEqual(pol.check(None), "private")

    def test_policy_rejects_unknown(self):
        pol = ScopePolicy(default_scope="private", allowed=("private", "family"))
        with self.assertRaises(ScopeError):
            pol.check("company")

    def test_view_for(self):
        pol = ScopePolicy(default_scope="private")
        self.assertEqual(pol.view_for("company"), "company")


if __name__ == "__main__":
    unittest.main()
