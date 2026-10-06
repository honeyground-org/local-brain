"""★A budget is a wall in elapsed time, and every wait answers to it★ (2026-09-29).

## Why this exists — the same defect, found twice on one morning

Two places in this repository declared a time budget and did not keep it, in the same way:

  · `jobs._await_network` declared ★180s★ and took ★627s★ (09-29 09:34, both daily jobs). Its loop
    checked the budget only ★between★ attempts, and the time went ★inside★ them: 7 attempts, 60s of
    sleep, and ~81s per `socket.getaddrinfo`. No socket timeout covers that call — resolution happens
    before any socket exists.
  · the recall hook is installed with a ★5s★ limit, and the host ★killed it 123 times★ (transcripts
    08-26 → 09-29: `hook_cancelled · timedOut · timeoutMs 5000`, in bursts — 60 clusters, the largest
    16 kills in 40 minutes). Replaying the same prompts later, every one finished inside 2.7s — so
    the cause was the moment, not the prompt. The hook's own waits: a SQLite lock up to 15s, an
    embedding call up to 60s ×2, a judge call up to 25s, DNS unbounded.

In both, ★the unit the guard measured (sleep time, one call's timeout) was not the unit it meant to
bound (total elapsed)★. Per-call timeouts do not add up to a wall, and a lookup has none at all.

## So the two tools here are both about total elapsed

    resolve(host, timeout)   a lookup that returns inside `timeout` or raises, whatever the resolver does
    watchdog(seconds, fn)    at the wall: call fn, then end the process — whatever the main thread is
                             blocked in (a lookup, a socket read, a SQLite busy-wait)

⛔ A watchdog ★thread★, not a signal. A Python signal handler runs only when the main thread returns
   to the interpreter, and a C call stuck in the resolver never returns. A thread gets the GIL
   because every one of those waits releases it.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
from typing import Callable, Optional


def resolve(host: str, port: int = 443, timeout: float = 5.0):
    """`socket.getaddrinfo`, bounded. Raises `TimeoutError` (an `OSError`) past `timeout`.

    The lookup runs on a daemon thread that is ★abandoned★, not cancelled, when time runs out —
    nothing can cancel a resolver call. It ends when the resolver answers or the process exits.
    ⚠️ `socket.getaddrinfo` is looked up at call time, so a check can still inject a fault.
    """
    box: dict = {}

    def run() -> None:
        try:
            box["ok"] = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            box["err"] = exc

    t = threading.Thread(target=run, name="brain-resolve", daemon=True)
    t.start()
    t.join(max(0.0, timeout))
    if t.is_alive():
        raise TimeoutError("resolving %s took longer than %.1fs" % (host, timeout))
    if "err" in box:
        raise box["err"]
    return box["ok"]


class Watchdog:
    """Fires `on_expire` once at the wall, then ends the process with exit 0.

    `claim()` is the one door to the output: the main thread calls it before writing its answer, the
    watchdog calls it before writing what was ready. Whoever gets there first writes; the other
    stays quiet — so the host never reads two answers glued together.
    """

    def __init__(self, seconds: float, on_expire: Callable[[], None]):
        self._lock = threading.Lock()
        self._claimed = False
        self.fired = False
        self._on_expire = on_expire
        self._timer = threading.Timer(max(0.0, seconds), self._fire)
        self._timer.daemon = True

    def start(self) -> "Watchdog":
        self._timer.start()
        return self

    def claim(self) -> bool:
        with self._lock:
            if self._claimed:
                return False
            self._claimed = True
            return True

    def cancel(self) -> None:
        self._timer.cancel()

    def _fire(self) -> None:
        if not self.claim():
            return                                       # the main thread is already answering
        self.fired = True
        try:
            self._on_expire()
        except Exception:                                # noqa: BLE001
            pass                                         # ⛔ a failure here must not keep us alive
        finally:
            try:
                sys.stdout.flush()                       # ⛔ `os._exit` does not flush buffers
            except Exception:                            # noqa: BLE001
                pass
            os._exit(0)


def watchdog(seconds: float, on_expire: Callable[[], None]) -> Optional[Watchdog]:
    """Start a `Watchdog`. `seconds <= 0` means "already past the wall" — it fires at once."""
    return Watchdog(seconds, on_expire).start()
