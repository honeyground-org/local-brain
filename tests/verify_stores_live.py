#!/usr/bin/env python3
"""★The real databases give the same answers as the local copy★ — Qdrant and Neo4j, measured live. (local · budget 0)

Runs only against databases you have running; skips (exit 77) and says how to start them otherwise:

    docker run -d -p 127.0.0.1:6333:6333 qdrant/qdrant
    NEO4J_AUTH=neo4j/<password> docker run -d -p 127.0.0.1:7474:7474 -e NEO4J_AUTH neo4j:5
    NEO4J_PASSWORD=<password> PYTHONPATH=. python3 tests/verify_stores_live.py

Addresses: `BRAIN_TEST_QDRANT_URL` (default http://localhost:6333) · `BRAIN_TEST_NEO4J_URL` (default
http://localhost:7474). Everything is written under a ★fresh collection prefix and graph namespace★ and
removed at the end — a database that also holds real data is not touched.

  ① a sync from a synthetic corpus, then the same questions to both: identical answers
  ② an edit and a removal reach the database through indexing alone, and the answers stay identical
  ③ a full rebuild (`--sync --full`) starts the target from nothing and lands on the same answers
  ④ a database that is not there: indexing still works, the local copy answers, the error is recorded
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-stores-live-")
_KEYS = ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_EMBED_DIM", "BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE",
         "BRAIN_VECTOR_STORE_URL", "BRAIN_GRAPH_STORE_URL", "BRAIN_ADAPTIVE_LEARN", "BRAIN_TRANSLIT_MS")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ.update(BRAIN_HOME=os.path.join(_TMP, "home"), BRAIN_CONFIG=os.path.join(_TMP, "config.json"),
                  BRAIN_EMBED_DIM="32", BRAIN_ADAPTIVE_LEARN="0", BRAIN_TRANSLIT_MS="0")
for _k in _KEYS[3:7]:
    os.environ.pop(_k, None)

from brain import graphstore, store, stores, vecstore  # noqa: E402
from tests import _needs, _stores_fixture as fx  # noqa: E402

QURL = os.environ.get("BRAIN_TEST_QDRANT_URL", "http://localhost:6333").rstrip("/")
NURL = os.environ.get("BRAIN_TEST_NEO4J_URL", "http://localhost:7474").rstrip("/")
RUN = "verify%s" % uuid.uuid4().hex[:8]
FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def reachable() -> dict:
    out = {}
    try:
        stores.http_json("GET", QURL + "/", timeout=2)
        out["vector"] = {"backend": "qdrant", "url": QURL, "collection_prefix": RUN}
    except stores.StoreError:
        pass
    if os.environ.get("NEO4J_PASSWORD"):
        c = {"backend": "neo4j", "url": NURL, "options": {"namespace": RUN}}
        try:
            graphstore.Neo4jGraph(c).ping()
            out["graph"] = {"backend": "neo4j", "url": NURL, "namespace": RUN}
        except stores.StoreError:
            pass
    return out


def same(db, roles, label: str) -> None:
    c = stores.check(db, roles)
    if "vector" in c:
        v = c["vector"]
        check("vector · %s: identical top documents and scores" % label,
              v.get("queries") and v["top1_agree"] == v["queries"] and v["overlap_at_10"] == 1.0
              and v["max_score_delta"] < 1e-5 and v["chunks_store"] == v["chunks_local"],
              json.dumps({k: v.get(k) for k in ("queries", "top1_agree", "overlap_at_10", "max_score_delta",
                                                 "chunks_local", "chunks_store", "error")}))
    if "graph" in c:
        g = c["graph"]
        check("graph · %s: identical neighbours, edges and link targets" % label,
              g.get("neighbors_same") == g.get("docs") and g.get("edges_same") and g.get("targets_same"),
              json.dumps({k: g.get(k) for k in ("docs", "neighbors_same", "edges_local", "edges_store", "error")}))


def cleanup(chosen: dict) -> None:
    from brain import vectors
    try:
        if "vector" in chosen:
            be = vecstore.make(stores.choice("vector"))
            be._call("DELETE", "/collections/%s" % be.collection(vectors.model_tag(), vectors.DIM), timeout=30)
        if "graph" in chosen:
            be = graphstore.make(stores.choice("graph"))
            be.run([("MATCH (d:BrainDoc {ns: $ns}) DETACH DELETE d", {"ns": be.ns})], timeout=30)
    except stores.StoreError as exc:
        print("  (cleanup: %s)" % exc)


def main() -> int:
    chosen = reachable()
    if not chosen:
        _needs.skip("neither Qdrant (%s) nor Neo4j (%s, NEO4J_PASSWORD) is reachable" % (QURL, NURL),
                    "start them as shown at the top of this file")
    roles = tuple(r for r in stores.ROLES if r in chosen)
    print("live: %s" % ", ".join("%s → %s" % (r, chosen[r]["backend"]) for r in roles))
    mem = fx.write_corpus(_TMP)
    cfg = json.load(open(os.environ["BRAIN_CONFIG"]))
    cfg["stores"] = chosen
    json.dump(cfg, open(os.environ["BRAIN_CONFIG"], "w"))
    db = store.connect()
    try:
        store.reindex(db, recalibrate=False, refresh_history=False)
        fx.fill_vectors(db)
        fx.fill_questions(db)

        print("\n① sync, then the same questions to both")
        r = stores.sync(db, roles)
        check("sync reports no error", not any(v.get("error") for v in r.values()), json.dumps(r)[:240])
        same(db, roles, "after the first sync")
        r = stores.sync(db, roles)
        check("a second sync sends nothing", all(v.get("unchanged") for v in r.values()))

        print("\n② an edit and a removal travel through indexing alone")
        fx.write_note(mem, "zeta_alone", "Now it links to [[theta_log]] and [[alpha_overview]].")
        os.remove(os.path.join(mem, "beta_details.md"))
        out = store.reindex(db, recalibrate=False, refresh_history=False)
        check("indexing pushed the difference", all(out.get("stores", {}).get(r) in ("sent", "unchanged")
                                                     for r in roles), str(out.get("stores")))
        same(db, roles, "after an edit and a removal")

        print("\n③ a full rebuild lands on the same answers")
        r = stores.sync(db, roles, full=True)
        check("full rebuild reports no error", not any(v.get("error") for v in r.values()))
        same(db, roles, "after a full rebuild")

        print("\n④ a database that is not there")
        for role in roles:
            os.environ["BRAIN_%s_STORE_URL" % role.upper()] = "http://127.0.0.1:9"
        r = stores.sync(db, roles)
        check("a sync to nowhere returns the error instead of raising",
              all(v.get("error") for v in r.values()), "; ".join(v.get("error", "")[:60] for v in r.values()))
        fx.write_note(mem, "theta_log", "Changed while the database is away — links to [[delta_plan]].")
        out = store.reindex(db, recalibrate=False, refresh_history=False)
        check("indexing still completes", out.get("updated", 0) >= 1, str(out.get("stores")))
        got = graphstore.ask(db, "edges") if "graph" in roles else None
        check("the local copy answers", got is None or got == graphstore.LOCAL.edges(db))
        check("the error is recorded for `brain stores`",
              all(stores.health(db, r).get("ok") is False for r in roles))
        for role in roles:
            os.environ.pop("BRAIN_%s_STORE_URL" % role.upper(), None)
        r = stores.sync(db, roles)
        check("when it is back, one sync catches up", not any(v.get("error") for v in r.values()))
        same(db, roles, "after catching up")
    finally:
        cleanup(chosen)
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the real databases give the same answers as the local copy")
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
