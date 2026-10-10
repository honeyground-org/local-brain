#!/usr/bin/env python3
"""Transliteration-bridge verification — ★measures precision against real volume★.

## Why this file exists on its own

When the bridge was built on 2026-08-19, false-bridges were checked with only **10 hand-picked words** (탐색·잘·기능·이어서·우회·평판·
배포·확인·수정·진행) and logged as "0 native false-bridges". That same day, measuring for real
against the top 400 words of 2,233 real prompts, ★38 of 53 were false-bridges★:

    항상(210회)→hangs · 해서(154)→hash · 경우(129)→gyeon · 모든(128)→modern
    생성(126)→sense · 같아(123)→gate · 다시(118)→dashed · 것이(107)→gets

⛔ This is me breaking a rule this project already wrote down — *"pull the sample from our long tail"*
(a source that passed on one famous name got 5 wrong out of 10 real partners) · *"a small set makes a bad
change look neutral"* (the adverb filter looked neutral at 14 cases, −4 at 34).

## What this measures

  precision  false-bridge if a bridge gets built onto any of the 150 native words (highest-frequency native/Sino-Korean words in real prompts)
  recall     does a bridge get built onto any of the 40 loanwords, and does the value match the expected original word
  volume     what % of real prompts contain even one false-bridge word (the hook runs on every prompt)

★Precision comes first★ — the hook runs without a human calling it, and a wrong expansion quietly
wrecks the ranking (the score goes up, so it doesn't show in the logs).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import store, translit  # noqa: E402

SET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "translit.json")

# Judgement floor. ⛔ Don't tune it to exactly match the measurement (do that and it freezes into "this is already the best").
MAX_FALSE_BRIDGE = 3      # measured 1/150(새로→share) · 2/156 including the added labels
MIN_LOAN_HIT = 24         # measured 27/45. Most remaining misses fall under §translit's "structural limit" section


def main() -> int:
    if not os.path.exists(SET_PATH):
        print("no set: %s (see bin/brain eval-init — it builds one from your own prompts)" % SET_PATH)
        return 0
    S = json.load(open(SET_PATH, encoding="utf-8"))
    loan, native = S["loan"], S["native"]
    db = store.connect()
    n_docs = store.corpus_stats(db)["docs"]
    # ⛔ ★verification must not get cut short by budget★ — the bridge has a time budget meant for the hook, so
    #    giving it 200 words at once processes only the first seven and stops (measured: only 8 came back).
    #    Then "1 false-bridge" becomes a measurement of ★a timeout★, not of precision.
    translit.BUDGET_MS = 0
    translit._ensure_cache(db)
    db.execute("DELETE FROM translit_cache_gen")   # so a judgement measured on the old corpus doesn't mix in
    db.commit()

    got_native = translit.bridge(db, native, n_docs)
    got_loan = translit.bridge(db, list(loan), n_docs)

    print("corpus %d docs\n" % n_docs)
    print("=== precision — false-bridge if a bridge gets built onto a native/Sino-Korean word ===")
    for w in native:
        if w in got_native:
            print("  ❌ %-8s → %s" % (w, got_native[w]))
    print("  false-bridges %d/%d" % (len(got_native), len(native)))

    print("\n=== recall — does a loanword reach its original word ===")
    hit = 0
    for w, want in loan.items():
        en = got_native.get(w) or got_loan.get(w)
        if en and (en == want or en.startswith(want) or want.startswith(en)):
            hit += 1
        else:
            print("  %s %-10s → %-14s (expected %s)" % ("△" if en else "✗", w, en or "none", want))
    print("  match %d/%d" % (hit, len(loan)))

    print("\nverdict: false-bridges %d(cap %d) · recall %d(floor %d) → %s"
          % (len(got_native), MAX_FALSE_BRIDGE, hit, MIN_LOAN_HIT,
             "pass ✅" if (len(got_native) <= MAX_FALSE_BRIDGE and hit >= MIN_LOAN_HIT) else "short ❌"))
    return 0 if (len(got_native) <= MAX_FALSE_BRIDGE and hit >= MIN_LOAN_HIT) else 1


if __name__ == "__main__":
    raise SystemExit(main())
