"""★What happened, per request★ — the record the monitor reads (2026-10-14).

Why this exists
---------------
On 2026-10-14 the hook's own timing line could not say where its second stage was cut (embedding, the vector
store or the judge looked the same), which database actually answered (a chosen store that is down or out of
sync silently falls back to the local copy), or how many prompts never reached it at all (a short prompt or a
host command returned before anything was written). And MCP `recall` wrote nothing, so whether an agent ever
asked for what meaning finds (`semantic_k`) could not be told. The monitor that is meant to say whether words
and meaning are really used together needs those facts first.

Rules
-----
- ⛔ ★No text★ — no prompt, no document, no raw error message. Counts, durations, names of models and
  backends, and short fixed codes (`overloaded`, `not_synced`, `NoKey`) only.
- One file per kind per UTC day (`observe/<kind>-YYYY-MM-DD.jsonl` under the brain's home), ★appended
  only★: each row is one `os.write` on a file opened for appending (on Windows, under a lock — §_write_locked),
  so rows from processes writing at once do not interleave, and nothing is ever rewritten in place — a size
  cap that drops the older half (the old timing file) loses rows when two processes trim at once. Old days
  are pruned by whoever summarises them.
- Every row carries `schema`, a random `run` id, the UTC time and the measuring code's generation, so rows
  from another version of the code are never mixed in silently.
- A record must never cost the answer: every failure here is swallowed.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Iterator, List, Optional, Tuple

SCHEMA = 1
DIR = "observe"


def _home() -> str:
    from brain import store
    return os.path.join(store.brain_home(), DIR)


def day(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts if ts is not None else time.time()))


def path(kind: str, on: Optional[str] = None) -> str:
    return os.path.join(_home(), "%s-%s.jsonl" % (kind, on or day()))


def new_run() -> str:
    return uuid.uuid4().hex[:12]


def write(kind: str, row: dict, to: Optional[str] = None) -> bool:
    """Append one row. Returns whether it was written (a caller never needs to look)."""
    try:
        from brain import calibrate
        row = dict(row)                                  # the caller's dict is left as it was
        out = {"schema": SCHEMA, "run": row.pop("run", None) or new_run(),
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "gen": calibrate.CODE_GENERATION}
        out.update(row)
        line = (json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        p = to or path(kind)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.name == "nt":
            return _write_locked(p, line)
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)                           # ★one write per row★ — see the module docstring
        finally:
            os.close(fd)
        return True
    except Exception:                                    # noqa: BLE001
        return False


# ⛔ ★Windows does not append atomically★ (CI, 2026-10-14: six processes at once left 1,557 of 1,800 rows
#    readable and 21 broken). Its C runtime emulates O_APPEND as "seek to the end, then write", so two
#    processes can land on the same offset. There, a row is written under a lock on one byte past any size a
#    record file reaches — beyond the end, so a reader is never blocked by it.
_LOCK_AT = 2 ** 31 - 2


def _write_locked(p: str, line: bytes) -> bool:
    import msvcrt
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.lseek(fd, _LOCK_AT, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)           # waits (it retries for about ten seconds)
        try:
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, line)
        finally:
            os.lseek(fd, _LOCK_AT, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return True
    finally:
        os.close(fd)


def read(kind: str, days: List[str]) -> Tuple[List[dict], int]:
    """Rows of `kind` on these UTC days, and how many lines could not be read (counted, never guessed)."""
    rows: List[dict] = []
    broken = 0
    for d in days:
        for line in _lines(path(kind, d)):
            try:
                rows.append(json.loads(line))
            except ValueError:
                broken += 1
    return rows, broken


def _lines(p: str) -> Iterator[str]:
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    yield line
    except OSError:
        return


def code(exc: BaseException) -> str:
    """A fixed short code for an exception — its type's name, never its message."""
    return type(exc).__name__
