#!/usr/bin/env python3
"""★When the words are unsure, an agent's `recall` asks for learned synonyms, then lets three rankings order★ (local · budget 0)

Why this exists (2026-10-14)
----------------------------
On the author's 66 labelled questions the answer sat in the top 50 of words or meaning for 63, and in the top 3
for 35 — found, then lost in the ordering. Measured with no model, as `recall` runs with the agent's terms:
when the words are unsure (the hook's own rule), adding the person's learned synonyms and then merging words,
meaning and the whole-body passage by reciprocal rank took the top 3 from 43 to 48. In the hook neither helped,
and synonyms deciding whether to attach took prompts that need no memory from 3 to 17 of 34 — so the hook is
left as it is, and so is every answer where the words are sure.

This builds a corpus whose vectors it writes itself (no network) and holds down:

  ① ★the passage★ — a document's worth is the most question one window holds, wherever it sits, and its length
     around that window changes nothing (how grep sees a line)
  ② `search.fused` merges words, meaning and the passage by reciprocal rank: a long document whose question words
     sit together at its end, below others by words, rises; a document only meaning found can be in the list
  ③ words sure → `recall` answers exactly as the words do: no synonym asked for, no reordering, nothing embedded
  ④ words unsure and a learned synonym makes them sure → the words' list with the synonyms named, no reordering;
     ★the default `recall` (the hook's) still leaves the synonyms out★ while the measured decision is off
  ⑤ still unsure → the list says it is ordered by the three together, and meaning's own list adds only what is
     not shown; `semantic_k: 0` keeps meaning out of both; without an embedding engine the other two still order
  ⑥ the record says unsure · how many synonyms · reordered — counts only, no words

⛔ Control: with `search.fused` replaced by the words' own order, ② and ⑤ turn red — the check sees the reordering.
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

_TMP = tempfile.mkdtemp(prefix="brain-unsure-order-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ.pop("BRAIN_LEXICON", None)

from brain import calibrate, lexicon, observe, search, server, store, vectors  # noqa: E402

FAILS = []
Q = "ledger reconciliation friday"
FILL = ("meadow river lantern harbour copper orchard signal window garden ferry candle marble " * 80)[:4000]
NOTES = {
    # words rank this first: every question word, scattered through a short note
    "spread_note": "ledger notes. " + FILL[:600] + " reconciliation later. " + FILL[:600] + " friday maybe.",
    # the answer: the question's words together, at the end of a long document
    "long_report": FILL + FILL + " The ledger reconciliation closes every friday. ",
    "only_ledger": "The ledger lives in the dry cabinet.",
    "quiet_note": "Ferries leave the harbour at dawn.",
    "tally_rule": "A tally of crates is kept beside the gate.",
}


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


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
                k = 20 * sorted(ids).index(name) + n
                axis[(name, n)] = k
                v = [0.0] * vectors.DIM
                v[k] = 1.0
                db.execute("INSERT OR REPLACE INTO vectors(doc_id,chunk_no,sha,model,dim,vec) VALUES (?,?,?,?,?,?)",
                           (did, n, row["sha"], vectors.model_tag(), vectors.DIM, array.array("f", v).tobytes()))
    # meaning: tally_rule first (no question word in it), then long_report's first chunk, then quiet_note
    q = [0.0] * vectors.DIM
    for (name, n), w in ((("tally_rule", 0), 0.8), (("long_report", 0), 0.5), (("quiet_note", 0), 0.3)):
        q[axis[(name, n)]] = w
    norm = math.sqrt(sum(x * x for x in q))
    vectors._qcache_get(db, Q)
    vectors._qcache_put(db, Q, array.array("f", [x / norm for x in q]))
    # a learned synonym — the person says "accounts", the notes say "ledger" — seen enough times to be used
    lexicon.ensure(db)
    with db:
        for _ in range(5):
            lexicon.observe(db, "accounts", "ledger", "fixture")
    return db


def names_in(text: str) -> list:
    return [n for n in sorted(NOTES, key=lambda n: text.find("%s  [" % n)) if "%s  [" % n in text]


def main() -> int:
    print("=" * 78)
    print("words unsure → learned synonyms, then words + meaning + passage order an agent's recall")
    print("=" * 78)
    db = _build()

    # ── ① the passage ────────────────────────────────────────────────────────
    w = search.query_weights(db, Q)
    together = search.passage_worth(FILL + " ledger reconciliation friday", w)
    apart = search.passage_worth("ledger " + FILL + " reconciliation " + FILL + " friday", w)
    longer = search.passage_worth(FILL * 10 + " ledger reconciliation friday" + FILL * 10, w)
    check("① the question's words together outweigh the same words apart", together > apart,
          "%.2f vs %.2f" % (together, apart))
    check("① the length around the window changes nothing", abs(longer - together) < 1e-9)

    # ── ② fused ──────────────────────────────────────────────────────────────
    rows = search.recall(db, Q, k=search.FUSE_POOL, log=False)
    words = [r["name"] for r in rows if not r.get("related")]
    fz, why = search.fused(db, Q, rows, 5)
    order = [r["name"] for r in fz]
    check("② ★control★: by words alone the long report is not first", words and words[0] != "long_report",
          "%s" % words)
    check("② merged: the long report, its words together at the end, rises above where words put it",
          "long_report" in order and order.index("long_report") < words.index("long_report"),
          "%s → %s" % (words, order))
    check("② a document only meaning found can be in the list (tally_rule has no question word)",
          "tally_rule" in order and "tally_rule" not in words, "%s" % order)

    real = calibrate.threshold
    try:
        # ── ③ words sure ─────────────────────────────────────────────────────
        calibrate.threshold = lambda db, default=0.0: 0.0
        sure = server.call_tool(db, "recall", {"query": Q, "terms": ["books"], "k": 3})
        words_only = server._fmt_recall(search.recall(db, Q, k=3, extra_terms=["books"], log=False, bridge=False))
        check("③ words sure → exactly the words' answer: no synonym, no reordering, nothing added",
              sure.split("\n\n⛔ hint")[0] == words_only, sure[:120])

        # ── ④ a learned synonym makes the words sure ─────────────────────────
        calibrate.threshold = lambda db, default=0.0: 0.01
        hook_view = search.recall(db, "accounts routine", k=3, log=False)
        hook_bridged = search.last_bridged()
        syn = server.call_tool(db, "recall", {"query": "accounts routine", "terms": ["vault"], "k": 3})
        check("④ the default recall (the hook's) leaves learned synonyms out while the decision is off",
              not hook_bridged and not hook_view, "%s" % hook_bridged)
        check("④ unsure, then a synonym → the words' list with it named, not reordered, no meaning list",
              "learned synonyms added: ledger" in syn and "ordered by words, meaning" not in syn
              and "close in meaning" not in syn and "only_ledger" in syn, syn[:200])

        # ── ⑤ still unsure ───────────────────────────────────────────────────
        calibrate.threshold = lambda db, default=0.0: 1e9
        un = server.call_tool(db, "recall", {"query": Q, "terms": ["books"], "k": 3})
        main_part, _, extra = un.partition("— close in meaning")
        shown, added = names_in(main_part), names_in(extra)
        check("⑤ still unsure → the list says it is ordered by the three together, the long report first",
              "ordered by words, meaning" in main_part and shown[:1] == ["long_report"], "%s" % shown)
        check("⑤ meaning's own list adds only what is not shown", added and not set(added) & set(shown),
              "%s | %s" % (shown, added))
        off = server.call_tool(db, "recall", {"query": Q, "terms": ["books"], "k": 3, "semantic_k": 0})
        check("⑤ `semantic_k: 0` keeps meaning out of both", "close in meaning" not in off
              and "ordered by words, meaning" not in off)
        blind = server.call_tool(db, "recall", {"query": "ledger reconciliation never embedded", "k": 3})
        check("⑤ no embedding engine → words and the passage still order, and nothing says unavailable",
              "ordered by words, meaning" in blind and "unavailable" not in blind, blind[:120])
    finally:
        calibrate.threshold = real

    # ── ⑥ the record ─────────────────────────────────────────────────────────
    rec, _ = observe.read("recall", [observe.day()])
    got = [(r.get("unsure"), r.get("bridged"), r.get("reordered")) for r in rec]
    check("⑥ the record: sure · synonym · reordered · meaning kept out · blind — counts only",
          got == [(False, 0, False), (True, 1, False), (True, 0, True), (True, 0, False), (True, 0, True)], "%s" % got)
    from brain import monitor
    ms = monitor.summarise(monitor.rows([observe.day()]))
    check("⑥ `brain monitor` counts them: 5 calls · unsure 4 · a synonym added 1 · reordered 2",
          tuple(ms["recall"][k] for k in ("calls", "unsure", "bridged", "reordered")) == (5, 4, 1, 2)
          and "4" in monitor.render(ms, {}, "test"), "%s" % ms["recall"])
    raw = open(observe.path("recall"), encoding="utf-8").read()
    check("⑥ no word of the question or of a synonym in the record",
          not any(x in raw for x in ("ledger", "accounts", "vault")))
    db.close()

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — unsure words ask for synonyms, then three rankings order; sure words are untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
