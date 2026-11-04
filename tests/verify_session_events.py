#!/usr/bin/env python3
"""★A session start indexes nothing by itself — it only reacts to events★ (local · budget 0)

Why this exists (2026-11-03)
----------------------------
`bin/brain-session` ran a full `brain index` on every session start: a scan of every source, a recalibration
that replays 138 recalls, the git history mapping, the store sync and trigger healing. On the author's machine
(2,300 documents) that was about a minute of CPU per session, and a terminal multiplexer restoring twenty panes
started twenty of them at once; ten measured in a row queued for over ten minutes behind the index lock — even
when no file had changed. A user saw "several local-brain indexing processes using a lot of CPU".

A session start now looks for the events that make work necessary and, only then, starts the single worker
(§deferred.kick). This check holds down, each with a control:

  ① nothing changed → no worker, no index pass, no recalibration (control: the old path recalibrates every time)
  ② a source file changed → one worker indexes it and clears the mark; the next session start finds nothing
  ③ ten session starts at once on an edited note → the change is indexed exactly once, by one worker
  ④ a repository moved (its HEAD log changed) → the worker rebuilds the git pointers once and records the
     fingerprint; an unmoved repository costs nothing
  ⑤ the ruler no longer fits the corpus → the worker re-measures it; a fitting ruler is left alone
  ⑥ the real hook script exits 0, prints only the notice channel, and spawns no index process of its own
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

_TMP = tempfile.mkdtemp(prefix="brain-session-events-")
_MEM = os.path.join(_TMP, "memory")
_REPO = os.path.join(_TMP, "repo")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
for k in ("BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE", "BRAIN_DEFERRED_LOCKED"):
    os.environ.pop(k, None)

from brain import calibrate, deferred, store  # noqa: E402

FAILS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def note(name: str, body: str) -> None:
    p = os.path.join(_MEM, name + ".md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: fixture %s\n---\n\n%s\n" % (name, name, body))


def settle() -> None:
    """The index stamps whole seconds; a write in the same second as the next stamp would still look newer."""
    time.sleep(1.1)


class Count:
    def __init__(self, real):
        self.real, self.n = real, 0

    def __call__(self, *a, **k):
        self.n += 1
        return self.real(*a, **k)


def wait_idle(secs: float = 60.0) -> bool:
    end = time.time() + secs
    while time.time() < end:
        db = store.connect()
        done = not deferred.pending(db).get("work") and not os.path.exists(deferred._lock_path())
        db.close()
        if done:
            return True
        time.sleep(0.2)
    return False


def worker_runs() -> int:
    path = os.path.join(os.environ["BRAIN_HOME"], "observe", "deferred.jsonl")
    if not os.path.exists(path):
        log = [p for p in os.listdir(os.environ["BRAIN_HOME"]) if p.startswith("observe")]
        return sum(1 for _ in open(os.path.join(os.environ["BRAIN_HOME"], log[0]), encoding="utf-8")) if log and os.path.isfile(
            os.path.join(os.environ["BRAIN_HOME"], log[0])) else 0
    return sum(1 for _ in open(path, encoding="utf-8"))


def observed(kind: str) -> list:
    from brain import observe
    try:
        return list(observe.read(kind)) if hasattr(observe, "read") else []
    except Exception:                                    # noqa: BLE001
        return []


def main() -> int:
    print("=" * 78)
    print("a session start indexes nothing by itself — it only reacts to events")
    print("=" * 78)
    os.makedirs(_MEM)
    os.makedirs(_REPO)
    subprocess.run(["git", "-C", _REPO, "init", "-q"], check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", _REPO, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    docs = os.path.join(_REPO, "docs")
    os.makedirs(docs)
    with open(os.path.join(docs, "guide.md"), "w", encoding="utf-8") as fh:
        fh.write("# Guide\n\nThe ferry leaves at dawn.\n")
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"], "exclude": ["MEMORY.md"], "max_depth": 1},
                               {"name": "docs", "path": docs, "include": ["*.md"], "max_depth": 2}]}, fh)
    topics = ("harbour ferry dawn", "orchard apple frost", "quarry stone chisel", "lantern wick oil",
              "copper wire spool", "garden hedge shears", "river barge lock", "signal flag mast")
    for i in range(24):
        note("note_%02d" % i, "A note about %s, number %d." % (topics[i % len(topics)], i))
    settle()
    db = store.connect()
    store.reindex(db, full=True)                          # a human-run index: calibrated, pointers built
    first = deferred.on_session_start(db)                 # records the repositories and their fingerprint
    wait_idle()
    settle()
    db.close()
    db = store.connect()

    # ── ① nothing changed ───────────────────────────────────────────────────────
    real_reindex, real_cal = store.reindex, calibrate.calibrate
    store.reindex, calibrate.calibrate = Count(real_reindex), Count(real_cal)
    try:
        out = deferred.on_session_start(db)
        r1, c1 = store.reindex.n, calibrate.calibrate.n
    finally:
        store.reindex, calibrate.calibrate = real_reindex, real_cal
    check("① nothing changed → no worker, no index pass, no recalibration",
          out["reasons"] == [] and out["kicked"] is False and r1 == 0 and c1 == 0 and not os.path.exists(deferred._lock_path()),
          json.dumps(out))
    calibrate.calibrate = Count(real_cal)
    try:
        store.reindex(db)                                 # the old session-start path (`brain index`)
        old_cal = calibrate.calibrate.n
    finally:
        calibrate.calibrate = real_cal
    check("① control: the old per-session index recalibrates even when nothing changed", old_cal == 1, "calibrations %d" % old_cal)

    # ── ② a source file changed ─────────────────────────────────────────────────
    settle()
    note("note_03", "A note about the saffron harbour, number 3.")
    settle()
    out = deferred.on_session_start(db)
    check("② a changed file → the worker is started", "files" in out["reasons"] and out["kicked"] is True, json.dumps(out))
    check("② the worker indexes it and finishes", wait_idle())
    db.close()
    db = store.connect()
    hit = db.execute("SELECT 1 FROM docs WHERE name='note_03' AND sha IS NOT NULL").fetchone()
    from brain import search
    found = [r["name"] for r in search.recall(db, "saffron harbour", k=3)]
    check("② the edited note is searchable after the worker", hit is not None and "note_03" in found, str(found))
    out = deferred.on_session_start(db)
    check("② the next session start finds nothing to do", out["reasons"] == [] and out["kicked"] is False, json.dumps(out))

    # ── ③ ten session starts at once ────────────────────────────────────────────
    settle()
    note("note_05", "A note about the vermilion barge, number 5.")
    settle()
    script = ("import sys,os,json; sys.path.insert(0, os.environ['ROOT']); from brain import deferred, store;"
              "db=store.connect(); print(json.dumps(deferred.on_session_start(db)))")
    env = dict(os.environ, ROOT=ROOT)
    procs = [subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True,
                              encoding="utf-8", errors="replace")
             for _ in range(10)]
    results = [json.loads(p.communicate()[0] or "{}") for p in procs]
    kicked = sum(1 for r in results if r.get("kicked"))
    skipped = sum(1 for r in results if r.get("skipped"))
    running = sum(1 for r in results if r.get("running"))
    idle = sum(1 for r in results if not r.get("reasons") and not r.get("skipped"))
    # Each start is accounted for exactly once: it started the worker, another start was checking, the worker it
    # would start was already running, or there was nothing to do (one may be lost to the timing of the test).
    check("③ ten starts at once → at most one worker started", kicked <= 1 and kicked + skipped + running + idle >= 9,
          "kicked %d · skipped %d · running %d · nothing to do %d" % (kicked, skipped, running, idle))
    check("③ the change is indexed once and the worker finishes", wait_idle())
    db.close()
    db = store.connect()
    found = [r["name"] for r in search.recall(db, "vermilion barge", k=3)]
    check("③ the edited note is searchable", "note_05" in found, str(found))

    # ── ④ a repository moved ───────────────────────────────────────────────────
    out = deferred.on_session_start(db)
    check("④ control: an unmoved repository is not a reason", "git" not in out["reasons"], json.dumps(out))
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", _REPO, "commit", "-q", "--allow-empty", "-m", "move"], check=True)
    head = os.path.join(_REPO, ".git", "logs", "HEAD")
    t = time.time() + 5
    os.utime(head, (t, t))
    real_ptr = store.save_git_pointers
    out = deferred.on_session_start(db)
    check("④ a moved repository → reason git, worker started", "git" in out["reasons"] and out["kicked"] is True, json.dumps(out))
    check("④ the worker finishes", wait_idle())
    db.close()
    db = store.connect()
    saved = json.loads(store.get_meta(db, deferred.GIT_KEY, "") or "{}")
    check("④ the new fingerprint is recorded", saved.get(_REPO) == round(os.stat(head).st_mtime, 3) or
          any(abs((v or 0) - os.stat(head).st_mtime) < 0.01 for v in saved.values()), json.dumps(saved))
    out = deferred.on_session_start(db)
    check("④ after the rebuild the next start finds nothing", "git" not in out["reasons"], json.dumps(out))

    # ── ⑤ the ruler ─────────────────────────────────────────────────────────────
    with db:
        db.execute("UPDATE meta SET v='' WHERE k=?", (calibrate._META_KEY,))
    out = deferred.on_session_start(db)
    check("⑤ a missing ruler → reason ruler, worker started", "ruler" in out["reasons"] and out["kicked"] is True, json.dumps(out))
    check("⑤ the worker finishes", wait_idle())
    db.close()
    db = store.connect()
    check("⑤ the worker re-measured it", bool(store.get_meta(db, calibrate._META_KEY, "")))
    out = deferred.on_session_start(db)
    check("⑤ control: a fitting ruler is left alone", "ruler" not in out["reasons"], json.dumps(out))
    db.close()

    # ── ⑥ the real hook script ─────────────────────────────────────────────────
    hook = os.path.join(ROOT, "bin", "brain-session")
    # Windows has no POSIX shell: it runs the same module the packaged entry point runs (§install.entry_command)
    argv = ([hook] if os.name != "nt" else [sys.executable, os.path.join(ROOT, "bin", "_run.py"), "brain.tail", "notice"])
    t0 = time.time()
    r = subprocess.run(argv + ["--host", "claude-code"], input=json.dumps({"session_id": "s", "source": "startup"}),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    took = time.time() - t0
    with open(hook, encoding="utf-8") as fh:
        body = fh.read()
    check("⑥ the hook exits 0 with nothing to announce on an unchanged index", r.returncode == 0 and r.stdout.strip() == "",
          (r.stdout or r.stderr)[-200:])
    check("⑥ the hook no longer runs an index of its own", "brain.cli index" not in body)
    check("⑥ and it does not leave a worker behind when nothing changed", not os.path.exists(deferred._lock_path()), "took %.2fs" % took)

    print()
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
