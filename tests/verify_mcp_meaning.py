#!/usr/bin/env python3
"""★MCP `recall` also returns what meaning found — when asked or when the words are unsure, apart★ (local · budget 0)

Why this exists (2026-10-13)
----------------------------
`search.semantic`'s docstring said MCP `recall` appends memories close in meaning; `server.py` never did,
so an agent's own search used words only. Measured on the author's 66 labelled questions, question alone:
words found 13 answers meaning missed and meaning 15 that words missed. `semantic_k` lets the caller ask
for up to k more, found by meaning — opt-in (default 0), in a separate list, never a re-rank of the words.

This builds a corpus whose vectors it writes itself (no network, no budget) and holds down:

  ① ★nothing changes unless asked★ — without `semantic_k`, or with 0, the answer is the same text as before;
     with it, the word results come first, unchanged
  ② the list: at most k, none already shown above, the `kinds` / `sources` filters apply (asking again for
     more when a filter eats the pool), memories always in context are left out
  ③ each row reads the passage of the chunk that matched, not the head
  ④ ★a failure is said★ — no embedding engine → "unavailable" with the reason; the word results still come
  ⑤ the cost is shown (cached embedding or one call) and ★no judge call★ is made
  ⑥ ★unasked, meaning comes only when the words are unsure★ (2026-10-14) — the hook's own rule with the calibrated
     threshold finds nothing to attach: then a few are added, said to be added for that reason; where the words
     are sure, the answer is the word results alone and no embedding is asked for; `semantic_k: 0` turns it off;
     a failure while unasked is recorded, not shown; the record tells an added list from an asked one

⛔ Control: the meaning list's top item is one the words do not find at all — so ② would be red on code that
   re-ranked words instead of searching by meaning.
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

_TMP = tempfile.mkdtemp(prefix="brain-mcpmeaning-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")

from brain import calibrate, engines, observe, search, server, store, vectors  # noqa: E402

FAILS = []
DEEP = "quillwort"
Q = "which rule keeps the books honest"


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def filler(n: int, seed: str) -> str:
    words = "meadow river lantern harbour copper orchard signal window garden ferry candle marble".split()
    out, i = [], 0
    while sum(len(w) + 1 for w in out) < n:
        out.append(words[(i * 5 + len(seed)) % len(words)])
        i += 1
    return " ".join(out)[:n]


NOTES = {   # name: (kind, body)
    "books_rule": ("project", "The rule that keeps the books honest: reconcile every Friday."),
    "quarry_note": ("project", "Stone from the north pit is cut before the frost arrives."),
    "deep_note": ("project", filler(2500, "a") + " A %s count keeps stock true. " % DEEP + filler(500, "b")),
    "copper_note": ("reference", "Copper wire is stored in the dry shed."),
    "harbour_note": ("project", "Ferries leave the harbour at dawn."),
}


def _build():
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"],
                                "exclude": ["MEMORY.md"], "max_depth": 1}]}, fh)
    for name, (kind, body) in NOTES.items():
        with open(os.path.join(_MEM, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: fixture %s\nmetadata:\n  type: %s\n---\n\n%s\n"
                     % (name, name, kind, body))
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
                db.execute("INSERT OR REPLACE INTO vectors(doc_id,chunk_no,sha,model,dim,vec) "
                           "VALUES (?,?,?,?,?,?)", (did, n, row["sha"], vectors.model_tag(),
                                                     vectors.DIM, array.array("f", v).tobytes()))
    # the question points at quarry_note, then books_rule (which the words already show — it must not come
    # again), then deep_note's third chunk, then copper_note
    q = [0.0] * vectors.DIM
    for (name, n), w in ((("quarry_note", 0), 0.8), (("books_rule", 0), 0.6),
                         (("deep_note", 2), 0.5), (("copper_note", 0), 0.2)):
        q[axis[(name, n)]] = w
    norm = math.sqrt(sum(x * x for x in q))
    vectors._qcache_get(db, Q)                              # the cache table is made on first read
    vectors._qcache_put(db, Q, array.array("f", [x / norm for x in q]))
    return db


def main() -> int:
    print("=" * 78)
    print("MCP recall can also return what meaning found — when asked, apart")
    print("=" * 78)
    db = _build()
    calls = []
    real_judge, real_threshold = engines.judge_text, calibrate.threshold
    engines.judge_text = lambda *a, **k: calls.append(1) or "[]"
    calibrate.threshold = lambda db, default=0.0: 0.0          # every word result clears it: the words are sure
    try:
        plain = server.call_tool(db, "recall", {"query": Q, "k": 1, "terms": ["ledger"]})
        zero = server.call_tool(db, "recall", {"query": Q, "k": 1, "terms": ["ledger"], "semantic_k": 0})
        two = server.call_tool(db, "recall", {"query": Q, "k": 1, "terms": ["ledger"], "semantic_k": 2})
        refs = server.call_tool(db, "recall", {"query": Q, "k": 1, "terms": ["ledger"], "semantic_k": 1,
                                               "kinds": ["reference"]})
        missing_plain = server.call_tool(db, "recall", {"query": "a question never embedded", "k": 1,
                                                        "terms": ["ledger"]})
        missing = server.call_tool(db, "recall", {"query": "a question never embedded", "k": 1,
                                                  "terms": ["ledger"], "semantic_k": 2})
        sure_rows = len(observe.read("recall", [observe.day()])[0])
        calibrate.threshold = lambda db, default=0.0: 1e9          # nothing clears it: the words are unsure
        unsure = server.call_tool(db, "recall", {"query": Q, "k": 1, "terms": ["ledger"]})
        unsure_off = server.call_tool(db, "recall", {"query": Q, "k": 1, "terms": ["ledger"], "semantic_k": 0})
        unsure_fail = server.call_tool(db, "recall", {"query": "a question never embedded", "k": 1,
                                                      "terms": ["ledger"]})
    finally:
        engines.judge_text, calibrate.threshold = real_judge, real_threshold

    # ── ① where the words are sure, nothing changes unless asked ─────────────
    check("① words sure: without semantic_k and with 0, the same text", plain == zero,
          "%d vs %d chars" % (len(plain), len(zero)))
    check("① with it, the word results come first, unchanged", two.startswith(plain) and len(two) > len(plain))
    head = plain.split("\n")[0]
    check("①   the word answer is the one the words find", "books_rule" in head, head)

    # ── ② the list ───────────────────────────────────────────────────────────
    section = two[len(plain):]
    listed = [n for n in NOTES if "%s  [" % n in section]
    check("② ★control★: its first item is one the words do not find at all (quarry_note)",
          section.find("quarry_note") >= 0 and "quarry_note" not in plain
          and section.find("quarry_note") < section.find("deep_note"), "%s" % listed)
    check("② at most k, and none already shown above", len(listed) == 2 and "books_rule" not in listed,
          "%s" % listed)
    rsec = refs.split("— close in meaning")[-1]
    check("② the kinds filter applies — and asks again when the filter eats the pool",
          "copper_note" in rsec and not any(n in rsec for n in NOTES if n != "copper_note"),
          rsec.strip()[:160])

    # ── ③ the passage that matched ───────────────────────────────────────────
    deep_item = section[section.find("deep_note"):]
    check("③ a row shows the passage of the chunk that matched, not the head — inside what is shown",
          DEEP in deep_item.split("\n")[3] and "keeps" in deep_item.split("\n")[3],
          deep_item[:200].replace("\n", " | "))

    # ── ④ ⑤ failure, cost, no judge ──────────────────────────────────────────
    check("④ no embedding engine → 'unavailable' with the reason, the word results still come",
          "unavailable — NoKey" in missing and missing.startswith(missing_plain),
          missing.split("— close in meaning")[-1].strip()[:120])
    check("⑤ the cost is shown", "query embedding cached" in two and "one embedding call" in missing)
    check("⑤ ★no judge call★", not calls, "%d" % len(calls))
    schema = [t for t in server.TOOLS if t["name"] == "recall"][0]["inputSchema"]["properties"]
    check("⑤ the tool says when it comes unasked, and how to turn it off", "semantic_k" in schema
          and "unsure" in schema["semantic_k"]["description"] and "0: never" in schema["semantic_k"]["description"])
    # ⛔ ★An option nobody is told about is never used★ (2026-10-14: the first day of records showed MCP recall
    #    called and meaning never asked for) — the tool's own description says when to ask for it.
    listed = [t for t in server.tools_for(db) if t["name"] == "recall"][0]     # what a host is sent
    check("⑤ the tool's description tells a caller when to ask for meaning",
          "semantic_k" in listed["description"] and "meaning" in listed["description"], listed["description"][-160:])

    # ── ⑥ unasked, only when the words are unsure ────────────────────────────
    rec, _ = observe.read("recall", [observe.day()])
    check("⑥ words sure, unasked: no embedding asked for", not any(r.get("vector") for r in rec[:2])
          and all(r.get("meaning") is None for r in rec[:2]), "%s" % [(r.get("auto"), r.get("meaning")) for r in rec[:2]])
    usec = unsure[len(plain):]
    added = [n for n in NOTES if "%s  [" % n in usec]
    check("⑥ words unsure, unasked: the word results come first, unchanged, then meaning's list",
          unsure.startswith(plain) and "quarry_note" in added, "%s" % added)
    check("⑥ the list says why it came, and holds at most %d" % server.AUTO_MEANING,
          "the words were unsure" in usec and 0 < len(added) <= server.AUTO_MEANING)
    check("⑥ `semantic_k: 0` turns it off", unsure_off == plain)
    check("⑥ a failure while unasked is not shown — the word results alone",
          "unavailable" not in unsure_fail and "close in meaning" not in unsure_fail)
    tail_rows = rec[sure_rows:]
    check("⑥ the record tells an added list from an asked one (semantic_k 0 · auto) — and keeps the failure",
          [(r.get("semantic_k"), r.get("auto")) for r in tail_rows] == [(0, True), (0, False), (0, True)]
          and tail_rows[0].get("meaning") and tail_rows[2].get("meaning_fail") == "NoKey",
          "%s" % [(r.get("semantic_k"), r.get("auto"), r.get("meaning"), r.get("meaning_fail")) for r in tail_rows])
    from brain import monitor
    ms = monitor.summarise(monitor.rows([observe.day()]))
    check("⑥ `brain monitor` counts the added apart from the asked, and how often meaning was used",
          # added: the two unsure calls, and the question no word matches even with the words sure (nothing found
          # is the surest kind of unsure)
          (ms["recall"]["calls"], ms["recall"]["semantic_asked"], ms["recall"]["auto"]) == (9, 3, 3)
          and monitor.metrics(ms)["meaning_used_rate"] == round(3 / 9, 3), "%s" % ms["recall"])
    db.close()

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — meaning comes when asked or when the words are unsure, apart from the words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
