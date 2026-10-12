#!/usr/bin/env python3
"""★is it actually better than the old way (MEMORY.md + grep)★ — a head-to-head comparison.

The user (2026-08-20): *"Our goal and purpose really is to build a brain that remembers well and
delivers good-quality content from memory. So it has to work better than the old file-based way —
reading and remembering memory.md and running grep to find things."*

So this check never measures the brain by ★its own yardstick★. It actually runs the old way, side by side.

## What is compared (same question · same files · ★same words★)

  A. grep, one word    scans with the single most discriminating-looking word in the question (what a human does)
  B. grep, several words  scans with every content word in the question, OR'd together (the old way, pushed for maximum recall)
  C. brain recall      the top 3 for ★the question alone★ — the same words grep is given
  D. brain hook        ★when nothing was asked at all★ — what gets attached automatically

## ⛔ Every method gets the same words (2026-10-12)

Until then C was the brain ★with the label's expansion words★ while A and B had the question alone,
and the headline read "brain 65% in 3 documents ↔ grep OR 82% in 222". Two things were wrong with it:

  · ★unequal information★ — the brain was handed words grep never saw. On the same 66 questions the
    brain's top 3 held the answer for 32% with the question alone and 65% with the expansion words.
  · ★the expansion words are the labelling lane★ — `short.json` says so itself: an answer is pointed
    at ★through recall with those words★ ("judged in the expansion lane, tested in the hook's lane").
    So recall-with-those-words reads its own labelling. 9 of the questions go from 0/9 to 9/9 with them.

So the verdict is drawn from the question alone, for every method. The expansion words are a second
lane given ★to both★ grep and the brain, and reported as an upper bound — never the verdict.

## What gets counted — was it found / how much has to be read / does it show up unasked

grep easily wins on "was the right file in the results" (if a word overlaps, everything gets caught).
So ★how many you'd have to read before hitting it★ is counted too — handing over 100 and saying
it's in there somewhere is not an answer. The hook attaches no ranking, only ★2 items★, so it is the strictest spot.

⛔ grep is not deliberately weakened (that would just measure my own imagination). Word extraction strips only
   particles/function words, ignores case, and matches substrings — the same meaning as real `grep -ril`.
"""
from __future__ import annotations

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # runs without PYTHONPATH too
from brain import evalinit as _ei  # noqa: E402 ★gold verdicts live in exactly one place★

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain import calibrate, hook, search, store   # noqa: E402

# Korean particles/function words + English function words — grep with these and everything gets caught (zero discriminating power)
STOP = set("""
그 이 저 것 수 등 및 또 또는 그리고 하지만 근데 지금 다시 좀 왜 어떻게 무엇 뭐 어디 언제
해줘 해줄 해야 하는 한다 했다 되나 되는 된다 있나 있는 있다 없나 없는 없다 같아 같은
확인 진행 작업 내용 문제 부분 경우 때문 통해 위해 대해 대한 관련 정리 알려 보고 다음
the and for with what how why when this that from into your our are was were will
should would could can may must not you it is be of to in on at as by or if do does
""".split())
MIN_TOK = 2


def tokens(q: str):
    raw = re.split(r"[^0-9A-Za-z가-힣_.#/-]+", q)
    out = []
    for t in raw:
        t = t.strip("._-#/")
        if len(t) < MIN_TOK or t.lower() in STOP or t in STOP:
            continue
        out.append(t)
    return out


def load_files():
    base = store.memory_dir()
    docs = {}
    for p in glob.glob(os.path.join(base, "*.md")):
        name = os.path.basename(p)[:-3]
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                docs[name] = fh.read()
        except OSError:
            continue
    return docs


def grep(docs, words):
    """substring · case-insensitive = the same meaning as `grep -ril`."""
    if not words:
        return []
    low = [w.lower() for w in words]
    hit = []
    for name, text in docs.items():
        t = text.lower()
        if any(w in t for w in low):
            hit.append(name)
    return hit


def cases():
    """Every labelled question this person has — from the one place the eval set lives (§evalinit.eval_dir,
    which honours BRAIN_EVAL_DIR), not a path of this repository's own."""
    out = []
    p1 = os.path.join(_ei.eval_dir(), "recall.json")
    if os.path.exists(p1):
        d = json.load(open(p1, encoding="utf-8"))
        out += [(c["q"], c["gold"], c.get("terms") or []) for c in d["cases"] if c.get("gold")]
    p2 = os.path.join(_ei.eval_dir(), "short.json")
    if os.path.exists(p2):
        d = json.load(open(p2, encoding="utf-8"))
        out += [(c["q"], c["gold"], c.get("terms") or [])
                for c in d.get("A_memory_needed", []) if c.get("gold")]
    return out


FULL = os.environ.get("BRAIN_SCORECARD_FULL", "") == "1"


def main() -> int:
    from tests import _needs
    _needs.private_index()                 # ⛔ calibrate.threshold may re-measure — on a copy, never the live index
    db = store.connect()
    thr = calibrate.threshold(db)
    docs = load_files()
    cs = cases()
    if not cs:
        # ⛔ a skip, not a pass — a green that compared nothing would read as "brain beats grep"
        from tests import _needs
        _needs.skip("no labelled questions in %s" % _ei.eval_dir(),
                    "`brain eval-init`, then pick the right answer for each question")

    print("=" * 78)
    print("the old way (grep) vs the brain — %d of the same questions · %d memory files" % (len(cs), len(docs)))
    print("=" * 78)

    stat = {k: {"hit": 0, "read": []} for k in ("A", "B", "C", "D", "BT", "CT")}
    rows = []
    for q, gold, terms in cs:
        toks = tokens(q)
        # A. one word — the longest one (closest to how a human would pick)
        one = [max(toks, key=len)] if toks else []
        ga = grep(docs, one)
        # B. every content word, OR'd
        gb = grep(docs, toks)
        # C. brain recall on ★the same words grep got★ — the question alone (is it in the top 3)
        rc = search.recall(db, q, k=8, log=False)
        direct = [r for r in rc if not r.get("related")]
        top3 = [r["name"] for r in direct[:3]]
        # D. the hook — what attaches automatically when nothing at all was asked
        # ⛔ ★must run the exact deployed pipeline★ (2026-08-25) — when the lexical path stays silent,
        #    the hook calls a 2-stage search (vector top-20 → judge). If this line only measures the lexical
        #    path, the scorecard comes out ★lower than reality★, and improvement never shows up in the numbers.
        #    The judge call is capped at 15/min and is slow, so it only turns on when BRAIN_SCORECARD_FULL=1.
        rh = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
        fired = [r["name"] for r in hook.select(rh, thr)]
        if not fired and FULL:
            fired = [r["name"] for r in search.judged(db, q)]

        for key, hit, read in (("A", any(_ei.gold_hit(gold, n) for n in ga), len(ga)),
                               ("B", any(_ei.gold_hit(gold, n) for n in gb), len(gb)),
                               ("C", any(_ei.gold_hit(gold, n) for n in top3), len(direct[:3])),
                               ("D", any(_ei.gold_hit(gold, n) for n in fired), len(fired))):
            if hit:
                stat[key]["hit"] += 1
            stat[key]["read"].append(read)
        if terms:
            # ★the label's expansion words, given to both★ — an upper bound, not the verdict (see the docstring)
            tt = toks + [t for w in terms for t in tokens(str(w)) if t not in toks]
            gt = grep(docs, tt)
            ct = [r["name"] for r in search.recall(db, q, k=8, extra_terms=terms, log=False)
                  if not r.get("related")][:3]
            for key, hit, read in (("BT", any(_ei.gold_hit(gold, n) for n in gt), len(gt)),
                                   ("CT", any(_ei.gold_hit(gold, n) for n in ct), len(ct))):
                if hit:
                    stat[key]["hit"] += 1
                stat[key]["read"].append(read)
        rows.append((q[:30], gold[:34], len(ga), len(gb),
                     any(_ei.gold_hit(gold, n) for n in ga), any(_ei.gold_hit(gold, n) for n in gb),
                     any(_ei.gold_hit(gold, n) for n in top3), any(_ei.gold_hit(gold, n) for n in fired)))

    def med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else 0

    n = len(cs)
    label = {"A": "grep, one word    ", "B": "grep, OR'd words  ",
             "C": "brain recall(top-3)", "D": "brain hook(auto, 2)"}
    print()
    print("  ★the question alone★ — every method gets the same words (the verdict below)")
    print("  method                hit rate   docs to read (median)")
    print("  " + "-" * 62)
    for k in ("A", "B", "C", "D"):
        s = stat[k]
        print("  %s  %2d/%d (%3.0f%%)   %d"
              % (label[k], s["hit"], n, 100.0 * s["hit"] / n, med(s["read"])))
    nt = len(stat["CT"]["read"])
    if nt:
        print()
        print("  ★with the label's expansion words, given to both★ — %d question(s) that have them" % nt)
        for k, lab in (("BT", "grep, OR'd + words "), ("CT", "brain recall(top-3)")):
            s = stat[k]
            print("  %s  %2d/%d (%3.0f%%)   %d"
                  % (lab, s["hit"], nt, 100.0 * s["hit"] / nt, med(s["read"])))
        print("  ⚠️ an upper bound for both, never the verdict — the answers were pointed at through recall")
        print("     with these same words (the labelling lane), so this lane partly reads its own labelling")

    print()
    print("  ★the same hit rate is not the same answer if the amount to read differs★")
    if not FULL:
        print("  ⚠️ the automatic row is measured on ★the lexical path alone★ — the deployed hook calls a")
        print("     2-stage search when it goes silent. Measure the whole thing with BRAIN_SCORECARD_FULL=1 (slow — the judge is capped at 15/min)")
    print("  grep only works when it is called — column D is a capability the old way never had at all")
    print("  (it attaches even when unasked · so grep's automatic hit rate is 0 by definition in this table)")

    # ⛔ r[5] is "grep OR found the answer", r[3] only how many files it returned (any at all) — until
    #    2026-10-12 this read r[3], so the list counted every question grep returned anything for.
    worst = [r for r in rows if r[5] and not r[6]]
    if worst:
        print()
        print("  ⚠️ %d question(s) grep OR found but the brain's top 3 didn't:" % len(worst))
        for r in worst[:6]:
            print("     %-30s gold %-34s grep had to scan %d" % (r[0], r[1], r[3]))

    # ⛔⛔ ★never compare each axis against a different opponent★ (caught 2026-08-28)
    #    The old verdict did this: hit rate compared against A (grep, one word), reading amount against B (grep OR).
    #    ★picking whichever opponent is favourable★ for each axis and drawing the one-line conclusion "reads less, finds more."
    #    This repo has already had this exact illness once — the scorecard measured the gain with 2-stage
    #    search on, and the cost with it off ([[lesson_i_measured_the_gain_with_one_pipeline...]]).
    #    ★Both axes must be asked of the same opponent.★ Do that and it turns out neither side dominates —
    #    that comes out, and that is the honest state right now.
    def cmp(other, label):
        dh = stat["C"]["hit"] - stat[other]["hit"]
        dr = med(stat["C"]["read"]) - med(stat[other]["read"])
        return {"label": label, "d_hit": dh, "d_read": dr,
                "wins_hit": dh >= 0, "wins_read": dr <= 0}
    vsA, vsB = cmp("A", "grep, one word"), cmp("B", "grep, OR'd words")
    print()
    print("=" * 78)
    print("★asks the same opponent both axes★ (never picks a favourable opponent per axis)")
    for c in (vsA, vsB):
        print("  vs %-14s hit %+3d %s · docs read %+5d %s"
              % (c["label"], c["d_hit"], "✅" if c["wins_hit"] else "❌",
                 c["d_read"], "✅" if c["wins_read"] else "❌"))
    dom = [c for c in (vsA, vsB) if c["wins_hit"] and c["wins_read"]]
    ok = bool(dom) and stat["D"]["hit"] > 0
    if ok:
        print("verdict: pass ✅ — beats %s on both axes" % dom[0]["label"])
    else:
        print("verdict: ⛔ ★no opponent is beaten on both axes★ — each one wins on one of them (above).")
        print("      the public bar (\"reads less than grep and matches as well or better\") is ★not there yet★.")
    print("⛔ this check only compares 'when it is called'. The old way's real limit was somewhere else —")
    print("   MEMORY.md hit its 25KB cap and 205 of 434 (47%) fell outside the index,")
    print("   pushed out, and those files might as well not have existed — ★because nobody ever called them★.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
