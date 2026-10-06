"""★Is the guard still alive on someone else's machine★ — a clean-room-style check. (local · budget 0)

## Why this check exists (measured 2026-09-02)

The threshold-regression guard was built on 2026-09-01. That guard compares the new threshold against
the old one using a fixed sample (`tests/eval/short.json`). But that sample
★is personal data, so `.gitignore` excludes it from distribution★ — the right call.

So on someone else's machine, this happens:

    ei.load_A() → []  ·  ei.load_C() → []
    calibrate._bench(...) → ★{}★
    `if b:`'s body never runs at all → ★whatever the new threshold is, it just deploys★ · 0 warnings

What got caught on this machine on 09-01 is exactly that accident (9.45 → 13.21 · automaticity 0.284 → 0.128).
★On someone else's machine, it happens with no brake at all.★

⛔ This is the worst form of the "probe that cannot fail" this repository keeps running into —
   ★worse than being unprotected is not knowing you are unprotected.★

So this check ★hides the sample★ and measures whether the whole path speaks up. It never touches real data.

## ★2026-09-09 — the answer changed, so this check changed with it★

Saying "you are unprotected" was the ★first★ step. Now a machine with no labels builds a ★proxy
sample★ from its own documents (§brain/proxy.py) and the guard actually works there — in one
direction, the one it was measured to get right (a candidate ★above★ the incumbent, which is the
direction that silences the hook). So the rows below no longer demand the words "not protected";
they demand ★the truth★, which is now three-valued:

    labelled → this person's own gold          full guard
    proxy    → built from their corpus, 0 labels   upward only, and it ★says★ so
    none     → corpus too small for either      unprotected, and it says that

⛔ The one thing that must never happen is a machine ★claiming a protection it does not have★. That
   is what every row here is really testing, and it is why the abstain path still writes the trace.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import calibrate as cal, evalinit as ei, health, store   # noqa: E402
from tests import _needs  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    print("=" * 72 + "\nimpersonating someone else's machine — does the guard ★speak up★ with no sample\n" + "=" * 72)
    db = store.connect()
    _needs.proxy_sample(db)
    _needs.prompts(30)

    # ── first, this machine must already be protected (control group) ──────────────────
    here = cal.guard_status(db)
    check("this machine's guard is on (control group — if this is false, the check itself can't be trusted)",
          here.get("protected") is True, "answer %s · control %s" % (here.get("a"), here.get("c")))

    # ── hides the sample — ⛔ never touches the real file, only points EVAL_DIR at an empty spot ──────
    tmp = tempfile.mkdtemp()
    real_dir = ei.EVAL_DIR
    saved_thr = store.get_meta(db, "hook_threshold", "")
    saved_ung = store.get_meta(db, "hook_threshold_unguarded", "")
    # ⛔ ★Every meta key this check can write has to come back★ — an earlier version restored the
    #    threshold but not the "rejected" record, and left a calibration refusal on the real DB that
    #    `brain budget` then showed as if it had really happened. A check that dirties the thing it
    #    measures is the same disease as a worktree poisoning the code stamp (2026-09-09).
    saved_blk = store.get_meta(db, "hook_threshold_blocked", "")
    saved_det = store.get_meta(db, "hook_threshold_detail", "")
    try:
        ei.EVAL_DIR = tmp
        g = cal.guard_status(db)
        check("with no labelled sample it falls back to a ★proxy★ — and names it as one",
              g.get("kind") == "proxy", "kind=%s · protected=%s" % (g.get("kind"), g.get("protected")))
        check("⛔ it never passes a proxy off as labelled gold",
              g.get("kind") != "labelled")
        check("the reason is written in human-readable form, and says `eval-init` is still the full answer",
              "eval-init" in (g.get("why") or "") and len(g.get("why") or "") > 40,
              (g.get("why") or "")[:58])

        pairs, ctrl, kind = cal.bench_sample(db)
        check("the proxy sample is big enough to judge with",
              kind == "proxy" and len(pairs) >= 30 and len(ctrl) >= 20,
              "%s · %d pairs · %d controls" % (kind, len(pairs), len(ctrl)))
        b = cal._bench(db, [9.45, 13.0], pairs, ctrl)
        check("★the guard can now actually measure here★ (it used to come back empty)",
              bool(b) and 9.45 in b and 13.0 in b,
              "auto %.3f vs %.3f" % (b.get(9.45, {}).get("auto", -1), b.get(13.0, {}).get("auto", -1)) if b else "{}")
        check("…and on this sample the ★higher★ candidate is the worse one (the direction it guards)",
              bool(b) and b[13.0]["auto"] <= b[9.45]["auto"])

        st = health.status(db)
        cg = st.get("calibration_guard") or {}
        check("`brain status` carries the proxy fact (the status screen must not stay quiet)",
              cg.get("kind") == "proxy", "calibration_guard.kind=%s" % cg.get("kind"))

        # ★downward — the proxy is measured to be wrong here, so it must abstain AND say so★
        store.set_meta(db, "hook_threshold", "9.45")
        r = cal.calibrate(db)
        check("a ★downward★ move is not judged by the proxy (measured 0/3 there)",
              r.get("guard_sample") == "proxy" and not r.get("not_deployed"),
              "sample=%s" % r.get("guard_sample"))
        check("★abstaining still leaves the trace★ (an unjudged move must not look like an approved one)",
              r.get("unguarded") is True, "unguarded=%s" % r.get("unguarded"))
        rec = cal.unguarded(db)
        check("that trace has ★what got deployed★ in it",
              rec.get("deployed") is not None and bool(rec.get("why")),
              "%s → %s" % (rec.get("from"), rec.get("deployed")))

        # ★upward, from a healthy incumbent — this is the protection, and it has to actually bite★
        # ⛔ The incumbent has to sit ★inside the healthy firing band★. An earlier version used 1.60,
        #    where the hook fires on nearly every prompt; the proxy stands down there on purpose
        #    (raising is obviously right) and the row was testing the abstention, not the guard.
        #    So this reproduces the recorded incident instead: a healthy ruler shoved upward.
        m0 = cal.measure(db)
        tops = m0.get("_real_tops") or []
        inc = float(store.get_meta(db, "hook_threshold", "") or m0["threshold"])
        saved_measure = cal.measure
        try:
            cal.measure = lambda _db: {"threshold": round(inc * 1.4, 2), "noise_floor": inc,
                                       "docs": len(tops), "margin": cal.MARGIN,
                                       "_real_tops": list(tops)}
            store.set_meta(db, "hook_threshold", "%.2f" % inc)
            r2 = cal.calibrate(db)
        finally:
            cal.measure = saved_measure
        check("an ★upward★ move from a healthy ruler IS judged and rolled back (no labels anywhere)",
              r2.get("guard_sample") == "proxy" and bool(r2.get("not_deployed")),
              "incumbent %.2f (firing %s%%) · kept %s" % (inc, cal.fire_rate_at(tops, inc), r2.get("threshold")))

        # ★with no corpus either, the old truth must still be told★
        _pa, _pc, kind0 = cal.bench_sample(store.connect())
        check("(control) the three-valued answer exists at all — labelled · proxy · none",
              kind0 in ("labelled", "proxy", "none"), "kind=%s" % kind0)
    finally:
        ei.EVAL_DIR = real_dir
        store.set_meta(db, "hook_threshold", saved_thr)
        store.set_meta(db, "hook_threshold_unguarded", saved_ung)
        store.set_meta(db, "hook_threshold_blocked", saved_blk)
        store.set_meta(db, "hook_threshold_detail", saved_det)
        shutil.rmtree(tmp, ignore_errors=True)

    # ── once the sample comes back, the alarm must clear (it doesn't stay stuck) ────────────
    back = cal.guard_status(db)
    check("once the sample comes back, ★the alarm clears★ (once on, it doesn't stay on forever)",
          back.get("protected") is True)
    check("the real data is back to normal (the check leaves no trace on the threshold)",
          store.get_meta(db, "hook_threshold", "") == saved_thr,
          "threshold %s" % store.get_meta(db, "hook_threshold", ""))
    check("★and no trace anywhere else either★ — no invented refusal is left on the real DB",
          store.get_meta(db, "hook_threshold_blocked", "") == saved_blk
          and store.get_meta(db, "hook_threshold_detail", "") == saved_det)

    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAIL), ", ".join(FAIL)) if FAIL else "✅ all passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
