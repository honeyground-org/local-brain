#!/usr/bin/env python3
"""recall JSON mode — ★the same rows as the text, in fields a program can trust★ (orbit-1, 2026-10-29).

## Why this check exists

A client that keeps a ledger (Cosmos Orbit) reads `recall(format="json")` and acts on it: it cites
`name`/`path`/`sha` as the thing it used, and it treats `meta.partial` as "the meaning lane was asked
and could not answer". If the JSON listed different rows from the text, or said `partial: false` while
the engine was down, the client would be certain about the wrong thing. So the two shapes are held
together, and what is not known is null.

## What is held down

  ① text mode is untouched — `format` omitted and `format: "text"` answer byte for byte the same
  ② the JSON lists ★exactly the names the text numbers, in the same order★; related rows sit apart
     (control: the fixture must yield both an answer and a related row, or ② proves nothing)
  ③ every row carries a stable id — `name` · `path` · a 40-hex `sha` equal to the index's — a boolean
     `stale`, a bounded excerpt, ranks 1..n, and `project_id` null until a memory declares one (§O4)
  ④ what was not asked is null; what could not be served is said — `semantic_k: 0` → `meaning` null,
     `partial` false, `embed_cached` null; meaning asked → `partial` is true exactly when no row came,
     and then carries a reason
  ⑤ arguments are checked in JSON mode — a 128-character `request_id` is echoed, 129 is refused with
     `invalid_argument`, an unknown `format` is refused (control: the valid call right before passed)

Seeds its own four notes in a private home; runs in a clean room.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-recall-json-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ.pop("BRAIN_LEXICON", None)

from brain import calibrate, server, store  # noqa: E402

# The graph bridge opens only above the confidence line, which a four-note fixture cannot calibrate;
# this check measures the JSON shape, not the gate, so the line is set low here (never in the live home).
calibrate.threshold = lambda db, default=0.0, measure=True: 0.1  # noqa: E731

FAILS = []
Q = "ledger reconciliation"
T = ["harbour"]
NOTES = {
    "ledger_rule": "The ledger reconciliation closes every friday in the harbour office. related: [[tally_rule]]",
    "ledger_history": "Ledger reconciliation used to happen on mondays before the harbour move.",
    "tally_rule": "A tally of crates is kept beside the harbour gate.",
    "quiet_note": "Ferries leave the harbour at dawn.",
}


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + str(detail)[:200]) if detail else ""))
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
    return db


def _call(db, args):
    return server.call_tool(db, "recall", args, _measure=True)


def main() -> int:
    db = _build()
    print("=" * 72 + "\n① text mode untouched\n" + "=" * 72)
    text_a = _call(db, {"query": Q, "terms": T, "k": 2, "semantic_k": 0})
    text_b = _call(db, {"query": Q, "terms": T, "k": 2, "semantic_k": 0, "format": "text"})
    check("format omitted == format text, byte for byte", text_a == text_b and len(text_a) > 20, len(text_a))

    print("\n" + "=" * 72 + "\n② the JSON lists the text's rows\n" + "=" * 72)
    raw = _call(db, {"query": Q, "terms": T, "k": 2, "semantic_k": 0, "format": "json", "request_id": "orbit-test-1"})
    out = json.loads(raw)
    names_text = re.findall(r"^\d+\. (\S+)  \[", text_a, re.M)
    related_text = re.findall(r"^  · (\S+) ← ", text_a, re.M)
    names_json = [r["name"] for r in out.get("results", [])]
    related_json = [r["name"] for r in out.get("related", [])]
    check("(control) the fixture yields an answer and a related row",
          len(names_text) >= 2 and len(related_text) >= 1, (names_text, related_text))
    check("results = the text's numbered names, same order", names_json == names_text, (names_json, names_text))
    check("related rows sit apart, same set", sorted(related_json) == sorted(related_text), (related_json, related_text))

    print("\n" + "=" * 72 + "\n③ a stable id on every row\n" + "=" * 72)
    shas = {r[0]: r[1] for r in db.execute("SELECT path, sha FROM docs")}
    rows = out.get("results", [])
    check("name · path · 40-hex sha equal to the index's",
          bool(rows) and all(re.fullmatch(r"[0-9a-f]{40}", r.get("sha") or "") and shas.get(r["path"]) == r["sha"]
                             and r["name"] and r["path"] for r in rows), [(r["name"], r.get("sha")) for r in rows][:2])
    check("stale is a bool, excerpt bounded, ranks 1..n",
          all(isinstance(r["stale"], bool) and len(r["excerpt"]) <= server.SHOWN and r["rank"] == i
              for i, r in enumerate(rows, 1)), [(r["rank"], len(r["excerpt"])) for r in rows])
    check("contract, echo and the question as asked",
          out.get("contract") == "orbit-1" and out.get("request_id") == "orbit-test-1" and out.get("k") == 2
          and out.get("query") == Q and out.get("terms") == T, {k: out.get(k) for k in ("contract", "request_id", "k")})
    check("lane is a named lane", out.get("lane") in ("words", "bridged", "fused"), out.get("lane"))
    check("project_id is null until a memory declares one", all(r.get("project_id") is None for r in rows))
    check("via names where each row came from", all(r.get("via") in ("words", "graph", "meaning") for r in rows))

    print("\n" + "=" * 72 + "\n④ null when not asked, said when not served\n" + "=" * 72)
    meta = out.get("meta", {})
    check("semantic_k 0 → meaning null · partial false · embed_cached null",
          out.get("meaning") is None and meta.get("partial") is False and meta.get("embed_cached") is None, meta)
    check("latency counted, corpus counted, excerpt limit named",
          isinstance(meta.get("latency_ms"), int) and meta.get("corpus_docs") == len(NOTES)
          and meta.get("excerpt_chars") == server.SHOWN, meta)
    asked = json.loads(_call(db, {"query": Q, "terms": T, "k": 2, "semantic_k": 2, "format": "json"}))
    m = asked.get("meaning") or {}
    served = bool(m.get("rows"))
    check("meaning asked → a meaning block with asked=2",
          m.get("asked") == 2 and isinstance(m.get("rows"), list), m)
    check("partial is true exactly when no meaning row came, and then names a reason",
          asked["meta"]["partial"] == (not served)
          and (served or bool(asked["meta"].get("fallback_reason"))), asked["meta"])

    print("\n" + "=" * 72 + "\n⑤ arguments are checked\n" + "=" * 72)
    ok128 = json.loads(_call(db, {"query": Q, "k": 2, "semantic_k": 0, "format": "json", "request_id": "r" * 128}))
    check("(control) a 128-character request_id is echoed", ok128.get("request_id") == "r" * 128)
    bad = json.loads(_call(db, {"query": Q, "k": 2, "semantic_k": 0, "format": "json", "request_id": "r" * 129}))
    check("129 characters are refused with invalid_argument",
          bad.get("ok") is False and (bad.get("error") or {}).get("code") == "invalid_argument", bad)
    badfmt = json.loads(_call(db, {"query": Q, "k": 2, "format": "xml"}))
    check("an unknown format is refused with invalid_argument",
          badfmt.get("ok") is False and (badfmt.get("error") or {}).get("code") == "invalid_argument", badfmt)
    plain = _call(db, {"query": Q, "k": 2, "semantic_k": 0, "request_id": "ignored-in-text-mode"})
    check("request_id without format=json leaves the text answer as it is", plain == _call(db, {"query": Q, "k": 2, "semantic_k": 0}))

    print("\n" + "=" * 72)
    if FAILS:
        print("❌ failed: %d — %s" % (len(FAILS), " · ".join(FAILS)))
        return 1
    print("✅ all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
