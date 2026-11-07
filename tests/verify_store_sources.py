#!/usr/bin/env python3
"""★The same engine syncs another program's canonical copy★ — `source=` on the two syncs. (local · budget 0)

Cosmos keeps its own canonical copy (its memories and its entity graph) and wants the dedicated databases to
serve it ★exactly as they serve the notes★: the ledger, the diff, the self-healing and `stores.serve`, from
this code, so an update here is an update there (Sean, 2026-11-07). So `vecstore.sync` and `graphstore.sync`
take an optional `source`; without one they push the notes, as they always have.

  ① no `source` = the notes, as before: the same calls reach the database with and without
     an explicit `NotesSource` (the default is not a second code path)
  ② a host's rows are pushed, and a second sync sends ★nothing★ (the ledger in the host's own database)
  ③ a changed version resends only that document; a removed one is removed there too
  ④ the host's ledger never touches the notes' ledger — two copies, two databases, no crosstalk
  ⑤ a graph source: nodes and edges, ★no names unless `names`★, and an edge to an unknown node is not sent
  ⑥ a lost copy (the database reset behind the ledger's back) is noticed and refilled for a host too
  ⑦ control — must be red without the feature: a sync ★without★ `source` does not send the host's rows
  ⑧ `stores.sync_source` on a host's ★plain★ database (no `meta` table, no Row factory) records it in sync,
     and `serve` then asks the database
  ⑨ before any sync — or on another target — `serve` answers from the host's own copy
  ⑩ a database that fails: the host's copy answers, the failure is written down, and the next question
     within `DOWN_RETRY_SEC` does not knock again

How to run:  PYTHONPATH=. python3 tests/verify_store_sources.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-sources-")
_KEYS = ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_EMBED_DIM")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ.update(BRAIN_HOME=os.path.join(_TMP, "home"), BRAIN_CONFIG=os.path.join(_TMP, "config.json"),
                  BRAIN_EMBED_DIM="4")

from brain import graphstore, stores, vecstore  # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


class FakeVectors:
    backend, remote = "qdrant", True

    def __init__(self, name="fake-vector"):
        self.name, self.points, self.writes = name, {}, 0

    def target(self, model, dim):
        return "%s:%s/%d" % (self.name, model, dim)

    def ensure(self, model, dim):
        pass

    def reset(self, model, dim):
        self.points.clear()

    def delete_docs(self, model, dim, ids):
        self.writes += 1
        for k in [k for k in self.points if k[0] in set(ids)]:
            del self.points[k]

    def upsert(self, model, dim, rows):
        self.writes += 1
        for d, c, v in rows:
            self.points[(d, c)] = list(v)

    def count(self, db, model, dim):
        return len(self.points)


class FakeGraph:
    backend, remote = "neo4j", True

    def __init__(self):
        self.nodes, self.E, self.labels, self.writes = set(), set(), {}, 0

    def target(self):
        return "fake-graph:cosmos"

    def ensure(self):
        pass

    def reset(self):
        self.nodes.clear()
        self.E.clear()

    def upsert_nodes(self, nodes):
        self.writes += 1
        self.nodes |= {d for d, _ in nodes}
        self.labels.update(dict(nodes))

    def delete_nodes(self, ids):
        self.writes += 1
        self.nodes -= set(ids)
        self.E = {e for e in self.E if not (set(e) & set(ids))}

    def add_edges(self, pairs):
        self.writes += 1
        self.E |= set(pairs)

    def remove_edges(self, pairs):
        self.writes += 1
        self.E -= set(pairs)

    def counts(self):
        return len(self.nodes), len(self.E)


class HostVectors:
    """What a host hands in — its own rows, its own versions."""

    def __init__(self):
        self.docs = {101: ("a1", [[1, 0, 0, 0]]), 102: ("b1", [[0, 1, 0, 0], [0, 0, 1, 0]])}

    def model_dim(self):
        return "host-model", 4

    def desired(self, model, dim):
        return {str(d): "%s|%d" % (v, len(ch)) for d, (v, ch) in self.docs.items()}

    def rows(self, model, dim, ids):
        return [(d, i, vec) for d in ids for i, vec in enumerate(self.docs[d][1])]


class HostGraph:
    def __init__(self):
        self.N = [(1, "Alice Kim"), (2, "Pangyo office"), (3, "Project Apollo")]
        self.E = [(1, 2), (1, 3), (3, 99)]          # 99 is not a node — must not be sent

    def nodes(self):
        return list(self.N)

    def edges(self):
        return list(self.E)


def notes_db() -> sqlite3.Connection:
    """A notes database with one note and its vectors — the brain's own copy."""
    from brain import store, vectors
    db = store.connect()
    db.execute("INSERT INTO docs(id, source, path, name, sha, mtime, size) VALUES (7,'mem','/n.md','note','s7',0,1)")
    vectors.ensure_table(db)
    import array
    blob = array.array("f", [0.5, 0.5, 0.5, 0.5]).tobytes()
    db.execute("INSERT INTO vectors(doc_id, chunk_no, model, dim, sha, vec) VALUES (7,0,?,?, 's7', ?)",
               (vectors.model_tag(), vectors.DIM, blob))
    db.commit()
    return db


def main() -> int:
    try:
        try:
            ndb = notes_db()
        except Exception as exc:                          # noqa: BLE001
            check("set-up: a notes database", False, "%s: %s" % (type(exc).__name__, exc))
            return 1
        # ① default = notes, the same path with or without an explicit NotesSource
        a, b = FakeVectors("a"), FakeVectors("b")
        sa = vecstore.sync(ndb, a)
        sb = vecstore.sync(ndb, b, source=vecstore.NotesSource(ndb))
        check("① without `source` the notes are pushed, as before", sa.get("sent_docs") == 1 and (7, 0) in a.points,
              str(sa))
        check("① an explicit NotesSource sends exactly the same", sorted(a.points) == sorted(b.points)
              and sa["sent_chunks"] == sb["sent_chunks"])

        # ② a host's rows, in the host's own database
        hdb = sqlite3.connect(os.path.join(_TMP, "host.db"))
        hv, fv = HostVectors(), FakeVectors("host")
        s1 = vecstore.sync(hdb, fv, source=hv)
        check("② the host's rows reach the database", sorted(fv.points) == [(101, 0), (102, 0), (102, 1)], str(s1))
        w = fv.writes
        s2 = vecstore.sync(hdb, fv, source=hv)
        check("② a second sync sends nothing", s2.get("unchanged") is True and fv.writes == w, str(s2))

        # ③ a changed version resends only that document; a removed one is removed there
        hv.docs[101] = ("a2", [[0, 0, 0, 1]])
        del hv.docs[102]
        s3 = vecstore.sync(hdb, fv, source=hv)
        check("③ only the changed document is resent", s3.get("sent_docs") == 1 and fv.points.get((101, 0)) == [0, 0, 0, 1],
              str(s3))
        check("③ a removed document is removed there", s3.get("deleted_docs") == 1
              and not any(k[0] == 102 for k in fv.points), str(s3))

        # ④ ledgers do not cross
        notes_keys = stores.ledger(ndb, "vector", fv.target("host-model", 4))
        host_keys = stores.ledger(hdb, "vector", fv.target("host-model", 4))
        check("④ the host's ledger is in the host's database, not the notes'", not notes_keys and set(host_keys) == {"101"},
              "notes=%s host=%s" % (notes_keys, host_keys))

        # ⑤ graph source
        hg, fg = HostGraph(), FakeGraph()
        g1 = graphstore.sync(hdb, fg, source=hg)
        check("⑤ the host's nodes and edges reach the database", fg.nodes == {1, 2, 3} and fg.E == {(1, 2), (1, 3)},
              str(g1))
        check("⑤ no names unless `names`", all(v == "" for v in fg.labels.values()), str(fg.labels))
        fg2 = FakeGraph()
        graphstore.sync(sqlite3.connect(os.path.join(_TMP, "host2.db")), fg2, source=hg, names=True)
        check("⑤ with `names`, names are sent (through the outbound door)", fg2.labels.get(3) == "Project Apollo",
              str(fg2.labels))
        g2 = graphstore.sync(hdb, fg, source=hg)
        check("⑤ a second graph sync sends nothing", g2.get("unchanged") is True, str(g2))

        # ⑥ a lost copy is noticed and refilled
        fv.points.clear()
        s4 = vecstore.sync(hdb, fv, source=hv)
        check("⑥ a database reset behind the ledger is refilled", "healed" in s4 and (101, 0) in fv.points, str(s4))

        # ⑦ control: without `source`, the host's rows are not what is pushed
        ctl = FakeVectors("ctl")
        vecstore.sync(ndb, ctl)
        check("⑦ control: a sync without `source` does not send the host's rows",
              not any(k[0] in (101, 102) for k in ctl.points), str(sorted(ctl.points)))

        # ⑧ sync_source on a plain host database
        pdb = sqlite3.connect(os.path.join(_TMP, "plain.db"))       # no meta table, no sqlite3.Row
        pv = FakeVectors("plain")
        target = pv.target("host-model", 4)
        asked = []
        before = stores.serve(pdb, "vector", target, lambda: asked.append("remote") or "remote",
                              lambda: "local")
        check("⑨ before any sync the host's own copy answers", before == "local" and not asked,
              str(stores.last_served("vector")))
        out = stores.sync_source(pdb, "vector", pv, HostVectors())
        check("⑧ sync_source sends the host's rows and records them in sync",
              out.get("sent_docs") == 2 and stores.health(pdb, "vector").get("synced") == target, str(out))
        got = stores.serve(pdb, "vector", target, lambda: "remote", lambda: "local")
        check("⑧ then `serve` asks the database", got == "remote"
              and stores.last_served("vector").get("by") == "remote", str(stores.last_served("vector")))
        other = stores.serve(pdb, "vector", target + "-other", lambda: "remote", lambda: "local")
        check("⑨ another target is not in sync — the host's copy answers", other == "local")

        # ⑩ a failing database
        calls = []

        def boom():
            calls.append(1)
            raise stores.StoreError("connection refused")
        first = stores.serve(pdb, "vector", target, boom, lambda: "local")
        second = stores.serve(pdb, "vector", target, boom, lambda: "local")
        h = stores.health(pdb, "vector")
        check("⑩ a failing database: the host's copy answers and it is written down",
              first == "local" and h.get("ok") is False and int(h.get("fallbacks") or 0) >= 1, str(h))
        check("⑩ the next question does not knock again", second == "local" and len(calls) == 1,
              str(stores.last_served("vector")))
        stores._DOWN.pop("vector", None)
    finally:
        for k, v in _SAVED.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(_TMP, ignore_errors=True)
    print("\n%d failed" % len(FAILS) if FAILS else "\nall passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
