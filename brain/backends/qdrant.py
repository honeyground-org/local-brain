"""Qdrant — the vector role, over Qdrant's HTTP API (§stores · §vecstore.CONTRACT).

What it is sent: the vectors and `{doc_id, chunk_no}` — never text (all `vecstore.sync` hands any vector
backend). One collection per (embedding model, dimension); a search is grouped by document, so the
answer is each document's closest chunk, as the local scan gives it.
"""
from __future__ import annotations

import re
import sqlite3
from typing import List, Sequence, Tuple

from brain import stores

POINT_STRIDE = 1000            # point id = doc_id × stride + chunk_no (a document has at most 12 chunks)


class QdrantVectors:
    """Qdrant over its HTTP API."""
    backend = "qdrant"
    remote = True

    def __init__(self, c: dict):
        self.url = c["url"]
        self.prefix = re.sub(r"[^A-Za-z0-9_-]", "_", str(stores.opt(c, "collection_prefix")))
        ns = re.sub(r"[^A-Za-z0-9_-]", "_", str(stores.opt(c, "namespace") or ""))
        if ns:
            self.prefix += "_" + ns
        # exact = brute force inside Qdrant (the same answers as the local scan); otherwise HNSW
        self.exact = bool(stores.opt(c, "exact"))
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

    def drop(self, model: str, dim: int) -> None:
        try:
            self._call("DELETE", "/collections/%s" % self.collection(model, dim), timeout=stores.SYNC_TIMEOUT)
        except stores.StoreError as exc:
            if "HTTP 404" not in str(exc):
                raise

    def reset(self, model: str, dim: int) -> None:
        self.drop(model, dim)
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


BACKEND = stores.Backend(
    name="qdrant", role="vector", title="Qdrant", make=QdrantVectors, url="http://localhost:6333",
    secret=stores.Secret(env="QDRANT_API_KEY", field="qdrant_api_key"),
    options=(stores.Option("collection_prefix", "brain", "collection name prefix"),
             stores.Option("exact", False, "brute-force search inside Qdrant — the same answers as the local scan")),
    docker=stores.Docker(image="qdrant/qdrant:v1.19.2",
                         ports=(stores.Port("api", 6333, "BRAIN_QDRANT_PORT"),),
                         data=(("", "/qdrant/storage"),)),
)
