"""Building the public snapshot — ★one manifest keeps the check and the export moving together★ (2026-09-03).

## Why a script

The public method is ★a snapshot★ (user 2026-09-03): this repo stays as-is internally, and the
public copy goes out as a new repo holding only the current files. The history has an internal name
in it 81 times, and `git push` sends history, not files.

⛔ Copy it by hand and ★it drifts from the list next time★ — this repo has been burned repeatedly by
*"something written in two places gets fixed in only one."* So this script and
`verify_public_scrub.py` read ★the same `tests/public_exclude.txt`★.

## Order — ★copy, then re-measure right there★

    1. pick the public files via the manifest      (git ls-files - the exclude list)
    2. copy them into the target folder            (create it if missing; if it exists, replace ★only tracked files★)
    3. ⛔ runs the scrub check ★again★ in the target folder
       — terms are passed via `BRAIN_SCRUB_TERMS` (`scrub_terms.txt` is gitignored,
         so it never gets copied → skip passing it and axes 1·2 die as `unknown`)
    4. commits only if it passes

## ⛔ On deleting things

Unless `--force`, it deletes ★only the files git tracks★ in the target folder. That folder's
`.git`·config·untracked files are never touched — someone else's work never vanishes silently.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_public_scrub as scrub  # noqa: E402

ROOT = scrub.ROOT
DEFAULT_DEST = os.path.join(os.path.dirname(ROOT), "local-brain-public")


def run(args, cwd, check=True):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise SystemExit(f"⛔ failed: {' '.join(args)}\n{p.stdout}{p.stderr}")
    return p


def main():
    dest = DEFAULT_DEST
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        dest = os.path.abspath(args[0])
    dry = "--dry-run" in sys.argv

    pub, exc = scrub.split_public()
    print("=" * 74)
    print("public snapshot export")
    print("=" * 74)
    print(f"source  {ROOT}")
    print(f"target  {dest}")
    print(f"public {len(pub)} · excluded by the manifest {len(exc)}")
    for f in exc:
        print(f"   excluded: {f}")
    if dry:
        print("\n--dry-run — nothing was written")
        return 0

    first = not os.path.isdir(os.path.join(dest, ".git"))
    os.makedirs(dest, exist_ok=True)
    if first:
        run(["git", "init", "-q", "-b", "main"], dest)
        print("\nmade a new repo (main)")
    else:
        # ★only tracked files★ get cleared — .git·config·anything someone else left stays
        old = run(["git", "ls-files"], dest).stdout.split()
        for f in old:
            p = os.path.join(dest, f)
            if os.path.exists(p):
                os.remove(p)
        print(f"\ncleared {len(old)} tracked file(s) from the previous snapshot")

    for f in pub:
        src, dst = os.path.join(ROOT, f), os.path.join(dest, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    print(f"copied {len(pub)} file(s)")

    # ── ⛔ re-measures right there (the copy step may have revived something)
    terms, src_name = scrub.terms()
    env = dict(os.environ)
    if terms:
        env["BRAIN_SCRUB_TERMS"] = ",".join(terms)
        print(f"passing {len(terms)} term(s) to the check (source {src_name})")
    else:
        print("⛔⛔ no term list — the check's axes 1·2 become `unknown`")
    run(["git", "add", "-A"], dest)
    checker = os.path.join(dest, "tests", "verify_public_scrub.py")
    p = subprocess.run([sys.executable, checker], cwd=dest, env=env,
                       capture_output=True, text=True)
    tail = [l for l in p.stdout.splitlines()
            if l.startswith(("✅", "⛔", "   ·")) or "control group" in l]
    print("\n── result of re-measuring in the target folder ──")
    print("\n".join(tail[-12:]))
    if p.returncode:
        print("\n⛔ the check blocked it — nothing was committed. Fix the items above and run it again.")
        return 1

    print("\n✅ the snapshot passed the check. Ready to commit.")
    print(f"   next: cd {dest} && git commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
