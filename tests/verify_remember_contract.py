#!/usr/bin/env python3
"""remember contract — ★a retry never duplicates, never overwrites what it did not read★ (orbit-1 O3, 2026-10-29).

## Why this check exists

A client that keeps an outbox (Cosmos Orbit) retries a save after a timeout. If the second attempt
created a second memory, or quietly replaced a memory a person wrote in between, the outbox would
make the brain worse with every hiccup. The contract's six rows (§remembercontract.decide) are each
exercised here against the disk, and the legacy text behaviour is held unchanged.

## What is held down

  ① legacy: without `idempotency_key`/`format`, remember still answers "saved/updated: name" and overwrites
  ② new: a keyed save is `new`, `stored`, `indexed_words`, with a 40-hex `sha` equal to the file's, the
     contract fields in the frontmatter, and `verified_at` = `observed_at`
  ③ noop: the same key and payload again changes nothing on disk and answers the same (minus timing)
  ④ sha_mismatch: same key, other body, no proof of the version read → refused, file untouched
  ⑤ updated: the same with `expected_sha` = the current sha → written, new sha, still indexed
  ⑥ name_taken: another key, or a memory written by a person (no key) → refused without proof; with
     `expected_sha` → updated (control: the refused call must have left the file byte-identical)
  ⑦ partial success: the index fails after the file is written → `stored: true, indexed_words: false`
     with the error named; the retry is a `noop` whose `indexed_words` is true
  ⑧ arguments: a bad `expected_sha`, `observed_at`, a 129-char key, an empty body → `invalid_argument`
     (control: the valid calls above passed)
  ⑨ the save path indexes the words and re-measures nothing — no threshold, no vector gate
     (control: a default index pass does re-measure)

Seeds its own home; runs in a clean room.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-remember-contract-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ.pop("BRAIN_LEXICON", None)

from brain import remembercontract as rc, server, store  # noqa: E402

FAILS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + str(detail)[:220]) if detail else ""))
    if not ok:
        FAILS.append(label)


def _build():
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"], "max_depth": 1}]}, fh)
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    return db


def _rem(db, **args):
    return server.call_tool(db, "remember", args, _measure=True)


def _j(db, **args):
    return json.loads(_rem(db, **args))


def _read(name):
    with open(os.path.join(_MEM, name + ".md"), encoding="utf-8") as fh:
        return fh.read()


def _strip(d):
    return {k: v for k, v in d.items() if k not in ("meta", "index")}


def main() -> int:
    db = _build()
    base = dict(description="harbour ledger rule", type="lesson", body="The ledger closes on friday.")

    print("=" * 72 + "\n① legacy text mode untouched\n" + "=" * 72)
    t1 = _rem(db, name="legacy_note", **base)
    t2 = _rem(db, name="legacy_note", description="harbour ledger rule", type="lesson", body="Changed by a person.")
    check("first save answers 'saved:', the second 'updated:', both name the path",
          t1.startswith("saved: legacy_note") and t2.startswith("updated: legacy_note") and "path:" in t1, (t1[:40], t2[:40]))
    check("legacy overwrites without asking", "Changed by a person." in _read("legacy_note"))

    print("\n" + "=" * 72 + "\n② new\n" + "=" * 72)
    a = _j(db, name="orbit_rule", idempotency_key="k1", project_id="proj-A", source="orbit task t1",
           observed_at="2026-10-01", format="json", **base)
    text = _read("orbit_rule")
    check("result new · stored · indexed_words", a.get("result") == "new" and a.get("stored") is True
          and a.get("indexed_words") is True, {k: a.get(k) for k in ("result", "stored", "indexed_words")})
    check("sha is the file's", a.get("sha") == rc.file_sha(os.path.join(_MEM, "orbit_rule.md")) and len(a.get("sha") or "") == 40, a.get("sha"))
    check("the frontmatter carries the contract fields",
          all(s in text for s in ("idempotency_key: k1", "payload_sha: ", "project_id: proj-A", "source: orbit task t1", "verified_at: 2026-10-01")), text[:260])
    check("verified_at is observed_at · project_id echoed · payload_sha 40 hex",
          a.get("verified_at") == "2026-10-01" and a.get("project_id") == "proj-A" and len(a.get("payload_sha") or "") == 40, a)
    check("embedded is false or unknown, never true right after saving", a.get("embedded") in (False, None), a.get("embedded"))
    check("served names the store situation", a.get("served") in ("n/a", "pending", "unknown"), a.get("served"))

    print("\n" + "=" * 72 + "\n③ noop\n" + "=" * 72)
    sha_before = a["sha"]
    b = _j(db, name="orbit_rule", idempotency_key="k1", project_id="proj-A", source="orbit task t1",
           observed_at="2026-10-01", format="json", **base)
    check("same key and payload → noop, same sha, file untouched",
          b.get("result") == "noop" and b.get("sha") == sha_before and _read("orbit_rule") == text, b.get("result"))
    check("the answer is the same as the first, apart from timing and index stats",
          _strip(dict(a, result="noop")) == _strip(b), (_strip(a), _strip(b)))

    print("\n" + "=" * 72 + "\n④ sha_mismatch · ⑤ updated\n" + "=" * 72)
    c = _j(db, name="orbit_rule", idempotency_key="k1", format="json",
           description="harbour ledger rule", type="lesson", body="The ledger closes on monday.")
    check("same key, other body, no expected_sha → conflict sha_mismatch, stored false, file untouched",
          c.get("result") == "conflict" and (c.get("conflict") or {}).get("reason") == "sha_mismatch"
          and c.get("stored") is False and _read("orbit_rule") == text and c["conflict"].get("current_sha") == sha_before, c.get("conflict"))
    d = _j(db, name="orbit_rule", idempotency_key="k1", format="json", expected_sha=sha_before,
           description="harbour ledger rule", type="lesson", body="The ledger closes on monday.")
    check("with expected_sha = current → updated, new sha, indexed",
          d.get("result") == "updated" and d.get("sha") != sha_before and d.get("indexed_words") is True
          and "monday" in _read("orbit_rule"), d.get("result"))

    print("\n" + "=" * 72 + "\n⑥ name_taken\n" + "=" * 72)
    sha_now = d["sha"]
    e = _j(db, name="orbit_rule", idempotency_key="k2", format="json", **base)
    check("another key without proof → conflict name_taken, file untouched",
          e.get("result") == "conflict" and (e.get("conflict") or {}).get("reason") == "name_taken"
          and rc.file_sha(os.path.join(_MEM, "orbit_rule.md")) == sha_now, e.get("conflict"))
    f = _j(db, name="legacy_note", idempotency_key="k3", format="json", **base)
    check("a memory written by a person (no key) is protected the same way",
          f.get("result") == "conflict" and (f.get("conflict") or {}).get("reason") == "name_taken"
          and "Changed by a person." in _read("legacy_note"), f.get("conflict"))
    g = _j(db, name="legacy_note", idempotency_key="k3", format="json", expected_sha=f["conflict"]["current_sha"], **base)
    check("with the version proved → updated, and the key is now k3",
          g.get("result") == "updated" and g.get("idempotency_key") == "k3" and "idempotency_key: k3" in _read("legacy_note"), g.get("result"))

    print("\n" + "=" * 72 + "\n⑦ partial success\n" + "=" * 72)
    real = store.reindex
    store.reindex = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("index unavailable"))  # noqa: E731
    try:
        h = _j(db, name="orbit_partial", idempotency_key="k4", format="json", **base)
    finally:
        store.reindex = real
    check("the file is stored but not indexed, and the error is named",
          h.get("result") == "new" and h.get("stored") is True and h.get("indexed_words") is False
          and "index unavailable" in str((h.get("index") or {}).get("error")), h)
    i = _j(db, name="orbit_partial", idempotency_key="k4", format="json", **base)
    check("the retry is a noop whose indexed_words is now true", i.get("result") == "noop" and i.get("indexed_words") is True, i.get("indexed_words"))

    print("\n" + "=" * 72 + "\n⑧ arguments are checked\n" + "=" * 72)
    bad = [
        ("expected_sha not 40 hex", _j(db, name="x1", idempotency_key="k9", format="json", expected_sha="abc", **base)),
        ("observed_at not a date", _j(db, name="x2", idempotency_key="k9", format="json", observed_at="yesterday", **base)),
        ("129-char key", _j(db, name="x3", idempotency_key="k" * 129, format="json", **base)),
        ("empty body", _j(db, name="x4", idempotency_key="k9", format="json", description="d", type="lesson", body="  ")),
    ]
    for label, r in bad:
        check("%s → invalid_argument" % label, r.get("ok") is False and (r.get("error") or {}).get("code") == "invalid_argument", r)
    check("(control) none of the refused calls wrote a file",
          not any(os.path.exists(os.path.join(_MEM, n + ".md")) for n in ("x1", "x2", "x3", "x4")))

    print("\n" + "=" * 72 + "\n⑨ the save path indexes the words and re-measures nothing\n" + "=" * 72)
    # A client with a 30 s timeout saw the file land but never the answer (2026-10-29): the default index pass
    # re-measured the hook threshold and the vector gate after every save (15–32 s on a 2,246-document copy).
    from brain import calibrate as _cal, vectors as _vec
    calls = {"threshold": 0, "gate": 0}
    real_cal, real_gate = _cal.calibrate, _vec.recalibrate_if_free
    _cal.calibrate = lambda db, *a, **k: (calls.__setitem__("threshold", calls["threshold"] + 1) or {"threshold": 9.0})  # noqa: E731
    _vec.recalibrate_if_free = lambda db, *a, **k: (calls.__setitem__("gate", calls["gate"] + 1) or None)  # noqa: E731
    try:
        j = _j(db, name="orbit_quick", idempotency_key="k7", format="json", **base)
        check("a contract save never re-measures the threshold or the gate",
              j.get("result") == "new" and j.get("indexed_words") is True and calls == {"threshold": 0, "gate": 0}, calls)
        store.reindex(db)
        check("(control) a default index pass does re-measure", calls["threshold"] >= 1, calls)
    finally:
        _cal.calibrate, _vec.recalibrate_if_free = real_cal, real_gate

    print("\n" + "=" * 72)
    if FAILS:
        print("❌ failed: %d — %s" % (len(FAILS), " · ".join(FAILS)))
        return 1
    print("✅ all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
