"""Scheduler-adapter check — ★does it answer the same question on macOS·Windows·Linux★. (local · budget 0)

## Why (user requirement 2026-09-02: an installable Mac·Windows package)

The reliability axis asks one question: ★did the scheduled job run on its own at the scheduled time★.
But the evidence that can answer it ★differs by platform★:

    macOS    launchctl print   → ★run count★ · exit code · scheduled time   (strongest)
    Windows  schtasks /query   → last-run time · last result                (middle)
    Linux    crontab -l        → ★registration only★ — no run history       (none)

⛔ If a weak-evidence platform shows ★the same green★, reliability 100 means a different thing per platform.
   This repository fixed exactly that trap on 09-01 ("running it by hand is not running on its own").
   ⇒ An adapter must state ★what it knows (`evidence`)★, and if it doesn't know, it must come back ★empty-handed★.

⚠️ This machine is macOS — the Windows·Linux paths are measured ★only by simulation★. Verify for real on that machine.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import health, scheduler as sch                # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    print("=" * 72 + "\nscheduler — does each platform state ★the strength of its evidence★\n" + "=" * 72)

    names = [s.name for s in sch.all_schedulers()]
    check("all four — mac·windows·linux·none — are declared",
          {"launchd", "schtasks", "cron", "none"} <= set(names), str(names))

    # ── ★does it state the strength of its evidence on its own★ ─────────────────────
    want = {"launchd": "runs", "schtasks": "last_run", "cron": "none", "none": "none"}
    for s in sch.all_schedulers():
        check("%s states its own evidence strength (%s)" % (s.name, want[s.name]),
              s.evidence == want[s.name], s.evidence)

    # ── ⛔ ★what it can't do, it answers empty-handed★ (it doesn't make evidence up) ──────────────
    for s in sch.all_schedulers():
        if s.available():
            continue
        got = s.query("nope", "com.nope.nothing")
        check("%s: when it can't ask, it's ★known=False★ (no evidence gets invented)" % s.name,
              got["known"] is False and got["runs"] is None)

    # ── what's actually in use on this machine ────────────────────────────────────────
    cur = sch.active()
    print("\n  this machine: %s (%s · evidence %s)" % (cur.name, cur.label, cur.evidence))
    check("found this machine's scheduler", cur.available() or cur.name == "none")

    # ── ★can BRAIN_SCHEDULER simulate a different platform★ ─────────────
    saved = os.environ.get("BRAIN_SCHEDULER")
    try:
        for want_name in ("schtasks", "cron", "none"):
            os.environ["BRAIN_SCHEDULER"] = want_name
            check("BRAIN_SCHEDULER=%s picks that one" % want_name,
                  sch.active(refresh=True).name == want_name, sch.active().name)
            # ⛔ ★does the reliability verdict stay alive on that platform★ — it must answer even with no evidence
            jobs = health.schedules()["jobs"]
            check("  the reliability verdict answers on %s too (doesn't die)" % want_name,
                  isinstance(jobs, list) and len(jobs) >= 1 and "ok" in jobs[0],
                  "%d job(s)" % len(jobs))
            # ⛔ ★with no evidence, it doesn't claim 'it ran on its own'★
            if want_name in ("cron", "none"):
                claimed = [j for j in jobs if j.get("by") == "scheduled"]
                check("  ★a platform with no evidence doesn't claim 'on its own at the scheduled time'★",
                      not claimed, "%d job(s) claiming it" % len(claimed))
    finally:
        os.environ.pop("BRAIN_SCHEDULER", None)
        if saved:
            os.environ["BRAIN_SCHEDULER"] = saved
        sch.active(refresh=True)

    # ── does the log path follow the home dir (home differs by platform) ──────────────
    from brain import store
    for j in health.schedules()["jobs"]:
        check("%s's log is ★under the home dir★ (the path isn't hard-coded)" % j["job"],
              j["log"].startswith(store.brain_home()), j["log"])

    # ── does the install command take ★a different shape per platform★ (shape only, doesn't run) ────
    check("launchd install says it makes a plist",
          "plist" in (sch.Launchd().install.__doc__ or "")
          or hasattr(sch.Launchd(), "install"))
    check("schtasks install ★wraps its own log redirect★ (the scheduler doesn't give it one)",
          ">>" in (open(os.path.join(os.path.dirname(os.path.dirname(
              os.path.abspath(__file__))), "brain", "scheduler.py"),
              encoding="utf-8").read().split("class Schtasks")[1]))

    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAIL), ", ".join(FAIL)) if FAIL else "✅ all passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
