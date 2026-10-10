"""Neo4j — the graph role, over Neo4j's HTTP transaction API (§stores · §graphstore.CONTRACT).

What it is sent: document ids and the links between them; names only when the person turned `names` on
(§graphstore.sync decides that, for every graph backend). The queries are the shared openCypher ones
(§backends._cypher); this file is the connection.
"""
from __future__ import annotations

from typing import List, Tuple

from brain import stores
from brain.backends._cypher import CypherGraph


class Neo4jGraph(CypherGraph):
    """Neo4j over HTTP: one POST to `/db/<database>/tx/commit` per transaction."""
    backend = "neo4j"
    DDL = ("CREATE CONSTRAINT brain_doc_key IF NOT EXISTS FOR (d:BrainDoc) REQUIRE d.key IS UNIQUE",
           "CREATE INDEX brain_doc_ns IF NOT EXISTS FOR (d:BrainDoc) ON (d.ns)")

    def __init__(self, c: dict):
        super().__init__(c)
        self.database = str(stores.opt(c, "database"))
        self.headers = stores.basic_auth(str(stores.opt(c, "user")), stores.secret(c))

    def target(self) -> str:
        return "neo4j:%s/%s#%s" % (self.url, self.database, self.ns)

    def run(self, statements: List[Tuple[str, dict]], timeout: float = 0.0) -> List[List[list]]:
        r = stores.http_json("POST", "%s/db/%s/tx/commit" % (self.url, self.database),
                             {"statements": [{"statement": s, "parameters": p} for s, p in statements]},
                             self.headers, timeout or stores.QUERY_TIMEOUT)
        errs = r.get("errors") or []
        if errs:
            raise stores.StoreError("neo4j: %s" % "; ".join("%s %s" % (e.get("code"), e.get("message"))
                                                             for e in errs)[:300])
        return [[row.get("row") for row in (res.get("data") or [])] for res in (r.get("results") or [])]

    def ping(self) -> dict:
        rows = self.run([("CALL dbms.components() YIELD versions, edition RETURN versions[0], edition", {})])
        v = rows[0][0] if rows and rows[0] else ["", ""]
        return {"ok": True, "version": "%s %s" % (v[0], v[1])}


BACKEND = stores.Backend(
    name="neo4j", role="graph", title="Neo4j", make=Neo4jGraph, url="http://localhost:7474",
    secret=stores.Secret(env="NEO4J_PASSWORD", field="neo4j_password", required=True),
    options=(stores.Option("database", "neo4j", "database name"),
             stores.Option("user", "neo4j", "user (the password comes from NEO4J_PASSWORD or secrets.json)")),
    docker=stores.Docker(image="neo4j:5.26.31-community",
                         ports=(stores.Port("http", 7474, "BRAIN_NEO4J_HTTP_PORT"),
                                stores.Port("bolt", 7687, "BRAIN_NEO4J_BOLT_PORT")),
                         data=(("data", "/data"), ("logs", "/logs")),
                         env={"NEO4J_AUTH": "neo4j/{secret}"}, generate_secret=True),
)
