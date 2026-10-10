"""Vector stores — ★where similarity search runs★ (§stores for the shape and the rules).

Every backend answers one question the same way: for a unit query vector, the documents whose
★closest chunk★ is nearest, best first, as `[(doc_id, cosine)]`. `vectors.search` turns that into the
ratio its gate uses, so a backend never has to know about gates.

  sqlite   a full scan of the local `vectors` table (the canonical copy) — the default
  others   one file each in `brain/backends/` (§stores "One file per backend")

A dedicated vector backend is a class with the methods in `CONTRACT`:

  target(model, dim) → str        where this copy lives — the ledger's key, so it must be stable; it must
                                  include the role's `namespace` option when one is set (§stores.ROLE_OPTIONS)
  ping() → {ok, version}          raises StoreError when the database does not answer
  ensure(model, dim)              create the collection (or table, or index) when it is not there
  reset(model, dim)               empty it and ensure it again
  drop(model, dim)                remove everything this brain wrote there
  upsert(model, dim, rows)        rows = [(doc_id, chunk_no, unit vector)] — ★all it is ever sent★
  delete_docs(model, dim, ids)    every chunk of these documents
  search(db, q, model, dim, n)    [(doc_id, cosine of its closest chunk)], best first, ties by doc_id
  count(db, model, dim) → int     chunks held — compared with the ledger to notice a lost copy
"""
from __future__ import annotations

import array
import operator
import sqlite3
from typing import Dict, List, Sequence, Tuple

from brain import stores

DOCS_PER_BATCH = 32            # one upsert request — ≈150 chunks of 768 floats, about 1–2 MB of JSON

CONTRACT = ("target", "ping", "ensure", "reset", "drop", "upsert", "delete_docs", "search", "count")


def _unpack(blob: bytes) -> array.array:
    v = array.array("f")
    v.frombytes(blob)
    return v


class SqliteVectors:
    """The local copy, scanned in full. Exact, dependency-free, and fast enough for one person's notes."""
    backend = "sqlite"
    remote = False

    def __init__(self, c: dict = None):
        self.c = c or {}

    def target(self, model: str, dim: int) -> str:
        return "sqlite"

    def ping(self) -> dict:
        return {"ok": True}

    def search(self, db: sqlite3.Connection, q: Sequence[float], model: str, dim: int,
               n_docs: int) -> List[Tuple[int, float]]:
        # ⛔ Only documents that still exist (2026-10-07) — a removed document's chunks stayed in the table
        #    and still pulled the top-N mean the gate divides by (8 such documents on the author's index).
        best: Dict[int, float] = {}
        for did, blob in db.execute(
                "SELECT v.doc_id, v.vec FROM vectors v JOIN docs d ON d.id=v.doc_id "
                "WHERE v.model=? AND v.dim=?", (model, dim)):
            sc = sum(map(operator.mul, q, _unpack(blob)))
            if sc > best.get(did, -2.0):
                best[did] = sc
        return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:n_docs]

    def count(self, db: sqlite3.Connection, model: str, dim: int) -> int:
        return db.execute("SELECT COUNT(*) FROM vectors v JOIN docs d ON d.id=v.doc_id "
                          "WHERE v.model=? AND v.dim=?", (model, dim)).fetchone()[0]


def make(c: dict):
    """The backend a choice names (§stores.backends) — the local copy when it names none that loads."""
    b = None if c.get("error") else stores.backends("vector").get(c.get("backend", ""))
    if b is None or b.make is None:
        return SqliteVectors(c)
    return b.make(c)


def sync(db: sqlite3.Connection, backend, full: bool = False, progress: bool = False) -> dict:
    """Push the local copy's current-model vectors to `backend`. Only what the ledger says it lacks."""
    from brain import vectors
    model, dim = vectors.model_tag(), vectors.DIM
    target = backend.target(model, dim)
    vectors.ensure_table(db)
    desired = {str(r[0]): "%s|%d" % (r[1], r[2]) for r in db.execute(
        "SELECT v.doc_id, MAX(v.sha), COUNT(*) FROM vectors v JOIN docs d ON d.id=v.doc_id "
        "WHERE v.model=? AND v.dim=? GROUP BY v.doc_id", (model, dim))}
    stats = {"backend": backend.backend, "target": target, "docs": len(desired),
             "sent_docs": 0, "sent_chunks": 0, "deleted_docs": 0}
    if full:
        backend.reset(model, dim)
        stores.ledger_clear(db, "vector", target)
    have = stores.ledger(db, "vector", target)
    if have and not full:
        # ★Does the database still hold what the ledger says it was sent★ (2026-10-07) — a collection deleted,
        # a Docker reset, a restore from an old backup: the ledger would keep the database out of sync forever.
        expected = sum(int(v.rsplit("|", 1)[1]) for v in have.values())
        try:
            held = backend.count(db, model, dim)
        except stores.StoreError as exc:
            if not stores.gone(exc):
                raise
            held = 0
        if held != expected:
            backend.reset(model, dim)
            stores.ledger_clear(db, "vector", target)
            have = {}
            stats["healed"] = {"expected": expected, "held": held}
    up = sorted((k for k, v in desired.items() if have.get(k) != v), key=int)
    gone = sorted((k for k in have if k not in desired), key=int)
    if not up and not gone:
        stats["unchanged"] = True
        return stats
    backend.ensure(model, dim)
    if gone:
        backend.delete_docs(model, dim, [int(k) for k in gone])
        stores.ledger_apply(db, "vector", target, {}, gone)
        stats["deleted_docs"] = len(gone)
    for i in range(0, len(up), DOCS_PER_BATCH):
        ids = [int(k) for k in up[i:i + DOCS_PER_BATCH]]
        rows = [(r[0], r[1], _unpack(r[2])) for r in db.execute(
            "SELECT doc_id, chunk_no, vec FROM vectors WHERE model=? AND dim=? AND doc_id IN (%s)"
            % ",".join("?" * len(ids)), (model, dim, *ids))]
        backend.delete_docs(model, dim, ids)          # a document that lost chunks loses them there too
        backend.upsert(model, dim, rows)
        stores.ledger_apply(db, "vector", target, {str(d): desired[str(d)] for d in ids})
        stats["sent_docs"] += len(ids)
        stats["sent_chunks"] += len(rows)
        if progress:
            from brain import i18n
            print(i18n.t("cli.stores.sync.progress", role="vector", backend=backend.backend,
                         done=stats["sent_docs"], total=len(up)), flush=True)
    return stats
