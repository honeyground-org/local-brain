"""Graph stores — ★where traversal of the links between notes runs★ (§stores for the shape and the rules).

The graph is ★resolved edges between documents★: a link written in a note (`[[name]]`, a path) is
resolved through `name_map` (names, aliases, declarations) into `(source doc id, target doc id)`.
Resolution stays local — it is about your files and their names. What a graph store holds and answers
is the resolved structure, through five questions every backend answers identically:

  neighbors(db, ids)   one hop, both directions, per seed      (recall's related slot)
  incoming(db, id)     who links here                           (the `neighbors` tool)
  linked(db, a, b)     are two documents linked either way      (duplicate detection)
  targets(db)          every document something links to       (orphans = memories outside this set)
  edges(db)            every resolved edge                      (the graph view)

  sqlite   the local `links` table joined through `name_map` — the default, and the canonical copy
  others   one file each in `brain/backends/` (§stores "One file per backend"); a Cypher database
           needs only its connection (§backends._cypher.CypherGraph)

A dedicated graph backend is a class with the five questions and the rest of `CONTRACT`:

  target() → str               where this copy lives — the ledger's key, so it must be stable
  ping() → {ok, version}       raises StoreError when the database does not answer
  ensure() · reset() · drop()  create what it needs · empty this brain's graph · remove it
  upsert_nodes([(id, name)])   ★name is "" unless the person turned `names` on★ — then already scrubbed
  delete_nodes(ids)            and every edge touching them
  add_edges([(a, b)]) · remove_edges([(a, b)])
  counts() → (nodes, edges)    what this brain holds there — compared with the ledger
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Sequence, Set, Tuple

from brain import stores

_RESOLVED = "FROM links l JOIN name_map m ON m.key=l.dst_name"

CONTRACT = ("target", "ping", "ensure", "reset", "drop", "upsert_nodes", "delete_nodes", "add_edges",
            "remove_edges", "counts", "neighbors", "incoming", "linked", "targets", "edges")


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


def make(c: dict):
    """The backend a choice names (§stores.backends) — the local copy when it names none that loads."""
    b = None if c.get("error") else stores.backends("graph").get(c.get("backend", ""))
    if b is None or b.make is None:
        return SqliteGraph(c)
    return b.make(c)


LOCAL = SqliteGraph()


def ask(db: sqlite3.Connection, question: str, *args):
    """One graph question, answered by the chosen backend when it is in sync — else by the local copy (§stores.serve)."""
    c = stores.choice("graph")
    if c["backend"] == "sqlite" or c.get("error"):
        return getattr(LOCAL, question)(db, *args)
    be = make(c)
    return stores.serve(db, "graph", be.target(), lambda: getattr(be, question)(db, *args),
                        lambda: getattr(LOCAL, question)(db, *args))


def sync(db: sqlite3.Connection, backend, full: bool = False, progress: bool = False,
         names: bool = False) -> dict:
    """Push the local graph (every document as a node, every resolved edge) to `backend`. Only the difference.

    ⛔ A node's name is sent only with `names` — and then through the outbound door first (§privacy.scrub).
       Decided here, for every backend: a backend is handed "" and cannot send what it never received.
    """
    from brain import privacy
    target = backend.target()
    labels = {r[0]: (privacy.scrub(r[1])[0] if names else "")
              for r in db.execute("SELECT id, name FROM docs")}
    want_nodes = {str(d): (n or "1") for d, n in labels.items()}
    want_edges = {"%d>%d" % e: "1" for e in LOCAL.edges(db) if e[0] in labels and e[1] in labels}
    stats = {"backend": backend.backend, "target": target, "nodes": len(want_nodes), "edges": len(want_edges),
             "sent_nodes": 0, "deleted_nodes": 0, "sent_edges": 0, "removed_edges": 0}
    if full:
        backend.reset()
        stores.ledger_clear(db, "graph.", target)
    have_nodes = stores.ledger(db, "graph.node", target)
    have_edges = stores.ledger(db, "graph.edge", target)
    if (have_nodes or have_edges) and not full:
        # ★Does the database still hold what the ledger says★ (§vecstore.sync) — else rebuild it from the local copy
        held, expected = backend.counts(), (len(have_nodes), len(have_edges))
        if held != expected:
            backend.reset()
            stores.ledger_clear(db, "graph.", target)
            have_nodes, have_edges = {}, {}
            stats["healed"] = {"expected": list(expected), "held": list(held)}
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
        backend.upsert_nodes([(int(k), labels[int(k)]) for k in node_up])
        stores.ledger_apply(db, "graph.node", target, {k: want_nodes[k] for k in node_up})
    if edge_add:
        backend.add_edges([pair(k) for k in edge_add])
        stores.ledger_apply(db, "graph.edge", target, {k: "1" for k in edge_add})
    stats.update(sent_nodes=len(node_up), deleted_nodes=len(node_gone), sent_edges=len(edge_add),
                 removed_edges=len(edge_gone))
    return stats
