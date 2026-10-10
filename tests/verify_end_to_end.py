"""Real-usage verification — measures ★"does it actually help storing and finding things"★ against a comparison.

A synthetic metric (hit rate) alone can't say whether it actually helped. So two things are checked:

  A. store round-trip — is what `remember` left behind found **immediately**, linked into the graph,
     and readable by a human as a file. Break any one link and "it was stored" becomes a lie.

  B. search comparison — measured side by side with ★what was actually usable before the brain existed★:
       ① was that memory listed in the old MEMORY.md index (if not, there was no way to call it)
       ② does searching the question's words with `grep` surface it (what you'd always do at a terminal)
       ③ the brain's `recall`
     what ①② can't do and ③ can is exactly **the size of the help**.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import search, server, store  # noqa: E402
from tests.verify_recall import CASES, rank_of  # noqa: E402

OLD_INDEX = "/tmp/MEMORY.md.before"


def _memory_dir() -> str:
    """★reads from config — never bakes the path into the test★.
    Bake it in and the test becomes meaningless on someone else's machine (it inspects an empty directory),
    coming back not as a failure but a **silent pass**."""
    for s in store.load_config().get("sources", []):
        if s["name"] == "memory":
            return os.path.expanduser(s["path"])
    raise RuntimeError("config.json has no memory source")


MEM_DIR = _memory_dir()

PROBE = "probe_brain_end_to_end_check"


# --------------------------------------------------------------- A. store round-trip
def verify_store(db) -> bool:
    path = os.path.join(MEM_DIR, PROBE + ".md")
    if os.path.exists(path):
        os.remove(path)
    marker = "testmarker quadruplehelix indexroundtrip"
    out = server.call_tool(db, "remember", {
        "name": PROBE,
        "description": "a temporary memory for the store round-trip check — " + marker,
        "type": "reference",
        "body": "this document is created by the store round-trip test. %s\n" % marker,
        "links": ["project_local_brain"],
    })
    checks = []

    checks.append(("the file was created", os.path.exists(path)))
    raw = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    checks.append(("the frontmatter matches the format",
                   raw.startswith("---\n") and "type: reference" in raw))
    checks.append(("the link was written into the body", "[[project_local_brain]]" in raw))
    # ⛔ ★never counts entries★ — the first version checked with `'"added": 1' in out`, but
    # if **one other file** happened to appear new in that same moment, it became `added: 2` and
    # a perfectly good save got flagged as a failure (actually happened on 2026-08-10). If a test
    # assumes the world is quiet, it raises a false failure on a noisy day.
    # What to ask is not "how many entries got indexed" but ★was this document indexed★.
    indexed = db.execute("SELECT 1 FROM docs WHERE name=?", (PROBE,)).fetchone()
    checks.append(("the index updated immediately", bool(indexed) and '"seconds"' in out))

    res = search.recall(db, "quadruplehelix indexroundtrip", extra_terms=[marker], k=5, log=False)
    checks.append(("found immediately (rank 1)", bool(res) and res[0]["name"] == PROBE))

    nb = search.neighbors(db, PROBE)
    checks.append(("linked into the graph", "project_local_brain" in nb.get("outgoing", [])))
    back = search.neighbors(db, "project_local_brain")
    checks.append(("also visible from the reverse direction", PROBE in back.get("incoming", [])))

    # cleanup — leave a trace behind and the next diagnostic counts it as an orphan
    os.remove(path)
    store.reindex(db)
    gone = db.execute("SELECT 1 FROM docs WHERE name=?", (PROBE,)).fetchone() is None
    checks.append(("deleting it also removes it from the index", gone))

    print("A. store round-trip")
    for label, ok in checks:
        print("   %s %s" % ("✅" if ok else "❌", label))
    return all(ok for _, ok in checks)


# --------------------------------------------------------------- B. search comparison
def in_old_index(needle: str) -> bool:
    """Did the old MEMORY.md point at that memory = could it have been called back then?"""
    if not os.path.exists(OLD_INDEX):
        return False
    text = open(OLD_INDEX, encoding="utf-8").read()
    return needle in text


def _grep_files(word: str):
    """Does in Python what `grep -rl <word>` does — **case-insensitive substring match**.

    ⛔ Why an external `grep` is never called (2026-08-10): in this environment, a grep child
    process launched by Python can't read the memory directory (even an ASCII pattern gets rc=1,
    0 hits, while the same command in the shell gets 7). **A comparison built on a broken measurement lies** —
    so the same behaviour is implemented directly and we own the result ourselves.
    """
    low = word.lower()
    out = []
    for fn in sorted(os.listdir(MEM_DIR)):
        if not fn.endswith(".md"):
            continue
        try:
            with open(os.path.join(MEM_DIR, fn), encoding="utf-8", errors="ignore") as fh:
                if low in fh.read().lower():
                    out.append(os.path.splitext(fn)[0])
        except OSError:
            continue
    return out


def grep_rank(question: str, terms, needle: str, k: int = 8) -> int:
    """★measured favourably toward grep★ — a comparison only means something against a strong opponent.

    A human never greps the whole question. They pick **the single most distinctive-looking word**.
    So this greps **every one** of the question's words plus the agent's synonyms individually,
    and picks **whichever returns the fewest results (= most discriminating)**.
    If even that finds nothing, that's a structural limit of grep.
    """
    words = [w for w in re.split(r"[^\w]+", question) if len(w) >= 2]
    words += [str(t) for t in (terms or []) if len(str(t)) >= 2]
    best_rank = 0
    for w in words:
        files = sorted(_grep_files(w))
        if not files or len(files) > 40:
            # more than 40 results flood out, and a human never reads that far (= not found)
            continue
        for i, name in enumerate(files[:k], 1):
            if needle in name:
                if best_rank == 0 or i < best_rank:
                    best_rank = i
                break
    return best_rank


def verify_search(db) -> bool:
    print("\nB. search comparison — side by side with what was usable before the brain")
    print("   %-34s %-12s %-12s %s" % ("question", "old index", "grep (best)", "brain recall"))
    print("   " + "-" * 78)
    old_ok = grep_ok = brain_ok = 0
    for q, terms, needle in CASES:
        listed = in_old_index(needle)
        g = grep_rank(q, terms, needle)
        res = search.recall(db, q, k=8, extra_terms=terms, log=False)
        b = rank_of(res, needle)
        old_ok += 1 if listed else 0
        grep_ok += 1 if 0 < g <= 3 else 0
        brain_ok += 1 if 0 < b <= 3 else 0
        print("   %-34s %-12s %-12s %s" % (
            q[:32], "listed" if listed else "❌ none",
            ("rank %d" % g) if g else "❌ not found",
            ("rank %d" % b) if b else "❌ not found"))
    n = len(CASES)
    print("   " + "-" * 78)
    print("   hit in top 3:  old index %d/%d (listed or not) · grep %d/%d · brain %d/%d"
          % (old_ok, n, grep_ok, n, brain_ok, n))
    return brain_ok >= max(grep_ok, old_ok) and brain_ok >= 8


if __name__ == "__main__":
    db = store.connect()
    a = verify_store(db)
    b = verify_search(db)
    print("\nverdict: store %s · search %s" % ("pass ✅" if a else "short ❌",
                                        "pass ✅" if b else "short ❌"))
    sys.exit(0 if (a and b) else 1)
