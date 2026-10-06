"""Judge precision — measures ★whether the control group and the right answers diverge★, per prompt variant.

## Why this is needed (measured 2026-08-31)

Two judges, same model and same code, with opposite separability:

    behaviour layer  positive 8~10 ↔ negative 0~2   ★gap 6★      prompt: "would skipping this cause an incident"
    recall layer     ★9 of 35 (26%) control cases score 8+★      prompt: "is it relevant and helpful"

⛔ ★Ask about relevance and a relevance score comes back.★ "880 is not our work" scoring 10 on an admin
   audit document is the proof — the topic is nearby, but it is not the answer.

So this harness measures ★swapping the prompt against the same set★. What is changed is one prompt;
everything else (candidates, queries, threshold, call contract) is fixed.

## The metrics — ★looked at as three, together★

    ① control false-fire   the share of talk needing no memory that crosses the threshold   ← lower is better
    ② positive hit rate    the share where gold is chosen 1st and crosses the threshold      ← higher is better
    ③ ★the gap★           lowest positive (among hits) − highest control                     ← this is where the threshold should stand

⛔ Watch ① alone and "the judge that attaches nothing" wins (the trap where silence takes first place —
   this repository has already been burnt by it in reproducibility measurement). Watch ② alone and noise wins. ★Look at all three together.★

## Usage

    PYTHONPATH=. python3 tests/measure_judge_precision.py            # the current prompt
    PYTHONPATH=. VARIANT=v1 python3 tests/measure_judge_precision.py # a variant

⛔ Leave the cache on — ★changing the prompt changes the cache key★, so variants never mix,
   and re-measuring the same variant is free (and returns the same value = it reproduces).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import rerank, search as bs, store, vectors      # noqa: E402
from brain import evalinit as _ei  # ★gold judging lives in exactly one place★

# ── prompt variants ─────────────────────────────────────────────────────────
# ⛔ A variant changes ★one axis at a time★. Change several together and you cannot tell which one worked.
VARIANTS = {}

VARIANTS["v0"] = rerank._PROMPT          # the current deployed version (baseline · cached, so free)

# en — measured 2026-10-06 against the Korean original and adopted as v0 (the numbers are in
#   rerank._PROMPT's comment). The Korean original is in git history.
VARIANTS["en"] = rerank._PROMPT

# v1 — ★transplants three things measured effective on the behaviour layer, unchanged★
#   ① removes the "related" band → compresses the question to ★is it the answer★ alone
#   ② names ★the most common wrong answer★ and gives an example
#   ③ enumerates continuation phrases and instructions ★concretely★ (the old prompt had only two examples)
VARIANTS["v1"] = """당신은 검색 판정자입니다. 물음은 ★하나★ 입니다:

  ★이 사람이 방금 한 말에, 이 기억이 ‘지금 읽어야 할 답’ 인가?★

- 10   : 이 기억이 그 말의 직접적인 답이거나, 모르고 진행하면 사고가 나는 규칙이다
- 6~9  : 그 말에 직접 걸리는 주의사항이다
- 3~5  : ★주제는 근처지만 답은 아니다★
- 0~2  : 답이 아니다

⛔ 가장 흔한 오답: ★주제가 겹친다는 이유로 점수를 주는 것★ 입니다.
   "880은 우리 작업이 아니야" 는 업무 이야기지만, 어드민 화면을 설명하는 문서는
   그 말의 답이 아닙니다 → 0~2.
⛔ 그 낱말이 후보에 나온다는 이유로 점수를 주지 마세요.

⛔ ★사용자의 말이 그저 이어서 하라는 지시·잡담이면 후보가 무엇이든 전부 0~2 입니다.★
   그런 말의 예: "이어서 해줘" · "계속 진행해" · "다시 해줘" · "확인해줘" · "정리해줘"
   · "커밋해" · "머지중" · "이건 우리 작업이 아니야" · "다시 한번만 보여줘"
   이런 말에는 ★어떤 문서도 답이 아닙니다.★ 억지로 고르지 마세요.

JSON 배열만 출력: [{"i": 0, "s": 7}, {"i": 1, "s": 2}, ...]

사용자의 말:
%s

후보:
%s"""


# v2 — ★no quote, no score★ (the 2026-08-31 diagnosis pointed here)
#
#   v0 and v1 failed on ★the same query★. Given a list with the right answer removed from the candidates
#   (= a list with no answer), the judge cannot say "there is none" and instead picks the nearest topic and gives it full marks:
#       a phrase like "continue creator-discovery exploration"  → a document tidying up retired profiles    10 points
#       a phrase like "let me sign in on LinkedIn…"             → the lesson about a passport symlink        10 points
#   ⛔ The real failure mode is not "overestimating relevance" but ★being unable to say there is no answer when there is none★.
#   So exactly one axis changes: ★force it to quote a sentence from inside the candidate.★
#   If it cannot produce a quote, that candidate is not the answer — the room to invent one is structurally removed.
VARIANTS["v3"] = "(a procedural variant — the v0 prompt + a decoy self-check)"
VARIANTS["v2"] = """당신은 검색 판정자입니다. 물음은 ★하나★ 입니다:

  ★이 기억 안에, 사람이 방금 한 말에 답하는 문장이 실제로 있는가?★

각 후보마다 이렇게 하세요:
  1) 후보 본문에서 그 말에 답하는 문장을 ★그대로★ 한 조각 고른다 (최대 40자).
  2) 고를 수 없으면 그 후보는 답이 아니다 → 점수 0~2, 인용은 빈 문자열.

점수
- 10   : 인용한 문장이 그 말에 직접 답하거나, 모르고 진행하면 사고가 나는 규칙이다
- 6~9  : 인용한 문장이 그 말에 직접 걸리는 주의사항이다
- 3~5  : 인용은 했지만 그 말의 답은 아니다
- 0~2  : ★인용할 문장이 없다★

⛔ 인용은 ★후보 본문에 실제로 있는 글자★ 여야 합니다. 요약하거나 지어내지 마세요.
⛔ ★주제가 겹친다★ 는 것은 인용이 아닙니다. 낱말이 같다는 것도 인용이 아닙니다.
⛔ ★후보 중에 답이 하나도 없을 수 있습니다.★ 그럴 때 전부 0~2 를 주는 것이 정답입니다.
   억지로 제일 나은 것을 고르지 마세요 — 없는 답을 고르면 사람이 잘못된 문서를 읽습니다.

JSON 배열만 출력: [{"i": 0, "s": 7, "q": "인용"}, {"i": 1, "s": 0, "q": ""}, ...]

사용자의 말:
%s

후보:
%s"""


# ── v3 — ★changes the procedure, not the prompt★ (2026-08-31)
#
# v0, v1 and v2 all failed at the same place: ★unable to say "there is none" when the answer is absent from the candidates.★
# No amount of rewording fixed it. So instead of trying to make the judge smarter,
# ★it learns on the spot when not to trust itself.★
#
# A pattern this repository has already validated —
#   "when building a judge, ★attach a control group, and never skip it★. A request with no credential
#    must be refused for the door to be alive — if it is not refused, `unknown`, never judge."
#   ([[lesson_a_probe_that_cannot_fail_its_own_calibration]])
#
# Method: mix ★decoys certainly unrelated★ into the candidate list and send it as the same request (0 extra calls).
#   · decoy crosses the threshold → the judge is lenient on this query → ★attach nothing★
#   · decoy stays low            → that verdict can be trusted → use the top result
# ⛔ Decoys are not random but ★the farthest by cosine★. A random pick could happen to be related,
#    and then whether a high decoy is the judge's fault or the decoy's fault cannot be told apart.
DECOYS = int(os.environ.get("DECOYS", "2") or 2)


def with_decoys(db, q, cands, n=DECOYS):
    """Append the ★farthest★ n memories after the candidates. (candidates, the set of decoy indices)"""
    far = []
    try:
        hits = vectors.search(db, q, k=400)
        have = {c["name"] for c in cands}
        for did, cos, ratio in reversed(hits):            # ★from the end★ = the farthest
            r = db.execute("SELECT name, description, body FROM docs WHERE id=?",
                           (did,)).fetchone()
            if r is None or r["name"] in have:
                continue
            far.append({"name": r["name"], "description": r["description"],
                        "body": (r["body"] or "")[:500]})
            if len(far) >= n:
                break
    except Exception:                                     # noqa: BLE001
        pass
    # ⛔⛔ ★Decoys must not be 'appended' at the tail★ — `rerank.score` ★truncates★ candidates to
    #    MAX_CAND (20). The first version appended 2 after 20, so the decoys were ★cut before they
    #    ever entered the request★. The body matched the previous run exactly, 24/25 became cache hits,
    #    and the measurement produced the ★false conclusion★ "the decoy check caught nothing" (the only
    #    clue was the budget dropping by just 1 call).
    #    ⇒ ★Decoys take a candidate's own seat★ — the last n real candidates are swapped for decoys.
    #      The request size and candidate-interference conditions stay the same, so it compares fairly against v0.
    keep = list(cands)[:max(0, len(cands) - len(far))]
    out = keep + far
    return out, set(range(len(keep), len(out)))


def load_set():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "short.json")
    with open(path, encoding="utf-8") as fh:
        ev = json.load(fh)
    C = [r if isinstance(r, str) else r["q"] for r in ev["C_no_memory_needed"]]
    A = [(r["q"], r["gold"]) for r in ev["A_memory_needed"]
         if isinstance(r, dict) and r.get("gold")]
    return A, C


def candidates_for(db, q):
    out = []
    for did, cos, ratio in vectors.search(db, q, k=rerank.MAX_CAND):
        row = bs._hydrate(db, did)
        if row is None or row["name"] in bs._always_in_context():
            continue
        out.append({"name": row["name"], "description": row["description"],
                    "body": (row["body"] or "")[:500]})
    return out


def near_decoys(db, q, gold, A, cands, n=2):
    """★Close but definitely wrong★ decoys — pulled from another query's right answer.

    ⛔ A far decoy (lowest cosine) ★is no test at all★ (measured 2026-08-31):
       the distribution of decoy top scores was {0:12, 1:2, 2:10, 5:1} — ★never once above 5★.
       The judge correctly rejects something certainly far away — the failure is not there.

    The failure is ★something topically close that is not the answer★. So the decoy must be that too:
    ★another query's gold★ is a real memory, on the same topic in the same corpus, and ★certainly not
    the answer to this query★ (the label says so). If the judge scores it high, that verdict cannot
    be trusted.
    """
    others = [g for qq, g in A if g and g != gold]
    have = {c["name"] for c in cands}
    out = []
    for g in others:
        if g in have:
            continue
        r = db.execute("SELECT name, description, body FROM docs WHERE name=?",
                       (g,)).fetchone()
        if r is None:
            continue
        out.append({"name": r["name"], "description": r["description"],
                    "body": (r["body"] or "")[:500]})
        have.add(g)
        if len(out) >= n:
            break
    keep = list(cands)[:max(0, len(cands) - len(out))]
    return keep + out, set(range(len(keep), len(keep) + len(out)))


def blind_control(db, A, seed=7):
    """★A control group that needs no label★ — a real query given ★random memories★ as candidates.

    ## Why this is needed (2026-08-31, found by a human reading the raw text)

    Opening 7 cases where the judge gave full marks in the hand-labelled control group (`C_no_memory_needed`)
    showed ★at least 3 were the judge being right★:

        "pause the task for a moment"        → "TaskStop only killed the shell — confirm before reporting it paused"
        "show me the link, let me sign in"    → "how a human re-signs a worker session in"
        "rx#584 is mid-merge, go on with T7b" → that document really does mention rx#584

    ⛔ Those labels were assigned ★in the lexical-search era★ (2026-08-12). Back then such sentences found
    nothing, so they looked like "no memory needed". Once meaning-search existed they became ★talk with
    a real answer★, but the label never moved. This repository has already been burnt in this exact spot twice.

    ## So instead of fixing the label, ★the label is made unnecessary★

    The query is real (something a human actually said), and only the candidates are filled with
    ★random memories that never went through search★. A random memory is ★by definition★ not the
    answer to that talk — zero room for argument. A high score here is ★an undisputable false fire★.

    ⛔ This removes at the root the self-fulfilling trick of "move the label to make the measurement look good".
    """
    # ⛔⛔ ★The sample is fixed★ (2026-08-31) — without fixing it, measuring the same control group three
    #    times gave ★12% · 36% · 12%★. Changing even one item in `A` reshuffles the random draw entirely,
    #    so ★neither a variant comparison nor a comparison over time would hold★.
    #    (This repository already fixed a sample the same way, in `tests/eval/prompts.json`.)
    #    If the file exists it is used as-is; if not, it is drawn once and written. Delete it and it draws again.
    import json as _j
    import random
    fixed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "eval", "blind_control.json")
    saved = None
    try:
        with open(fixed_path, encoding="utf-8") as fh:
            saved = _j.load(fh)
    except (OSError, ValueError):
        saved = None
    rnd = random.Random(seed)
    names = [r["name"] for r in db.execute(
        "SELECT name FROM docs WHERE source='memory' ORDER BY name")]
    # ★A query can have several gold answers★ — flatten them in (§evalinit.golds)
    golds = {x for _, g in A for x in _ei.golds(g)}
    if saved:
        out = []
        for row in saved["pairs"]:
            cands = []
            for n in row["cands"]:
                r = db.execute("SELECT name, description, body FROM docs WHERE name=?",
                               (n,)).fetchone()
                if r:
                    cands.append({"name": r["name"], "description": r["description"],
                                  "body": (r["body"] or "")[:500]})
            out.append((row["q"], cands))
        return out
    out = []
    for q, gold in A:
        pick = []
        while len(pick) < rerank.MAX_CAND and names:
            n = names[rnd.randrange(len(names))]
            if n in golds or n in pick:
                continue                                  # ★mixing in the right answer breaks the control group★
            pick.append(n)
        cands = []
        for n in pick:
            r = db.execute("SELECT name, description, body FROM docs WHERE name=?",
                           (n,)).fetchone()
            if r:
                cands.append({"name": r["name"], "description": r["description"],
                              "body": (r["body"] or "")[:500]})
        out.append((q, cands))
    try:
        os.makedirs(os.path.dirname(fixed_path), exist_ok=True)
        with open(fixed_path, "w", encoding="utf-8") as fh:
            _j.dump({"_": ["★a fixed sample★ — delete it and it draws again.",
                           "⛔ without fixing it, the same control group wobbled 12%·36%·12%."],
                     "seed": seed,
                     "pairs": [{"q": q, "cands": [c["name"] for c in cs]}
                               for q, cs in out]}, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return out


def run(db, prompt, A, C, cache):
    """(control-group top scores, positives (top score, is gold 1st)). ⛔ a failure is not counted."""
    ctrl, pos, fails = [], [], 0
    for q in C:
        cands = candidates_for(db, q)
        s = rerank.score(q, cands, prompt=prompt, db=db, cache=cache)
        if s is None:
            fails += 1
            continue
        ctrl.append(max(s) if s else 0.0)
    for q, gold in A:
        cands = candidates_for(db, q)
        s = rerank.score(q, cands, prompt=prompt, db=db, cache=cache)
        if s is None:
            fails += 1
            continue
        if not s:
            pos.append((0.0, False))
            continue
        i = max(range(len(s)), key=lambda j: s[j])
        pos.append((s[i], _ei.gold_hit(gold, cands[i]["name"])))
    return ctrl, pos, fails


def run_v3(db, prompt, A, C, cache, thr_probe=8.0):
    """★Test the judge on the spot with a decoy★ — if a decoy scores high, that query is not judged."""
    ctrl, pos, fails, killed = [], [], 0, 0
    def one(q, cands):
        nonlocal fails, killed
        cc, decoy = with_decoys(db, q, cands)
        s = rerank.score(q, cc, prompt=prompt, db=db, cache=cache)
        if s is None:
            fails += 1
            return None, None
        dmax = max((s[i] for i in decoy if i < len(s)), default=0.0)
        real = [(s[i], cc[i]["name"]) for i in range(len(cands)) if i < len(s)]
        if not real:
            return 0.0, ""
        best = max(real)
        if dmax >= thr_probe:                             # ★the self-check failed → silence★
            killed += 1
            return 0.0, ""
        return best[0], best[1]
    for q in C:
        v, _n = one(q, candidates_for(db, q))
        if v is not None:
            ctrl.append(v)
    for q, gold in A:
        v, n = one(q, candidates_for(db, q))
        if v is not None:
            pos.append((v, _ei.gold_hit(gold, n)))
    return ctrl, pos, fails, killed


def report(label, ctrl, pos, thr):
    noise = sum(1 for v in ctrl if v >= thr)
    hit = sum(1 for v, ok in pos if ok and v >= thr)
    hit_scores = [v for v, ok in pos if ok and v >= thr]
    gap = (min(hit_scores) - max(ctrl)) if hit_scores and ctrl else 0.0
    print("  %-4s thr %4.1f │ control false-fire %2d/%-2d (%3.0f%%) │ positive hit %2d/%-2d (%3.0f%%) │ "
          "control max %4.1f │ gap %+.1f"
          % (label, thr, noise, len(ctrl), 100.0 * noise / max(1, len(ctrl)),
             hit, len(pos), 100.0 * hit / max(1, len(pos)),
             max(ctrl) if ctrl else 0.0, gap))
    return {"thr": thr, "noise": noise, "n_ctrl": len(ctrl), "hit": hit,
            "n_pos": len(pos), "ctrl_max": max(ctrl) if ctrl else 0.0, "gap": gap}


def main():
    which = os.environ.get("VARIANT", "v0")
    cache = os.environ.get("NOCACHE", "") != "1"
    if which not in VARIANTS:
        print("unknown variant: %s (available: %s)" % (which, ", ".join(VARIANTS)))
        return 2
    db = store.connect()
    b = rerank.budget(db)
    A, C = load_set()
    # ★Shrink the sample to sweep quickly★ — for picking a variant when the budget is tight.
    # ⛔ A conclusion drawn from a shrunk sample is written down ★together with that fact★ (a small sample paints a picture of its own).
    if os.environ.get("A_N"):
        A = A[:int(os.environ["A_N"])]
    if os.environ.get("C_N"):
        C = C[:int(os.environ["C_N"])]
    need = len(A) + len(C)
    print("variant %s · control %d · positive %d · candidate cap %d · cache %s"
          % (which, len(C), len(A), rerank.MAX_CAND, "on" if cache else "off"))
    print("budget %d/%d · left %d (max needed %d)" % (b["used"], b["limit"], b["left"], need))
    if b["wall"] or (b["left"] < need and cache is False):
        print("⛔ the budget is short or walled — try again after Pacific midnight.")
        return 2

    killed = 0
    if which == "v3":
        ctrl, pos, fails, killed = run_v3(db, VARIANTS["v0"], A, C, cache)
        print("★self-check with %d decoys★ — %d queries barred from judging" % (DECOYS, killed))
    else:
        ctrl, pos, fails = run(db, VARIANTS[which], A, C, cache)
    # ★A control group needing no label★ — a real query + a random memory. Counts only an undisputable false fire.
    blind, bfail = [], 0
    for q, cands in blind_control(db, A)[:int(os.environ.get("BLIND_N", "25") or 25)]:
        if which == "v3":
            cc, decoy = with_decoys(db, q, cands)
            sb = rerank.score(q, cc, prompt=VARIANTS["v0"], db=db, cache=cache)
            if sb is None:
                bfail += 1
                continue
            dmax = max((sb[i] for i in decoy if i < len(sb)), default=0.0)
            real = [sb[i] for i in range(len(cands)) if i < len(sb)]
            blind.append(0.0 if dmax >= 8.0 else (max(real) if real else 0.0))
            continue
        sb = rerank.score(q, cands, prompt=VARIANTS[which], db=db, cache=cache)
        if sb is None:
            bfail += 1
            continue
        blind.append(max(sb) if sb else 0.0)
    if fails:
        print("⛔ %d call failures — %s" % (fails, rerank.last_failure()[:70]))
        print("   ★counting a failure as a score of 0 produces a false conclusion★. This result is partial.")
    print()
    print("  ★look at all three together — watch only false fires and silence wins, watch only hits and noise wins★")
    rows = [report(which, ctrl, pos, t) for t in (7.0, 8.0, 9.0, 10.0)]
    print()
    if blind:
        print()
        print("  ★a control group needing no label★ (a real query + %d random memories — not the answer by definition)" % len(blind))
        for t in (7.0, 8.0, 9.0, 10.0):
            n = sum(1 for v in blind if v >= t)
            print("     thr %4.1f │ ★undisputable false fire★ %2d/%-2d (%3.0f%%)"
                  % (t, n, len(blind), 100.0 * n / len(blind)))
        print("     distribution: %s" % json.dumps(
            {int(v): sum(1 for x in blind if int(x) == int(v)) for v in sorted(set(blind))},
            ensure_ascii=False))
    print()
    print("  hand-labelled control-group score distribution: %s" % json.dumps(
        {int(v): sum(1 for x in ctrl if int(x) == int(v)) for v in sorted(set(ctrl))},
        ensure_ascii=False))
    print("  budget %s" % json.dumps(rerank.budget(db), ensure_ascii=False))
    out = os.environ.get("OUT", "")
    if out:
        json.dump({"variant": which, "ctrl": ctrl, "pos": pos, "rows": rows},
                  open(out, "w"), ensure_ascii=False)
        print("  raw data %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
