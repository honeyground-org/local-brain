"""Scorecard-honesty check — ★does a measurement read its own answer★.

## Why this check exists (measured 2026-08-31)

The accuracy axis measures "does it say there's no answer when there isn't one" with an unlabelled
control group. But that call had ★the judge cache turned on★. The result:

    cache on  : 25 queries · accuracy 88.0% · ★budget 0★  ← every answer it had already given before
    cache off : 25 queries · accuracy 76.0% · budget 25
    cache off again : ★88.0%★                          ← same input, but split by 12pp

⛔ Seeing this 88% come out the same twice, I nearly wrote in the WORKPLAN ★"it reproduced —
   the fixed sample works"★. What reproduced was not performance but ★the cache★.

★A control group is almost always a 'first encounter' in real use★ — a person doesn't repeat the
same small talk. So a value measured with the cache on is not a real-use value.

And that 12pp wobble is exactly what the ★reproducibility axis★ was supposed to measure. The two axes
share the same root, so now they're produced together in ★a single pass★.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("BRAIN_JUDGE_PROVIDER", "gemini")  # intercepted below — no network, but a judge must be chosen
from brain import rerank, scorecard, store                # noqa: E402
from tests import _needs                                  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    print("=" * 72 + "\nscorecard — does a measurement read its own answer\n" + "=" * 72)

    # ── intercepts and inspects ★how it's actually called★ (no network, no budget spent) ──
    seen = []
    real = rerank.score

    def spy(query, candidates, model="", prompt="", reserve=0, db=None, cache=True):
        seen.append(cache)
        return [9.0] * len(candidates)                # mimics a made-up answer

    _needs.labelled_sample()                              # accuracy is measured on ★your★ questions
    rerank.score = spy
    try:
        db = store.connect()
        raw = scorecard.collect(db, cheap=False)
    finally:
        rerank.score = real

    check("the accuracy measurement actually calls the judge", bool(seen), "%d call(s)" % len(seen))
    check("★every call turns the cache off★ (the measurement doesn't read its own answer)",
          seen and not any(seen), "calls made with cache=True: %d" % sum(1 for c in seen if c))

    # ── does repetition actually happen — reproducibility can only be measured with R>1 ──────────
    reps = int(os.environ.get("BRAIN_SCORE_REPEATS", "3") or 3)
    check("★asks the same query several times★ (only R>1 can measure 'did it split')", reps > 1,
          "R=%d" % reps)
    check("call count is R times the query count", len(seen) % reps == 0,
          "calls %d · R %d" % (len(seen), reps))

    # ── both axes come out of the same pass ───────────────────────────────────────
    check("accuracy and reproducibility ★both★ get filled in (no cell is left unmeasured)",
          raw.get("precision") is not None and raw.get("stability") is not None,
          "precision %s · stability %s" % (raw.get("precision"), raw.get("stability")))

    # ── the composite doesn't count an unmeasured axis as 0 ────────────────────────
    r = scorecard.compute({k: None for _, k, _, _ in scorecard.AXES})
    check("⛔ if everything is unmeasured, the composite is ★absent, not 0★",
          r.get("total") is None, "total %s" % r.get("total"))

    # ── ★a value from an earlier run may be shown, but never as a fresh one★ (2026-09-28) ──
    #
    #    Opening the dashboard must not spend judge budget, so the remote axes come back empty and
    #    the screen said "not measured" — with that morning's full measurement sitting in the
    #    history. On screen 65.9 against a measured 73.5. Two numbers for one thing is how a screen
    #    stops being believed, so the value is carried over ★and labelled with the day it was taken★.
    import json as _json                                  # noqa: E402
    import tempfile as _tf                                # noqa: E402
    import time as _time                                  # noqa: E402
    from brain import scorecard as _sk                     # noqa: E402

    def _hist(days_old, path):
        at = _time.strftime("%Y-%m-%dT%H:%M:%S",
                            _time.localtime(_time.time() - days_old * 86400))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_json.dumps({"at": at, "raw": {"precision": 0.9, "stability": 0.8}}) + "\n")
        return at

    with _tf.TemporaryDirectory() as tmp:
        hp = os.path.join(tmp, "scorecard.jsonl")
        real_history = _sk.history
        _sk.history = lambda path="", limit=10: [
            _json.loads(l) for l in open(hp, encoding="utf-8") if l.strip()][-limit:]
        try:
            at = _hist(1, hp)
            raw, carried, when = _sk.carry_remote({"recall": 0.4, "precision": None,
                                                   "stability": None})
            check("★both remote axes are carried★ — they come out of one pass, so one of them "
                  "alone was never a principled split",
                  sorted(carried) == ["precision", "stability"], str(carried))
            check("  and it hands back ★when★ (so the screen can say so)", when == at, when)
            check("  the values really land in raw", raw["precision"] == 0.9 and raw["stability"] == 0.8)

            # ⛔ control ① — a value measured in this pass must never be overwritten
            raw2, carried2, _w = _sk.carry_remote({"precision": 0.5, "stability": None})
            check("★control★: a fresh value wins over a stored one",
                  raw2["precision"] == 0.5 and carried2 == ["stability"], str(carried2))

            # ⛔ control ② — too old is back to honestly unmeasured, not quietly carried
            _hist(_sk.CARRY_MAX_DAYS + 1, hp)
            raw3, carried3, when3 = _sk.carry_remote({"precision": None, "stability": None})
            check("★control★: past %.0f days nothing is carried (it is not today's brain)"
                  % _sk.CARRY_MAX_DAYS,
                  carried3 == [] and raw3["precision"] is None and when3 == "", str(carried3))

            # ⛔ control ③ — no history at all must not raise and must not invent
            _sk.history = lambda path="", limit=10: []
            raw4, carried4, _w4 = _sk.carry_remote({"precision": None, "stability": None})
            check("★control★: with no history it stays empty and does not fail", carried4 == [])
        finally:
            _sk.history = real_history

    # ⛔ ★one implementation★ — the classic screen used to carry the value over with its own inline
    #    copy, and filled precision only. Both screens must read the same rule.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dash = open(os.path.join(root, "brain", "dashboard.py"), encoding="utf-8").read()
    import re as _re                                      # noqa: E402
    calls = len(_re.findall(r"\b_?sk\.carry_remote\(", dash))
    check("both screens call ★the shared rule★, neither re-implements it",
          calls == 2 and 'row["_from"]' not in dash, "%d call site(s)" % calls)
    view = open(os.path.join(root, "brain", "dashview.py"), encoding="utf-8").read()
    check("★the screen marks a carried axis★ instead of drawing it like a fresh one",
          'a.get("carried")' in view and "dash.carried" in view)

    print("=" * 72)
    if FAIL:
        print("❌ %d short: %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ all passed — ★the measurement doesn't read the cache, and doesn't hide a wobble★")
    return 0


if __name__ == "__main__":
    sys.exit(main())
