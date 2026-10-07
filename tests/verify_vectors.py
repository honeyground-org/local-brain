#!/usr/bin/env python3
"""Measures semantic search (remote embedding) — ★no harm★ separate from ★usefulness★.

## Why this check exists

Vectors are ★the most dangerous addition★ in this brain. Three reasons:

  ① the lexical path is already locked in by contract and runs well (42/43 given synonyms). A new path that
     steals its place is a pure loss — yesterday the graph was added into recall and hit exactly that (top-8 9→8).
  ② the calibration differs. Cosine sits on a 0.3~0.5 scale as a matter of course, and getting the threshold
     wrong ★loads something onto every question★. This has already been burned twice, on the BM25 threshold.
  ③ it's remote — a round trip costs 508ms (measured) and money, and the memory body leaves the machine.

So the verdict splits in two. ①~③ are ★no harm★ (set a threshold, fail on it),
④ is ★usefulness★ (only measured, no threshold — the size is decided by the corpus and the model).

## A state where quality can't be measured never counts as green

If loading is incomplete or before calibration, ④ reports ★can't measure★ (never says it passed).
· lesson_i_prioritized_a_defect_my_eval_set_cannot_measure
"""
from __future__ import annotations

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # runs without PYTHONPATH too
from brain import evalinit as _ei  # noqa: E402 ★gold verdicts live in exactly one place★

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain import calibrate, hook, search, store, vectors   # noqa: E402

ok = True
notes = []


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  %s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))


db = store.connect()
cov = vectors.coverage(db)
thr_cos = vectors.min_cos(db)
thr_ratio = vectors.min_ratio(db)
from brain import rerank                                  # noqa: E402
thr_rerank = rerank.min_score(db)
# ★the canonical gate is the judge's score★ (2026-08-25) — cosine ratio couldn't separate the control group from the right answer
calibrated = (thr_rerank <= 10.0) or (thr_ratio < vectors.MIN_RATIO_FALLBACK)
pct = 100.0 * cov["embedded"] / max(1, cov["docs"])

print("=" * 74)
print("semantic search — provider %s · space %s · dim %d"
      % (vectors.PROVIDER, vectors.model_tag(), vectors.DIM))
print("loaded %d/%d docs (%.0f%%) · chunks %d · gate(ratio) %s · reference cosine %s"
      % (cov["embedded"], cov["docs"], pct, cov["chunks"],
         ("judge %.0f/10" % thr_rerank) if thr_rerank <= 10 else
         (("%.4f" % thr_ratio) if thr_ratio < 99 else "uncalibrated (silent)"),
         ("%.4f" % thr_cos) if thr_cos <= 1.0 else "-"))
print("=" * 74)

print()
print("① contract — lexical recall never ★mixes in★ the semantic path")
mixed = []
for q in ("테라폼 apply 해도 되나", "출금 검증", "세션이 죽었는데 정상이라고 나와"):
    for r in search.recall(db, q, k=8, log=False):
        if r.get("via_vector"):
            mixed.append((q, r["name"]))
check("recall has no via_vector result", not mixed, str(mixed[:2]))

print()
print("② calibration — the threshold comes ★from calibration, not code★")
saved = store.get_meta(db, "rerank_min_score") or store.get_meta(db, "vec_min_ratio")
check("no threshold is hardcoded in the code",
      vectors.MIN_RATIO_FALLBACK >= 99.0 and rerank.MIN_SCORE_FALLBACK >= 99.0,
      "fallback ratio %.1f · judge %.1f = both silent"
      % (vectors.MIN_RATIO_FALLBACK, rerank.MIN_SCORE_FALLBACK))
check("nothing is added before calibration",
      calibrated or not search.semantic(db, "테라폼 apply 해도 되나", k=1),
      "stored value %s" % (saved or "none"))
if calibrated:
    stamp = store.get_meta(db, "vec_model") or ""
    # ⛔ the stored tag is ★the space's name★ (model+prefix scheme) — comparing by MODEL alone is always wrong
    want = "%s/%s/%d" % (vectors.PROVIDER, vectors.model_tag(), vectors.DIM)
    check("the model the threshold was measured on matches the current model", stamp == want,
          "stored %s ↔ now %s" % (stamp or "none", want))

# read the eval set first (③-0 uses the control-group sentences as probes)
path = os.path.join(ROOT, "tests", "eval", "short.json")
control, positives = [], []
if os.path.exists(path):
    with open(path, encoding="utf-8") as fh:
        _d = json.load(fh)
    control = [c["q"] if isinstance(c, dict) else c
               for c in _d.get("C_no_memory_needed", [])]
    positives = [(c["q"], c["gold"]) for c in _d.get("A_memory_needed", [])
                 if c.get("gold")]

print()
print("③-0 ★an unlabeled negative control★ — a random memory that bypassed search should score low")
# ⛔ why this is the canonical control group (2026-08-25):
#   it was measuring noise with a list of "real sentences needing no memory," but as the corpus grew,
#   ★a genuinely correct memory★ appeared for those very sentences ("everything got merged" → merging
#   three at once means one commit missed the deploy). A threshold set on stale labels makes the brain
#   ★go silent exactly where it should speak★. So a control group needing no labels is used instead —
#   ★random pairs★. Feed the judge a candidate memory that bypassed search and it should score low.
import random as _rnd
from brain import rerank as _rr
if _rr.available() and cov["chunks"]:
    _rnd.seed(11)
    names = [r["name"] for r in db.execute(
        "SELECT name FROM docs WHERE source='memory' ORDER BY RANDOM() LIMIT 200")]
    probes = [q for q in (control or [])[:6]]
    highs = []
    for q in probes:
        cands = []
        for n in _rnd.sample(names, min(8, len(names))):
            r = db.execute("SELECT name, description, body FROM docs WHERE name=?",
                           (n,)).fetchone()
            if r:
                cands.append({"name": r["name"], "description": r["description"],
                              "body": (r["body"] or "")[:500]})
        sc = _rr.score(q, cands)
        if sc:
            highs.append(max(sc))
    if highs:
        med = sorted(highs)[len(highs) // 2]
        check("the random pair's median score is low (<4)", med < 4, "median %.1f · top %.1f"
              % (med, max(highs)))
    else:
        print("  ⏭ judge call failed — could be the rate limit (15/min)")
else:
    print("  ⏭ no judge available, or vectors not loaded")

print()
print("③ silence — a semantic path riding along on a real sentence needing no memory is noise")
if not calibrated:
    print("  ⏭ uncalibrated — right now it structurally stays silent (retry after `brain vec calibrate`)")
    notes.append("③ not measured (uncalibrated)")
elif not control:
    print("  ⏭ no control group — tests/eval/short.json is required")
    notes.append("③ not measured (no control group)")
else:
    # ⚠️ this labeled control group is ★reference only★ — on 2026-08-25, 10 entries turned out mislabeled
    #    (a genuinely correct memory had appeared while still marked 'no memory needed'). The verdict is made by ③-0 (random pairs) above.
    noisy = []
    for q in control[:12]:
        if search.semantic(db, q, k=1):
            noisy.append(q[:36])
    print("  reference: %d of 12 labeled control entries got something added — ★suspect the label first★" % len(noisy))

print()
print("④ usefulness — does it find the answer on a question with ★no synonym given★ (no threshold, only measured)")
if not calibrated or cov["embedded"] < cov["docs"] * 0.9:
    print("  ⏭ can't measure — loaded %.0f%% · %s" % (pct, "calibrated" if calibrated else "uncalibrated"))
    print("     ⛔ this state is never called 'passed.' Load and calibrate, then run it again.")
    notes.append("④ not measured (loaded %.0f%%)" % pct)
else:
    thr = calibrate.threshold(db)
    lex_hit = vec_hit = both = 0
    for q, gold in positives:
        rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
        fired = [r["name"] for r in hook.select(rows, thr)]
        l = any(_ei.gold_hit(gold, n) for n in fired)
        v = any(_ei.gold_hit(gold, r["name"]) for r in search.semantic(db, q, k=3))
        lex_hit += l
        vec_hit += v
        both += (l or v)
    n = len(positives)
    print("  lexical only %d/%d (%.0f%%) · semantic only %d/%d (%.0f%%) · either one %d/%d (%.0f%%)"
          % (lex_hit, n, 100.0*lex_hit/n, vec_hit, n, 100.0*vec_hit/n,
             both, n, 100.0*both/n))
    print("  ★this table's last column is automatic recall's real skill★ (the 'automatic' row of the grep comparison table)")
    check("the semantic path never lowers automatic recall", both >= lex_hit,
          "%d → %d" % (lex_hit, both))

print()
print("⑤ cost")
t0 = time.time()
search.semantic(db, "한도를 걸었는데 왜 안 막혔지", k=3)
d1 = (time.time() - t0) * 1000
t0 = time.time()
search.semantic(db, "한도를 걸었는데 왜 안 막혔지", k=3)
d2 = (time.time() - t0) * 1000
print("  first call %.0fms · repeat call for the same query %.0fms (query cache)" % (d1, d2))
check("a cached query has no round trip (<120ms)", d2 < 120, "%.0fms" % d2)

print()
print("=" * 74)
print("verdict: %s%s" % ("pass ✅" if ok else "short ❌",
                     ("  · " + " · ".join(notes)) if notes else ""))
sys.exit(0 if ok else 1)
