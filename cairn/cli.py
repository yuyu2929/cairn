"""cairn -- a local-first shared memory database.

Usage: cairn <command> [options]

Commands:
  init       create a database
  record     assert a fact
  get        read the current value of a fact
  history    full bitemporal timeline of a subject
  search     full-text search over values
  nodes      list subjects
  links      outgoing edges of a subject
  traverse   walk the graph from a subject
  retract / dispute / verify   stream operations on a claim
  merge      union another database into this one
  export / import    portable bundles (no server needed)
  compact    rewrite the log in canonical form
  verify     full integrity check
  stats      database statistics
  serve      local web UI (http://localhost:8777)
  enterprise license / encrypt / decrypt (paid edition)
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .claim import Claim, P_ALIAS, norm_time
from .enterprise import (EnterpriseDB, LicenseError, issue_license,
                         load_license, save_license, vendor_keypair)
from .graph import CairnGraph
from .ingest import Ingestor, Proposal
from .merge import diff, equal, export, import_
from .store import Store, StoreError


def _parse_value(s: str):
    """Values arrive as text; accept JSON, else treat as a plain string."""
    try:
        v = json.loads(s)
    except (ValueError, TypeError):
        return s
    return v


def _fmt_resolution(r) -> str:
    if r.claim is None:
        return "(no live value)" if not r.retracted else "(retracted)"
    out = f"{r.value!r}"
    flags = []
    if r.disputed:
        flags.append(f"disputed by {', '.join(sorted(set(r.disputed_by)))}")
    if r.verified:
        flags.append(f"verified by {', '.join(sorted(set(r.verified_by)))}")
    if flags:
        out += "  [" + "; ".join(flags) + "]"
    return out


def _cmd_init(a) -> int:
    Store.create(a.db)
    print(f"created cairn database at {a.db}")
    return 0


def _cmd_record(a) -> int:
    g = CairnGraph(a.db)
    claim = g.record(a.subject, a.predicate, _parse_value(a.value),
                     a.valid_from or "now", a.recorder, valid_to=a.valid_to,
                     source=a.source, scope=a.scope)
    if claim is None:
        print("duplicate: that exact claim is already in the database")
        return 0
    print(f"recorded {claim.cid}")
    return 0


def _cmd_get(a) -> int:
    g = CairnGraph(a.db)
    v = g.view(a.scope)
    print(_fmt_resolution(v.resolve(a.subject, a.predicate)))
    return 0


def _cmd_history(a) -> int:
    g = CairnGraph(a.db)
    rows = g.view(a.scope).timeline(a.subject)
    if not rows:
        print(f"no claims about {a.subject!r}")
        return 0
    for row in rows:
        c = row["claim"]
        flags = []
        if row["retracted"]:
            flags.append("RETRACTED")
        if row["disputed_by"]:
            flags.append("disputed:" + ",".join(sorted(set(row["disputed_by"]))))
        if row["verified_by"]:
            flags.append("verified:" + ",".join(sorted(set(row["verified_by"]))))
        tag = ("[" + " ".join(flags) + "]") if flags else ""
        print(f"{c.valid_from}  {c.predicate} = {c.value!r}  "
              f"by {c.recorder} ({c.scope}) {tag}")
        print(f"    cid={c.cid} recorded_at={c.recorded_at} src={c.source!r}")
    return 0


def _cmd_search(a) -> int:
    g = CairnGraph(a.db)
    hits = g.view(a.scope).search(a.query, limit=a.limit)
    if not hits:
        print("no matches")
        return 0
    for c in hits:
        print(f"{c.subject} --{c.predicate}--> {c.value!r}  "
              f"[{c.valid_from}..{c.valid_to or 'now'}] ({c.scope})")
        print(f"    cid={c.cid}")
    print(f"{len(hits)} match(es)")
    return 0


def _cmd_nodes(a) -> int:
    g = CairnGraph(a.db)
    for s in sorted(g.view(a.scope).subjects()):
        print(s)
    return 0


def _cmd_links(a) -> int:
    g = CairnGraph(a.db)
    for p, v, cid in g.view(a.scope).links(a.subject):
        print(f"{a.subject} --{p}--> {v!r}   cid={cid}")
    return 0


def _cmd_traverse(a) -> int:
    g = CairnGraph(a.db)
    graph = g.view(a.scope).traverse(a.subject, max_depth=a.depth)
    for node in sorted(graph):
        print(f"{node} -> {sorted(graph[node])}")
    return 0


def _cmd_operate(a) -> int:
    g = CairnGraph(a.db)
    fn = {"retract": g.retract, "dispute": g.dispute,
          "endorse": g.endorse}[a.command]
    claim = fn(a.cid, a.recorder, a.reason or "", scope=a.scope)
    if claim is None:
        print("duplicate operation")
        return 0
    print(f"{a.command}ed {a.cid} -> {claim.cid}")
    return 0


def _cmd_alias(a) -> int:
    g = CairnGraph(a.db)
    claim = g.alias(a.subject, a.other, a.recorder, scope=a.scope)
    print(f"aliased {a.subject} = {a.other} -> {claim.cid if claim else 'dup'}")
    return 0


def _cmd_merge(a) -> int:
    g = CairnGraph(a.db)
    n = g.merge(a.other)
    print(f"imported {n} new claim(s)")
    return 0


def _cmd_export(a) -> int:
    m = export(a.db, a.dest)
    print(f"exported {m['count']} claim(s) to {a.dest}")
    return 0


def _cmd_import(a) -> int:
    n = import_(a.db, a.bundle)
    print(f"imported {n} new claim(s) from {a.bundle}")
    return 0


def _cmd_compact(a) -> int:
    g = CairnGraph(a.db)
    n = g.compact()
    print(f"compacted to {n} claim(s) in canonical form")
    return 0


def _cmd_verify(a) -> int:
    g = CairnGraph(a.db)
    problems = g.verify()
    if problems:
        for p in problems:
            print(f"PROBLEM: {p}")
        return 1
    print("VERIFY OK")
    return 0


def _cmd_stats(a) -> int:
    g = CairnGraph(a.db)
    print(json.dumps(g.stats(), indent=1, sort_keys=True))
    return 0


def _cmd_serve(a) -> int:
    from .web import serve
    serve(a.db, port=a.port, scope=a.scope)
    return 0


def _cmd_demo(a) -> int:
    from demo.run_demo import main
    return main(a.out)


def _cmd_ingest(a) -> int:
    g = CairnGraph(a.db)
    ing = Ingestor(a.db)
    proposals = []
    with open(a.file, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError as e:
                print(f"line {lineno}: bad JSON ({e})")
                return 1
            try:
                proposals.append(Proposal(
                    subject=d["subject"], predicate=d["predicate"],
                    value=d["value"], valid_from=d["valid_from"],
                    recorder=d.get("recorder", a.recorder),
                    recorded_at=d.get("recorded_at"),
                    valid_to=d.get("valid_to"), source=d.get("source", a.file),
                    scope=d.get("scope", a.scope)))
            except TypeError as e:
                print(f"line {lineno}: {e}")
                return 1
    if a.commit:
        report = ing.auto(g, proposals)
        print(f"auto-commit: {report.summary()}")
    else:
        report = ing.propose(proposals)
        print(f"proposed {len(report.accepted)} claim(s) to the inbox "
              f"({len(report.rejected)} rejected); "
              f"review with `cairn inbox {a.db}` then `cairn commit`")
    for rec, why in report.rejected:
        print(f"  REJECTED: {why}: {json.dumps(rec, ensure_ascii=False)[:120]}")
    return 0 if report.ok else 1


def _cmd_inbox(a) -> int:
    ing = Ingestor(a.db)
    items = ing.inbox()
    if not items:
        print("inbox empty")
        return 0
    for i, p in enumerate(items):
        print(f"[{i}] {p.subject} --{p.predicate}--> {p.value!r} "
              f"@ {p.valid_from} by {p.recorder} ({p.scope})")
    return 0


def _cmd_commit(a) -> int:
    g = CairnGraph(a.db)
    ing = Ingestor(a.db)
    approvals = None
    if a.indices:
        approvals = [int(i) for i in a.indices.split(",") if i != ""]
    report = ing.commit(g, approvals)
    print(f"committed: {report.summary()}")
    for rec, why in report.rejected:
        print(f"  REJECTED: {why}")
    return 0


def _cmd_enterprise(a) -> int:
    if a.enterprise_cmd == "keygen":
        sk, pk = vendor_keypair()
        print(f"vendor secret key (KEEP SECRET): {sk.hex()}")
        print(f"vendor public key (ship with customers): {pk.hex()}")
        return 0
    if a.enterprise_cmd == "license":
        sk = bytes.fromhex(a.vendor_sk)
        doc = issue_license(sk, a.org, seats=a.seats, days=a.days)
        save_license(doc, a.out)
        print(f"license written to {a.out}")
        return 0
    lic = load_license(a.license, bytes.fromhex(a.vendor_pk))
    edb = EnterpriseDB.open(a.db, lic, bytes.fromhex(a.vendor_pk))
    if a.enterprise_cmd == "encrypt":
        n = edb.encrypt_database()
        print(f"encrypted {n} claim(s) at rest")
        return 0
    if a.enterprise_cmd == "decrypt":
        n = edb.decrypt_to(a.dest)
        print(f"decrypted {n} segment(s) to {a.dest}")
        return 0
    if a.enterprise_cmd == "stats":
        print(json.dumps(edb.stats(), indent=1, sort_keys=True))
        return 0
    raise SystemExit(f"unknown enterprise command: {a.enterprise_cmd}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cairn", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"cairn {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, help, **kw):
        return sub.add_parser(name, help=help, description=help, **kw)

    sp = add("init", "create a database")
    sp.add_argument("db")
    sp.set_defaults(fn=_cmd_init)

    sp = add("record", "assert a fact")
    sp.add_argument("db"); sp.add_argument("subject"); sp.add_argument("predicate")
    sp.add_argument("value")
    sp.add_argument("--valid-from", "--at", dest="valid_from", default=None,
                    help="when the fact became true (ISO-8601 or 'now')")
    sp.add_argument("--valid-to", default=None)
    sp.add_argument("--recorder", required=True, help="who is asserting this")
    sp.add_argument("--source", default="")
    sp.add_argument("--scope", default="private", help="private|family|guild|company|public")
    sp.set_defaults(fn=_cmd_record)

    for name, help in (("get", "read the current value of a fact"),
                       ("history", "full bitemporal timeline of a subject")):
        sp = add(name, help)
        sp.add_argument("db"); sp.add_argument("subject")
        if name == "get":
            sp.add_argument("predicate")
        sp.add_argument("--scope", default=None)
        sp.set_defaults(fn=_cmd_get if name == "get" else _cmd_history)

    sp = add("search", "full-text search over values")
    sp.add_argument("db"); sp.add_argument("query")
    sp.add_argument("--scope", default=None); sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(fn=_cmd_search)

    sp = add("nodes", "list subjects")
    sp.add_argument("db"); sp.add_argument("--scope", default=None)
    sp.set_defaults(fn=_cmd_nodes)

    sp = add("links", "outgoing edges of a subject")
    sp.add_argument("db"); sp.add_argument("subject"); sp.add_argument("--scope", default=None)
    sp.set_defaults(fn=_cmd_links)

    sp = add("traverse", "walk the graph from a subject")
    sp.add_argument("db"); sp.add_argument("subject")
    sp.add_argument("--depth", type=int, default=2); sp.add_argument("--scope", default=None)
    sp.set_defaults(fn=_cmd_traverse)

    for name in ("retract", "dispute", "endorse"):
        sp = add(name, f"{name} a claim (stream operation)")
        sp.add_argument("db"); sp.add_argument("cid")
        sp.add_argument("--recorder", required=True)
        sp.add_argument("--reason", default="")
        sp.add_argument("--scope", default="private")
        sp.set_defaults(fn=_cmd_operate)

    sp = add("alias", "declare two node ids the same entity")
    sp.add_argument("db"); sp.add_argument("subject"); sp.add_argument("other")
    sp.add_argument("--recorder", required=True); sp.add_argument("--scope", default="private")
    sp.set_defaults(fn=_cmd_alias)

    sp = add("merge", "union another database into this one")
    sp.add_argument("db"); sp.add_argument("other")
    sp.set_defaults(fn=_cmd_merge)

    sp = add("export", "write a portable bundle")
    sp.add_argument("db"); sp.add_argument("dest")
    sp.set_defaults(fn=_cmd_export)

    sp = add("import", "merge a portable bundle")
    sp.add_argument("db"); sp.add_argument("bundle")
    sp.set_defaults(fn=_cmd_import)

    sp = add("compact", "rewrite the log in canonical (cid-sorted) form")
    sp.add_argument("db")
    sp.set_defaults(fn=_cmd_compact)

    sp = add("verify", "full integrity check")
    sp.add_argument("db")
    sp.set_defaults(fn=_cmd_verify)

    sp = add("stats", "database statistics")
    sp.add_argument("db")
    sp.set_defaults(fn=_cmd_stats)

    sp = add("serve", "local web UI")
    sp.add_argument("db"); sp.add_argument("--port", type=int, default=8777)
    sp.add_argument("--scope", default=None)
    sp.set_defaults(fn=_cmd_serve)

    sp = add("demo", "run the three-scenario demo")
    sp.add_argument("--out", default="/tmp/cairn-demo")
    sp.set_defaults(fn=_cmd_demo)

    sp = add("ingest", "bulk-ingest a JSONL file (validation gate)")
    sp.add_argument("db"); sp.add_argument("file")
    sp.add_argument("--recorder", required=True)
    sp.add_argument("--scope", default="private")
    sp.add_argument("--commit", action="store_true",
                    help="auto-commit (trusted device); default parks in inbox")
    sp.set_defaults(fn=_cmd_ingest)

    sp = add("inbox", "list pending ingest proposals")
    sp.add_argument("db")
    sp.set_defaults(fn=_cmd_inbox)

    sp = add("commit", "commit inbox proposals (all, or selected indices)")
    sp.add_argument("db"); sp.add_argument("indices", nargs="?", default=None)
    sp.set_defaults(fn=_cmd_commit)

    sp = add("enterprise", "enterprise edition: licenses and encryption")
    esp = sp.add_subparsers(dest="enterprise_cmd", required=True)
    e = esp.add_parser("keygen", help="generate a vendor keypair")
    e.set_defaults(fn=_cmd_enterprise)
    e = esp.add_parser("license", help="issue a license file")
    e.add_argument("--vendor-sk", required=True)
    e.add_argument("--org", required=True)
    e.add_argument("--seats", type=int, default=10)
    e.add_argument("--days", type=int, default=365)
    e.add_argument("--out", required=True)
    e.set_defaults(fn=_cmd_enterprise)
    for name in ("encrypt", "decrypt", "stats"):
        e = esp.add_parser(name)
        e.add_argument("db")
        e.add_argument("--license", required=True)
        e.add_argument("--vendor-pk", required=True)
        if name == "decrypt":
            e.add_argument("--dest", required=True)
        e.set_defaults(fn=_cmd_enterprise)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (StoreError, LicenseError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"error: unknown claim: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
