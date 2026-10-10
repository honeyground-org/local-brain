"""★A check that measures this person's own data says so when there is none★ — skipped, never failed.

## Why (measured in a clean room, 2026-10-06)

The public files were copied into an empty folder and every check was run with a fresh HOME — no notes,
no labelled sample, no conversation history, no engine chosen. 18 of 43 went red. Most were not defects:
they measure ★this machine's★ corpus (does the threshold hold on *your* labelled questions, does the
proxy sample agree on *your* notes) and there was nothing to measure. A stranger's first `tests/` run
then reads as "this project is broken".

The fix is not to make them pass on nothing — a check that passes with no data is the probe that cannot
fail. It is to ★stop and say what is missing★, with a distinct exit code, so a harness counts it as
neither a pass nor a failure:

    exit 77  skipped — the automake convention

How a check uses it:

    from tests._needs import labelled_sample, memories
    labelled_sample()          # exits 77 with the reason when there is none
"""
from __future__ import annotations

import os
import sys

SKIP = 77


def skip(reason: str, how: str = "") -> None:
    print("⏭ skipped — %s" % reason)
    if how:
        print("   to run it: %s" % how)
    sys.exit(SKIP)


def labelled_sample(min_a: int = 0, min_c: int = 0) -> None:
    """This person's labelled short-query sample (`<eval folder>/short.json`)."""
    from brain import calibrate, evalinit
    min_a = min_a or calibrate.MIN_A
    min_c = min_c or calibrate.MIN_C
    a, c = len(evalinit.load_A()), len(evalinit.load_C())
    if a < min_a or c < min_c:
        skip("needs your own labelled sample (%d questions with an answer and %d without; found %d and %d)"
             % (min_a, min_c, a, c),
             "`brain eval-init`, then pick the right answer for each question and save it as "
             + os.path.join(evalinit.eval_dir(), "short.json"))


def memories(db, at_least: int = 1) -> None:
    """Indexed notes in the memory source."""
    n = db.execute("SELECT COUNT(*) FROM docs WHERE source='memory'").fetchone()[0]
    if n < at_least:
        skip("needs your own indexed notes (at least %d in the memory source; found %d)" % (at_least, n),
             "install brain, point the memory source at your notes, then `brain index`")


def proxy_sample(db) -> None:
    """A corpus large enough for the label-free proxy sample (§brain/proxy.py)."""
    from brain import calibrate
    _a, _c, kind = calibrate.bench_sample(db)
    if kind == "none":
        skip("needs a corpus large enough for a proxy sample (or your own labelled one)",
             "index more of your notes, or label a sample with `brain eval-init`")


def prompts(at_least: int = 1) -> None:
    """Real prompts from a host's history."""
    from brain import hosts
    n = sum(len(h.prompts(limit=at_least)) for h in hosts.detected())
    if n < at_least:
        skip("needs real prompts from a coding agent's history (found %d)" % n,
             "use brain with Claude Code or Codex for a while")


def entry(script: str, module: str) -> list:
    """The command for one of brain's entry points, the way the installer picks it (§install.entry_command):
    the repo's `bin/<script>` on POSIX, `<this python> -m <module>` on Windows — which has no `sh` to run
    the scripts (WinError 193, CI 2026-10-10). The installed `.exe` shims are checked by verify_package.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local = os.path.join(root, "bin", script)
    if os.name != "nt" and os.path.isfile(local):
        return [local]
    return [sys.executable, "-m", module]
