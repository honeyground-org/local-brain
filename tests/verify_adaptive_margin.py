#!/usr/bin/env python3
"""★The margin learns per person★ — a Kalman filter that measures first, then adapts. (local · budget 0)

## What this holds down (§brain/adaptive.py) — synthetic observations on a temp home, never the author's

  ① it starts at the prior (1.35, "not known yet") and ★only measures★ for the first observations
  ② it converges on the person's own best margin when the evidence is consistent
  ③ ★a weak observation moves it less than a strong one★ — the shrinkage that keeps a small or flat
     sample from deciding the ruler on its own
  ④ the same evidence is not counted twice (or the filter grows sure of itself on repeated questions)
  ⑤ a label-free proxy sample may only argue upward
  ⑥ it stays inside 1.00–2.50 · a margin a human set wins · no sample keeps the prior
  ⑦ the best margin on a sample is found where the curve really peaks (ties → the middle)
  ⑧ control: the fixed 1.35 never follows a person whose best margin is 1.6

How to run:  PYTHONPATH=. python3 tests/verify_adaptive_margin.py
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-adaptive-")
_SAVED = {k: os.environ.get(k) for k in ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_CALIB_MARGIN")}
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ.pop("BRAIN_CALIB_MARGIN", None)
with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as _fh:
    json.dump({"sources": []}, _fh)

from brain import adaptive as ad, store  # noqa: E402

FAILS: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", name, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


_EV = [0]


def obs(z: float, sd: float = 0.05, kind: str = "labelled", evidence: str = "") -> dict:
    _EV[0] += 1
    return {"z": z, "R": max(sd, ad.OBS_SD_MIN) ** 2, "obs_sd": sd, "kind": kind,
            "evidence": evidence or "ev%d" % _EV[0]}


def fresh(db) -> None:
    store.set_meta(db, ad._META, "")


def main() -> int:
    db = store.connect()

    print("=" * 72 + "\n★① it starts at the prior and only measures at first★\n" + "=" * 72)
    fresh(db)
    s = ad.summary(db)
    check("starts at 1.35, not known yet", s["in_use"] == ad.PRIOR and s["sd"] == ad.PRIOR_SD, str(s))
    ad.update(db, obs(1.6))
    check("★one observation alone does not change the ruler★ — it measures first", ad.current(db) == ad.PRIOR,
          ad.summary(db)["mode"])
    check("the measure-only phase is more than one observation long", ad.WARMUP >= 2, "WARMUP=%d" % ad.WARMUP)
    for i in range(ad.WARMUP - 2):
        ad.update(db, obs(1.6))
    check("before %d observations it still uses the prior" % ad.WARMUP, ad.current(db) == ad.PRIOR,
          ad.summary(db)["mode"])
    ad.update(db, obs(1.6))
    s = ad.summary(db)
    check("after them it uses its estimate", abs(s["in_use"] - s["estimate"]) < 1e-3 and s["in_use"] > ad.PRIOR,
          "%s · %.3f" % (s["mode"], s["in_use"]))

    print("\n★② it converges on the person's own best★")
    fresh(db)
    rnd = random.Random(1)
    sds = []
    for _ in range(15):
        ad.update(db, obs(1.6 + rnd.gauss(0, 0.05), 0.08))
        sds.append(ad.summary(db)["sd"])
    s = ad.summary(db)
    check("noisy observations around 1.6 → an estimate near 1.6", abs(s["estimate"] - 1.6) < 0.06,
          "%.3f ± %.3f" % (s["estimate"], s["sd"]))
    check("its uncertainty shrinks as evidence accumulates", sds[-1] < sds[0], "%.3f → %.3f" % (sds[0], sds[-1]))

    print("\n★③ a weak observation moves it less than a strong one★")
    fresh(db)
    ad.update(db, obs(1.8, 0.5))
    weak = ad.state(db)["x"] - ad.PRIOR
    fresh(db)
    ad.update(db, obs(1.8, 0.05))
    strong = ad.state(db)["x"] - ad.PRIOR
    check("a flat / small-sample observation barely moves it", 0 < weak < strong / 3,
          "weak +%.3f · strong +%.3f" % (weak, strong))

    print("\n★④ the same evidence is not counted twice★")
    fresh(db)
    ad.update(db, obs(1.6, evidence="same"))
    n1 = ad.state(db)["n"]
    ad.update(db, obs(1.6, evidence="same"))
    s = ad.state(db)
    check("a repeat is recorded as skipped, not as a second observation",
          s["n"] == n1 and "same evidence" in (s["history"][-1].get("skipped") or ""), str(s["history"][-1])[:90])

    print("\n★⑤ a proxy sample may only argue upward★")
    fresh(db)
    ad.update(db, obs(1.1, kind="proxy"))
    check("a proxy observation below the estimate is not used", ad.state(db)["x"] == ad.PRIOR)
    ad.update(db, obs(1.6, kind="proxy"))
    check("one above it is", ad.state(db)["x"] > ad.PRIOR, "%.3f" % ad.state(db)["x"])

    print("\n★⑥ bounds · a human's margin · no sample★")
    fresh(db)
    for _ in range(5):
        ad.update(db, obs(9.0, 0.05))
    check("it never leaves 1.00–2.50", ad.LO <= ad.state(db)["x"] <= ad.HI, "%.3f" % ad.state(db)["x"])
    os.environ["BRAIN_CALIB_MARGIN"] = "1.5"
    s = ad.summary(db)
    check("BRAIN_CALIB_MARGIN wins and the filter does not deploy",
          s["in_use"] == 1.5 and "fixed" in s["mode"], str(s["mode"]))
    os.environ.pop("BRAIN_CALIB_MARGIN", None)
    fresh(db)
    ad.update(db, None)
    s = ad.state(db)
    check("no sample → skipped, the prior kept", s["x"] == ad.PRIOR and s["n"] == 0
          and "no sample" in s["history"][-1]["skipped"])

    print("\n★⑥-b a process told not to learn does not teach the filter★")
    fresh(db)
    os.environ["BRAIN_ADAPTIVE_LEARN"] = "0"
    before = store.get_meta(db, ad._META, "")
    ad.step(db, 7.0)
    check("BRAIN_ADAPTIVE_LEARN=0 → the stored state is untouched (a test run must not teach it)",
          store.get_meta(db, ad._META, "") == before)
    os.environ.pop("BRAIN_ADAPTIVE_LEARN", None)

    print("\n★⑦ the best margin is found where the curve peaks★")
    j16 = ad.GRID.index(1.6)
    # hits rise until 1.6 and fall after it; the control group fires below 1.6
    H = [[1 if (j <= j16 + (i % 3) - 1) else 0 for j in range(len(ad.GRID))] for i in range(40)]
    F = [[1 if j < j16 - 2 else 0 for j in range(len(ad.GRID))] for _ in range(20)]
    best = ad._best(H, F, range(len(H)), range(len(F)))
    check("a peaked curve → its peak", abs(best - 1.6) <= ad.STEP + 1e-9, "%.2f" % best)
    flat_h = [[1] * len(ad.GRID) for _ in range(10)]
    flat_f = [[0] * len(ad.GRID) for _ in range(10)]
    check("a flat curve → the middle of the tie", ad._best(flat_h, flat_f, range(10), range(10))
          == ad.GRID[len(ad.GRID) // 2])

    print("\n★⑨ an observation counts for what it adds — its novelty★")
    # the morning of 2026-10-07: every session start calibrated, the corpus had changed by a note, so each
    # observation had new evidence — but the same 91 questions answered the same way
    base = {"A%02d" % i: "1" * 10 + "0" * 21 for i in range(57)}
    base.update({"C%02d" % i: "1" * 4 + "0" * 27 for i in range(34)})
    fresh(db)
    for _ in range(3):
        o = obs(1.30, 0.13)
        o["rows"] = dict(base)
        ad.update(db, o)
    s = ad.state(db)
    n_eff = s.get("n_eff", s.get("n", 0))
    check("the same answers three times are one observation's worth", abs(n_eff - 1.0) < 1e-9
          and ad.current(db) == ad.PRIOR, "Σν %.2f · %s" % (n_eff, ad.summary(db)["mode"]))
    check("the repeats are recorded as skipped, with the reason",
          "nothing new" in (s["history"][-1].get("skipped") or ""), str(s["history"][-1].get("skipped")))
    sd_after_repeats = ad.summary(db)["sd"]
    fresh(db)
    for _ in range(3):
        ad.update(db, obs(1.30, 0.13))             # control: no rows → each one counted in full (the old rule)
    check("control: counted in full, the same three would have collapsed the uncertainty and deployed",
          ad.summary(db)["sd"] < sd_after_repeats - 0.02 and ad.state(db).get("n_eff", ad.state(db)["n"]) >= ad.WARMUP,
          "sd %.3f (full) vs %.3f (novelty)" % (ad.summary(db)["sd"], sd_after_repeats))
    fresh(db)
    o = obs(1.30, 0.10)
    o["rows"] = dict(base)
    ad.update(db, o)
    moved = dict(base)
    for i in range(0, 57, 2):
        moved["A%02d" % i] = "1" * 12 + "0" * 19   # half the questions now answer differently
    o = obs(1.50, 0.10)
    o["rows"] = moved
    x1 = ad.state(db)["x"]
    ad.update(db, o)
    s = ad.state(db)
    nu = s["history"][-1].get("novelty", 1.0)
    n_eff = s.get("n_eff", s.get("n", 0))
    check("a partly new observation counts as its changed share", abs(nu - 29 / 91.0) < 1e-3
          and abs(n_eff - (1 + 29 / 91.0)) < 1e-3, "ν %.3f · Σν %.3f" % (nu, n_eff))
    full = ad.state(db)
    fresh(db)
    o = obs(1.30, 0.10)
    o["rows"] = dict(base)
    ad.update(db, o)
    ad.update(db, obs(1.50, 0.10))                  # the same move, counted in full
    check("…and moves the estimate less than a fully new one would",
          0 < full["x"] - x1 < ad.state(db)["x"] - x1,
          "+%.3f vs +%.3f" % (full["x"] - x1, ad.state(db)["x"] - x1))
    store.set_meta(db, ad._META, json.dumps({"x": 1.31, "P": 0.0047, "n": 5, "tries": 9, "history": []}))
    s = ad.summary(db)
    check("a state from before novelty starts over from the prior — its count over-counted",
          ad.current(db) == ad.PRIOR and s["estimate"] == ad.PRIOR and s["sd"] == ad.PRIOR_SD,
          "%s · %.3f ± %.3f" % (s["mode"], s["estimate"], s["sd"]))

    print("\n★⑧ control — the fixed 1.35★")
    fresh(db)
    for _ in range(10):
        ad.update(db, obs(1.6, 0.05))
    check("the filter follows a person whose best is 1.6", abs(ad.current(db) - 1.6) < 0.05,
          "%.3f" % ad.current(db))
    check("⛔ the fixed margin it replaced would have stayed 1.35 — 0.25 away", abs(1.35 - 1.6) > 0.2)

    db.close()
    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAILS), " · ".join(FAILS)) if FAILS
          else "✅ all passed — the margin measures first, then adapts")
    return 1 if FAILS else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for k, v in _SAVED.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
