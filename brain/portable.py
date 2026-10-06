"""Move the brain between devices — ★only what was earned, by name★.

## What moves and what doesn't (measurement decided it · 2026-08-27)

This brain's core property decides nearly all of the design: ★the file is the source of truth, the DB is a derivative★.
So what moves is only "what cannot be rebuilt from the file."

| | what | row count (measured) | verdict |
|---|---|---|---|
| regenerable | postings · terms · skeletons · links · docs · git_* | 818k · 102k · 55k | ★not carried★ |
| earned | lexicon · rule_judged · rerank_cache · usage · vec_qcache | 1437 · 961 · 102 · 230 · 265 | carried |
| earned | ★vectors★ | 3545 chunks (862 docs) | carried — days' worth of remote embedding |
| ⛔forbidden | meta's ★calibration★ (threshold · noise floor · knobs) | 19 | ★not carried★ |

### ⛔ Why calibration must not move

`hook_threshold` is ★a value measured on this corpus★. Because IDF depends on document count N, the
same-strength match still scores lower on a smaller corpus. Plant it on someone else's machine and the
hook either goes silent forever or floods with noise — this repo has already been burned by this twice
(a stored threshold measured on another corpus, silently applied).
The judge's threshold and `lexicon_knobs` are the same. ★Calibration is not something you move — it is
something you re-measure on the new machine★ — and those layers already measure themselves (§calibrate · §lexicon.decide).

### ⛔⛔ Why a key must never be exported as a rowid

`vectors` uses `doc_id` (= `docs.rowid`) as its key. ★On a different device, that number is
guaranteed to differ.★ This repo has already been badly burned by this exact trap — `usage` was holding
onto `docs.rowid`, and when a full reindex renumbered them, ★42 of the top 52 entries lost their usage
history★ (no error, no log).

So on export, `doc_id` is swapped for `docs.name`, and on import it is looked up by name again.

### ★sha blocks a stale vector★

`vectors` already carries the `sha` of the chunk's body. On import, ★if it differs from the sha computed
on that document on this machine right now, it is discarded★ — planting a vector built from a body that
hasn't been updated on the other side yet makes search pick a document based on ★content that doesn't even exist★. A quiet, expensive failure.

## Paths — measurement says this is small

Of 635 memories, only ★29 (4.6%)★ hold an absolute path, and it is essentially one shape:
`/Users/<person>/development/...`. So a single `$HOME` substitution resolves almost all of them.
⛔ But ★this module never touches a memory file's own body★ — that is the user's own writing, carried
by git, and auto-substituting someone else's writing is beyond this layer's authority. Here, only
★paths inside the DB★ (`usage.path` · `git_repos_tbl.path`) are made portable.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Dict, List, Optional

FORMAT = 1

# ★tables keyed by name/path/sha — safe to move as-is★
PLAIN = ("lexicon", "rule_judged", "rerank_cache", "vec_qcache")
# ⛔⛔ ★this is an allow-list, not a block-list★ (2026-08-27, caught while auditing my own first draft)
#
#    The first draft wrote down a list of "keys not to move." But opening the exported bundle showed
#    `hook_threshold_detail` (calibration detail) · `vec_min_cos` (cosine threshold) ·
#    `last_index` (this machine's index timestamp) had ★leaked straight through★ — because they weren't on the list.
#    ★A block-list leaks quietly every time a new key appears.★ And a leak in this layer means
#    planting someone else's calibration on my machine — an expensive mistake.
#
#    So it was inverted: ★only what is explicitly decided to move, moves.★ And counting it for real
#    showed ★there is no meta worth moving★ — everything in meta is one of (ⓐ calibration measured on
#    this corpus ⓑ this machine's index state ⓒ a decision this machine made), and all three must be
#    decided fresh on the other side. Those layers already decide for themselves (§calibrate · §lexicon.decide).
#    Written here so an empty set reads as ★a conclusion★, not a mistake.
KEEP_META: tuple = ()


def portable(path: str) -> str:
    """`/Users/<you>/x` → `~/x`. ⛔ Never bake a person's name into the bundle."""
    home = os.path.expanduser("~")
    if path and path.startswith(home):
        return "~" + path[len(home):]
    return path


def local(path: str) -> str:
    return os.path.expanduser(path) if path.startswith("~") else path


_BLOB = "0x!"          # ★the marker prefixed to bytes★ — JSON has no byte type


def _enc(v):
    return (_BLOB + bytes(v).hex()) if isinstance(v, (bytes, bytearray, memoryview)) else v


def _dec(v):
    return bytes.fromhex(v[len(_BLOB):]) if isinstance(v, str) and v.startswith(_BLOB) else v


def _rows(db, sql, args=()):
    """⛔ Carrying a blob as-is kills json.dump — tag it and carry it as hex instead."""
    try:
        return [{k: _enc(r[k]) for k in r.keys()} for r in db.execute(sql, args)]
    except sqlite3.Error:
        return []


def export(db: sqlite3.Connection, out: str = "") -> dict:
    """What was earned, into one file. ⛔ Holds nothing regenerable (not about size — about ★correctness★).

    Carry a derivative and ★an index computed from someone else's corpus★ survives on the other side. Rebuilding
    from the file is always right, and that is this brain's whole design premise.
    """
    name_of = {r["id"]: r["name"] for r in _rows(db, "SELECT id, name FROM docs")}
    vecs = []
    for r in _rows(db, "SELECT doc_id, chunk_no, sha, model, dim, vec FROM vectors"):
        nm = name_of.get(r["doc_id"])
        if not nm:
            continue                                     # an orphan vector doesn't move
        # `_rows` already turned the blob into marker+hex — don't wrap it again here.
        vecs.append({"name": nm, "chunk_no": r["chunk_no"], "sha": r["sha"],
                     "model": r["model"], "dim": r["dim"], "vec": r["vec"]})
    bundle = {
        "format": FORMAT,
        "made_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "home_was": portable(os.path.expanduser("~")),
        "vectors": vecs,
        "usage": [dict(r, path=portable(r["path"]))
                  for r in _rows(db, "SELECT * FROM usage")],
        "repos": [dict(r, path=portable(r["path"]))
                  for r in _rows(db, "SELECT rid, path FROM git_repos_tbl")],
        "recalls": _rows(db, "SELECT ts, query, terms, hits, top FROM recalls"),
    }
    for t in PLAIN:
        bundle[t] = _rows(db, "SELECT * FROM %s" % t)
    # meta holds ★only what is allowed★ (empty right now, and that is the conclusion — §KEEP_META)
    bundle["meta"] = {r["k"]: r["v"] for r in _rows(db, "SELECT k, v FROM meta")
                      if r["k"] in KEEP_META}
    out = out or os.path.expanduser("~/brain-bundle.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False)
    return {"path": out, "bytes": os.path.getsize(out),
            "vectors": len(vecs), "docs_with_vectors": len({v["name"] for v in vecs}),
            **{t: len(bundle[t]) for t in PLAIN},
            "usage": len(bundle["usage"]), "meta_kept": len(bundle["meta"]),
            "meta_note": "calibration · index state · decisions do not move — the other side measures its own"}


def _ensure_tables(db) -> None:
    """⛔ ★a new machine has no tables yet★ — but that is exactly this feature's use case.

    Each table is created the first time its own layer uses it (`vectors.ensure_table` · `lexicon.ensure` …).
    Import runs ★before★ those layers, so it must guarantee the tables here. The first draft skipped this
    and died on a clean machine with `no such table: vectors` — right at the moment of import.
    """
    from . import lexicon, rerank, ruledisc, vectors
    vectors.ensure_table(db)
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS vec_qcache("
                   " qsha TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL,"
                   " vec BLOB NOT NULL, PRIMARY KEY(qsha, model, dim))")
    lexicon.ensure(db)
    ruledisc._ensure(db)
    rerank._cache_ensure(db)


def _sha_now(db, name: str, chunk_no: int) -> Optional[str]:
    """The sha of that chunk of that document on this machine right now. None if indexing hasn't made it yet."""
    r = db.execute(
        "SELECT v.sha FROM vectors v JOIN docs d ON d.id = v.doc_id"
        " WHERE d.name=? AND v.chunk_no=?", (name, chunk_no)).fetchone()
    return r["sha"] if r else None


def imp(db: sqlite3.Connection, path: str, dry_run: bool = True) -> dict:
    """Imports a bundle. ⛔ Defaults to a ★dry run★ — shows what would come in first.

    ★Never overwrites — merges toward the larger value★ — a cumulative field like `seen`/`hits` was earned
    separately by each machine, so max is correct (summing would double-count the same event).
    """
    with open(path, encoding="utf-8") as fh:
        b = json.load(fh)
    if int(b.get("format", 0)) != FORMAT:
        return {"error": "format %s — this code only knows %d" % (b.get("format"), FORMAT)}
    _ensure_tables(db)
    ids = {r["name"]: r["id"] for r in _rows(db, "SELECT id, name FROM docs")}
    rep = {"format": b["format"], "made_at": b.get("made_at"), "dry_run": dry_run,
           "vectors_in": 0, "vectors_no_doc": 0, "vectors_stale_sha": 0,
           "vectors_have": 0}
    plan_vec = []
    for v in b.get("vectors", []):
        did = ids.get(v["name"])
        if did is None:
            rep["vectors_no_doc"] += 1                   # that document isn't on this side
            continue
        cur = _sha_now(db, v["name"], v["chunk_no"])
        if cur is not None and cur == v["sha"]:
            rep["vectors_have"] += 1                     # already have the same one
            continue
        if cur is not None and cur != v["sha"]:
            # ⛔ ★the body differs — do not plant a stale vector★. A quiet failure that makes search
            #    pick a document based on content that doesn't even exist.
            rep["vectors_stale_sha"] += 1
            continue
        plan_vec.append((did, v))
        rep["vectors_in"] += 1
    rep["tables"] = {}
    for t in PLAIN:
        rep["tables"][t] = len(b.get(t, []))
    rep["usage"] = len(b.get("usage", []))
    rep["meta"] = len(b.get("meta", {}))
    rep["meta_note"] = "calibration does not import — this machine re-measures its own"
    if dry_run:
        return rep
    with db:
        for did, v in plan_vec:
            db.execute("INSERT OR REPLACE INTO vectors"
                       "(doc_id, chunk_no, sha, model, dim, vec) VALUES(?,?,?,?,?,?)",
                       (did, v["chunk_no"], v["sha"], v["model"], v["dim"],
                        sqlite3.Binary(_dec(v["vec"]))))
        for r in b.get("lexicon", []):
            db.execute(
                "INSERT INTO lexicon(src, dst, seen, source, last) VALUES(?,?,?,?,?)"
                " ON CONFLICT(src, dst) DO UPDATE SET seen=max(seen, excluded.seen)",
                (r["src"], r["dst"], r.get("seen", 1), r.get("source", ""),
                 r.get("last", "")))
        for r in b.get("rule_judged", []):
            db.execute("INSERT OR IGNORE INTO rule_judged(signal, memory, score, at)"
                       " VALUES(?,?,?,?)",
                       (r["signal"], r["memory"], r["score"], r.get("at", "")))
        for r in b.get("vec_qcache", []):
            db.execute("INSERT OR IGNORE INTO vec_qcache(qsha, model, dim, vec)"
                       " VALUES(?,?,?,?)",
                       (r["qsha"], r["model"], r["dim"],
                        sqlite3.Binary(_dec(r["vec"]))))
        for r in b.get("rerank_cache", []):
            db.execute("INSERT OR IGNORE INTO rerank_cache(k, model, n, scores, at, hits)"
                       " VALUES(?,?,?,?,?,?)",
                       (r["k"], r["model"], r["n"], r["scores"], r.get("at", ""),
                        r.get("hits", 0)))
        for r in b.get("usage", []):
            db.execute(
                "INSERT INTO usage(path, hits, opens, useful, wrong, stale)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET"
                " hits=max(hits, excluded.hits), opens=max(opens, excluded.opens),"
                " useful=max(useful, excluded.useful), wrong=max(wrong, excluded.wrong)",
                (local(r["path"]), r.get("hits", 0), r.get("opens", 0),
                 r.get("useful", 0), r.get("wrong", 0), r.get("stale", 0)))
    rep["applied"] = True
    return rep
