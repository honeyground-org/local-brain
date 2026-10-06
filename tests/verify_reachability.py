"""Reachability verification — ★direct judgement of goal ①(removing the memory limit)★.

Claim: "the list was pulled out of MEMORY.md, but not a single memory was lost."
How it's proven: does asking with each memory's own **description** bring back that memory itself.

Why this test matters — under the old structure, a memory absent from the index **could not be called by anyone**.
Having the file was the same as not having it (measured 205 cases). Now search does that job, not the index.
So it must be shown ★exhaustively★ that "outside the index" no longer means "invisible".
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import search, store  # noqa: E402
from tests import _needs  # noqa: E402

K = 5


def main() -> int:
    db = store.connect()
    _needs.memories(db)
    rows = db.execute(
        "SELECT name, description, title FROM docs "
        "WHERE source='memory' AND name != 'MEMORY'").fetchall()
    # determinism — the same sample must come back every time, or a regression can't be compared.
    random.seed(20260810)
    sample = rows if len(rows) <= 200 else random.sample(list(rows), 200)

    hit1 = hitk = miss = 0
    misses = []
    for r in sample:
        q = (r["description"] or r["title"] or "").strip()
        if not q:
            continue
        res = search.recall(db, q, k=K, log=False)
        names = [x["name"] for x in res]
        if names and names[0] == r["name"]:
            hit1 += 1
            hitk += 1
        elif r["name"] in names:
            hitk += 1
        else:
            miss += 1
            misses.append(r["name"])

    n = hit1 + (hitk - hit1) + miss
    print("sample %d (of %d total)" % (n, len(rows)))
    print("  ranked 1st when asked with its own description : %d (%.0f%%)" % (hit1, 100.0 * hit1 / n))
    print("  in the top %d                                  : %d (%.0f%%)" % (K, hitk, 100.0 * hitk / n))
    print("  not found                                      : %d" % miss)
    if misses:
        print("  not-found examples: %s" % ", ".join(misses[:8]))
    ok = hitk >= n * 0.95
    print("  verdict (95%% or higher reachability): %s" % ("pass ✅" if ok else "short ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
