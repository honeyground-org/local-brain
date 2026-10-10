"""The daily self-improvement jobs — ★one platform-independent set★.

## Why this was moved to Python (2026-09-02)

These two jobs used to be ★heredoc Python★ inside `bin/brain-vec-daily`·`bin/brain-rules-daily`.
The shell printed the log header and fed Python through a pipe, so it ①couldn't run on Windows,
②a check couldn't call it (a heredoc can't be imported), ③it couldn't be an entry point for scheduling.

## ⛔ Change the log's ★first-line shape★ and the liveness verdict breaks

`health._START_RE` reads this line with a regex to decide ★did it fire after the scheduled time★:

    ── 2026-09-02 09:00:03 start

On 2026-09-01, that check was missing `re.M` and was reading only ★the first line★ — this actually happened.
★To change the shape, fix `health`'s regex and that check together — two places.★

## ⛔ A cron must ★fail cleanly★ — but cleanly is not ★silently★ (corrected 2026-09-22)

Dying with a traceback means nobody sees it the next day, so a failure is still written as ★one line★.
It used to also end with ★0★, and that part was wrong: `health.schedules()` decides "did it run and
succeed" from the ★exit code★, and it has a branch written for exactly this —
*"the scheduler called it" is not "the job succeeded"*. With every run exiting 0, that branch could
never fire. Two deliberate decisions cancelled each other out, and the result was measured on
2026-09-22: both jobs died on DNS two days running, `stale` grew 0 → 47, and the liveness axis
reported ★100★ the whole time while `brain status` said *"they all ran recently ✅"*.

So: one line in the log ★and★ a non-zero exit (`FAILED`). The log stays readable, and the axis that
was already looking for a failure can finally see one. ⚠️ Not everything that stops is a failure —
★the judge's daily budget wall is expected★ and still exits 0, or liveness would go red every day
the quota runs out.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional, TextIO


def _log_header(fh: TextIO) -> None:
    """⛔ ★this shape is paired with `health._START_RE`★ — change it and the liveness verdict breaks."""
    fh.write("-- %s start\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    fh.flush()


def _open_log(env_key: str, name: str) -> TextIO:
    from brain import store
    path = os.environ.get(env_key) or os.path.join(store.brain_home(), name)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "a", encoding="utf-8")


# ★"it ran and did not do its work"★ — the one thing a 0 could never say (§module docstring).
#   ⛔ The plist carries no `KeepAlive`, so a non-zero exit does ★not★ make launchd retry in a loop.
FAILED = 3

# ★How long to let the network arrive before calling it dead★ (seconds; 0 turns the wait off).
#   Sized for "the machine just woke", not for an outage: it is a grace period, not a retry policy.
NET_WAIT = float(os.environ.get("BRAIN_NET_WAIT", "180") or 180)


def _await_network(out: TextIO, host: str, budget: float = None) -> bool:
    """Wait, bounded, for `host` to resolve. True if it does (or if nothing goes out).

    ## Why (measured 2026-09-22)

    Both daily jobs died on `[Errno 8] nodename nor servname provided` — DNS, failing ★instantly★
    (`seconds: 0`), on five days out of fifteen. Two explanations were tested and ★both were wrong★:

      · *"it started late, so the network was not up"* — the fifteen runs show a failure at ★+0 min★
        and a success at ★+76 min★. Start time is not the axis.
      · *"launchd gives it a crippled environment"* — a probe run by launchd resolved the same host
        in ★4ms★ with 16 environment variables. The context is fine.

    What is left is ★when★: these jobs fire at a fixed hour, and a laptop that is asleep runs them
    the moment it wakes — while the interface is still coming up. Resolution then fails immediately
    rather than timing out, and `_post`'s own retry (one attempt, 0.6s later) is an order of
    magnitude too short for that. Hence a grace period here, before any work starts.

    ⛔ It costs a woken-up machine some seconds and a healthy one ★nothing★ — the first resolution
    on a working network returns in single-digit milliseconds, and this returns immediately after it.
    ⚠️ This is a ★prescription written from a diagnosis, not from a reproduction★. The cure cannot be
    confirmed until a run meets the real condition; the log line below is what will say whether it
    worked, so it is written on ★every★ wait, not only on failure.

    ## ⛔ The budget is total elapsed time — it was sleep time (measured 2026-09-29)

    The first real run of this gate logged `unresolved after 627s (7 attempts)` against a 180s budget.
    The sleeps added up to 60s; the other 567s were spent ★inside★ `getaddrinfo`, about 81s a call —
    the resolver was not failing fast that morning, it was hanging. The loop checked the budget only
    between attempts, so one blocking call could outrun it by any amount, and the check that was meant
    to catch this injected only ★instant★ failures, which is the shape a hang never takes. Now each
    lookup is bounded by what is left (§deadline.resolve), so the wall holds whichever shape comes.
    """
    from brain import deadline
    budget = NET_WAIT if budget is None else budget
    if not host or budget <= 0:
        return True
    t0 = time.monotonic()
    end = t0 + budget
    gap, tries = 1.0, 0
    while True:
        tries += 1
        try:
            deadline.resolve(host, 443, timeout=end - time.monotonic())
            if tries > 1:                                # ★silent when it was never needed★
                out.write("network arrived after %.1fs (%d attempts) — %s\n"
                          % (time.monotonic() - t0, tries, host))
            return True
        except OSError as exc:                           # TimeoutError is an OSError
            left = end - time.monotonic()
            if left <= gap:
                out.write("⛔ network never arrived — %s unresolved after %.0fs (%d attempts · last: %s)\n"
                          % (host, time.monotonic() - t0, tries, type(exc).__name__))
                return False
            time.sleep(gap)
            gap = min(gap * 2, 15.0)                     # 1·2·4·8·15·15… — quick at first, then patient


def _say(line: str) -> None:
    """One line on ★standard output★ — which is the scheduler's own log when the scheduler runs us.

    ⛔ ★Every run must write here, or the liveness verdict goes blind★ (measured 2026-09-30).
       `health.schedules` reads the age of the scheduler-only stdout log as ★the evidence that the
       scheduler launched the job★ — a human running it from a terminal never touches that file.
       But launchd only ★opens★ an existing file at launch; its clock moves only when something is
       ★written★. The old inline scripts printed a header, so the assumption held by accident. The
       thin wrappers of 09-28 made `rules` silent on stdout, and from then on the verdict said
       *"the scheduler has not run it for 2.1 days — that was a human"* while launchd itself
       reported `runs = 23 · last exit 0`. The liveness axis fell 100 → 50 on a job that was fine.
       `vec` stayed green only because it had work that day and printed progress; on an idle day it
       would have gone red the same way.
    """
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:                                    # noqa: BLE001
        pass                                             # a closed stdout must not stop the job


def _run(env_key: str, name: str, body) -> int:
    """Header → body → tail. ★Never a traceback★, but a failure ★does★ come back as `FAILED`.

    A body says "I failed" by returning something truthy, or by raising. Anything else is 0.
    The start and the result also go to stdout (§_say) — that is the scheduler's evidence.
    """
    from brain import hosts
    hosts.utf8_stdio()
    try:
        fh = _open_log(env_key, name)
    except OSError:
        fh = sys.stdout                                  # runs even if the log can't be opened
    failed = False
    try:
        _log_header(fh)
        if fh is not sys.stdout:
            _say("-- %s start %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), name))
        try:
            failed = bool(body(fh))
        except Exception as exc:                         # noqa: BLE001
            fh.write("⛔ stopped — %s: %s\n" % (type(exc).__name__, str(exc)[:200]))
            failed = True
        fh.write("-- end\n")
        fh.flush()
    finally:
        if fh is not sys.stdout:
            fh.close()
            _say("-- %s %s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), name,
                                  "FAILED (exit %d)" % FAILED if failed else "done"))
    return FAILED if failed else 0


# ── semantic search ingest ───────────────────────────────────────────────────
def _vec_body(out: TextIO) -> bool:
    from brain import store, vectors
    # ⛔ ★no engine chosen is a choice, not a failure★ — same rule as `embed:false` below: say it, exit clean
    if vectors.PROVIDER == "none":
        out.write(json.dumps({"skipped": "no embedding engine is chosen — `brain engines`"},
                             ensure_ascii=False) + "\n")
        return False
    if not _await_network(out, vectors.endpoint_host()):
        return True                                      # ★a failure, and it says so in the exit code★
    db = store.connect()
    # ★fills the control-query cache first★ — once this is filled, gate recalibration becomes
    # permanently free, and the moment loading crosses 90% it arms itself with no human hand. Uses only a few % of the daily share.
    warm = vectors.warm_control_cache(db)
    if warm["warmed"] or warm.get("stopped"):
        out.write(json.dumps({"warm": warm}, ensure_ascii=False) + "\n")
    before = vectors.coverage(db)
    failed = False
    try:
        st = vectors.build(db, progress=True)
    except Exception as exc:                             # noqa: BLE001
        st = {"chunks": 0, "docs": 0, "seconds": 0,
              "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
        out.write(json.dumps({"stopped": st["error"]}, ensure_ascii=False) + "\n")
        # ⛔ ★this is the line that used to vanish★ — written to the log and then exited 0, so the
        #    liveness axis kept calling a dead job green. `daily_quota_reached` below is ★not★ this:
        #    running out of the day's share is the plan working, not the job failing.
        failed = True
    after = vectors.coverage(db)
    out.write(json.dumps({"added_chunks": st["chunks"], "docs": st["docs"],
                          "daily_quota_reached": st.get("daily_quota_reached"),
                          "before": before, "after": after,
                          "seconds": st["seconds"]}, ensure_ascii=False) + "\n")
    if after["stale"] == 0:
        out.write("★fully complete★ — now set the threshold with `brain vec calibrate` and "
                  "measure quality with `tests/verify_vectors.py`.\n")
    return failed


def vec_daily(argv: Optional[list] = None) -> int:
    return _run("BRAIN_VEC_LOG", "vec-daily.log", _vec_body)


# ── behaviour-rule discovery + lexicon self-judgement ────────────────────────
def _rules_body(out: TextIO) -> bool:
    from brain import engines, rerank, ruledisc, store
    if engines.choice("judge")["provider"] == "none":
        out.write(json.dumps({"skipped": "no judge engine is chosen — `brain engines`"},
                             ensure_ascii=False) + "\n")
        return False                                     # a choice, not a failure
    if not _await_network(out, rerank.endpoint_host()):
        return True
    db = store.connect()
    limit = int(os.environ.get("BRAIN_RULES_DAILY_LIMIT", "300") or 300)
    try:
        r = ruledisc.discover(limit=limit, db=db)
    except Exception as exc:                             # noqa: BLE001
        out.write(json.dumps(
            {"stopped": "%s: %s" % (type(exc).__name__, str(exc)[:160])},
            ensure_ascii=False) + "\n")
        return True
    if r.get("local_only"):
        out.write(json.dumps(
            {"skipped": "the memory source is embed:false — no remote judging"},
            ensure_ascii=False) + "\n")
        return False                                     # a policy, not a failure
    bg = r.get("budget") or {}
    out.write(json.dumps({"candidates": r["candidates"], "judged": r["judged"],
                          "failed": r["failed"], "remaining": r["skipped"],
                          "stopped_early": r.get("stopped_early", False),
                          "proposals": len(r["proposals"]),
                          "budget": bg}, ensure_ascii=False) + "\n")

    # ★turns on with no human★ (user instruction 2026-08-26: "automatically, as it gets used")
    # Four gates: score 10 · not broad · doesn't contain an existing rule · not something a human turned off.
    auto = ruledisc.auto_approve(proposals=r["proposals"])
    if auto["added"]:
        out.write(json.dumps({"auto_approved": [a["signal"] for a in auto["added"]]},
                             ensure_ascii=False) + "\n")
        for a in auto["added"]:
            out.write("  ★turned on a rule★ %-26s %.0f pts · %d use(s) · %s\n"
                      % (a["signal"], a["top"], a["uses"], ", ".join(a["memories"][:2])))
        out.write("  ⛔ if it's noise, `brain rules --disable auto-<signal>` — once disabled, never re-enables itself.\n")

    # ⛔ ★stopping early is not one thing★ — the day's judge budget running out is the design
    #    working (it lifts at Pacific midnight), while a network error is a job that did not do its
    #    work. Only the second one may colour the liveness axis, or the axis goes red every busy day.
    failed = bool(r.get("stopped_early")) and not bool(bg.get("wall"))
    if r.get("stopped_early"):
        # ⛔ ★"likely" is not a diagnosis★ — the reason is now known (§rerank.budget).
        out.write("⛔ stopped — reason: %s\n" % (r.get("stop_reason") or "(assumed network)"))
        out.write("   daily budget %d/%d · %d left%s\n"
                  % (bg.get("used", 0), bg.get("limit", 0), bg.get("left", 0),
                     " ⛔wall" if bg.get("wall") else ""))
        out.write("   continues tomorrow (verdicts are cached).\n")
    elif r["skipped"] == 0:
        out.write("★all candidates judged★ — see remaining proposals with `brain rules --discover`, "
                  "and turn one on directly with `--approve '<signal>'` if needed.\n")
    else:
        out.write("%d candidates remain — continues tomorrow.\n" % r["skipped"])

    # ★an unused rule is only reported★ — never disabled. It never fires, so it does no harm, and next month
    # it may become relevant to that repo again. Auto-deleting it means nobody ever knows the guidance vanished.
    cold = [x["id"] for x in ruledisc.stale()["rules"] if x["all_cold"]]
    if cold:
        out.write("⚠️ rules whose only behaviour never fired in this window: %s\n" % ", ".join(cold))

    # ── ★layers that calibrate themselves★ (user instruction 2026-08-27) ─────────────
    # ⛔ Before this existed, this repo had ★two standards that were only ever written down and never run★.
    #    ★A standard that never runs is not a standard.★ So the scheduled job calls it every day.
    try:
        from brain import lexicon
        r2 = lexicon.decide(db)                # ★local only★ — spends no judge budget
        out.write("lexicon self-decision — %s · %s\n" % (r2["decision"], r2.get("why", "")))
    except Exception as exc:                             # noqa: BLE001
        out.write("lexicon self-decision failed (recall keeps running): %s\n" % str(exc)[:120])

    # ★behaviour-layer firing-rate table★ — guard's session budget reads it. guard is a hook that runs
    # on every tool call, so it can't measure this itself. The scheduled job measures and writes it.
    # ⛔ Without this file, guard applies no cap — ★never blocking on the unknown★.
    try:
        r3 = ruledisc.measure_active()
        rare = sum(1 for v in r3["rates"].values() if v < 1.0)
        out.write("behaviour-layer firing rate updated — %d rules · %d rare (under 1%%) → %s\n"
                  % (r3["rules"], rare, r3["path"]))
    except Exception as exc:                             # noqa: BLE001
        out.write("firing-rate update failed (only the budget goes uncapped, recall still runs): %s\n" % str(exc)[:120])

    # ⚠️ The two side layers above are ★reported, not exit-coded★ — each says in its own line that
    #    recall keeps working without it. Colouring the axis for those would hide the real failures.
    return failed


def rules_daily(argv: Optional[list] = None) -> int:
    return _run("BRAIN_RULES_LOG", "rules-daily.log", _rules_body)


# ⛔ ★An unknown word must not quietly run something★ — this used to be
#    `rules_daily() if argv[1] == "rules" else vec_daily()`, so a missing or misspelled
#    subcommand silently ran the ingest. `brain/tail.py` had the mirror image of this bug
#    (a missing subcommand mapped to `lambda: 0`, i.e. the session hook did nothing at all).
#    Both shapes are the same mistake: a dispatch whose default is a guess.
_JOBS = {"vec": vec_daily, "rules": rules_daily}

if __name__ == "__main__":
    _which = sys.argv[1] if len(sys.argv) > 1 else ""
    if _which not in _JOBS:
        sys.stderr.write("usage: python3 -m brain.jobs {%s}\n"
                         % "|".join(sorted(_JOBS)))
        sys.exit(2)
    sys.exit(_JOBS[_which]())
