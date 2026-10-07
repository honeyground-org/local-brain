#!/usr/bin/env python3
"""★Who is allowed to overwrite the ruler★ — the ordering key, and the discipline that keeps it honest. (local · budget 0)

## The incident this replaces (2026-09-09)

The ordering key used to be ★the newest mtime among `brain/*.py`★. mtime says *when a file was
written to disk*, never *which generation of the code it holds* — and those two come apart the
moment a second copy exists. `git worktree add`, a re-clone and `pip install .` all stamp every
file with "now", so whichever copy was created ★last★ outranked the others forever.

Measured: one comparison worktree, run once, froze this machine's threshold. Every later
calibration was discarded with a single stderr line, and the scorecard's autonomy axis fell
66.7 → 33.3. Nothing errored. The release path guarantees the second copy (`pip install .`, §8-c),
so this was shipping.

## What replaced it, and what this check holds down

`calibrate.CODE_GENERATION` — an explicit integer, ★copy-stable★ — is the order.
`calibrate.code_fingerprint()` — a hash of `RULER_MODULES`' source with comments stripped — is the
identity, and it is what makes the discipline checkable.

  ① the ordering rule behaves (§`calibrate.older_ruler`) — including ★equal generation always
     passes★, which is the whole fix
  ② the fingerprint ignores file time (or we would have rebuilt the same trap under a new name)
  ③ ⛔ ★you cannot change measurement code without bumping the generation★ — the working tree is
     fingerprinted against ★the committed version (HEAD)★, which is exactly the granularity that
     matters: one bump per change that ships, not one per keystroke. There is no record file to
     keep in step, because a rule kept in two places gets fixed in one.

⛔ What this check does ★not★ cover: the end-to-end refusal (an older generation actually being
   turned away by `calibrate()`) is measured in `tests/verify_threshold.py`, on the real corpus.
   Two checks, one rule — deliberately, because that one runs the whole pipeline and this one must
   stay free of a corpus so it runs anywhere.

How to run:  PYTHONPATH=. python3 tests/verify_code_generation.py
             PYTHONPATH=. python3 tests/verify_code_generation.py --base origin/main    # a pull request

★In a pull request, HEAD is the pull request itself★, so "compared with HEAD" is always a match there.
`--base REF` compares with ★where the change started★ — `git merge-base REF HEAD` — so the whole pull
request must carry one bump, however many commits it has. A base that git cannot resolve is a failure,
not a skip: CI asked for the comparison, and a check that quietly does less than asked says "fine".

★There is no flag that makes §3 green.★ Either the measurement code matches HEAD, or
`CODE_GENERATION` is higher than HEAD's. A switch that silences a check is the thing this file
exists to prevent. Outside a git repository §3 reports ★skipped★ — not passed.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-codegen-")
_HOME_BEFORE = os.environ.get("BRAIN_HOME")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")

from brain import calibrate, store  # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


VERDICTS = {
    "match":      "the measurement code is unchanged since the base",
    "baseline":   "the base predates generations — this commit sets the baseline",
    "bumped":     "measurement code changed ★and the generation was bumped★",
    "not-bumped": "★measurement code changed but CODE_GENERATION did not★",
    "gen-alone":  "★the generation moved without the code moving★",
}


def _git(*args):
    """Run git in the repository root · (ok, text). Never raises — no git means ★skipped★, not passed."""
    import subprocess
    try:
        p = subprocess.run(("git",) + args, cwd=ROOT, capture_output=True, text=True, timeout=20)
    except (OSError, ValueError):
        return False, ""
    return p.returncode == 0, p.stdout


def head_state(ref: str = "HEAD"):
    """(generation, fingerprint) of the committed version `ref` · (None, None) when git cannot answer.

    ⛔ The fingerprint is built with `calibrate.fingerprint_of` — ★the same rule the live one uses★,
    never a second copy of the hashing logic.
    """
    ok, _ = _git("rev-parse", "--git-dir")
    if not ok:
        return None, None
    sources = {}
    for name in calibrate.RULER_MODULES:
        got, text = _git("show", "%s:brain/%s.py" % (ref, name))
        sources[name] = text if got else ""          # absent at HEAD = a new module, a real difference
    import re
    m = re.search(r"^CODE_GENERATION\s*=\s*(\d+)", sources.get("calibrate") or "", re.M)
    return (int(m.group(1)) if m else None), calibrate.fingerprint_of(sources)


def verdict(head_gen, head_fp, gen, fp):
    """(tag, ok, detail) — ★the whole discipline rule, as one pure function★ so it can be driven
    through every branch by a control group (§3b). Nothing here reads the disk or calls git."""
    if head_gen is None:
        return "baseline", True, "the base predates CODE_GENERATION — this commit introduces %s" % gen
    if head_fp == fp:
        if head_gen == gen:
            return "match", True, "generation %s, unchanged since the base" % gen
        return "gen-alone", False, "base gen %s · working gen %s, but no code changed" % (head_gen, gen)
    if isinstance(head_gen, int) and isinstance(gen, int) and gen > head_gen:
        return "bumped", True, "measurement code changed · gen %s → %s" % (head_gen, gen)
    return ("not-bumped", False,
            "base gen %s · working gen %s — bump `calibrate.CODE_GENERATION` in this change"
            % (head_gen, gen))


def _put(db, code_id) -> None:
    """Write a stored ruler record carrying `code_id` (None = a record from before generations)."""
    rec = {"threshold": 9.0}
    if code_id is not None:
        rec["code_id"] = code_id
    store.set_meta(db, "hook_threshold_detail", json.dumps(rec, ensure_ascii=False))


def base_ref(argv):
    """The commit to compare with · ("HEAD", label) by default · (None, why) when `--base` cannot resolve."""
    if "--base" not in argv:
        return "HEAD", "HEAD"
    i = argv.index("--base")
    if i + 1 >= len(argv):
        return None, "--base needs a ref (for example origin/main)"
    want = argv[i + 1]
    ok, out = _git("merge-base", want, "HEAD")
    if not ok or not out.strip():
        return None, "git cannot find a merge base between %s and HEAD — fetch it first (fetch-depth: 0)" % want
    return out.strip(), "merge-base(%s, HEAD) = %s" % (want, out.strip()[:12])


def main(argv=()) -> int:
    db = store.connect()
    mine = calibrate.code_identity()
    print("=" * 78)
    print("code generation %s · fingerprint %s… · mtime %s (diagnostic only)"
          % (mine["gen"], mine["fp"][:12], mine["mtime"]))
    print("=" * 78)

    # ── ① the ordering rule ──────────────────────────────────────────────────
    print("★1. who may overwrite the ruler (§calibrate.older_ruler)")
    _put(db, dict(mine))
    check("the same code may write (identical record)", calibrate.older_ruler(db, mine) == "")

    # ★the 2026-09-09 fix★ — another copy of the same generation: different fingerprint AND a file
    # time far in the future. Under the old rule this locked the original out permanently.
    _put(db, {"gen": mine["gen"], "fp": "f" * 64, "mtime": mine["mtime"] + 86400 * 365})
    check("★equal generation always passes★ — a worktree/re-clone/reinstall cannot lock the original out",
          calibrate.older_ruler(db, mine) == "",
          "stored mtime +1 year, different fingerprint")

    _put(db, {"gen": mine["gen"] + 1, "fp": "a" * 64, "mtime": 0})
    got = calibrate.older_ruler(db, mine)
    check("a newer generation is protected (older code is turned away)", got != "", got or "(allowed!)")

    _put(db, {"gen": mine["gen"] - 1, "fp": "b" * 64, "mtime": mine["mtime"] + 999})
    check("an older generation is overwritten, however new its files look",
          calibrate.older_ruler(db, mine) == "")

    _put(db, None)
    check("a record from before generations counts as 0 (un-sticks an already-locked machine)",
          calibrate.older_ruler(db, mine) == "")

    store.set_meta(db, "hook_threshold_detail", "{ this is not json")
    check("an unreadable record never blocks (a broken record must not silence the brain)",
          calibrate.older_ruler(db, mine) == "")

    store.set_meta(db, "hook_threshold_detail", json.dumps({"code_id": {"gen": "nonsense"}}))
    check("a nonsense generation never blocks", calibrate.older_ruler(db, mine) == "")

    # ── ② the fingerprint must not be a file clock in disguise ───────────────
    print("\n★2. the fingerprint is content, not file time")
    victim = os.path.join(ROOT, "brain", calibrate.RULER_MODULES[0] + ".py")
    st = os.stat(victim)
    try:
        # ⛔ Past the ★newest★ file, not past its own clock (2026-09-29). `code_stamp()` is a max, so
        #    "+3 days on this file" only moves it while this file is within 3 days of the newest one —
        #    the row went red the day a different module was edited, with nothing wrong in the code.
        os.utime(victim, (st.st_atime, max(st.st_mtime, mine["mtime"]) + 86400 * 3))
        moved_fp = calibrate.code_fingerprint()
        moved_stamp = calibrate.code_stamp()
        check("moving a source file's clock does ★not★ move the fingerprint",
              moved_fp == mine["fp"], os.path.basename(victim) + " +3 days")
        check("…while `code_stamp()` does move (the two are genuinely decoupled)",
              moved_stamp != mine["mtime"], "%s → %s" % (mine["mtime"], moved_stamp))
    finally:
        os.utime(victim, (st.st_atime, st.st_mtime))
    check("the clock is restored and the fingerprint is unchanged",
          calibrate.code_fingerprint() == mine["fp"] and calibrate.code_stamp() == mine["mtime"])

    # ── ③ the discipline — bump the generation when the ruler moves ──────────
    print("\n★3. measurement code cannot change without a generation bump")
    check("every module named in RULER_MODULES exists",
          all(os.path.exists(os.path.join(ROOT, "brain", m + ".py")) for m in calibrate.RULER_MODULES),
          " · ".join(calibrate.RULER_MODULES))

    ref, label = base_ref(list(argv))
    head_gen, head_fp = (None, None) if ref is None else head_state(ref)
    if ref is None:
        check("the base to compare with resolves", False, label)
    elif head_fp is None and head_gen is None:
        print("⏭  skipped — not a git repository (this rule is checked where changes are made)")
    else:
        print("  compared with %s" % label)
        tag, ok, detail = verdict(head_gen, head_fp, mine["gen"], mine["fp"])
        check(VERDICTS[tag], ok, detail)

    # ⛔ ★A probe that cannot fail its own calibration★ — this repository has been burnt by exactly
    #    that (lesson_a_probe_that_cannot_fail_its_own_calibration). The live row above is green on a
    #    clean tree ★by design★, so it proves nothing on its own. These rows drive `verdict()` through
    #    ★every branch★, including the two that must be red.
    print("\n★3b. control group — the discipline check ★can★ fail")
    CASES = [
        ("unchanged since HEAD",                       (1, "aa", 1, "aa"), "match",           True),
        ("HEAD predates generations",                  (None, None, 3, "aa"), "baseline",     True),
        ("code changed, generation bumped",            (1, "aa", 2, "bb"), "bumped",          True),
        ("⛔ code changed, generation did NOT",         (1, "aa", 1, "bb"), "not-bumped",     False),
        ("⛔ code changed, generation went backwards",  (2, "aa", 1, "bb"), "not-bumped",     False),
        ("⛔ generation moved with no code change",     (1, "aa", 2, "aa"), "gen-alone",      False),
        ("⛔ an unparsable HEAD generation is not accepted", ("x", "aa", 1, "bb"), "not-bumped", False),
    ]
    for label, args, want_tag, want_ok in CASES:
        got_tag, got_ok, _d = verdict(*args)
        good = (got_tag, got_ok) == (want_tag, want_ok)
        if not good:
            FAILS.append("control: %s → %s/%s (wanted %s/%s)" % (label, got_tag, got_ok, want_tag, want_ok))
        print("  %s %-46s → %s" % ("✅" if good else "❌", label, got_tag))

    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the ruler's ordering key is copy-stable, and the discipline behind it is checked")
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
        if _HOME_BEFORE is None:
            os.environ.pop("BRAIN_HOME", None)
        else:
            os.environ["BRAIN_HOME"] = _HOME_BEFORE
    sys.exit(code)
