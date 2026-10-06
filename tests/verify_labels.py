"""Label-conflict check — ★if the same phrase carries two labels, no judge can tell them apart★.

## Why this check exists (measured 2026-08-25)

Digging into 5 cases where meaning-search stayed silent showed the vectors placing gold in the
candidates ★every single time★ — 1st·2nd·2nd·5th·17th place. The break was in the judge, and the
judge was not wrong — it was behaving ★consistently★, because the same shape of phrase sat on both A (memory needed) and C (memory not needed).

    0.80  A: 머지 완료했어. 검증해줘      C: 머지 완료했어. 확인해줘
    0.56  A: 배포 완료되면 화면에서 확인해줘  C: 배포 완료되면 바로 진행해

Silence is the right answer on one side (0 false fires) and the wrong answer (a miss) on the other.
There is no signal in the sentence to tell them apart, so ★no threshold, no prompt can get both right at once.★
· Same shape as [[lesson_one_parameter_two_meanings_is_why_the_cap_kept_coming_back]].

The cause was a relabeling pass ★that lost its pair★ (moving 10 items from C→A left an almost-identical
sentence behind in C). Labels keep growing, so this is stopped by a check, not human attention.

## Threshold (set from measurement — not from looking at the labels)

The 2 pairs that got moved were 0.80 and 0.56, and the highest that remained after the move was 0.526. Hence 0.55.
0.45~0.55 shows only as a ★warning★ — that's a spot for a human to read and judge, not for the check to decide.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAIL_AT = 0.55
WARN_AT = 0.45
HERE = os.path.dirname(os.path.abspath(__file__))


def _q(x):
    return x["q"] if isinstance(x, dict) else x


def _norm(s: str) -> str:
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", s or "")


def main() -> int:
    path = os.path.join(HERE, "eval", "short.json")
    if not os.path.exists(path):
        print("no label file — run `brain eval-init` to build your own set.")
        return 0
    d = json.load(open(path, encoding="utf-8"))
    A = [_q(x) for x in d.get("A_memory_needed", [])]
    C = [_q(x) for x in d.get("C_no_memory_needed", [])]

    print("=" * 74)
    print("label conflicts — A(memory needed) %d · C(memory not needed) %d" % (len(A), len(C)))
    print("=" * 74)

    conflicts, warns = [], []
    for a in A:
        na = _norm(a)
        for c in C:
            r = difflib.SequenceMatcher(None, na, _norm(c)).ratio()
            if r >= FAIL_AT:
                conflicts.append((r, a, c))
            elif r >= WARN_AT:
                warns.append((r, a, c))

    for r, a, c in sorted(conflicts, reverse=True):
        print("  ⛔ %.3f  A: %-32s" % (r, a[:32]))
        print("            C: %-32s" % c[:32])
    for r, a, c in sorted(warns, reverse=True)[:5]:
        print("  ⚠️ %.3f  A: %-32s  C: %s" % (r, a[:32], c[:32]))

    # ★eval contamination★ — write an eval sentence into a memory doc ★verbatim★ and the check finds itself.
    # measured 2026-08-26: a lesson written that day quoted a control-group sentence as an example, and that
    # memory attached to that query at 18.46, turning a false-fire count of 0 into 1. This repository has already
    # hit this trap (`868efef` "writing a control-group sentence into a doc made the calibration find itself"). Memories keep getting written, so it recurs.
    from brain import store as _store
    db = _store.connect()
    tainted = []
    # ⛔ ★only C (the control group) is counted as a failure★ — A is supposed to find gold anyway, so the
    #    sentence being in a doc points the same direction. What's harmful is a phrase labeled "memory not needed"
    #    sitting in a doc and ★causing a false fire★. A contamination is not even a warning (a legitimate quote in a design doc).
    for q in C:
        if len(q) < 12:                                  # a short phrase can overlap by chance
            continue
        rows = db.execute(
            "SELECT name FROM docs WHERE instr(body, ?) > 0 LIMIT 3", (q,)).fetchall()
        for r in rows:
            tainted.append((q, r["name"]))
    for q, nm in tainted:
        print("  ⛔ eval contamination: %-34s appears verbatim in %s" % (q[:34], nm[:40]))
    if tainted:
        print("     → ★alter★ the quote (keep the meaning, break only the verbatim match).")

    # an A item with no gold — cannot be scored
    nogold = [x for x in d.get("A_memory_needed", [])
              if isinstance(x, dict) and not x.get("gold")]
    for x in nogold:
        print("  ⛔ no gold: %s" % x["q"][:50])

    print()
    ok = not conflicts and not nogold and not tainted
    print("verdict: %s (conflicts %d · warnings %d · no gold %d · contamination %d)"
          % ("pass ✅" if ok else "short ❌", len(conflicts), len(warns),
             len(nogold), len(tainted)))
    if warns and ok:
        print("⚠️ a warning is not a failure — it's a spot for a human to read and judge (not for the check to decide)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
