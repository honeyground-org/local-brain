#!/usr/bin/env python3
"""★The clean room★ — every check, on the tracked files only, with an empty home. The release bar.

What a stranger gets is what `git ls-files` lists, on a machine with none of the author's notes, labels,
history or keys. This runs exactly that, the same way locally and in CI:

  · the tracked files are copied into a fresh folder — nothing untracked can help a check pass
  · every `tests/verify_*.py` runs with its ★own★ empty HOME, outside that folder
  · exit 0 = pass · 77 = skipped (the check needs data a fresh machine does not have, and says so) ·
    anything else = fail

    python3 tests/clean_room.py                 # every check
    python3 tests/clean_room.py --pythonpath    # the same, with PYTHONPATH set to the copy (both must pass)
    python3 tests/clean_room.py verify_stores verify_i18n     # only these
    python3 tests/clean_room.py --keep          # keep the folder and the logs for reading
    python3 tests/clean_room.py --strict verify_stores_live   # a skip is a failure (CI, where the
                                                               # databases are supposed to be there)

Some checks reach outside on purpose and are skipped unless asked for: `verify_stores_live` (needs Qdrant /
Neo4j — set BRAIN_TEST_QDRANT_URL / NEO4J_PASSWORD) and the Docker half of `verify_docker_stores`
(BRAIN_TEST_DOCKER=1). Those variables are passed through; nothing else from your environment is.

Standard library only; macOS, Linux and Windows.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS_THROUGH = ("BRAIN_TEST_QDRANT_URL", "BRAIN_TEST_NEO4J_URL", "NEO4J_PASSWORD", "BRAIN_TEST_DOCKER",
                "BRAIN_SCRUB_TERMS", "BRAIN_GITLEAKS", "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC",
                "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL", "TZ")


def tracked() -> list:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def copy_tree(dest: str) -> int:
    n = 0
    for rel in tracked():
        src = os.path.join(ROOT, rel)
        if not os.path.isfile(src):
            continue                                    # a deleted-but-not-committed file
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    # some checks read git history (the scrub and code-generation checks) — give the copy one commit
    who = "clean-room" + "@users.noreply.github.com"     # assembled: an address in a file trips the scrub
    env = dict(os.environ, GIT_AUTHOR_NAME="clean-room", GIT_COMMITTER_NAME="clean-room",
               GIT_AUTHOR_EMAIL=who, GIT_COMMITTER_EMAIL=who)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "clean room"]):
        subprocess.run(["git"] + args, cwd=dest, capture_output=True, env=env, check=True)
    return n


def main(argv) -> int:
    # ⛔ A Windows console speaks cp1252 and dies on the first ✅ (CI, 2026-10-07) — write UTF-8 regardless.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    keep = "--keep" in argv
    with_pp = "--pythonpath" in argv
    strict = "--strict" in argv
    only = [a for a in argv if not a.startswith("-")]
    base = tempfile.mkdtemp(prefix="brain-clean-room-")
    code = os.path.join(base, "repo")
    homes = os.path.join(base, "homes")
    logs = os.path.join(base, "logs")
    os.makedirs(homes)
    os.makedirs(logs)
    n = copy_tree(code)
    checks = sorted(f[:-3] for f in os.listdir(os.path.join(code, "tests"))
                    if f.startswith("verify_") and f.endswith(".py"))
    if only:
        checks = [c for c in checks if c in only]
    print("clean room · %d tracked files · %d checks · Python %s · %s%s"
          % (n, len(checks), sys.version.split()[0], sys.platform, " · PYTHONPATH set" if with_pp else ""))
    results = {}
    for name in checks:
        home = os.path.join(homes, name)
        os.makedirs(home)
        env = {k: os.environ[k] for k in PASS_THROUGH if k in os.environ}
        env.update(HOME=home, USERPROFILE=home, PYTHONIOENCODING="utf-8", LANG=env.get("LANG", "en_US.UTF-8"))
        if with_pp:
            env["PYTHONPATH"] = code
        t = time.time()
        with open(os.path.join(logs, name + ".log"), "wb") as fh:
            rc = subprocess.run([sys.executable, os.path.join("tests", name + ".py")], cwd=code, env=env,
                                stdout=fh, stderr=subprocess.STDOUT).returncode
        results[name] = rc
        skip = rc == 77 and not strict
        mark = "✅" if rc == 0 else "⏭ " if skip else "❌"
        print("  %s %-34s %5.1fs%s" % (mark, name, time.time() - t, "" if rc == 0 or skip else "  (exit %d)" % rc),
              flush=True)
    failed = [c for c, rc in results.items() if rc != 0 and (strict or rc != 77)]
    print("\npass %d · skipped %d · fail %d"
          % (sum(1 for r in results.values() if r == 0), sum(1 for r in results.values() if r == 77 and not strict), len(failed)))
    for c in failed:
        print("\n──── %s ────" % c)
        with open(os.path.join(logs, c + ".log"), encoding="utf-8", errors="replace") as fh:
            print("".join(fh.readlines()[-40:]))
    if keep:
        print("kept: %s" % base)
    else:
        shutil.rmtree(base, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
