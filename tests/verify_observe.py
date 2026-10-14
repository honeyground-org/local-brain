#!/usr/bin/env python3
"""★Per-request records say where each request went — and nothing it said★ (local · budget 0)

Why this exists (2026-10-14)
----------------------------
The monitor of "words and meaning used together" needs facts the old timing line could not give: a cut
second stage looked the same whether it was embedding, searching or waiting on the judge (and on 2026-10-14
it was the judge: 5–9 s, then 503); a chosen database that is down or out of sync is silently replaced by
the local copy; prompts that never reached a search left no row, so rates had no denominator; and MCP
`recall` wrote nothing at all. §observe is the record; this check holds it down:

  ① rows are appended whole — six processes writing at once leave every row readable; a broken line is
     counted, not guessed; every row has schema, run id, time and code generation
  ② the hook records ★every way out★ (empty · command · disabled · short), the time of each stage, the
     reindex (ran, seconds, how many changed), and what was attached ★by which source★ (words · a declared
     trigger · meaning · a graph neighbour)
  ③ the second stage says where it was: done (embedded or cached, which store, request sent and returned,
     a valid answer) · cut ★while embedding★ · cut ★with the judge request out★ (sent, not returned) ·
     the provider overloaded (a fixed code, not its words) · answered from the cache (nothing sent)
  ④ ★which database answered★ — a chosen store that is not in sync is answered by the local copy, and the
     record says so; the local choice says local
  ⑤ MCP `recall` writes a row per call: whether meaning was asked for and what each way returned
  ⑥ ★no text★ — a probe word that verifiably reached the judge and the search is in no record

⛔ Control: the old record (before this change) had none of `t`, `reindex`, `attached`, `phase` — so ②–③
   are red on it; and ① is run against a writer that does two writes per row (it interleaves).
"""
from __future__ import annotations

import array
import json
import math
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-observe-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ["BRAIN_RERANK_RPM"] = "0"
os.environ.pop("BRAIN_VECTOR_STORE", None)

from brain import observe, search, server, store, vectors  # noqa: E402

FAILS = []
PROBE = "zephyrprobe41"


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


NOTES = {"ledger_rule": "The ledger is reconciled every Friday by the finance owner.",
         "deploy_note": "Deploys happen on weekdays before four in the afternoon.",
         "harbour_note": "Ferries leave the harbour at dawn."}
PROMPTS = {s: "which note explains the %s routine for %s please" % (PROBE, s)
           for s in ("semantic", "cut_judge", "busy")}

# ── the writer, run as several processes at once (①) ────────────────────────
WRITER = r'''
import os, sys
sys.path.insert(0, os.environ["ROOT"])
from brain import observe
p, n, who, mode = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
for i in range(n):
    row = {"who": who, "i": i, "pad": "x" * 300}
    if mode == "split":                                    # ⛔ the control: two writes per row
        import json
        line = json.dumps(row)
        with open(p, "a") as fh:
            fh.write(line[:150]); fh.flush(); fh.write(line[150:] + "\n")
    else:
        observe.write("t", row, to=p)
'''

# ── the hook, run as its own process (②–④) ──────────────────────────────────
DRIVER = r'''
import io, json, os, sys, time, urllib.error
sys.path.insert(0, os.environ["ROOT"])
from brain import engines, hook, search, store, vectors
shape, log, prompt = sys.argv[1:4]
hook._timing_path = lambda: log
store.needs_reindex = lambda db: shape == "reindex"
if shape == "reindex":
    def _re(db, **k):
        time.sleep(0.05)
        return {"added": 1, "updated": 2, "removed": 0}
    store.reindex = _re
hook._named = (lambda p, db=None: [("probe phrase", int(os.environ["DOC"]), os.environ["DOCNAME"])]) \
    if shape == "trigger" else (lambda p, db=None: [])
if shape in ("lexical", "reindex"):
    hook.lexical = lambda db, p: [search._present(search._hydrate(db, int(os.environ["DOC"])), 50.0, [], None, [], db)]
else:
    search.recall = lambda *a, **k: []                   # words fall silent
if shape == "trigger":
    hook.fallback = lambda db, prompt, **k: []
if shape == "cut_vector":
    vectors.embed = lambda *a, **k: (time.sleep(30), [[0.0]])[1]
def judge(text, timeout=25.0, model=""):
    if shape == "cut_judge":
        time.sleep(30)
    if shape == "busy":
        raise engines.JudgeError(503, "UNAVAILABLE", "This model is currently experiencing high demand")
    return json.dumps([{"i": i, "s": s} for i, s in enumerate([9, 2, 1])])
engines.judge_text = judge
sys.stdin = io.StringIO(json.dumps({"prompt": prompt}))
try:
    sys.exit(hook.main())
except SystemExit:
    raise
except Exception:
    sys.exit(0)
'''


def _build():
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"],
                                "exclude": ["MEMORY.md"], "max_depth": 1}]}, fh)
    for name, body in NOTES.items():
        with open(os.path.join(_MEM, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: fixture %s\n---\n\n%s\n" % (name, name, body))
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    ids = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM docs")}
    vectors.ensure_table(db)
    with db:
        for i, (name, did) in enumerate(sorted(ids.items())):
            sha = db.execute("SELECT sha FROM docs WHERE id=?", (did,)).fetchone()["sha"]
            v = [0.0] * vectors.DIM
            v[i] = 1.0
            db.execute("INSERT OR REPLACE INTO vectors(doc_id,chunk_no,sha,model,dim,vec) VALUES (?,?,?,?,?,?)",
                       (did, 0, sha, vectors.model_tag(), vectors.DIM, array.array("f", v).tobytes()))
    q = [0.0] * vectors.DIM
    q[0], q[1], q[2] = 0.8, 0.5, 0.3
    n = math.sqrt(sum(x * x for x in q))
    vectors._qcache_get(db, "warm")
    for p in list(PROMPTS.values()) + ["a question about the ledger routine"]:
        vectors._qcache_put(db, p, array.array("f", [x / n for x in q]))
    return db, ids


def _run(shape: str, prompt: str, env: dict = None) -> dict:
    log = os.path.join(_TMP, "hook-%s.jsonl" % shape)
    drv = os.path.join(_TMP, "driver.py")
    with open(drv, "w", encoding="utf-8") as fh:
        fh.write(DRIVER)
    e = dict(os.environ, ROOT=ROOT, BRAIN_RERANK_MIN="8")
    e.update(env or {})
    subprocess.run([sys.executable, drv, shape, log, prompt], capture_output=True, text=True,
                   timeout=30, env=e, encoding="utf-8", errors="replace")
    raw = open(log, encoding="utf-8").read() if os.path.exists(log) else ""
    rows = [json.loads(x) for x in raw.splitlines() if x.strip()]
    return {"row": rows[-1] if rows else {}, "raw": raw}


def main() -> int:
    print("=" * 78)
    print("per-request records — where each request went, and nothing it said")
    print("=" * 78)

    # ── ① whole rows ─────────────────────────────────────────────────────────
    for mode in ("observe", "split"):
        p = os.path.join(_TMP, "concurrent-%s.jsonl" % mode)
        w = os.path.join(_TMP, "writer.py")
        with open(w, "w", encoding="utf-8") as fh:
            fh.write(WRITER)
        procs = [subprocess.Popen([sys.executable, w, p, "300", str(j), mode], env=dict(os.environ, ROOT=ROOT))
                 for j in range(6)]
        for pr in procs:
            pr.wait(timeout=120)
        good = bad = 0
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                json.loads(line)
                good += 1
            except ValueError:
                bad += 1
        if mode == "observe":
            check("① six processes at once: every row whole and readable", good == 1800 and bad == 0,
                  "%d readable · %d broken" % (good, bad))
        else:
            check("① ★control★: a writer that splits a row does interleave (so ① could fail)", bad > 0,
                  "%d broken" % bad)
    p = os.path.join(_TMP, "one.jsonl")
    observe.write("x", {"a": 1}, to=p)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"half a row\n')
    os.environ["BRAIN_HOME_SAVE"] = os.environ["BRAIN_HOME"]
    row = json.loads(open(p, encoding="utf-8").readline())
    check("① a row carries schema, run id, UTC time and the code generation",
          all(k in row for k in ("schema", "run", "ts", "gen")) and row["ts"].endswith("Z"), json.dumps(row))
    os.makedirs(os.path.dirname(observe.path("x")), exist_ok=True)
    os.replace(p, observe.path("x"))
    rows, broken = observe.read("x", [observe.day()])
    check("① a broken line is counted, not guessed", len(rows) == 1 and broken == 1, "%d · %d" % (len(rows), broken))

    db, ids = _build()
    names = sorted(ids)
    os.environ["DOC"], os.environ["DOCNAME"] = str(ids["ledger_rule"]), "ledger_rule"

    # ── ② every way out, the stages, the sources ─────────────────────────────
    for shape, prompt, env, want in (("empty", "", None, "empty"), ("command", "/clear the screen now", None, "command"),
                                     ("disabled", "a long enough prompt to pass", {"BRAIN_HOOK_DISABLED": "1"}, "disabled"),
                                     ("short", "hi", None, "short")):
        r = _run(shape, prompt, env)
        check("② a prompt that never reached a search is recorded: %s" % want,
              r["row"].get("excluded") == want, json.dumps(r["row"])[:120])
    r = _run("lexical", "a question about the ledger routine")["row"]
    check("② words answered: attached by words, stage times, no reindex",
          r.get("attached") == {"lexical": 1, "trigger": 0, "semantic": 0, "graph": 0}
          and set(r.get("t", {})) >= {"index", "lexical", "triggers"} and r.get("reindex") == {"ran": False},
          json.dumps({k: r.get(k) for k in ("attached", "t", "reindex")}))
    r = _run("reindex", "a question about the ledger routine")["row"]
    rx = r.get("reindex") or {}
    check("② a reindex is recorded: ran, its seconds, how many documents changed",
          rx.get("ran") is True and rx.get("changed") == 3 and (rx.get("secs") or 0) >= 0.05, json.dumps(rx))
    r = _run("trigger", "a question that names a declared thread")["row"]
    check("② a declared trigger is counted as a trigger, not as words",
          r.get("attached") == {"lexical": 0, "trigger": 1, "semantic": 0, "graph": 0}, json.dumps(r.get("attached")))

    # ── ③ the second stage ───────────────────────────────────────────────────
    r = _run("semantic", PROMPTS["semantic"])
    row, j = r["row"], r["row"].get("judge") or {}
    v = j.get("vector") or {}
    check("③ done: attached by meaning, request sent and returned, a valid answer",
          row.get("attached") == {"lexical": 0, "trigger": 0, "semantic": 1, "graph": 0} and j.get("phase") == "done"
          and j.get("sent") is True and j.get("returned") is True and j.get("valid") is True
          and j.get("decision") == "fired", json.dumps({k: j.get(k) for k in ("phase", "sent", "returned", "valid", "decision")}))
    check("③   the vector search: cached embedding, answered by the local store, timed",
          v.get("embedded") is False and v.get("store") == "sqlite" and v.get("by") == "local"
          and v.get("store_s") is not None, json.dumps(v))
    raw_semantic = r["raw"]
    r = _run("semantic", PROMPTS["semantic"])
    j = r["row"].get("judge") or {}
    check("③ the same prompt again: answered from the cache — nothing sent",
          j.get("cached") is True and j.get("sent") is False, json.dumps({k: j.get(k) for k in ("cached", "sent")}))
    r = _run("cut_vector", "a question nobody embedded before about %s" % PROBE)
    j = r["row"].get("judge") or {}
    check("③ cut ★while embedding★ — phase vector, nothing sent",
          r["row"].get("cut") is True and j.get("outcome") == "cut" and j.get("phase") == "vector"
          and not j.get("sent"), json.dumps({k: j.get(k) for k in ("outcome", "phase", "sent")}))
    r = _run("cut_judge", PROMPTS["cut_judge"])
    j = r["row"].get("judge") or {}
    check("③ cut ★with the judge request out★ — sent, not returned",
          j.get("outcome") == "cut" and j.get("phase") == "judge" and j.get("sent") is True
          and j.get("returned") is False, json.dumps({k: j.get(k) for k in ("outcome", "phase", "sent", "returned")}))
    r = _run("busy", PROMPTS["busy"])
    j = r["row"].get("judge") or {}
    check("③ the provider overloaded → failed · overloaded — a fixed code, not its words",
          j.get("outcome") == "failed" and j.get("kind") == "overloaded" and j.get("fail") == "overloaded"
          and "demand" not in r["raw"], json.dumps({k: j.get(k) for k in ("outcome", "kind", "fail")}))

    # ── ④ which database answered ────────────────────────────────────────────
    os.environ["BRAIN_VECTOR_STORE"] = "qdrant"
    os.environ["BRAIN_VECTOR_STORE_URL"] = "http://127.0.0.1:9"
    try:
        vectors.search(db, PROMPTS["semantic"], k=3)
        v = vectors.last_search()
    finally:
        os.environ.pop("BRAIN_VECTOR_STORE", None)
        os.environ.pop("BRAIN_VECTOR_STORE_URL", None)
    check("④ a chosen store not in sync → the local copy answered, and the record says why",
          v.get("store") == "sqlite" and v.get("by") == "local" and v.get("why") == "not_synced", json.dumps(v))
    vectors.search(db, PROMPTS["semantic"], k=3)
    v = vectors.last_search()
    check("④ ★control★: the local choice says local, with no reason", v.get("by") == "local" and v.get("why") is None,
          json.dumps(v))

    # ── ⑤ MCP recall ─────────────────────────────────────────────────────────
    server.call_tool(db, "recall", {"query": "ledger routine %s" % PROBE, "terms": ["finance", "friday"]})
    server.call_tool(db, "recall", {"query": PROMPTS["semantic"], "k": 1, "semantic_k": 2, "kinds": ["project"]})
    server.call_tool(db, "recall", {"query": "never embedded %s" % PROBE, "k": 1, "semantic_k": 2})
    rows, _ = observe.read("recall", [observe.day()])
    a, b, c = (rows + [{}, {}, {}])[:3]
    # unasked, meaning is added only when the words are unsure (§server._words_unsure) — the record says which
    check("⑤ a recall without semantic_k: words counted, not asked, and whether meaning was added unasked",
          a.get("semantic_k") == 0 and a.get("auto") in (True, False)
          and (a.get("meaning") is None) == (not a.get("auto")) and a.get("terms") == 2 and a.get("words", 0) >= 1,
          json.dumps({k: a.get(k) for k in ("semantic_k", "auto", "meaning", "terms", "words")}))
    check("⑤ with it: how many meaning returned, filtered, embedding cached, which store",
          b.get("semantic_k") == 2 and b.get("meaning") is not None and b.get("filtered") is True
          and b.get("embed_cached") is True and (b.get("vector") or {}).get("store") == "sqlite",
          json.dumps({k: b.get(k) for k in ("semantic_k", "meaning", "filtered", "embed_cached")}))
    check("⑤ no engine → a fixed failure code", c.get("meaning") == 0 and c.get("meaning_fail") == "NoKey",
          json.dumps({k: c.get(k) for k in ("meaning", "meaning_fail")}))

    # ── ⑥ no text ────────────────────────────────────────────────────────────
    every = raw_semantic + "".join(open(observe.path(k), encoding="utf-8").read()
                                   for k in ("recall",) if os.path.exists(observe.path(k)))
    for f in os.listdir(_TMP):
        if f.startswith("hook-") and f.endswith(".jsonl"):
            every += open(os.path.join(_TMP, f), encoding="utf-8").read()
    check("⑥ ★no text★ — the probe word is in no record", PROBE not in every and "Friday" not in every)
    db.close()

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — each request says where it went, and nothing it said")
    return 0


if __name__ == "__main__":
    sys.exit(main())
