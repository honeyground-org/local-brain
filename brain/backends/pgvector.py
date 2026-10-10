"""pgvector — the vector role, in Postgres (§stores · §vecstore.CONTRACT).

For a team that already runs Postgres: one table per (namespace, embedding model, dimension), spoken to
over Postgres' own wire protocol with brain's standard-library client (§backends._pgwire).

What it is sent: the vectors and `{doc_id, chunk_no}` — never text (all `vecstore.sync` hands any vector
backend).

★Exact by default★ — `GROUP BY doc_id` over the cosine distance of every chunk, the same question the local
scan answers, so the answers are the same. `index = "hnsw"` adds pgvector's approximate index and asks it
for `n_docs × MAX_CHUNKS_PER_DOC` nearest chunks first, for a corpus where a full scan is too slow.

Measured on the author's notes (1,923 documents, 9,852 chunks, 2026-10-10), 40 questions: exact — every
answer identical, score difference 0.0, ~29 ms (local scan ~190 ms); hnsw — 100% of the top 10 once its
search breadth is raised with the request (84.5% at pgvector's default of 40), ~33 ms: at this size the
index buys nothing, so it is not the default.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import urllib.parse
from typing import List, Sequence, Tuple

from brain import stores
from brain.backends import _pgwire as pg

MISSING = "42P01"                  # undefined_table


class PgVectors:
    """pgvector over the Postgres wire protocol: one session per call."""
    backend = "pgvector"
    remote = True

    def __init__(self, c: dict):
        self.url = c["url"]
        self.user = str(stores.opt(c, "user"))
        self.password = stores.secret(c)
        self.sslmode = str(stores.opt(c, "sslmode"))
        self.index = str(stores.opt(c, "index"))
        self.ef_search = max(1, min(1000, int(stores.opt(c, "ef_search"))))
        self.prefix = re.sub(r"[^a-z0-9_]", "_", str(stores.opt(c, "table_prefix")).lower())
        ns = re.sub(r"[^a-z0-9_]", "_", str(stores.opt(c, "namespace") or "").lower())
        if ns:
            self.prefix += "_" + ns

    def table(self, model: str, dim: int) -> str:
        """A lower-case identifier of at most 63 bytes (Postgres' limit) — a long one keeps a hash of the rest."""
        name = "%s_%s_%d" % (self.prefix, re.sub(r"[^a-z0-9_]", "_", model.lower()), dim)
        if len(name) > 63:
            name = name[:52] + "_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        return name

    def target(self, model: str, dim: int) -> str:
        p = urllib.parse.urlsplit(self.url)
        return "pgvector:%s://%s%s/%s" % (p.scheme, p.netloc.rsplit("@", 1)[-1], p.path, self.table(model, dim))

    def _q(self, sql: str, timeout: float = 0.0) -> List[list]:
        try:
            with pg.connect(self.url, self.user, self.password, timeout or stores.QUERY_TIMEOUT, self.sslmode) as c:
                return c.query(sql)
        except pg.PgError as exc:
            if exc.code == MISSING:
                raise stores.StoreGone(str(exc)) from None
            raise

    def ping(self) -> dict:
        rows = self._q("SELECT current_setting('server_version'), "
                       "(SELECT default_version FROM pg_available_extensions WHERE name = 'vector')")
        v = rows[0] if rows else ["", ""]
        return {"ok": True, "version": "PostgreSQL %s · pgvector %s" % (v[0], v[1] or "not installed")}

    def ensure(self, model: str, dim: int) -> None:
        t = self.table(model, dim)
        held = self._q("CREATE EXTENSION IF NOT EXISTS vector; "
                       "CREATE TABLE IF NOT EXISTS %s (doc_id bigint NOT NULL, chunk_no integer NOT NULL, "
                       "vec vector(%d) NOT NULL, PRIMARY KEY (doc_id, chunk_no)); "
                       "SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass AND attname = 'vec'"
                       % (t, int(dim), pg.literal(t)), timeout=stores.SYNC_TIMEOUT)
        if held and held[0][0] and int(held[0][0]) != int(dim):
            raise stores.StoreError("table %s holds %s-dimensional vectors, not %d" % (t, held[0][0], dim))
        if self.index == "hnsw":
            self._q("CREATE INDEX IF NOT EXISTS %s_hnsw ON %s USING hnsw (vec vector_cosine_ops)" % (t[:57], t),
                    timeout=stores.SYNC_TIMEOUT)

    def drop(self, model: str, dim: int) -> None:
        self._q("DROP TABLE IF EXISTS %s" % self.table(model, dim), timeout=stores.SYNC_TIMEOUT)

    def reset(self, model: str, dim: int) -> None:
        self.drop(model, dim)
        self.ensure(model, dim)

    def delete_docs(self, model: str, dim: int, doc_ids: Sequence[int]) -> None:
        if doc_ids:
            self._q("DELETE FROM %s WHERE doc_id IN (%s)" % (self.table(model, dim), ",".join(str(int(d)) for d in doc_ids)),
                    timeout=stores.SYNC_TIMEOUT)

    def upsert(self, model: str, dim: int, rows: Sequence[Tuple[int, int, Sequence[float]]]) -> None:
        if rows:
            # ⛔ every value is a number brain made — never text from a note — so a literal is safe here
            values = ",".join("(%d,%d,'%s')" % (int(d), int(c), _vec(v)) for d, c, v in rows)
            self._q("INSERT INTO %s (doc_id, chunk_no, vec) VALUES %s ON CONFLICT (doc_id, chunk_no) "
                    "DO UPDATE SET vec = EXCLUDED.vec" % (self.table(model, dim), values), timeout=stores.SYNC_TIMEOUT)

    def search(self, db: sqlite3.Connection, q: Sequence[float], model: str, dim: int,
               n_docs: int) -> List[Tuple[int, float]]:
        from brain import vectors
        t, qv = self.table(model, dim), _vec(q)
        first = ""
        if self.index == "hnsw":
            # ⛔ the index returns at most `hnsw.ef_search` rows (40 by default) whatever LIMIT asks — measured
            #    2026-10-10: top-10 overlap 84.5% until the breadth was raised with the request
            want = int(n_docs) * vectors.MAX_CHUNKS_PER_DOC
            first = "SET hnsw.ef_search = %d; " % max(self.ef_search, min(1000, want))
            src = "(SELECT doc_id, vec <=> '%s' AS d FROM %s ORDER BY vec <=> '%s' LIMIT %d) AS near" % (qv, t, qv, want)
        else:
            src = "(SELECT doc_id, vec <=> '%s' AS d FROM %s) AS every_chunk" % (qv, t)
        rows = self._q(first + "SELECT doc_id, 1 - MIN(d) AS s FROM %s GROUP BY doc_id ORDER BY s DESC, doc_id LIMIT %d"
                       % (src, int(n_docs)))
        return [(int(d), float(s)) for d, s in rows]

    def count(self, db: sqlite3.Connection, model: str, dim: int) -> int:
        return int(self._q("SELECT COUNT(*) FROM %s" % self.table(model, dim))[0][0])


def _vec(v: Sequence[float]) -> str:
    """pgvector's text form. `repr` of a float32 read as a double parses back to the same float32."""
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


BACKEND = stores.Backend(
    name="pgvector", role="vector", title="pgvector (Postgres)", make=PgVectors,
    url="postgres://localhost:5432/brain",
    secret=stores.Secret(env="PGPASSWORD", field="pgvector_password"),
    options=(stores.Option("user", "brain", "Postgres user (the password comes from PGPASSWORD or secrets.json)"),
             stores.Option("sslmode", "prefer", "TLS: disable · prefer · require · verify-full"),
             stores.Option("index", "exact", "exact (the same answers as the local scan) · hnsw (approximate, faster)"),
             stores.Option("ef_search", 1000, "HNSW search breadth, with index=hnsw (pgvector allows up to 1000)"),
             stores.Option("table_prefix", "brain", "table name prefix")),
    docker=stores.Docker(image="pgvector/pgvector:0.8.7-pg17",
                         ports=(stores.Port("postgres", 5432, "BRAIN_PGVECTOR_PORT", host=5442),),
                         scheme="postgres", url_path="/brain",
                         data=(("data", "/var/lib/postgresql/data"),),
                         env={"POSTGRES_USER": "brain", "POSTGRES_DB": "brain", "POSTGRES_PASSWORD": "{secret}"},
                         generate_secret=True, user="host",
                         telemetry_off=()),          # Postgres reports nothing; the Docker check watches it
)
