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
            if not stores.gone(exc):
                raise
        # Cosine: Qdrant normalises on the way in — the vectors already are unit length (§vectors._unit)
        try:
            self._call("PUT", "/collections/%s" % name, {"vectors": {"size": int(dim), "distance": "Cosine"}},
                       timeout=stores.SYNC_TIMEOUT)
        except stores.StoreError as exc:
            # ⛔ (2026-11-04) Another process created it between the GET above and this PUT — the first
            #    `vec build` after choosing an engine races the session-start index. Qdrant answers 409;
            #    that is the state we wanted, so it is checked like any existing collection, not failed.
            if "HTTP 409" not in str(exc):
                raise
            return self.ensure(model, dim)
        self._call("PUT", "/collections/%s/index?wait=true" % name,
                   {"field_name": "doc_id", "field_schema": "integer"}, timeout=stores.SYNC_TIMEOUT)

    def drop(self, model: str, dim: int) -> None:
        try:
            self._call("DELETE", "/collections/%s" % self.collection(model, dim), timeout=stores.SYNC_TIMEOUT)
        except stores.StoreError as exc:
            if not stores.gone(exc):
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


QDRANT_RELEASE = "https://github.com/qdrant/qdrant/releases/download/v1.19.2/"


def native_layout(ctx: dict) -> dict:
    """Qdrant without Docker: the official binary, one config file, nothing else (§nativestores)."""
    import json as _json
    import os as _os
    cfg = _os.path.join(ctx["data"][""], "config.yaml")
    q = lambda v: _json.dumps(v)                     # a JSON string is a valid YAML scalar on every platform
    text = ("# Written by local-brain (`brain stores --native`).\n"
            "storage:\n  storage_path: %s\n  snapshots_path: %s\n"
            "service:\n  host: \"127.0.0.1\"\n  http_port: %d\n  grpc_port: %d\n"
            "telemetry_disabled: true\n"
            % (q(_os.path.join(ctx["data"][""], "storage")), q(_os.path.join(ctx["data"][""], "snapshots")),
               ctx["ports"]["api"], ctx["ports"]["grpc"]))
    exe = _os.path.join(ctx["home"], "qdrant.exe" if ctx["platform"].startswith("windows") else "qdrant")
    return {"argv": [exe, "--config-path", cfg], "cwd": ctx["data"][""], "files": {cfg: text}}


BACKEND = stores.Backend(
    name="qdrant", role="vector", title="Qdrant", make=QdrantVectors, url="http://localhost:6333",
    secret=stores.Secret(env="QDRANT_API_KEY", field="qdrant_api_key"),
    options=(stores.Option("collection_prefix", "brain", "collection name prefix"),
             stores.Option("exact", False, "brute-force search inside Qdrant — the same answers as the local scan")),
    docker=stores.Docker(image="qdrant/qdrant:v1.19.2",
                         ports=(stores.Port("api", 6333, "BRAIN_QDRANT_PORT"),),
                         data=(("", "/qdrant/storage"),),
                         telemetry_off=("QDRANT__TELEMETRY_DISABLED=true",)),   # on by default
    # Without Docker: the official release binary, checksums from the GitHub release (2026-11-07).
    native=stores.Native(
        version="1.19.2",
        files={"darwin-arm64": (QDRANT_RELEASE + "qdrant-aarch64-apple-darwin.tar.gz",
                                "d24dfd25f4584684b337dd30ee016472cd25f53fc2501f1cd058ab3d7ee0b019"),
               "darwin-x64": (QDRANT_RELEASE + "qdrant-x86_64-apple-darwin.tar.gz",
                              "e095b682ad7460baff43827eaa13647b5ec97c48edcb676646ae2f4108395044"),
               "linux-x64": (QDRANT_RELEASE + "qdrant-x86_64-unknown-linux-musl.tar.gz",
                             "50b253243309ed0ae50a19f678f0d0adcc4f0b567b3bd2c5c0c7e02fa8404bb6"),
               "linux-arm64": (QDRANT_RELEASE + "qdrant-aarch64-unknown-linux-musl.tar.gz",
                               "6970b93b56fa1203f0cea47d2f330fdb654988fd56617aa77e8478cffd3c0ccb"),
               "windows-x64": (QDRANT_RELEASE + "qdrant-x86_64-pc-windows-msvc.zip",
                               "7d86596f16c6e85d45a50312f5e16ccb51e059b62e3d7308110cb65b4c799a4d")},
        ports=(stores.Port("api", 6333, "BRAIN_QDRANT_PORT"), stores.Port("grpc", 6334, "BRAIN_QDRANT_GRPC_PORT")),
        data=("",), layout=native_layout,
        telemetry="telemetry_disabled: true in the config file"),
)
