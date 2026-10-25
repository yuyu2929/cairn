#!/usr/bin/env python3
"""cairn benchmark -- raw speed of the things that matter.

Run:  python3 examples/bench.py

A local-first database lives or dies on two numbers: how fast you can
write claims, and how fast you can read them back after a merge.  This
prints both on whatever machine you run it on (no warm-up tricks).
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairn
from cairn.ingest import Ingestor


def _rate(n: int, elapsed: float) -> str:
    return f"{n / elapsed:,.0f} ops/s  ({elapsed*1000/n:.3f} ms/op)"


def bench_record(n: int = 20_000) -> None:
    g = cairn.open(os.path.join(tempfile.mkdtemp(), "bench"), create=True)
    t0 = time.perf_counter()
    for i in range(n):
        g.record("s%d" % (i % 1000), "p", i, "2020-01-01",
                 recorder="bench")
    dt = time.perf_counter() - t0
    print(f"  record x{n:>7}: {_rate(n, dt)}  (total {dt:.2f}s)")


def bench_ingest(n: int = 100_000) -> None:
    g = cairn.open(os.path.join(tempfile.mkdtemp(), "bench"), create=True)
    bot = Ingestor(g.path)
    rows = ({"subject": "u%d" % (i % 500), "predicate": "score",
             "value": i, "valid_from": "2020-01-01", "recorder": "bot"}
            for i in range(n))
    t0 = time.perf_counter()
    rep = bot.auto(g, rows)
    dt = time.perf_counter() - t0
    print(f"  ingest x{n:>7}: {_rate(n, dt)}  -> {rep.summary()}")


def bench_resolve(n: int = 20_000, q: int = 10_000) -> None:
    g = cairn.open(os.path.join(tempfile.mkdtemp(), "bench"), create=True)
    for i in range(n):
        g.record("s%d" % (i % 1000), "p", i, "2020-01-01", recorder="bench")
    t0 = time.perf_counter()
    for i in range(q):
        g.value("s%d" % (i % 1000), "p")
    dt = time.perf_counter() - t0
    print(f"  resolve x{q:>7}: {_rate(q, dt)}  (over {n} claims)")


def bench_merge(n: int = 10_000) -> None:
    a = cairn.open(os.path.join(tempfile.mkdtemp(), "a"), create=True)
    b = cairn.open(os.path.join(tempfile.mkdtemp(), "b"), create=True)
    for i in range(n):
        a.record("s%d" % i, "p", i, "2020-01-01", recorder="a")
        b.record("t%d" % i, "p", i, "2020-01-01", recorder="b")
    t0 = time.perf_counter()
    moved = a.merge(b.path)
    dt = time.perf_counter() - t0
    print(f"  merge  +{moved:>6}: {_rate(moved, dt)}  (union of two {n}-claim dbs)")


def bench_search(n: int = 20_000, q: int = 2_000) -> None:
    g = cairn.open(os.path.join(tempfile.mkdtemp(), "bench"), create=True)
    for i in range(n):
        g.record("doc%d" % i, "note",
                 "incident 三月 report %d" % i, "2020-01-01", recorder="bench")
    t0 = time.perf_counter()
    for _ in range(q):
        g.view().search("三月")
    dt = time.perf_counter() - t0
    print(f"  search x{q:>7}: {_rate(q, dt)}  (CJK token AND over {n} docs)")


def main() -> None:
    print("Cairn %s benchmark" % cairn.__version__)
    print("(machine-dependent; the point is the order of magnitude)\n")
    bench_record()
    bench_ingest()
    bench_resolve()
    bench_merge()
    bench_search()
    print("\nAll writes are append-only JSONL; all reads are in-memory "
          "indexes rebuilt from the log on open.  No server, no network.")


if __name__ == "__main__":
    main()
