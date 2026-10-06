#!/usr/bin/env python3
"""★Why recall misses★ — the reach axis broken down by cause. (local · budget 0 · a measurement, not a check)

A single number ("reach 50.4") tells you nothing about what to do next. This splits the misses into
the two causes that need completely different work, and it exists so the split is ★re-measured
rather than remembered★.

## ⛔ Read this before trusting any split — the cut-off decides the answer

`docs/WORKPLAN.md` §1-b records this exact diagnosis being made ★and then overturned★ on
2026-08-31: counted to a short cut-off it looked like an overwhelming "vocabulary gap", and counting
to rank 100 showed most of those were ★in the candidate list, merely ranked wrong★. On 2026-09-09
the same mistake was made again here — measured to 20, reported 42% vocabulary — and caught by
reading that note. ★So the cut-off is 100, and it is not a preference.★
"the query has no content word in the answer" does ★not★ mean "it cannot be found".

## What it found on 2026-09-09 (57-case labelled sample · 1,401 documents)

    ✅ top 3                              23  (40%)
    ②  4-10                                7  (12%)  ┐
    ②  11-30                               4  ( 7%)  ├ ★15 (26%) — in the list, ranked wrong★
    ②  31-100                              4  ( 7%)  ┘
    ① not in the top 100 — ★vocabulary★   19  (33%)  ← 18 of them share <= 2 words with the answer

    (2026-08-31, same sample size: 26 · 4 · 6 · 7 · 14. Reach has drifted slightly down while the
     corpus grew 1,311 → 1,401 and the tokenizer changed — worth a look, not a panic.)

## What each band needs — they are ★not★ the same work

  ② ★ranking★ (26%) is the recoverable band, and §1-b already measured the fix: re-ranking the
    lexical top 20 with the judge moved ★4 of 11★ into the top 3 (reach 45.6% → 52.6%). It is not
    deployed because the judge's daily budget cannot pay for it on every recall — which is the real
    reason a local re-ranker was investigated (and rejected on quality, §1-b-3). ⛔ The lever here
    is ★affordability★, not accuracy.

  ① ★vocabulary★ (33%) is the hard band. Measured against every bridge that already exists:
      · the deployed ★second stage★ (semantic + judge) rescues ★4 of 29★ lexical silences
        (the hook's hit rate 28% → 35% — real, but it does not close this)
      · the ★transliteration bridge★ cannot close it ★by design★ — it declines a target too common
        to discriminate, so `세션` → `session` and `머지` → `merge` are refused on purpose (verified
        by clearing the cache and recomputing with an unlimited budget: the refusal is the rule)
      · the ★personal lexicon★ turned itself off — all 36 grid points raised false fires, and this
        repository's written rule is that any rise in false fires disqualifies (§lexicon.decide)
    ⇒ closing ① needs a ★new association mechanism★ — learning "when the user says this kind of
      thing, that memory applied", the prompt-side equivalent of what the behaviour layer already
      does for tool calls. A design project, not a threshold.

⛔ Do not move a threshold to make this number look better. Two of the three bridges above are
   restrained ★on purpose★; loosening one is undoing a measured decision, not making a new one.

How to run:  PYTHONPATH=. python3 tests/measure_recall_causes.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain import evalinit as ei, search, store, textindex  # noqa: E402

TOPK = 100                    # ⛔ 100, not 20 — see the docstring: a short cut-off turns a
                              #    ranking problem into a fake "vocabulary gap" (this repo did that once)
BANDS = ((3, "top 3"), (10, "4-10"), (30, "11-30"), (100, "31-100"))
THIN = 2                      # "shares this many words or fewer" counts as no lexical contact


def main() -> int:
    db = store.connect()
    A = ei.load_A()
    if not A:
        print("no labelled sample on this machine — run `bin/brain eval-init` first")
        print("(this is a measurement of ★your own★ questions; there is nothing to measure without them)")
        return 0

    band_n = {label: 0 for _, label in BANDS}
    top3 = ranked = missing = thin = 0
    examples = []
    for q, gold in A:
        rows = search.recall(db, q, k=TOPK, log=False)
        pos = next((i for i, r in enumerate(rows, 1) if ei.gold_hit(gold, r["name"])), 0)
        if pos:
            for edge, label in BANDS:
                if pos <= edge:
                    band_n[label] += 1
                    break
        if pos and pos <= 3:
            top3 += 1
            continue
        if pos:
            ranked += 1
            continue
        missing += 1
        name = ei.golds(gold)[0]
        row = db.execute(
            "SELECT title, description, substr(body,1,4000) b FROM docs WHERE name=?", (name,)).fetchone()
        if not row:
            continue
        qw = {w.lower() for w, _ in textindex.query_terms(q)}
        dw = {w.lower() for w in textindex.words(
            " ".join([row["title"] or "", row["description"] or "", row["b"]]))}
        shared = qw & dw
        if len(shared) <= THIN:
            thin += 1
            if len(examples) < 6:
                examples.append((q, name, sorted(shared)))

    n = len(A)
    print("=" * 78)
    print("why recall misses — %d cases, top %d" % (n, TOPK))
    print("=" * 78)
    for _edge, label in BANDS:
        mark = "✅" if label == "top 3" else "② "
        print("  %s %-8s %3d  (%2.0f%%)%s" % (mark, label, band_n[label], 100.0 * band_n[label] / n,
              "  ← in the candidate list, ★ranked wrong★" if label == "4-10" else ""))
    print("  ① not in the top %-3d — ★vocabulary★   %3d  (%2.0f%%)" % (TOPK, missing, 100.0 * missing / n))
    print("     ↳ ★ranking★ total (4-%d): %d (%.0f%%) — recoverable by re-ranking, not by new words"
          % (TOPK, ranked, 100.0 * ranked / n))
    print("       of those, %d share %d words or fewer with their own answer" % (thin, THIN))
    if examples:
        print("\n  what ① looks like (query · answer · words in common):")
        for q, name, shared in examples:
            print("    %-40s → %-38s %s" % (q[:40], name[:38], shared or "★none★"))
    print("\n" + "=" * 78)
    print("② is the recoverable band — re-ranking moved 4 of 11 into the top 3 once (§WORKPLAN 1-b);")
    print("   it is not deployed because the judge's budget cannot pay for it on every recall.")
    print("⛔ ① is a vocabulary gap, not a threshold. Read this file's docstring before tuning anything —")
    print("   the semantic stage, the transliteration bridge and the lexicon were each already measured")
    print("   against it, and two of the three are restrained ★on purpose★.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
