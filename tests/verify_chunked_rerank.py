"""Chunked-rerank check — ★does the order stay intact when asked in batches★.

## Why this check exists (2026-08-31)

Give the judge 20 candidates ★as one list★ and the same input splits into different answers (measured:
the same decision only 62% of the time). Asking 5 at a time gave 100%. The premise behind "top-20" was
★a dedicated reranker★, and we use a general-purpose LLM — the candidates interfere with each other.

So `rerank.score_chunked` exists, but ⛔ **code that asks in batches slips out of sync easily**:
- if a score and a candidate ★drift apart★ at a batch boundary, another candidate's score gets read as this one's
- if the judge answers ★fewer★ than the candidate count, everything after shifts
- if one batch fails and gets filled with 0.0, "there's no answer" and "couldn't ask" get mixed up

This check ★intercepts★ the judge (no network, no budget spent) and inspects those three.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import rerank                                  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    print("=" * 72 + "\nchunked rerank — does the order stay intact when asked in batches\n" + "=" * 72)
    cands = [{"name": "n%02d" % i, "description": "d", "body": "b"} for i in range(23)]
    real = rerank.score

    # ① do scores come back ★in the same slot★ as their candidate — verified by turning the name back into a score
    calls = []

    def spy(query, candidates, model="", prompt="", reserve=0, db=None, cache=True):
        calls.append([c["name"] for c in candidates])
        return [float(c["name"][1:]) for c in candidates]     # n07 → 7.0

    rerank.score = spy
    try:
        got = rerank.score_chunked("q", cands, size=5)
    finally:
        rerank.score = real
    check("★scores come back in the same slot as their candidate★ (no shift at a batch boundary)",
          got == [float(i) for i in range(23)],
          "first 6 received %s" % (got[:6] if got else got))
    check("the batch count is right (23 items, 5 at a time → 5 batches)", len(calls) == 5,
          "%d call(s) · last batch %d item(s)" % (len(calls), len(calls[-1]) if calls else 0))
    check("every candidate is asked ★exactly once, none skipped★",
          sorted(sum(calls, [])) == sorted(c["name"] for c in cands))

    # ② if the judge answers ★fewer★ than expected, does everything after stay unshifted
    def short(query, candidates, model="", prompt="", reserve=0, db=None, cache=True):
        return [9.0] * max(0, len(candidates) - 1)            # one short
    rerank.score = short
    try:
        got2 = rerank.score_chunked("q", cands, size=5)
    finally:
        rerank.score = real
    check("even with fewer answers from the judge, ★the length still matches the candidate count★",
          got2 is not None and len(got2) == len(cands),
          "length %s ↔ candidates %d" % (len(got2) if got2 else None, len(cands)))

    # ③ if one batch fails, ★the whole result is None★ — it isn't filled with 0.0
    state = {"n": 0}

    def flaky(query, candidates, model="", prompt="", reserve=0, db=None, cache=True):
        state["n"] += 1
        return None if state["n"] == 2 else [9.0] * len(candidates)
    rerank.score = flaky
    try:
        got3 = rerank.score_chunked("q", cands, size=5)
    finally:
        rerank.score = real
    check("★if one batch fails, the result is None★ — it doesn't mix up 'no answer' and 'couldn't ask'",
          got3 is None, "got %s" % ("None" if got3 is None else "a list"))

    # ④ when it's off, does it ask ★all at once★ (current behaviour unchanged)
    calls2 = []

    def spy2(query, candidates, model="", prompt="", reserve=0, db=None, cache=True):
        calls2.append(len(candidates))
        return [1.0] * len(candidates)
    rerank.score = spy2
    try:
        rerank.score_chunked("q", cands, size=0)
    finally:
        rerank.score = real
    check("size=0 (the default) means ★it doesn't split★ — current behaviour holds",
          calls2 == [len(cands)], "calls %s" % calls2)
    check("the default is off (it doesn't turn on before verification)", rerank.CHUNK == 0,
          "CHUNK=%d" % rerank.CHUNK)

    print("=" * 72)
    if FAIL:
        print("❌ %d short: %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ all passed — ★the order stays intact when asked in batches★")
    return 0


if __name__ == "__main__":
    sys.exit(main())
