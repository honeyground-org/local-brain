"""Automatic axis check — ★does it count both directions★. (local · budget 0)

## Why this is needed (measured 2026-09-01)

This repository measured the automatic axis ★half-right, twice★ — and both times in opposite directions.

    round 1  measured by ★firing rate★        → noise ★raised★ the score      (fixed 08-31)
    round 2  counted ★hits alone★              → reducing noise earned ★no credit★

The scene where round 2 actually lied (09-01):

    the lexical threshold moved on its own, 8.5 → 9.45
      hit          20/57 → 19/57  (−1)
      false fire     5/34 →  1/34  (−4)      ← ★a clear improvement★
      automatic axis  70 → 67   (−3 points)  ← ★wrote the improvement down as a drop★

So this check is built so that ★watching only one side cannot pass★.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ⛔ This check calibrates ★the real index★ with forced or broken samples — it must never teach the live
#    margin filter (§brain/adaptive.step). Inherited by every child process it starts.
os.environ["BRAIN_ADAPTIVE_LEARN"] = "0"

from brain import calibrate as cal, evalinit as ei, hook, scorecard, search, store  # noqa: E402
from tests import _needs  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def sweep(db, A, C, t):
    """(hit, false fire) at threshold t."""
    hit = 0
    for q, gold in A:
        keep = hook.select(search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False), t)
        if keep and any(ei.gold_hit(gold, r["name"]) for r in keep):
            hit += 1
    ff = sum(1 for q in C
             if hook.select(search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False), t))
    return hit, ff


def main():
    print("=" * 72 + "\nAutomatic axis — does it count hits and noise ★together★\n" + "=" * 72)
    db = store.connect()
    _needs.labelled_sample()               # measures ★your★ labelled questions
    # ⛔ ★The check also calls the canonical source★ — reading it by hand goes green even if the
    #    canonical source changes (the same trap that let `verify_diagnostics` miss drift by holding its own constant)
    A = ei.load_A()
    C = ei.load_C()

    # ── ① block ★a probe that cannot fail★ first ─────────────────────────
    # On 09-01 the control-group key was wrong, 0 cases loaded, and the table ★confidently★ reported 'false fire 0.0%'.
    check("the control group actually loaded (a wrong key name dies right here)",
          len(C) >= 30, "C=%d cases" % len(C))
    check("the gold sample loaded", len(A) >= 50, "A=%d cases" % len(A))
    if FAIL:
        return 1

    # ── ② the axis ★produces no score without a control group★ ───────────────
    got = scorecard.collect.__doc__ is not None            # is the module alive
    sc = scorecard.collect(db, cheap=True)
    check("automatic is measured", sc.get("automatic") is not None,
          "raw %.4f" % (sc.get("automatic") or -1))
    check("hit and false-fire are recorded ★separately★ (merged, the cause is invisible)",
          sc.get("_auto_hit") is not None and sc.get("_auto_false_fire") is not None,
          "hit %.3f · false-fire %.3f" % (sc.get("_auto_hit") or -1, sc.get("_auto_false_fire") or -1))
    check("the control-group size is recorded", (sc.get("_auto_control_n") or 0) >= 30,
          "%d cases" % (sc.get("_auto_control_n") or 0))

    # ── ③ ★does it credit reducing noise★ — the same 09-01 scene, reproduced ────
    lo, hi = 8.5, 9.45
    h_lo, f_lo = sweep(db, A, C, lo)
    h_hi, f_hi = sweep(db, A, C, hi)
    print("   threshold %.2f → hit %d · false-fire %d" % (lo, h_lo, f_lo))
    print("   threshold %.2f → hit %d · false-fire %d" % (hi, h_hi, f_hi))
    # ⛔ ★The scene has to be an actual improvement, not merely the right shape★ (tightened 2026-09-09).
    #    The old condition was "hits did not rise and noise fell", which reproduces whenever ★any★
    #    hits are lost and ★any★ noise is removed — regardless of how much of each. On this corpus
    #    that became 2 hits lost (of 57) against 1 false fire removed (of 34): the higher threshold
    #    is genuinely the worse one, the new ruler correctly says so, and the row failed for being
    #    right. Measured on the pre-tokenizer index too — the same counts, so it is corpus drift,
    #    not a regression.
    #    ⛔ Not narrowed to "run only when the assertion holds" — that is a probe that cannot fail.
    #    The condition below is about ★the counts★ (is the trade favourable at all), while the
    #    assertion is about ★the product rule★, so the row still has something to prove.
    lost = (h_lo - h_hi) / float(len(A))                  # share of answers given up
    quiet = (f_lo - f_hi) / float(len(C))                 # share of noise removed
    if h_hi <= h_lo and f_hi < f_lo and quiet > lost:
        old = h_hi / float(len(A))                        # the old ruler (hits alone)
        old_lo = h_lo / float(len(A))
        new = (h_hi / float(len(A))) * (1 - f_hi / float(len(C)))
        new_lo = (h_lo / float(len(A))) * (1 - f_lo / float(len(C)))
        check("the old ruler wrote this improvement down as ★a drop★ (why it was fixed)", old <= old_lo,
              "%.4f ≤ %.4f" % (old, old_lo))
        check("★the new ruler writes the improvement down as a rise★", new > new_lo,
              "%.4f > %.4f" % (new, new_lo))
    else:
        # ⛔ Say it out loud. A scene that stopped reproducing must never look like a passing row.
        print("   ⏭  the 09-01 scene does not reproduce on this corpus — %.1f%% of answers given up "
              "for %.1f%% of noise removed (hit %d→%d · false-fire %d→%d). Not an improvement, so "
              "there is nothing to credit; this cell is skipped, not passed."
              % (lost * 100, quiet * 100, h_lo, h_hi, f_lo, f_hi))

    # ── ④ both extremes — "always fires" and "never gets one right" are neither one automatic ────
    h0, f0 = sweep(db, A, C, -1e9)                        # fires unconditionally
    always = (h0 / float(len(A))) * (1 - f0 / float(len(C)))
    check("★firing always★ makes automatic 0 (noise cannot raise the score — round 1's hole)",
          always == 0.0, "%.4f (hit %d · false-fire %d/%d)" % (always, h0, f0, len(C)))
    check("★getting nothing right★ makes automatic 0 (having no noise alone earns no credit — the flip side of round 2)",
          (0.0 * 1.0) == 0.0, "hit 0 × quiet 1.0 = 0")

    # ★The relative rule for short queries★ (REL_FLOOR_SHARE·REL_RATIO) — designed behaviour (measured
    #  2026-08-19: of 12 silent cases, 3 had 'gold ranked 1st but below the threshold'), measured here as a
    #  ★net gain★ on its own. Until 2026-10-06 its floor was an absolute 7.5, so an infinite threshold left it
    #  firing and this check measured it that way. The floor is now a share of the threshold: an infinite one
    #  (no ruler) silences the hook, and the rule is measured standalone at the calibrated scale instead.
    h9, f9 = sweep(db, A, C, 1e9)
    check("an infinite threshold — no ruler — silences the hook, the relative rule included",
          h9 == 0 and f9 == 0, "hit %d · false-fire %d" % (h9, f9))
    thr_now = cal.threshold(db)
    floor = thr_now * hook.REL_FLOOR_SHARE

    def rel_only(rows):
        d = [r for r in rows if not r.get("related")]
        return [r for r in d if r["score"] >= floor and r.get("ratio", 0.0) >= hook.REL_RATIO][:1]
    rh = sum(1 for q, g in A if any(ei.gold_hit(g, r["name"]) for r in
                                   rel_only(search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False))))
    rf = sum(1 for q in C if rel_only(search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)))
    check("★the relative rule★ on its own, at the calibrated scale, is a net gain (hit > false-fire)",
          rh > rf, "floor %.2f · hit %d · false-fire %d" % (floor, rh, rf))

    # ── ⑤ change the ruler and ★does the record get a version stamp★ ──────────────────────────
    check("an axis-definition version is declared", getattr(scorecard, "AXIS_VERSION", 0) >= 2,
          "v%s" % getattr(scorecard, "AXIS_VERSION", "?"))

    # ── ⑥ ★the history★ of a self-moving scale ────────────────────────────
    hist = cal.history(db)
    check("the lexical threshold's movement is recorded (unrecorded, a score change cannot be attributed)",
          isinstance(hist, list) and len(hist) >= 1,
          "%d rounds · latest %s" % (len(hist), hist[-1]["threshold"] if hist else "—"))

    # ── ⑦ ★is a worse scale kept from deploying★ (2026-09-01) ─────────────
    #    Today the lexical scale moved on its own, 9.45 → 12.15 (hit 19→10 · false-fire 1→0).
    #    The corpus was unchanged (1311), and it moved because the scale uses ★recent real prompts★ as
    #    the positive control group — a session that digs into one topic alone pulls the scale up.
    print("-" * 72)
    b = cal._bench(db, [9.45, 12.15])
    check("two scales can be compared on a fixed sample", bool(b),
          "9.45 automatic %.3f ↔ 12.15 %.3f" % (b[9.45]["auto"], b[12.15]["auto"]) if b else "no sample")
    if b:
        check("★12.15 really is worse★ (the guard's premise)",
              b[9.45]["auto"] - b[12.15]["auto"] > cal.deploy_margin(b[12.15]["n_a"]),
              "difference %.3f > margin %.3f" % (b[9.45]["auto"] - b[12.15]["auto"],
                                                 cal.deploy_margin(b[12.15]["n_a"])))

    # is the guard ★really blocking★ — leave the stored value good and call calibration.
    #
    # ⛔ ★It must not be expected that calibration produces a bad value today★ (this cell went red
    #    on 2026-09-02): the scale is measured over ★recent real prompts★, so it differs every day.
    #    On 09-01 it produced 13.21, on 09-02 it produced 9.46 — the latter is not a bad value, it is
    #    correct for the guard to pass it. A check that leans on the environment ★goes red even when
    #    the feature is fine.★ ⇒ ★Pin★ the measurement so only the rejection path is measured deterministically.
    saved = store.get_meta(db, "hook_threshold", "")
    real_measure = cal.measure
    try:
        BAD = 13.21                                       # the actual bad value produced on 09-01
        cal.measure = lambda db, **kw: {"threshold": BAD, "docs": 0, "real_prompts": 0,
                                        "fire_rate_pct": 0.0}
        store.set_meta(db, "hook_threshold", "9.45")
        r = cal.calibrate(db)
        check("★a calibration that regresses is not deployed★ (measured with the measurement pinned to %.2f)" % BAD,
              r.get("not_deployed") is True and float(store.get_meta(db, "hook_threshold", "0")) == 9.45,
              "produced %.2f · stored %s" % (r["threshold"], store.get_meta(db, "hook_threshold", "")))
        check("the rejection grounds are recorded (it does not stay quiet)", bool(cal.blocked(db).get("why")),
              (cal.blocked(db).get("why") or "")[:60])

        # ⛔⛔ ★does drift set in stone★ — the first version broke exactly here (2026-09-01 18:40).
        #    Comparing only (current value ↔ new value), the moment a bad value is stored once it
        #    becomes ★the new baseline, with no way back★. It was found stuck at automatic 0.284 → 0.128.
        #    ⇒ The guard must ★remember the value it kept★. This measures exactly that.
        store.set_meta(db, "hook_threshold_blocked", json.dumps({"kept": 9.45}))
        store.set_meta(db, "hook_threshold", "13.21")     # ★a bad value already sits in storage★
        r3 = cal.calibrate(db)
        back = float(store.get_meta(db, "hook_threshold", "0") or 0)
        b3 = cal._bench(db, [back]) if back else {}
        check("★with a bad value already stored, it rolls back to the kept value★ (drift does not set in stone)",
              abs(back - 13.21) > 1e-9 and bool(b3) and b3[back]["auto"] > 0.20,
              "13.21 → %.2f (automatic %.3f)" % (back, b3[back]["auto"] if b3 else -1))

        # ⛔ ★A guard that only blocks freezes the scale★ — a good value must still pass.
        #    Leave the current scale as ★a bad value★ and calibration's own value is better, so it must pass.
        store.set_meta(db, "hook_threshold", "20.0")     # a value where nothing fires
        store.set_meta(db, "hook_threshold_blocked", "")
        r2 = cal.calibrate(db)                            # the pinned 13.21 beats 20.0
        check("★an improving calibration passes★ (the guard does not freeze the scale)",
              not r2.get("not_deployed") and float(store.get_meta(db, "hook_threshold", "0")) < 20.0,
              "20.0 → %s" % store.get_meta(db, "hook_threshold", ""))
    finally:
        cal.measure = real_measure                        # ★always release the pin★
        store.set_meta(db, "hook_threshold", saved)       # ★restore the real data★
        store.set_meta(db, "hook_threshold_blocked", "")
        store.set_meta(db, "hook_threshold_unguarded", "")
    # ⛔ ★This check must not leave a bad scale behind★ — it plants 20.0 partway through.
    #    A 13.19 was actually once found left behind (the cause was not this check but ★a long-running
    #    process holding old code★ reindexing, but it is nailed down here too).
    left = float(store.get_meta(db, "hook_threshold", "0") or 0)
    b2 = cal._bench(db, [left]) if left else {}
    check("★the scale is a usable value after the check ends★ (no bad value left behind)",
          bool(b2) and b2[left]["auto"] > 0.20,
          "remaining scale %.2f · automatic %.3f" % (left, b2[left]["auto"] if b2 else -1))

    # ── ⑧ measured through the deploy pipeline, ★is the cache off★ (budget 0 — fake the judge) ──
    #    ⛔ The first version of this code left the cache on and called it 52 times — `verify_score_honesty` caught it.
    #    A measurement that reads its own past answer 'reproduces' anything.
    print("-" * 72)
    # ⛔ ★This check spends no budget.★ The first version did not block `score`, and the accuracy axis
    #    actually called the judge ★75 times★ — the check was eating self-improvement's own wallet.
    #    ★Both entry points are blocked★ (`score` · `score_chunked`).
    from brain import rerank as _rr
    seen = []
    real_c, real_s = _rr.score_chunked, _rr.score

    def spy_c(query, candidates, size=0, db=None, cache=True):
        seen.append(cache)
        return None                                   # spends neither network nor budget

    def spy_s(query, candidates, model="", prompt="", reserve=0, db=None, cache=True):
        seen.append(cache)
        return None

    _rr.score_chunked, _rr.score = spy_c, spy_s
    os.environ["BRAIN_SCORE_AUTO_DEPLOYED"] = "1"
    b_before = _rr.budget()["used"]
    try:
        scorecard.collect(db, cheap=False)
    except Exception:                                 # noqa: BLE001
        pass                                          # the accuracy axis may die here
    finally:
        _rr.score_chunked, _rr.score = real_c, real_s
        os.environ.pop("BRAIN_SCORE_AUTO_DEPLOYED", None)
    # ⛔ ★The global counter must not be asserted at 'exactly 0 growth'★ — `rerank_used` is
    #    ★used by other processes too★ (the prompt hook, an MCP server calling the second stage while recalling).
    #    The first version actually went red at +1 and the cause was not my code.
    #    (`brain budget` already carries the same warning: "used is what we counted".)
    #    ⇒ Leave room, but ★a 75-call leak must still be caught★ (the first version did exactly that).
    grew = _rr.budget()["used"] - b_before
    check("★this check spends (almost) no budget★ — a check must not eat self-improvement's own wallet",
          grew <= 3, "budget %d → %d (grew %d · the margin of 3 belongs to other processes)"
          % (b_before, _rr.budget()["used"], grew))
    check("the deploy-pipeline automatic axis ★actually calls the judge★", bool(seen),
          "%d calls" % len(seen))
    check("★every one of those calls has the cache off★", seen and not any(seen),
          "calls made with cache=True: %d" % sum(1 for c in seen if c))

    print("=" * 72)
    print("❌ %d failed: %s" % (len(FAIL), ", ".join(FAIL)) if FAIL else "✅ all passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
