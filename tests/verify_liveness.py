"""Liveness check — ★can't "runs on its own" be faked green by running it by hand★.

## Why this check exists (2026-08-31)

This repo has met the same family ★four times★:

  ① "registered" does not mean "ran"        (cron registration came after the cron time)
  ② a written standard that never runs isn't a standard (`calibrate`, which nobody calls)
  ③ "ran" does not mean "keeps running"       (cron → launchd)
  ④ ★"run by hand" does not mean "runs on its own"★  ← this is the spot this check guards

④ — why it's dangerous: the verdict looked only at ★the job log's age★. That log gets
refreshed just by typing `./bin/brain-vec-daily` in a terminal — even with the schedule dead,
the diagnosis goes green. And that green flows straight into the scorecard's `liveness` axis.

★A probe that cannot fail its own calibration is not a probe.★ So this check deliberately feeds
the verdict ★the exact situations that would fake a green★.
"""
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The outbound doors are tested with an engine ★named★ (a key alone chooses nothing, §engines) — the calls
# are intercepted, nothing leaves.
os.environ.setdefault("BRAIN_JUDGE_PROVIDER", "gemini")
os.environ.setdefault("BRAIN_EMBED_PROVIDER", "gemini")
from brain import health                                  # noqa: E402

FAIL = []
NOW = time.time()


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def _write_log(path, starts, at_line="start"):
    """Mimics a job log — `starts` is a list of (epoch)."""
    with open(path, "w", encoding="utf-8") as fh:
        for t in starts:
            fh.write("── %s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S",
                                                   time.localtime(t)), at_line))
            fh.write("{\"added_chunks\": 1}\n── end\n")
    return path


def _probe(known=True, runs=1, stdout=None, last_exit="0", at=(9, 0)):
    return lambda _label: {"known": known, "runs": runs, "stdout": stdout,
                           "last_exit": last_exit, "at": at}


def _job(log, every=1):
    return (("job", log, "what", every, "com.test.job"),)


def _run(log, probe, every=1):
    r = health.schedules(now=NOW, _probe=probe, _jobs=_job(log, every))
    return r["jobs"][0]


def main():
    print("=" * 72 + "\nliveness — can it be faked green by running it by hand\n" + "=" * 72)
    tmp = tempfile.mkdtemp(prefix="brain-liveness-")
    log = os.path.join(tmp, "job.log")
    ld_log = os.path.join(tmp, "job.launchd.log")

    # epoch for today's 09:00 (scheduled time) and just-now (not scheduled)
    lt = time.localtime(NOW)
    at_nine = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 3, 0, 0, -1))
    if at_nine > NOW:                                     # if before 09:00, use yesterday's
        at_nine -= 86400

    # ── ④ a fake green: only the job log is fresh (a human ran it by hand) ──────────────
    _write_log(log, [NOW - 60])                           # ran just now — not the scheduled time
    j = _run(log, _probe(runs=0, stdout=None))
    check("★something a human ran by hand is not green★", j["ok"] is False,
          "by=%s · %s day(s) since the job" % (j["by"], j["ran_days"]))
    check("  and it says ★why it's red★", "by hand" in j["why"], j["why"][:60])

    # ── wiring-only check (kickstart / catch-up): launchd fired, but not at the scheduled time ──
    open(ld_log, "w", encoding="utf-8").close()                             # launchd-only log = just now
    j = _run(log, _probe(runs=1, stdout=ld_log))
    # ⛔ ★one requirement was folded here★ (2026-09-01) — at first it required "a kickstart wake
    #    is also not green." That requirement was only possible because the window was narrowed to ±5min.
    #    But 09-01 measurement showed the machine asleep, launchd ★catching up at 09:48★,
    #    and that was exactly the point of the cron → launchd migration. Widen the window and kickstart
    #    and catch-up ★become indistinguishable★ — because launchd doesn't hand over that information.
    #    ⇒ guard what can be guarded: ★running the script directly from a terminal★ is what the
    #      check above blocks (the launchd-only log doesn't change then). kickstart is exceptional
    #      behaviour, and ★a green day with no kickstart★ becomes the evidence.
    check("wiring being alive is stated ★separately★ (wired)", j.get("wired") is True)
    check("⛔ kickstart cannot be distinguished from a catch-up — the code says so in writing",
          "distinguished★ from a catch-up" in open(os.path.join(
              os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
              "brain", "health.py"), encoding="utf-8").read())

    # ── a real green: fires on its own at the scheduled time ────────────────────────────
    _write_log(log, [at_nine])
    j = _run(log, _probe(runs=1, stdout=ld_log))
    check("★fires on its own at the scheduled time = green★", j["ok"] is True,
          "by=%s · %s day(s) since the calendar slot" % (j["by"], j.get("cal_days")))

    # ── ran but failed: exit code isn't 0 ────────────────────────────
    j = _run(log, _probe(runs=1, stdout=ld_log, last_exit="1"))
    check("★ran but failed is not green★", j["ok"] is False,
          "exit code %s" % j.get("last_exit"))
    check("  'never ran' (never exited) does not count as a failure",
          _run(log, _probe(stdout=ld_log, last_exit="(never exited)"))["ok"] is True)

    # ── an old scheduled firing is red ───────────────────────────────────
    _write_log(log, [at_nine - 4 * 86400])
    j = _run(log, _probe(runs=1, stdout=ld_log))
    check("★a firing from 4 days ago is not green★", j["ok"] is False, "by=%s" % j["by"])

    # ── a machine that can't be asked about launchd (not macOS) — falls back to the old verdict ────
    _write_log(log, [NOW - 60])
    j = _run(log, _probe(known=False))
    check("when launchd can't be asked, it ★says unknown★ and falls back to log age",
          j["by"] == "unknown" and j["ok"] is True, "by=%s ok=%s" % (j["by"], j["ok"]))

    # ── ⛔ does it find the ★last★ run in a multi-line log (a real 2026-09-01 defect) ──
    #    `_START_RE` was missing `re.M`, so `^` only looked at the file's ★first line★. So it
    #    couldn't read the 09:48 run and answered "7.8 days ago," pinning liveness at 0.
    #    Earlier checks ★passed anyway★ because they only ever wrote one line to the log.
    _write_log(log, [at_nine - 9 * 86400, at_nine - 4 * 86400, at_nine])  # oldest → newest
    j = _run(log, _probe(runs=1, stdout=ld_log))
    # ⛔ ★`x or 99` treats a legitimate 0.0 as missing★ (2026-09-14). `cal_days` is 0.0 whenever the
    #    newest run sits on ★today's★ slot — which is the case every time this check runs after 09:00.
    #    So the row failed by the clock, not by the code: green before 09:00, red after. A check that
    #    answers differently on identical code is the defect it exists to catch, wearing a hat.
    #    ⇒ "absent" is `None`, and only `None` gets the fallback.
    _cal = j.get("cal_days")
    check("★reads the most recent run in a multi-line log★ (re.M regression)",
          j["ok"] is True and _cal is not None and _cal < 1.5,
          "%s day(s) since the calendar slot (must read the newest of 3 log lines)" % _cal)

    # ── ★catching up★ after waking from sleep also counts as 'ran on its own' (2026-09-01) ────────
    #    scheduled for 09:00 but ran at 09:48 — the machine was asleep and launchd caught up.
    #    ★That is exactly the point of the cron → launchd migration.★ A narrow window discounted that success.
    _write_log(log, [at_nine + 48 * 60])
    j = _run(log, _probe(runs=1, stdout=ld_log))
    check("★a catch-up run (scheduled +48min) is also green★ — what's measured is not 'on time' but 'without a human'",
          j["ok"] is True, "by=%s" % j["by"])

    # ⛔ But a run ★before★ the scheduled time is not accepted — that was a human calling it
    _write_log(log, [at_nine - 3 * 3600])
    j = _run(log, _probe(runs=1, stdout=ld_log))
    check("⛔ a run ★before★ the scheduled time is not green (a human called it)",
          j["ok"] is False, "by=%s" % j["by"])

    # ── ⑤ ★a job that fails must ★say so with its exit code★★ (2026-09-22) ─────────
    #
    #    The verdict above already has a branch for it — *"the scheduler called it" is not "the job
    #    succeeded"* — and a row above proves that branch works. But the branch could never fire,
    #    because `jobs._run` ended at 0 ★no matter what★ (a deliberate "a cron must fail cleanly").
    #    Two correct decisions cancelled each other out, and nothing was watching the seam.
    #    Measured that day: both jobs died on DNS two days running, `stale` grew 0 → 47, and this
    #    axis reported ★100★ throughout. So the seam itself is now a row.
    import io                                             # noqa: E402
    from brain import jobs                                # noqa: E402
    os.environ["BRAIN_TEST_LOG"] = os.path.join(tmp, "runner.log")
    check("a body that finishes quietly exits 0",
          jobs._run("BRAIN_TEST_LOG", "runner.log", lambda fh: None) == 0)
    check("★a body that raises exits non-zero★ (and still writes one line, never a traceback)",
          jobs._run("BRAIN_TEST_LOG", "runner.log", lambda fh: 1 / 0) == jobs.FAILED)
    check("a body that reports failure exits non-zero",
          jobs._run("BRAIN_TEST_LOG", "runner.log", lambda fh: True) == jobs.FAILED)
    log_txt = open(os.environ["BRAIN_TEST_LOG"], encoding="utf-8").read()
    check("  the failure is ★one line in the log★, not a traceback",
          "stopped" in log_txt and "Traceback" not in log_txt)

    # ⛔ ★not everything that stops is a failure★ — the judge's daily budget wall is the design
    #    working (it lifts at Pacific midnight). Colour the axis for that and it goes red every
    #    busy day, and a red that is normal is a red nobody reads.
    from brain import ruledisc                            # noqa: E402
    real_discover = ruledisc.discover

    def _fake_discover(wall):
        return lambda **k: {"candidates": 1, "judged": 0, "failed": 0, "skipped": 0,
                            "stopped_early": True, "proposals": [],
                            "stop_reason": "daily quota" if wall else "network URLError",
                            "budget": {"used": 1, "limit": 500, "left": 499, "wall": wall}}
    try:
        ruledisc.discover = _fake_discover(False)
        check("★a job stopped by a network error is a failure★", jobs._rules_body(io.StringIO()) is True)
        ruledisc.discover = _fake_discover(True)
        check("★control★: stopped by the daily budget wall is ★not★ a failure",
              jobs._rules_body(io.StringIO()) is False)
    finally:
        ruledisc.discover = real_discover

    # ── ★the two halves actually meet★ — the exit code above, read by the verdict ──────
    #    This is the row that was missing. Each half was right on its own.
    # ⛔ At the scheduled time itself, not "an hour ago" — that only lands after 09:00 when the clock
    #    reads 10:00 or later, so this row was red in CI at 05:00 UTC and green on the author's afternoon
    #    (2026-10-07). A check whose verdict depends on the time of day it runs is measuring the clock.
    fresh = _write_log(log, [at_nine])
    green = _run(fresh, _probe(runs=1, stdout=ld_log, last_exit="0"))
    red = _run(fresh, _probe(runs=1, stdout=ld_log, last_exit=str(jobs.FAILED)))
    check("★a job that exits FAILED is not green★ — end to end",
          green["ok"] is True and red["ok"] is False,
          "exit 0 → ok=%s · exit %d → ok=%s" % (green["ok"], jobs.FAILED, red["ok"]))
    check("  and the reason names the exit code", str(jobs.FAILED) in red["why"], red["why"][:70])

    # ── ⑥ ★the grace period for a network that has not arrived yet★ (2026-09-22) ────
    #
    #    Diagnosis: both daily jobs died on instant DNS failures on 5 of 15 days. Two explanations
    #    were tested and both were wrong — a failure at +0 min and a success at +76 min rule out
    #    "started late", and a launchd-run probe resolved the host in 4ms with 16 env vars, ruling
    #    out "launchd's environment". What is left is a machine waking into its scheduled hour with
    #    the interface still coming up. `_post` retries such errors once, 0.6s later — too short by
    #    an order of magnitude.
    #    ⚠️ Written from a diagnosis, ★not from a reproduction★. So the mechanism is what gets
    #       tested here, by injecting the fault; the cure itself is confirmed by tomorrow's log.
    import socket                                        # noqa: E402
    real_gai = socket.getaddrinfo

    def _fail_n(n):
        box = {"i": 0}

        def f(*a, **k):
            box["i"] += 1
            if box["i"] <= n:
                raise socket.gaierror(8, "nodename nor servname provided, or not known")
            return real_gai(*a, **k)                     # ⛔ the host must ★really★ resolve after n
        return f

    buf = io.StringIO()
    t0 = time.time()
    got = jobs._await_network(buf, "localhost", budget=30)
    check("★a healthy network costs nothing★ — no wait, and nothing in the log",
          got is True and (time.time() - t0) < 0.5 and buf.getvalue() == "",
          "%.0fms" % ((time.time() - t0) * 1000))

    socket.getaddrinfo = _fail_n(3)
    buf, t0 = io.StringIO(), time.time()
    try:
        got = jobs._await_network(buf, "localhost", budget=30)
    finally:
        socket.getaddrinfo = real_gai
    check("★a network that arrives late is waited for★, and the wait is written down",
          got is True and "network arrived" in buf.getvalue(),
          "%.1fs · %s" % (time.time() - t0, buf.getvalue().strip()[:46]))

    socket.getaddrinfo = _fail_n(10 ** 6)
    buf, t0 = io.StringIO(), time.time()
    try:
        got = jobs._await_network(buf, "localhost", budget=5)
    finally:
        socket.getaddrinfo = real_gai
    waited = time.time() - t0
    check("★one that never arrives gives up inside its budget★ — a grace period, not a hang",
          got is False and waited <= 6.0 and "never arrived" in buf.getvalue(),
          "%.1fs of a 5s budget" % waited)

    # ⛔ ★A resolver that hangs, not one that fails★ (2026-09-29). The row above injects ★instant★
    #    failures, and it stayed green while the real run took ★627s against 180s★: that morning each
    #    `getaddrinfo` blocked ~81s, and the budget was only checked between attempts. A fault is
    #    only a test of the guard if it has the shape the world actually produced.
    #    Calibrated against the code before the fix: this row went red at 20.0s of a 3s budget.
    import threading                                     # noqa: E402
    release = threading.Event()

    def _hang(*a, **k):
        release.wait(20)                                 # ⛔ ends on its own — never outlives the check
        raise socket.gaierror(8, "nodename nor servname provided, or not known")

    socket.getaddrinfo = _hang
    buf, t0 = io.StringIO(), time.time()
    try:
        got = jobs._await_network(buf, "localhost", budget=3)
    finally:
        socket.getaddrinfo = real_gai
        release.set()
    waited = time.time() - t0
    check("★a resolver that hangs still cannot outrun the budget★ — total elapsed, not sleep",
          got is False and waited <= 3.5 and "never arrived" in buf.getvalue(),
          "%.1fs of a 3s budget · %s" % (waited, buf.getvalue().strip()[-40:]))

    # ⛔ control — nothing goes out (the stub provider), so there is nothing to wait for. Without
    #    this row the gate could wait on every machine that never makes a request.
    buf, t0 = io.StringIO(), time.time()
    got = jobs._await_network(buf, "", budget=30)
    check("★control★: with no endpoint to call, it does not wait at all",
          got is True and (time.time() - t0) < 0.05 and buf.getvalue() == "")

    # ⛔ the host is ★asked of the module that will call it★ — hardcode it here or in jobs.py and
    #    switching provider leaves the gate watching a host we never talk to (green while blind).
    from brain import rerank, vectors                    # noqa: E402
    jobs_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "brain", "jobs.py"), encoding="utf-8").read()
    check("both callers can name their own endpoint host",
          bool(vectors.endpoint_host()) and bool(rerank.endpoint_host()),
          "%s · %s" % (vectors.endpoint_host(), rerank.endpoint_host()))
    check("★the job never spells a hostname itself★ — it asks",
          "googleapis" not in jobs_src and "endpoint_host()" in jobs_src)

    # ── ⑦ ★is the code we fixed the code that actually runs★ (2026-09-28) ──────────
    #
    #    Everything above tests `brain/jobs.py`. For 26 days that was ★not what the scheduler ran★.
    #    `brain/jobs.py` took the job over on 2026-09-02, but `bin/brain-vec-daily` kept its own
    #    inline heredoc copy and the registered program still pointed at the script. So two fixes
    #    written into the module — a non-zero exit on failure, and a grace period for a network that
    #    has not woken up — ★never executed once in production★, while this very check stayed green
    #    and the liveness axis reported 100 through six failed mornings.
    #    ⛔ Unit-testing a module cannot see this. The seam is "what does the scheduler start".
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    binder = os.path.join(root, "bin")
    scripts = sorted(f for f in os.listdir(binder) if not f.startswith("."))
    holds_logic = [f for f in scripts
                   if "<<'PY'" in open(os.path.join(binder, f), encoding="utf-8").read()]
    check("★no script under bin/ carries its own copy of a job★ (an inline heredoc is the tell)",
          not holds_logic, ", ".join(holds_logic) or "%d script(s) clean" % len(scripts))
    no_dispatch = [f for f in scripts
                   if "-m brain." not in open(os.path.join(binder, f), encoding="utf-8").read()]
    check("every script under bin/ ★hands off to a module★",
          not no_dispatch, ", ".join(no_dispatch) or "all %d dispatch" % len(scripts))
    for job in ("brain-vec-daily", "brain-rules-daily"):
        body = open(os.path.join(binder, job), encoding="utf-8").read()
        check("  %s runs ★brain.jobs★" % job, "-m brain.jobs" in body)

    # ⛔ ★control★ — the row above must be able to fail. A script that holds a body is planted in a
    #    temporary bin/ and the same rule is applied to it.
    import tempfile as _tf                                # noqa: E402
    with _tf.TemporaryDirectory() as tmpbin:
        planted = os.path.join(tmpbin, "brain-fake-daily")
        with open(planted, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\npython3 - <<" + "'PY'\nprint(1)\nPY\n")
        caught = "<<'PY'" in open(planted, encoding="utf-8").read()
        missing = "-m brain." not in open(planted, encoding="utf-8").read()
        check("★control★: both rules catch a script that was planted with a body", caught and missing)

    # The registered program, where it can be read — ★what production actually starts★.
    #    ⚠️ macOS only, and only once installed; absent, this says so instead of passing quietly.
    agents = os.path.expanduser("~/Library/LaunchAgents")
    seen = 0
    for job in ("vec-daily", "rules-daily"):
        plist = os.path.join(agents, "com.local-brain.%s.plist" % job)
        if not os.path.isfile(plist):
            continue
        seen += 1
        text = open(plist, encoding="utf-8").read()
        prog = re.search(r"<string>([^<]*brain-%s[^<]*)</string>" % job, text)
        leads = False
        if prog and os.path.isfile(prog.group(1)):
            leads = "-m brain.jobs" in open(prog.group(1), encoding="utf-8").read()
        elif "brain.jobs" in text:
            leads = True                                  # registered as `python -m brain.jobs`
        check("★the registered %s program leads to brain.jobs★" % job, leads,
              prog.group(1) if prog else "(no program path found)")
    if not seen:
        print("   ⚠️ no LaunchAgent installed here — ★the production seam was not measured★, "
              "only the scripts on disk")

    # ── ⑧ ★every run leaves its mark in the scheduler's own log★ (2026-09-30) ─────────
    #
    #    `schedules()` reads the age of the scheduler-only stdout log as its evidence that the
    #    scheduler launched the job. launchd only ★opens★ that file; its clock moves only on a write.
    #    After the thin wrappers of 09-28 the rules job wrote nothing there, and the verdict said
    #    *"the scheduler has not run it for 2.1 days"* while launchd said `runs = 23 · last exit 0`.
    #    So the job is run here ★the way launchd runs it★ — a fresh process, stdout and stderr
    #    appended to an existing file whose clock is old — and that clock must move.
    #    Calibrated against the code before the fix (09-30): the file stayed at its old clock, +0 bytes.
    import subprocess                                     # noqa: E402
    sched_tmp = tempfile.mkdtemp(prefix="brain-sched-log-")
    ld_file = os.path.join(sched_tmp, "x.launchd.log")
    with open(ld_file, "w", encoding="utf-8") as fh:
        fh.write("an older run\n")
    old = time.time() - 3 * 86400
    os.utime(ld_file, (old, old))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    snippet = ("import sys; from brain import jobs; "
               "sys.exit(jobs._run('BRAIN_X_LOG', 'x.log', lambda out: %s))")
    for label, body, want in (("a job that succeeds", "False", 0),
                              ("a job that fails", "True", jobs.FAILED)):
        os.utime(ld_file, (old, old))
        size0 = os.path.getsize(ld_file)
        with open(ld_file, "a", encoding="utf-8") as out:        # ★what launchd does★
            # ⛔ `cwd=sched_tmp` — `python -c` imports from the working directory first, so run from a
            #    repository it would test ★that★ copy, not `root` (it did, during calibration).
            p = subprocess.run([sys.executable, "-c", snippet % body], stdout=out, stderr=out,
                               cwd=sched_tmp, env=dict(os.environ, PYTHONPATH=root,
                                        BRAIN_X_LOG=os.path.join(sched_tmp, "x.log")))
        moved = os.path.getmtime(ld_file) > old + 86400
        text = open(ld_file, encoding="utf-8").read()[size0:]
        check("★%s moves the scheduler log's clock★" % label,
              p.returncode == want and moved and "start" in text,
              "exit %s · +%d bytes · %s" % (p.returncode, len(text), text.strip()[-40:]))
    # ⛔ and both jobs go through that one door — a job that bypassed `_run` would be silent again
    seen_run = []
    real_run = jobs._run
    jobs._run = lambda env_key, name, body: seen_run.append(name) or 0
    try:
        jobs.vec_daily()
        jobs.rules_daily()
    finally:
        jobs._run = real_run
    import shutil                                        # noqa: E402
    shutil.rmtree(sched_tmp, ignore_errors=True)
    check("both daily jobs go through `_run` (the one place that writes that mark)",
          seen_run == ["vec-daily.log", "rules-daily.log"], ", ".join(seen_run))

    # ── the scorecard reads this same verdict ────────────────────────────
    from brain import scorecard                           # noqa: E402
    check("the scorecard's `liveness` axis reads ★the same verdict★",
          "liveness" in dict((a[1], a) for a in scorecard.AXES),
          "axes: %s" % ", ".join(a[1] for a in scorecard.AXES))
    # ★the axis drops when a job fails★ — otherwise the row above guards a number nobody uses
    j_ok = {"jobs": [{"ok": True}, {"ok": True}]}
    j_bad = {"jobs": [{"ok": True}, {"ok": False}]}
    liveness = lambda r: sum(1 for x in r["jobs"] if x["ok"]) / float(len(r["jobs"]))
    check("★and the axis actually falls★ when one of two jobs fails",
          liveness(j_ok) == 1.0 and liveness(j_bad) == 0.5,
          "%.1f → %.1f" % (liveness(j_ok), liveness(j_bad)))

    for f in (log, ld_log, os.environ.pop("BRAIN_TEST_LOG", "")):
        try:
            os.remove(f)
        except OSError:
            pass
    os.rmdir(tmp)

    print("=" * 72)
    if FAIL:
        print("❌ %d failure(s): %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ all passed — ★this axis cannot be faked green by running it by hand★")
    return 0


if __name__ == "__main__":
    sys.exit(main())
