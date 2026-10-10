"""Milvus — the vector role, over Milvus' RESTful API v2 (§stores · §vecstore.CONTRACT).

What it is sent: the vectors and `{doc_id, chunk_no}` — never text (all `vecstore.sync` hands any vector
backend). One collection per (namespace, embedding model, dimension) with a ★FLAT★ index — Milvus'
exhaustive search, so the answers are the local scan's — and a grouping search (`groupingField: doc_id`)
that returns each document's closest chunk, as the local scan does. Reads ask for Strong consistency:
Milvus is eventually consistent by default, and a sync is followed at once by the questions that check it.

In Docker it runs standalone in one container — embedded etcd and local storage, the data in its folder —
as Milvus' own single-container script runs it, without the config files that script mounts.
"""
from __future__ import annotations

import re
import sqlite3
from typing import List, Sequence, Tuple

from brain import stores

POINT_STRIDE = 1000            # primary key = doc_id × stride + chunk_no (a document has at most 12 chunks)
NOT_FOUND = 100                # Milvus' code for a collection that does not exist


class MilvusVectors:
    """Milvus over HTTP. Every answer is `{code, data | message}`; code 0 is success."""
    backend = "milvus"
    remote = True

    def __init__(self, c: dict):
        self.url = c["url"]
        self.database = str(stores.opt(c, "database"))
        self.prefix = re.sub(r"[^A-Za-z0-9_]", "_", str(stores.opt(c, "collection_prefix")))
        ns = re.sub(r"[^A-Za-z0-9_]", "_", str(stores.opt(c, "namespace") or ""))
        if ns:
            self.prefix += "_" + ns
        key = stores.secret(c)
        self.headers = {"Authorization": "Bearer " + key} if key else {}

    def collection(self, model: str, dim: int) -> str:
        name = "%s_%s_%d" % (self.prefix, re.sub(r"[^A-Za-z0-9_]", "_", model), dim)
        return name if name[0].isalpha() or name[0] == "_" else "c_" + name

    def target(self, model: str, dim: int) -> str:
        return "milvus:%s/%s/%s" % (self.url, self.database, self.collection(model, dim))

    def _call(self, path: str, body: dict, timeout: float = 0.0):
        if self.database != "default":
            body = dict(body, dbName=self.database)
        r = stores.http_json("POST", "%s/v2/vectordb/%s" % (self.url, path), body, self.headers,
                             timeout or stores.QUERY_TIMEOUT)
        code = int(r.get("code", 0) or 0)
        if code == NOT_FOUND:
            raise stores.StoreGone("milvus %s → %s" % (path, str(r.get("message", ""))[:200]))
        if code != 0:
            raise stores.StoreError("milvus %s → %d %s" % (path, code, str(r.get("message", ""))[:200]))
        return r.get("data")

    def ping(self) -> dict:
        """Answering is not serving: after a restart Milvus lists its collections at once, but a search waits
        ~6 s and then fails with 503 "no available shard leaders" (measured 2026-10-10). So the ping searches one
        of this brain's collections, when there is one — readiness and `brain stores` then mean "it can answer"."""
        names = [n for n in (self._call("collections/list", {}) or []) if str(n).startswith(self.prefix + "_")]
        for name in names[:1]:
            fields = (self._call("collections/describe", {"collectionName": name}) or {}).get("fields") or []
            dim = next((int({p.get("key"): p.get("value") for p in f.get("params") or []}.get("dim") or 0)
                        for f in fields if f.get("name") == "vec"), 0)
            if dim:
                self._call("entities/search", {"collectionName": name, "data": [[1.0] + [0.0] * (dim - 1)],
                                               "annsField": "vec", "limit": 1, "consistencyLevel": "Strong"})
        return {"ok": True, "version": "Milvus"}

    def ensure(self, model: str, dim: int) -> None:
        name = self.collection(model, dim)
        if (self._call("collections/has", {"collectionName": name}, timeout=stores.SYNC_TIMEOUT) or {}).get("has"):
            fields = (self._call("collections/describe", {"collectionName": name}, timeout=stores.SYNC_TIMEOUT)
                      or {}).get("fields") or []
            for f in fields:
                if f.get("name") == "vec":
                    held = {p.get("key"): p.get("value") for p in f.get("params") or []}.get("dim")
                    if held and int(held) != int(dim):
                        raise stores.StoreError("collection %s holds %s-dimensional vectors, not %d" % (name, held, dim))
            return
        self._call("collections/create", {
            "collectionName": name,
            "schema": {"autoId": False, "enableDynamicField": False, "fields": [
                {"fieldName": "id", "dataType": "Int64", "isPrimary": True},
                {"fieldName": "doc_id", "dataType": "Int64"},
                {"fieldName": "chunk_no", "dataType": "Int64"},
                {"fieldName": "vec", "dataType": "FloatVector", "elementTypeParams": {"dim": str(int(dim))}}]},
            "indexParams": [{"fieldName": "vec", "indexName": "vec", "metricType": "COSINE", "indexType": "FLAT"}]},
            timeout=stores.SYNC_TIMEOUT)

    def drop(self, model: str, dim: int) -> None:
        try:
            self._call("collections/drop", {"collectionName": self.collection(model, dim)}, timeout=stores.SYNC_TIMEOUT)
        except stores.StoreGone:
            pass

    def reset(self, model: str, dim: int) -> None:
        self.drop(model, dim)
        self.ensure(model, dim)

    def delete_docs(self, model: str, dim: int, doc_ids: Sequence[int]) -> None:
        if doc_ids:
            self._call("entities/delete", {"collectionName": self.collection(model, dim),
                                           "filter": "doc_id in [%s]" % ",".join(str(int(d)) for d in doc_ids)},
                       timeout=stores.SYNC_TIMEOUT)

    def upsert(self, model: str, dim: int, rows: Sequence[Tuple[int, int, Sequence[float]]]) -> None:
        if rows:
            self._call("entities/upsert", {"collectionName": self.collection(model, dim), "data": [
                {"id": int(d) * POINT_STRIDE + int(c), "doc_id": int(d), "chunk_no": int(c),
                 "vec": [float(x) for x in v]} for d, c, v in rows]}, timeout=stores.SYNC_TIMEOUT)

    def search(self, db: sqlite3.Connection, q: Sequence[float], model: str, dim: int,
               n_docs: int) -> List[Tuple[int, float]]:
        hits = self._call("entities/search", {
            "collectionName": self.collection(model, dim), "data": [[float(x) for x in q]], "annsField": "vec",
            "limit": int(n_docs), "groupingField": "doc_id", "outputFields": ["doc_id"],
            "consistencyLevel": "Strong"}) or []
        return sorted(((int(h["doc_id"]), float(h["distance"])) for h in hits), key=lambda kv: (-kv[1], kv[0]))

    def count(self, db: sqlite3.Connection, model: str, dim: int) -> int:
        rows = self._call("entities/query", {"collectionName": self.collection(model, dim), "filter": "",
                                             "outputFields": ["count(*)"], "consistencyLevel": "Strong"}) or [{}]
        return int(rows[0].get("count(*)") or 0)


BACKEND = stores.Backend(
    name="milvus", role="vector", title="Milvus", make=MilvusVectors, url="http://localhost:19530",
    secret=stores.Secret(env="MILVUS_TOKEN", field="milvus_token"),
    options=(stores.Option("collection_prefix", "brain", "collection name prefix"),
             stores.Option("database", "default", "Milvus database")),
    docker=stores.Docker(image="milvusdb/milvus:v2.6.25",
                         ports=(stores.Port("api", 19530, "BRAIN_MILVUS_PORT"),),
                         data=(("", "/var/lib/milvus"),),
                         env={"ETCD_USE_EMBED": "true", "ETCD_DATA_DIR": "/var/lib/milvus/etcd",
                              "COMMON_STORAGETYPE": "local", "DEPLOY_MODE": "STANDALONE"},
                         command=("milvus", "run", "standalone"),
                         telemetry_off=()),          # the Docker check watches it for connections off the machine
)
