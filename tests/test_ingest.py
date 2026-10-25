import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairn
from cairn.ingest import Ingestor, Proposal, validate

RT = "2021-01-01T00:00:00+00:00"


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.p = os.path.join(tempfile.mkdtemp(), "db")
        cairn.open(self.p, create=True)
        self.ing = Ingestor(self.p)
        self.g = cairn.open(self.p)

    def test_validate_rejects_empty(self):
        p = Proposal(subject="", predicate="p", value="v",
                     valid_from="2020-01-01", recorder="r")
        self.assertIsNotNone(validate(p))

    def test_validate_rejects_reserved(self):
        p = Proposal(subject="s", predicate="retract", value="v",
                     valid_from="2020-01-01", recorder="r")
        self.assertIsNotNone(validate(p))

    def test_validate_ok(self):
        p = Proposal(subject="s", predicate="p", value="v",
                     valid_from="2020-01-01", recorder="r")
        self.assertIsNone(validate(p))

    def test_auto_accept(self):
        rep = self.ing.auto(self.g, [dict(subject="s", predicate="p",
                                         value="v", valid_from="2020-01-01",
                                         recorder="r")])
        self.assertEqual(len(rep.accepted), 1)
        self.assertTrue(rep.ok)
        self.assertEqual(self.g.value("s", "p"), "v")

    def test_auto_reject_bad(self):
        rep = self.ing.auto(self.g, [dict(subject="", predicate="p",
                                         value="v", valid_from="2020-01-01",
                                         recorder="r")])
        self.assertEqual(len(rep.rejected), 1)
        self.assertFalse(rep.ok)

    def test_propose_then_commit(self):
        rep = self.ing.propose([Proposal(subject="s", predicate="p", value="v",
                                         valid_from="2020-01-01", recorder="r")])
        self.assertEqual(len(rep.accepted), 1)
        # not yet in the stream
        self.assertIsNone(cairn.open(self.p).value("s", "p"))
        rep2 = self.ing.commit(self.g)
        self.assertEqual(len(rep2.accepted), 1)
        self.assertEqual(self.g.value("s", "p"), "v")


if __name__ == "__main__":
    unittest.main()
