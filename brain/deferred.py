"""★The work the hook leaves for later★ — updating the dedicated databases and healing triggers (2026-10-14).

Why this exists
---------------
The recall hook reindexes changed notes before it searches, inside a 0.6 s budget. Measured on the author's
index on 2026-10-14, one changed document cost 1.70 s: ★1.10 s updating the graph database★, 0.17 s re-reading
every file for trigger declarations, the rest the words index. Past the budget the hook withdraws, so on a
machine whose notes keep changing (several sessions editing at once) 27% of a day's prompts attached nothing.

So the hook updates the words index in place — an edited memory must never be recalled with its old content
(§store.needs_reindex) — and leaves the rest here (`store.reindex(sync=False, heal=False)` marks it). After it
has answered, it starts ★one★ worker process (§kick) that does that work and clears the mark. Until it lands,
the dedicated databases answer from the local copy (§stores.serve), which holds the same answers.

- One worker at a time: a lock file made with O_EXCL; a lock older than `STALE_SEC` is a crashed worker's.
- The worker is a separate process the hook does not wait for (a new session on POSIX, a detached process on
  Windows) — the hook's own time is not spent on it.
- A worker that fails leaves the mark, and the next prompt starts another.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import List

PENDING = "deferred_work"        # meta key: {"work": [...], "since": epoch, "n": marks so far}
LOCK = "deferred.lock"
STALE_SEC = 600                  # a worker holding the lock this long is gone (a sync is bounded by its timeout)


def mark(db, work: List[str]) -> None:
    """Add work for the worker — kept until a worker finishes it."""
    from brain import store
    have = pending(db)
    got = sorted(set(have.get("work") or []) | set(work))
    store.set_meta(db, PENDING, json.dumps({"work": got, "since": have.get("since") or time.time(),
                                            "n": int(have.get("n") or 0) + 1}))


def pending(db) -> dict:
    from brain import store
    try:
        return json.loads(store.get_meta(db, PENDING, "") or "{}")
    except ValueError:
        return {}


def _lock_path() -> str:
    from brain import store
    return os.path.join(store.brain_home(), LOCK)


def _take_lock() -> bool:
    p = _lock_path()
    for _ in range(2):
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, json.dumps({"pid": os.getpid(), "at": time.time()}).encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(p) < STALE_SEC:
                    return False
                os.remove(p)                             # a crashed worker's — take it over
            except OSError:
                return False
        except OSError:
            return False
    return False


def _release() -> None:
    try:
        os.remove(_lock_path())
    except OSError:
        pass


def kick(db) -> bool:
    """Start the worker if there is work and no worker is running. Never waits, never raises."""
    try:
        if not pending(db).get("work"):
            return False
        if not _take_lock():
            return False
        try:
            _spawn()
            return True
        except Exception:                                # noqa: BLE001
            _release()
            return False
    except Exception:                                    # noqa: BLE001
        return False


def _spawn() -> None:
    from brain import store
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["BRAIN_HOME"] = store.brain_home()              # the home this process adopted (§store.adopt_home_from_argv)
    env["BRAIN_DEFERRED_LOCKED"] = "1"                  # the lock is already taken for it
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True, env=env, cwd=root)
    if os.name == "nt":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    else:
        kw["start_new_session"] = True
    from brain import launch                             # the installed brain, never a `brain/` in some folder
    subprocess.Popen(launch.argv("brain.deferred"), **kw)


def run(db) -> dict:
    """Do the pending work now: index anything new, update the chosen databases, heal triggers."""
    from brain import observe, store, stores
    t0 = time.time()
    start = pending(db)
    todo = start.get("work") or []
    out = {"work": todo}
    stats = store.reindex(db, recalibrate=False)        # anything changed since the hook; heals triggers
    out["changed"] = sum(int(stats.get(k) or 0) for k in ("added", "updated", "removed"))
    ok = True
    if "sync" in todo:
        pushed = stores.sync_if_chosen(db)
        out["stores"] = {r: ("error" if v.get("error") else "unchanged" if v.get("unchanged") else "sent")
                         for r, v in pushed.items()}
        ok = not any(v == "error" for v in out["stores"].values())
    # ⛔ ★Only the work this run saw★ — a hook that marked more while it ran keeps its mark (the count moved).
    out["cleared"] = ok and int(pending(db).get("n") or 0) == int(start.get("n") or 0)
    if out["cleared"]:
        store.set_meta(db, PENDING, "")
    out["secs"] = round(time.time() - t0, 3)
    observe.write("deferred", out)
    return out


def main() -> int:
    from brain import store
    if not os.environ.get("BRAIN_DEFERRED_LOCKED") and not _take_lock():
        return 0                                         # another worker is on it
    try:
        db = store.connect()
        run(db)
        db.close()
    except Exception:                                    # noqa: BLE001
        pass                                             # the mark stays; the next prompt starts another
    finally:
        _release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
