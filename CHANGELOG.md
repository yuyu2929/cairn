# Changelog

All notable changes to Cairn are documented here. The container format is
content-addressed and self-describing; a breaking change to the canonical
claim encoding bumps the major version.

## [1.0.1] - 2026-10-25

Windows portability and canonical-time fixes. The CRDT core held up under
adversarial probing (6 merge orders produced one identical result set,
re-merging added nothing, `verify()` clean).

### Fixed

- **`Store.compact()` and `EnterpriseDB.encrypt_database()` crashed on
  Windows** with `PermissionError: [WinError 32] 另一个程序正在使用此文件`.
  The store keeps one long-lived append handle open on the current segment;
  both paths unlinked segments *before* closing it. Linux tolerates unlinking
  an open file, which is why the original suite passed on CI. The handle is
  now released first (`Store.close()`), and `compact()` no longer re-opens on
  a handle that points at a deleted file.
- **The same instant had two canonical spellings.** `norm_time()` passed
  timezone-aware datetimes straight through to `isoformat()`, so
  `2020-01-01T08:00:00+08:00` and `2020-01-01T00:00:00Z` — the same moment —
  produced different `valid_from` strings. Two devices could then order the
  same pair of claims differently, which breaks the resolution rule the
  database is built on. Aware inputs are now converted to UTC and stored
  without an offset; unix timestamps are normalised the same way (and the
  deprecated `datetime.utcfromtimestamp` call is gone).
- `README.md` reported 70 tests; the suite is now 74.

### Added

- `tests/test_merge_order.py`: merge over all 6 chunk orders produces one
  identical claim set; re-merging adds nothing; `compact()` output is
  byte-identical regardless of insert order; the same instant written in two
  time zones canonicalises to one value.
- `Store.close()`.

### Verified, not changed

Probed adversarially and found sound: merge commutativity/associativity over
all 6 orders of three chunks; idempotence; `verify()` clean after merges and
compaction.

## [1.0.0] - 2026

Initial release: content-addressed claims, grow-only set merge, bitemporal
resolution, scope visibility, robot ingest, ChaCha20-Poly1305 encrypted
scopes, Ed25519 licences, CLI and local web UI.

[1.0.1]: https://github.com/yuyu2929/cairn/releases/tag/v1.0.1
[1.0.0]: https://github.com/yuyu2929/cairn/releases/tag/v1.0.0
