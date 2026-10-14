#!/usr/bin/env python3
"""★The memory the agent opens is matched against what brain had just shown★ (local · budget 0)

Why this exists (2026-10-14)
----------------------------
"Did brain find the right memory" had only one measure: a hand-labelled sample, which most people do not have.
There is a signal that needs no label and no model — the agent opens the memory it needed. Replayed over the
author's last 40 sessions: 36 indexed documents opened; brain had shown 7 of them just before (the hook 2, both
first; recall 5, at 1 · 1 · 2 · 3 · 4+) and ★29 never★. That is a real-use measure of what brain finds, and the
record of what it misses.

The `Stop` hook's transcript scan (§tail) now reads what the hook attached, what `recall` returned and which
files were opened (§hosts.Host.brain_events), and writes a `use` record. This check runs it on a transcript of
known content:

  ① an opened memory the hook had attached is recorded with its rank; one `recall` returned, with its rank
  ② an opened indexed document brain never showed is recorded as not shown — the miss
  ③ a file outside the index is ignored; opening the same memory twice in a turn counts once
  ④ `usage.opens` grows; the document's ★weight does not★ (the ranking is not moved by this record)
  ⑤ the scan is incremental — scanning again adds nothing; no name or text is in the `use` record
  ⑥ names on a line that are not indexed documents (another hook's bullets) are never counted as shown
  ⑦ `brain monitor` counts them, and compares the shown-first ratio with the person's own history

⛔ Control: the same transcript without the hook's attachment records that memory as not shown — so ① can fail.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-use-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ["BRAIN_HOST"] = "claude-code"

from brain import hosts, observe, store, tail  # noqa: E402

FAILS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def hook(lines):
    return {"type": "attachment", "attachment": {"type": "hook_additional_context", "content": ["\n".join(lines)]}}


def tool_use(tid, name, inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def tool_result(tid, text):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                                     "content": [{"type": "text", "text": text}]}]}}


def transcript(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        for x in lines:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")


def main() -> int:
    print("=" * 78)
    print("the memory the agent opens, matched against what brain had just shown")
    print("=" * 78)
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"], "max_depth": 1}]}, fh)
    for n in ("ledger_rule", "deploy_window", "harbour_note", "quiet_note"):
        with open(os.path.join(_MEM, n + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: fixture\n---\n\nA note about %s.\n" % (n, n))
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False)
    w0 = {r["name"]: r["weight"] for r in db.execute("SELECT name, weight FROM docs")}
    hosts.pin("claude-code")
    p = lambda n: os.path.join(_MEM, n + ".md")
    lines = [
        user("which rule keeps the books honest"),
        hook(["[brain] header text in any language", "· ledger_rule  [memory]", "  desc", "· harbour_note  [memory]",
              "· not_a_document  [elsewhere]"]),
        tool_use("r1", "Read", {"file_path": p("harbour_note")}),                       # hook rank 2
        tool_use("r2", "Read", {"file_path": p("harbour_note")}),                       # again, same turn
        tool_use("c1", "mcp__brain__recall", {"query": "deploys"}),
        tool_result("c1", "1. deploy_window  [memory/-]\n   desc\n\n2. ledger_rule  [memory/-]\n   desc"),
        tool_use("r3", "Read", {"file_path": p("deploy_window")}),                      # recall rank 1
        tool_use("r4", "Read", {"file_path": p("quiet_note")}),                         # never shown
        tool_use("r5", "Read", {"file_path": os.path.join(_TMP, "config.json")}),       # not indexed
    ]
    t = os.path.join(_TMP, "session.jsonl")
    transcript(t, lines)
    rec = {"session_id": "s1", "offset": 0, "tail": tail._blank_tail(), "saves": 0}
    tail.scan(t, rec)
    rows, _ = observe.read("use", [observe.day()])
    got = [(r["via"], r["rank"], r["shown"]) for r in rows]
    check("① the hook's memory, opened: via hook, rank 2 of 2", ("hook", 2, 2) in got, "%s" % got)
    check("① recall's first result, opened: via recall, rank 1 of 2", ("recall", 1, 2) in got)
    check("② an indexed document brain never showed: recorded as not shown", (None, None, None) in got)
    check("③ a file outside the index is ignored, and a second open in the same turn counts once",
          len(rows) == 3, "%d rows" % len(rows))
    opens = {r["path"]: r["opens"] for r in db.execute("SELECT path, opens FROM usage")}
    w1 = {r["name"]: r["weight"] for r in db.execute("SELECT name, weight FROM docs")}
    check("④ usage.opens grows for what was opened", opens.get(p("harbour_note")) == 1 and opens.get(p("quiet_note")) == 1,
          json.dumps({os.path.basename(k): v for k, v in opens.items()}))
    check("④ ★the documents' weight does not move★", w0 == w1)
    tail.scan(t, rec)
    rows2, _ = observe.read("use", [observe.day()])
    raw = open(observe.path("use"), encoding="utf-8").read()
    check("⑤ scanning again adds nothing", len(rows2) == 3)
    check("⑤ no name or text in the use record", not any(n in raw for n in ("ledger_rule", "harbour_note", "quiet_note", "books")))
    check("⑥ a bullet naming no indexed document is never counted as shown",
          all(r["shown"] in (None, 2) for r in rows2))
    from brain import monitor
    sm = monitor.summarise(monitor.rows([observe.day()]))
    check("⑦ `brain monitor` counts them: opened 3 · shown first 2 (hook 1 · recall 1) · first 1 · top 3 2",
          sm["use"] == {"opened": 3, "shown": 2, "hook": 1, "recall": 1, "first": 1, "top3": 2}, "%s" % sm["use"])
    check("⑦ and the ratio is compared with the person's own history",
          monitor.metrics(sm)["shown_before_open"] == 0.667)
    from brain import i18n
    check("⑦ the screen says it", i18n.t("monitor.use", **sm["use"]) in monitor.render(sm, {}, "test"))

    # ── control: no attachment → that memory was not shown ───────────────────
    os.remove(observe.path("use"))
    t2 = os.path.join(_TMP, "session2.jsonl")
    transcript(t2, [lines[0]] + lines[2:3])
    rec2 = {"session_id": "s2", "offset": 0, "tail": tail._blank_tail(), "saves": 0}
    tail.scan(t2, rec2)
    rows3, _ = observe.read("use", [observe.day()])
    check("★control★: without the hook's attachment the same open is recorded as not shown",
          [(r["via"], r["rank"]) for r in rows3] == [(None, None)], "%s" % rows3)
    db.close()

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — what the agent opens is matched against what brain showed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
