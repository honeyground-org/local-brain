"""The graph role in openCypher — shared by every backend that speaks Cypher (§graphstore.CONTRACT).

Every question and every write a Cypher database is asked lives here, once. A backend subclasses
`CypherGraph` and brings only what differs between databases:

  run(statements, timeout)   send [(cypher, params)] in one transaction → each statement's rows
  ping()                     {ok, version}
  DDL                        the statements `ensure` runs (each must be safe to run again)

The model: nodes `:BrainDoc {key, ns, id[, name]}`, relationships `:LINKS_TO`. `key` = "<ns>:<id>" so
several brains can share one database; `ns` is derived from the brain's home (§stores.namespace).
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Sequence, Set, Tuple

from brain import stores

BATCH = 1000                   # rows per statement (UNWIND)


class CypherGraph:
    backend = ""
    remote = True
    DDL: Tuple[str, ...] = ()

    def __init__(self, c: dict):
        self.c = c
        self.url = c.get("url", "")
        self.ns = str(stores.opt(c, "namespace") or stores.namespace())

    # ── what a subclass provides ──
    def run(self, statements: List[Tuple[str, dict]], timeout: float = 0.0) -> List[List[list]]:
        raise NotImplementedError

    def ping(self) -> dict:
        raise NotImplementedError

    def target(self) -> str:
        return "%s:%s#%s" % (self.backend, self.url, self.ns)

    # ── writes ──
    def _key(self, did: int) -> str:
        return "%s:%d" % (self.ns, int(did))

    def ensure(self) -> None:
        for s in self.DDL:
            self.run([(s, {})], timeout=stores.SYNC_TIMEOUT)

    def drop(self) -> None:
        self.run([("MATCH (d:BrainDoc {ns: $ns}) DETACH DELETE d", {"ns": self.ns})], timeout=stores.SYNC_TIMEOUT)

    def reset(self) -> None:
        self.ensure()
        self.drop()

    def upsert_nodes(self, nodes: Sequence[Tuple[int, str]]) -> None:
        # the name is "" unless the person turned `names` on (§graphstore.sync); null removes the property
        for i in range(0, len(nodes), BATCH):
            rows = [{"key": self._key(d), "id": int(d), "name": n or ""} for d, n in nodes[i:i + BATCH]]
            self.run([("UNWIND $rows AS r MERGE (d:BrainDoc {key: r.key}) SET d.ns = $ns, d.id = r.id, "
                       "d.name = CASE WHEN r.name = '' THEN null ELSE r.name END", {"rows": rows, "ns": self.ns})],
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

    def counts(self) -> Tuple[int, int]:
        """(nodes, edges) this brain holds here — what a sync compares with its ledger."""
        rows = self.run([("MATCH (d:BrainDoc {ns: $ns}) RETURN count(d)", {"ns": self.ns}),
                         ("MATCH (:BrainDoc {ns: $ns})-[r:LINKS_TO]->() RETURN count(r)", {"ns": self.ns})],
                        timeout=stores.SYNC_TIMEOUT)
        return int(rows[0][0][0]), int(rows[1][0][0])

    # ── the five questions ──
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
