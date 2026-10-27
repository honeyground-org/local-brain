"""Single background worker for indexing, serving-store sync and local policy measurement.

Hooks read the existing index and start this process only after answering. The worker
owns a tokenised lease, a total elapsed-time wall, and a persisted retry cooldown.
Database sync ledgers and embedding batches checkpoint work independently. No source
file is rewritten by this module; pending generations survive concurrent markers.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import math
import uuid
import os
import subprocess
import sys
import time
from typing import List

PENDING = "deferred_work"        # meta key: {"work": [...], "since": epoch, "n": marks so far}
LOCK = "deferred.lock"
STALE_SEC = 600                  # recovery grace for an ownerless legacy lock, not a live worker timeout
_TOKEN = ""


def budget() -> float:
    """Administrative wall, configurable by the host; not a learned retrieval parameter."""
    raw = os.environ.get("BRAIN_WORKER_TIMEOUT", str(STALE_SEC))
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("BRAIN_WORKER_TIMEOUT must be a finite positive number")
    return value


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True  # unknown ownership is not permission to start a second writer


def _owner() -> dict:
    try:
        with open(_lock_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}



def mark(db, work: List[str]) -> None:
    """Add work for the worker — kept until a worker finishes it."""
    from brain import store
    with db:
        # Acquire the write lock before reading, so simultaneous producers cannot lose a generation.
        db.execute("INSERT OR IGNORE INTO meta(k,v) VALUES(?, '')", (PENDING,))
        have = pending(db)
        got = sorted(set(have.get("work") or []) | set(work))
        value = json.dumps({"work": got, "since": have.get("since") or time.time(),
                            "n": int(have.get("n") or 0) + 1})
        db.execute("UPDATE meta SET v=? WHERE k=?", (value, PENDING))


def pending(db) -> dict:
    from brain import store
    try:
        return json.loads(store.get_meta(db, PENDING, "") or "{}")
    except ValueError:
        return {}


def _lock_path() -> str:
    from brain import store
    return os.path.join(store.brain_home(), LOCK)


@contextmanager
def _lease_guard():
    """Serialise lease inspection/replacement without waiting in the hook.

    The guard file is never unlinked: every process locks the same inode. The OS
    releases the advisory lock even when its owner dies during lease recovery.
    """
    fh = open(_lock_path() + ".guard", "a+b")
    held = False
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fh.fileno()).st_size == 0:
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                held = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
            except OSError:
                pass
        yield held
    finally:
        if held and os.name == "nt":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        fh.close()


def _take_lock() -> bool:
    global _TOKEN
    p = _lock_path()
    try:
        with _lease_guard() as held:
            if not held:
                return False
            if os.path.exists(p):
                owner = _owner()
                if _alive(int(owner.get("pid") or 0)):
                    return False
                if not owner.get("pid") and time.time() - os.path.getmtime(p) < STALE_SEC:
                    return False
                os.remove(p)
            token = uuid.uuid4().hex
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "at": time.time(), "token": token}, fh)
            _TOKEN = token
            return True
    except (OSError, ValueError):
        return False


def _release() -> None:
    # A finishing old worker must never remove a successor's lease.
    try:
        with _lease_guard() as held:
            if held and _TOKEN and _owner().get("token") == _TOKEN:
                os.remove(_lock_path())
    except OSError:
        pass


def _retry_state():
    from brain import store
    path = os.path.join(store.brain_home(), "deferred-retry.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return path, json.load(fh)
    except (OSError, ValueError):
        return path, {}


def _retry(failed, seconds):
    path, previous = _retry_state()
    failures = min(20, int(previous.get("failures", 0)) + 1) if failed else 0
    delay = min(seconds, 2 ** failures) if failures else 0
    tmp = path + "." + uuid.uuid4().hex
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"failures": failures, "after": time.time() + delay}, fh)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def kick(db=None, scan: bool = False) -> bool:
    """Start the worker if there is work and no worker is running. Never waits, never raises."""
    try:
        if time.time() < float(_retry_state()[1].get("after", 0)):
            return False
        if not scan and not pending(db).get("work"):
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
    env["BRAIN_DEFERRED_LOCKED"] = _TOKEN                  # the lock is already taken for it
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
    ok = not any(v == "error" for v in (stats.get("stores") or {}).values())
    if "sync" in todo and not stats.get("stores"):
        pushed = stores.sync_if_chosen(db)
        out["stores"] = {r: ("error" if v.get("error") else "unchanged" if v.get("unchanged") else "sent")
                         for r, v in pushed.items()}
        ok = not any(v == "error" for v in out["stores"].values())
    from brain import calibrate
    calibrate.threshold(db)  # missing/drifted rulers are measured outside the hook
    from brain import personal
    out["personal"] = {mode: personal.update(db, mode) for mode in ("query", "terms")}
    # ⛔ ★Only the work this run saw★ — a hook that marked more while it ran keeps its mark (the count moved).
    # Compare and clear in one write: a concurrent marker cannot be lost between read and clear.
    out["cleared"] = ok and not pending(db)
    if ok:
        with db:
            current = store.get_meta(db, PENDING, "")
            if (json.loads(current or "{}") == start):
                changed = db.execute("UPDATE meta SET v='' WHERE k=? AND v=?", (PENDING, current)).rowcount
                out["cleared"] = bool(changed) or not current
    out["secs"] = round(time.time() - t0, 3)
    observe.write("deferred", out)
    return out


def main() -> int:
    from brain import deadline, observe, store
    global _TOKEN
    seconds = budget()
    inherited = os.environ.get("BRAIN_DEFERRED_LOCKED", "")
    if inherited:
        with _lease_guard() as held:
            if not held or _owner().get("token") != inherited:
                return 3
            _TOKEN = inherited
            # Transfer ownership before doing anything that can block.
            with open(_lock_path(), "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "at": time.time(), "token": _TOKEN}, fh)
    elif not _take_lock():
        return 0
    t0 = time.monotonic()

    def expired():
        try:
            _retry(True, seconds)
            observe.write("deferred", {"outcome": "timeout", "secs": time.monotonic() - t0,
                                       "budget": seconds, "cleared": False})
        finally:
            # Keep the lease until process death: the main thread can still be in a write.
            # The next worker recovers it only after the owning PID is no longer alive.
            pass

    wd = deadline.watchdog(seconds, expired, exit_code=3)
    db = None
    result = 0
    try:
        db = store.connect()
        out = run(db)
        if not out.get("cleared"):
            result = 3
    except Exception as exc:                              # pending work survives failures
        observe.write("deferred", {"outcome": "error", "error": type(exc).__name__,
                                   "secs": time.monotonic() - t0, "cleared": False})
        result = 3
    finally:
        if db is not None:
            db.close()
        if wd.claim():
            wd.cancel()
            _retry(result != 0, seconds)
            _release()
    return result


if __name__ == "__main__":
    sys.exit(main())
