#!/usr/bin/env python3
"""★The judge can be shown the part of a document that matched★ (local · budget 0)

Why this exists (2026-10-13)
----------------------------
Similarity search keeps a document's best chunk score but not which chunk earned it, and the judge read a
candidate's description plus the first 420 characters of its body. For half the answers in the vector top
20 the chunk that matched was further in, so what matched could not reach the judge.

`vectors.best_chunks` recovers the chunk from the local copy, `search.evidence` takes a passage from it,
and `search.judged(excerpt="evidence")` sends that passage — an experiment path; the hook still reads the
head. This check builds a corpus whose vectors it writes itself (no network, no budget) and holds down:

  ① the chunk spans are exactly the chunks that were embedded (every length, the 12-chunk cap)
  ② the best chunk is the one closest to the query, a tie goes to the lower number
  ③ ★evidence from another version is not evidence★ — a document edited since it was embedded, or cut
     with other settings, is left out; without the query's vector nothing is answered
  ④ the passage: is where the question's words are, stays inside its chunk, is at most the width, counts a word once,
     takes the earliest of equal windows, falls back to the centre (a chunk) or the start (a whole body)
  ⑤ `judged(excerpt="evidence")` sends the passage, keeps the candidates and their order, records its
     policy, and a stale document keeps its head
  ⑥ the default is unchanged — the head is sent and the policy says so
  ⑦ `query_weights` weighs the question's words exactly as `recall` does (the shared gram rule)
  ⑧ for the experiment: an answer that leaves candidates unscored is told apart from a 0, and `cached()`
     answers from the cache without ever calling the judge

⛔ Control: in ⑤ the default mode ★does not★ send the deep word — so the evidence row can tell the two apart.
"""
from __future__ import annotations

import array
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-evidence-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ["BRAIN_RERANK_RPM"] = "0"

from brain import engines, rerank, search, store, textindex, vectors  # noqa: E402

FAILS = []
DEEP = "quillwort"                     # a word only the third chunk of one note holds


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def filler(n: int, seed: str) -> str:
    words = ("meadow river lantern harbour copper orchard signal ledger window garden "
             "ferry candle quarry thistle marble beacon").split()
    out, i = [], 0
    while sum(len(w) + 1 for w in out) < n:
        out.append(words[(i * 7 + len(seed)) % len(words)])
        i += 1
    return " ".join(out)[:n]


def unit(k: int) -> array.array:
    v = [0.0] * vectors.DIM
    v[k % vectors.DIM] = 1.0
    return array.array("f", v)


def _build():
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"],
                                "exclude": ["MEMORY.md"], "max_depth": 1}]}, fh)
    deep_body = filler(2500, "a") + " the %s rule keeps the ledger honest. " % DEEP + filler(600, "b")
    notes = {"deep_note": deep_body, "short_note": "A short note about copper and harbours.",
             "edited_note": filler(3000, "c"), "recut_note": filler(3000, "d")}
    for name, body in notes.items():
        with open(os.path.join(_MEM, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: fixture %s\n---\n\n%s\n" % (name, name, body))
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    ids = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM docs")}
    vectors.ensure_table(db)
    with db:
        for name, did in ids.items():
            row = db.execute("SELECT sha, body FROM docs WHERE id=?", (did,)).fetchone()
            for n in range(len(vectors.chunk_spans(row["body"] or ""))):
                # chunk n of note i points along axis 10·i + n — the query picks the axis it wants
                k = 10 * sorted(ids).index(name) + n
                db.execute("INSERT OR REPLACE INTO vectors(doc_id,chunk_no,sha,model,dim,vec) "
                           "VALUES (?,?,?,?,?,?)", (did, n, row["sha"], vectors.model_tag(),
                                                     vectors.DIM, unit(k).tobytes()))
        # a document edited since it was embedded · one cut with other settings (a chunk missing)
        db.execute("UPDATE vectors SET sha='older' WHERE doc_id=?", (ids["edited_note"],))
        db.execute("DELETE FROM vectors WHERE doc_id=? AND chunk_no=1", (ids["recut_note"],))
    return db, ids


def main() -> int:
    print("=" * 78)
    print("the judge can be shown the part of a document that matched")
    print("=" * 78)

    # ── ① spans = what was embedded ──────────────────────────────────────────
    bad = []
    for n in (0, 1, 199, 1199, 1200, 1201, 2000, 5000, 40000):
        body = "  " + filler(n, str(n)) + "\n"
        spans, cs = vectors.chunk_spans(body), vectors.chunks(body)
        st = body.strip()
        if len(spans) != len(cs) or any(st[a:b] != c for (a, b), c in zip(spans, cs)):
            bad.append(n)
    check("① chunk spans are exactly the embedded chunks (lengths 0 … 40,000, the 12-chunk cap)",
          not bad, "mismatch at %s" % bad)

    db, ids = _build()
    names = sorted(ids)
    q = "which rule keeps the ledger honest"
    deep = ids["deep_note"]
    axis = 10 * names.index("deep_note") + 2              # deep_note's third chunk
    vectors._qcache_get(db, q)                           # the cache table is made on first read, as search does
    vectors._qcache_put(db, q, unit(axis))

    # ── ② best chunk ─────────────────────────────────────────────────────────
    got = vectors.best_chunks(db, q, list(ids.values()))
    check("② the best chunk is the one closest to the query", got.get(deep, (None,))[0] == 2,
          "deep_note → %s" % (got.get(deep),))
    vectors._qcache_put(db, "a tie", array.array("f", [0.0] * vectors.DIM))
    tie = vectors.best_chunks(db, "a tie", [deep])
    check("② a tie goes to the lower chunk number", tie.get(deep, (None,))[0] == 0, "%s" % (tie,))

    # ── ③ another version is not evidence ────────────────────────────────────
    check("③ a document edited since it was embedded is left out", ids["edited_note"] not in got)
    check("③ a document cut with other settings is left out", ids["recut_note"] not in got)
    check("③ ★control★: the untouched ones are answered", deep in got and ids["short_note"] in got)
    check("③ without the query's vector nothing is answered",
          vectors.best_chunks(db, "never embedded", [deep]) == {})

    # ── ④ the passage ────────────────────────────────────────────────────────
    row = search._hydrate(db, deep)
    body = (row["body"] or "").strip()
    a, b = vectors.chunk_spans(body)[2]
    ev = search.evidence(db, q, row, 2)
    check("④ the passage is where the question's words are (a sentence deep in the third chunk)",
          DEEP in ev["text"] and "honest" in ev["text"], ev["rule"])
    check("④ it stays inside its chunk and within the width",
          a <= ev["start"] < ev["end"] <= b and ev["end"] - ev["start"] <= rerank.EXCERPT,
          "%d–%d in chunk %d–%d" % (ev["start"], ev["end"], a, b))
    w = [("common", 1.0, ["aa"]), ("rare", 2.0, ["zz"])]     # counted four times, the common word would win
    text = "zz" + "." * 30 + "aa aa aa aa" + "." * 30
    s, e, _ = search._passage(text, w, 0, len(text), 20, "centre")
    check("④ a word counts once — one rare word beats a common one repeated", "zz" in text[s:e],
          repr(text[s:e]))
    text = "." * 10 + "zz" + "." * 40 + "zz" + "." * 10
    s, e, _ = search._passage(text, [("rare", 5.0, ["zz"])], 0, len(text), 8, "centre")
    check("④ of equal windows the earliest wins", s <= 10, "start %d" % s)
    s, e, r = search._passage("." * 100, [("rare", 5.0, ["zz"])], 0, 100, 20, "centre")
    check("④ no word in a chunk → its centre", (s, e, r) == (40, 60, "centre"), "%s" % ((s, e, r),))
    s, e, r = search._passage("." * 100, [("rare", 5.0, ["zz"])], 0, 100, 20, "start")
    check("④ no word in a whole body → its start", (s, e, r) == (0, 20, "start"), "%s" % ((s, e, r),))

    rows_, cands_, found_ = search.judge_inputs(db, q, [(deep, None, None)], "evidence", vector_ids=[])
    check("④ a document only words found reads a passage from its whole body",
          found_[0] is not None and found_[0]["chunk"] is None and DEEP in cands_[0].get("excerpt", ""),
          json.dumps({k: v for k, v in (found_[0] or {}).items() if k != "text"}))

    # ── ⑤ ⑥ what the judge is sent ───────────────────────────────────────────
    sent = []
    real_search, real_judge = vectors.search, engines.judge_text
    order = [ids["edited_note"], deep, ids["short_note"]]
    vectors.search = lambda db_, q_, k=5: [(d, 0.9 - 0.1 * i, 1.2) for i, d in enumerate(order)]

    def judge(text, timeout=25.0, model=""):
        sent.append(text)
        return json.dumps([{"i": i, "s": s} for i, s in enumerate([2, 9, 1])])
    engines.judge_text = judge
    os.environ["BRAIN_RERANK_MIN"] = "8"
    try:
        o_head, o_ev = search.judge_observation(), search.judge_observation()
        r_head = search.judged(db, q, cache=False, observe=o_head)
        r_ev = search.judged(db, q, cache=False, observe=o_ev, excerpt="evidence")
    finally:
        vectors.search, engines.judge_text = real_search, real_judge
        os.environ.pop("BRAIN_RERANK_MIN", None)
    head_text, ev_text = (sent + ["", ""])[:2]

    def listed(t):                                       # candidate names in the order they were sent
        return sorted((n for n in names if "] %s —" % n in t), key=lambda n: t.find("] %s —" % n))
    check("⑥ ★control★: the default sends the head — the deep word is not in it", DEEP not in head_text)
    check("⑤ evidence mode sends the passage — the deep word reaches the judge", DEEP in ev_text)
    check("⑤ same candidates, same order", listed(head_text) == listed(ev_text) and len(listed(ev_text)) == 3,
          "%s" % listed(ev_text))
    edited_head = filler(3000, "c")[:60]
    check("⑤ a stale document keeps its head in evidence mode", edited_head in ev_text)
    check("⑤ ⑥ the record names the policy each was scored under",
          o_head["policy"] == "vector20+head%d" % rerank.EXCERPT
          and o_ev["policy"] == "vector20+evidence%d" % rerank.EXCERPT,
          "%s · %s" % (o_head["policy"], o_ev["policy"]))
    check("⑤ the answer shows the passage the judge read",
          r_ev and r_ev[0]["name"] == "deep_note" and DEEP in r_ev[0].get("excerpt", "")
          and r_ev[0].get("evidence", {}).get("chunk") == 2
          and r_head and "evidence" not in r_head[0],
          json.dumps(r_ev[0].get("evidence") if r_ev else None))
    try:
        search.judged(db, q, excerpt="tail")
        unknown = False
    except ValueError:
        unknown = True
    check("⑤ an unknown excerpt is refused, not guessed", unknown)

    # ── ⑧ what a measurement needs from the judge ────────────────────────────
    rerank._parse(json.dumps([{"i": 0, "s": 9}, {"i": 0, "s": 3}, {"i": 2, "s": 11}]), 3)
    check("⑧ an answer that leaves a candidate unscored is told apart from a 0",
          rerank.last_answer_shape() == {"n": 3, "answered": 2, "duplicates": 1, "out_of_range": 1},
          json.dumps(rerank.last_answer_shape()))
    calls = []
    engines.judge_text = lambda *x, **k: calls.append(1) or json.dumps([{"i": 0, "s": 7}])
    try:
        cands = [{"name": "deep_note", "description": "d", "body": "b"}]
        miss = rerank.cached("a question nobody asked", cands, db)
        rerank.score("a question asked once", cands, db=db, cache=True)    # ★score★ files its answer
        hit = rerank.cached("a question asked once", cands, db)            # ★cached★ must find it
        other = rerank.cached("a question asked once", [dict(cands[0], body="c")], db)
    finally:
        engines.judge_text = real_judge
    check("⑧ `cached()` never calls the judge, and finds what `score()` would find",
          miss is None and hit == [7.0] and other is None and len(calls) == 1,
          "calls %d · hit %s · another excerpt %s" % (len(calls), hit, other))

    # ── ⑦ the shared gram rule ───────────────────────────────────────────────
    stats = store.corpus_stats(db)
    _, _, word_idf = search._score_terms(db, textindex.query_terms(q), max(1, stats["docs"]),
                                         max(1.0, stats["avg_doclen"]), None, None)
    mine = {w: idf for w, idf, _ in search.query_weights(db, q)}
    check("⑦ query_weights weighs the question's words exactly as recall does",
          mine == word_idf and mine, "%s" % sorted(mine))
    db.close()

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — the matched passage can reach the judge, and the default is unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
