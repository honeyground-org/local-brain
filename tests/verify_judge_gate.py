#!/usr/bin/env python3
"""★The hook asks the judge only when it can answer in the time left★ (local · budget 0)

Why this exists (2026-10-14)
----------------------------
The free judge model was overloaded (5–9 s answers, then 503 "high demand"), and from one evening on 15 of 16
second-stage runs on the author's machine were cut while the request was out — each spending a call, holding
the prompt for the full 4.5 s and attaching nothing. §judgegate decides from this installation's own records
whether a fresh request goes out. This check holds down:

  ① no record → ask; a well provider whose usual answer fits → ask; one slower than the time left → not asked
  ② an unwell provider (the last request cut while out, or refused for a transient reason) is not asked, except
     for a probe after an exponential backoff counted in chances: n failures in a row → 2^(n-1) - 1 skipped
  ③ a `Retry-After` the provider gave is waited out; one probe at a time across processes
  ④ ★only the hook is gated★: a measurement (no deadline) asks anyway, and an answer already in the cache is
     served — no call, not skipped
  ⑤ the hook, run as its own process with an unwell record, ends early, spends no call and says why

⛔ Control: the same unwell record with the gate's decision forced to "ask" sends a request — so ⑤ could fail.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-judgegate-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ["BRAIN_LEDGER"] = os.path.join(_TMP, "ledger.db")
os.environ["BRAIN_RERANK_RPM"] = "0"

from brain import engines, judgegate, observe, rerank, search, store, vectors  # noqa: E402

FAILS = []
NOW = time.time()


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def a(t: float, sent=True, returned=True, secs=1.0, kind=None, retry_after=None, skipped=False) -> dict:
    return {"t": t, "sent": sent, "returned": returned, "secs": secs, "waited": secs, "kind": kind,
            "retry_after": retry_after, "cached": False, "skipped": skipped}


def cut(t: float) -> dict:
    return a(t, returned=False, secs=None)


def skip(t: float) -> dict:
    return a(t, sent=False, returned=False, secs=None, skipped=True)


def put(rows: list) -> None:
    """Hook records of the kind §observe writes — the gate reads the real files."""
    for r in rows:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r.pop("t")))
        observe.write("hook", dict(r, ts=ts))


HOOK = r'''
import io, json, os, sys
sys.path.insert(0, os.environ["ROOT"])
from brain import engines, hook, judgegate, search
hook._timing_path = lambda: os.environ["LOG"]
search.recall = lambda *a, **k: []
calls = []
engines.judge_text = lambda *a, **k: calls.append(1) or json.dumps([{"i": 0, "s": 9}])
if os.environ.get("FORCE_ASK"):
    judgegate.decide = lambda remaining, now=None, history=None: (True, "forced", False)
sys.stdin = io.StringIO(json.dumps({"prompt": "which routine mentions the quarterly ledger step"}))
try:
    hook.main()
finally:
    open(os.environ["CALLS"], "w", encoding="utf-8").write(str(len(calls)))
'''


def main() -> int:
    print("=" * 78)
    print("the hook asks the judge only when it can answer in the time left")
    print("=" * 78)

    # ── ① ────────────────────────────────────────────────────────────────────
    check("① no record → ask", judgegate.decide(3.0, NOW, [])[:2] == (True, "no_history"))
    well = [a(NOW - 300, secs=0.8), a(NOW - 200, secs=1.2), a(NOW - 100, secs=1.0)]
    check("① a well provider whose usual answer fits the time left → ask",
          judgegate.decide(3.0, NOW, well)[:2] == (True, "ok"))
    check("① its usual answer slower than the time left → not asked",
          judgegate.decide(0.5, NOW, well)[:2] == (False, "slower_than_time_left"))

    # ── ② exponential backoff in chances ─────────────────────────────────────
    one = well + [cut(NOW - 50)]
    got = judgegate.decide(3.0, NOW, one)
    judgegate.release_probe()
    check("② one failure → the next chance is a probe", got == (True, "probe", True), "%s" % (got,))
    three = well + [cut(NOW - 90), cut(NOW - 70), cut(NOW - 50)]
    check("② three in a row → 3 chances skipped first (1 so far → not asked)",
          judgegate.decide(3.0, NOW, three + [skip(NOW - 40)])[:2] == (False, "provider_unwell"))
    got = judgegate.decide(3.0, NOW, three + [skip(NOW - 40), skip(NOW - 30), skip(NOW - 20)])
    judgegate.release_probe()
    check("② … and after 3 skipped, a probe", got == (True, "probe", True), "%s" % (got,))
    busy = well + [a(NOW - 50, kind="overloaded")]
    got = judgegate.decide(3.0, NOW, busy)
    judgegate.release_probe()
    check("② an overloaded refusal counts as unwell (not as an answer)", got[1] == "probe", "%s" % (got,))
    check("② ★control★: a refusal that is not transient (a bad request) leaves the provider well",
          judgegate.decide(3.0, NOW, well + [a(NOW - 50, kind="refused")])[:2] == (True, "ok"))

    # ── ③ Retry-After and one probe at a time ────────────────────────────────
    said = well + [a(NOW - 10, kind="rate_limited", retry_after=60)]
    check("③ a Retry-After of 60 s, 10 s ago → not asked",
          judgegate.decide(3.0, NOW, said)[:2] == (False, "provider_said_wait"))
    got = judgegate.decide(3.0, NOW + 70, said)
    check("③ … 70 s after it → a probe", got[:2] == (True, "probe"), "%s" % (got,))
    check("③ while that probe is out, another process is not asked",
          judgegate.decide(3.0, NOW + 71, said)[:2] == (False, "probe_in_flight"))
    judgegate.release_probe()

    # ── ④ only the hook is gated; the cache is served ────────────────────────
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"], "max_depth": 1}]}, fh)
    with open(os.path.join(_MEM, "ledger_note.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: ledger_note\ndescription: fixture\n---\n\nThe quarterly ledger step is signed off.\n")
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False)
    did = db.execute("SELECT id FROM docs").fetchone()["id"]
    put([dict(cut, judge={"sent": True, "returned": False, "phase": "judge"}) for cut in
         ({"t": NOW - 30},)])
    calls = []
    real_search, real_judge = vectors.search, engines.judge_text
    vectors.search = lambda db_, q, k=5: [(did, 0.8, 1.2)]
    engines.judge_text = lambda *x, **k: calls.append(1) or json.dumps([{"i": 0, "s": 9}])
    os.environ["BRAIN_RERANK_MIN"] = "8"
    try:
        # the probe right after one failure would ask — make it two failures with none skipped yet
        put([{"t": NOW - 20, "judge": {"sent": True, "returned": False, "phase": "judge"}}])
        o1 = search.judge_observation()
        r1 = search.judged(db, "a question while the judge is unwell", observe=o1, deadline=time.time() + 3)
        n1 = len(calls)
        o2 = search.judge_observation()
        search.judged(db, "a question while the judge is unwell", observe=o2)
        n2 = len(calls)
        o3 = search.judge_observation()
        r3 = search.judged(db, "a question while the judge is unwell", observe=o3, deadline=time.time() + 3)
        n3 = len(calls)
    finally:
        vectors.search, engines.judge_text = real_search, real_judge
        os.environ.pop("BRAIN_RERANK_MIN", None)
    check("④ the hook's path (a deadline) with an unwell provider: not asked, and the record says why",
          n1 == 0 and not r1 and o1["outcome"] == "skipped" and (o1["gate"] or {}).get("why") == "provider_unwell",
          json.dumps(o1["gate"]))
    check("④ ★control★: a measurement (no deadline) asks anyway", n2 == 1 and o2["outcome"] == "fired")
    check("④ an answer already in the cache is served on the hook's path — no call, not skipped",
          n3 == 1 and r3 and o3["cached"] is True and o3["gate"] is None, "outcome %s" % o3["outcome"])
    db.close()

    # ── ⑤ end to end ─────────────────────────────────────────────────────────
    drv = os.path.join(_TMP, "hook.py")
    with open(drv, "w", encoding="utf-8") as fh:
        fh.write(HOOK)
    vectors_seed = store.connect()
    vectors.ensure_table(vectors_seed)
    import array
    sha = vectors_seed.execute("SELECT sha FROM docs WHERE id=?", (did,)).fetchone()["sha"]
    v = [0.0] * vectors.DIM
    v[0] = 1.0
    with vectors_seed:
        vectors_seed.execute("INSERT OR REPLACE INTO vectors(doc_id,chunk_no,sha,model,dim,vec) VALUES (?,?,?,?,?,?)",
                             (did, 0, sha, vectors.model_tag(), vectors.DIM, array.array("f", v).tobytes()))
    vectors._qcache_get(vectors_seed, "warm")
    vectors._qcache_put(vectors_seed, "which routine mentions the quarterly ledger step", array.array("f", v))
    vectors_seed.close()
    res = {}
    for force in ("", "1"):
        log, cf = os.path.join(_TMP, "log%s.jsonl" % force), os.path.join(_TMP, "calls%s" % force)
        used0 = rerank.budget()["used"]
        t = time.time()
        subprocess.run([sys.executable, drv], capture_output=True, text=True, timeout=30, encoding="utf-8",
                       env=dict(os.environ, ROOT=ROOT, LOG=log, CALLS=cf, BRAIN_RERANK_MIN="8",
                                FORCE_ASK=force))
        rec = json.loads(open(log, encoding="utf-8").read().splitlines()[-1]) if os.path.exists(log) else {}
        res[force] = (time.time() - t, int(open(cf, encoding="utf-8").read() or 0) if os.path.exists(cf) else -1,
                      rerank.budget()["used"] - used0, (rec.get("judge") or {}))
    took, n, spent, j = res[""]
    check("⑤ the hook with an unwell record: no call, no budget, skipped with its reason",
          n == 0 and spent == 0 and j.get("outcome") == "skipped" and (j.get("gate") or {}).get("ask") is False,
          "%.2fs · calls %d · %s" % (took, n, json.dumps(j.get("gate"))))
    took, n, spent, j = res["1"]
    check("⑤ ★control★: the same record with the decision forced to ask sends a request", n == 1 and spent == 1,
          "calls %d · spent %d" % (n, spent))

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — the judge is asked only when it can answer in the time left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
