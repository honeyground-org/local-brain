#!/usr/bin/env python3
"""Short one-line query verification — ★measures where the hook falls silent★.

## Why this file is separate

`verify_threshold.py` measures with **mixed lengths** (12~600 characters). Then a paragraph prompt
pulls the average up, hiding the fact that the hook almost never fires on a short query.
Measured (2026-08-12): of 1,728 unique prompts, **a 12~40-character single line is 443 of them (25.6%)**.
One in four sits in this range, and at the old threshold 12.45 the firing rate there was **4%**.

## What differs — ★there is gold★

verify_threshold.py measures only "did it fire", leaving whether it was the right memory for a human
to read (hand-judged 2026-08-11, 15 cases: relevant 7 · weak 5 · unrelated 3). Here every A carries
**the correct memory** attached, so `gold rank 1 / top-3` is measured automatically. A regression that
lowers the threshold just to raise firing is caught by this metric — a rising firing rate with gold rank unchanged is pure noise.

★How gold was decided★ — not by gut feeling. It is what `recall` given expansions actually returned
(the judging lane has expansions, the tested lane is the expansion-free hook — different lanes, so this is not circular).
The set-building discipline and what was excluded live in the `_` field of `tests/eval/short.json`.

## ⛔ What this script cannot answer

**Which memory to attach** cannot be measured here. The threshold only decides "attach or not". In a
held-out measurement, "commits vanishing mid-merge" ranked `creator_lanes_must_stay_in_sync` 1st (the
right answer was `commits_after_pr_merge_orphaned`), and moving the threshold either way never fixes
that wrong answer — it only dies along with the signal. That is **a ranking problem**.

  usage: verify_short.py [--read]      --read = prints the body of what fired for a human to read
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import calibrate, search, store  # noqa: E402
from tests import _needs  # noqa: E402

# ⛔ A personal set lives under tests/eval/ (gitignored) — gold names memories on this machine, so
#    anyone else cloning and running it fails everything. Build your own with `bin/brain eval-init`.
SET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "short.json")

# The pass floor. ⛔ Never tuned to fit the current measurement exactly — do that and it freezes as "this is already the best".
MIN_RECALL_PCT = 25.0     # measured 32% (14% at the old threshold 12.45)
MAX_FALSE_FIRE = 0        # not a single one from group C may fire
MIN_GOLD_TOP3 = 11        # measured 14/34. ★The target is this number, not the recall rate★

# ★The lexical ceiling — measured 2026-08-12★
# The maximum recall reachable by moving only the threshold while holding false-fire at 0 is **16/34
# (47%)**, and the current value is 11/34 (32%). That 15 points cannot be closed by the threshold (A's
# lowest sits below C's highest). And the 53% above that **no threshold at all** can catch — the
# lexical score cannot separate A from C in that range. Three things were tried and failed:
#
#   query 2-grams only        gold-top3 14→15 · ceiling recall 16→15   cancel out, no evidence
#   filtering 23 Korean adverbs  A-fired 11→12  · ceiling recall 16→12   ⛔ harmful
#   query 3-grams only        fixed one specific wrong answer · A's lowest 5.50→2.48  collapsed everything
#
# ⛔ ★A small set makes a bad change look neutral★ — the adverb filter, on a first pass over 14 A
#   cases, looked like "fixed one wrong answer, everything else unchanged". Measured over 34, the
#   ceiling recall collapsed by 4 cases. The same thing an earlier memory system hit comparing local embedding
#   models (5 questions "effectively the same" → 35 questions "34/35 vs 24/35 vs 19/35").


# ★Judges by exactly the hook's own rule★ (fixed 2026-08-19)
#
# This script long judged firing by `hits[0]["score"]`. The hook does not do that —
# `[r for r in rows if r["score"] >= min_score][:MAX_ITEMS]` picks everything above the threshold
# ★from the whole result set★. Until 2026-08-12 this was the same rule (rank 1 was the top score). The
# 2026-08-18 aboutness fix decided ★the identity bonus affects rank only, never the score★ (§search.ID_STAGE),
# and since then ★rank 1 is not the top score★. Measured example: a phrase like "let me set up the tier
# categories..." ranked 1st at 6.96 while rank 2 was 10.84 — the hook fired and this check counted silence.
# ⛔ C (false fires) is the more dangerous direction — measuring with k=1 hides a false fire the hook actually attaches at rank 2~4.
# ★It never transcribes the rule — it calls the product's own function★
from brain import hook  # noqa: E402

# ★Cost must be measured on the same pipeline as the gain★ (2026-08-25) — see §hook.fallback's comment.
# The judge has a 15/min limit, so the default is the lexical path alone, and then ★it says it is unmeasured★.
FULL = os.environ.get("BRAIN_SCORECARD_FULL", "") == "1"
# ⛔ ★What could not be called is never counted as 'no false fires'★ — §search.JudgeUnavailable.
UNMEASURED = [0]

HOOK_K = hook.MAX_ITEMS + 2


def _top(db, q, k=HOOK_K):
    return search.recall(db, q, k=k, log=False)


def _fires(hits, th):
    """Does the hook actually attach anything + the top score among what fires."""
    keep = hook.select(hits, th)
    return (bool(keep), max([h["score"] for h in hits], default=0.0), keep)


def main() -> int:
    read = "--read" in sys.argv
    db = store.connect()
    _needs.labelled_sample()               # measures ★your★ labelled questions
    th = calibrate.threshold(db)
    S = json.load(open(SET_PATH, encoding="utf-8"))
    A, C = S["A_memory_needed"], S["C_no_memory_needed"]
    meta = S["meta"]
    print("threshold %.2f · corpus %d docs (%d docs · threshold %.2f at set-build time)"
          % (th, store.corpus_stats(db)["docs"], meta["corpus_docs_at_build"],
             meta["threshold_at_build"]))
    print("short queries are %.1f%% of real prompts — one in four\n" % meta["short_share_pct"])

    fired = gold1 = gold3 = 0
    misses = []
    print("A. short queries needing memory — with no expansion (same condition as the hook)")
    for a in A:
        hits = _top(db, a["q"])
        ok_fire, s, _keep = _fires(hits, th)
        if not ok_fire and FULL:                         # ★the same second stage as the deployed hook★
            try:
                fb = hook.fallback(db, a["q"], strict=True)
            except search.JudgeUnavailable:
                fb, UNMEASURED[0] = [], UNMEASURED[0] + 1
            if fb:
                ok_fire, _keep = True, fb
                if fb[0]["name"] == a["gold"]:
                    hits = fb + hits                     # rank gold 1st
        rank = next((i + 1 for i, h in enumerate(hits) if h["name"] == a["gold"]), 0)
        if ok_fire:
            fired += 1
        if rank == 1:
            gold1 += 1
        if rank and rank <= 3:
            gold3 += 1
        else:
            misses.append((a, hits[:2]))
        print("  %6.2f %s gold %-4s %s" % (
            s, "fired" if ok_fire else "silent", (str(rank) if rank else "out"), a["q"][:40]))
    rate = 100.0 * fired / max(1, len(A))
    print("\n  fired %d/%d (%.0f%%) · gold rank 1 %d · gold top-3 %d"
          % (fired, len(A), rate, gold1, gold3))

    print("\nC. short queries needing no memory — every one must sit below the threshold")
    over, over2 = [], []
    for q in C:
        hits = _top(db, q)
        ok_fire, s, keep = _fires(hits, th)
        if ok_fire:
            over.append((keep[0]["score"], q, keep[0]["name"]))
        elif FULL:                                       # ★where words fall silent = where the second stage runs★
            try:
                fb = hook.fallback(db, q, strict=True)
            except search.JudgeUnavailable:
                fb, UNMEASURED[0] = [], UNMEASURED[0] + 1
            if fb:
                over2.append((fb[0].get("rerank", 0.0), q, fb[0]["name"]))
    print("  lexical false fires %d/%d" % (len(over), len(C)))
    if FULL:
        print("  second-stage false fires %d/%d  ← ★what the deployed hook actually attaches★" % (len(over2), len(C)))
        for sc, q, nm in over2:
            print("      %-40s → %-40s %.0f/10" % (q[:40], nm[:40], sc))
    else:
        print("  ⚠️ second-stage (vectors→judge) false fires are ★unmeasured★ — rerun with BRAIN_SCORECARD_FULL=1.")
        print("     The deployed hook calls the second stage whenever words fall silent (§hook.fallback). Drop this")
        print("     line and calling it '0 false fires' produces ★a scorecard that just never measures the cost★.")
    for s, q, nm in over:
        print("   ⛔ read the body — if it is a real answer, the label is wrong")
        print("      %s (%.2f) → %s" % (q, s, nm))

    if read and misses:
        print("\nwhat was missed — ★the fix is the ranking, not the threshold★")
        for a, hits in misses:
            print("  %s\n     gold: %s" % (a["q"], a["gold"]))
            for h in hits:
                print("     came back instead: %-46s %.2f" % (h["name"][:46], h["score"]))

    total_over = len(over) + len(over2)
    # ⛔ ★The unmeasured is never called green★ — if the judge could not be called for some cases, the
    #    second-stage line is not "no false fires", it is ★unknown★ (the same discipline as §verify_vectors).
    ok = (total_over <= MAX_FALSE_FIRE and rate >= MIN_RECALL_PCT
          and gold3 >= MIN_GOLD_TOP3 and not (FULL and UNMEASURED[0]))
    print("=" * 72)
    print("  recall %.0f%% (floor %.0f%%) · false fires %d%s (ceiling %d) · gold top-3 %d (floor %d)"
          % (rate, MIN_RECALL_PCT, total_over,
             (" [lexical %d + 2nd-stage %d]" % (len(over), len(over2))) if FULL else " [lexical only]",
             MAX_FALSE_FIRE, gold3, MIN_GOLD_TOP3))
    if FULL and UNMEASURED[0]:
        # ⛔ ★never diagnose by guessing★ (2026-08-26) — the old message guessed "either the per-minute
        #    limit (15/min) or...", but the real cause was ★the daily wall (500/day)★, a layer that did
        #    not exist in the code at all. Now the judge carries its own failure reason (§rerank.last_failure).
        from brain import rerank as _rr
        b = _rr.budget()
        print("  ⛔ ★%d queries the judge could not be called for★ — reason: %s"
              % (UNMEASURED[0], _rr.last_failure() or "(unknown)"))
        print("     daily budget %d/%d · left %d%s   (`brain budget`)"
              % (b["used"], b["limit"], b["left"], "  ⛔wall" if b["wall"] else ""))
        print("     This is not 'no false fires' — it is ★unknown★. Measure it again.")
    print("  verdict: %s" % ("pass ✅" if ok else "short ❌"))
    print("  ⛔ a rising firing rate with gold rank unchanged is just rising noise — watch both together")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
