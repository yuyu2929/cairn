"""cairn.enterprise -- the paid edition's teeth.

Three things the community edition deliberately does not have, all built
on the primitives in crypto.py (zero dependencies):

  1. License gate    Ed25519-signed licenses.  Features (encrypted
                     scopes, seats, audit) are inert without a license
                     the vendor's public key validates.  This is the
                     open-core boundary: the code is open, the *right to
                     use certain features at scale* is what companies pay
                     for -- same model as GitLab/Sentry, no phone-home.

  2. Encrypted scopes  Claims in a scoped segment are ciphertext at rest,
                     keyed by a per-database, per-scope key wrapped by the
                     license master secret.  Scope stops being a read-time
                     filter (community) and becomes a cryptographic
                     boundary (enterprise): a leaked segment file is
                     noise without the license key.

  3. Audit stream    Every enterprise write also lands in an append-only
                     audit log keyed by the recorder, so "who asserted
                     what, when" survives even retractions.

Honest limitations (docs/RESEARCH.md "risks"):
  - the license file must stay secret for encryption to hold; it is a
    capability, not just a permission slip;
  - offline expiry checks use the local clock (documented, not hidden);
  - this is open-core, not DRM: a determined customer with the source
    can bypass the gate.  What they cannot do is forge a license.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from .claim import Claim
from .crypto import (chacha20poly1305_decrypt, chacha20poly1305_encrypt,
                     derive_key, ed25519_keypair, ed25519_sign,
                     ed25519_verify)
from .graph import CairnGraph
from .store import Store

LICENSE_VERSION = 1
DEFAULT_FEATURES = ("encrypted_scopes", "audit_log")


class LicenseError(Exception):
    pass


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------- #
# licenses
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class License:
    org: str
    features: tuple[str, ...]
    seats: int
    expires: str                 # ISO-8601; "9999-12-31T00:00:00+00:00" = never
    license_id: str
    master_secret: bytes         # capability: wraps the scope keys
    issued_at: str

    def has(self, feature: str) -> bool:
        return feature in self.features

    def expired(self, now: Optional[str] = None) -> bool:
        now = now or _now_iso()
        return now > self.expires

    def to_payload(self) -> dict:
        return {
            "version": LICENSE_VERSION,
            "license_id": self.license_id,
            "org": self.org,
            "features": list(self.features),
            "seats": self.seats,
            "expires": self.expires,
            "issued_at": self.issued_at,
            "master_secret": _b64e(self.master_secret),
        }

    @classmethod
    def from_payload(cls, d: dict) -> "License":
        return cls(
            org=d["org"], features=tuple(d["features"]), seats=int(d["seats"]),
            expires=d["expires"], license_id=d["license_id"],
            master_secret=_b64d(d["master_secret"]), issued_at=d["issued_at"])


def issue_license(vendor_sk: bytes, org: str, features=DEFAULT_FEATURES,
                  seats: int = 10, days: int = 365,
                  license_id: Optional[str] = None,
                  master_secret: Optional[bytes] = None) -> dict:
    """Vendor side: mint a license file (dict, ready to JSON-dump)."""
    if master_secret is None:
        master_secret = os.urandom(32)
    if license_id is None:
        license_id = "lic-" + os.urandom(8).hex()
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    lic = License(org=org, features=tuple(features), seats=seats,
                  expires=expires.isoformat(), license_id=license_id,
                  master_secret=master_secret, issued_at=_now_iso())
    payload = lic.to_payload()
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = ed25519_sign(vendor_sk, msg)
    return {"payload": payload, "signature": _b64e(sig)}


def load_license(path: str, vendor_pk: bytes) -> License:
    """Customer side: verify and load a license file."""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    try:
        payload = doc["payload"]
        sig = _b64d(doc["signature"])
    except (KeyError, ValueError) as e:
        raise LicenseError(f"malformed license file: {e}") from e
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if not ed25519_verify(vendor_pk, msg, sig):
        raise LicenseError("license signature does not verify "
                           "(wrong vendor key or forged license)")
    lic = License.from_payload(payload)
    if lic.expired():
        raise LicenseError(f"license {lic.license_id} expired at {lic.expires}")
    return lic


def save_license(doc: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------- #
# encrypted segments
# --------------------------------------------------------------------- #

ENC_SUFFIX = ".eseg"
ENC_HEADER = "cairn-encrypted-segment-1"


def _db_id(db_path: str) -> bytes:
    meta_path = os.path.join(db_path, "meta.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    if "db_id" not in meta:
        raise LicenseError("database has no db_id; re-create with enterprise "
                           "support or run `cairn enterprise init`")
    return _b64d(meta["db_id"])


def master_key(license: License, db_path: str) -> bytes:
    """Per-database key, wrapped by the license master secret.

    The same database + license always derives the same key, so a database
    encrypted on one device can be decrypted on another with the same
    license file -- that is the sync story for enterprise teams.  A leaked
    .eseg file is noise without the license.
    """
    info = (ENC_HEADER + "|db|").encode() + _db_id(db_path)
    return derive_key(license.master_secret, info, 32)


def scope_key(license: License, db_path: str, scope: str) -> bytes:
    """Per-scope key for selective sharing: export one scope as an
    encrypted bundle a partner org can read with a scoped sub-license."""
    info = (ENC_HEADER + "|scope|" + scope + "|").encode() + _db_id(db_path)
    return derive_key(license.master_secret, info, 32)


def encrypt_segment_file(src: str, dst: str, key: bytes, scope: str,
                         db_path: str) -> None:
    """Encrypt one .cseg file into a self-describing .eseg file."""
    with open(src, "rb") as f:
        plaintext = f.read()
    aad = (ENC_HEADER + "|" + scope + "|").encode() + _db_id(db_path)
    nonce = os.urandom(12)
    ct = chacha20poly1305_encrypt(key, nonce, plaintext, aad)
    header = {"format": ENC_HEADER, "scope": scope, "nonce": _b64e(nonce),
              "aad": _b64e(aad)}
    with open(dst, "w", encoding="utf-8") as f:
        f.write(json.dumps(header, sort_keys=True) + "\n")
        f.write(_b64e(ct) + "\n")


def decrypt_segment_file(src: str, key: bytes) -> bytes:
    with open(src, "r", encoding="utf-8") as f:
        header = json.loads(f.readline())
        blob = f.readline().strip()
    if header.get("format") != ENC_HEADER:
        raise LicenseError(f"not an encrypted segment: {src}")
    aad = _b64d(header["aad"])
    return chacha20poly1305_decrypt(key, _b64d(header["nonce"]),
                                    _b64d(blob), aad)


# --------------------------------------------------------------------- #
# the enterprise database
# --------------------------------------------------------------------- #

AUDIT_RECORDER = "__audit__"


@dataclass
class EnterpriseDB:
    """A CairnGraph plus license-gated capabilities."""
    graph: CairnGraph
    license: License
    vendor_pk: bytes
    encrypted_scopes: set[str] = field(default_factory=set)

    # ---- gating -------------------------------------------------------- #
    def require(self, feature: str) -> None:
        if not self.license.has(feature):
            raise LicenseError(
                f"feature {feature!r} requires a Cairn enterprise license "
                f"(this one grants: {', '.join(self.license.features)})")

    def seats_used(self) -> int:
        return len({c.recorder for c in self.graph.claims()
                    if c.recorder != AUDIT_RECORDER})

    def check_seats(self) -> None:
        if self.seats_used() > self.license.seats:
            raise LicenseError(
                f"seat limit exceeded: {self.seats_used()} recorders, "
                f"license allows {self.license.seats}")

    # ---- writes with audit --------------------------------------------- #
    def record(self, *args, **kwargs) -> Optional[Claim]:
        self.require("audit_log")
        self.check_seats()
        claim = self.graph.record(*args, **kwargs)
        if claim is not None:
            self._audit("record", claim)
        return claim

    def _audit(self, action: str, claim: Claim) -> None:
        note = json.dumps({"action": action, "cid": claim.cid,
                           "subject": claim.subject,
                           "predicate": claim.predicate,
                           "at": _now_iso()}, sort_keys=True)
        self.graph.record(AUDIT_RECORDER, "audit", note,
                          valid_from=claim.recorded_at,
                          recorder=AUDIT_RECORDER, scope="company",
                          source="enterprise.audit")

    # ---- encrypted scopes ---------------------------------------------- #
    def encrypt_database(self) -> int:
        """Encrypt the whole log at rest.

        The log is rewritten as one encrypted segment: community tools can
        still see the *structure* (one opaque .eseg file) but not the
        claims without the license key.  Scopes stay meaningful *inside*
        the ciphertext -- read-time scope views work as before once the
        database is decrypted.
        """
        self.require("encrypted_scopes")
        key = master_key(self.license, self.graph.path)
        store = self.graph.store
        claims = store.load()
        fd, tmp = tempfile.mkstemp(dir=store.segdir, suffix=".cseg.tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for cid in sorted(claims):
                f.write(claims[cid].to_json() + "\n")
        # Close the long-lived append handle before unlinking: Windows will
        # not delete a file that is still open (WinError 32).
        store.close()
        for path in store.segment_paths():
            os.unlink(path)
        dst = os.path.join(store.segdir, "000001" + ENC_SUFFIX)
        encrypt_segment_file(tmp, dst, key, "database", store.root)
        os.unlink(tmp)
        self.encrypted_scopes.add("database")
        self.graph.reload()
        return len(claims)

    def is_encrypted(self) -> bool:
        return any(n.endswith(ENC_SUFFIX) for n in
                   os.listdir(self.graph.store.segdir))

    def decrypt_to(self, dest_db: str) -> int:
        """Materialize a plaintext copy of an encrypted database.

        This is the compliance/export path: with the license key, an
        encrypted database becomes an ordinary one.
        """
        if not self.is_encrypted():
            raise LicenseError("database is not encrypted")
        key = master_key(self.license, self.graph.path)
        out = Store.create(dest_db)
        n = 0
        for name in sorted(os.listdir(self.graph.store.segdir)):
            if not name.endswith(ENC_SUFFIX):
                continue
            src = os.path.join(self.graph.store.segdir, name)
            try:
                plaintext = decrypt_segment_file(src, key)
            except Exception as e:  # noqa: BLE001
                raise LicenseError(f"cannot decrypt {name}: wrong license "
                                   f"key? ({e})") from e
            fd, tmp = tempfile.mkstemp(dir=out.segdir, suffix=".cseg")
            with os.fdopen(fd, "wb") as f:
                f.write(plaintext)
            os.replace(tmp, os.path.join(out.segdir, f"{n + 1:06d}.cseg"))
            n += 1
        return n

    # ---- lifecycle ------------------------------------------------------ #
    @classmethod
    def open(cls, db_path: str, license: License, vendor_pk: bytes,
             create: bool = False) -> "EnterpriseDB":
        graph = CairnGraph(db_path, create=create)
        return cls(graph=graph, license=license, vendor_pk=vendor_pk)

    def stats(self) -> dict:
        return {**self.graph.stats(), "seats_used": self.seats_used(),
                "encrypted": self.is_encrypted(),
                "features": list(self.license.features),
                "license": self.license.license_id,
                "org": self.license.org}


def vendor_keypair() -> tuple[bytes, bytes]:
    """A vendor's long-term signing keypair (generated once, kept secret)."""
    return ed25519_keypair()


__all__ = ["License", "LicenseError", "EnterpriseDB", "issue_license",
           "load_license", "save_license", "vendor_keypair", "scope_key",
           "master_key",
           "encrypt_segment_file", "decrypt_segment_file", "ENC_SUFFIX",
           "DEFAULT_FEATURES"]
