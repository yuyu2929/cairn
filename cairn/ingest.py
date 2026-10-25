"""cairn.ingest -- bulk ingestion with a validation gate.

Bots (game servers, scrapers, household devices) produce claims in bulk,
and bulk is exactly where garbage gets in.  The ingest path therefore
separates three stages:

  propose()  -> writes to <db>/inbox.jsonl (NOT the claim stream yet)
  review()   -> AI or human approves/rejects each proposal
  commit()   -> approved proposals become real claims

Auto-commit mode (auto=True) runs the same validation but skips the
inbox; that is the trusted-device path (your own game server writing its
own logs).  Untrusted input always goes through the inbox.

Validation rules (fail closed):
  - subject / predicate: non-empty strings, NFC
  - predicate must not be a reserved stream operation
  - value must be JSON-serializable (no NaN/Inf, no arbitrary objects)
  - valid_from must be parseable; valid_to (if given) must be >= valid_from
"""

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from .claim import Claim, RESERVED_PREDICATES, SCOPE_DEFAULT, norm_time

INBOX_NAME = "inbox.jsonl"


@dataclass
class Proposal:
    """One candidate fact, pre-validation, pre-hash."""
    subject: str
    predicate: str
    value: Any
    valid_from: Any
    recorder: str
    recorded_at: Any = None
    valid_to: Any = None
    source: str = ""
    scope: str = SCOPE_DEFAULT
    note: str = ""

    def to_json(self) -> str:
        d = {k: getattr(self, k) for k in
             ("subject", "predicate", "value", "valid_from", "recorder",
              "recorded_at", "valid_to", "source", "scope", "note")}
        return json.dumps(d, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, line: str) -> "Proposal":
        d = json.loads(line)
        known = {f for f in cls.__dataclass_fields__}
        extra = set(d) - known
        if extra:
            raise ValueError(f"proposal has unknown fields: {sorted(extra)}")
        return cls(**d)


@dataclass
class IngestReport:
    accepted: list[Claim] = field(default_factory=list)
    duplicates: int = 0
    rejected: list[tuple[dict, str]] = field(default_factory=list)  # (record, reason)

    @property
    def ok(self) -> bool:
        return not self.rejected

    def summary(self) -> str:
        return (f"{len(self.accepted)} accepted, {self.duplicates} duplicate, "
                f"{len(self.rejected)} rejected")


def validate(p: Proposal) -> Optional[str]:
    """Return None if the proposal is a legal claim, else the reason."""
    for name in ("subject", "predicate"):
        v = getattr(p, name)
        if not isinstance(v, str) or not unicodedata.normalize("NFC", v).strip():
            return f"{name} must be a non-empty string"
    if p.predicate in RESERVED_PREDICATES:
        return f"predicate {p.predicate!r} is reserved"
    try:
        json.dumps(p.value, allow_nan=False)
    except (TypeError, ValueError):
        return "value is not JSON-serializable (NaN/Inf/objects rejected)"
    try:
        vf = norm_time(p.valid_from)
        if p.valid_to is not None:
            vt = norm_time(p.valid_to)
            if vt < vf:
                return f"valid_to {vt} precedes valid_from {vf}"
    except ValueError as e:
        return f"bad time: {e}"
    if not isinstance(p.recorder, str) or not p.recorder.strip():
        return "recorder must be a non-empty string"
    return None


def to_claim(p: Proposal) -> Claim:
    return Claim.create(
        p.subject, p.predicate, p.value, p.valid_from, p.recorder,
        recorded_at=p.recorded_at, valid_to=p.valid_to,
        source=p.source, scope=p.scope or SCOPE_DEFAULT)


class Ingestor:
    """The validation gate attached to one database."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        self.inbox_path = os.path.join(self.db_path, INBOX_NAME)

    # -- stage 1: propose (writes inbox, never the stream) ---------------- #
    def propose(self, proposals: Iterable[Proposal]) -> IngestReport:
        """Stage-1 ingest: validate and park in the inbox for review."""
        report = IngestReport()
        with open(self.inbox_path, "a", encoding="utf-8") as f:
            for p in proposals:
                why = validate(p)
                if why:
                    report.rejected.append((_as_dict(p), why))
                    continue
                f.write(p.to_json() + "\n")
                report.accepted.append(to_claim(p))
        return report

    def inbox(self) -> list[Proposal]:
        if not os.path.isfile(self.inbox_path):
            return []
        out = []
        with open(self.inbox_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(Proposal.from_json(line))
        return out

    def clear_inbox(self) -> int:
        if not os.path.isfile(self.inbox_path):
            return 0
        with open(self.inbox_path, encoding="utf-8") as f:
            n = sum(1 for line in f if line.strip())
        os.unlink(self.inbox_path)
        return n

    # -- stage 2: commit (inbox -> claim stream) -------------------------- #
    def commit(self, graph, approvals: Optional[Iterable[int]] = None
               ) -> IngestReport:
        """Move inbox proposals into the claim stream.

        `approvals` (if given) selects which inbox entries (by index) are
        approved; everything else stays parked.  Without it, everything in
        the inbox is committed (the AI-gate-approved path).
        """
        proposals = self.inbox()
        report = IngestReport()
        if approvals is not None:
            allow = set(approvals)
            selected = [p for i, p in enumerate(proposals) if i in allow]
        else:
            selected = proposals
        for p in selected:
            why = validate(p)
            if why:
                report.rejected.append((_as_dict(p), why))
                continue
            claim = graph.record(
                p.subject, p.predicate, p.value, p.valid_from, p.recorder,
                recorded_at=p.recorded_at, valid_to=p.valid_to,
                source=p.source, scope=p.scope)
            if claim is None:
                report.duplicates += 1
            else:
                report.accepted.append(claim)
        if not report.rejected and approvals is None:
            self.clear_inbox()
        return report

    # -- fast path: validated auto-commit --------------------------------- #
    def auto(self, graph, records: Iterable[dict | Proposal],
             scope: str = SCOPE_DEFAULT) -> IngestReport:
        """Trusted-device path: validate then commit in one pass."""
        report = IngestReport()
        for rec in records:
            p = rec if isinstance(rec, Proposal) else _dict_to_proposal(rec, scope)
            why = validate(p)
            if why:
                report.rejected.append((_as_dict(p), why))
                continue
            claim = graph.record(
                p.subject, p.predicate, p.value, p.valid_from, p.recorder,
                recorded_at=p.recorded_at, valid_to=p.valid_to,
                source=p.source, scope=p.scope)
            if claim is None:
                report.duplicates += 1
            else:
                report.accepted.append(claim)
        return report


def _as_dict(p: Proposal) -> dict:
    return {f: getattr(p, f) for f in p.__dataclass_fields__}


def _dict_to_proposal(d: dict, default_scope: str) -> Proposal:
    missing = [k for k in ("subject", "predicate", "value", "valid_from",
                           "recorder") if k not in d]
    if missing:
        raise ValueError(f"record missing fields: {missing}")
    d = dict(d)
    d.setdefault("scope", default_scope)
    d.setdefault("recorded_at", None)
    d.setdefault("valid_to", None)
    d.setdefault("source", "")
    d.setdefault("note", "")
    return Proposal(**d)


__all__ = ["Proposal", "IngestReport", "Ingestor", "validate", "to_claim"]
