"""Memgraph — the graph role, over Bolt (§stores · §graphstore.CONTRACT).

An in-memory graph database that speaks openCypher, so the queries are the shared ones
(§backends._cypher) and this file is the connection. Memgraph has no HTTP API — only Bolt — so it uses
brain's own Bolt client (§backends._bolt), standard library only.

What it is sent: document ids and the links between them; names only when the person turned `names` on
(§graphstore.sync decides that, for every graph backend).

In Docker: its data folder holds the write-ahead log and the snapshots, and it recovers from them on
start — so removing the container keeps the graph (measured: a forced removal, a new container, the same
nodes). Its own usage reporting is turned off (`--telemetry-enabled=false`; on by default).
"""
from __future__ import annotations

from typing import List, Tuple

from brain import stores
from brain.backends import _bolt
from brain.backends._cypher import CypherGraph


class MemgraphGraph(CypherGraph):
    """Memgraph over Bolt: one connection per transaction."""
    backend = "memgraph"
    # each runs on its own (Memgraph refuses index changes inside a transaction); all are safe to repeat
    DDL = ("CREATE INDEX ON :BrainDoc(key)",
           "CREATE INDEX ON :BrainDoc(ns)",
           "CREATE CONSTRAINT ON (d:BrainDoc) ASSERT d.key IS UNIQUE")

    def __init__(self, c: dict):
        super().__init__(c)
        self.user = str(stores.opt(c, "user"))
        self.password = stores.secret(c)

    def run(self, statements: List[Tuple[str, dict]], timeout: float = 0.0) -> List[List[list]]:
        with _bolt.connect(self.url, self.user, self.password, timeout or stores.QUERY_TIMEOUT) as conn:
            return conn.run(statements)

    def ping(self) -> dict:
        with _bolt.connect(self.url, self.user, self.password, stores.QUERY_TIMEOUT) as conn:
            rows = conn.run([("SHOW VERSION", {})])[0]
        return {"ok": True, "version": str(rows[0][0]) if rows and rows[0] else ""}


BACKEND = stores.Backend(
    name="memgraph", role="graph", title="Memgraph", make=MemgraphGraph, url="bolt://localhost:7687",
    secret=stores.Secret(env="MEMGRAPH_PASSWORD", field="memgraph_password"),
    options=(stores.Option("user", "brain", "user (the password comes from MEMGRAPH_PASSWORD or secrets.json; "
                                            "none for a server without authentication)"),),
    docker=stores.Docker(image="memgraph/memgraph:3.13.2",
                         ports=(stores.Port("bolt", 7687, "BRAIN_MEMGRAPH_PORT", host=7688),),
                         scheme="bolt",
                         data=(("data", "/var/lib/memgraph"), ("logs", "/var/log/memgraph")),
                         env={"MEMGRAPH_USER": "brain", "MEMGRAPH_PASSWORD": "{secret}"}, generate_secret=True,
                         command=("--data-recovery-on-startup=true", "--storage-wal-enabled=true",
                                  "--storage-snapshot-on-exit=true"),
                         user="host", telemetry_off=("--telemetry-enabled=false",)),   # on by default
)
