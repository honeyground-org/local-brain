#!/usr/bin/env python3
"""project scope — ★a scope keeps its own memories and the unscoped ones, never another project's★ (orbit-1 O4, 2026-10-29).

## Why this check exists

Two projects can name the same thing — a `deploy_rule` in each. A client working in project A must get
A's rule and the notes that declared no project, and must never be handed B's rule as if it were A's.
Without a scope, nothing may change: every memory written before this day is unscoped and stays
visible to everyone.

## What is held down

  ① the index carries the scope: the column exists (schema 5), a memory's `project_id:` is indexed as
     declared, and an old database without the column is migrated in place
  ② (control) with no scope, recall returns the rows of both projects and the unscoped note
  ③ with `scope="proj-A"`, every row is A's or unscoped; B's rows are absent; the unscoped note is still
     there and carries no project (null)
  ④ the graph lane obeys the same rule: a neighbour linked from an A memory but declared in B is not
     shown as related under A's scope, while A's own neighbour is
  ⑤ the meaning lane filters by the same rule (asked through `fused`, with no engine it stays silent
     and the words still order — never an exception)
  ⑥ the MCP recall takes `scope`, says `scope.applied`, returns only A's or unscoped rows in JSON,
     `brain_capabilities` reports `recall.scope: true` by itself, and a bad scope is refused
     (control: without a scope the JSON says applied false and B is back)

Seeds its own home; runs in a clean room.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-scope-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ.pop("BRAIN_LEXICON", None)

from brain import calibrate, search, store  # noqa: E402

# The graph bridge opens only above the confidence line, which a five-note fixture cannot calibrate;
# this check measures the scope rule, not the gate — so the line is placed between the seed and its
# neighbours' scores in ④ (shut everywhere else), never in the live home.
FLOOR = [float("inf")]
calibrate.threshold = lambda db, default=0.0, measure=True: FLOOR[0]  # noqa: E731

FAILS = []
Q = "deploy migration"
NOTES = {
    "a_deploy_rule": ("proj-A", "A deploy needs the migration first, then the harbour restart — the lantern rule. [[a_runbook]] [[b_runbook]]"),
    "b_deploy_rule": ("proj-B", "A deploy needs the migration first, then the ferry restart."),
    "shared_deploy_note": ("", "Every deploy with a migration is announced in the harbour channel."),
    "a_runbook": ("proj-A", "Harbour runbook: migration, deploy, restart."),
    "b_runbook": ("proj-B", "Ferry runbook: migration, deploy, restart."),
}


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + str(detail)[:220]) if detail else ""))
    if not ok:
        FAILS.append(label)


def _build():
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"], "max_depth": 1}]}, fh)
    for name, (pid, body) in NOTES.items():
        scope_line = ("project_id: %s\n" % pid) if pid else ""
        with open(os.path.join(_MEM, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: fixture %s\n%smetadata:\n  type: project\n---\n\n%s\n"
                     % (name, name, scope_line, body))
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    return db


def main() -> int:
    db = _build()
    print("=" * 72 + "\n① the index carries the scope\n" + "=" * 72)
    cols = {r[1] for r in db.execute("PRAGMA table_info(docs)")}
    check("docs has project_id and the schema version is 5",
          "project_id" in cols and store.get_meta(db, "schema_version") == "5", (sorted(cols)[-3:], store.get_meta(db, "schema_version")))
    got = {r[0]: r[1] for r in db.execute("SELECT name, project_id FROM docs")}
    check("each memory's declared project_id is indexed, '' when none",
          all(got.get(n) == pid for n, (pid, _) in NOTES.items()), got)
    old = sqlite3.connect(":memory:")
    old.row_factory = sqlite3.Row
    old.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY, name TEXT, path TEXT, source TEXT, kind TEXT, title TEXT, "
                "description TEXT, body TEXT, mtime REAL, size INTEGER, sha TEXT, doclen REAL, weight REAL, indexed_at TEXT)")
    old.execute("CREATE TABLE usage(path TEXT PRIMARY KEY, hits INTEGER, opens INTEGER, useful INTEGER, wrong INTEGER, stale INTEGER, last_hit TEXT)")
    store._migrate(old)
    check("an older database gains the column in place (migration)",
          "project_id" in {r[1] for r in old.execute("PRAGMA table_info(docs)")})

    print("\n" + "=" * 72 + "\n② (control) no scope → everything\n" + "=" * 72)
    rows = search.recall(db, Q, k=8, log=False, bridge=False)
    names = [r["name"] for r in rows if not r.get("related")]
    check("both projects' rules and the unscoped note are returned",
          {"a_deploy_rule", "b_deploy_rule", "shared_deploy_note"} <= set(names), names)
    check("rows name their project, null when unscoped",
          {r["name"]: r.get("project_id") for r in rows}.get("shared_deploy_note", "x") is None
          and {r["name"]: r.get("project_id") for r in rows}.get("b_deploy_rule") == "proj-B", {r["name"]: r.get("project_id") for r in rows})

    print("\n" + "=" * 72 + "\n③ scope = proj-A\n" + "=" * 72)
    rows_a = search.recall(db, Q, k=8, log=False, bridge=False, scope="proj-A")
    direct_a = [r for r in rows_a if not r.get("related")]
    names_a = [r["name"] for r in direct_a]
    check("B's rows are absent", not any(n.startswith("b_") for n in names_a), names_a)
    check("A's rule and the unscoped note are present", {"a_deploy_rule", "shared_deploy_note"} <= set(names_a), names_a)
    check("every row is A's or unscoped (null)", all(r.get("project_id") in ("proj-A", None) for r in direct_a),
          [(r["name"], r.get("project_id")) for r in direct_a])

    print("\n" + "=" * 72 + "\n④ the graph lane obeys the scope\n" + "=" * 72)
    QG = "deploy migration harbour restart lantern"       # `lantern` makes a_deploy_rule (the linking note) the seed
    probe = search.recall(db, QG, k=8, log=False, bridge=False)   # the line is shut: raw scores, no bridge
    scores = sorted({r["name"]: r["score"] for r in probe if not r.get("related")}.values(), reverse=True)
    FLOOR[0] = (scores[0] + scores[1]) / 2 if len(scores) > 1 else scores[0] - 1e-6
    rows_g0 = search.recall(db, QG, k=1, log=False, bridge=False)
    related0 = [r["name"] for r in rows_g0 if r.get("related")]
    check("(control) without a scope the B neighbour appears as related", "b_runbook" in related0,
          ("seed", [r["name"] for r in rows_g0 if not r.get("related")], "related", related0))
    rows_g = search.recall(db, QG, k=1, log=False, bridge=False, scope="proj-A")
    related = [r["name"] for r in rows_g if r.get("related")]
    check("under A's scope the B neighbour never appears as related, A's own still can",
          "b_runbook" not in related and "a_runbook" in related, related)
    FLOOR[0] = float("inf")

    print("\n" + "=" * 72 + "\n⑤ the meaning lane takes the scope without an engine\n" + "=" * 72)
    try:
        fused, why = search.fused(db, Q, rows_a, 8, scope="proj-A")
        ok = all(not n.startswith("b_") for n in [r["name"] for r in fused])
        check("fused orders the words under the scope and names why meaning gave nothing",
              ok and isinstance(why, str), (len(fused), why[:60]))
    except Exception as exc:                             # noqa: BLE001
        check("fused never raises without an engine", False, repr(exc)[:120])

    print("\n" + "=" * 72 + "\n⑥ the MCP recall takes the scope and says so\n" + "=" * 72)
    from brain import server
    out = json.loads(server.call_tool(db, "recall", {"query": Q, "k": 8, "semantic_k": 0, "format": "json", "scope": "proj-A"}, _measure=True))
    check("scope.applied is true and the project_id is echoed",
          out.get("scope") == {"project_id": "proj-A", "applied": True}, out.get("scope"))
    check("every JSON row is A's or unscoped (null), B absent",
          all(r.get("project_id") in ("proj-A", None) for r in out.get("results", []))
          and not any(r["name"].startswith("b_") for r in out.get("results", [])), [(r["name"], r.get("project_id")) for r in out.get("results", [])])
    plain = json.loads(server.call_tool(db, "recall", {"query": Q, "k": 8, "semantic_k": 0, "format": "json"}, _measure=True))
    check("(control) without a scope the JSON says applied false and B is back",
          plain.get("scope", {}).get("applied") is False and any(r["name"].startswith("b_") for r in plain.get("results", [])), plain.get("scope"))
    caps = json.loads(server.call_tool(db, "brain_capabilities", {}))
    check("brain_capabilities now reports recall.scope true", caps["tools"]["recall"]["scope"] is True, caps["tools"]["recall"])
    bad = json.loads(server.call_tool(db, "recall", {"query": Q, "k": 2, "format": "json", "scope": "s" * 129}, _measure=True))
    check("a 129-character scope is refused", bad.get("ok") is False and (bad.get("error") or {}).get("code") == "invalid_argument", bad)

    print("\n" + "=" * 72)
    if FAILS:
        print("❌ failed: %d — %s" % (len(FAILS), " · ".join(FAILS)))
        return 1
    print("✅ all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
