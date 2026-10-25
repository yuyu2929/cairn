"""cairn.scope -- visibility policy.

Scope is a *convenience* layering, not a security boundary (THEORY.md 6):
the community edition stores one plaintext stream per database and
filters at read time, which stops accidents (a child opening the
"company" view sees nothing) but not adversaries.  The enterprise
edition adds real cryptographic separation: per-scope encrypted segments
(see enterprise.py) where the key itself is the boundary.

Ladder (higher sees lower, never the reverse):

    private < family < guild < company < public
"""

from __future__ import annotations

from typing import Iterable, Optional

SCOPE_LEVELS = ("private", "family", "guild", "company", "public")
DEFAULT_SCOPE = "private"

_RANK = {s: i for i, s in enumerate(SCOPE_LEVELS)}


class ScopeError(ValueError):
    pass


def rank(scope: str) -> int:
    try:
        return _RANK[scope]
    except KeyError:
        raise ScopeError(f"unknown scope {scope!r} "
                         f"(known: {', '.join(SCOPE_LEVELS)})") from None


def can_view(viewer: str, claim_scope: str) -> bool:
    """True if a viewer at `viewer` may read a claim scoped `claim_scope`."""
    return rank(viewer) >= rank(claim_scope)


def highest_visible(viewer: str, scopes: Iterable[str]) -> Optional[str]:
    """The most specific scope in `scopes` that `viewer` can read."""
    best: Optional[str] = None
    best_rank = -1
    for s in scopes:
        if can_view(viewer, s) and rank(s) > best_rank:
            best, best_rank = s, rank(s)
    return best


class ScopePolicy:
    """Per-recorder default and ceiling.

    A family database might say: kids record at "family" by default and
    may never write "company" claims; the household admin records at
    "company" (pay stubs) and "family" (appointments) but never leaks
    "private" to others.
    """

    def __init__(self, default_scope: str = DEFAULT_SCOPE,
                 allowed: Optional[Iterable[str]] = None):
        self.default_scope = default_scope
        self.allowed = tuple(allowed) if allowed else SCOPE_LEVELS
        for s in (self.default_scope,) + tuple(self.allowed):
            rank(s)  # validates

    def check(self, scope: str) -> str:
        """Validate a requested scope; fall back to the default."""
        if scope is None or scope == "":
            return self.default_scope
        if scope not in self.allowed:
            raise ScopeError(f"scope {scope!r} not permitted "
                             f"(allowed: {', '.join(self.allowed)})")
        return scope

    def view_for(self, viewer_scope: str) -> str:
        return self.check(viewer_scope)

    def __repr__(self) -> str:  # pragma: no cover
        return (f"ScopePolicy(default={self.default_scope!r}, "
                f"allowed={self.allowed})")


__all__ = ["SCOPE_LEVELS", "DEFAULT_SCOPE", "ScopePolicy", "ScopeError",
           "rank", "can_view", "highest_visible"]
