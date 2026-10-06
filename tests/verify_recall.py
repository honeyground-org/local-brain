"""Recall-quality regression test — ★this file is the criterion for 'done'★.

Baseline (2026-08-10, memory 434 docs · SQLite FTS5 trigram):
    · natural-language question as-is, no expanded terms  → **1** of 6
    · agent expands the search terms                       → **5** of 6
      (the remaining 1 fails because a common word like `failed`(df=134)·`fixed`(df=58) buries the signal)

The corpus is now more than double that size (the run itself prints the size). It must still improve to pass.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import search, store  # noqa: E402

# ⛔ don't hard-code the eval set — gold is ★this computer's memory names★, and if someone else clones
#    and runs it, everything fails (there's no way to tell whether it's the engine or a different corpus).
#    build your own set with `brain eval-init`. tests/eval/ is gitignored.
_EVAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "recall.json")


def _load_cases():
    try:
        with open(_EVAL, encoding="utf-8") as fh:
            raw = json.load(fh)["cases"]
    except (OSError, ValueError, KeyError):
        print("no eval set: %s" % _EVAL)
        print("  → run `bin/brain eval-init` to draft one from your own conversation history, then fill in gold.")
        print("  (`tests/eval/recall.example.json` is the format example)")
        raise SystemExit(0)
    return [(c["q"], c.get("terms") or [], c["gold"]) for c in raw]


CASES = _load_cases()


def rank_of(results, needle):
    for i, r in enumerate(results, 1):
        if needle in r["name"]:
            return i
    return 0


def run(with_terms: bool, k: int = 8, quiet: bool = False):
    db = store.connect()
    hits1 = hits3 = hitsk = 0
    for q, terms, expect in CASES:
        res = search.recall(db, q, k=k, extra_terms=terms if with_terms else [],
                            log=False)
        pos = rank_of(res, expect)
        hits1 += 1 if pos == 1 else 0
        hits3 += 1 if 0 < pos <= 3 else 0
        hitsk += 1 if pos else 0
        if not quiet:
            mark = "✅" if 0 < pos <= 3 else ("△" if pos else "❌")
            print("%s %-2s %s" % (mark, pos or "-", q))
            if pos != 1:
                print("     expected: %s" % expect)
                print("     top 1~3: %s" % [r["name"][:46] for r in res[:3]])
    n = len(CASES)
    return {"top1": hits1, "top3": hits3, "topk": hitsk, "n": n}


if __name__ == "__main__":
    print("=" * 72)
    print("A. question as-is (no expanded terms)   — baseline 1/6")
    print("=" * 72)
    a = run(with_terms=False)
    print("\n" + "=" * 72)
    print("B. agent expands search terms            — baseline 5/6")
    print("=" * 72)
    b = run(with_terms=True)
    print("\n" + "=" * 72)
    # ⛔ don't hard-code the corpus size — it goes stale every time the index grows (measured 866 → 1090).
    from brain import store as _store
    try:
        _n = _store.connect().execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    except Exception:                                    # noqa: BLE001
        _n = -1
    print("result (%d item(s) · corpus %s doc(s))" % (a["n"], _n if _n >= 0 else "?"))
    print("  A no expansion : top1 %d · top3 %d · top%d %d" %
          (a["top1"], a["top3"], 8, a["topk"]))
    print("  B with expansion : top1 %d · top3 %d · top%d %d" %
          (b["top1"], b["top3"], 8, b["topk"]))
    # ★why the judgement criterion is written here★ — if "what counts as done" lives outside the code,
    # the next session looks at the numbers and reads them however it likes.
    #
    # B (with expanded search terms) is the contract: the MCP tool description requires expansion on the calling
    # side, and all real recall comes through this path. So B carries the high threshold.
    #
    # A (question as-is) is ★not a product path yet★ — it's for a spot with no human (the automatic-recall hook),
    # and local embedding is what's meant to fill that spot. For now it only carries a low floor and
    # **deliberately leaves the gap visible**: this number is the evidence for adopting embedding.
    ok_b = b["top3"] >= 8 and b["topk"] >= 9
    ok_a = a["topk"] >= 3
    print("  B(contract) top3≥8 & top8≥9 : %s" % ("pass ✅" if ok_b else "short ❌"))
    print("  A(floor) top8≥3              : %s" % ("pass ✅" if ok_a else "short ❌"))
    print("  ⚠︎ A gap %d/%d — this is the share local embedding is meant to fill (the automatic-recall hook)"
          % (a["top3"], a["n"]))
    sys.exit(0 if (ok_b and ok_a) else 1)

