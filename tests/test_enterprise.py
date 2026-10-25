import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairn
from cairn.enterprise import (EnterpriseDB, LicenseError, issue_license,
                              load_license, save_license, vendor_keypair)

RT = "2021-01-01T00:00:00+00:00"


class TestEnterprise(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.vsk, self.vpk = vendor_keypair()
        self.lic_doc = issue_license(self.vsk, "acme", seats=5)

    def _lic(self):
        path = os.path.join(self.d, "license.json")
        save_license(self.lic_doc, path)
        return load_license(path, self.vpk)

    def test_license_verifies(self):
        lic = self._lic()
        self.assertTrue(lic.has("encrypted_scopes"))
        self.assertFalse(lic.expired("2020-01-01T00:00:00+00:00"))

    def test_wrong_vendor_rejected(self):
        _, vpk2 = vendor_keypair()
        path = os.path.join(self.d, "license.json")
        save_license(self.lic_doc, path)
        with self.assertRaises(LicenseError):
            load_license(path, vpk2)

    def test_require_gate(self):
        lic = self._lic()
        db = EnterpriseDB.open(os.path.join(self.d, "db"), lic, self.vpk,
                               create=True)
        with self.assertRaises(LicenseError):
            db.require("super_feature")

    def test_record_writes_audit(self):
        lic = self._lic()
        db = EnterpriseDB.open(os.path.join(self.d, "db"), lic, self.vpk,
                               create=True)
        c = db.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        self.assertIsNotNone(c)
        # audit claim was also written
        self.assertGreater(db.seats_used(), 0)

    def test_encrypt_decrypt_roundtrip(self):
        lic = self._lic()
        db = EnterpriseDB.open(os.path.join(self.d, "db"), lic, self.vpk,
                               create=True)
        db.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        n = db.encrypt_database()
        self.assertGreater(n, 0)
        self.assertTrue(db.is_encrypted())
        # a plaintext reader now sees nothing (ciphertext at rest)
        g2 = cairn.open(os.path.join(self.d, "db"))
        self.assertEqual(g2.count(), 0)
        dest = os.path.join(self.d, "plain")
        db.decrypt_to(dest)
        g3 = cairn.open(dest)
        self.assertEqual(g3.value("alice", "role"), "admin")

    def test_wrong_license_cannot_decrypt(self):
        lic = self._lic()
        db = EnterpriseDB.open(os.path.join(self.d, "db"), lic, self.vpk,
                               create=True)
        db.record("alice", "role", "admin", "2020-01-01", recorder="alice")
        db.encrypt_database()
        lic2_doc = issue_license(self.vsk, "other", seats=5)
        p2 = os.path.join(self.d, "lic2.json")
        save_license(lic2_doc, p2)
        lic2 = load_license(p2, self.vpk)
        db2 = EnterpriseDB(graph=db.graph, license=lic2, vendor_pk=self.vpk)
        with self.assertRaises(LicenseError):
            db2.decrypt_to(os.path.join(self.d, "plain2"))


if __name__ == "__main__":
    unittest.main()
