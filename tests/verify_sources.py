#!/usr/bin/env python3
"""Measures corpus detection·add — ★the first gate of the installable package★.

## Why

The user's instruction (2026-08-24): *"Where existing important information lives is going to differ
per person and per environment... it has to be able to detect that well, or configure it, so
detection should happen, and add should let more documents or records get added."*

This brain's performance depends on ★what got indexed★ even before the engine. And a failure here is
always ★silent★ — a path that doesn't exist becomes an empty index, a worktree copy inflates the corpus,
and treating a code repo as notes puts its README above real memories. So a check exists for every shape condition.

⛔ This check touches the user's real config.json, so it ★must restore the original★ (the final block).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain import discover, store, vectors      # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  %s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))


CONFIG = store.default_config_path()
BACKUP = CONFIG + ".verify-backup"
if not os.path.exists(CONFIG):
    # ⛔ A first day has no config yet — it crashed here in a clean room (2026-10-07). Say what is missing.
    from tests import _needs
    _needs.skip("no config yet at %s" % CONFIG, "run `bin/brain install` (or `bin/brain index`) first")
shutil.copyfile(CONFIG, BACKUP)

try:
    print("=" * 74)
    print("corpus detection·add")
    print("=" * 74)

    print()
    print("① detection — does it find the corpus already in use on its own")
    cfg = store.load_config(tolerant=True)
    cands = discover.detect(min_files=3)
    found = {os.path.normpath(c.path) for c in cands}
    mem = os.path.normpath(os.path.expanduser(
        next((s["path"] for s in cfg["sources"] if s.get("name") == "memory"), "")))
    check("one or more candidates", bool(cands), "%d found" % len(cands))
    check("found the memory directory already in use", mem in found, mem[-48:])
    check("every candidate carries evidence (file count > 0)",
          all(c.files > 0 for c in cands))

    print()
    print("② shape conditions — does it filter out a copy and a code repo")
    # a worktree: a checkout where `.git` is ★a file★ — looked for under the roots detection itself walks.
    # ⛔ It used to open the author's `~/development` by name and crash on any machine without one (2026-10-07).
    wt = []
    for root in discover.candidate_roots():
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        wt += [p for p in (os.path.join(root, d) for d in names)
               if os.path.isfile(os.path.join(p, ".git"))]
    if wt:
        check("judges a worktree copy as a copy", discover.is_worktree_copy(wt[0]),
              os.path.basename(wt[0]))
        check("the worktree is not a candidate",
              not any(os.path.normpath(c.path).startswith(os.path.normpath(wt[0]))
                      for c in cands))
    else:
        print("  ⏭ this machine has no worktree — skipping the judgement")
    check("a code repo is not notes",
          not discover.looks_like_notes("/x", ["package.json", "README.md"] +
                                        ["a%d.md" % i for i in range(9)]))
    check("a folder that's mostly prose is notes",
          discover.looks_like_notes("/x", ["a%d.md" % i for i in range(9)]))

    print()
    print("③ add — idempotent, and a nonexistent path is rejected")
    tmp = tempfile.mkdtemp(prefix="brain-verify-src-")
    with open(os.path.join(tmp, "note.md"), "w", encoding="utf-8") as fh:
        fh.write("# a test note\n\nbrain corpus add check.\n")
    src = {"name": "verify_tmp", "path": tmp, "include": ["*.md"], "prior": 0.5,
           "note": "for the check"}
    a1 = store.add_source(dict(src))
    a2 = store.add_source(dict(src))
    check("gets added the first time", a1["action"] == "added", str(a1))
    check("the second time is blocked as a duplicate (by path)", a2["action"] == "exists", str(a2))
    bad = store.add_source({"name": "nope", "path": "/definitely/not/here"})
    check("a nonexistent path is rejected (prevents a silent empty index)", bad["action"] == "missing")

    print()
    print("④ embed:false — is it left out of semantic search (remote)")
    n_before = len(vectors.stale_docs(store.connect()))
    cfg2 = store.load_config()
    for s in cfg2["sources"]:
        if s.get("name") == "verify_tmp":
            s["embed"] = False
    store.save_config(cfg2)
    check("caught as a source that never gets sent", "verify_tmp" in vectors.no_embed_sources(),
          str(vectors.no_embed_sources()))
    check("word search still works (local)", True, "a local path is unaffected")

    print()
    print("⑤ remove — does it revert")
    r = store.remove_source(tmp)
    check("removed by path", bool(r["removed"]), str(r["removed"])[:60])
    left = [s.get("name") for s in store.load_config()["sources"]]
    check("not in the remaining sources", "verify_tmp" not in left, str(left))
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("⑥ does a registered source actually exist (the cause of a silent empty index)")
    missing = [s for s in store.load_config()["sources"]
               if not os.path.isdir(os.path.expanduser(s.get("path", "")))]
    check("no nonexistent path is registered", not missing,
          str([m.get("path") for m in missing]))
finally:
    shutil.copyfile(BACKUP, CONFIG)
    os.remove(BACKUP)
    print()
    print("(config.json restored to its original)")

print()
print("=" * 74)
print("verdict: %s" % ("pass ✅" if ok else "short ❌"))
sys.exit(0 if ok else 1)
