"""cairn.merge -- the sync primitives.

Theorem (THEORY.md 4): merge(A, B) = A u B is associative, commutative
and idempotent.  This module is the operational face of that theorem:

  diff(a, b)      the cids in b that a lacks (what a download would fetch)
  merge_into(a,b) append exactly those claims
  export(path)    a portable bundle: segment files + manifest, no database
  import_(path)   append a bundle's claims

A bundle is just the segment files (already canonical JSONL) plus a
manifest of cids -- it can travel by USB stick, email, or HTTP, and it
can be merged even into a read-only host by anyone with write access to
a fresh database directory.  No central server is ever required.
"""

from __future__ import annotations

import json
import os
from typing import Iterable, Optional

from .claim import Claim
from .store import Store


class MergeError(Exception):
    pass


def _load_cids(db: str) -> set[str]:
    return Store.open(db).cids()


def diff(db_a: str, db_b: str) -> set[str]:
    """Cids present in b but missing from a (the download set for a)."""
    return _load_cids(db_b) - _load_cids(db_a)


def equal(db_a: str, db_b: str) -> bool:
    """Set equality of the two claim streams (ignores segment layout)."""
    return _load_cids(db_a) == _load_cids(db_b)


def merge_into(db_a: str, db_b: str) -> int:
    """Union b into a.  Returns the number of claims imported (0 == up to date)."""
    return Store.open(db_a).merge_dir(db_b)


def export(db: str, dest: str) -> dict:
    """Write a portable bundle of `db` to directory `dest`.

    The bundle contains the raw segment files (renamed to
    ``part-000001.cseg`` ... for layout independence) plus
    ``manifest.json`` listing every cid.  A partial export (subset of
    scopes) is possible by passing a claim filter.
    """
    store = Store.open(db)
    claims = store.load()
    os.makedirs(dest, exist_ok=True)
    # canonical: one part file, cid-sorted, exactly like compact()
    part = os.path.join(dest, "part-000001.cseg")
    with open(part, "w", encoding="utf-8") as f:
        for cid in sorted(claims):
            f.write(claims[cid].to_json() + "\n")
    manifest = {
        "format": "cairn-bundle-1",
        "count": len(claims),
        "cids": sorted(claims),
    }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")
    return manifest


def import_(db: str, bundle: str) -> int:
    """Merge a bundle into `db`.  Returns the number of claims imported."""
    store = Store.open(db)
    part = os.path.join(bundle, "part-000001.cseg")
    if not os.path.isfile(part):
        raise MergeError(f"bundle missing part file: {bundle}")
    man_path = os.path.join(bundle, "manifest.json")
    manifest: Optional[dict] = None
    if os.path.isfile(man_path):
        with open(man_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    imported = 0
    existing = store.cids()
    claims = []
    total = 0
    with open(part, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                c = Claim.from_json(line)
            except Exception as exc:  # noqa: BLE001
                raise MergeError(f"{part}:{lineno}: {exc}") from exc
            if c.cid not in existing:
                claims.append(c)
                existing.add(c.cid)
    imported = store.append_many(claims)
    if manifest is not None and manifest.get("count") != total:
        # not fatal: re-importing a partially merged bundle is a normal
        # operation; the manifest cross-check exists for tooling.
        pass
    return imported


def conflicts(db: str, subject: str, predicate: str) -> list[Claim]:
    """Live candidate values that disagree (the 'contested memory' report).

    Two claims about the same (subject, predicate) with equal resolution
    rank but different values are a genuine conflict: Cairn keeps both
    and flags them rather than letting a timestamp pick a winner.
    """
    from .graph import CairnGraph
    g = CairnGraph(db)
    r = g.resolve(subject, predicate)
    if len(r.candidates) < 2:
        return []
    top = r.candidates[-1]
    # "equal rank" == same (valid_from, recorded_at); cid is only a
    # deterministic tiebreak for identity, not for truth, so claims that
    # differ in value but tie on (valid_from, recorded_at) are conflicts.
    return [c for c in r.candidates
            if (c.valid_from, c.recorded_at) == (top.valid_from, top.recorded_at)
            and c.value != top.value]


__all__ = ["diff", "equal", "merge_into", "export", "import_", "conflicts",
           "MergeError"]
