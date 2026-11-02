#!/usr/bin/env python3
"""★The hook's second stage without a judge: a memory both lanes agree on, behind a gate the controls set★ (local · budget 0)

Why this exists (2026-11-02)
----------------------------
The judge engine can be none — the author stopped using a remote judge. Until then the hook's second stage
either asked the judge or, behind a single-lane cosine-ratio gate, attached meaning's top-1. Measured on the
author's 66 labelled prompts and 34 that need no memory, that gate (set at the controls' maximum ratio, 1.40)
let 1 answer through, because the top-1 ratio of an answer and of no-memory talk overlap (1.11–1.61 vs
1.08–1.40). Asking the two lanes to ★agree★ — the memory sits in the words' top 3 and in meaning's top 3 —
and gating the ratio against the ★agreeing★ controls attached 2 more answers and fired on none of the 34.

This builds a corpus whose vectors it writes itself (no network, no engine) and holds down:

  ① ★silent on an unknown scale★ — with no calibration stored, a prompt both lanes agree on attaches nothing
  ② `vectors.calibrate_cos` measures the agreement gate from the controls the lanes agree on and stores it
     under the embedding profile's key, apart from the single-lane gate
  ③ calibrated: the answer both lanes hold attaches; the control the lanes also agree on (lower ratio) does not;
     a prompt whose meaning top-1 is not in the words' top 3 still comes through the single-lane gate as before,
     and with that gate silenced the agreement rule alone attaches nothing for it, however high its ratio
  ④ the record: outcome `no_judge`, the judge-free decision `fired` / `below`, the attachment counted as meaning
  ⑤ `brain monitor` counts a judge-free decision as the stage finishing

⛔ Control: with agreement replaced by meaning alone (the lanes not asked to agree), ③'s third case fires —
   the check sees the agreement rule.
"""
from __future__ import annotations

import array
import json
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-hook-agreement-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ.pop("BRAIN_LEXICON", None)
for key in ("BRAIN_EMBED_MIN_RATIO", "BRAIN_EMBED_MIN_AGREE_RATIO", "BRAIN_RERANK_MIN"):
    os.environ.pop(key, None)

from brain import calibrate, hook, observe, search, store, vectors  # noqa: E402

FAILS = []
FILL = ("meadow river lantern harbour copper orchard signal window garden ferry candle marble " * 20)[:900]
NOTES = {
    # the answer to P: its words, and (by the vector below) its meaning
    "ledger_rule": "The ledger is reconciled every friday before the books close. " + FILL,
    # the lanes agree on this one for the control prompt C — a lookalike, lower in meaning
    "ledger_notes": "Ledger notes and reconciliation reminders. " + FILL,
    # meaning's favourite for prompt X — the words never rank it
    "harbour_tide": "Tide tables for the harbour ferries. " + FILL,
    "quiet_note": "Ferries leave the harbour at dawn.",
    "tally_rule": "A tally of crates is kept beside the gate.",
}
P = "when does the ledger reconciliation happen"       # words: ledger_rule, ledger_notes · meaning: ledger_rule first
C = "ledger reconciliation reminders please"           # words: ledger_notes first · meaning: ledger_notes, lower ratio
X = "what time do the boats go"                        # words: nothing of harbour_tide · meaning: harbour_tide, high


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return array.array("f", [x / n for x in v])


def _build():
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"], "max_depth": 1}]}, fh)
    for name, body in NOTES.items():
        with open(os.path.join(_MEM, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: fixture %s\nmetadata:\n  type: project\n---\n\n%s\n"
                     % (name, name, body))
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    ids = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM docs")}
    vectors.ensure_table(db)
    axis = {}
    with db:
        for name, did in ids.items():
            row = db.execute("SELECT sha, body FROM docs WHERE id=?", (did,)).fetchone()
            for n in range(len(vectors.chunk_spans(row["body"] or ""))):
                k = 10 * sorted(ids).index(name) + n
                axis[(name, n)] = k
                v = [0.0] * vectors.DIM
                v[k] = 1.0
                db.execute("INSERT OR REPLACE INTO vectors(doc_id,chunk_no,sha,model,dim,vec) VALUES (?,?,?,?,?,?)",
                           (did, n, row["sha"], vectors.model_tag(), vectors.DIM, array.array("f", v).tobytes()))

    def query(weights):
        q = [0.0] * vectors.DIM
        for name, w in weights.items():
            q[axis[(name, 0)]] = w
        return _unit(q)
    vectors._qcache_get(db, P)                          # creates the cache table (a put alone is swallowed)
    # P: ledger_rule clearly first in meaning, ledger_notes second → both lanes hold ledger_rule
    vectors._qcache_put(db, P, query({"ledger_rule": 0.9, "ledger_notes": 0.3, "tally_rule": 0.1}))
    # C: ledger_notes first in meaning but ★less clearly★ (a flatter top) → a lower ratio than P's winner
    vectors._qcache_put(db, C, query({"ledger_notes": 0.6, "ledger_rule": 0.5, "quiet_note": 0.45}))
    # X: harbour_tide alone, as sharp as it gets — and never in the words' lane
    vectors._qcache_put(db, X, query({"harbour_tide": 1.0}))
    return db


def main() -> int:
    print("=" * 78)
    print("judge-free second stage — both lanes agree, behind the gate the agreeing controls set")
    print("=" * 78)
    db = _build()
    real = calibrate.threshold
    calibrate.threshold = lambda db, default=0.0, **kw: 1e9        # the words' stage stays silent: the second stage runs
    try:
        words_p = [r["name"] for r in search.recall(db, P, k=3, log=False, bridge=False) if not r.get("related")]
        words_x = [r["name"] for r in search.recall(db, X, k=3, log=False, bridge=False) if not r.get("related")]
        sem_p = search.semantic(db, P, k=3, min_cos=0.0)
        sem_c = search.semantic(db, C, k=3, min_cos=0.0)
        sem_x = search.semantic(db, X, k=3, min_cos=0.0)
        check("fixture: the words' top 3 for P hold the answer, and never harbour_tide for X",
              "ledger_rule" in words_p and "harbour_tide" not in words_x, "%s | %s" % (words_p, words_x))
        check("fixture: meaning puts the answer first for P, the lookalike first for C, harbour_tide first for X",
              [sem_p[0]["name"], sem_c[0]["name"], sem_x[0]["name"]] == ["ledger_rule", "ledger_notes", "harbour_tide"],
              "%s" % [(r["name"], r["ratio"]) for r in sem_p[:1] + sem_c[:1] + sem_x[:1]])
        check("fixture: the control's agreeing ratio sits below the answer's",
              sem_c[0]["ratio"] < sem_p[0]["ratio"], "%.3f < %.3f" % (sem_c[0]["ratio"], sem_p[0]["ratio"]))

        # ── ① silent on an unknown scale ─────────────────────────────────────
        check("① no calibration stored → both gates are silent (99)",
              vectors.min_agree_ratio(db) >= 99.0 and vectors.min_ratio(db) >= 99.0)
        obs = search.judge_observation()
        check("① uncalibrated: the prompt both lanes agree on attaches nothing",
              hook.fallback(db, P, strict=True, observe=obs) == [], "outcome %s" % obs["outcome"])
        check("① the record says no judge was chosen, not 'uncalibrated'", obs["outcome"] == "no_judge", obs["outcome"])

        # ── ② the gate is measured from the agreeing controls ────────────────
        res = vectors.calibrate_cos(db, [C], [(P, "ledger_rule")], save=True)
        agree = res.get("agree_gate") or {}
        stored = store.get_meta(db, vectors.calibration_key("vec_agree_min_ratio"))
        words_c = [r["name"] for r in search.recall(db, C, k=3, log=False, bridge=False) if not r.get("related")]
        agreeing_c = [r["ratio"] for r in sem_c if r["name"] in words_c]
        check("② calibrate_cos reports the agreement gate apart from the single-lane gate — the highest ratio the lanes "
              "agreed on in a control, plus the margin",
              agree.get("depth") == vectors.AGREE_N and agree.get("controls_agreeing") == len(agreeing_c) >= 1
              and abs(agree.get("min_ratio", 0) - (max(agreeing_c) + 0.01)) < 1e-3
              and agree.get("positives_attached") == 1, json.dumps(agree) + " agreeing %s" % agreeing_c)
        check("② the agreement gate is stored under the embedding profile's key",
              stored and abs(float(stored) - agree["min_ratio"]) < 1e-6
              and abs(vectors.min_agree_ratio(db) - agree["min_ratio"]) < 1e-6, "stored %s" % stored)
        check("② the single-lane gate sits above the control's top-1 ratio, as before",
              abs(res["min_ratio"] - (sem_c[0]["ratio"] + 0.01)) < 1e-3, "%.3f" % res["min_ratio"])

        # ── ③ calibrated ─────────────────────────────────────────────────────
        obs_p, obs_c, obs_x = (search.judge_observation() for _ in range(3))
        got_p = hook.fallback(db, P, strict=True, observe=obs_p)
        got_c = hook.fallback(db, C, strict=True, observe=obs_c)
        got_x = hook.fallback(db, X, strict=True, observe=obs_x)
        check("③ the answer both lanes hold attaches, as meaning, with the agreement named",
              [r["name"] for r in got_p] == ["ledger_rule"] and got_p[0].get("via_vector")
              and "both lanes agree" in got_p[0]["why"], "%s" % [(r["name"], r.get("why")) for r in got_p])
        check("③ the control the lanes also agree on stays below the gate", got_c == [],
              "%s" % [(r["name"], r.get("ratio")) for r in got_c])
        check("③ meaning's favourite the words never rank still comes through the single-lane gate, as before",
              [r["name"] for r in got_x] == ["harbour_tide"] and obs_x["gate"] == "ratio",
              "%s gate %s" % ([r["name"] for r in got_x], obs_x["gate"]))
        os.environ["BRAIN_EMBED_MIN_RATIO"] = "99"          # the single-lane gate silenced: agreement alone decides
        obs_x2 = search.judge_observation()
        try:
            got_x2 = hook.fallback(db, X, strict=True, observe=obs_x2)
        finally:
            os.environ.pop("BRAIN_EMBED_MIN_RATIO", None)
        check("③ with the single-lane gate silenced, agreement alone attaches nothing for X, however sharp its ratio",
              got_x2 == [] and sem_x[0]["ratio"] > vectors.min_agree_ratio(db) and obs_x2["decision"] == "below",
              "ratio %.3f vs agreement gate %.3f · decision %s" % (sem_x[0]["ratio"], vectors.min_agree_ratio(db), obs_x2["decision"]))

        # ── ④ the record ─────────────────────────────────────────────────────
        check("④ the judge-free decision is recorded: P fired by agreement · C below · X fired by the ratio gate",
              (obs_p["decision"], obs_c["decision"], obs_x["decision"]) == ("fired", "below", "fired")
              and (obs_p["gate"], obs_c["gate"], obs_x["gate"]) == ("agreement", "agreement", "ratio"),
              "%s" % [(o["outcome"], o["phase"], o["gate"], o["decision"]) for o in (obs_p, obs_c, obs_x)])
        check("④ the attachment is counted under meaning", hook._sources(got_p) == {"lexical": 0, "trigger": 0, "semantic": 1, "graph": 0})

        # ── ★control★: without the agreement rule, X fires ───────────────────
        real_agreed = search.agreed
        search.agreed = lambda db, q, k, min_ratio, words=None: [r for r in search.semantic(db, q, k=k, min_cos=0.0)
                                                                if r.get("ratio", 0.0) >= min_ratio]
        os.environ["BRAIN_EMBED_MIN_RATIO"] = "99"
        try:
            loose = hook.fallback(db, X, strict=True, observe=search.judge_observation())
        finally:
            search.agreed = real_agreed
            os.environ.pop("BRAIN_EMBED_MIN_RATIO", None)
        check("★control★ meaning alone in place of agreement (the single-lane gate silenced) fires on X — the check sees the rule",
              [r["name"] for r in loose] == ["harbour_tide"], "%s" % [r["name"] for r in loose])

        # ── ⑤ the monitor ────────────────────────────────────────────────────
        from brain import monitor
        day = observe.day()
        for o, hit in ((obs_p, True), (obs_c, False), (obs_x, True)):
            observe.write("hook", {"total": 0.1, "stage": "merge", "path": "fallback", "fb": 0.05, "fb_hit": hit,
                                   "hit": hit, "cut": False, "t": {}, "reindex": {"ran": False},
                                   "attached": {"lexical": 0, "trigger": 0, "semantic": int(hit), "graph": 0},
                                   "judge": o}, to=observe.path("hook"))
        ms = monitor.summarise(monitor.rows([day]))
        check("⑤ `brain monitor`: the stage reached 3 and finished 3 with no judge · two attached by meaning",
              ms["second"]["reached"] == 3 and ms["second"]["finished"] == 3
              and ms["second"]["outcomes"] == {"no_judge": 3} and ms["hook"]["by_source"]["semantic"] == 2,
              "%s" % ms["second"])
    finally:
        calibrate.threshold = real
    db.close()
    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — without a judge, the hook attaches what both lanes agree on, behind a gate the controls set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
