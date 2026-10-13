#!/usr/bin/env python3
"""★The hook writes down what its judge said — before the threshold, at no extra call★ (local · budget 0)

Why this exists (2026-10-13)
----------------------------
The second stage asks a judge to score up to 20 candidates and attaches the best one if it clears a
threshold. Everything below the threshold was thrown away unseen. So the one sample a threshold could be
measured from — ★this person's own prompts, scored by the judge they actually use★ — was never kept, and
the threshold could only be set from a hand-labelled sample most people do not have.

So `search.judged` fills in a record (§search.judge_observation) and the hook writes it into its timing
line. This check runs the hook ★as its own process, the way the host does★, with the judge intercepted
(no network, no budget), and holds down:

  ① what the judge said is recorded — candidates sent, the top two scores, ties at the top, the threshold,
     the excerpt policy — for a run that fires, one below the threshold, and a tie
  ② "could not ask" is never recorded as a score: a network failure, an unreadable answer and a run the
     watchdog ended are `failed` / `failed` / `cut`, with no top score
  ③ why the judge did not run is said: no vectors in the space in use, no threshold measured for this
     judge (where a person without a labelled sample stands), no candidates, or words already answered
  ④ ★no extra call★ — one judge call per uncached run, none when it does not run, none from the cache
  ⑤ ★nothing returned changes★ — `judged()` gives the same answer with and without a record to fill
  ⑥ ★no prompt text in the record★ — a probe word that verifiably reached the judge is not in the file

⛔ Control: the hook with the record ★not wired★ (its second stage replaced by the old call, which ignores
   the record) leaves it `pending` — so the rows of ① would be red on code that does not record.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-judgeobs-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ["BRAIN_RERANK_RPM"] = "0"          # the interceptor answers at once; no pacing to wait out

from brain import hook, search, store, vectors  # noqa: E402

FAILS = []
PROBE = "obsprobe7f3a"                        # a word that must reach the judge and never the record

def prompt_for(shape: str) -> str:
    """One prompt per shape — the judge's cache answers a repeated prompt, which only "again" wants."""
    return "which of these notes explains the %s rule (%s)" % (PROBE, "fired" if shape == "again" else shape)


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


NOTES = {
    "ledger_retention": "Ledger rows are kept for ninety days, then archived.",
    "deploy_window": "Deploys happen on weekdays before four in the afternoon.",
    "naming_rules": "Branch names describe the change, not the person.",
}

# The driver is the hook's own `main()` in a fresh interpreter. The judge is intercepted at the provider
# call (`engines.judge_text`), so `rerank.score` runs for real — its cache, its ledger, its parser.
DRIVER = r'''
import io, json, os, sys, time, urllib.error
sys.path.insert(0, os.environ["ROOT"])
from brain import engines, hook, search, store, vectors
shape, log, calls, prompt = sys.argv[1:5]
ids = [int(x) for x in os.environ["IDS"].split(",")]
hook._timing_path = lambda: log
hook._named = lambda p, db=None: []
search.recall = lambda *a, **k: []                    # words fall silent → the second stage runs
vectors.search = lambda db, q, k=5: ([] if shape == "nocands" else
                                     [(ids[0], 0.8, 1.3), (ids[1], 0.7, 1.2), (ids[2], 0.6, 1.1)])
if shape == "novec":
    vectors.model_tag = lambda: "a-space-nobody-embedded-in"
if shape == "lexical":
    def _words(db, p):
        row = search._hydrate(db, ids[0])
        return [search._present(row, 50.0, [], None, [], db)]
    hook.lexical = _words
if shape == "unwired":                                # ⛔ the control: the old call, which ignores the record
    hook.fallback = lambda db, prompt, **k: search.judged(db, prompt)[:1]
ANSWERS = {
    "fired": [9, 3, 2], "again": [9, 3, 2], "unwired": [9, 3, 2], "tie": [7, 7, 3], "below": [5, 4, 1],
}
def judge(text, timeout=25.0, model=""):
    with open(calls, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"shape": shape, "sent": text}) + "\n")
    if shape == "failed":
        raise urllib.error.URLError("the network is down")
    if shape == "garbage":
        return "I would rather not say."
    if shape == "slow":
        time.sleep(30)
    return json.dumps([{"i": i, "s": s} for i, s in enumerate(ANSWERS.get(shape, [0, 0, 0]))])
engines.judge_text = judge
sys.stdin = io.StringIO(json.dumps({"prompt": prompt}))
try:
    sys.exit(hook.main())
except SystemExit:
    raise
except Exception:
    sys.exit(0)
'''


def _run(shape: str, env_extra: dict = None) -> dict:
    log = os.path.join(_TMP, "timing-%s.jsonl" % shape)
    calls = os.path.join(_TMP, "calls-%s.jsonl" % shape)
    drv = os.path.join(_TMP, "driver.py")
    with open(drv, "w", encoding="utf-8") as fh:
        fh.write(DRIVER)
    env = dict(os.environ, ROOT=ROOT, IDS=os.environ["IDS"], BRAIN_RERANK_MIN="8")
    env.update(env_extra or {})
    env = {k: v for k, v in env.items() if v is not None}
    p = subprocess.run([sys.executable, drv, shape, log, calls, prompt_for(shape)], capture_output=True,
                       text=True, timeout=30, env=env, encoding="utf-8", errors="replace")
    rec, raw, sent = [], "", []
    if os.path.exists(log):
        raw = open(log, encoding="utf-8").read()
        rec = [json.loads(x) for x in raw.splitlines() if x.strip()]
    if os.path.exists(calls):
        sent = [json.loads(x) for x in open(calls, encoding="utf-8") if x.strip()]
    last = rec[-1] if rec else {}
    return {"out": p.stdout, "err": p.stderr[-300:], "rec": rec, "raw": raw, "calls": len(sent),
            "sent": sent, "judge": last.get("judge"), "cut": last.get("cut")}


def _build() -> tuple:
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"],
                                "exclude": ["MEMORY.md"], "max_depth": 1}]}, fh)
    for name, body in NOTES.items():
        with open(os.path.join(_MEM, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: fixture note %s\n---\n\n%s\n" % (name, name, body))
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    got = db.execute("SELECT id, name FROM docs ORDER BY id").fetchall()
    ids, names = [r["id"] for r in got], [r["name"] for r in got]
    # one vector in the space in use — the hook asks only "is there any", the search itself is intercepted
    vectors.ensure_table(db)
    with db:
        db.execute("INSERT OR REPLACE INTO vectors(doc_id, chunk_no, sha, model, dim, vec) "
                   "VALUES (?, 0, 'x', ?, ?, ?)",
                   (ids[0], vectors.model_tag(), vectors.DIM,
                    struct.pack("%df" % vectors.DIM, *([0.0] * vectors.DIM))))
    db.close()
    return ids, names


def main() -> int:
    print("=" * 78)
    print("the hook's judge record — what it said before the threshold, at no extra call")
    print("=" * 78)
    ids, names = _build()
    if len(ids) < 3:
        check("the fixture index holds three notes", False, "%d" % len(ids))
        return 1
    os.environ["IDS"] = ",".join(str(i) for i in ids[:3])
    policy = search.judge_policy(20)

    # ── ① what the judge said ─────────────────────────────────────────────────
    r = _run("fired")
    j = r["judge"] or {}
    check("① a run that fires: outcome, candidates sent, top two, ties, threshold",
          j.get("outcome") == "fired" and j.get("n") == 3 and j.get("top") == 9.0
          and j.get("second") == 3.0 and j.get("ties") == 1 and j.get("floor") == 8.0,
          json.dumps(j)[:160])
    check("①   it says which excerpt policy the scores were given under, and that it was not cached",
          j.get("policy") == policy and j.get("cached") is False and j.get("secs") is not None,
          "policy %s · cached %s" % (j.get("policy"), j.get("cached")))
    check("①   and it still attaches the candidate it scored highest (the first, at 9)",
          names[0] in r["out"] and names[1] not in r["out"], "%s · %r" % (names[0], r["out"][-120:]))
    check("④ one judge call for the run", r["calls"] == 1, "%d" % r["calls"])
    check("⑥ the probe word reached the judge (so it was there to leak)",
          any(PROBE in s["sent"] for s in r["sent"]))
    check("⑥ ★and is not in the record★", r["raw"] and PROBE not in r["raw"])

    r = _run("again")
    j = r["judge"] or {}
    check("④ the same prompt again is answered from the cache — ★no call★, and the record says so",
          r["calls"] == 0 and j.get("cached") is True and j.get("top") == 9.0,
          "calls %d · cached %s" % (r["calls"], j.get("cached")))

    r = _run("below")
    j = r["judge"] or {}
    check("① below the threshold: ★the scores are kept★ and nothing is attached",
          j.get("outcome") == "below" and j.get("top") == 5.0 and j.get("second") == 4.0
          and r["out"].strip() == "", json.dumps(j)[:120])

    r = _run("tie")
    j = r["judge"] or {}
    check("① a tie at the top is counted", j.get("ties") == 2 and j.get("top") == 7.0,
          json.dumps(j)[:120])

    # ── ② could not ask ≠ a score ─────────────────────────────────────────────
    for shape, want in (("failed", "failed"), ("garbage", "failed")):
        r = _run(shape)
        j = r["judge"] or {}
        check("② %-8s → %s with ★no top score★ and a reason" % (shape, want),
              j.get("outcome") == want and j.get("top") is None and bool(j.get("fail"))
              and r["calls"] == 1, json.dumps(j)[:140])
    r = _run("slow")
    j = r["judge"] or {}
    check("② the watchdog ended the run while the judge was out → ★cut★, no score",
          r["cut"] is True and j.get("outcome") == "cut" and j.get("top") is None,
          json.dumps(j)[:120])

    # ── ③ why it did not run ──────────────────────────────────────────────────
    for shape, env, want in (("novec", None, "no_vectors"),
                             ("uncalibrated", {"BRAIN_RERANK_MIN": None}, "uncalibrated"),
                             ("nocands", None, "no_candidates")):
        r = _run(shape, env)
        j = r["judge"] or {}
        check("③ %-12s → %s, no call" % (shape, want),
              j.get("outcome") == want and r["calls"] == 0, json.dumps(j)[:120])
    r = _run("lexical")
    check("③ words answered → the second stage never ran, and the record says none",
          r["rec"] and r["judge"] is None and r["calls"] == 0, json.dumps(r["rec"][-1:])[:120])

    # ── ⑤ nothing returned changes ────────────────────────────────────────────
    from brain import engines
    real_search, real_judge = vectors.search, engines.judge_text
    vectors.search = lambda db, q, k=5: [(ids[0], 0.8, 1.3), (ids[1], 0.7, 1.2), (ids[2], 0.6, 1.1)]
    engines.judge_text = lambda text, timeout=25.0, model="": json.dumps(
        [{"i": 0, "s": 3}, {"i": 1, "s": 9}, {"i": 2, "s": 1}])
    os.environ["BRAIN_RERANK_MIN"] = "8"
    try:
        db = store.connect()
        a = search.judged(db, "a different question about deploys", cache=False)
        rec = search.judge_observation()
        b = search.judged(db, "a different question about deploys", cache=False, observe=rec)
        db.close()
    finally:
        vectors.search, engines.judge_text = real_search, real_judge
        os.environ.pop("BRAIN_RERANK_MIN", None)
    same = [(x["name"], x.get("rerank")) for x in a] == [(x["name"], x.get("rerank")) for x in b]
    check("⑤ judged() answers the same with and without a record to fill",
          same and a and a[0]["name"] == names[1] and rec["outcome"] == "fired",
          "%s ↔ %s" % ([x["name"] for x in a], [x["name"] for x in b]))

    # ── control ──────────────────────────────────────────────────────────────
    r = _run("unwired")
    j = r["judge"] or {}
    check("★control★: with the record not wired it stays pending — ① would be red on such code",
          j.get("outcome") == "pending" and j.get("top") is None, json.dumps(j)[:100])

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — the judge's answer is kept before the threshold, at no extra call")
    return 0


if __name__ == "__main__":
    sys.exit(main())
