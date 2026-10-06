"""The margin over the noise floor, ★learned per person★ — a one-dimensional Kalman filter.

## Why (user instruction, 2026-10-06)

*"Can't the 1.35 be adjusted dynamically? At first, only measure which value would be right — then
start from an initial setting and find the best value adaptively, like a Kalman filter."*

The threshold is `noise floor × margin`. The floor has always been measured on each person's own corpus;
the margin (1.35) was chosen once, on the author's notes. Re-fitting it naively — sweep, keep the best —
is choosing a threshold with the very sample it is then judged on, which this repository has already
refused as overfitting (§calibrate._calibrate_locked). A filter does something different: it ★weighs★
each measurement by how much that measurement can be trusted, so a small or flat sample barely moves it
and only a consistent, sharp signal does. That shrinkage toward what is already known is what keeps a
30-question sample from deciding the ruler on its own.

## How

  state     x = the margin estimate · P = its variance. Starts at the prior: 1.35 ± 0.25 ("not known yet").
  observe   each calibration, every margin 1.00–2.50 is scored on the person's sample with the same
            arithmetic the guard uses — then the sample is ★resampled 200 times (bootstrap)★ and the best
            margin found in each. z = the median of those, R = their variance: a best margin that jumps
            around between resamples is a weak observation and barely moves the filter.
            ⛔ Measured on the author's notes (2026-10-06): the first version scored ★one half★ of the sample
            and took the middle of the "within one hit of the best" plateau. 28 questions made the curve
            flat, the plateau ran down towards the low margins (false fires rise slowly below the best,
            hits fall off a cliff above it), and the filter settled at 1.24 — one more false fire and no
            more hits than 1.35 on the full sample. The bootstrap median said 1.30 (± 0.15), where the
            full sample's own best is (1.30–1.35).
            ⛔ ★The same evidence is not new evidence★ — an observation is used only when the sample or
            the corpus changed since the last one; otherwise the filter would grow sure of itself by
            re-reading the same questions.
  predict   P += DRIFT² — the corpus grows, so yesterday's best may move a little.
  update    K = P / (P + R) · x += K·(z − x) · P = (1 − K)·P.
  deploy    ★only after WARMUP observations★ — until then it measures and records, and the ruler keeps
            using the prior. After that, the threshold uses x — and the regression guard still refuses
            any threshold that does worse on the full sample than the one in place.

  ⛔ A label-free proxy sample is trusted only upward (measured: it agrees with gold above the incumbent,
     not below — §proxy.may_judge). So with a proxy, an observation that would lower the margin is not used.
  ⛔ No sample at all → no observation → the prior stays, and the state says so.
  ⛔ `BRAIN_CALIB_MARGIN` set → a human chose the margin. The filter still observes (so the evidence is
     visible) but never deploys.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import statistics
import time
from typing import Optional

PRIOR = 1.35            # the reference corpus's value (§calibrate.MARGIN)
PRIOR_SD = 0.25         # how unsure that prior is on a corpus it was not measured on
OBS_SD_MIN = 0.05       # no single observation is trusted more than this (the grid step)
DRIFT_SD = 0.02         # how far the best margin may move between two calibrations
WARMUP = 3              # observations before the estimate is used
LO, HI, STEP = 1.00, 2.50, 0.05
GRID = [round(LO + STEP * i, 2) for i in range(int(round((HI - LO) / STEP)) + 1)]
HISTORY_KEEP = 30
BOOTSTRAP = 200
_META = "margin_filter"


def state(db: sqlite3.Connection) -> dict:
    from brain import store
    try:
        s = json.loads(store.get_meta(db, _META, "") or "{}")
    except (ValueError, TypeError):
        s = {}
    if not isinstance(s, dict) or "x" not in s:
        s = {"x": PRIOR, "P": PRIOR_SD ** 2, "n": 0, "tries": 0, "history": []}
    return s


def human_margin() -> Optional[float]:
    raw = os.environ.get("BRAIN_CALIB_MARGIN", "")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def deployed(s: dict) -> bool:
    """Past the measure-only phase. (A human's margin still wins in `current` — it is checked there.)"""
    return int(s.get("n", 0)) >= WARMUP


def current(db: sqlite3.Connection) -> float:
    """The margin the ruler should use right now."""
    h = human_margin()
    if h is not None:
        return h
    s = state(db)
    return float(s["x"]) if deployed(s) else PRIOR


def summary(db: sqlite3.Connection) -> dict:
    s = state(db)
    last = (s.get("history") or [None])[-1]
    mode = ("fixed by BRAIN_CALIB_MARGIN" if human_margin() is not None
            else "adaptive" if deployed(s) else "measuring only (%d of %d observations)" % (s["n"], WARMUP))
    return {"in_use": current(db), "estimate": round(float(s["x"]), 3),
            "sd": round(float(s["P"]) ** 0.5, 3), "observations": int(s["n"]), "mode": mode,
            "last": last}


def evidence_id(db: sqlite3.Connection, pairs: list, ctrl: list, kind: str) -> str:
    """What this observation is made of — the sample's questions and the corpus size."""
    from brain import store
    h = hashlib.sha1(kind.encode())
    for q, g in pairs:
        h.update(("A\0%s\0%s\n" % (q, g)).encode("utf-8", "replace"))
    for q in ctrl:
        h.update(("C\0%s\n" % q).encode("utf-8", "replace"))
    h.update(str(store.corpus_stats(db)["docs"]).encode())
    return h.hexdigest()[:16]


def _best(H: list, F: list, ia, ic) -> float:
    """The best margin on one (re)sample — the middle of the margins that tie at the top."""
    na, nc = float(len(ia)), float(len(ic))
    autos = []
    for j in range(len(GRID)):
        hit = sum(H[i][j] for i in ia)
        ff = sum(F[i][j] for i in ic)
        autos.append((hit / na) * (1.0 - ff / nc))
    top = max(autos)
    tied = [GRID[j] for j, a in enumerate(autos) if a >= top - 1e-12]
    return tied[len(tied) // 2]


def observe(db: sqlite3.Connection, floor: float, pairs: list, ctrl: list, kind: str) -> Optional[dict]:
    """One measurement of the best margin, with its uncertainty · None when there is nothing to learn from."""
    from brain import calibrate, evalinit as ei, hook, search
    if kind not in ("labelled", "proxy") or floor <= 0 or not pairs or not ctrl:
        return None
    ths = [max(calibrate.FLOOR_MIN, round(floor * m, 2)) for m in GRID]
    # ★each query is recalled once★ — only the threshold changes along the grid (as in §calibrate._bench)
    H = []
    for q, g in pairs:
        rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
        H.append([1 if any(ei.gold_hit(g, r["name"]) for r in hook.select(rows, t)) else 0 for t in ths])
    F = []
    for q in ctrl:
        rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
        F.append([1 if hook.select(rows, t) else 0 for t in ths])
    if not any(any(row) for row in H):
        return None                                   # nothing hits at any margin — no information
    eid = evidence_id(db, pairs, ctrl, kind)
    rnd = random.Random(eid)                          # reproducible: the same evidence, the same answer
    na, nc = len(pairs), len(ctrl)
    zs = [_best(H, F, [rnd.randrange(na) for _ in range(na)], [rnd.randrange(nc) for _ in range(nc)])
          for _ in range(BOOTSTRAP)]
    sd = statistics.pstdev(zs)
    return {"z": round(statistics.median(zs), 3), "R": max(sd, OBS_SD_MIN) ** 2,
            "obs_sd": round(sd, 3), "full_best": _best(H, F, range(na), range(nc)),
            "n_a": na, "n_c": nc, "kind": kind, "evidence": eid}


def update(db: sqlite3.Connection, obs: Optional[dict]) -> dict:
    """One Kalman step (or a recorded skip) · returns the new state."""
    from brain import store
    s = state(db)
    s["tries"] = int(s.get("tries", 0)) + 1
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    x, P = float(s["x"]), float(s["P"])
    if obs is None:
        entry["skipped"] = "no sample to learn from"
    elif obs.get("evidence") and obs["evidence"] == s.get("last_evidence"):
        entry.update(obs, skipped="the same evidence as the last observation")
    elif obs["kind"] == "proxy" and obs["z"] < x:
        entry.update(obs, skipped="a proxy sample may only argue upward")
    else:
        P += DRIFT_SD ** 2
        K = P / (P + obs["R"])
        x = min(HI, max(LO, x + K * (obs["z"] - x)))
        P = (1.0 - K) * P
        s["x"], s["P"], s["n"] = round(x, 4), P, int(s.get("n", 0)) + 1
        s["last_evidence"] = obs.get("evidence")
        entry.update(obs, K=round(K, 3), x=round(x, 3), sd=round(P ** 0.5, 3))
    s["history"] = (list(s.get("history") or []) + [entry])[-HISTORY_KEEP:]
    store.set_meta(db, _META, json.dumps(s, ensure_ascii=False))
    return s


def step(db: sqlite3.Connection, floor: float) -> dict:
    """Observe, then update. Never raises.

    ⛔ `BRAIN_ADAPTIVE_LEARN=0` → this process reads the filter but never teaches it (measured 2026-10-06: a
       test run calibrating the real index with its own forced and deliberately broken samples left six
       observations in the live filter, deployed it, and would have raised the next threshold to ~10.05).
    """
    from brain import calibrate
    if os.environ.get("BRAIN_ADAPTIVE_LEARN", "") == "0":
        return summary(db)
    try:
        pairs, ctrl, kind = calibrate.bench_sample(db)
        if kind in ("labelled", "proxy") and evidence_id(db, pairs, ctrl, kind) == state(db).get("last_evidence"):
            # nothing new to learn — skipped before a single recall is spent
            update(db, {"evidence": state(db)["last_evidence"], "kind": kind, "z": None})
        else:
            update(db, observe(db, floor, pairs, ctrl, kind))
    except Exception:                                    # noqa: BLE001
        pass                                             # a failed observation must not block calibration
    return summary(db)
