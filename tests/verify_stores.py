#!/usr/bin/env python3
"""★The vector and graph databases are replaceable★ — the contract, checked without any database. (local · budget 0)

## What this holds down (§brain/stores.py) — on a temp home with a synthetic corpus, never a person's notes

In-memory stand-ins play the dedicated databases, so the contract is checked anywhere; the real ones
(Qdrant, Neo4j) are measured by `tests/verify_stores_live.py` when they are running.

  ① by default nothing changes: sqlite answers both roles, and indexing sends nothing anywhere
  ② a sync sends the local copy, and a second sync sends ★nothing★ (the ledger knows what is there)
  ③ the database answers the same questions the same way (`stores.check`) — and recall uses it
  ④ an edited note sends ★only its difference★, a removed note is removed there too (edges with it)
  ⑤ a database not yet in sync never answers; one that fails falls back to the local copy, says so,
     and is not asked again for a while
  ⑥ a choice is a person's: written to config, refused for the wrong role, the environment wins

How to run:  PYTHONPATH=. python3 tests/verify_stores.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-stores-")
_KEYS = ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_EMBED_DIM", "BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE",
         "BRAIN_ADAPTIVE_LEARN", "BRAIN_TRANSLIT_MS")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ.update(BRAIN_HOME=os.path.join(_TMP, "home"), BRAIN_CONFIG=os.path.join(_TMP, "config.json"),
                  BRAIN_EMBED_DIM="16", BRAIN_ADAPTIVE_LEARN="0", BRAIN_TRANSLIT_MS="0")
for _k in ("BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE"):
    os.environ.pop(_k, None)

from brain import graphstore, search, store, stores, vecstore, vectors  # noqa: E402
from tests import _stores_fixture as fx  # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


class FakeVectors:
    """A vector database in memory — answers from what it was sent, nothing else."""
    backend, remote = "qdrant", True

    def __init__(self):
        self.points, self.calls, self.fail = {}, {"search": 0, "write": 0}, False

    def target(self, model, dim):
        return "fake-vector:%s/%d" % (model, dim)

    def ping(self):
        return {"ok": True, "version": "fake"}

    def ensure(self, model, dim):
        pass

    def reset(self, model, dim):
        self.points.clear()

    def delete_docs(self, model, dim, ids):
        self.calls["write"] += 1
        for k in [k for k in self.points if k[0] in set(ids)]:
            del self.points[k]

    def upsert(self, model, dim, rows):
        self.calls["write"] += 1
        for d, c, v in rows:
            self.points[(d, c)] = list(v)

    def search(self, db, q, model, dim, n):
        self.calls["search"] += 1
        if self.fail:
            raise stores.StoreError("fake vector database is down")
        best = {}
        for (d, _c), v in self.points.items():
            s = sum(a * b for a, b in zip(q, v))
            best[d] = max(best.get(d, -2.0), s)
        return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    def count(self, db, model, dim):
        return len(self.points)


class FakeGraph:
    """A graph database in memory — every answer is computed from the nodes and edges it was sent."""
    backend, remote, names = "neo4j", True, False

    def __init__(self):
        self.nodes, self.E, self.calls, self.fail = set(), set(), {"ask": 0, "write": 0}, False

    def target(self):
        return "fake-graph"

    def ping(self):
        return {"ok": True, "version": "fake"}

    def ensure(self):
        pass

    def reset(self):
        self.nodes.clear()
        self.E.clear()

    def upsert_nodes(self, nodes):
        self.calls["write"] += 1
        self.nodes |= {d for d, _ in nodes}

    def delete_nodes(self, ids):
        self.calls["write"] += 1
        gone = set(ids)
        self.nodes -= gone
        self.E = {e for e in self.E if not (set(e) & gone)}     # DETACH

    def add_edges(self, pairs):
        self.calls["write"] += 1
        self.E |= {p for p in pairs if p[0] in self.nodes and p[1] in self.nodes}

    def remove_edges(self, pairs):
        self.calls["write"] += 1
        self.E -= set(pairs)

    def _ask(self):
        self.calls["ask"] += 1
        if self.fail:
            raise stores.StoreError("fake graph database is down")

    def neighbors(self, db, ids):
        self._ask()
        return {i: {b for a, b in self.E if a == i} | {a for a, b in self.E if b == i} for i in ids}

    def incoming(self, db, did):
        self._ask()
        return {a for a, b in self.E if b == did}

    def linked(self, db, a, b):
        self._ask()
        return (a, b) in self.E or (b, a) in self.E

    def targets(self, db):
        self._ask()
        return {b for _, b in self.E}

    def edges(self, db):
        self._ask()
        return sorted(self.E)


FV, FG = FakeVectors(), FakeGraph()
_real_vmake, _real_gmake = vecstore.make, graphstore.make
vecstore.make = lambda c: FV if c.get("backend") == "qdrant" and not c.get("error") else _real_vmake(c)
graphstore.make = lambda c: FG if c.get("backend") == "neo4j" and not c.get("error") else _real_gmake(c)


def index(db) -> dict:
    return store.reindex(db, recalibrate=False, refresh_history=False)


def main() -> int:
    mem = fx.write_corpus(_TMP)
    db = store.connect()
    index(db)
    n_chunks = fx.fill_vectors(db)
    fx.fill_questions(db)
    n_docs = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    local_edges = graphstore.LOCAL.edges(db)

    print("① by default nothing changes")
    check("both roles are the local copy", [stores.choice(r)["backend"] for r in stores.ROLES] == ["sqlite", "sqlite"])
    check("indexing sends nothing anywhere", stores.sync_if_chosen(db) == {} and FV.calls["write"] == FG.calls["write"] == 0)
    check("the graph answers locally", graphstore.ask(db, "edges") == local_edges and FG.calls["ask"] == 0,
          "%d edges" % len(local_edges))

    print("\n② a sync sends the local copy — once")
    os.environ.update(BRAIN_VECTOR_STORE="qdrant", BRAIN_GRAPH_STORE="neo4j")
    r = stores.sync(db)
    check("vector: every document with embeddings is sent", r["vector"]["sent_docs"] == n_docs
          and len(FV.points) == n_chunks, "%s docs · %d chunks" % (r["vector"]["sent_docs"], len(FV.points)))
    check("graph: every document is a node, every resolved edge an edge",
          FG.nodes == {d for (d,) in db.execute("SELECT id FROM docs")} and sorted(FG.E) == local_edges,
          "%d nodes · %d edges" % (len(FG.nodes), len(FG.E)))
    writes = dict(FV.calls), dict(FG.calls)
    r = stores.sync(db)
    check("★a second sync sends nothing★", r["vector"].get("unchanged") and r["graph"].get("unchanged")
          and (FV.calls, FG.calls) == writes)

    print("\n③ the database answers the same questions the same way")
    c = stores.check(db)
    check("vector: the same top documents and scores", c["vector"]["top1_agree"] == c["vector"]["queries"] > 0
          and c["vector"]["overlap_at_10"] == 1.0 and c["vector"]["max_score_delta"] < 1e-6,
          json.dumps({k: c["vector"][k] for k in ("queries", "top1_agree", "overlap_at_10", "max_score_delta")}))
    check("graph: the same neighbours, edges and link targets", c["graph"]["neighbors_same"] == n_docs
          and c["graph"]["edges_same"] and c["graph"]["targets_same"])
    before = FG.calls["ask"]
    alpha = db.execute("SELECT id FROM docs WHERE name='alpha_overview'").fetchone()[0]
    via = search._neighbors(db, [alpha])
    check("recall's related slot is answered by the graph database", FG.calls["ask"] > before
          and sorted(via) == sorted(graphstore.LOCAL.neighbors(db, [alpha])[alpha] - {alpha}))
    q = vectors._qcache_get(db, "fixture question 0")
    before = FV.calls["search"]
    check("semantic search is answered by the vector database", [d for d, *_ in vectors.search(db, "", k=3, qvec=list(q))]
          == [d for d, _ in vecstore.SqliteVectors().search(db, q, vectors.model_tag(), vectors.DIM, 3)]
          and FV.calls["search"] == before + 1)

    print("\n④ an edit sends its difference; a removal is removed there too")
    fx.write_note(mem, "zeta_alone", "Now it links to [[theta_log]].")
    out = index(db)
    zeta = db.execute("SELECT id FROM docs WHERE name='zeta_alone'").fetchone()[0]
    theta = db.execute("SELECT id FROM docs WHERE name='theta_log'").fetchone()[0]
    check("indexing pushed the new edge by itself", (zeta, theta) in FG.E and out.get("stores", {}).get("graph") == "sent",
          str(out.get("stores")))
    os.remove(os.path.join(mem, "gamma_notes.md"))
    gamma = db.execute("SELECT id FROM docs WHERE name='gamma_notes'").fetchone()[0]
    index(db)
    check("a removed note leaves the graph with its edges", gamma not in FG.nodes
          and not any(gamma in e for e in FG.E) and sorted(FG.E) == graphstore.LOCAL.edges(db))
    check("…and the vector database", not any(d == gamma for d, _ in FV.points)
          and db.execute("SELECT COUNT(*) FROM vectors WHERE doc_id=?", (gamma,)).fetchone()[0] == 0)
    r = stores.sync(db)
    check("the ledger agrees with what is there (nothing left to send)",
          r["vector"].get("unchanged") and r["graph"].get("unchanged"))

    print("\n⑤ out of sync never answers · a failure falls back and says so")
    real = stores.health(db, "graph")
    stores.record(db, "graph", synced="some-other-target")
    before = FG.calls["ask"]
    got = graphstore.ask(db, "edges")
    check("a database not known to hold this copy is not asked", FG.calls["ask"] == before
          and got == graphstore.LOCAL.edges(db))
    stores.record(db, "graph", synced=real["synced"])
    FG.fail = True
    got = graphstore.ask(db, "targets")
    h = stores.health(db, "graph")
    check("a failing database → the local copy answers", got == graphstore.LOCAL.targets(db))
    check("…and it is recorded for `brain stores`", h.get("ok") is False and "down" in (h.get("error") or "")
          and h.get("fallbacks") == 1, str({k: h.get(k) for k in ("ok", "error", "fallbacks")}))
    before = FG.calls["ask"]
    graphstore.ask(db, "edges")
    check("…and it is not asked again for a while", FG.calls["ask"] == before)
    FG.fail = False
    stores._DOWN.clear()

    print("\n⑥ a choice is a person's")
    for k in ("BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE"):
        os.environ.pop(k)
    entry = stores.set_choice("vector", "qdrant", "http://127.0.0.1:6333/", {"exact": True, "collection_prefix": ""})
    cfg = json.load(open(os.environ["BRAIN_CONFIG"]))
    check("written to config.json, without empty options", cfg["stores"]["vector"] == entry
          == {"backend": "qdrant", "url": "http://127.0.0.1:6333", "exact": True}, json.dumps(entry))
    check("read back from config", stores.choice("vector")["backend"] == "qdrant"
          and stores.choice("vector")["source"] == "config")
    for bad in (("graph", "qdrant"), ("vector", "neo4j"), ("cache", "sqlite")):
        try:
            stores.set_choice(*bad)
            check("refused: %s=%s" % bad, False)
        except ValueError:
            check("refused: %s=%s" % bad, True)
    os.environ["BRAIN_VECTOR_STORE"] = "sqlite"
    check("the environment wins over config", stores.choice("vector")["backend"] == "sqlite"
          and stores.choice("vector")["source"] == "env")
    os.environ["BRAIN_VECTOR_STORE"] = "chroma"
    check("an unknown backend is named, and the local copy answers",
          stores.choice("vector")["error"] == "unknown" and isinstance(vecstore.make(stores.choice("vector")),
                                                                     vecstore.SqliteVectors))
    os.environ.pop("BRAIN_VECTOR_STORE")

    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the vector and graph databases are replaceable, and the local copy keeps answering")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
        for k, v in _SAVED.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    sys.exit(code)
