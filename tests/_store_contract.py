"""The storage contract, run against whichever backends are chosen — the same questions, the same answers.

One sequence for every backend, so a new one is held to exactly what the others are:

  ① a sync from a synthetic corpus, then the same questions to both — identical; a second sync sends nothing
  ② an edit and a removal reach the database through indexing alone — identical again
  ③ a full rebuild (`--sync --full`) starts the target from nothing and lands on the same answers
  ④ the database loses what it held — the next sync notices and refills it from the local copy
  ⑤ the database is not there — indexing still works, the local copy answers, the error is recorded,
     and one sync catches up when it is back

Used by tests/verify_store_adapters.py (a backend dropped in as one file), tests/verify_stores_live.py
(every backend that answers) and tests/verify_docker_stores.py (every backend that runs in Docker).
"""
from __future__ import annotations

import json
import os
import urllib.parse
from typing import Callable, Dict, List, Optional

from tests import _stores_fixture as fx


def prepare(root: str, chosen: Optional[Dict[str, dict]] = None):
    """A fresh synthetic corpus in `root`, indexed, with vectors and cached questions. Returns (db, memory folder).

    `chosen` = {role: config entry}; without it the stores already in config.json are kept.
    """
    from brain import store
    path = os.environ["BRAIN_CONFIG"]
    try:
        keep = json.load(open(path, encoding="utf-8")).get("stores") or {}
    except (OSError, ValueError):
        keep = {}
    mem = fx.write_corpus(root)
    cfg = json.load(open(path, encoding="utf-8"))
    cfg["stores"] = chosen if chosen is not None else keep
    json.dump(cfg, open(path, "w", encoding="utf-8"))
    db = store.connect()
    store.reindex(db, recalibrate=False, refresh_history=False)
    fx.fill_vectors(db)
    fx.fill_questions(db)
    return db, mem


def same(db, roles, check: Callable, label: str) -> None:
    from brain import stores
    c = stores.check(db, roles)
    if "vector" in c:
        v = c["vector"]
        check("vector %s · %s: identical top documents and scores" % (v.get("backend", ""), label),
              bool(v.get("queries")) and v["top1_agree"] == v["queries"] and v["overlap_at_10"] == 1.0
              and v["max_score_delta"] < 1e-5 and v["chunks_store"] == v["chunks_local"],
              json.dumps({k: v.get(k) for k in ("queries", "top1_agree", "overlap_at_10", "max_score_delta",
                                                 "chunks_local", "chunks_store", "error")}))
    if "graph" in c:
        g = c["graph"]
        check("graph %s · %s: identical neighbours, edges and link targets" % (g.get("backend", ""), label),
              g.get("neighbors_same") == g.get("docs") and bool(g.get("edges_same")) and bool(g.get("targets_same")),
              json.dumps({k: g.get(k) for k in ("docs", "neighbors_same", "edges_local", "edges_store", "error")}))


def drop_all(roles) -> None:
    """Remove what this run wrote, from every chosen database (a database that also holds real data keeps it)."""
    from brain import stores, vectors
    for role in roles:
        c = stores.choice(role)
        if c["backend"] == "sqlite" or c.get("error"):
            continue
        try:
            if role == "vector":
                stores.vector_backend(c).drop(vectors.model_tag(), vectors.DIM)
            else:
                stores.graph_backend(c).drop()
        except stores.StoreError as exc:
            print("  (cleanup: %s)" % exc)


def compose_items(text: str) -> Dict[str, List[str]]:
    """The compose file's list items by section — {"ports": [...], "volumes": [...], "environment": [...]}.

    ⛔ Read by section, not by counting colons: a Windows mount (`C:\\…:/data`) has as many colons as a
       port mapping, and was counted as one (CI, 2026-10-10).
    """
    out: Dict[str, List[str]] = {}
    key = ""
    for line in text.splitlines():
        s = line.strip()
        if s.endswith(":") and not s.startswith("-"):
            key = s[:-1]
            continue
        if s.startswith("- ") and key:
            item = s[2:].split("#", 1)[0].strip()
            out.setdefault(key, []).append(json.loads(item) if item.startswith('"') else item)   # written by json.dumps
    return out


def mount_host(item: str) -> str:
    """`<host folder>:<container path>` → the host folder (which may itself hold a drive's colon)."""
    return item.rsplit(":/", 1)[0]


def unreachable(url: str) -> str:
    """The same kind of address, where nothing answers."""
    return "%s://127.0.0.1:9" % (urllib.parse.urlsplit(url).scheme or "http")


def run(db, mem: str, roles, check: Callable, outage: bool = True) -> None:
    from brain import graphstore, store, stores
    index = lambda: store.reindex(db, recalibrate=False, refresh_history=False)  # noqa: E731
    names = " + ".join("%s %s" % (r, stores.choice(r)["backend"]) for r in roles)

    print("\n① [%s] sync, then the same questions to both" % names)
    r = stores.sync(db, roles, full=True)
    check("sync reports no error", not any(v.get("error") for v in r.values()), json.dumps(r)[:240])
    same(db, roles, check, "after the first sync")
    r = stores.sync(db, roles)
    check("a second sync sends nothing", all(v.get("unchanged") for v in r.values()),
          json.dumps({k: v.get("unchanged") for k, v in r.items()}))

    print("\n② [%s] an edit and a removal travel through indexing alone" % names)
    fx.write_note(mem, "zeta_alone", "Now it links to [[theta_log]] and [[alpha_overview]].")
    os.remove(os.path.join(mem, "beta_details.md"))
    out = index()
    check("indexing pushed the difference", all(out.get("stores", {}).get(r) in ("sent", "unchanged")
                                                 for r in roles), str(out.get("stores")))
    same(db, roles, check, "after an edit and a removal")

    print("\n③ [%s] a full rebuild lands on the same answers" % names)
    r = stores.sync(db, roles, full=True)
    check("full rebuild reports no error", not any(v.get("error") for v in r.values()))
    same(db, roles, check, "after a full rebuild")

    print("\n④ [%s] the database loses what it held" % names)
    drop_all(roles)
    r = stores.sync(db, roles)
    check("the next sync notices and refills it", all(v.get("healed") for v in r.values()),
          json.dumps({k: v.get("healed") for k, v in r.items()}))
    same(db, roles, check, "after refilling")

    if not outage:
        return
    print("\n⑤ [%s] a database that is not there" % names)
    for role in roles:
        os.environ["BRAIN_%s_STORE_URL" % role.upper()] = unreachable(stores.choice(role)["url"])
    try:
        r = stores.sync(db, roles)
        check("a sync to nowhere returns the error instead of raising",
              all(v.get("error") for v in r.values()), "; ".join(v.get("error", "")[:60] for v in r.values()))
        fx.write_note(mem, "theta_log", "Changed while the database is away — links to [[delta_plan]].")
        out = index()
        check("indexing still completes", out.get("updated", 0) >= 1, str(out.get("stores")))
        got = graphstore.ask(db, "edges") if "graph" in roles else None
        check("the local copy answers", got is None or got == graphstore.LOCAL.edges(db))
        check("the error is recorded for `brain stores`",
              all(stores.health(db, r).get("ok") is False for r in roles))
    finally:
        for role in roles:
            os.environ.pop("BRAIN_%s_STORE_URL" % role.upper(), None)
    stores._DOWN.clear()
    r = stores.sync(db, roles)
    check("when it is back, one sync catches up", not any(v.get("error") for v in r.values()))
    same(db, roles, check, "after catching up")
