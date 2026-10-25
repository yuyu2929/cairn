"""Headline claims, pinned: merge is order-independent, idempotent and
verifiable; the same instant has exactly one canonical spelling."""
import itertools
import os
import tempfile

import cairn

# recorded_at is fixed on purpose: cid is content-derived, so a `now()` would
# make every run different and turn the test into noise.
ROWS = {
    "A": [("alice", "role", "admin", "2020-01-01", "dev-a", "2026-01-01T01:00:00"),
          ("alice", "city", "Berlin", "2021-05-05", "dev-a", "2026-01-01T01:01:00")],
    "B": [("alice", "role", "engineer", "2022-01-01", "dev-b", "2026-01-01T02:00:00"),
          ("bob", "role", "viewer", "2020-03-03", "dev-b", "2026-01-01T02:01:00")],
    "C": [("bob", "city", "Paris", "2023-01-01", "dev-c", "2026-01-01T03:00:00")],
}


def _claims_of(graph):
    return sorted(c.cid for c in graph.store.load().values())


def test_merge_is_commutative_and_associative():
    base = tempfile.mkdtemp(prefix="cairn-merge-")
    chunks = {}
    for name, rows in ROWS.items():
        path = os.path.join(base, f"chunk_{name}")
        g = cairn.open(path, create=True)
        for r in rows:
            g.record(r[0], r[1], r[2], r[3], recorder=r[4], recorded_at=r[5])
        chunks[name] = path

    outcomes = []
    for order in itertools.permutations("ABC"):
        d = os.path.join(base, "merge_" + "".join(order))
        g = cairn.open(d, create=True)
        for name in order:
            g.merge(chunks[name])
        outcomes.append(_claims_of(g))

    assert len({tuple(o) for o in outcomes}) == 1, \
        "merge result depends on the order the chunks were merged in"


def test_merge_is_idempotent():
    base = tempfile.mkdtemp(prefix="cairn-idem-")
    src = os.path.join(base, "src")
    g0 = cairn.open(src, create=True)
    for r in ROWS["A"] + ROWS["B"]:
        g0.record(r[0], r[1], r[2], r[3], recorder=r[4], recorded_at=r[5])

    g = cairn.open(os.path.join(base, "dst"), create=True)
    first = g.merge(src)
    second = g.merge(src)
    assert first > 0 and second == 0
    assert g.verify() == []


def test_same_instant_has_one_spelling():
    base = tempfile.mkdtemp(prefix="cairn-tz-")
    a = cairn.open(os.path.join(base, "a"), create=True)
    b = cairn.open(os.path.join(base, "b"), create=True)
    a.record("x", "t", "v1", "2020-01-01T08:00:00+08:00", recorder="dev-a")
    b.record("x", "t", "v2", "2020-01-01T00:00:00Z", recorder="dev-b")
    ca = list(a.store.load().values())[0]
    cb = list(b.store.load().values())[0]
    assert ca.valid_from == cb.valid_from, \
        "the same instant canonicalised differently on two devices"
    assert a.verify() == [] and b.verify() == []


def test_compact_is_canonical():
    base = tempfile.mkdtemp(prefix="cairn-compact-")
    digests = []
    for order in ("AB", "BA"):
        d = os.path.join(base, order)
        g = cairn.open(d, create=True)
        for name in order:
            for r in ROWS[name]:
                g.record(r[0], r[1], r[2], r[3], recorder=r[4], recorded_at=r[5])
        g.compact()
        seg = os.path.join(g.store.segdir, "000001.cseg")
        with open(seg, "rb") as fh:
            digests.append(fh.read())
    assert digests[0] == digests[1], "compact output depends on insert order"
