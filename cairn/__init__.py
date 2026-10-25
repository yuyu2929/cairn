"""cairn -- local-first shared memory.

Everything is a claim; the database is a directory; merging is a set
union; conflicts are kept, not resolved away.  See docs/THEORY.md.

    import cairn
    g = cairn.open("~/my-wiki", create=True)
    g.record("alice", "role", "admin", "2026-01-01", recorder="alice")
    g.view("family").value("alice", "role")   # -> 'admin'
"""

from __future__ import annotations

__version__ = "1.0.1"

from .claim import (Claim, RESERVED_PREDICATES, SCOPE_DEFAULT, content_id,
                    norm_time)
from .crypto import (chacha20poly1305_decrypt, chacha20poly1305_encrypt,
                     ed25519_keypair, ed25519_sign, ed25519_verify)
from .enterprise import (EnterpriseDB, License, LicenseError, issue_license,
                         load_license, save_license, vendor_keypair)
from .graph import CairnGraph, GraphView, Resolution, SCOPE_LEVELS
from .ingest import Ingestor, Proposal
from .merge import diff, equal, export, import_, merge_into
from .scope import ScopePolicy
from .store import Store, StoreError


def open(path: str, create: bool = False, **kw) -> CairnGraph:
    """Open (or create) a cairn database. The one entry point you need."""
    return CairnGraph(path, create=create, **kw)


__all__ = [
    "open", "CairnGraph", "GraphView", "Resolution", "Claim", "Store",
    "StoreError", "ScopePolicy", "SCOPE_LEVELS", "SCOPE_DEFAULT",
    "Ingestor", "Proposal", "diff", "equal", "export", "import_",
    "merge_into", "EnterpriseDB", "License", "LicenseError", "issue_license",
    "load_license", "save_license", "vendor_keypair", "ed25519_keypair",
    "ed25519_sign", "ed25519_verify", "chacha20poly1305_encrypt",
    "chacha20poly1305_decrypt", "content_id", "norm_time",
    "RESERVED_PREDICATES", "__version__",
]
