#!/usr/bin/env python3
"""★The same files get the same ids on every filesystem★ — so the same corpus measures the same anywhere. (local · budget 0)

## Why (2026-10-10)

A document's id is the order it was first indexed in, and ids decide ties in a ranking and which documents
the proxy sample draws (§proxy.build: `ORDER BY id`). The indexer took files in the order `os.walk` gave
them — the filesystem's order: sorted on macOS, hash order on Linux. The same corpus and the same code then
measured hit@3 0.883 on macOS and 0.833 on Linux (tests/verify_quality_ab.py); with the walk sorted, both
measured 0.900.

  ① however the filesystem orders a folder, the indexer walks folders and files in sorted order
  ② a fresh index numbers documents in that order
  ③ control: the filesystem order used here really differs from the sorted one — else ① proves nothing

How to run:  PYTHONPATH=. python3 tests/verify_index_order.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-order-")
_KEYS = ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_ADAPTIVE_LEARN", "BRAIN_TRANSLIT_MS")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ.update(BRAIN_HOME=os.path.join(_TMP, "home"), BRAIN_CONFIG=os.path.join(_TMP, "config.json"),
                  BRAIN_ADAPTIVE_LEARN="0", BRAIN_TRANSLIT_MS="0")

from brain import store  # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def main() -> int:
    mem = os.path.join(_TMP, "memory")
    names = ["zeta_last", "alpha_first", "mid_note", "beta_two", "omega_end", "gamma_three"]
    for sub in ("b_folder", "a_folder"):
        os.makedirs(os.path.join(mem, sub))
    for i, n in enumerate(names):
        where = os.path.join(mem, ("a_folder", "b_folder", "")[i % 3])
        with open(os.path.join(where, n + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: note %s\n---\n\nThe body of %s, long enough to index.\n" % (n, n, n))
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": mem, "include": ["*.md"]}]}, fh)

    real_walk = os.walk

    def shuffled_walk(top, *a, **k):
        """What a hash-ordered filesystem may hand back: the same entries, ★reversed★ — never sorted.

        ⛔ A seeded shuffle of six names came out sorted on one CI run (the seed was a random temp path),
           and the control below went red for nothing. Reversed is unsorted every time.
        """
        for dirpath, dirnames, filenames in real_walk(top, *a, **k):
            dirnames.sort(reverse=True)
            yield dirpath, dirnames, sorted(filenames, reverse=True)
    store.os.walk = shuffled_walk
    try:
        got = [os.path.relpath(p, mem) for p, _s, _r in store.iter_source_files(store.load_config())]
        raw = [os.path.relpath(os.path.join(d, f), mem) for d, _ds, fs in shuffled_walk(mem) for f in fs]
        db = store.connect()
        store.reindex(db, full=True, recalibrate=False, refresh_history=False)
        ids = [r[0] for r in db.execute("SELECT name FROM docs ORDER BY id")]
    finally:
        store.os.walk = real_walk

    want = [os.path.relpath(os.path.join(d, f), mem) for d, ds, fs in
            ((d, sorted(ds), sorted(fs)) for d, ds, fs in real_walk(mem)) for f in fs]
    print("① the walk")
    check("folders and files come in sorted order, whatever the filesystem's order", got == want, " · ".join(got))
    print("\n② a fresh index")
    order = [os.path.splitext(os.path.basename(p))[0] for p in got]
    check("documents are numbered in that order", ids == order, " · ".join(ids))
    print("\n③ control")
    check("(control) the filesystem order used here is not already sorted", raw != want, " · ".join(raw))
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the same files get the same ids on every filesystem")
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
