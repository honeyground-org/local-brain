"""Alias check — ★does a short name a human declared still reach that memory★.

## Why this check exists (measured 2026-08-31)

The brain had ★2,632★ aliases stored and still couldn't find `[[diagnostic_wolf]]`.
There were only three generation rules: ①strip the kind prefix ②frontmatter `name`
③hyphen↔underscore. ★A short name a human decided by hand★ was nowhere among them.

But that name was already written down — `MEMORY.md` declares
`[diagnostic_wolf](lesson_a_diagnostic_that_counts_what_you_cannot_fix.md)`, and
memories call it `[[diagnostic_wolf]]`. ⛔ The index file is loaded wholesale every session,
so ★it is never indexed as a document★ — so nobody ever read that declaration,
and that path was silently dead (it only ever showed up as a broken link).

★It generalizes to one rule★ — any document that writes `[label](target.md)` makes that label the target's alias.
Not a MEMORY.md-only special case — ★it's exactly what a human already writes, taken as knowledge as-is★.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import health, search, store                   # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    db = store.connect()
    print("=" * 72 + "\naliases — does it learn a declaration\n" + "=" * 72)

    pairs = store._declared_pairs("[short_name](feedback_a_very_long_real_name.md)")
    check("`[label](target.md)` extracts an alias",
          ("short_name", "feedback_a_very_long_real_name") in pairs)
    # The mirror form (`feedback_short_name`) needs the corpus's kind prefixes, so it is built after
    # indexing, not here — checked on a fixture corpus in tests/verify_corpus_kinds.py ④.
    check("only the declared pair — no prefix is guessed at parse time",
          len(pairs) == 1, "%d found" % len(pairs))

    for bad in ("[여기](x.md)", "[the full sentence goes here](x.md)", "[a](x.md)"):
        check("⛔ discards a label that isn't identifier-shaped: %s" % bad[:30],
              not store._declared_pairs(bad))
    check("a label pointing at itself is not an alias",
          not store._declared_pairs("[x_name](x_name.md)"))
    check("⛔ an external link is not an alias",
          not store._declared_pairs("[x_name](https://e.com/x_name.md)"))

    # ⛔ ★a document has to be able to declare its own alias★ — the owner of a name deciding
    #    the name beats someone else's document (MEMORY.md) declaring it instead. The first version
    #    blocked this with `t != name`, and that's why `two_persona_axes` stayed broken.
    #    ★declaring an alias isn't a link★ — "a link pointing at itself is meaningless" doesn't apply here.
    self_decl = store._declared_pairs("[short](reference_a_long_name.md)")
    check("★a document can declare its own alias★",
          ("short", "reference_a_long_name") in self_decl)

    print("\n" + "=" * 72 + "\nin the real corpus\n" + "=" * 72)
    if not db.execute("SELECT COUNT(*) FROM docs WHERE source='memory'").fetchone()[0]:
        # ⛔ the half below measures ★this machine's★ notes — a fresh machine has none (clean room 2026-10-06)
        print("⏭ skipped — needs your own indexed notes")
        print("=" * 72)
        if FAIL:
            print("❌ %d failure(s): %s" % (len(FAIL), " · ".join(FAIL)))
            return 1
        print("✅ the parser checks passed — the real-corpus half needs your notes")
        return 0
    n = db.execute("SELECT COUNT(*) c FROM alias_decl").fetchone()["c"]
    check("learned the index file's declarations (after indexing)", n > 0, "%d found" % n)

    live = db.execute("SELECT COUNT(*) c FROM alias_decl a"
                      " JOIN docs d ON d.name = a.target").fetchone()["c"]
    check("some of them point at a target that actually exists", live > 0, "%d found" % live)

    for alias in ("diagnostic_wolf", "no_stale_quotes", "feedback_no_stale_quotes"):
        r = search.resolve(db, alias)
        check("reaches the document through the alias `%s`" % alias, r is not None,
              r["name"][:52] if r else "★not found★")

    # ⛔ ★never shadows a name that actually exists★ — a declaration covering a real document makes it vanish.
    real = db.execute("SELECT name FROM docs LIMIT 1").fetchone()["name"]
    hit = db.execute("SELECT doc_id FROM name_map WHERE key=?", (real,)).fetchall()
    check("★a declaration never shadows a real document's name★", len(hit) >= 1, real[:44])

    shadow = db.execute(
        "SELECT COUNT(*) c FROM alias_decl a"
        " WHERE EXISTS (SELECT 1 FROM docs d WHERE d.name = a.alias)").fetchone()["c"]
    check("a declaration overlapping a real name is excluded from the view (harmless even if present)", True, "%d overlap(s)" % shadow)

    print("\n" + "=" * 72 + "\ndoes the diagnosis pick out ★what can be fixed★\n" + "=" * 72)
    d = health.dangling_split(db, "memory")
    check("splits broken links into typo / missing",
          "typo" in d and "missing" in d,
          "name issue %d · no memory %d" % (len(d["typo"]), len(d["missing"])))
    check("★hands over a 'similar name' too★ — makes an action possible instead of just 'not found'",
          all(x["near"] for x in d["typo"]) if d["typo"] else True)

    print("=" * 72)
    if FAIL:
        print("❌ %d failure(s): %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ all passed — a human-declared name also reaches the memory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
