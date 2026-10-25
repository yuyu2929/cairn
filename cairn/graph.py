"""cairn.graph -- the queryable graph.

The store is a flat stream of claims; the graph is the read-time view.
Everything here is derived and can be rebuilt from the store at any time
(nothing is authoritative but the log -- THEORY.md 7).

Indexes (all in-memory, rebuilt on load):
  by_cid        cid -> Claim
  by_subject    subject -> [Claim]          (for "who/what" queries)
  by_predicate  predicate -> [Claim]
  by_valid      sorted [(valid_from, cid)]  (for "when" queries)
  inverted      token -> {cid}              (for "where/why" full-text)

Read-time resolution (THEORY.md 3): the current value of (subject,
predicate) is the candidate with the maximum (valid_from, recorded_at,
cid) among non-retracted claims.  History is never overwritten.
"""

from __future__ import annotations

import re
from bisect import bisect_right
import heapq
from typing import Any, Iterable, Optional

from .claim import (Claim, P_ALIAS, P_DISPUTE, P_RETRACT, P_VERIFY,
                    RESERVED_PREDICATES)
from .store import DEFAULT_SEGMENT_BYTES, Store, StoreError

# visibility ladder: a viewer at level N sees every claim whose scope
# rank is <= N.  (Convenience layering, not a security boundary -- see
# docs/THEORY.md 6 and docs/RESEARCH.md "scope is not encryption".)
SCOPE_LEVELS = ("private", "family", "guild", "company", "public")
_SCOPE_RANK = {s: i for i, s in enumerate(SCOPE_LEVELS)}

_TOKEN = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]+")


_CJK = re.compile(r"[\u3400-\u9fff\uF900-\uFAFF]")


def _tokens(v: Any) -> set[str]:
    """Lowercased search tokens from a claim value.

    ASCII/alnum runs are tokens; CJK has no word boundaries, so each Han
    character is emitted as its own token -- that way a query "三月" matches
    the phrase "三月雇佣了 Bob" rather than requiring an exact run.
    """
    out: set[str] = set()
    if isinstance(v, str):
        out.update(t.lower() for t in re.findall(r"[0-9A-Za-z]+", v))
        out.update(ch for ch in v if _CJK.match(ch))
    elif isinstance(v, (list, tuple)):
        for x in v:
            out |= _tokens(x)
    elif isinstance(v, dict):
        for x in v.values():
            out |= _tokens(x)
    elif v is not None and not isinstance(v, bool):
        out.add(str(v).lower())
    return out


class Resolution:
    """The outcome of reading one (subject, predicate) pair."""

    __slots__ = ("claim", "candidates", "retracted", "disputed_by", "verified_by")

    def __init__(self, claim, candidates, retracted, disputed_by, verified_by):
        self.claim = claim                # winner, or None if retracted/none
        self.candidates = candidates      # all candidates (incl. retracted), oldest first
        self.retracted = retracted        # bool
        self.disputed_by = disputed_by    # list of recorder ids
        self.verified_by = verified_by    # list of recorder ids

    @property
    def value(self) -> Any:
        return self.claim.value if self.claim else None

    @property
    def disputed(self) -> bool:
        return bool(self.disputed_by)

    @property
    def verified(self) -> bool:
        return bool(self.verified_by)

    def __repr__(self):  # pragma: no cover
        return (f"Resolution(value={self.value!r}, disputed={self.disputed}, "
                f"verified={self.verified}, candidates={len(self.candidates)})")


class GraphView:
    """A scope-filtered / time-filtered lens over a CairnGraph.

    Filtering happens at read time: the underlying claims are untouched,
    so a family member opening the "company" view sees exactly the claims
    their scope permits and nothing leaks.
    """

    def __init__(self, graph: "CairnGraph", scope: Optional[str] = None,
                 known_at: Optional[str] = None):
        self._g = graph
        self.scope = scope
        self.known_at = known_at

    # -- visibility -------------------------------------------------------- #
    def _visible(self, c: Claim) -> bool:
        if self.scope is not None:
            rank = _SCOPE_RANK.get(c.scope)
            if rank is None or rank > _SCOPE_RANK[self.scope]:
                return False
        if self.known_at is not None and c.recorded_at > self.known_at:
            return False
        return True

    def _view_claims(self, claims: Iterable[Claim]) -> list[Claim]:
        return [c for c in claims if self._visible(c)]

    # -- reads ------------------------------------------------------------- #
    def subjects(self) -> set[str]:
        return {c.subject for c in self._view_claims(self._g.claims())
                if not c.is_operation()}

    def resolve(self, subject: str, predicate: str) -> Resolution:
        """Read-time resolution: the current value of (subject, predicate)."""
        cands = [c for c in self._g.by_subject.get(subject, ())
                 if c.predicate == predicate and not c.is_operation()]
        cands = self._view_claims(cands)
        cands.sort(key=lambda c: (c.valid_from, c.recorded_at, c.cid))
        disputed_by: list[str] = []
        verified_by: list[str] = []
        live: list[Claim] = []
        for c in cands:
            ops = self._g.operations_on(c.cid)
            ops = self._view_claims(ops)
            if any(o.predicate == P_RETRACT for o in ops):
                continue
            live.append(c)
            disputed_by.extend(o.recorder for o in ops if o.predicate == P_DISPUTE)
            verified_by.extend(o.recorder for o in ops if o.predicate == P_VERIFY)
        winner = live[-1] if live else None
        # retracted == True only when every candidate was withdrawn, i.e.
        # the attribute currently has no valid value (THEORY.md 3).
        retracted = bool(cands) and not live
        return Resolution(winner, cands, retracted, disputed_by, verified_by)

    def value(self, subject: str, predicate: str, default: Any = None) -> Any:
        r = self.resolve(subject, predicate)
        return default if r.claim is None else r.value

    def history(self, subject: str, predicate: Optional[str] = None) -> list[Claim]:
        """Every non-operation claim about a subject, oldest first."""
        out = [c for c in self._view_claims(self._g.by_subject.get(subject, ()))
               if not c.is_operation()
               and (predicate is None or c.predicate == predicate)]
        out.sort(key=lambda c: (c.valid_from, c.recorded_at, c.cid))
        return out

    def timeline(self, subject: str) -> list[dict]:
        """Full bitemporal history as dicts with live/disputed/retracted flags."""
        rows = []
        for c in self.history(subject):
            ops = self._view_claims(self._g.operations_on(c.cid))
            rows.append({
                "claim": c,
                "retracted": any(o.predicate == P_RETRACT for o in ops),
                "disputed_by": [o.recorder for o in ops
                                if o.predicate == P_DISPUTE],
                "verified_by": [o.recorder for o in ops
                                if o.predicate == P_VERIFY],
            })
        return rows

    def links(self, subject: str, predicate: Optional[str] = None
              ) -> list[tuple[str, Any, str]]:
        """Outgoing edges: (predicate, value, cid), resolved-and-live only."""
        out = []
        preds = [predicate] if predicate else sorted(
            {c.predicate for c in self._g.by_subject.get(subject, ())
             if not c.is_operation()})
        for p in preds:
            r = self.resolve(subject, p)
            if r.claim is not None:
                out.append((p, r.value, r.claim.cid))
        return out

    def traverse(self, start: str, max_depth: int = 2,
                 predicate: Optional[str] = None) -> dict[str, set[str]]:
        """Breadth-first walk over resolved edges.

        Returns {node: {neighbour, ...}} for every node within max_depth.
        Cycles terminate because visited nodes are not re-expanded.
        """
        graph: dict[str, set[str]] = {}
        frontier = [start]
        seen = {start}
        for _ in range(max_depth):
            nxt = []
            for node in frontier:
                nbrs = graph.setdefault(node, set())
                for p, v, _cid in self.links(node, predicate):
                    if isinstance(v, str) and v in self._g.by_subject:
                        nbrs.add(v)
                        if v not in seen:
                            seen.add(v)
                            nxt.append(v)
            frontier = nxt
            if not frontier:
                break
        return graph

    def search(self, query: str, limit: int = 50) -> list[Claim]:
        """Token AND-search over values (via the inverted index)."""
        terms = _tokens(query)
        if not terms:
            return []
        inv = self._g.inverted
        sets = [inv.get(t, set()) for t in terms]
        if any(not s for s in sets):
            return []
        hits = set.intersection(*sets)
        out = self._view_claims(self._g.by_cid[cid] for cid in hits)
        key = lambda c: (c.recorded_at, c.cid)  # newest first
        if len(out) <= limit:
            out.sort(key=key, reverse=True)
            return out
        # Avoid sorting the whole hit set when only the top-k are wanted.
        return heapq.nlargest(limit, out, key=key)

    def at_time(self, valid_at: str) -> list[Claim]:
        """Claims whose validity window contains valid_at, live only.

        Uses the valid_from index so cost is O(log n + k) for k claims
        starting before valid_at, not a full scan.
        """
        self._g._ensure_valid_sorted()
        idx = self._g._valid_sorted
        keys = [vf for vf, _ in idx]
        upto = bisect_right(keys, valid_at)
        out = []
        for _vf, cid in idx[:upto]:
            c = self._g.by_cid[cid]
            if not self._visible(c) or c.is_operation():
                continue
            if c.valid_to is not None and valid_at >= c.valid_to:
                continue
            if any(o.predicate == P_RETRACT
                   for o in self._view_claims(self._g.operations_on(c.cid))):
                continue
            out.append(c)
        return out

    def stats(self) -> dict:
        return {
            "claims": len(self._view_claims(self._g.claims())),
            "subjects": len(self.subjects()),
            "scope": self.scope,
        }


class CairnGraph:
    """A database opened for reading and writing."""

    def __init__(self, path: str, create: bool = False,
                 segment_bytes: int = DEFAULT_SEGMENT_BYTES):
        self.store = Store.open(path, create=create, segment_bytes=segment_bytes)
        self.path = self.store.root
        self._claims: dict[str, Claim] = {}
        self.by_subject: dict[str, list[Claim]] = {}
        self.by_predicate: dict[str, list[Claim]] = {}
        self._valid_sorted: list[tuple[str, str]] = []   # (valid_from, cid)
        self._valid_dirty: bool = False  # True when _valid_sorted needs a sort
        self.inverted: dict[str, set[str]] = {}
        self._ops_by_target: dict[str, list[Claim]] = {}
        self.reload()

    # ------------------------------------------------------------------ #
    # index maintenance
    # ------------------------------------------------------------------ #

    def reload(self) -> None:
        """Rebuild all in-memory indexes from the store."""
        claims = self.store.load()
        self._claims = claims
        self.by_subject = {}
        self.by_predicate = {}
        self.inverted = {}
        self._ops_by_target = {}
        for cid in sorted(claims):
            c = claims[cid]
            if c.is_operation():
                # operations key on the claim they act on (subject = cid)
                self._ops_by_target.setdefault(c.subject, []).append(c)
                continue
            self.by_subject.setdefault(c.subject, []).append(c)
            self.by_predicate.setdefault(c.predicate, []).append(c)
            for tok in _tokens(c.value):
                self.inverted.setdefault(tok, set()).add(cid)
        self._valid_sorted = sorted(
            (c.valid_from, cid) for cid, c in claims.items()
            if not c.is_operation())
        self._valid_dirty = False

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #

    def record(self, subject: str, predicate: str, value: Any,
               valid_from: Any, recorder: str, recorded_at: Any = None,
               valid_to: Any = None, source: str = "",
               scope: str = "private") -> Optional[Claim]:
        """Assert a fact.  Returns the Claim, or None if it was a duplicate.

        Idempotence is by design: recording the identical fact twice
        (same recorder, same recorded_at, same source) yields the same cid
        and the second write is skipped by the store.
        """
        if predicate in RESERVED_PREDICATES:
            raise ValueError(
                f"predicate {predicate!r} is reserved for stream operations; "
                f"use retract()/dispute()/verify()/alias()")
        claim = Claim.create(subject, predicate, value, valid_from, recorder,
                             recorded_at=recorded_at, valid_to=valid_to,
                             source=source, scope=scope)
        if self.store.append(claim):
            self._index_add(claim)
            return claim
        return None

    def _index_add(self, c: Claim) -> None:
        self._claims[c.cid] = c
        if c.is_operation():
            self._ops_by_target.setdefault(c.subject, []).append(c)
            return
        self.by_subject.setdefault(c.subject, []).append(c)
        self.by_predicate.setdefault(c.predicate, []).append(c)
        for tok in _tokens(c.value):
            self.inverted.setdefault(tok, set()).add(c.cid)
        # Append-only valid-time index; the costly sort is deferred to the
        # next query (at_time) via _ensure_valid_sorted, so bulk writes stay
        # O(1) per claim instead of O(n log n) per claim.
        self._valid_sorted.append((c.valid_from, c.cid))
        self._valid_dirty = True

    def _ensure_valid_sorted(self) -> None:
        """Sort the valid-time index on demand; no-op if already sorted."""
        if self._valid_dirty:
            self._valid_sorted.sort()
            self._valid_dirty = False

    # ---- stream operations ---------------------------------------------- #

    def _operate(self, target_cid: str, predicate: str, value: Any,
                 recorder: str, recorded_at: Any = None,
                 source: str = "", scope: str = "private") -> Optional[Claim]:
        if target_cid not in self._claims:
            raise KeyError(f"unknown claim: {target_cid}")
        claim = Claim.create(target_cid, predicate, value,
                             valid_from=self._claims[target_cid].valid_from,
                             recorder=recorder, recorded_at=recorded_at,
                             source=source, scope=scope)
        if self.store.append(claim):
            self._index_add(claim)
            return claim
        return None

    def retract(self, target_cid: str, recorder: str, reason: str = "",
                **kw) -> Optional[Claim]:
        """Withdraw a claim (the fact never happened / was recorded in error)."""
        return self._operate(target_cid, P_RETRACT, reason, recorder, **kw)

    def dispute(self, target_cid: str, recorder: str, reason: str = "",
                **kw) -> Optional[Claim]:
        """Contest a claim's value.  The claim stays; a flag attaches."""
        return self._operate(target_cid, P_DISPUTE, reason, recorder, **kw)

    def endorse(self, target_cid: str, recorder: str, note: str = "",
               **kw) -> Optional[Claim]:
        """Attest a claim (e.g. a second family member confirms it)."""
        return self._operate(target_cid, P_VERIFY, note, recorder, **kw)

    def alias(self, subject: str, other_id: str, recorder: str,
              **kw) -> Optional[Claim]:
        """Declare that two node ids are the same entity.

        Alias resolution happens at read time: an alias (A, alias, B)
        makes every lookup of A transparently consult B's claims too
        (one hop; chains resolve transitively with a visited guard).
        """
        if not other_id:
            raise ValueError("alias target must be non-empty")
        claim = Claim.create(subject, P_ALIAS, other_id, valid_from=0,
                             recorder=recorder, **kw)
        if self.store.append(claim):
            self._index_add(claim)
            return claim
        return None

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #

    def claims(self) -> list[Claim]:
        return list(self._claims.values())

    def by_cid_map(self) -> dict[str, Claim]:
        return dict(self._claims)

    @property
    def by_cid(self) -> dict[str, Claim]:
        return self._claims

    def operations_on(self, cid: str) -> list[Claim]:
        return list(self._ops_by_target.get(cid, ()))

    def view(self, scope: Optional[str] = None,
             known_at: Optional[str] = None) -> GraphView:
        if scope is not None and scope not in _SCOPE_RANK:
            raise ValueError(f"unknown scope: {scope!r} "
                             f"(known: {', '.join(SCOPE_LEVELS)})")
        return GraphView(self, scope, known_at)

    def known_at(self, when: Any) -> GraphView:
        """Bitemporal read: the database as it was known at `when`."""
        from .claim import norm_time
        return GraphView(self, None, norm_time(when))

    # ---- alias-aware resolution ----------------------------------------- #

    def _alias_chain(self, subject: str) -> list[str]:
        """subject plus every id reachable through alias claims (guarded)."""
        chain = [subject]
        seen = {subject}
        queue = [subject]
        while queue:
            node = queue.pop(0)
            for c in self.by_subject.get(node, ()):
                if c.predicate == P_ALIAS and isinstance(c.value, str):
                    if c.value not in seen:
                        seen.add(c.value)
                        chain.append(c.value)
                        queue.append(c.value)
        return chain

    def resolve(self, subject: str, predicate: str) -> Resolution:
        """Alias-aware resolution: consults every id the subject is known by."""
        merged: dict[str, Claim] = {}
        for node in self._alias_chain(subject):
            for c in self.by_subject.get(node, ()):
                if c.predicate == predicate and not c.is_operation():
                    merged[c.cid] = c
        cands = sorted(merged.values(),
                       key=lambda c: (c.valid_from, c.recorded_at, c.cid))
        live: list[Claim] = []
        disputed_by: list[str] = []
        verified_by: list[str] = []
        for c in cands:
            ops = self.operations_on(c.cid)
            if any(o.predicate == P_RETRACT for o in ops):
                continue
            live.append(c)
            disputed_by.extend(o.recorder for o in ops
                               if o.predicate == P_DISPUTE)
            verified_by.extend(o.recorder for o in ops
                               if o.predicate == P_VERIFY)
        # retracted == True only when every candidate was withdrawn.
        retracted = bool(cands) and not live
        return Resolution(live[-1] if live else None, cands, retracted,
                          disputed_by, verified_by)

    def search(self, query: str, limit: int = 50) -> list[Claim]:
        return self.view().search(query, limit)

    def value(self, subject: str, predicate: str, default: Any = None) -> Any:
        """Convenience: current value in the default (private) view."""
        return self.view().value(subject, predicate, default)

    def resolve(self, subject: str, predicate: str) -> Resolution:
        """Convenience: alias-aware resolution in the default view."""
        return self.view().resolve(subject, predicate)

    def timeline(self, subject: str) -> list[dict]:
        rows = []
        for node in self._alias_chain(subject):
            rows.extend(self.view().timeline(node))
        rows.sort(key=lambda r: (r["claim"].valid_from,
                                 r["claim"].recorded_at, r["claim"].cid))
        return rows

    # ------------------------------------------------------------------ #
    # merging
    # ------------------------------------------------------------------ #

    def merge(self, other_path: str) -> int:
        """Union another database into this one; returns claims imported."""
        n = self.store.merge_dir(other_path)
        if n:
            self.reload()
        return n

    # ------------------------------------------------------------------ #
    # maintenance / io
    # ------------------------------------------------------------------ #

    def compact(self) -> int:
        n = self.store.compact()
        self.reload()
        return n

    def count(self) -> int:
        """Number of distinct claims (assertions + operations) in the log."""
        return self.store.count()

    def verify(self) -> list[str]:
        return self.store.verify()

    def stats(self) -> dict:
        ops = sum(len(v) for v in self._ops_by_target.values())
        return {
            **self.store.stats(),
            "subjects": len(self.by_subject),
            "predicates": len(self.by_predicate),
            "operations": ops,
        }

    def close(self) -> None:
        pass  # no handles held; everything is flushed on append


__all__ = ["CairnGraph", "GraphView", "Resolution", "SCOPE_LEVELS",
           "StoreError"]
