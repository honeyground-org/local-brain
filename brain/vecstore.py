"""Vector stores — ★where similarity search runs★ (§stores for the shape and the rules).

Every backend answers one question the same way: for a unit query vector, the documents whose
★closest chunk★ is nearest, best first, as `[(doc_id, cosine)]`. `vectors.search` turns that into the
ratio its gate uses, so a backend never has to know about gates.

  sqlite   a full scan of the local `vectors` table (the canonical copy) — the default
  qdrant   Qdrant's HTTP API; one collection per (embedding model, dimension); grouped by document

Add a backend: a class with `backend`, `remote`, `target`, `ping`, `search` (and, if remote,
`ensure`, `reset`, `upsert`, `delete_docs`, `count`), then a row in `make`.
"""
from __future__ import annotations

import array
import operator
import re
import sqlite3
from typing import Dict, List, Sequence, Tuple

from brain import stores

DOCS_PER_BATCH = 32            # one upsert request — ≈150 chunks of 768 floats, about 1–2 MB of JSON
POINT_STRIDE = 1000            # point id = doc_id × stride + chunk_no (a document has at most 12 chunks)


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


class QdrantVectors:
    """Qdrant over its HTTP API. What it is sent: the vectors and `{doc_id, chunk_no}` — never text."""
    backend = "qdrant"
    remote = True

    def __init__(self, c: dict):
        o = c.get("options") or {}
        self.url = c["url"]
        self.prefix = re.sub(r"[^A-Za-z0-9_-]", "_", str(o.get("collection_prefix") or "brain"))
        # exact = brute force inside Qdrant (the same answers as the local scan); otherwise HNSW
        self.exact = bool(o.get("exact", False))
        key = stores.secret(c)
        self.headers = {"api-key": key} if key else {}

    def collection(self, model: str, dim: int) -> str:
        return "%s_%s_%d" % (self.prefix, re.sub(r"[^A-Za-z0-9_-]", "_", model), dim)

    def target(self, model: str, dim: int) -> str:
        return "qdrant:%s/%s" % (self.url, self.collection(model, dim))

    def _call(self, method: str, path: str, body=None, timeout: float = 0.0) -> dict:
        return stores.http_json(method, self.url + path, body, self.headers,
                                timeout or stores.QUERY_TIMEOUT)

    def ping(self) -> dict:
        r = self._call("GET", "/")
        return {"ok": True, "version": r.get("version", "")}

    def ensure(self, model: str, dim: int) -> None:
        name = self.collection(model, dim)
        try:
            info = self._call("GET", "/collections/%s" % name, timeout=stores.SYNC_TIMEOUT)
            size = (((info.get("result") or {}).get("config") or {}).get("params") or {}) \
                .get("vectors", {}).get("size")
            if size and int(size) != int(dim):
                raise stores.StoreError("collection %s holds %s-dimensional vectors, not %d" % (name, size, dim))
            return
        except stores.StoreError as exc:
            if "HTTP 404" not in str(exc):
                raise
        # Cosine: Qdrant normalises on the way in — the vectors already are unit length (§vectors._unit)
        self._call("PUT", "/collections/%s" % name, {"vectors": {"size": int(dim), "distance": "Cosine"}},
                   timeout=stores.SYNC_TIMEOUT)
        self._call("PUT", "/collections/%s/index?wait=true" % name,
                   {"field_name": "doc_id", "field_schema": "integer"}, timeout=stores.SYNC_TIMEOUT)

    def reset(self, model: str, dim: int) -> None:
        try:
            self._call("DELETE", "/collections/%s" % self.collection(model, dim), timeout=stores.SYNC_TIMEOUT)
        except stores.StoreError as exc:
            if "HTTP 404" not in str(exc):
                raise
        self.ensure(model, dim)

    def delete_docs(self, model: str, dim: int, doc_ids: Sequence[int]) -> None:
        if doc_ids:
            self._call("POST", "/collections/%s/points/delete?wait=true" % self.collection(model, dim),
                       {"filter": {"must": [{"key": "doc_id", "match": {"any": [int(i) for i in doc_ids]}}]}},
                       timeout=stores.SYNC_TIMEOUT)

    def upsert(self, model: str, dim: int, rows: Sequence[Tuple[int, int, Sequence[float]]]) -> None:
        if rows:
            self._call("PUT", "/collections/%s/points?wait=true" % self.collection(model, dim),
                       {"points": [{"id": int(d) * POINT_STRIDE + int(c), "vector": [float(x) for x in v],
                                    "payload": {"doc_id": int(d), "chunk_no": int(c)}} for d, c, v in rows]},
                       timeout=stores.SYNC_TIMEOUT)

    def search(self, db: sqlite3.Connection, q: Sequence[float], model: str, dim: int,
               n_docs: int) -> List[Tuple[int, float]]:
        r = self._call("POST", "/collections/%s/points/query/groups" % self.collection(model, dim),
                       {"query": [float(x) for x in q], "group_by": "doc_id", "group_size": 1,
                        "limit": int(n_docs), "with_payload": False, "params": {"exact": self.exact}})
        out = []
        for g in ((r.get("result") or {}).get("groups") or []):
            hits = g.get("hits") or []
            if hits:
                out.append((int(g["id"]), float(hits[0]["score"])))
        return sorted(out, key=lambda kv: (-kv[1], kv[0]))

    def count(self, db: sqlite3.Connection, model: str, dim: int) -> int:
        r = self._call("POST", "/collections/%s/points/count" % self.collection(model, dim), {"exact": True})
        return int((r.get("result") or {}).get("count") or 0)


def make(c: dict):
    if c.get("backend") == "qdrant" and not c.get("error"):
        return QdrantVectors(c)
    return SqliteVectors(c)


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
