"""Judge reproducibility — measures ★is it a function of candidate count★ as a curve. (a measurement script, not a check)

## Why this is measured

The 2-stage search's judge is ★non-deterministic★. `temperature=0`, same query, same candidates, yet
the top score wobbled and firing dropped to 1/5 in one observation (2026-08-25). The reasoning it had
rested on — "candidates are top-20" — assumed ★a dedicated reranker (cross-encoder)★, but we hand a
general-purpose LLM 20 of them ★as one single list★ — the candidates interfere with each other.

⛔ What was measured then was only ★20 vs 5, 2 queries★. Without knowing what's in between (10),
   there's no way to decide "how many to split it into." The cost gap between batching by 4 (4x the
   calls) and by 2 (2x) is large, and the daily cap (§rerank.RPD = 500/day) forces that choice.

## What this measures

The same query repeated R times, watched per candidate count N for ①the spread/stdev of the top score
②whether firing/silence flips ③whether the #1 candidate stays the same.

⛔ ★queries are drawn only from the real input distribution★ — since the judge is only called when the
   lexical path stays silent (§hook.fallback), only queries that pass that condition are used. Hand-picking
   would measure imagination instead (this repo has been burned by that trap twice).

## How to use

    PYTHONPATH=. R=5 NS=5,10,20 python3 tests/measure_rerank_stability.py

Read-only — writes no memory, no rule, no threshold (only the budget counter goes up).
⛔ The score cache is called ★off★ (`cache=False`) — turn it on and the measurement reads its own answer.
"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import calibrate, hook, rerank, search as bsearch, store, vectors  # noqa: E402
from brain import evalinit as _ei  # ★gold verdicts live in exactly one place★

R = int(os.environ.get("R", "5") or 5)
NS = [int(x) for x in (os.environ.get("NS", "5,10,20") or "5,10,20").split(",")]
PER_LABEL = int(os.environ.get("PER_LABEL", "3") or 3)
OUT = os.environ.get("OUT", "")


def main() -> int:
    db = store.connect()
    floor = rerank.min_score(db)
    b = rerank.budget(db)
    print("threshold %.1f · model %s · candidate cap %d" % (floor, rerank.MODEL, rerank.MAX_CAND))
    print("daily budget %d/%d · %d left%s"
          % (b["used"], b["limit"], b["left"], "  ⛔wall" if b["wall"] else ""))
    if b["wall"]:
        # ⛔ ★never measures against the wall★ — count a failure as a score of 0 and it produces
        #    the false conclusion that "the judge is useless." This repo has come right up to that edge before.
        print("⛔ today's share is used up — measure again after Pacific midnight (`brain budget`).")
        return 2

    ev = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "eval", "short.json"), encoding="utf-8"))

    def qs(key):
        return [r if isinstance(r, str) else r["q"] for r in ev[key]]

    GOLD = {r["q"]: r["gold"] for r in ev["A_memory_needed"] if isinstance(r, dict)}

    min_score = calibrate.threshold(db)

    def word_silent(q):
        return not hook.select(bsearch.recall(db, q, k=hook.MAX_ITEMS + 2, log=False),
                               min_score)

    picked = []
    for label, pool in (("A", qs("A_memory_needed")), ("C", qs("C_no_memory_needed"))):
        n = 0
        for q in pool:
            if n >= PER_LABEL:
                break
            if not word_silent(q):
                continue
            try:
                hits = vectors.search(db, q, k=max(NS))
            except Exception:                            # noqa: BLE001
                continue
            cands = []
            for did, cos, ratio in hits:
                row = bsearch._hydrate(db, did)
                if row is None or row["name"] in bsearch._always_in_context():
                    continue
                cands.append({"name": row["name"], "description": row["description"],
                              "body": (row["body"] or "")[:500]})
            if len(cands) < max(NS):                     # too few candidates and the N comparison breaks
                continue
            picked.append((label, q, cands))
            n += 1

    need = len(picked) * len(NS) * R
    print("\n%d queries (%d each for A/C · lexical-silent + %d candidates secured) · %d repeats"
          % (len(picked), PER_LABEL, max(NS), R))
    print("%d calls planned — at %d/min that's about %.0fmin, %.0f%% of the remaining daily share"
          % (need, rerank.RPM, need / float(rerank.RPM), 100.0 * need / max(1, b["left"])))
    if need > b["left"]:
        print("⛔ bigger than the remaining daily share (%d) — reduce R or NS." % b["left"])
        return 2
    for lab, q, _ in picked:
        print("  [%s] %s" % (lab, q[:56]))
    print()

    rows = []
    for lab, q, cands in picked:
        for n in NS:
            tops, args, fails = [], [], 0
            for _ in range(R):
                # ⛔⛔ ★turn the cache back on and this measurement lies to itself.★ Repeating the same
                #    input R times is this script's entire point — read the cache and all R runs come
                #    back the same value, producing the false conclusion "it reproduces perfectly."
                s = rerank.score(q, cands[:n], db=db, cache=False)
                if s is None:                            # ⛔ a failure is not a score of 0
                    fails += 1
                    continue
                i = max(range(len(s)), key=lambda j: s[j])
                tops.append(s[i])
                args.append(cands[i]["name"])
            if not tops:
                print("  [%s] n=%-2d every call failed — %s"
                      % (lab, n, rerank.last_failure()[:60]))
                continue
            fire = sum(1 for t in tops if t >= floor)
            agree = max(args.count(a) for a in set(args)) / float(len(args))
            rng = max(tops) - min(tops)
            sd = statistics.pstdev(tops) if len(tops) > 1 else 0.0
            flip = fire not in (0, len(tops))            # ★firing/silence flipped★
            top_name = max(set(args), key=args.count)
            g = GOLD.get(q, "")
            rows.append(dict(label=lab, q=q, n=n, tops=tops, fire=fire, R=len(tops),
                             agree=agree, rng=rng, sd=sd, fails=fails, flip=flip,
                             top_name=top_name, gold=g,
                             gold_top=bool(_ei.gold_hit(g, top_name))))
            print("  [%s] n=%-2d %-30s range %.1f sd %.2f fire %d/%d top-match %3.0f%% %s%s"
                  % (lab, n, str(tops), rng, sd, fire, len(tops), agree * 100,
                     "★flipped★" if flip else "", ("  fail%d" % fails) if fails else ""))

    # ⛔⛔ ★watch only the wobble and silence wins★ (this actually happened, measured 2026-08-26)
    #    The first pass's summary gave only the top score's spread/stdev, and on that scale n=20 was
    #    the most "stable." Opening it up, that stability turned out to be ★not moving near a score of
    #    0★ — the same query picked the right answer #1 but scored it low, so firing died 5/5 → 0/5.
    #    ★Stable ≠ good.★ So the summary reports the ★quality★ of the decision first.
    #    · The same trap: watching only the firing rate and never the gold rank (§verify_short's last line)
    print("\n=== summary by candidate count — ★decision quality first, wobble second★ ===")
    print("  N   gold_over_threshold  gold_but_silent  control_false_fire  decision_flip   range   sd")
    for n in NS:
        rs = [r for r in rows if r["n"] == n]
        if not rs:
            continue
        A = [r for r in rs if r["label"] == "A"]
        C = [r for r in rs if r["label"] == "C"]
        goldA = [r for r in A if r["gold_top"]]
        win = sum(1 for r in goldA if r["fire"] == r["R"])       # picked it and cleared every time
        lost = sum(1 for r in goldA if r["fire"] == 0)           # picked it and never once cleared
        noise = sum(1 for r in C if r["fire"] > 0)
        print("  %-3d      %2d/%-2d          %2d          %2d/%-2d        %2d/%-2d   %4.2f %4.2f"
              % (n, win, len(A), lost, noise, len(C),
                 sum(1 for r in rs if r["flip"]), len(rs),
                 statistics.mean(r["rng"] for r in rs),
                 statistics.mean(r["sd"] for r in rs)))
    print("\n⛔ ★'stable' can mean 'silent'★ — never decide candidate count from range/sd alone.")
    print("⛔ claiming an improvement requires ★a change bigger than the wobble range★.")
    print("⚠️ a gold label accepts only one right answer — being 'outside gold' does not make it wrong.")
    print("daily budget %s" % json.dumps(rerank.budget(db), ensure_ascii=False))
    if OUT:
        json.dump(rows, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("raw data %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
