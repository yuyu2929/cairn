"""cairn.store -- append-only segment storage.

A database is a directory.  Claims live in ``segments/*.cseg`` files, one
canonical JSON claim per line, in append order.  Nothing is ever updated or
deleted in place: a retraction is just another claim (see claim.py), so the
storage layer only needs to add lines.

Merge semantics: loading a database is the union of all claims in all
segments.  Because every claim carries a content id, re-appending a claim
that is already present is a no-op (Theorem: merge(A, B) = A u B is
idempotent), which is what makes "copy a segment file onto a USB stick"
a complete sync primitive.

Layout::

    <db>/
      meta.json          {"format": 1, "created": ...}
      segments/
        000001.cseg      canonical JSONL, one claim per line
        000002.cseg      ...

A segment is closed once it exceeds ``--segment-bytes``; appends go to the
open (highest-numbered) segment.  ``compact()`` rewrites the whole log as
one segment whose lines are sorted by cid -- a canonical form of the
database, useful for snapshot comparison (two databases with identical
canonical compacted forms are equal as sets).
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from datetime import datetime, timezone

from .claim import Claim, canonical_bytes, content_id

FORMAT_VERSION = 1
DEFAULT_SEGMENT_BYTES = 8 * 1024 * 1024  # 8 MiB
ENC_SUFFIX = ".eseg"    # encrypted segments (enterprise edition)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StoreError(Exception):
    """Raised when a database directory is malformed or corrupt."""


def _is_db(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "meta.json"))


class Store:
    """Append-only claim log.  Thread-unsafe by design (single writer)."""

    def __init__(self, root: str, segment_bytes: int = DEFAULT_SEGMENT_BYTES):
        self.root = os.path.abspath(root)
        self.segdir = os.path.join(self.root, "segments")
        self.segment_bytes = segment_bytes
        # lazily-loaded set of cids we already have on disk.  Bulk appends
        # stay O(n) because duplicate-checking against this set is O(1)
        # after the first scan; without it, every append re-read the whole
        # log and write storms degenerated to O(n^2).
        self._cids = None
        self._open_seg = None  # cached open segment path for appends

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    @classmethod
    def create(cls, root: str, segment_bytes: int = DEFAULT_SEGMENT_BYTES) -> "Store":
        """Create a fresh database directory (refuses to clobber)."""
        if _is_db(root):
            raise StoreError(f"database already exists: {root}")
        store = cls(root, segment_bytes)
        os.makedirs(store.segdir, exist_ok=True)
        meta = {"format": FORMAT_VERSION, "created": _now(),
                "db_id": base64.b64encode(os.urandom(16)).decode("ascii")}
        with open(os.path.join(root, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1, sort_keys=True)
            f.write("\n")
        return store

    @classmethod
    def open(cls, root: str, create: bool = False,
             segment_bytes: int = DEFAULT_SEGMENT_BYTES) -> "Store":
        if not _is_db(root):
            if create:
                return cls.create(root, segment_bytes)
            raise StoreError(f"not a cairn database: {root}")
        store = cls(root, segment_bytes)
        if not os.path.isdir(store.segdir):
            raise StoreError(f"missing segments dir: {store.segdir}")
        return store

    # ------------------------------------------------------------------ #
    # reading
    # ------------------------------------------------------------------ #

    def segment_paths(self) -> list[str]:
        """All plaintext segment files, oldest first (numeric name order)."""
        names = [n for n in os.listdir(self.segdir) if n.endswith(".cseg")]
        names.sort()
        return [os.path.join(self.segdir, n) for n in names]

    def encrypted_segments(self) -> list[str]:
        """Encrypted (.eseg) segments -- readable only with a license key."""
        names = [n for n in os.listdir(self.segdir) if n.endswith(ENC_SUFFIX)]
        names.sort()
        return [os.path.join(self.segdir, n) for n in names]

    def _ensure_plaintext(self) -> None:
        """Guard: plaintext operations on an encrypted database would
        silently drop the .eseg files (compact deletes what it cannot
        read), so refuse instead."""
        if self.encrypted_segments():
            raise StoreError(
                "database contains encrypted segments; decrypt it first "
                "(cairn enterprise decrypt) before plaintext operations")

    def load(self) -> dict[str, Claim]:
        """Every claim in the database, keyed by cid.

        Duplicated cids across segments collapse to one claim (the union
        theorem in practice: replay is order-independent and idempotent).

        Read-only: an encrypted database simply yields no plaintext
        claims here (its data lives in .eseg files); mutating operations
        below still refuse on encrypted databases.
        """
        claims: dict[str, Claim] = {}
        for path in self.segment_paths():
            with open(path, "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        claim = Claim.from_json(line)
                    except Exception as exc:  # noqa: BLE001 - report with context
                        raise StoreError(
                            f"corrupt claim at {path}:{lineno}: {exc}"
                        ) from exc
                    if claim.cid in claims and claims[claim.cid] != claim:
                        # Same cid, different bytes: impossible unless the
                        # canonical encoding broke.  Fail loudly.
                        raise StoreError(
                            f"cid collision with differing content at "
                            f"{path}:{lineno} (cid={claim.cid})"
                        )
                    claims[claim.cid] = claim
        self._cids = set(claims.keys())
        return claims

    def _sync_cids(self) -> set[str]:
        """Return the in-memory cid set, scanning the log from disk once."""
        if self._cids is None:
            self._cids = set()
            for path in self.segment_paths():
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        self._cids.add(Claim.from_json(line).cid)
        return self._cids

    def cids(self) -> set[str]:
        return set(self._sync_cids())

    def __len__(self) -> int:
        return len(self._sync_cids())

    def count(self) -> int:
        return len(self._sync_cids())

    # ------------------------------------------------------------------ #
    # writing
    # ------------------------------------------------------------------ #

    def _append_handle(self):
        """Return the open file handle for the current append segment.

        Keeps one handle alive for the process instead of open()/close() on
        every single append -- that per-record syscall churn was the
        dominant write bottleneck.  Rolls to a fresh segment once the open
        one crosses ``segment_bytes``.  Reused across append() and
        append_many()."""
        cur = getattr(self, "_open_seg", None)
        if cur is not None and os.path.getsize(cur) < self.segment_bytes:
            return self._fh
        # need a (new) open segment
        if getattr(self, "_fh", None) is not None:
            self._fh.close()
            self._fh = None
        paths = self.segment_paths()
        if paths:
            last = paths[-1]
            if os.path.getsize(last) < self.segment_bytes:
                self._open_seg = last
            else:
                n = int(os.path.basename(last).split(".")[0])
                self._open_seg = os.path.join(self.segdir, f"{n + 1:06d}.cseg")
        else:
            self._open_seg = os.path.join(self.segdir, "000001.cseg")
        self._fh = open(self._open_seg, "a", encoding="utf-8")
        return self._fh

    def append(self, claim: Claim) -> bool:
        """Append one claim.  Returns False if its cid was already present.

        The skip is not an optimization, it is correctness hygiene: a
        database that never stores the same cid twice keeps merges cheap
        and makes ``compact()`` canonical (sorted set, no multiplicities).
        """
        self._ensure_plaintext()
        known = self._sync_cids()
        if claim.cid in known:
            return False
        line = claim.to_json() + "\n"
        # One write of one line keeps the append atomic on Linux (PIPE_BUF
        # for regular files: the line lands whole or the process died; a
        # torn line is caught by verify()).  We flush but do NOT fsync per
        # record -- fsync on every single write is what makes bulk import
        # pathological.  Durability is provided in batches by append_many
        # and compact(), and the OS cache survives a process crash.
        fh = self._append_handle()
        fh.write(line)
        fh.flush()
        known.add(claim.cid)
        return True

    def append_many(self, claims) -> int:
        """Append an iterable of claims; returns how many were new."""
        self._ensure_plaintext()
        existing = self._sync_cids()
        fh = self._append_handle()
        written = 0
        for claim in claims:
            if claim.cid in existing:
                continue
            fh.write(claim.to_json() + "\n")
            existing.add(claim.cid)
            written += 1
        fh.flush()
        os.fsync(fh.fileno())  # one durability barrier for the whole batch
        return written

    # ------------------------------------------------------------------ #
    # maintenance
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Release the long-lived append handle.

        Windows refuses to delete a file that is still open (WinError 32),
        so anything that unlinks or replaces segments must close this first.
        On Linux unlinking an open file simply works, which is why the
        original code passed on CI while failing on Windows.
        """
        fh = getattr(self, "_fh", None)
        if fh is not None:
            try:
                fh.close()
            finally:
                self._fh = None
                self._open_seg = None

    def compact(self) -> int:
        """Rewrite the log as one segment with cid-sorted lines.

        Returns the number of claims retained.  The result is a canonical
        form: two databases with the same claim set compact to
        byte-identical segment files (modulo the meta.json timestamp).
        """
        self._ensure_plaintext()
        claims = self.load()
        # Close before touching the files: the append handle is open on the
        # last segment, and Windows will not let us unlink it.
        self.close()
        fd, tmp = tempfile.mkstemp(dir=self.segdir, suffix=".cseg.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for cid in sorted(claims):
                    f.write(claims[cid].to_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            for path in self.segment_paths():
                os.unlink(path)
            os.replace(tmp, os.path.join(self.segdir, "000001.cseg"))
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return len(claims)

    def verify(self) -> list[str]:
        """Full replay check.  Returns a list of problems ([] == healthy).

        Re-derives every cid from the line content and checks segment
        naming order, so a truncated final line (crash mid-append) is
        reported rather than silently trusted.
        """
        problems: list[str] = []
        if self.encrypted_segments():
            problems.append("database contains encrypted segments "
                            "(not verifiable without a license key)")
        seen: set[str] = set()
        paths = self.segment_paths()
        expected_n = 0
        for path in paths:
            expected_n += 1
            base = os.path.basename(path)
            want = f"{expected_n:06d}.cseg"
            if base != want:
                problems.append(f"segment out of order: {base} (want {want})")
            with open(path, "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        claim = Claim.from_json(line)
                    except Exception as exc:  # noqa: BLE001
                        problems.append(f"{base}:{lineno}: unparseable ({exc})")
                        continue
                    if claim.cid in seen:
                        problems.append(
                            f"{base}:{lineno}: duplicate cid {claim.cid[:12]}..."
                        )
                    seen.add(claim.cid)
        return problems

    # ------------------------------------------------------------------ #
    # merging
    # ------------------------------------------------------------------ #

    def merge_dir(self, other_root: str) -> int:
        """Union another database's claims into this one.

        This is the whole sync story: read their segments, append the
        claims we do not have.  Returns how many claims were imported.
        """
        self._ensure_plaintext()
        other = Store.open(other_root)
        return self.append_many(other.load().values())

    # ------------------------------------------------------------------ #
    # misc
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        paths = self.segment_paths()
        size = sum(os.path.getsize(p) for p in paths)
        return {
            "segments": len(paths),
            "bytes": size,
            "claims": len(self.load()),
        }


def segment_checksums(db: str) -> list[tuple[str, str]]:
    """(segment name, blake2b of file bytes) -- cheap way to diff two DBs
    without parsing claims; used by the demo and the merge benchmark."""
    import hashlib

    store = Store(db)
    out = []
    for path in store.segment_paths():
        with open(path, "rb") as f:
            out.append((os.path.basename(path), hashlib.blake2b(f.read()).hexdigest()))
    return out


__all__ = [
    "Store",
    "StoreError",
    "FORMAT_VERSION",
    "DEFAULT_SEGMENT_BYTES",
    "segment_checksums",
    "canonical_bytes",
    "content_id",
]
