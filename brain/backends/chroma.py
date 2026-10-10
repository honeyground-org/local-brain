"""Chroma — the vector role, over Chroma's HTTP API v2 (§stores · §vecstore.CONTRACT).

What it is sent: the vectors and `{doc_id, chunk_no}` — never text (all `vecstore.sync` hands any vector
backend). One collection per (namespace, embedding model, dimension), cosine distance.

Chroma answers per chunk and has no grouping, so a search asks for `n_docs × MAX_CHUNKS_PER_DOC` chunks
and keeps each document's best one. That is enough: the best chunk of the n-th best document can be
outranked only by chunks of the n−1 documents above it, at most 12 each.

Chroma's index is approximate (HNSW). Measured on the author's notes (1,923 documents, 9,852 chunks,
2026-10-10): `ef_search` 200 kept the top 1 of all 40 questions but 97–98% of the top 10; ★1000 (the
default here) kept 100% of the top 10★, at ~15–20 ms per question against ~180 ms for the local scan.

Measured on 1.5.9 (2026-10-10): watched for 150 seconds, the container opened no connection off the
machine — its only exporter is OpenTelemetry, which sends nothing until an endpoint is configured.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Dict, List, Sequence, Tuple

from brain import stores


class ChromaVectors:
    """Chroma over HTTP."""
    backend = "chroma"
    remote = True

    def __init__(self, c: dict):
        self.url = c["url"]
        tenant, database = str(stores.opt(c, "tenant")), str(stores.opt(c, "database"))
        self.base = "%s/api/v2/tenants/%s/databases/%s" % (self.url, tenant, database)
        self.prefix = re.sub(r"[^A-Za-z0-9_-]", "_", str(stores.opt(c, "collection_prefix")))
        ns = re.sub(r"[^A-Za-z0-9_-]", "_", str(stores.opt(c, "namespace") or ""))
        if ns:
            self.prefix += "_" + ns
        self.ef_search = int(stores.opt(c, "ef_search"))
        key = stores.secret(c)
        self.headers = {"x-chroma-token": key} if key else {}
        self._ids: Dict[str, str] = {}

    def collection(self, model: str, dim: int) -> str:
        return "%s_%s_%d" % (self.prefix, re.sub(r"[^A-Za-z0-9_-]", "_", model), dim)

    def target(self, model: str, dim: int) -> str:
        return "chroma:%s/%s" % (self.base, self.collection(model, dim))

    def _call(self, method: str, path: str, body=None, timeout: float = 0.0) -> dict:
        return stores.http_json(method, self.base + path, body, self.headers, timeout or stores.QUERY_TIMEOUT)

    def _id(self, model: str, dim: int) -> str:
        """The collection's id — Chroma addresses a collection by id for everything but create and delete."""
        name = self.collection(model, dim)
        if name not in self._ids:
            self._ids[name] = str(self._call("GET", "/collections/%s" % name)["id"])
        return self._ids[name]

    def ping(self) -> dict:
        v = stores.http_json("GET", self.url + "/api/v2/version", None, self.headers, stores.QUERY_TIMEOUT)
        return {"ok": True, "version": str(v)}

    def ensure(self, model: str, dim: int) -> None:
        name = self.collection(model, dim)
        r = self._call("POST", "/collections", {
            "name": name, "get_or_create": True, "metadata": {"by": "local-brain", "dim": int(dim)},
            "configuration": {"hnsw": {"space": "cosine", "ef_search": self.ef_search,
                                       "ef_construction": max(100, min(self.ef_search, 400))}}},
            timeout=stores.SYNC_TIMEOUT)
        self._ids[name] = str(r["id"])

    def drop(self, model: str, dim: int) -> None:
        name = self.collection(model, dim)
        self._ids.pop(name, None)
        try:
            self._call("DELETE", "/collections/%s" % name, timeout=stores.SYNC_TIMEOUT)
        except stores.StoreError as exc:
            if not stores.gone(exc):
                raise

    def reset(self, model: str, dim: int) -> None:
        self.drop(model, dim)
        self.ensure(model, dim)

    def delete_docs(self, model: str, dim: int, doc_ids: Sequence[int]) -> None:
        if doc_ids:
            self._call("POST", "/collections/%s/delete" % self._id(model, dim),
                       {"where": {"doc_id": {"$in": [int(i) for i in doc_ids]}}}, timeout=stores.SYNC_TIMEOUT)

    def upsert(self, model: str, dim: int, rows: Sequence[Tuple[int, int, Sequence[float]]]) -> None:
        if rows:
            self._call("POST", "/collections/%s/upsert" % self._id(model, dim), {
                "ids": ["%d:%d" % (int(d), int(c)) for d, c, _ in rows],
                "embeddings": [[float(x) for x in v] for _, _, v in rows],
                "metadatas": [{"doc_id": int(d), "chunk_no": int(c)} for d, c, _ in rows]},
                timeout=stores.SYNC_TIMEOUT)

    def search(self, db: sqlite3.Connection, q: Sequence[float], model: str, dim: int,
               n_docs: int) -> List[Tuple[int, float]]:
        from brain import vectors
        cid = self._id(model, dim)
        r = self._call("POST", "/collections/%s/query" % cid, {
            "query_embeddings": [[float(x) for x in q]], "n_results": int(n_docs) * vectors.MAX_CHUNKS_PER_DOC,
            "include": ["distances", "metadatas"]})
        best: Dict[int, float] = {}
        for meta, dist in zip((r.get("metadatas") or [[]])[0], (r.get("distances") or [[]])[0]):
            d, score = int(meta["doc_id"]), 1.0 - float(dist)          # cosine distance → cosine
            if score > best.get(d, -2.0):
                best[d] = score
        return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:n_docs]

    def count(self, db: sqlite3.Connection, model: str, dim: int) -> int:
        return int(self._call("GET", "/collections/%s/count" % self._id(model, dim)))


BACKEND = stores.Backend(
    name="chroma", role="vector", title="Chroma", make=ChromaVectors, url="http://localhost:8000",
    secret=stores.Secret(env="CHROMA_TOKEN", field="chroma_token"),
    options=(stores.Option("collection_prefix", "brain", "collection name prefix"),
             stores.Option("tenant", "default_tenant", "Chroma tenant"),
             stores.Option("database", "default_database", "Chroma database"),
             stores.Option("ef_search", 1000, "HNSW search breadth — higher is closer to the exact local scan")),
    docker=stores.Docker(image="chromadb/chroma:1.5.9",
                         ports=(stores.Port("api", 8000, "BRAIN_CHROMA_PORT", host=8800),),
                         data=(("", "/data"),), user="host",
                         telemetry_off=()),          # measured: no connection off the machine (see above)
)
