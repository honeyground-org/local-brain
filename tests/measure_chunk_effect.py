"""Measures the effect of chunked rerank — ★the spot where on/off gets decided★. (a measurement script, not a check)

## Why this is kept around (2026-08-31)

Whether `rerank.CHUNK` gets turned on is ★measured and decided against the full sample★. That day only
8 of budget was left, so only 8 of the sample could be measured, and the result split:

    ★reproducibility★ of 5-item batches    62% → 100%  (sample 8 · 2 runs each) — a clear win
    net gain of word-path rerank            gained 2/8 · ★broke 1/8★      — uncertain

⛔ So the code went in, but ★the default stayed off★. This script is what settles that judgement.

## Usage

    PYTHONPATH=. python3 tests/measure_chunk_effect.py           # batches of 5, 2 runs
    PYTHONPATH=. R=3 SIZE=10 python3 tests/measure_chunk_effect.py

⛔ Spends budget — queries × (20/size) × R calls. Check `./bin/brain budget` before starting.
⛔ The cache is off (`cache=False`) — otherwise the measurement reads its own answer.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import evalinit as ei, rerank, search, store    # noqa: E402

R = int(os.environ.get("R", "2") or 2)
SIZE = int(os.environ.get("SIZE", "5") or 5)
K = int(os.environ.get("K", "20") or 20)
# ⛔ The full run (57 queries × 2 runs × 5 calls = 570) ★exceeds the daily budget (350)★ —
#    so the sample is shrunk to measure, and ★the same sample★ is compared the next day (seed fixed).
N = int(os.environ.get("N", "0") or 0)


def hydrate(db, name):
    r = db.execute("SELECT name, description, body FROM docs WHERE name=?",
                   (name,)).fetchone()
    return {"name": r["name"], "description": r["description"],
            "body": (r["body"] or "")[:500]} if r else None


def top3(db, q, cands, names, size):
    """★only what the judge is confident about moves up front★ — everything else keeps its word-search rank."""
    floor = rerank.min_score(db)
    sc = (rerank.score_chunked(q, cands, size=size, db=db, cache=False) if size
          else rerank.score(q, cands, db=db, cache=False))
    if sc is None:
        return None
    hi = sorted([cands[i]["name"] for i in range(min(len(cands), len(sc)))
                 if sc[i] >= floor],
                key=lambda n: -sc[[c["name"] for c in cands].index(n)])
    rest = [n for n in names if n not in hi]
    return (hi + rest)[:3]


def main():
    db = store.connect()
    A = ei.load_A()
    b0 = rerank.budget()["used"]
    rows = []
    for q, gold in A:
        rs = search.recall(db, q, k=K, log=False)
        if not rs:
            continue
        names = [h["name"] for h in rs]
        cands = [c for c in (hydrate(db, n) for n in names) if c]
        rows.append((q, gold, cands, names,
                     any(ei.gold_hit(gold, n) for n in names[:3])))
    if N and len(rows) > N:
        import random
        random.Random(7).shuffle(rows)               # ★seed fixed★ — the same sample every day
        rows = rows[:N]
    base = sum(1 for r in rows if r[4])
    per_q = 1 + (max(1, (K + SIZE - 1) // SIZE) if SIZE else 1)   # one-shot + batches
    est = len(rows) * R * per_q
    print("sample %d%s · word-only %d (%.1f%%) · batch %s · %d run(s)" %
          (len(rows), " (shrunk by N)" if N else "", base,
           100.0 * base / len(rows), SIZE or "off", R))
    print("⛔ expected judge calls ★%d★ · available right now %d\n"
          % (est, max(0, rerank.budget()["left"] - rerank.RESERVE)))
    if est > max(0, rerank.budget()["left"] - rerank.RESERVE):
        print("⛔ budget is short — don't measure today (a half-measured value can't be compared).")
        return 2

    for size in (0, SIZE):
        runs, t0 = [], time.time()
        for _ in range(R):
            per = []
            for q, gold, cands, names, _was in rows:
                t = top3(db, q, cands, names, size)
                per.append(bool(t) and any(ei.gold_hit(gold, n) for n in t))
            runs.append(per)
        steady = sum(1 for i in range(len(rows))
                     if len(set(r[i] for r in runs)) == 1)
        print("  %-12s hit %s / %d  ★same decision %d/%d (%.0f%%)★  %.0fs"
              % ("one-shot" if size == 0 else "%d at a time" % size,
                 " · ".join(str(sum(r)) for r in runs), len(rows),
                 steady, len(rows), 100.0 * steady / len(rows), time.time() - t0))
    print("\nbudget %d used" % (rerank.budget()["used"] - b0))
    print("⛔ turn on `BRAIN_RERANK_CHUNK` ★only if the hit rate beats word-only(%d) and reproducibility rises★."
          % base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
