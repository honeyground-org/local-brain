#!/usr/bin/env python3
"""★index-trim touches only what the audit calls safe★ — and never a fact that lives only in the index.
(local · budget 0)

## Why this exists (2026-11-20)

The host asks every session to add a pointer line to the auto-loaded index, so it grows one line per
memory. The size warning only says so, and in a file several sessions write at once nobody acts on it
(measured: 21,701 characters, every session declined). `brain index-trim` acts on the audit's verdict.

## What this holds down — the trim's own decisions, on a fixture with ★chosen verdicts★

The audit's verdicts depend on recall over the corpus; they are verified elsewhere
(verify_index_roles). Here the verdict is ★given★, so every check is about what the trim does with it.

  ① the pointer form is shortened to its first clause, capped, never inside a code span;
     any other form is not shortened
  ② under the target nothing happens
  ③ only `safe` rows are touched — directives, if-asked, at-risk stay byte-identical; a `safe` row in a
     directives or signals section stays too
  ④ a line holding a fact absent from its source stays — ★paired control★: the same line is trimmed
     once the fact is written into the source
  ⑤ a recently changed source stays — ★paired control★: trimmed once the source is old
  ⑥ dropping needs the kind's stale limit — a `project` past 21 days drops, a `reference` of the same
     age is only shortened
  ⑦ the write is refused when another writer changed the file after the plan; otherwise it writes,
     keeps a backup and one ledger line per change, and leaves every other line identical
  ⑧ config bounds — out of range falls back; `auto` is off unless the person turns it on

How to run:  PYTHONPATH=. python3 tests/verify_index_trim.py
Controls (run by hand on a copy, 2026-11-20 — each turned the named check red): the verdict filter
removed (③) · the role filter removed (③) · the fact check removed (④) · the age check removed (⑤) ·
the stale limit ignored (⑥) · both hash checks removed (⑦).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-itrim-")
_MEM = os.path.join(_TMP, "memory")
_SAVED = {k: os.environ.get(k) for k in ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_HOST", "BRAIN_LANG")}
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ["BRAIN_LANG"] = "en"

from brain import hosts, indexaudit as ia, indextrim as it, store  # noqa: E402

FAILS: list = []
DAY = 86400.0
NOW = time.time()


def check(name: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", name, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


FILLER = ("the long tail of a status line that sessions keep adding to the pointer, about builds and "
          "reviews and screenshots and whatever else happened that day in the project")


def _note(name: str, kind: str, body: str, age_days: float) -> None:
    p = os.path.join(_MEM, name + ".md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: %s\nmetadata:\n  type: %s\n---\n\n%s\n"
                 % (name, body[:60], kind, body))
    os.utime(p, (NOW - age_days * DAY, NOW - age_days * DAY))


def _cfg(extra: dict) -> None:
    d = {"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"],
                      "exclude": ["MEMORY.md"], "max_depth": 1}]}
    d.update(extra)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump(d, fh)


# name, kind, age (days), verdict given to its index line, section marker
ROWS = [
    ("rule_ask_first", "feedback", 90, "directive-kept", "directives"),
    ("proj_old_a", "project", 60, "safe", ""),
    ("proj_old_b", "project", 50, "safe", ""),
    ("proj_old_c", "project", 40, "safe", ""),
    ("ref_old", "reference", 60, "safe", ""),
    ("proj_recent", "project", 1, "safe", ""),
    ("proj_fact", "project", 60, "safe", ""),
    ("proj_ifasked", "project", 60, "if-asked", ""),
    ("proj_atrisk", "project", 60, "at-risk", ""),
    ("proj_in_directives", "project", 60, "safe", "directives"),
    ("proj_in_signals", "project", 60, "safe", "signals"),
]


def _line(name: str) -> str:
    extra = " ads#4821 only here." if name == "proj_fact" else "."
    return "- [%s](%s.md) — First clause of %s%s Then %s" % (name, name, name, extra, FILLER)


def _index() -> str:
    out = ["# Memory index", ""]
    for section, marker in (("Standing instructions", "directives"), ("Signals", "signals"),
                            ("Notes", "")):
        out.append("## %s%s" % (section, " <!-- brain: %s -->" % marker if marker else ""))
        out.append("")
        for name, _k, _a, _v, sec in ROWS:
            if sec == marker:
                out.append(_line(name))
        out.append("")
    # padding so the file is over the smallest target (0.2 × the host's warning size)
    for i in range(30):
        out.append("- plain bullet %d with no link — %s" % (i, FILLER))
    return "\n".join(out) + "\n"


def _audit(index: str) -> dict:
    """The audit's rows with ★chosen★ verdicts — the trim is what is under test."""
    given = {name: v for name, _k, _a, v, _s in ROWS}
    rows = []
    for item in ia.parse(index):
        g = item.golds[0] if item.golds else ""
        rows.append({"item": item, "detail": [], "verdict": given.get(g, "no-link")})
    return {"rows": rows, "threshold": 0.0, "n_docs": 0}


def _by_name(plan: dict) -> dict:
    return {a["names"][0]: a for a in plan["actions"]}


def main() -> int:
    os.makedirs(_MEM)
    for name, kind, age, _v, _s in ROWS:
        _note(name, kind, "Body of %s. First clause of %s. Then more detail." % (name, name), age)
    index = os.path.join(_MEM, "MEMORY.md")
    with open(index, "w", encoding="utf-8") as fh:
        fh.write(_index())
    _cfg({"index_trim": {"target_ratio": 0.2}})
    hosts.pin("claude-code")
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    # the reindex can touch nothing in the memory folder, but re-stamp the ages to be sure
    for name, _k, age, _v, _s in ROWS:
        p = os.path.join(_MEM, name + ".md")
        os.utime(p, (NOW - age * DAY, NOW - age * DAY))

    print("=" * 72 + "\n★① the pointer form, shortened★\n" + "=" * 72)
    check("first clause, at a sentence end", it.first_clause("Done. Then more.", 80) == "Done.")
    check("first clause, at ` · `", it.first_clause("A thing · and another", 80) == "A thing")
    check("capped with an ellipsis", it.first_clause("x" * 120, 20) == "x" * 19 + "…")
    check("never ends inside a code span", "`" not in it.first_clause("see `brain index-trim --apply now`", 12),
          it.first_clause("see `brain index-trim --apply now`", 12))
    check("the pointer keeps its link", it.shorten(_line("proj_old_a"), 80).startswith(
        "- [proj_old_a](proj_old_a.md) — First clause"), it.shorten(_line("proj_old_a"), 80))
    check("another form is not shortened", it.shorten("- ⛔ **Never** do this — [x](x.md)", 10) == "")

    print("\n★② under the target nothing happens★")
    _cfg({"index_trim": {"target_ratio": 1.0}})
    p = it.plan(index, now=NOW, audit=_audit(index))
    check("a file under the target gets no actions", p["reason"] == "under-target" and not p["actions"],
          "%s · %d chars · target %d" % (p["reason"], p["chars"], p["target"]))

    print("\n★③ only safe rows, never a directives or signals section★")
    _cfg({"index_trim": {"target_ratio": 0.2}})
    p = it.plan(index, now=NOW, audit=_audit(index))
    acted = _by_name(p)
    check("the plan acts", bool(acted), "%d chars → %d, target %d" % (p["chars"], p["after"], p["target"]))
    for name in ("rule_ask_first", "proj_ifasked", "proj_atrisk"):
        check("not touched: %s" % name, name not in acted)
    for name in ("proj_in_directives", "proj_in_signals"):
        check("a safe row in a %s section is not touched" % name.split("_")[-1], name not in acted)
    check("old safe project rows are acted on", {"proj_old_a", "proj_old_b", "proj_old_c"} <= set(acted),
          str(sorted(acted)))

    print("\n★④ a fact that lives only in the index stays — paired control★")
    check("a line holding `ads#4821` (absent from its source) is not touched", "proj_fact" not in acted)
    check("…counted as fact-only-here", p["skipped"].get("fact-only-here") == 1, str(p["skipped"]))
    _note("proj_fact", "project", "Body of proj_fact. First clause of proj_fact. ads#4821 is recorded.", 60)
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    os.utime(os.path.join(_MEM, "proj_fact.md"), (NOW - 60 * DAY, NOW - 60 * DAY))
    p2 = it.plan(index, now=NOW, audit=_audit(index))
    check("control: the same line is trimmed once the fact is in its source", "proj_fact" in _by_name(p2),
          str(sorted(_by_name(p2))))

    print("\n★⑤ current work stays whole — paired control★")
    check("a source changed a day ago is not touched", "proj_recent" not in acted)
    os.utime(os.path.join(_MEM, "proj_recent.md"), (NOW - 30 * DAY, NOW - 30 * DAY))
    p3 = it.plan(index, now=NOW, audit=_audit(index))
    check("control: trimmed once the source is old", "proj_recent" in _by_name(p3))

    print("\n★⑥ dropping needs the kind's stale limit★")
    a3 = _by_name(p3)
    check("a project past 21 days can be dropped", a3.get("proj_old_a", {}).get("action") == "drop",
          str(a3.get("proj_old_a", {}).get("action")))
    check("a reference of the same age is only shortened (its limit is 180 days)",
          a3.get("ref_old", {}).get("action") == "shorten", str(a3.get("ref_old", {}).get("action")))
    check("drops go oldest first", [a["names"][0] for a in sorted(
        [a for a in p3["actions"] if a["action"] == "drop"], key=lambda a: -a["age_days"])][:1] == ["proj_old_a"])

    print("\n★⑦ the write — refused on a change, otherwise exact★")
    it_dir = os.path.join(_TMP, "trim-out")
    it.trim_dir = lambda: it_dir                      # noqa: E731
    before = open(index, encoding="utf-8").read()
    with open(index, "a", encoding="utf-8") as fh:
        fh.write("- [late](late.md) — another session wrote this\n")
    r = it.apply(p3)
    check("refused after another writer changed the file", not r["applied"] and r["reason"] == "changed")
    with open(index, "w", encoding="utf-8") as fh:
        fh.write(before)
    p4 = it.plan(index, now=NOW, audit=_audit(index))
    r = it.apply(p4)
    after = open(index, encoding="utf-8").read()
    check("written", r["applied"] and len(after) < len(before), "%d → %d" % (len(before), len(after)))
    touched = {x for a in p4["actions"] for x in a["old"]}
    kept_before = [x for x in before.split("\n") if x not in touched]
    check("every other line is identical and in order", kept_before == [
        x for x in after.split("\n") if x not in {y for a in p4["actions"] for y in a["new"]}])
    check("a backup of the previous file", r["backup"] and open(r["backup"], encoding="utf-8").read() == before)
    led = open(os.path.join(it_dir, "ledger.jsonl"), encoding="utf-8").read().strip().split("\n")
    check("one ledger line per change", len(led) == len(p4["actions"]), "%d / %d" % (len(led), len(p4["actions"])))

    print("\n★⑧ config★")
    st = it.settings({"index_trim": {"target_ratio": 5, "hook_chars": 3, "keep_recent_days": -1}})
    check("out of range falls back to the defaults", st == dict(it.DEFAULTS, auto=False), str(st))
    check("`auto` is off unless turned on", it.settings({})["auto"] is False)
    _cfg({"index_trim": {"target_ratio": 0.2}})
    check("the daily step does nothing when off", it.auto(index) == {"skipped": "off"})

    print("\n" + ("✅ all passed" if not FAILS else "❌ %d failed: %s" % (len(FAILS), ", ".join(FAILS))))
    return 1 if FAILS else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for k, v in _SAVED.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
