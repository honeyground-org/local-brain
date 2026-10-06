#!/usr/bin/env python3
"""★A corpus's kinds are its own★ — which prefixes mark them, which get colors. Not lists we ship. (local · budget 0)

## What this replaced (2026-10-06)

`store._KIND_PREFIX` was the author's four (`feedback_`·`lesson_`·`project_`·`reference_`). It decided
two things for everyone: ① `feedback_x` is also reachable as `x`, ② a declared short name is also
reachable with the target's prefix attached. So on anyone else's notes —
  · kinds called anything else (`decision_`, `note-`, Claude Code's own `user_`) got neither, and
  · a corpus that never uses those kinds still had the author's four stripped from its filenames.

Now a prefix is `K_`/`K-` where some document of kind K is itself named that way
(§store.learn_kind_prefixes), and the aliases are rebuilt from the whole corpus on every index.

## What this holds down — on a fixture corpus, never the author's

  ① the prefixes are learned from this corpus: `decision_`·`note-`·`user_`, and ★not★ `feedback_`
  ② prefix-free names reach their document — including one whose prefix is not its own kind
  ③ ⛔ nothing of the author's leaks: `feedback_tabs_not_spaces` (no document of kind feedback) gets no short name
  ④ the mirror form — a declared short name with ★the target's own★ prefix, never another kind's
  ⑤ ⛔ two documents stripping to one name get neither · an alias never shadows a real name ·
     a remainder under four characters is not a name
  ⑥ learning moves with the corpus — a new kind appears and a vanished one is forgotten, ★on an
     incremental index★ (files that did not change are not re-read)
  ⑦ control — with the old fixed list in place of the learner, ①–③ go red (so a pass means something)

## The graph's colors (same incident, `graphview.KIND_COLORS`)

The three colors went to the author's feedback/project/lesson. On anyone else's notes every node was
gray and the legend named kinds they did not have. Now: the three most common kinds in the picture,
or the ones a person names in config `graph_kinds`.

  ⑧ the legend and the node colors are this corpus's top three · a person's choice wins · ties go by
     name · a kind no catalog translates is shown as written · control: the old fixed three go red

How to run:  PYTHONPATH=. python3 tests/verify_corpus_kinds.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-kindpfx-")
_MEM = os.path.join(_TMP, "memory")
_SAVED = {k: os.environ.get(k) for k in ("BRAIN_HOME", "BRAIN_CONFIG")}
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")

from brain import graphview, search, store  # noqa: E402

FAILS: list = []


def check(name: str, ok: bool, detail: str = "", into: list = None) -> None:
    if into is not None:            # a control run collects its reds — it neither prints nor fails the suite
        if not ok:
            into.append(name)
        return
    print("%s %s%s" % ("✅" if ok else "❌", name, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def _note(name: str, kind: str = "", body: str = "", fm_name: str = "") -> None:
    front = "---\nname: %s\ndescription: fixture note %s\n" % (fm_name or name, name)
    if kind:
        front += "metadata:\n  type: %s\n" % kind
    with open(os.path.join(_MEM, name + ".md"), "w", encoding="utf-8") as fh:
        fh.write(front + "---\n\n" + (body or "A note about %s.\n" % name.replace("_", " ")))


# Someone else's notes: their own kinds, one with a hyphen, one prefix that is not the file's own kind.
CORPUS = [
    ("decision_use_postgres_for_ledger", "decision"),
    ("decision_drop_the_nightly_export", "decision", "drop_the_nightly_export"),  # states its short name itself
    ("decision_rollback_window", "project"),      # prefix ≠ own kind — still a `decision_` name
    ("decision_tz", "decision"),                  # remainder "tz" — too short to be a name
    ("decision_retry_budget", "decision"),        # ┐ both strip to `retry_budget` → neither gets it
    ("note-retry_budget", "note"),                # ┘
    ("note-weekly-sync-format", "note"),
    ("decision_api_limits", "decision"),          # ┐ strips to a name that really exists
    ("api_limits", ""),                           # ┘
    ("user_role_and_timezone", "user"),
    ("feedback_tabs_not_spaces", ""),             # the author's prefix, but this corpus has no such kind
    ("release_checklist", "", "deploy_steps"),    # ┐ a name another note states for itself wins
    ("decision_deploy_steps", "decision"),        # ┘ over a stripped one
]
LINKER = ("index_of_links", "",
          "See [[use_postgres_for_ledger]], [[decision_ledger]], [[weekly-sync-format]], "
          "[[note-sync_format]] and [[rollback_window]].\n\n"
          "[sync_format](note-weekly-sync-format.md) is what we call the meeting notes.\n")
INDEX = "# Index\n\n- [ledger](decision_use_postgres_for_ledger.md) — which database\n"


def _resolves(db, key: str):
    r = search.resolve(db, key)
    return r["name"] if r else None


def _index(db, full: bool = False) -> dict:
    return store.reindex(db, full=full, recalibrate=False, refresh_history=False)


def assertions(db, into: list = None) -> None:
    """①–⑤ — run against the real learner, and again against the old fixed list (⑦)."""
    pre = store.learn_kind_prefixes(db)
    check("① prefixes are learned from this corpus", pre == ["decision_", "note-", "user_"],
          str(pre), into)
    for key, want in (("use_postgres_for_ledger", "decision_use_postgres_for_ledger"),
                      ("weekly-sync-format", "note-weekly-sync-format"),
                      ("role_and_timezone", "user_role_and_timezone"),
                      ("rollback_window", "decision_rollback_window")):
        got = _resolves(db, key)
        check("② `%s` reaches its document" % key, got == want, str(got), into)
    got = _resolves(db, "tabs_not_spaces")
    check("③ ⛔ the author's `feedback_` is not stripped here", got is None, str(got), into)


def graph_assertions(html: str, into: list = None) -> None:
    legend = html.split('<div class="g-legend">', 1)[-1].split("<svg", 1)[0]
    check("the legend names this corpus's top three", all(
        ('<i class="k%d"></i>' % i) in legend and want in legend.split('<i class="k%d"></i>' % i, 1)[1][:80]
        for i, want in enumerate(("<span>decision</span>", "<span>note</span>", 'data-t="kind.project"'))),
        legend[:160], into)
    check("⛔ not the author's three (no `kind.feedback` · `kind.lesson`)",
          "kind.feedback" not in legend and "kind.lesson" not in legend, "", into)
    check("a `decision` node wears the first color",
          'class="nd k0" data-nd="decision_use_postgres_for_ledger|decision|' in html, "", into)


def graph_colors(db) -> None:
    print("\n★⑧ the graph colors this corpus's kinds★")
    graph_assertions(graphview.render(db))
    check("a person's choice wins (`graph_kinds`)",
          graphview.colored_kinds(["decision", "decision", "user"], {"graph_kinds": ["user"]}) == ["user"])
    check("ties go by name — the same corpus draws the same picture",
          graphview.colored_kinds(["b", "a", "c", "d"], {}) == ["a", "b", "c"])
    check("no kinds → no colors (everything is 'other', nothing invented)",
          graphview.colored_kinds(["", ""], {}) == [])
    real = graphview.colored_kinds
    try:
        graphview.colored_kinds = lambda kinds, cfg=None: ["feedback", "project", "lesson"]
        red: list = []
        graph_assertions(graphview.render(db), into=red)
        check("control: the old fixed three fail ⑧ (%d of 3 red)" % len(red), len(red) == 3, ", ".join(red))
    finally:
        graphview.colored_kinds = real


def main() -> int:
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"],
                                "exclude": ["MEMORY.md"], "max_depth": 1}]}, fh)
    for name, kind, *fm in CORPUS:
        _note(name, kind, fm_name=fm[0] if fm else "")
    _note(LINKER[0], LINKER[1], LINKER[2])
    with open(os.path.join(_MEM, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(INDEX)

    db = store.connect()
    _index(db, full=True)

    print("=" * 72 + "\nkind prefixes — learned from the corpus\n" + "=" * 72)
    assertions(db)

    print("\n★④ the mirror form — the target's own prefix only★")
    for key, want in (("ledger", "decision_use_postgres_for_ledger"),
                      ("decision_ledger", "decision_use_postgres_for_ledger"),
                      ("sync_format", "note-weekly-sync-format"),
                      ("note-sync_format", "note-weekly-sync-format")):
        got = _resolves(db, key)
        check("`%s` reaches its document" % key, got == want, str(got))
    for key in ("note-ledger", "user_ledger", "decision_sync_format"):
        got = _resolves(db, key)
        check("⛔ `%s` — another kind's prefix is not attached" % key, got is None, str(got))
    dangling = [r["dst_name"] for r in db.execute(
        "SELECT l.dst_name FROM links l JOIN docs s ON s.id=l.src_id WHERE s.name=? AND NOT EXISTS"
        " (SELECT 1 FROM name_map m WHERE m.key=l.dst_name)", (LINKER[0],))]
    check("every link in the linking note resolves", not dangling, str(dangling))

    print("\n★⑤ when in doubt, do not connect★")
    got = _resolves(db, "retry_budget")
    check("⛔ two documents stripping to `retry_budget` → neither", got is None, str(got))
    hits = sorted({r["name"] for r in db.execute(
        "SELECT d.name FROM name_map m JOIN docs d ON d.id=m.doc_id WHERE m.key='api_limits'")})
    check("⛔ an alias never shadows a real name (`api_limits`)", hits == ["api_limits"], str(hits))
    hits = sorted({r["name"] for r in db.execute(
        "SELECT d.name FROM name_map m JOIN docs d ON d.id=m.doc_id WHERE m.key='deploy_steps'")})
    check("⛔ a name a note states for itself is not taken by a stripped one (`deploy_steps`)",
          hits == ["release_checklist"], str(hits))
    got = _resolves(db, "drop_the_nightly_export")
    check("a stated short name that equals the stripped one still resolves",
          got == "decision_drop_the_nightly_export", str(got))
    got = _resolves(db, "tz")
    check("⛔ a remainder under four characters is not a name (`tz`)", got is None, str(got))
    dup = db.execute("SELECT COUNT(*) FROM (SELECT key, doc_id FROM name_map"
                     " GROUP BY key, doc_id HAVING COUNT(*) > 1)").fetchone()[0]
    check("no name→document pair appears twice", dup == 0, "%d" % dup)

    print("\n★⑥ learning moves with the corpus — incremental index★")
    _note("idea_dark_mode_toggle", "idea")
    os.remove(os.path.join(_MEM, "user_role_and_timezone.md"))
    st = _index(db)
    check("(incremental: the unchanged files were not re-read)", st.get("skipped", 0) >= len(CORPUS) - 1,
          "skipped %s" % st.get("skipped"))
    pre = store.learn_kind_prefixes(db)
    check("a new kind is learned, a vanished one forgotten", pre == ["decision_", "idea_", "note-"],
          str(pre))
    check("`dark_mode_toggle` reaches the new note",
          _resolves(db, "dark_mode_toggle") == "idea_dark_mode_toggle")
    check("`use_postgres_for_ledger` still resolves (unchanged file, rebuilt alias)",
          _resolves(db, "use_postgres_for_ledger") == "decision_use_postgres_for_ledger")
    check("the meta records what was learned",
          json.loads(store.get_meta(db, "kind_prefixes", "[]")) == pre)

    print("\n★⑦ control — the old fixed list in place of the learner★")
    real = store.learn_kind_prefixes
    _user = ("user_role_and_timezone", "user")
    _note(*_user)                               # back to the ①–③ corpus
    os.remove(os.path.join(_MEM, "idea_dark_mode_toggle.md"))
    _index(db)
    try:
        store.learn_kind_prefixes = lambda _db: ["feedback_", "lesson_", "project_", "reference_"]
        store._build_kind_aliases(db)
        red: list = []
        assertions(db, into=red)
        check("the fixed list fails ①–③ (%d of 6 red)" % len(red), len(red) >= 3, ", ".join(red))
    finally:
        store.learn_kind_prefixes = real
        store._build_kind_aliases(db)
    green: list = []
    assertions(db, into=green)
    check("and the learner passes them again", not green, ", ".join(green))

    graph_colors(db)

    db.close()
    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAILS), " · ".join(FAILS)) if FAILS
          else "✅ all passed — this corpus's kinds are its own")
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
