"""★Ask the judge only when it can answer in the time left★ — decided from this installation's own records (2026-10-14).

Why this exists
---------------
The hook's second stage asks a remote judge inside the time the host allows (about 4.5 s after the hook
starts). On 2026-10-14 the free judge model was overloaded: 5–9 s answers, then HTTP 503 "high demand". From
one evening on, 15 of 16 second-stage runs on the author's machine were cut while the request was out — each one
spending a call of the day's budget, holding the prompt for the full 4.5 s, and attaching nothing.

This module decides, before a fresh request, whether to send it — from what the records (§observe) say about the
last requests this installation sent, not from a number tuned to anyone:

- ★The provider looks unwell★ when the last request sent was cut before it answered, or refused for a reason
  worth asking again later (§rerank.TRANSIENT). Then it is ★not asked★ — except for one probe after a backoff
  that doubles with every failure in a row: after n failures in a row, 2^(n-1) - 1 chances are skipped, then
  one request goes out (the classic exponential backoff, counted in chances because prompts come minutes
  apart, so a backoff in seconds would skip nothing). A `Retry-After` the provider gave is waited out too. One
  probe at a time across processes (a lock made with O_EXCL, stale after the request's own timeout).
- ★The provider looks well★ but its usual answer is slower than the time left: when the median of the
  requests that answered today and yesterday exceeds the time left, it is not asked.
- No record to go on → it is asked (and the answer becomes the record).

A request it lets through is one the hook would have made anyway; one it holds back is one that would have been
cut. Measurements that ask the judge directly (`rerank.score`, the experiment harness) are never gated — only
the hook passes a deadline.
"""
from __future__ import annotations

import calendar
import os
import time
from typing import List, Optional, Tuple

PROBE_LOCK = "judge-probe.lock"


def _epoch(ts: str) -> float:
    try:
        return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, TypeError):
        return 0.0


def attempts(now: Optional[float] = None) -> List[dict]:
    """The second-stage runs of today and yesterday (UTC) that reached the gate or sent a request, oldest first:
    {t, sent, returned, secs, waited, kind, retry_after, cached, skipped}."""
    from brain import observe
    now = now if now is not None else time.time()
    rows, _ = observe.read("hook", sorted({observe.day(now - 86400), observe.day(now)}))
    out = []
    for r in rows:
        j = r.get("judge") or {}
        gate = j.get("gate") or {}
        if not (j.get("sent") or gate.get("ask") is False):
            continue
        out.append({"t": _epoch(r.get("ts", "")), "sent": bool(j.get("sent")),
                    "returned": bool(j.get("returned")), "secs": j.get("secs"), "waited": j.get("waited"),
                    "kind": j.get("kind"), "retry_after": j.get("retry_after"), "cached": bool(j.get("cached")),
                    "skipped": gate.get("ask") is False})
    return sorted(out, key=lambda a: a["t"])


def _unwell(a: dict) -> bool:
    from brain import rerank
    return a["sent"] and (not a["returned"] or (a["kind"] in rerank.TRANSIENT))


def _lock_path() -> str:
    from brain import store
    return os.path.join(store.brain_home(), PROBE_LOCK)


def _take_probe() -> bool:
    from brain import rerank
    p = _lock_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)   # a missing home is not "a probe in flight"
    except OSError:
        return False
    for _ in range(2):
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(p) < rerank.TIMEOUT:
                    return False
                os.remove(p)                             # a probe that never came back
            except OSError:
                return False
        except OSError:
            return False
    return False


def release_probe() -> None:
    try:
        os.remove(_lock_path())
    except OSError:
        pass


def decide(remaining: float, now: Optional[float] = None,
           history: Optional[List[dict]] = None) -> Tuple[bool, str, bool]:
    """(ask, why, probe) for one fresh request with `remaining` seconds left before the hook's wall."""
    now = now if now is not None else time.time()
    hist = history if history is not None else attempts(now)
    sent = [a for a in hist if a["sent"] and not a["cached"]]
    if not sent:
        return True, "no_history", False
    last = sent[-1]
    if _unwell(last):
        streak = 0
        for a in reversed(sent):
            if not _unwell(a):
                break
            streak += 1
        skipped = sum(1 for a in hist if a["skipped"] and a["t"] >= last["t"])
        if last.get("retry_after") and now - last["t"] < float(last["retry_after"]):
            return False, "provider_said_wait", False
        if skipped < 2 ** (streak - 1) - 1:
            return False, "provider_unwell", False
        if not _take_probe():
            return False, "probe_in_flight", False
        return True, "probe", True
    done = sorted(a["secs"] for a in sent if a["returned"] and not a["kind"] and a["secs"] is not None)
    if done and done[len(done) // 2] > remaining:
        return False, "slower_than_time_left", False
    return True, "ok", False
