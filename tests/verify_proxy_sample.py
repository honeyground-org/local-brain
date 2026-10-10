#!/usr/bin/env python3
"""★Does the label-free sample reach the same verdict as real gold★ — and only where it was measured to. (local · budget 0)

## Why this exists

`brain/proxy.py` lets the calibration guard work on a machine that has no labelled sample, by
cutting (query → document) pairs out of the user's own corpus. Everything about that idea rests on
★one empirical claim★: that on the comparison the guard actually makes, the proxy decides the same
way real gold does. A claim like that must be held down by an instrument, or it quietly rots into
"we assumed it was fine".

So this check re-measures the claim, on the one machine that has ★both★ samples.

## What is measured

  ① the sample is ★frozen★ — the same corpus gives the same pairs, run after run
     (`tests/_sample.py` is the record of what a redrawn sample does: 68% → 73% → 71% in one day)
  ② the pairs are not ★trivial★ — a query never contains the words of its own document's name
  ③ ⛔ ★verdict agreement★ with real gold, split by direction:
        upward   (candidate above the incumbent)  — the direction the guard is allowed to judge
        downward (candidate below the incumbent)  — recorded, and precisely why it must not judge there
  ④ the restriction in `proxy.may_judge` matches what ③ found
  ⑤ ★a control group★ — a deliberately broken proxy must ★fail★ ③, or ③ proves nothing

⛔ ③ needs this person's labelled sample. On a machine without one it reports ★skipped★, never
   "passed" — an unmeasured agreement is not an agreement.

How to run:  PYTHONPATH=. python3 tests/verify_proxy_sample.py
"""
from __future__ import annotations

import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# ⛔ This check calibrates ★the real index★ with forced or broken samples — it must never teach the live
#    margin filter (§brain/adaptive.step). Inherited by every child process it starts.
os.environ["BRAIN_ADAPTIVE_LEARN"] = "0"

from brain import calibrate, evalinit as ei, proxy, store  # noqa: E402
from tests import _needs  # noqa: E402

# The grid the verdicts are compared on. Spread around where a real threshold lives (9-ish here) so
# the comparison is about decisions that can actually happen, not about arithmetic extremes.
GRID = [6.0, 7.0, 8.0, 9.2, 9.45, 10.5, 12.15, 14.0, 16.0]

# Incumbents to test from. ⛔ Kept separate from the grid: the guard always compares "a candidate
# against ★the value already deployed★", never two arbitrary numbers.
#
# ⛔ ★These must span the peak of the real curve★ (fixed 2026-09-09, caught by ⑤ below). The first
#    version listed only 8.0 and up, where real gold says "keep the incumbent" for ★every★ upward
#    candidate. Any sample whose curve merely decreases then agrees 18/18 — and a proxy with
#    ★shuffled answers★ scored exactly that. The test was vacuous: it measured the shape of a curve,
#    not whether the answers were right. Below the peak gold does say "take the higher one", and
#    those are the rows that can tell a real sample from a broken one.
INCUMBENTS = [6.0, 7.0, 8.0, 9.2, 9.45, 10.5]

# The contract. `REJECT` is measured only over the comparisons `proxy.may_judge` actually allows —
# judging something it stands down on would be measuring a decision nobody makes. `STANDDOWN` is the
# anti-ratchet row: of the rises real gold ★wants★, how many does the proxy leave alone.
REJECT_FLOOR, STANDDOWN_FLOOR = 0.90, 0.90

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def agreement(db, gold_pairs, gold_ctrl, pairs, ctrl, real_tops):
    """What the proxy would actually do, against what gold wants · counts as (ok, n).

      judged   — comparisons `may_judge` allows: does the proxy keep the incumbent exactly when gold does
      standdown— rises gold ★wants accepted★: does the proxy leave them alone (the anti-ratchet row)
      down     — downward comparisons, reported to show ★why★ they are never judged
    """
    from brain import proxy as px
    ra = calibrate._bench(db, GRID, gold_pairs, gold_ctrl)
    rb = calibrate._bench(db, GRID, pairs, ctrl)
    out = {"judged": [0, 0], "standdown": [0, 0], "down": [0, 0]}
    for inc in INCUMBENTS:
        fire = calibrate.fire_rate_at(real_tops, inc)
        for cand in GRID:
            if abs(cand - inc) < 1e-9:
                continue
            gold_keeps = ra[inc]["auto"] >= ra[cand]["auto"]
            proxy_keeps = rb[inc]["auto"] >= rb[cand]["auto"]
            allowed = px.may_judge(inc, cand, fire)
            if cand < inc:
                out["down"][1] += 1
                out["down"][0] += (gold_keeps == proxy_keeps)
                continue
            if allowed:
                out["judged"][1] += 1
                out["judged"][0] += (gold_keeps == proxy_keeps)
            if not gold_keeps:                        # gold wants this rise taken
                out["standdown"][1] += 1
                out["standdown"][0] += (not allowed) or proxy_keeps is False
    return {k: tuple(v) for k, v in out.items()}


def main() -> int:
    db = store.connect()
    _needs.proxy_sample(db)
    print("=" * 78)
    print("the label-free sample — is its verdict the same one real gold reaches")
    print("=" * 78)

    # ── ① frozen ─────────────────────────────────────────────────────────────
    first = proxy.positives(db)
    second = proxy.positives(db)
    check("the sample is frozen (two reads, same pairs)", first == second and len(first) > 0,
          "%d pairs" % len(first))
    # ⛔ ★Determinism is "the same corpus gives the same draw", not "the draw never changes"★
    #    (fixed 2026-09-10). The first version compared a fresh `build()` against the ★stored★
    #    sample and demanded they match — which only holds while the corpus stands still. It went
    #    red the moment the corpus grew by 0.9%, reporting a defect where the freeze was in fact
    #    working exactly as designed. The two claims are separate and both are checked here.
    check("★deterministic★ — two draws from the same corpus are identical (no hidden randomness)",
          proxy.build(db) == proxy.build(db), "%d pairs each" % len(proxy.build(db)))
    stored_at = db.execute("SELECT built_docs FROM proxy_sample LIMIT 1").fetchone()
    now_docs = store.corpus_stats(db)["docs"]
    was = int(stored_at["built_docs"]) if stored_at else now_docs
    drift = abs(now_docs - was) / float(max(1, was))
    if drift < proxy.REBUILD_RATIO:
        check("★the freeze survives corpus drift★ — it is not redrawn on every new document",
              first == proxy.positives(db),
              "built at %d docs · now %d (%.1f%% drift · rebuild at %.0f%%)"
              % (was, now_docs, drift * 100, proxy.REBUILD_RATIO * 100))
    else:
        print("  ⏭  corpus moved %.0f%% (past the %.0f%% rebuild line) — the sample is meant to be "
              "redrawn here, so the freeze row does not apply"
              % (drift * 100, proxy.REBUILD_RATIO * 100))
    ctrl = proxy.controls(db)
    check("controls are frozen too", ctrl == proxy.controls(db), "%d controls" % len(ctrl))
    check("the sample is big enough for the guard's own minimum",
          len(first) >= proxy.MIN_A and len(ctrl) >= calibrate.MIN_C,
          "%d pairs (need %d) · %d controls (need %d)"
          % (len(first), proxy.MIN_A, len(ctrl), calibrate.MIN_C))

    # ── ② not trivial ────────────────────────────────────────────────────────
    from brain import textindex
    leaked = []
    for q, gold in first:
        own = {w.lower() for w in textindex.words(gold.replace("_", " "))}
        if own & {w.lower() for w in textindex.words(q)}:
            leaked.append((q, gold))
    check("no query contains a word of its own document's name (it would be a lookup, not a question)",
          not leaked, "%d leaked" % len(leaked) if leaked else "checked %d" % len(first))

    # ── ③ verdict agreement with real gold ───────────────────────────────────
    print("\n★agreement with this person's labelled gold")
    A, C = ei.load_A(), ei.load_C()
    if len(A) < calibrate.MIN_A or len(C) < calibrate.MIN_C:
        print("⏭  skipped — no labelled sample on this machine (an unmeasured agreement is not an agreement)")
        print("   build one with `bin/brain eval-init`, then this row measures the claim in brain/proxy.py")
    else:
        m = calibrate.measure(db)                 # one pass — also gives every real prompt's top score
        real_tops = m.get("_real_tops") or []
        print("   (real prompts %d · healthy firing band %s)" % (len(real_tops), str(calibrate.FIRE_RATE_OK)))
        g = agreement(db, A, C, first, ctrl, real_tops)
        jd, sd, dn = g["judged"], g["standdown"], g["down"]
        j_rate = jd[0] / float(jd[1] or 1)
        s_rate = sd[0] / float(sd[1] or 1)
        check("★where it is allowed to judge, it decides what gold decides★ (the incident this guard exists for)",
              jd[1] > 0 and j_rate >= REJECT_FLOOR,
              "%d/%d = %.0f%% (floor %.0f%%)" % (jd + (j_rate * 100, REJECT_FLOOR * 100)))
        check("★it stands down on the rises gold wants★ — no one-way ratchet",
              sd[1] > 0 and s_rate >= STANDDOWN_FLOOR,
              "%d/%d = %.0f%% (floor %.0f%%)" % (sd + (s_rate * 100, STANDDOWN_FLOOR * 100)))
        print("   ↳ downward %d/%d = %.0f%% — ★this is why `may_judge` refuses downward★, not an incident"
              % (dn + (100.0 * dn[0] / (dn[1] or 1),)))

        # ── ⑤ control group — ★end to end, does the veto actually fire★ ──────
        #
        # ⛔ Comparing verdicts is not enough here, and finding that out took two tries. Inside the
        #    envelope the proxy is allowed to judge, real gold says "keep the incumbent" for ★every★
        #    candidate — so a proxy with shuffled answers agreed 18/18 as well. A row that a broken
        #    sample passes is not a row. What separates them is not the verdict but ★the evidence★:
        #    the real sample measures a loss big enough to veto, a shuffled one measures nothing at
        #    all (its hit rate is ~0 at both thresholds, so there is no loss to point at).
        #    So the control runs the whole guard and asks the only question that matters:
        #    ★did the incident get stopped, and does it stop being stopped when the sample is junk.★
        print("\n★end to end — the 2026-09-01 incident shape (9.45 → 13.21, incumbent firing 65.8%)")
        saved_measure, saved_pos = calibrate.measure, proxy.positives
        saved_thr = store.get_meta(db, "hook_threshold", "")
        saved_detail = store.get_meta(db, "hook_threshold_detail", "")
        saved_blocked = store.get_meta(db, "hook_threshold_blocked", "")
        saved_dir = ei.EVAL_DIR
        import shutil as _sh, tempfile as _tf
        tmp = _tf.mkdtemp(prefix="brain-proxy-e2e-")
        try:
            ei.EVAL_DIR = tmp                              # hide the labels: be a new user's machine
            calibrate.measure = lambda _db: {"threshold": 13.21, "noise_floor": 9.79,
                                             "docs": len(real_tops), "margin": calibrate.MARGIN,
                                             "_real_tops": list(real_tops)}
            store.set_meta(db, "hook_threshold", "9.45")
            r_real = calibrate.calibrate(db)
            check("the real proxy ★stops★ the incident", bool(r_real.get("not_deployed")),
                  "kept %s · sample=%s" % (r_real.get("threshold"), r_real.get("guard_sample")))

            rnd = random.Random(5)
            names = [g2 for _, g2 in first]
            shuffled = [(q, rnd.choice(names)) for q, _g in first]   # same queries, ★wrong★ answers
            proxy.positives = lambda _db, rebuild=False: shuffled
            store.set_meta(db, "hook_threshold", "9.45")
            store.set_meta(db, "hook_threshold_blocked", "")
            r_broken = calibrate.calibrate(db)
            check("⑤ (control) with ★shuffled answers★ it stops nothing — so the row above is real",
                  not r_broken.get("not_deployed"),
                  "deployed %s · sample=%s" % (r_broken.get("threshold"), r_broken.get("guard_sample")))
        finally:
            calibrate.measure, proxy.positives = saved_measure, saved_pos
            ei.EVAL_DIR = saved_dir
            store.set_meta(db, "hook_threshold", saved_thr)
            store.set_meta(db, "hook_threshold_detail", saved_detail)
            store.set_meta(db, "hook_threshold_blocked", saved_blocked)
            _sh.rmtree(tmp, ignore_errors=True)
        check("the real threshold is left exactly as it was",
              store.get_meta(db, "hook_threshold", "") == saved_thr,
              "threshold %s" % store.get_meta(db, "hook_threshold", ""))

    # ── ④ the restriction matches what was measured ──────────────────────────
    print("\n★the restriction in proxy.may_judge")
    hi = calibrate.FIRE_RATE_OK[1]
    cases = [
        ("a rise from an incumbent inside the band is judged (the 9.45 → 13.21 incident shape)",
         proxy.may_judge(9.45, 13.21, 65.8), True),
        ("a rise from an incumbent ★already too noisy★ is not (raising is right; stand down)",
         proxy.may_judge(6.0, 9.2, hi + 15.0), False),
        ("a candidate below the incumbent is not", proxy.may_judge(9.2, 8.0, 65.0), False),
        ("an equal candidate is not", proxy.may_judge(9.2, 9.2, 65.0), False),
        ("with no incumbent there is nothing to compare", proxy.may_judge(None, 12.0, 65.0), False),
        ("⛔ an unknown firing rate stands down (an unverifiable precondition is not a satisfied one)",
         proxy.may_judge(9.2, 12.0, None), False),
    ]
    for label, got, want in cases:
        check(label, got is want, "→ %s" % got)

    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the label-free sample decides like real gold where it is allowed to decide")
    return 0


if __name__ == "__main__":
    sys.exit(main())
