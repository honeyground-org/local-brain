#!/usr/bin/env python3
"""★The recall hook ends before the host's wall, whatever it is blocked in★ (2026-09-29).

Why this exists
---------------
The installer gives the host a 5s limit for this hook (`hook.HOOK_TIMEOUT_SEC`). The transcripts
hold ★123★ `hook_cancelled · timedOut · 5000ms` records for it (08-26 → 09-29), in bursts. A later
replay of the same prompts finished inside 2.7s, so the cause was the moment, not the prompt — and
the hook had four waits with no common bound: a SQLite lock (15s), an embedding call (60s ×2), the
judge (25s), DNS (none). Only the lexical stage was ever guarded.

So this check does not test one cause. It runs the hook ★as its own process, the way the host does★,
blocks it in two different shapes, and asks the same three questions each time:

  ① did the process end ★before★ the wall?
  ② was what was already ready still handed over (a declared trigger is a lookup, done first)?
  ③ does the timing record say ★which stage★ it was cut in?

  shape A — a stage that never returns (the network: fallback)
  shape B — ★blocked inside C★ on a real SQLite write lock. This is the one that decides whether a
            watchdog thread can work at all: it only gets to run if the blocked call releases the GIL.

⛔ Controls — a normal run is not cut, writes exactly one record and answers as before; a cut with
   nothing ready writes ★nothing★ (never half an answer); `claim()` keeps the watchdog quiet once the
   main thread has answered.
⛔ Calibrated against the code before the fix (2026-09-29): shape A ran 30.1s and shape B 30.3s —
   both to the end of the injected block. A did print its answer, ★25 seconds after the host would
   have cancelled it★; B printed nothing. Both rows above go red on that code.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain import hook, store  # noqa: E402

FAILS = []
BLOCK = 30.0          # what the injected fault would cost without the guard


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


# The driver is the hook's own `main()` in a fresh interpreter, with one stage replaced. It never
# writes to the real timing log (the path is redirected) and never holds the real index's lock.
DRIVER = r'''
import io, json, os, sqlite3, sys, time
sys.path.insert(0, os.environ["ROOT"])
from brain import hook, search
shape, doc_id, doc_name, log, lockdb, block = sys.argv[1:7]
block = float(block)
hook._timing_path = lambda: log
if doc_id:
    hook._named = lambda p, db=None: [("probe phrase", int(doc_id), doc_name)]
else:
    hook._named = lambda p, db=None: []
search.recall = lambda *a, **k: []                    # words fall silent → the fallback runs
if shape == "network":
    hook.fallback = lambda db, prompt, **k: (time.sleep(block), [])[1]
elif shape == "lock":
    from brain import store
    store.needs_reindex = lambda db: True
    def _stuck(db, **k):                              # ★a real busy-wait, inside sqlite3's C code★
        c = sqlite3.connect(lockdb, timeout=block)
        c.execute("INSERT INTO t VALUES (1)")
        c.commit()
    store.reindex = _stuck
elif shape == "normal":
    hook.fallback = lambda db, prompt, **k: []
sys.stdin = io.StringIO(json.dumps({"prompt": "a prompt long enough to pass the length gate"}))
try:
    sys.exit(hook.main())
except SystemExit:
    raise
except Exception:
    sys.exit(0)
'''


def _run(shape: str, doc, log: str, lockdb: str = "") -> dict:
    drv = os.path.join(os.path.dirname(log), "driver.py")
    with open(drv, "w", encoding="utf-8") as fh:
        fh.write(DRIVER)
    t0 = time.time()
    p = subprocess.run([sys.executable, drv, shape, str(doc[0]) if doc else "",
                        doc[1] if doc else "", log, lockdb, str(BLOCK)],
                       capture_output=True, text=True, timeout=BLOCK + 15,
                       env=dict(os.environ, ROOT=ROOT))
    rec = []
    if os.path.exists(log):
        with open(log, encoding="utf-8") as fh:
            rec = [json.loads(x) for x in fh if x.strip()]
        os.remove(log)
    return {"secs": time.time() - t0, "code": p.returncode, "out": p.stdout,
            "err": p.stderr[-300:], "rec": rec}


def main() -> int:
    wall = hook.HOOK_TIMEOUT_SEC
    print("=" * 78)
    print("the hook's wall: %ss (installed) · the watchdog fires %.1fs before it"
          % (wall, hook.WATCHDOG_MARGIN_SEC))
    print("=" * 78)

    # ⓪ one source for the wall — the installer must write the number the watchdog is set against
    src = open(os.path.join(ROOT, "brain", "install.py"), encoding="utf-8").read()
    check("the installer writes ★the hook's own constant★, not a literal",
          "HOOK_TIMEOUT_SEC" in src and '5, "recalling"' not in src)
    check("the watchdog sits inside the wall", 0 < hook.WATCHDOG_MARGIN_SEC < wall)

    db = store.connect()
    row = db.execute("SELECT id, name FROM docs ORDER BY id LIMIT 1").fetchone()
    doc = (row["id"], row["name"]) if row else None
    if doc is None:
        print("⚠️  empty index — ② (what was ready is handed over) cannot be measured here")

    tmp = tempfile.mkdtemp(prefix="brain-wall-")
    log = os.path.join(tmp, "timing.jsonl")

    # ── shape A: the network stage never returns ──────────────────────────────
    r = _run("network", doc, log)
    check("A ★a stage that never returns ends before the wall★",
          r["secs"] < wall and r["code"] == 0,
          "%.2fs (the fault would have cost %.0fs) · exit %s" % (r["secs"], BLOCK, r["code"]))
    if doc:
        check("A   ★what was ready is still handed over★ (the declared trigger)",
              doc[1] in r["out"] and r["out"].count("hookSpecificOutput") == 1,
              "%d answer(s)" % r["out"].count("hookSpecificOutput"))
    rec = r["rec"][-1] if r["rec"] else {}
    check("A   the record says ★cut, in the fallback★",
          len(r["rec"]) == 1 and rec.get("cut") is True and rec.get("stage") == "fallback",
          json.dumps(rec)[:120])

    # ── shape B: blocked inside C, on a real SQLite write lock ────────────────
    lockdb = os.path.join(tmp, "locked.db")
    holder = sqlite3.connect(lockdb, isolation_level=None)
    holder.execute("CREATE TABLE t(x)")
    holder.execute("BEGIN IMMEDIATE")                   # ★held for the whole run★
    try:
        r = _run("lock", doc, log, lockdb)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    check("B ★blocked in a SQLite busy-wait (C code) still ends before the wall★",
          r["secs"] < wall and r["code"] == 0,
          "%.2fs (the lock would have held it %.0fs)" % (r["secs"], BLOCK))
    check("B   nothing was ready, so ★nothing is written★ — never half an answer",
          r["out"].strip() == "", repr(r["out"][:60]))
    rec = r["rec"][-1] if r["rec"] else {}
    check("B   the record says ★cut, while indexing★",
          len(r["rec"]) == 1 and rec.get("cut") is True and rec.get("stage") == "index",
          json.dumps(rec)[:120])

    # ── controls ─────────────────────────────────────────────────────────────
    r = _run("normal", doc, log)
    rec = r["rec"][-1] if r["rec"] else {}
    check("★control★: a normal run is not cut, and writes exactly one record",
          r["secs"] < 2.0 and len(r["rec"]) == 1 and rec.get("cut") is False,
          "%.2fs · %s" % (r["secs"], json.dumps(rec)[:100]))
    if doc:
        check("★control★:   and still answers, once",
              doc[1] in r["out"] and r["out"].count("hookSpecificOutput") == 1)

    from brain import deadline                           # noqa: E402
    fired = []
    wd = deadline.Watchdog(0.05, lambda: fired.append(1))
    claimed = wd.claim()                                 # the main thread answered first
    wd.start()
    time.sleep(0.3)                                      # ⛔ if it fired, this process would be gone
    check("★control★: once the main thread has claimed the answer, the watchdog stays quiet",
          claimed and not fired and not wd.fired)

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — the wall holds in both shapes, and the controls stay quiet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
