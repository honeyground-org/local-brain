#!/usr/bin/env python3
"""Retrieval fusion — the experiment fixed in docs/notes/retrieval-fusion-plan.md, on your own labelled sample.

Arms (what the judge is given per question):

    A  meaning 20                          · description + head        (what the hook does today)
    B  meaning 20                          · description + evidence    (the passage that matched)
    C  words 20 ∪ meaning 20               · description + head
    D  words 20 ∪ meaning 20               · description + evidence
    R  meaning 20                          · description + a passage at a random place in the same
                                             document, same length (fixed seed) — the evidence rule's control

Judge-free controls (no calls): words top 3 · meaning top 3 · reciprocal-rank fusion (k = 60) top 3 ·
the union in its input order (min of the two ranks) top 3.

⛔ This spends judge calls (about one per question and arm). It stops before the hook's reserve
   (`rerank.RESERVE`) and picks up where it stopped on the next run — the cache holds every answer, so a
   re-run of a finished arm is free. The calls go in a seeded shuffle of (question, arm), so an arm is not
   tied to a day.

⛔ It runs on a ★snapshot★ of the index taken on the first run (`--snapshot`, default under the brain's
   home), with the local vector scan forced, so a run spread over two days measures one corpus: the hook
   keeps editing the live index (reindexing, the lexicon it learns) in between. The judge's daily count
   stays shared with the hook (§rerank.ledger_path).

Usage:
    PYTHONPATH=. python3 tests/measure_retrieval_fusion.py            # controls, cache state, calls needed
    PYTHONPATH=. python3 tests/measure_retrieval_fusion.py --run      # ask the judge until the reserve
    PYTHONPATH=. python3 tests/measure_retrieval_fusion.py --arms A,B --run
    PYTHONPATH=. python3 tests/measure_retrieval_fusion.py --run --max 150   # leave the day's other jobs their share

The report: per arm Hit@1 · Hit@3 · MRR, answer among the candidates, Hit@3 given that, errors (a failed
call, an answer that left candidates unscored), and the paired difference against the best judge-free
control with a 95% interval (bootstrap over questions, fixed seed). Short and long questions apart.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ARMS = ("A", "B", "C", "D", "R")
SEED = 20261013
K = 20


def _snapshot(path: str) -> str:
    """Point this process at a frozen copy of the index — made from the live one the first time."""
    from brain import evalinit, rerank, store
    os.environ.setdefault("BRAIN_CONFIG", store.default_config_path())
    os.environ.setdefault("BRAIN_EVAL_DIR", evalinit.eval_dir())
    os.environ.setdefault("BRAIN_LEDGER", rerank.ledger_path())
    os.environ["BRAIN_VECTOR_STORE"] = "sqlite"           # the local scan — the copy is what is measured
    os.environ["BRAIN_GRAPH_STORE"] = "sqlite"
    live = store.db_path()
    os.makedirs(path, exist_ok=True)
    os.environ["BRAIN_HOME"] = path
    copy = store.db_path()
    if not os.path.exists(copy):
        src, dst = sqlite3.connect(live), sqlite3.connect(copy)
        src.backup(dst)
        dst.close()
        src.close()
        print("snapshot taken: %s" % copy)
    return copy


def _cases():
    from brain import evalinit as _ei
    out = []
    p = os.path.join(_ei.eval_dir(), "short.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        out += [(c["q"], c["gold"], "short") for c in d.get("A_memory_needed", [])
                if isinstance(c, dict) and c.get("gold")]
    p = os.path.join(_ei.eval_dir(), "recall.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        out += [(c["q"], c["gold"], "long") for c in d.get("cases", []) if c.get("gold")]
    return out


def _inputs(db, q):
    """Candidate lists per question: meaning 20 · words 20 · the union in min-rank order."""
    from brain import search, vectors
    vec = vectors.search(db, q, k=K)
    ids = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM docs")}
    words = [r for r in search.recall(db, q, k=K, log=False) if not r.get("related")][:K]
    w_ids = [ids[r["name"]] for r in words if r["name"] in ids]
    v_ids = [d for d, _, _ in vec]
    inf = float("inf")
    rank = {}
    for i, d in enumerate(w_ids):
        rank.setdefault(d, [inf, inf])[0] = i
    for i, d in enumerate(v_ids):
        rank.setdefault(d, [inf, inf])[1] = i
    union = sorted(rank, key=lambda d: (min(rank[d]), d))
    cos = {d: (c, r) for d, c, r in vec}
    return {"vector": vec, "union": [(d, cos.get(d, (None, None))[0], cos.get(d, (None, None))[1])
                                     for d in union],
            "v_ids": v_ids, "w_ids": w_ids}


def _random_passages(db, q, rows, found, rnd):
    """R: for each candidate that has an evidence passage, the same length at another place of the same body."""
    out = []
    for (row, _, _), ev in zip(rows, found):
        body = (row["body"] or "").strip()
        if ev is None:
            out.append(None)
            continue
        n = ev["end"] - ev["start"]
        spots = [s for s in range(0, max(1, len(body) - n + 1), 20)
                 if s + n <= ev["start"] or s >= ev["end"]] or [0]
        s = rnd.choice(spots)
        out.append(body[s:s + n])
    return out


def _arm_candidates(db, q, arm, inp, rnd):
    from brain import search
    hits = inp["vector"] if arm in ("A", "B", "R") else inp["union"]
    excerpt = "evidence" if arm in ("B", "D", "R") else "head"
    rows, cands, found = search.judge_inputs(db, q, hits, excerpt,
                                             vector_ids=inp["v_ids"] if arm in ("C", "D") else None)
    if arm == "R":
        for c, alt in zip(cands, _random_passages(db, q, rows, found, rnd)):
            if alt is not None:
                c["excerpt"] = alt
    return rows, cands, found


def _metrics(rank_of_gold):
    n = len(rank_of_gold)
    h1 = sum(1 for r in rank_of_gold if r is not None and r < 1)
    h3 = sum(1 for r in rank_of_gold if r is not None and r < 3)
    mrr = sum(1.0 / (r + 1) for r in rank_of_gold if r is not None) / max(1, n)
    return h1, h3, mrr


def _paired_ci(a, b, rnd, reps=2000):
    """95% interval of mean(a − b) over questions, by bootstrap with a fixed seed."""
    d = [x - y for x, y in zip(a, b)]
    if not d:
        return 0.0, 0.0, 0.0
    means = sorted(sum(rnd.choice(d) for _ in d) / len(d) for _ in range(reps))
    return sum(d) / len(d), means[int(0.025 * reps)], means[int(0.975 * reps) - 1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="ask the judge (spends calls, stops at the reserve)")
    ap.add_argument("--arms", default="A,B,C,D,R")
    ap.add_argument("--snapshot", default="")
    ap.add_argument("--max", type=int, default=0,
                    help="at most this many judge calls in this run (0 = until the reserve) — to leave the "
                         "day's other jobs their share")
    a = ap.parse_args()
    arms = [x for x in a.arms.split(",") if x in ARMS]

    from brain import store as _store
    snap_dir = a.snapshot or os.path.join(_store.brain_home(), "measure", "retrieval-fusion")
    _snapshot(snap_dir)

    from brain import evalinit as _ei
    from brain import lexicon, rerank, search, store
    lexicon.learn_from_vector = lambda *x, **k: None      # ⛔ frozen during the experiment
    rerank.MAX_CAND = 2 * K                                # ⛔ this process only: the union is judged whole

    cases = _cases()
    if not cases:
        print("⏭ skipped — needs your own labelled sample (`brain eval-init`)")
        return 77
    db = store.connect()
    rnd = random.Random(SEED)
    inputs = [_inputs(db, q) for q, _, _ in cases]
    names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM docs")}

    def gold_rank(gold, ordered_names):
        return next((i for i, n in enumerate(ordered_names) if _ei.gold_hit(gold, n)), None)

    # ── judge-free controls ──────────────────────────────────────────────────
    ctrl = {"words": [], "meaning": [], "rrf": [], "union-order": []}
    for (q, gold, _), inp in zip(cases, inputs):
        ctrl["words"].append(gold_rank(gold, [names[d] for d in inp["w_ids"]]))
        ctrl["meaning"].append(gold_rank(gold, [names[d] for d in inp["v_ids"]]))
        rrf = {}
        for lst in (inp["w_ids"], inp["v_ids"]):
            for i, d in enumerate(lst):
                rrf[d] = rrf.get(d, 0.0) + 1.0 / (60 + i + 1)
        ctrl["rrf"].append(gold_rank(gold, [names[d] for d in sorted(rrf, key=lambda d: (-rrf[d], d))]))
        ctrl["union-order"].append(gold_rank(gold, [names[d] for d, _, _ in inp["union"]]))

    # ── the arms ─────────────────────────────────────────────────────────────
    tasks = [(i, arm) for i in range(len(cases)) for arm in arms]
    random.Random(SEED).shuffle(tasks)
    built = {}
    for i, arm in tasks:
        built[(i, arm)] = _arm_candidates(db, cases[i][0], arm, inputs[i], random.Random(SEED + i))
    results = {arm: [None] * len(cases) for arm in arms}
    errors = {arm: [] for arm in arms}
    stopped, spent, missing = "", 0, 0
    for i, arm in tasks:
        q, gold, _ = cases[i]
        rows, cands, found = built[(i, arm)]
        if not cands:
            results[arm][i] = ("none", [])
            continue
        sc = rerank.cached(q, cands, db)
        shape = None
        if sc is None:
            if not a.run or stopped:
                missing += 1
                continue
            b = rerank.budget(db)
            if a.max and spent >= a.max:
                stopped = "--max %d" % a.max
                missing += 1
                continue
            if b["wall"] or (b["left"] is not None and b["left"] <= rerank.RESERVE):
                stopped = "the hook's reserve (%s of %s left)" % (b["left"], b["limit"])
                missing += 1
                continue
            sc = rerank.score(q, cands, db=db, cache=True, reserve=rerank.RESERVE)
            spent += not rerank.last_was_cached()
            if sc is None:
                why = rerank.last_failure() or "?"
                if b["left"] is not None and rerank.budget(db)["left"] <= rerank.RESERVE:
                    stopped, missing = why, missing + 1
                    continue
                errors[arm].append((i, "failed: " + why[:80]))
                results[arm][i] = ("error", [])
                continue
            shape = rerank.last_answer_shape()
        if shape and (shape["answered"] < shape["n"] or shape["duplicates"] or shape["out_of_range"]):
            errors[arm].append((i, "answer shape %s" % json.dumps(shape)))
        order = sorted(range(len(sc)), key=lambda j: (-sc[j], j))      # ties: the input order
        results[arm][i] = ("ok", [rows[j][0]["name"] for j in order])

    # ── report ───────────────────────────────────────────────────────────────
    n = len(cases)
    groups = {"all": range(n), "short": [i for i in range(n) if cases[i][2] == "short"],
              "long": [i for i in range(n) if cases[i][2] == "long"]}
    print("=" * 78)
    print("retrieval fusion · %d labelled questions · judge %s · snapshot %s"
          % (n, rerank.answering(db) or "?", snap_dir))
    print("=" * 78)
    for g, idx in groups.items():
        if not idx:
            continue
        print("\n%s (%d)" % (g, len(idx)))
        print("  %-14s %8s %8s %6s" % ("", "Hit@1", "Hit@3", "MRR"))
        for k, ranks in ctrl.items():
            h1, h3, mrr = _metrics([ranks[i] for i in idx])
            print("  %-14s %8d %8d %6.3f   (no judge)" % (k, h1, h3, mrr))
        for arm in arms:
            done = [i for i in idx if results[arm][i] is not None]
            ranks = [gold_rank(cases[i][1], results[arm][i][1]) for i in done]
            h1, h3, mrr = _metrics(ranks)
            in_c = sum(1 for i in done if any(_ei.gold_hit(cases[i][1], r[0]["name"])
                                               for r in built[(i, arm)][0]))
            print("  arm %-10s %8d %8d %6.3f   answered %d/%d · answer among candidates %d · errors %d"
                  % (arm, h1, h3, mrr, len(done), len(idx), in_c,
                     sum(1 for i, _ in errors[arm] if i in set(idx))))
    best = max(ctrl, key=lambda k: _metrics(ctrl[k])[1])
    print("\npaired Hit@3 against the best judge-free control (%s), on the questions each arm answered:" % best)
    for arm in arms:
        idx = [i for i in range(n) if results[arm][i] is not None and results[arm][i][0] == "ok"]
        if not idx:
            continue
        x = [1 if (lambda r: r is not None and r < 3)(gold_rank(cases[i][1], results[arm][i][1])) else 0
             for i in idx]
        y = [1 if ctrl[best][i] is not None and ctrl[best][i] < 3 else 0 for i in idx]
        m, lo, hi = _paired_ci(x, y, random.Random(SEED))
        print("  %s − %s: %+.1f points (95%% %+.1f … %+.1f) over %d" % (arm, best, 100 * m, 100 * lo,
                                                                     100 * hi, len(idx)))
    for arm in arms:
        for i, why in errors[arm][:5]:
            print("  ⚠ %s q%d %s" % (arm, i, why))
    print("\njudge calls this run %d · still to ask %d · budget %s%s" % (
        spent, missing, json.dumps(rerank.budget(db), ensure_ascii=False),
        (" · ⛔ stopped: %s — run again after the reset" % stopped) if stopped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
