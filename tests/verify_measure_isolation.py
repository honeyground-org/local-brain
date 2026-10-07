#!/usr/bin/env python3
"""★A measurement must not change the index under everyone else★ (local · budget 0)

## The incident (2026-10-07)

A bench run three times in a row on the real index read 19/12 · 18/7 · 16/11 (hits/false fires at one
threshold). The code was deterministic — on a copy of the index it read 15/3 every time, cold cache or
warm, any time budget. Two shared-state writes were behind it:

  ① `lexicon.decide()` (the daily job) tried each of its 37 settings by ★writing it to the shared
     meta★ and restoring it afterwards. For the whole sweep every other process — the hook, the MCP
     servers, a person's own measurement — searched with a trial setting it took for the real one.
     Reproduced on a copy: the old decision beside a bench read 20/15 19/12 16/6 17/11 …; the fixed
     one 15/3 fifteen times.
  ② `store._init` dropped and re-created the `name_map` view on ★every connect★, and Python's sqlite3
     opens no transaction before DDL, so the DROP committed alone. A reader beside a process that only
     connects hit "no such table: name_map" 66,833 times in 6 seconds; the daily decision died on it.

## What this holds down

  ① while `decide()` sweeps, a ★second connection★ always reads the setting that was there before —
     and (control) this process really did search with the trial settings, or ① could pass by
     trying nothing
  ② connecting to an index whose `name_map` is current runs ★no DROP★ — and (control) a missing or
     outdated view is still rebuilt
  ③ a process of ★another code generation★ (a long-running MCP server holding old code) writing the
     bridge cache leaves the current generation's rows alone — and (control) a newer one does clear
     the older generation's rows, so the table does not grow with every generation

How to run:  PYTHONPATH=. python3 tests/verify_measure_isolation.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-isolation-")
_ENV_BEFORE = {k: os.environ.get(k) for k in ("BRAIN_HOME", "BRAIN_EVAL_DIR", "BRAIN_LEXICON")}
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_EVAL_DIR"] = os.path.join(_TMP, "eval")
os.environ.pop("BRAIN_LEXICON", None)                # a human override would skip the sweep

from brain import calibrate, lexicon, search, store, translit  # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def lexicon_sweep() -> None:
    print("① the lexicon decision tries its settings in its own process")
    os.makedirs(os.environ["BRAIN_EVAL_DIR"], exist_ok=True)
    with open(os.path.join(os.environ["BRAIN_EVAL_DIR"], "short.json"), "w", encoding="utf-8") as fh:
        json.dump({"A_memory_needed": [{"q": "question %d" % i, "gold": "memory_%d" % i} for i in range(12)],
                   "C_no_memory_needed": ["control %d" % i for i in range(12)]}, fh)
    db = store.connect()
    other = store.connect()                          # what every other process on the index sees
    store.set_meta(db, "lexicon_enabled", "0")
    store.set_meta(db, "lexicon_knobs", "")
    seen_shared, seen_here = set(), set()
    real_recall = search.recall

    def spy(conn, query, **kw):
        seen_shared.add((store.get_meta(other, "lexicon_enabled", ""),
                         store.get_meta(other, "lexicon_knobs", "")))
        seen_here.add((lexicon.enabled(conn), lexicon.knobs(conn)))
        return []
    search.recall = spy
    try:
        r = lexicon.decide(db, force=True)
    finally:
        search.recall = real_recall
    check("the sweep ran", r.get("grid", 0) > 1, "grid %s · %s" % (r.get("grid"), r.get("decision")))
    check("another connection never saw a trial setting", seen_shared == {("0", "")},
          "saw %d distinct (enabled, knobs): %s" % (len(seen_shared), sorted(seen_shared)[:4]))
    check("control: this process did search with the trial settings",
          any(on for on, _ in seen_here) and len({k for on, k in seen_here if on}) > 1,
          "%d distinct settings in this process" % len(seen_here))
    check("nothing is left behind after the sweep", getattr(lexicon, "_TRIAL", None) is None
          and store.get_meta(db, "lexicon_enabled", "") in ("0", "1"))


def name_map_view() -> None:
    print("\n② connecting does not rebuild a current name_map")
    path = store.db_path()

    def statements_on_connect():
        conn = sqlite3.connect(path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        ran = []
        conn.set_trace_callback(ran.append)
        store._init(conn)
        conn.close()
        return [s for s in ran if "name_map" in s and s.lstrip().upper().startswith(("DROP", "CREATE"))]

    store.connect().close()                          # an index whose view is current
    check("a current view: no DROP, no CREATE", statements_on_connect() == [])
    conn = sqlite3.connect(path)
    conn.execute("DROP VIEW name_map")
    conn.execute("CREATE VIEW name_map AS SELECT name AS key, id AS doc_id FROM docs")
    conn.commit()
    conn.close()
    rebuilt = statements_on_connect()
    sql = sqlite3.connect(path).execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='name_map'").fetchone()
    check("control: an outdated view is rebuilt", bool(rebuilt) and sql and "kind_aliases" in sql[0],
          "%d statement(s)" % len(rebuilt))
    conn = sqlite3.connect(path)
    conn.execute("DROP VIEW name_map")
    conn.commit()
    conn.close()
    statements_on_connect()
    check("control: a missing view is created", sqlite3.connect(path).execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name='name_map'").fetchone()[0] == 1)


def bridge_generations() -> None:
    print("\n③ another generation's process does not replace this generation's bridge rows")
    db = store.connect()
    words = ["커밋해", "리베이스", "테라폼"]
    gen = calibrate.code_cache_key()
    real_stamp = translit._code_stamp
    try:
        translit.bridge(db, words, 100)
        mine = lambda: {r[0] for r in db.execute(                       # noqa: E731
            "SELECT ko FROM translit_cache_gen WHERE code=?", (gen,))}
        before = mine()
        translit._code_stamp = lambda: gen - 1        # an old server answering a recall
        translit.bridge(db, words, 100)
        check("the current generation's rows survive an older process's write", mine() == before == set(words),
              "%d → %d of %d" % (len(before), len(mine()), len(words)))
        older = db.execute("SELECT COUNT(*) FROM translit_cache_gen WHERE code=?", (gen - 1,)).fetchone()[0]
        check("the older process wrote ★its own★ rows", older == len(words), "%d rows" % older)
        translit._code_stamp = lambda: gen
        translit.bridge(db, ["클라우드"], 100)
        left = db.execute("SELECT COUNT(*) FROM translit_cache_gen WHERE code<?", (gen,)).fetchone()[0]
        check("control: the current generation clears the older rows when it writes", left == 0,
              "%d older rows left" % left)
    finally:
        translit._code_stamp = real_stamp


def main() -> int:
    lexicon_sweep()
    name_map_view()
    bridge_generations()
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ measuring leaves the index as everyone else sees it")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
        for k, v in _ENV_BEFORE.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    sys.exit(code)
