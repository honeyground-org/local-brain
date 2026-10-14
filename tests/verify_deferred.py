#!/usr/bin/env python3
"""★The hook updates the words index in place and leaves the databases for one worker★ (local · budget 0)

Why this exists (2026-10-14)
----------------------------
On the author's machine 27% of a day's prompts withdrew while reindexing: one changed document cost 1.70 s
against the hook's 0.6 s, ★1.10 s of it updating the graph database★ and 0.17 s re-reading every file for
trigger declarations. The hook now passes `sync=False, heal=False`, and §deferred starts one worker after the
answer. This check holds down:

  ① other callers are unchanged — by default a changed document still updates the databases and heals
  ② the hook's pass does neither, says so (`deferred`), and marks the work
  ③ ★an edited memory is never recalled with its old content★ — the words index is updated in place
     (control: before the reindex, the old word is what search finds)
  ④ the worker does the work, clears the mark, writes a record — and keeps a mark that arrived while it ran
  ⑤ `kick`: nothing to do → no worker; a worker already running → no second one; a crashed worker's lock is
     taken over; a real detached worker finishes and clears the mark
  ⑥ end to end: the hook, run as its own process on an edited note, answers from the new text, records the
     deferral, and the worker it started clears the mark
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-deferred-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
for k in ("BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE", "BRAIN_DEFERRED_LOCKED"):
    os.environ.pop(k, None)

from brain import deferred, observe, search, store, stores  # noqa: E402

FAILS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def note(name: str, body: str) -> None:
    p = os.path.join(_MEM, name + ".md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: fixture %s\n---\n\n%s\n" % (name, name, body))
    t = time.time() + 2                                   # newer than the last index, whatever the clock grain
    os.utime(p, (t, t))


class Count:
    """Counts calls of a function it stands in for."""

    def __init__(self, real, hook=None):
        self.real, self.n, self.hook = real, 0, hook

    def __call__(self, *a, **k):
        self.n += 1
        if self.hook:
            self.hook()
        return self.real(*a, **k)


def wait_cleared(db_path_home: str, secs: float = 30.0) -> bool:
    end = time.time() + secs
    while time.time() < end:
        db = store.connect()
        done = not deferred.pending(db).get("work") and not os.path.exists(deferred._lock_path())
        db.close()
        if done:
            return True
        time.sleep(0.3)
    return False


HOOK = r'''
import io, json, os, sys
sys.path.insert(0, os.environ["ROOT"])
from brain import hook
hook._timing_path = lambda: os.environ["LOG"]
hook.fallback = lambda db, prompt, **k: []
sys.stdin = io.StringIO(json.dumps({"prompt": "which routine mentions the %s step" % os.environ["WORD"]}))
sys.exit(hook.main())
'''


def main() -> int:
    print("=" * 78)
    print("the hook updates words in place and leaves the databases for one worker")
    print("=" * 78)
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"],
                                "exclude": ["MEMORY.md"], "max_depth": 1}]}, fh)
    note("routine_note", "The closing routine has a marigold step before the ledger is locked.")
    # a corpus large enough for document frequencies to mean something (two notes give every word no weight)
    topics = ("harbour ferry dawn", "orchard apple frost", "quarry stone chisel", "lantern wick oil",
              "copper wire spool", "garden hedge shears", "river barge lock", "signal flag mast",
              "marble column plinth", "candle wax mould", "window pane putty", "meadow hay bale")
    for i in range(30):
        note("other_note_%02d" % i, "A note about %s, number %d." % (topics[i % len(topics)], i))
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False)

    real_sync, real_heal = stores.sync_if_chosen, store.heal_triggers

    # ── ① other callers unchanged ────────────────────────────────────────────
    note("routine_note", "The closing routine has a saffron step before the ledger is locked.")
    stores.sync_if_chosen, store.heal_triggers = Count(real_sync), Count(real_heal)
    try:
        st = store.reindex(db, recalibrate=False)
        s1, h1 = stores.sync_if_chosen.n, store.heal_triggers.n
    finally:
        stores.sync_if_chosen, store.heal_triggers = real_sync, real_heal
    check("① by default a changed document still updates the databases and heals triggers",
          st.get("updated") == 1 and s1 == 1 and h1 == 1 and "deferred" not in st, "sync %d · heal %d" % (s1, h1))

    # ── ② ③ the hook's pass ──────────────────────────────────────────────────
    note("routine_note", "The closing routine has a juniper step before the ledger is locked.")
    before = [r["name"] for r in search.recall(db, "juniper", k=3, log=False)]
    old = [r["name"] for r in search.recall(db, "saffron", k=3, log=False)]
    check("③ ★control★: before the reindex, search still has the old word", "routine_note" in old
          and "routine_note" not in before, "old %s · new %s" % (old[:1], before[:1]))
    stores.sync_if_chosen, store.heal_triggers = Count(real_sync), Count(real_heal)
    try:
        st = store.reindex(db, recalibrate=False, sync=False, heal=False)
        s2, h2 = stores.sync_if_chosen.n, store.heal_triggers.n
    finally:
        stores.sync_if_chosen, store.heal_triggers = real_sync, real_heal
    check("② the hook's pass neither updates the databases nor heals, and says so",
          s2 == 0 and h2 == 0 and st.get("deferred") == ["sync", "heal"], "%s" % st.get("deferred"))
    p = deferred.pending(db)
    check("② the work is marked for the worker", p.get("work") == ["heal", "sync"] and p.get("n") == 1, json.dumps(p))
    new = [r["name"] for r in search.recall(db, "juniper", k=3, log=False)]
    gone = [r["name"] for r in search.recall(db, "saffron", k=3, log=False)]
    check("③ ★the edited text is found at once, and the old text no longer is★",
          new[:1] == ["routine_note"] and "routine_note" not in gone, "new %s · old %s" % (new[:1], gone[:1]))

    # ── ④ the worker ─────────────────────────────────────────────────────────
    stores.sync_if_chosen = Count(real_sync)
    try:
        out = deferred.run(db)
        s4 = stores.sync_if_chosen.n
    finally:
        stores.sync_if_chosen = real_sync
    rows, _ = observe.read("deferred", [observe.day()])
    check("④ the worker updates the databases, clears the mark and writes a record",
          s4 == 1 and out.get("cleared") is True and not deferred.pending(db).get("work") and rows,
          json.dumps(out))
    deferred.mark(db, ["sync"])
    stores.sync_if_chosen = Count(real_sync, hook=lambda: deferred.mark(db, ["sync"]))   # a hook marks mid-run
    try:
        out = deferred.run(db)
    finally:
        stores.sync_if_chosen = real_sync
    check("④ a mark that arrives while it runs is kept", out.get("cleared") is False
          and deferred.pending(db).get("work") == ["sync"], json.dumps(deferred.pending(db)))
    store.set_meta(db, deferred.PENDING, "")

    # ── ⑤ kick ───────────────────────────────────────────────────────────────
    check("⑤ nothing to do → no worker", deferred.kick(db) is False and not os.path.exists(deferred._lock_path()))
    deferred.mark(db, ["sync"])
    with open(deferred._lock_path(), "w", encoding="utf-8") as fh:
        fh.write("{}")
    check("⑤ a worker already running → no second one", deferred.kick(db) is False)
    old_t = time.time() - deferred.STALE_SEC - 5
    os.utime(deferred._lock_path(), (old_t, old_t))
    started = deferred.kick(db)
    check("⑤ a crashed worker's lock is taken over, and a real detached worker clears the mark",
          started is True and wait_cleared(_TMP), "started %s · pending %s" % (started, deferred.pending(db)))

    # ── ⑥ end to end ─────────────────────────────────────────────────────────
    note("routine_note", "The closing routine has a lavender step before the ledger is locked.")
    log = os.path.join(_TMP, "hook.jsonl")
    drv = os.path.join(_TMP, "hook_driver.py")
    with open(drv, "w", encoding="utf-8") as fh:
        fh.write(HOOK)
    t = time.time()
    r = subprocess.run([sys.executable, drv], capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, ROOT=ROOT, LOG=log, WORD="lavender"), encoding="utf-8")
    took = time.time() - t
    rec = json.loads(open(log, encoding="utf-8").read().splitlines()[-1]) if os.path.exists(log) else {}
    rx = rec.get("reindex") or {}
    check("⑥ the hook answers from the edited note", "routine_note" in r.stdout, r.stdout[-120:])
    check("⑥ it records the reindex and what it left for later",
          rx.get("ran") is True and rx.get("deferred") == ["sync", "heal"], json.dumps(rx))
    check("⑥ and the worker it started clears the mark", wait_cleared(_TMP), "hook took %.2fs" % took)
    db.close()

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — words are updated in place, the databases by one worker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
