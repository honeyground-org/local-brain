#!/usr/bin/env python3
"""★An incremental pass recounts only the terms it touched — and lands on exactly the full recount★ (local · budget 0)

Why this exists (2026-10-14)
----------------------------
After the databases' update left the hook (#35), its reindex still recounted ★every★ term's document frequency
(0.14 s) and rebuilt every transliteration skeleton (0.07 s) for one changed document — the hook's budget is
0.6 s. An incremental pass now recounts, from the postings, only the terms whose postings it changed (§_TOUCHED ·
`_recount_terms` · `_recount_skeletons`). The rule it must not break: ★df is never adjusted by hand★ — a wrong
df quietly ruins ranking. So this check edits a corpus in random rounds (change, add, remove, rename words in and
out, Latin and Hangul) and after every incremental pass compares both tables with a recount of everything:

  ① `terms` equals a full recount from the postings, row for row, after every round
  ② `skeletons` equals a full rebuild from that, row for row
  ③ a full pass and a first index still rebuild everything
  ④ the incremental pass is cheaper on this corpus than the full recount it replaces (timed, reported)

⛔ Control: a pass that forgets the terms of a removed document leaves `terms` different from the full recount —
   so ① can fail.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-recount-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")

from brain import store, translit  # noqa: E402

FAILS = []
WORDS = ("ledger reconcile harbour ferry quarry marble lantern copper orchard signal deploy rollback "
         "budget cache index window garden thistle beacon candle grep qdrant neo4j sqlite").split() + [
    # Hangul words, escaped so this file stays English (§tests/english_only_ratchet.txt)
    "\uc138\uc158", "\ubc30\ud3ec", "\uc0c1\ud55c", "\ubb38\ud131", "\ud310\uc815", "\uae30\uc5b5", "\ud68c\uc0c1", "\ub3d9\uae30\ud654", "\uc7ac\uc0c9\uc778", "\uadf8\ub798\ud504", "\ubca1\ud130", "\ub0b1\ub9d0", "\uc9c8\ubb38", "\uc815\ub2f5"]


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def note(name: str, rnd: random.Random) -> None:
    body = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(8, 40)))
    p = os.path.join(_MEM, name + ".md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: note %s %s\n---\n\n%s\n" % (name, name, rnd.choice(WORDS), body))
    t = time.time() + rnd.random() * 5 + 2
    os.utime(p, (t, t))


def full_terms(db) -> dict:
    return {r[0]: r[1] for r in db.execute("SELECT term, COUNT(*) FROM postings GROUP BY term")}


def full_skeletons(terms: dict) -> set:
    out = set()
    for t, df in terms.items():
        if df >= 2 and t.isascii() and t.isalpha() and t.islower():
            k = translit.skeleton_key(t)
            if len(k) >= 2:
                out.add((k, t, df))
    return out


def tables(db):
    return ({r[0]: r[1] for r in db.execute("SELECT term, df FROM terms")},
            {(r[0], r[1], r[2]) for r in db.execute("SELECT skel, term, df FROM skeletons")})


def main() -> int:
    print("=" * 78)
    print("an incremental pass recounts only what it touched — and lands on the full recount")
    print("=" * 78)
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"], "max_depth": 1}]}, fh)
    rnd = random.Random(20261014)
    names = ["note_%03d" % i for i in range(60)]
    for n in names:
        note(n, rnd)
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False)
    t, s = tables(db)
    check("③ a first index builds both tables in full", t == full_terms(db) and s == full_skeletons(full_terms(db)))

    bad_t = bad_s = 0
    inc_secs, rounds = [], 12
    for r in range(rounds):
        live = [n for n in names if os.path.exists(os.path.join(_MEM, n + ".md"))]
        for n in rnd.sample(live, min(len(live), rnd.randint(1, 4))):       # change
            note(n, rnd)
        if rnd.random() < 0.7:                                               # add
            n = "note_new_%02d" % r
            names.append(n)
            note(n, rnd)
        if rnd.random() < 0.6 and len(live) > 10:                            # remove
            os.remove(os.path.join(_MEM, rnd.choice(live) + ".md"))
        t0 = time.time()
        store.reindex(db, recalibrate=False, sync=False, heal=False)
        inc_secs.append(time.time() - t0)
        t, s = tables(db)
        ft = full_terms(db)
        bad_t += t != ft
        bad_s += s != full_skeletons(ft)
    check("① `terms` equals the full recount after every one of %d incremental rounds" % rounds, bad_t == 0,
          "%d round(s) differ" % bad_t)
    check("② `skeletons` equals the full rebuild after every round", bad_s == 0, "%d round(s) differ" % bad_s)

    note(names[0], rnd)
    store.reindex(db, full=True, recalibrate=False)
    t, s = tables(db)
    check("③ a full pass still rebuilds both in full", store._TOUCHED is None and t == full_terms(db))

    # ④ the cost it replaces
    t0 = time.time()
    store._rebuild_terms(db)
    store._rebuild_skeletons(db)
    whole = time.time() - t0
    note(names[1], rnd)
    t0 = time.time()
    store.reindex(db, recalibrate=False, sync=False, heal=False)
    one = time.time() - t0
    print("   ④ one changed document: incremental pass %.3fs · the full recount alone %.3fs (on %d documents)"
          % (one, whole, len(names)))

    # ── control: forget a removed document's terms ───────────────────────────
    real = store._touch
    store._touch = lambda db_, doc_id=None, terms=(): real(db_, None, terms)   # old terms never collected
    try:
        live = [n for n in names if os.path.exists(os.path.join(_MEM, n + ".md"))]
        for n in live[:3]:
            os.remove(os.path.join(_MEM, n + ".md"))
        store.reindex(db, recalibrate=False, sync=False, heal=False)
        t, _ = tables(db)
        broken = t != full_terms(db)
    finally:
        store._touch = real
    check("★control★: forgetting a removed document's terms leaves `terms` wrong — so ① can fail", broken)
    db.close()

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — the incremental recount equals the full one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
