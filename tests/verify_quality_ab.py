#!/usr/bin/env python3
"""★A change that lowers recall is caught★ — the base code and the head code, the same frozen corpus, the
same questions. (local · budget 0 · needs git history)

## Why (2026-10-10)

The quality checks measure the author's own notes, so they skip everywhere else — CI included — and a pull
request that made recall worse would pass every check. A labelled public set would fix that only by
breaking this repository's rule that ★an evaluation query is never invented★ (§brain/proxy.py: an invented
set measures its author's imagination). So this check invents nothing:

  corpus      this repository's own writing ★as of the base commit★ — README, the documents, and every
              module's docstring — real text, public, and the same for both sides
  questions   the inverse-cloze proxy sample (§brain/proxy.py) and its controls, built once from that
              corpus by the ★base★ code and handed to both sides unchanged
  compared    each side indexes the corpus, calibrates its own threshold and answers the same questions:
              hit@1 · hit@3 · MRR (no threshold involved), and the hook's hits · false firings ·
              automatic at that side's own threshold (§calibrate._bench — the project's own ruler)
  verdict     a fall in hit@3, MRR or automatic, or a rise in false firings, ★fails★ — unless the pull
              request says why in a line of its description: `Quality: <the reason>`

Because the corpus and the questions are identical, a difference is the code's alone. No AI engine is
involved (lexical retrieval only), so it costs nothing and gives the same answer every run.

## ⛔ It catches a fall — a rise here is not evidence of an improvement

An inverse-cloze question is a fragment of the very document it should find, so it rewards exact lookup
(§proxy: "a fragment of a document finds its own document ~90% of the time"). Measured 2026-10-10 against
deliberately broken copies:

  length normalisation B 0.6 → 1.0      MRR 0.804 → 0.781 · automatic 0.433 → 0.400     ❌ caught
  the hook attaching at 30% of its bar  false firings 0 → 22 · automatic 0.433 → 0.266  ❌ caught
  BM25 saturation K1 1.2 → 0.1          MRR 0.804 → 0.882                               "better" here
  no boost for a document's own name    MRR 0.804 → 0.874                               "better" here

The last two values were chosen on real labelled questions, where they help; this corpus cannot see that.
So a change that only raises these numbers has shown nothing — an improvement still needs the labelled
measurement (tests/verify_threshold.py, tests/verify_short.py) on someone's own notes.

`--report` (CI after a merge, and in the merge queue, where there is no description to read) prints the
comparison and never fails.

How to run:  PYTHONPATH=. python3 tests/verify_quality_ab.py                 # base = origin/main
             PYTHONPATH=. python3 tests/verify_quality_ab.py --base <ref>
Without git history (the clean room) it skips (exit 77).
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import _needs  # noqa: E402

FAILS: list = []
DOC_FILES = re.compile(r"^(?:[A-Z][A-Z_]*\.md|docs/[^/]+\.md)$")
CODE_FILES = re.compile(r"^(?:brain/(?:backends/)?[a-z_][a-z0-9_]*\.py|tests/verify_[a-z0-9_]+\.py)$")
MIN_DOCSTRING = 300                     # shorter ones are a line, not a document
ACCEPT = re.compile(r"^\s*Quality:\s*(\S.{9,})$", re.M | re.I)


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def base_commit(argv) -> str:
    want = argv[argv.index("--base") + 1] if "--base" in argv and argv.index("--base") + 1 < len(argv) else \
        (os.environ.get("BRAIN_QUALITY_BASE") or "origin/main")
    r = git("merge-base", want, "HEAD")
    if r.returncode != 0 or not r.stdout.strip():
        _needs.skip("no git history to compare with (%s)" % want,
                    "run it in a clone with history: `git fetch origin main`, then this again")
    return r.stdout.strip()


# ── the frozen corpus ───────────────────────────────────────────────────────
def corpus(base: str, out: str) -> int:
    """The base commit's writing as notes: documents as they are, each module's docstring as one note."""
    os.makedirs(out)
    n = 0
    for path in git("ls-tree", "-r", "--name-only", base).stdout.split():
        if not (DOC_FILES.match(path) or CODE_FILES.match(path)):
            continue
        text = git("show", "%s:%s" % (base, path)).stdout
        name = re.sub(r"[^a-z0-9]+", "_", path.lower().rsplit(".", 1)[0]).strip("_")
        if path.endswith(".py"):
            try:
                body = ast.get_docstring(ast.parse(text)) or ""
            except SyntaxError:
                continue
            if len(body) < MIN_DOCSTRING:
                continue
        else:
            body = text
        first = next((l.strip("# ").strip() for l in body.splitlines() if l.strip()), name)[:120]
        with open(os.path.join(out, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: %s\nmetadata:\n  type: reference\n---\n\n%s\n"
                     % (name, json.dumps(first), body))
        n += 1
    return n


# ── one side ────────────────────────────────────────────────────────────────
DRIVER = r'''
import json, sys
mode, out = sys.argv[1], sys.argv[2]
from brain import calibrate, evalinit as ei, hook, proxy, search, store
db = store.connect()
store.reindex(db, full=True, recalibrate=False, refresh_history=False)
if mode == "questions":
    json.dump({"pairs": proxy.build(db), "controls": proxy.controls(db)}, open(out, "w", encoding="utf-8"))
    sys.exit(0)
qs = json.load(open(sys.argv[3], encoding="utf-8"))
pairs, ctrl = [tuple(p) for p in qs["pairs"]], qs["controls"]
t = calibrate.threshold(db)
h1 = h3 = 0
rr = 0.0
for q, g in pairs:
    names = [r["name"] for r in search.recall(db, q, k=10, log=False)]
    rank = next((i + 1 for i, n in enumerate(names) if ei.gold_hit(g, n)), 0)
    h1 += rank == 1
    h3 += 0 < rank <= 3
    rr += 1.0 / rank if rank else 0.0
b = calibrate._bench(db, [t], pairs, ctrl, min_a=1, min_c=1).get(t, {})
n = float(len(pairs))
json.dump({"docs": store.corpus_stats(db)["docs"], "threshold": t, "n_a": len(pairs), "n_c": len(ctrl),
           "hit@1": h1 / n, "hit@3": h3 / n, "mrr": rr / n, "hook_hits": b.get("hit"), "false_fires": b.get("ff"),
           "automatic": b.get("auto")}, open(out, "w", encoding="utf-8"))
'''


def side(code: str, corpus_dir: str, work: str, mode: str, questions: str = "") -> dict:
    home = os.path.join(work, "home")
    cfg = os.path.join(work, "config.json")
    os.makedirs(home, exist_ok=True)
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": corpus_dir, "include": ["*.md"], "max_depth": 1}]}, fh)
    out = os.path.join(work, mode + ".json")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BRAIN_")}
    env.update(PYTHONPATH=code, BRAIN_HOME=home, BRAIN_CONFIG=cfg, HOME=work, USERPROFILE=work, BRAIN_LANG="en",
               BRAIN_ADAPTIVE_LEARN="0", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", DRIVER, mode, out] + ([questions] if questions else []), cwd=work,
                       env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("%s side failed: %s" % (os.path.basename(work), (r.stderr or r.stdout).strip()[-400:]))
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


def accepted() -> str:
    """The pull request's `Quality: <reason>` line, when it has one (read from the event GitHub hands CI)."""
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
            body = ((json.load(fh).get("pull_request") or {}).get("body")) or ""
    except (KeyError, OSError, ValueError):
        return ""
    m = ACCEPT.search(body)
    return m.group(1).strip() if m else ""


def main(argv) -> int:
    base = base_commit(argv)
    head = git("rev-parse", "HEAD").stdout.strip()
    tmp = tempfile.mkdtemp(prefix="brain-quality-")
    wt = os.path.join(tmp, "base-code")
    try:
        if git("worktree", "add", "--detach", wt, base).returncode != 0:
            _needs.skip("could not check the base out (%s)" % base[:12])
        docs = corpus(base, os.path.join(tmp, "corpus"))
        print("base %s · head %s%s · corpus: %d documents from the base commit"
              % (base[:12], head[:12], " (uncommitted changes included)" if git("status", "--porcelain").stdout.strip()
                 else "", docs))
        qs = os.path.join(tmp, "questions.json")
        q = side(wt, os.path.join(tmp, "corpus"), os.path.join(tmp, "b-questions"), "questions")
        with open(qs, "w", encoding="utf-8") as fh:
            json.dump(q, fh)
        check("the base code builds enough questions from the corpus", len(q["pairs"]) >= 30 and len(q["controls"]) >= 20,
              "%d questions · %d controls" % (len(q["pairs"]), len(q["controls"])))
        b = side(wt, os.path.join(tmp, "corpus"), os.path.join(tmp, "base"), "measure", qs)
        h = side(ROOT, os.path.join(tmp, "corpus"), os.path.join(tmp, "head"), "measure", qs)
    finally:
        git("worktree", "remove", "--force", wt)
        shutil.rmtree(tmp, ignore_errors=True)

    rows = [("hit@1", "higher"), ("hit@3", "higher"), ("mrr", "higher"), ("automatic", "higher"),
            ("false_fires", "lower"), ("threshold", "")]
    print("\n%-12s %10s %10s" % ("", "base", "head"))
    for k, _ in rows:
        print("%-12s %10s %10s" % (k, _fmt(b.get(k)), _fmt(h.get(k))))
    worse = [k for k, way in rows if way and b.get(k) is not None and h.get(k) is not None
             and ((way == "higher" and h[k] < b[k] - 1e-9) or (way == "lower" and h[k] > b[k] + 1e-9))
             and k != "hit@1"]
    reason = accepted()
    print()
    if worse and reason:
        print("⚠️ worse on %s — accepted by the pull request: %s" % (", ".join(worse), reason))
    elif worse and "--report" in argv:
        print("⚠️ worse on %s — reported, not judged (--report)" % ", ".join(worse))
    else:
        check("recall is not worse than the base (hit@3 · MRR · automatic · false firings)", not worse,
              ", ".join("%s %s → %s" % (k, _fmt(b[k]), _fmt(h[k])) for k in worse) or "")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### Recall on a frozen corpus — base vs head\n\n%d documents · %d questions · %d controls\n\n"
                     "| | base | head |\n|---|---|---|\n%s\n" % (b.get("docs", 0), b.get("n_a", 0), b.get("n_c", 0),
                     "\n".join("| %s | %s | %s |" % (k, _fmt(b.get(k)), _fmt(h.get(k))) for k, _ in rows)))
    print("=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ recall on the frozen corpus is not worse than the base (%d documents, %d questions)"
          % (b.get("docs", 0), b.get("n_a", 0)))
    return 0


def _fmt(v) -> str:
    if v is None:
        return "-"
    return ("%.4f" % v) if isinstance(v, float) else str(v)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
