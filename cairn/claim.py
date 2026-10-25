"""Claim: the single atom of Cairn.

A claim is a nine-tuple (subject, predicate, value, valid_from, valid_to,
recorder, recorded_at, source, scope). Its identity is the hash of its
CANONICAL encoding: two recordings of the same fact -- by different
recorders, on different devices, at different times -- produce the same
cid, which makes recording idempotent and merging a pure set union
(THEORY.md sections 1-3).

Canonical encoding rules (determinism is load-bearing, see THEORY.md 8):
  - fixed key set, keys sorted, no whitespace, ASCII-escaped;
  - times normalized to ISO-8601 via datetime.fromisoformat().isoformat();
  - floats via repr (shortest round-trip); -0.0 normalized to 0.0;
  - NaN/Infinity rejected (not representable);
  - strings are NFC-normalized (unicodedata) before encoding.
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

CID_LEN = 16          # blake2b digest bytes -> 32 hex chars
_FIELDS = ("subject", "predicate", "value", "valid_from", "valid_to",
           "recorder", "recorded_at", "source", "scope")

SCOPE_PRIVATE = "private"
SCOPE_DEFAULT = SCOPE_PRIVATE

# predicates reserved for stream-level operations
P_RETRACT = "retract"
P_DISPUTE = "dispute"
P_VERIFY = "verify"
P_ALIAS = "alias"          # (subject, alias, "other node id") for identity merge
RESERVED_PREDICATES = frozenset({P_RETRACT, P_DISPUTE, P_VERIFY, P_ALIAS})


def norm_time(t: Any) -> str:
    """Normalize a time to a canonical ISO-8601 string.

    Accepts datetime, unix int/float (seconds), or ISO string. Comparison
    of normalized ISO strings is lexicographic and matches chronological
    order for the ISO-8601 profile (fixed width, prefix rule).

    Timezone-aware inputs are converted to UTC and stored without an offset.
    Without that step the same instant had two canonical spellings
    (``...+08:00`` and the equivalent ``...Z``), so two devices could order
    the same pair of claims differently -- which breaks the resolution rule
    this database is built on.
    """
    if isinstance(t, datetime):
        dt = t
    elif isinstance(t, bool):
        raise ValueError("bool is not a time")
    elif isinstance(t, (int, float)):
        dt = datetime.fromtimestamp(float(t), timezone.utc)
    elif isinstance(t, str):
        s = unicodedata.normalize("NFC", t.strip())
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as e:
            raise ValueError(f"bad ISO-8601 time: {t!r}") from e
    else:
        raise ValueError(f"unsupported time type: {type(t).__name__}")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _norm_str(s: Any) -> str:
    if not isinstance(s, str):
        raise ValueError(f"expected string, got {type(s).__name__}")
    return unicodedata.normalize("NFC", s)


def _norm_value(v: Any) -> Any:
    """Recursively normalize a JSON-able value for canonical encoding."""
    if isinstance(v, str):
        return unicodedata.normalize("NFC", v)
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise ValueError("NaN/Infinity are not canonicalizable")
        return 0.0 if v == 0.0 else v      # kills -0.0
    if isinstance(v, int):
        return v
    if isinstance(v, (list, tuple)):
        return [_norm_value(x) for x in v]
    if isinstance(v, dict):
        return {_norm_str(k): _norm_value(x) for k, x in sorted(v.items())}
    raise ValueError(f"unsupported value type: {type(v).__name__}")


def canonical_bytes(claim_dict: dict) -> bytes:
    """The canonical byte encoding of a claim dict (THEORY.md 2)."""
    obj = {
        "subject": _norm_str(claim_dict["subject"]),
        "predicate": _norm_str(claim_dict["predicate"]),
        "value": _norm_value(claim_dict["value"]),
        "valid_from": norm_time(claim_dict["valid_from"]),
        "valid_to": (None if claim_dict["valid_to"] is None
                     else norm_time(claim_dict["valid_to"])),
        "recorder": _norm_str(claim_dict["recorder"]),
        "recorded_at": norm_time(claim_dict["recorded_at"]),
        "source": _norm_str(claim_dict.get("source", "")),
        "scope": _norm_str(claim_dict.get("scope", SCOPE_DEFAULT)),
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def content_id(canonical: bytes) -> str:
    return hashlib.blake2b(canonical, digest_size=CID_LEN).hexdigest()


@dataclass(frozen=True)
class Claim:
    """One immutable assertion. cid is derived, never stored separately."""
    subject: str
    predicate: str
    value: Any
    valid_from: str
    valid_to: Optional[str]
    recorder: str
    recorded_at: str
    source: str = ""
    scope: str = SCOPE_DEFAULT

    # ---- construction ----------------------------------------------------
    def __post_init__(self):
        # Compute the content id exactly once.  cid is read on every store
        # append (duplicate check), every index insert, and as the sort key
        # in search/sort -- making it a recomputed property turned those
        # paths quadratic.  A frozen dataclass forbids normal assignment, so
        # we set the slot directly.
        object.__setattr__(self, "cid", content_id(self.canonical()))

    @classmethod
    def create(cls, subject: str, predicate: str, value: Any,
               valid_from: Any, recorder: str, recorded_at: Any = None,
               valid_to: Any = None, source: str = "",
               scope: str = SCOPE_DEFAULT) -> "Claim":
        if recorded_at is None:
            recorded_at = datetime.now()
        if not subject or not predicate:
            raise ValueError("subject and predicate must be non-empty")
        return cls(
            subject=_norm_str(subject),
            predicate=_norm_str(predicate),
            value=_norm_value(value),
            valid_from=norm_time(valid_from),
            valid_to=None if valid_to is None else norm_time(valid_to),
            recorder=_norm_str(recorder),
            recorded_at=norm_time(recorded_at),
            source=_norm_str(source),
            scope=_norm_str(scope) or SCOPE_DEFAULT,
        )

    # ---- encoding --------------------------------------------------------
    def canonical(self) -> bytes:
        return canonical_bytes(self.to_dict())

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in _FIELDS}

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        missing = [k for k in _FIELDS if k not in d]
        if missing:
            raise ValueError(f"claim missing fields: {missing}")
        extra = set(d) - set(_FIELDS)
        if extra:
            raise ValueError(f"claim has unknown fields: {sorted(extra)}")
        return cls.create(
            subject=d["subject"], predicate=d["predicate"], value=d["value"],
            valid_from=d["valid_from"], recorder=d["recorder"],
            recorded_at=d["recorded_at"], valid_to=d["valid_to"],
            source=d.get("source", ""), scope=d.get("scope", SCOPE_DEFAULT))

    # ---- JSONL line encoding --------------------------------------------
    def to_json(self) -> str:
        """Canonical single-line JSON (the .cseg wire format)."""
        return canonical_bytes(self.to_dict()).decode("ascii")

    @classmethod
    def from_json(cls, line: str) -> "Claim":
        """Parse one canonical JSONL line. Re-normalizes through create(),
        so a hand-edited line with e.g. non-NFC text still hashes to the
        cid it deserves (tamper-evidence is a property of canonical bytes,
        not of the bytes on disk)."""
        return cls.from_dict(json.loads(line))

    # ---- derived ---------------------------------------------------------
    def is_operation(self) -> bool:
        """retract/dispute/verify/alias operate on other claims, not facts."""
        return self.predicate in RESERVED_PREDICATES

    def __str__(self) -> str:  # pragma: no cover - display only
        return (f"{self.subject} --{self.predicate}--> {self.value!r} "
                f"[{self.valid_from}..{self.valid_to or 'now'}] "
                f"by {self.recorder} ({self.scope})")
