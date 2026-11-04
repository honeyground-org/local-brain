#!/usr/bin/env python3
"""★Self-evolution: a repeated error becomes a gated fix, and nothing personal leaves the machine★ (local · budget 0)

Why this exists (2026-11-04)
----------------------------
A hook that fails withdraws quietly (it must never block the host), so a defect could break recall in every
session and nobody would see it. `brain/evolve.py` records each unhandled error (class and brain frames, never
the message), and - only when a maintainer turns it on - lets an agent write a failing check and a fix in an
isolated worktree. The change leaves the machine only through every gate. The agent, the reviewer and `gh` are
stand-ins here (no model, no network); the upstream is a synthetic repository with its own clean room.

  ① signals: the module runner, the prompt hook, the session hooks and the daily jobs record an unhandled error
     as class + brain frames; the message (control: it holds a secret phrase) is never written
  ② the trigger: mode local never starts a run and creates nothing (control); turning it on counts only what
     happens from then on; a due candidate starts exactly one run, also from the real session-start entry; a
     live lock or a corrupted state file starts nothing and raises nothing
  ③ every gate stops a change before anything is pushed, each with its own reason: a phrase from the person's
     notes (control: a phrase the repository itself already contains passes), the home path, a corpus root,
     an e-mail address, a phrase in the description, non-ASCII outside the locales (control: inside them
     passes), no reproducing check, a check that passes on the base, a check that skips on the base, a broken
     check, a reviewer who finds it personal, an agent that says it is not a code defect
  ④ propose mode keeps the gated change as a local branch and a report; nothing is pushed
  ⑤ merge mode pushes a branch and opens a pull request that follows the repository's rules (title format,
     sign-off, the five sections, no personal trace) with auto-merge; main is unchanged until it is merged;
     once merged, a later run installs it by fast-forward into a checkout on main (control: a checkout on
     another branch is left alone)
  ⑥ a candidate is tried again only when it occurs again, and never more than MAX_ATTEMPTS times
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = Path(tempfile.mkdtemp(prefix="brain-verify-evolve-"))
NOTES = _TMP / "my-notes"
NOTES.mkdir()
os.environ["BRAIN_HOME"] = str(_TMP / "home")
os.environ["BRAIN_CONFIG"] = str(_TMP / "config.json")
(_TMP / "config.json").write_text(json.dumps({"sources": [{"name": "memory", "path": str(NOTES), "include": ["*.md"]}]}),
                                  encoding="utf-8")
os.environ.pop("BRAIN_HOOK_DISABLED", None)
MAIL = "evolve@" + "test.invalid"              # split so the public scrub does not count a made-up address
OTHER_MAIL = "jane.doe@" + "corp-mail.com"
for k, v in (("GIT_AUTHOR_NAME", "Evolve Test"), ("GIT_AUTHOR_EMAIL", MAIL),
             ("GIT_COMMITTER_NAME", "Evolve Test"), ("GIT_COMMITTER_EMAIL", MAIL),
             ("GIT_CONFIG_NOSYSTEM", "1")):
    os.environ[k] = v

from brain import evolve, store, tail  # noqa: E402

FAILS = []
USER_PHRASE = "remember the quarterly invoice exporter for the finance team"
SHARED_PHRASE = "the quick brown fox jumps over the lazy dog"
SECRET = "my private prompt about payroll"


def check(label: str, ok, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + str(detail)[:300]) if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


def git(*args, cwd=None) -> str:
    return subprocess.run(["git"] + [str(a) for a in args], cwd=None if cwd is None else str(cwd), check=True,
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


CLEAN_ROOM = '''import os, subprocess, sys
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
only = [a for a in sys.argv[1:] if not a.startswith("-")]
files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.split()
checks = [f for f in files if f.startswith("tests/verify_") and f.endswith(".py")
          and (not only or os.path.basename(f)[:-3] in only)]
bad = [f for f in checks if subprocess.run([sys.executable, f], cwd=root).returncode not in (0, 77)]
sys.exit(1 if bad else 0)
'''
GOOD_TEST = 'import sys\nsys.path.insert(0, ".")\nfrom lib import add\nsys.exit(0 if add(2, 3) == 5 else 1)\n'


def upstream():
    origin = _TMP / "origin.git"
    git("init", "-q", "--bare", "-b", "main", origin)
    seed = _TMP / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    (seed / "lib.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (seed / "README.md").write_text("A demo. %s.\n" % SHARED_PHRASE, encoding="utf-8")
    (seed / "tests").mkdir()
    (seed / "tests" / "clean_room.py").write_text(CLEAN_ROOM, encoding="utf-8")
    (seed / "tests" / "verify_basic.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "init", cwd=seed)
    git("remote", "add", "origin", origin, cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    clone = _TMP / "clone"
    git("clone", "-q", origin, clone)
    return origin, clone


RUNS = []


def agent(extra=None, test=GOOD_TEST, fix=True, fixable=True, break_basic=False, summary="add returns the sum",
          extra_path="lib.py"):
    def run(work, prompt):
        work = Path(work)
        RUNS.append(prompt)
        if test is not None:                             # a new check each run
            (work / "tests" / ("verify_add_%d.py" % len(RUNS))).write_text(test, encoding="utf-8")
        if fix:
            (work / "lib.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        if extra:
            target = work / extra_path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(extra)
        if break_basic:
            (work / "tests" / "verify_basic.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
        return {"fixable": fixable, "title": "lib: add returns the sum", "summary": summary,
                "effect": "add(2, 3) is 5", "extensibility": "no new seam", "risks": "none; revert to undo",
                "test_files": [], "reason": "synthetic"}
    return run


def reviewer(general=True, risk=False):
    return lambda work, diff, key: {"general": general, "regression_risk": risk, "reason": "stand-in"}


class FakeGh:
    def __init__(self):
        self.calls, self.states, self.bodies = [], {}, []

    def __call__(self, args, cwd):
        self.calls.append(list(args))
        if args[:2] == ["pr", "create"]:
            url = "https://example.invalid/o/r/pull/%d" % (len(self.states) + 1)
            self.states[url] = "OPEN"
            self.bodies.append((args[args.index("--title") + 1], args[args.index("--body") + 1]))
            return 0, url + "\n"
        if args[:2] == ["pr", "merge"]:
            return 0, ""
        if args[:2] == ["pr", "view"]:
            return 0, json.dumps({"state": self.states.get(args[2], "OPEN")})
        return 1, "unknown"

    def created(self):
        return [c for c in self.calls if c[:2] == ["pr", "create"]]


def errors_rows():
    path = evolve._errors()
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def bump(n=2):
    """The same defect happening again, through the real module runner."""
    for _ in range(n):
        subprocess.run([sys.executable, os.path.join(ROOT, "bin", "_run.py"), "brain.module_that_was_removed"],
                       capture_output=True, env=dict(os.environ))


def main() -> int:
    print("① signals")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "_run.py"), "brain.module_that_was_removed"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = errors_rows()
    check("the module runner re-raises (the host still sees the failure) and records it",
          r.returncode != 0 and rows and rows[-1]["where"] == "brain.module_that_was_removed"
          and rows[-1]["error"] == "ImportError" and rows[-1]["frames"] == ["bin/_run.py:<module>"], (r.returncode, rows))
    # the prompt hook swallows its failure and exits 0; the runner reads the swallowed error off the exit
    code = ("import runpy, sys\nfrom brain import hosts\n"
            "def boom(*a, **k):\n    raise RuntimeError(%r)\n"
            "hosts.pin_from_argv = boom\nsys.argv = [%r, 'brain.hook']\n"
            "runpy.run_path(%r, run_name='__main__')\n" % (SECRET, os.path.join(ROOT, "bin", "_run.py"),
                                                           os.path.join(ROOT, "bin", "_run.py")))
    r = subprocess.run([sys.executable, "-c", code], input="{}", capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
                       env=dict(os.environ, PYTHONPATH=ROOT))
    rows = errors_rows()
    hook_rows = [x for x in rows if x["where"] == "brain.hook"]
    check("a failing prompt hook still exits 0, and the runner records what it swallowed, with its brain frames",
          r.returncode == 0 and hook_rows and hook_rows[-1]["error"] == "RuntimeError"
          and "brain/hook.py:main" in hook_rows[-1]["frames"], (r.returncode, r.stderr[-300:], rows))

    before = len(errors_rows())
    runner = os.path.join(ROOT, "bin", "_run.py")
    ok_hook = subprocess.run([sys.executable, runner, "brain.hook", "--host", "claude-code"], input="{}",
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    typo = subprocess.run([sys.executable, runner, "brain.cli", "no-such-command"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    check("control: a hook that succeeds and a mistyped command (exit 2) record nothing",
          ok_hook.returncode == 0 and typo.returncode == 2 and len(errors_rows()) == before,
          (ok_hook.returncode, typo.returncode, errors_rows()[before:]))

    def failing_stop():
        raise ValueError(SECRET)
    check("a failing session hook (tail) exits 0 and is recorded",
          tail._safe(failing_stop) == 0 and errors_rows()[-1]["where"] == "tail.failing_stop")
    from brain import jobs
    jobs._run("BRAIN_TEST_EVOLVE_LOG", "evolve-test", lambda out: (_ for _ in ()).throw(OSError(SECRET)))
    check("a failing daily job is recorded", errors_rows()[-1]["where"] == "jobs.evolve-test", errors_rows()[-1])
    raw = evolve._errors().read_text(encoding="utf-8")
    check("the message is never written (control: it held a secret phrase)", SECRET not in raw and "payroll" not in raw)

    print("② the trigger")
    launched = []
    if evolve._state_path().exists():
        evolve._state_path().unlink()
    check("mode local (the default) starts nothing and creates no file (control)",
          evolve.maybe_start(launch=lambda: launched.append(1) or 1) is None and not launched
          and not evolve._state_path().exists())
    evolve.view()                                        # `brain evolve` in mode local: the file now exists
    bump(2)
    check("mode local with a state file and repeated errors still starts nothing (control)",
          evolve.maybe_start(launch=lambda: launched.append(1) or 1) is None and not launched
          and evolve.view()["mode"] == "local")
    origin, clone = upstream()
    evolve.configure("merge", str(clone))
    check("turning it on counts only what happens from then on (earlier errors were seen by people)",
          not evolve.due(evolve.scan()))
    bump(1)
    check("one occurrence is not due yet (it can be the weather)", not evolve.due(evolve.scan()))
    bump(1)
    value = evolve.scan()
    key = (evolve.due(value) or [""])[0]
    check("a second occurrence makes it due", key.startswith("error:brain.module_that_was_removed ImportError"), value["candidates"])
    check("the agent's prompt carries no home or brain-home path",
          str(Path.home()) not in evolve.agent_prompt(key, value["candidates"][key])
          and os.environ["BRAIN_HOME"] not in evolve.agent_prompt(key, value["candidates"][key]))
    bump(1)
    check("a due candidate starts exactly one run", evolve.maybe_start(launch=lambda: launched.append(1) or 7) == 7
          and launched == [1])
    from brain import deferred
    real_launch, real_start = evolve._launch, deferred.on_session_start
    evolve._launch = lambda: launched.append("notice") or 8
    deferred.on_session_start = lambda db: {}            # no index worker in this check
    sys.stdin = io.StringIO("{}")
    try:
        bump(1)
        tail.main_notice()
    finally:
        evolve._launch, deferred.on_session_start = real_launch, real_start
        sys.stdin = sys.__stdin__
    check("…and the real session-start entry is where it starts", launched[-1] == "notice", launched)
    evolve._lock_path().write_text(json.dumps({"pid": os.getpid(), "at": __import__("time").time()}), encoding="utf-8")
    bump(1)
    check("a live lock starts nothing", evolve.maybe_start(launch=lambda: launched.append(1) or 1) is None)
    evolve._lock_path().unlink()
    saved = evolve._state_path().read_text(encoding="utf-8")
    evolve._state_path().write_text("{not json", encoding="utf-8")
    try:
        check("a corrupted state file raises nothing", evolve.maybe_start(launch=lambda: 1) in (None, 1))
    except Exception as error:                           # noqa: BLE001
        check("a corrupted state file raises nothing", False, error)
    evolve._state_path().write_text(saved, encoding="utf-8")

    # the person's own notes, as indexed - the personal gate reads them
    db = store.connect()
    for i, body in enumerate(("Note: %s by Friday." % USER_PHRASE, "A saying: %s." % SHARED_PHRASE)):
        db.execute("INSERT INTO docs(name, path, source, body) VALUES (?, ?, 'memory', ?)",
                   ("note%d" % i, str(NOTES / ("note%d.md" % i)), body))
    db.commit()
    db.close()

    print("③ every gate stops a change before anything is pushed")
    gh = FakeGh()
    cases = [
        ("a phrase from the person's notes", agent(extra="# " + USER_PHRASE + "\n"), reviewer(), "personal"),
        ("the home path", agent(extra='CACHE = "%s/.cache"\n' % Path.home()), reviewer(), "personal"),
        ("a corpus root", agent(extra='NOTES = "%s"\n' % NOTES), reviewer(), "personal"),
        ("an e-mail address", agent(extra="# ask %s\n" % OTHER_MAIL), reviewer(), "personal"),
        ("a personal phrase in the description", agent(summary="fixes " + USER_PHRASE), reviewer(), "personal"),
        ("non-ASCII text outside the locales", agent(extra="# résumé handling\n"), reviewer(), "language"),
        ("no reproducing check", agent(test=None), reviewer(), "reproduces"),
        ("a check that passes on the base too", agent(test="import sys\nsys.exit(0)\n"), reviewer(), "reproduces"),
        ("a check that skips on the base", agent(test="import sys\nsys.exit(77)\n"), reviewer(), "reproduces"),
        ("a broken check", agent(break_basic=True), reviewer(), "checks"),
        ("a reviewer who finds it personal", agent(), reviewer(general=False), "independent review"),
        ("a reviewer who sees a regression risk", agent(), reviewer(risk=True), "independent review"),
        ("an agent that says it is not a code defect", agent(fixable=False), reviewer(), "not a code defect"),
    ]
    for label, stand_in, review, expect in cases:
        refs = git("ls-remote", origin)
        row = evolve.run(key, agent=stand_in, reviewer=review, gh=gh, checkout=clone)
        reason = row.get("reason", "")
        check("%s → rejected (%s), nothing pushed" % (label, expect),
              row["outcome"] == "rejected" and expect in reason and git("ls-remote", origin) == refs, (reason, row.get("gates")))
    check("no pull request was opened for a rejected change", not gh.created(), gh.calls)
    base = git("rev-parse", "origin/main", cwd=clone)
    check("control: a phrase the repository already contains is not personal",
          evolve.personal_hits([("lib.py", "# " + SHARED_PHRASE)], clone, base) == [])
    check("control: the same check catches a phrase only the notes contain",
          evolve.personal_hits([("lib.py", "# " + USER_PHRASE)], clone, base) != [])
    check("control: non-ASCII inside brain/locales/ passes the language gate",
          evolve.language_hits([("brain/locales/fr.json", '"x": "résumé"')]) == []
          and evolve.language_hits([("brain/x.py", "# résumé")]) != [])

    print("④ propose mode")
    evolve.configure("propose")
    refs = git("ls-remote", origin)
    row = evolve.run(key, agent=agent(), reviewer=reviewer(), gh=gh, checkout=clone)
    check("the gated change is kept as a local branch and a report",
          row["outcome"] == "proposed" and row["branch"] in git("branch", "--list", "evolve/*", cwd=clone)
          and Path(row["report"]).exists(), row)
    check("…and nothing is pushed, no pull request", git("ls-remote", origin) == refs and not gh.created())

    print("⑤ merge mode")
    evolve.configure("merge")
    main_before = git("rev-parse", "main", cwd=origin)
    row = evolve.run(key, agent=agent(), reviewer=reviewer(), gh=gh, checkout=clone)
    check("outcome: a pull request with auto-merge", row["outcome"] == "pr" and row.get("auto_merge") is True, row)
    check("…its branch is pushed, main is unchanged until it is merged",
          row.get("branch") and git("rev-parse", row["branch"], cwd=origin) == row.get("commit")
          and git("rev-parse", "main", cwd=origin) == main_before)
    title, body = gh.bodies[-1] if gh.bodies else ("", "")
    message = git("log", "-1", "--format=%B", row.get("commit", "HEAD"), cwd=origin)
    check("…the title follows the repository's rule", bool(evolve.TITLE.match(title)), title)
    check("…the description has the five sections",
          all("## " + s in body for s in ("Purpose", "Effect", "Scalability and extensibility", "Risks and rollback", "Checks")), body)
    check("…the commit is signed off", "Signed-off-by: Evolve Test <%s>" % MAIL in message, message)
    check("…and carries no personal trace",
          not any(p in title + body + message for p in (str(Path.home()), os.environ["BRAIN_HOME"], str(NOTES), "quarterly invoice")))
    check("…auto-merge is asked for, squash", ["pr", "merge", row.get("pr"), "--auto", "--squash"] in gh.calls, gh.calls)
    git("fetch", "-q", "origin", cwd=clone)
    other = _TMP / "other-checkout"
    git("clone", "-q", origin, other)
    git("checkout", "-q", "-b", "my-work", cwd=other)
    other_head = git("rev-parse", "HEAD", cwd=other)
    installed = _TMP / "installed"
    git("clone", "-q", origin, installed)
    gh.states[row["pr"]] = "MERGED"
    git("push", "-q", "origin", "%s:refs/heads/main" % row["branch"], cwd=clone)   # the repository merged it
    value = evolve.state()
    value["followed_at"] = 0
    evolve.save(value)
    check("a merged pull request makes the next session start launch a run",
          evolve.maybe_start(launch=lambda: "follow") == "follow")
    later = evolve.run(agent=agent(fixable=False), reviewer=reviewer(), gh=gh, checkout=installed)
    merged = [f for f in later.get("followed", []) if f["outcome"] == "merged"]
    check("…which records it merged and fast-forwards the checkout on main",
          merged and merged[0].get("installed") is True
          and git("rev-parse", "HEAD", cwd=installed) == git("rev-parse", "main", cwd=origin)
          and "a + b" in (installed / "lib.py").read_text(encoding="utf-8"), later)
    check("…and stops following it", not evolve.state()["prs"] and evolve.state()["candidates"][key]["status"] == "merged")
    ff = evolve.fast_forward(other)
    check("control: a checkout on another branch is left alone",
          ff["installed"] is False and git("rev-parse", "HEAD", cwd=other) == other_head, ff)

    print("⑥ a candidate is tried again only when it occurs again")
    value = evolve.state()
    value["candidates"][key]["attempts"] = 0             # the gate cases above were explicit runs
    evolve.save(value)
    check("after a merge it is not due", key not in evolve.due(evolve.scan()))
    bump(1)
    check("…and is due again once it recurs", key in evolve.due(evolve.scan()), evolve.state()["candidates"].get(key))
    for _ in range(evolve.MAX_ATTEMPTS):
        evolve.run(key, agent=agent(fixable=False), reviewer=reviewer(), gh=gh, checkout=clone)
        bump(1)
    check("…but never more than %d attempts (no endless loop)" % evolve.MAX_ATTEMPTS, key not in evolve.due(evolve.scan()))

    print("\n" + ("FAIL " + ", ".join(FAILS) if FAILS else "PASS"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
