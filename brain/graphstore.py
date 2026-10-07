"""Graph stores — ★where traversal of the links between notes runs★ (§stores for the shape and the rules).

The graph is ★resolved edges between documents★: a link written in a note (`[[name]]`, a path) is
resolved through `name_map` (names, aliases, declarations) into `(source doc id, target doc id)`.
Resolution stays local — it is about your files and their names. What a graph store holds and answers
is the resolved structure, through five questions every backend answers identically:

  neighbors(ids)   one hop, both directions, per seed      (recall's related slot)
  incoming(id)     who links here                           (the `neighbors` tool)
  linked(a, b)     are two documents linked either way      (duplicate detection)
  targets()        every document something links to       (orphans = memories outside this set)
  edges()          every resolved edge                      (the graph view)

  sqlite   the local `links` table joined through `name_map` — the default, and the canonical copy
  neo4j    Neo4j's HTTP API (Cypher); nodes `:BrainDoc {key, ns, id}`, relationships `:LINKS_TO`

Add a backend: a class with those five plus `backend`, `remote`, `target`, `ping` (and, if remote,
`ensure`, `reset`, `upsert_nodes`, `delete_nodes`, `add_edges`, `remove_edges`), then a row in `make`.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Sequence, Set, Tuple

from brain import stores

_RESOLVED = "FROM links l JOIN name_map m ON m.key=l.dst_name"
BATCH = 1000                   # rows per Cypher statement (UNWIND)


class SqliteGraph:
    """The local copy — the queries recall, health and the graph view have always used."""
    backend = "sqlite"
    remote = False

    def __init__(self, c: dict = None):
        self.c = c or {}

    def target(self) -> str:
        return "sqlite"

    def ping(self) -> dict:
        return {"ok": True}

    def neighbors(self, db: sqlite3.Connection, ids: Sequence[int]) -> Dict[int, Set[int]]:
        out: Dict[int, Set[int]] = {}
        for did in ids:
            s = {r[0] for r in db.execute("SELECT DISTINCT m.doc_id %s WHERE l.src_id=?" % _RESOLVED, (did,))}
            s |= {r[0] for r in db.execute("SELECT DISTINCT l.src_id %s WHERE m.doc_id=?" % _RESOLVED, (did,))}
            out[int(did)] = s
        return out

    def incoming(self, db: sqlite3.Connection, did: int) -> Set[int]:
        return {r[0] for r in db.execute("SELECT DISTINCT l.src_id %s WHERE m.doc_id=?" % _RESOLVED, (did,))}

    def linked(self, db: sqlite3.Connection, a: int, b: int) -> bool:
        return db.execute("SELECT 1 %s WHERE (l.src_id=? AND m.doc_id=?) OR (l.src_id=? AND m.doc_id=?) LIMIT 1"
                          % _RESOLVED, (a, b, b, a)).fetchone() is not None

    def targets(self, db: sqlite3.Connection) -> Set[int]:
        return {r[0] for r in db.execute("SELECT DISTINCT m.doc_id %s" % _RESOLVED)}

    def edges(self, db: sqlite3.Connection) -> List[Tuple[int, int]]:
        return sorted({(r[0], r[1]) for r in db.execute("SELECT DISTINCT l.src_id, m.doc_id %s" % _RESOLVED)})


class Neo4jGraph:
    """Neo4j over its HTTP API. What it is sent: document ids and the edges between them; names only on request."""
    backend = "neo4j"
    remote = True

    def __init__(self, c: dict):
        o = c.get("options") or {}
        self.url = c["url"]
        self.database = str(o.get("database") or "neo4j")
        self.names = bool(o.get("names", False))
        self.ns = str(o.get("namespace") or stores.namespace())
        self.headers = stores.basic_auth(str(o.get("user") or "neo4j"), stores.secret(c))

    def target(self) -> str:
        return "neo4j:%s/%s#%s" % (self.url, self.database, self.ns)

    def _key(self, did: int) -> str:
        return "%s:%d" % (self.ns, int(did))

    def run(self, statements: List[Tuple[str, dict]], timeout: float = 0.0) -> List[List[list]]:
        """Cypher statements in one transaction → each statement's rows."""
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

    def ensure(self) -> None:
        self.run([("CREATE CONSTRAINT brain_doc_key IF NOT EXISTS FOR (d:BrainDoc) REQUIRE d.key IS UNIQUE", {}),
                  ("CREATE INDEX brain_doc_ns IF NOT EXISTS FOR (d:BrainDoc) ON (d.ns)", {})],
                 timeout=stores.SYNC_TIMEOUT)

    def reset(self) -> None:
        self.ensure()
        self.run([("MATCH (d:BrainDoc {ns: $ns}) DETACH DELETE d", {"ns": self.ns})], timeout=stores.SYNC_TIMEOUT)

    def upsert_nodes(self, nodes: Sequence[Tuple[int, str]]) -> None:
        from brain import privacy
        for i in range(0, len(nodes), BATCH):
            # a name is the only text that can leave (and only with `names`) — through the outbound door first
            rows = [{"key": self._key(d), "id": int(d), "name": privacy.scrub(n)[0] if self.names else ""}
                    for d, n in nodes[i:i + BATCH]]
            self.run([("UNWIND $rows AS r MERGE (d:BrainDoc {key: r.key}) SET d.ns = $ns, d.id = r.id"
                       + (", d.name = r.name" if self.names else " REMOVE d.name"), {"rows": rows, "ns": self.ns})],
                     timeout=stores.SYNC_TIMEOUT)

    def delete_nodes(self, ids: Sequence[int]) -> None:
        for i in range(0, len(ids), BATCH):
            self.run([("UNWIND $keys AS k MATCH (d:BrainDoc {key: k}) DETACH DELETE d",
                       {"keys": [self._key(d) for d in ids[i:i + BATCH]]})], timeout=stores.SYNC_TIMEOUT)

    def add_edges(self, pairs: Sequence[Tuple[int, int]]) -> None:
        for i in range(0, len(pairs), BATCH):
            self.run([("UNWIND $pairs AS p MATCH (a:BrainDoc {key: p[0]}), (b:BrainDoc {key: p[1]}) "
                       "MERGE (a)-[:LINKS_TO]->(b)",
                       {"pairs": [[self._key(a), self._key(b)] for a, b in pairs[i:i + BATCH]]})],
                     timeout=stores.SYNC_TIMEOUT)

    def remove_edges(self, pairs: Sequence[Tuple[int, int]]) -> None:
        for i in range(0, len(pairs), BATCH):
            self.run([("UNWIND $pairs AS p MATCH (a:BrainDoc {key: p[0]})-[r:LINKS_TO]->(b:BrainDoc {key: p[1]}) "
                       "DELETE r", {"pairs": [[self._key(a), self._key(b)] for a, b in pairs[i:i + BATCH]]})],
                     timeout=stores.SYNC_TIMEOUT)

    def neighbors(self, db: sqlite3.Connection, ids: Sequence[int]) -> Dict[int, Set[int]]:
        out: Dict[int, Set[int]] = {int(d): set() for d in ids}
        if not ids:
            return out
        rows = self.run([("UNWIND $keys AS k MATCH (a:BrainDoc {key: k})-[:LINKS_TO]-(b:BrainDoc) "
                          "RETURN a.id, collect(DISTINCT b.id)", {"keys": [self._key(d) for d in ids]})])[0]
        for a, bs in rows:
            out[int(a)] = {int(b) for b in bs}
        return out

    def incoming(self, db: sqlite3.Connection, did: int) -> Set[int]:
        rows = self.run([("MATCH (a:BrainDoc)-[:LINKS_TO]->(b:BrainDoc {key: $k}) RETURN DISTINCT a.id",
                          {"k": self._key(did)})])[0]
        return {int(r[0]) for r in rows}

    def linked(self, db: sqlite3.Connection, a: int, b: int) -> bool:
        rows = self.run([("MATCH (x:BrainDoc {key: $a})-[:LINKS_TO]-(y:BrainDoc {key: $b}) RETURN count(*) > 0",
                          {"a": self._key(a), "b": self._key(b)})])[0]
        return bool(rows and rows[0][0])

    def targets(self, db: sqlite3.Connection) -> Set[int]:
        rows = self.run([("MATCH (:BrainDoc {ns: $ns})-[:LINKS_TO]->(d:BrainDoc) RETURN DISTINCT d.id",
                          {"ns": self.ns})], timeout=stores.SYNC_TIMEOUT)[0]
        return {int(r[0]) for r in rows}

    def edges(self, db: sqlite3.Connection) -> List[Tuple[int, int]]:
        rows = self.run([("MATCH (a:BrainDoc {ns: $ns})-[:LINKS_TO]->(b:BrainDoc) RETURN a.id, b.id",
                          {"ns": self.ns})], timeout=stores.SYNC_TIMEOUT)[0]
        return sorted({(int(a), int(b)) for a, b in rows})


def make(c: dict):
    if c.get("backend") == "neo4j" and not c.get("error"):
        return Neo4jGraph(c)
    return SqliteGraph(c)


LOCAL = SqliteGraph()


def ask(db: sqlite3.Connection, question: str, *args):
    """One graph question, answered by the chosen backend when it is in sync — else by the local copy (§stores.serve)."""
    c = stores.choice("graph")
    if c["backend"] == "sqlite" or c.get("error"):
        return getattr(LOCAL, question)(db, *args)
    be = make(c)
    return stores.serve(db, "graph", be.target(), lambda: getattr(be, question)(db, *args),
                        lambda: getattr(LOCAL, question)(db, *args))


def sync(db: sqlite3.Connection, backend, full: bool = False, progress: bool = False) -> dict:
    """Push the local graph (every document as a node, every resolved edge) to `backend`. Only the difference."""
    target = backend.target()
    names = {r[0]: r[1] for r in db.execute("SELECT id, name FROM docs")}
    want_nodes = {str(d): (n if getattr(backend, "names", False) else "1") for d, n in names.items()}
    want_edges = {"%d>%d" % e: "1" for e in LOCAL.edges(db) if e[0] in names and e[1] in names}
    stats = {"backend": backend.backend, "target": target, "nodes": len(want_nodes), "edges": len(want_edges),
             "sent_nodes": 0, "deleted_nodes": 0, "sent_edges": 0, "removed_edges": 0}
    if full:
        backend.reset()
        stores.ledger_clear(db, "graph.", target)
    have_nodes = stores.ledger(db, "graph.node", target)
    have_edges = stores.ledger(db, "graph.edge", target)
    node_up = [k for k, v in want_nodes.items() if have_nodes.get(k) != v]
    node_gone = [k for k in have_nodes if k not in want_nodes]
    edge_add = [k for k in want_edges if k not in have_edges]
    edge_gone = [k for k in have_edges if k not in want_edges]
    if not (node_up or node_gone or edge_add or edge_gone):
        stats["unchanged"] = True
        return stats
    backend.ensure()

    def pair(k: str) -> Tuple[int, int]:
        a, b = k.split(">")
        return int(a), int(b)
    # order: edges out → nodes out → nodes in → edges in (an edge never points at a missing node)
    if edge_gone:
        backend.remove_edges([pair(k) for k in edge_gone])
        stores.ledger_apply(db, "graph.edge", target, {}, edge_gone)
    if node_gone:
        # every edge touching a removed node is already in `edge_gone` (a wanted edge needs both nodes),
        # so the DETACH below removes nothing the ledger still lists
        backend.delete_nodes([int(k) for k in node_gone])
        stores.ledger_apply(db, "graph.node", target, {}, node_gone)
    if node_up:
        backend.upsert_nodes([(int(k), names[int(k)]) for k in node_up])
        stores.ledger_apply(db, "graph.node", target, {k: want_nodes[k] for k in node_up})
    if edge_add:
        backend.add_edges([pair(k) for k in edge_add])
        stores.ledger_apply(db, "graph.edge", target, {k: "1" for k in edge_add})
    stats.update(sent_nodes=len(node_up), deleted_nodes=len(node_gone), sent_edges=len(edge_add),
                 removed_edges=len(edge_gone))
    return stats
