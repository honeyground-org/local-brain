"""★The scorecard★ — gathers scattered axes into one number so they can be compared.

## Why this is needed (user instruction, 2026-08-31)

*"Turn the evaluation criteria into scores so they can be compared. Decide which
categories matter, measure each one, and make the comparison possible."*

Until then the axes were scattered, and ★picking a favourable opponent per axis★ kept
happening: the benefit was measured with stage 2 on and the cost with it off; hit rate
was compared against weak grep and read volume against strong grep. ★One table makes that impossible.★

## Categories — derived from the goal, not from taste

The goal is *"a brain that is useful and efficient, better than the old way (an index
file plus grep), and that maintains itself without human hands"*. Its words are the axes.

| Axis | Which word of the goal | What it measures |
|---|---|---|
| Recall | "useful" | Does the right answer land in the top 3 when asked |
| Precision | "useful" | ★Does it say "nothing" when there is nothing★ |
| Efficiency | "efficient" | How many documents must be read per answer |
| Automatic | "better than the old way" | Does ★the right thing★ surface unasked, and is it ★quiet★ when not needed (grep cannot do this) |
| Stability | "useful" | Same input, same decision |
| Autonomy | "maintains itself" | Do the thresholds set themselves without a human |
| Liveness | "maintains itself" | Do scheduled jobs fire ★on their own, on time★ (⛔ a manual run does not count) |

⛔ ★The weights are written here and not changed.★ Adjusting weights after seeing the
   result is exactly "picking the favourable number". If they must change, put ★why★ in the commit.

⛔ ★An unmeasured axis is `None`, not 0★. Counting it as 0 makes "not measured" into a
   failure; counting it as full marks makes it a success. Both are lies.
"""
from __future__ import annotations

from brain import evalinit as _ei  # ★gold judgement lives in one place only★
from brain import i18n

# ★Declared for the key scanner★ — `sc.note.<bucket>` is built at runtime below (§render), invisible to a scanner
# that reads `t("...")`. Same declaration shape as dashview's UI_PREFIX.
I18N_PREFIX = ("sc.note.",)

import json
import os
import time
from typing import Dict, List, Optional, Tuple

# ★Axis definition version★ — ⛔ change the ruler and ★earlier records cannot be compared directly★.
#   On each bump, note here in one line what changed, and put ★why★ in the commit message.
#     1 (~2026-08-31)  automatic = share of hook firings that surfaced ★the right thing★ (before: firing rate)
#     2 (2026-09-01~)  automatic = the above × ★share of quiet when no memory was needed★
#                       — closed the half-count that gave no credit for reducing noise
AXIS_VERSION = 2

# ★Axes and weights★ — derived from the goal (table above). They sum to 100.
AXES = (
    ("Recall", "recall", 25, "Does the right answer land in the top 3 when asked"),
    ("Precision", "precision", 25, "Does it say nothing when there is nothing (unlabelled control)"),
    ("Efficiency", "efficiency", 15, "Documents to read per answer (versus grep)"),
    ("Automatic", "automatic", 15, "Does ★the right thing★ surface unasked, and stay quiet otherwise"),
    ("Stability", "stability", 10, "Same input, same decision"),
    ("Autonomy", "autonomy", 5, "Do the thresholds set themselves without a human"),
    ("Liveness", "liveness", 5, "Do scheduled jobs fire ★on their own, on time★"),
)

# ★Target line★ — pass this and the axis scores 100. Set from the goal, never adjusted afterwards.
TARGET = {
    "recall": 0.80,        # right answer in the top 3, 80%
    "precision": 0.95,     # false firing on the unlabelled control at or under 5% → 100
    "efficiency": 1.0,     # full marks at 1/50 of grep's read volume (see below)
    "automatic": 0.50,     # automatic recall 50%
    "stability": 0.90,     # share of inputs whose decision does not split across 5 runs
    "autonomy": 1.0,       # share of self-setting thresholds that actually got set
    "liveness": 1.0,       # share of scheduled jobs that ran ★on their own, on time★
}


def _query_words(q: str) -> set:
    """The words a person would grep for — ★the search's own word rule★ (§textindex.words).
    ⛔ It used to be a Korean/Latin-only pattern: a Japanese query had no words at all, `Prüfung` split in two,
    and a trailing `.` or a Korean particle stuck to a Latin word made the grep baseline weaker than a
    person typing the word would be."""
    from brain import textindex
    return {w for w in textindex.words(q) if len(w) >= 2}


def _short_json() -> str:
    """This person's labelled short-query sample — wherever their eval folder is (§evalinit.eval_dir).
    ⛔ It used to be `<package parent>/tests/eval/short.json`, which an installed copy does not have."""
    return os.path.join(_ei.eval_dir(), "short.json")


def _pct(v: Optional[float], target: float) -> Optional[float]:
    """0–1 against the target line, as 0–100. ⛔ None passes through as None (unmeasured)."""
    if v is None:
        return None
    return round(min(100.0, 100.0 * v / target), 1)


def compute(raw: Dict[str, Optional[float]]) -> dict:
    """raw = per-axis 0–1 values (None where unmeasured). → axis scores + total.

    The total is ★a weighted average over the measured axes★ — an unmeasured axis leaves the
    denominator too, so that "not measuring something neither raises nor lowers the score".
    """
    rows, num, den = [], 0.0, 0.0
    extra = {k: v for k, v in raw.items() if k.startswith("_")}
    for _label, key, weight, _what in AXES:
        # ★reuses the dashboard's own axis./axis..what catalog keys★ — a name in two places
        # (this file's own English label and dashview's translated one) gets fixed in one (2026-09-08).
        score = _pct(raw.get(key), TARGET[key])
        rows.append({"axis": i18n.t("axis." + key), "key": key, "weight": weight, "score": score,
                     "raw": raw.get(key), "what": i18n.t("axis." + key + ".what")})
        if score is not None:
            num += score * weight
            den += weight
    total = round(num / den, 1) if den else None
    return {"axes": rows, "total": total, "extra": extra,
            "measured_weight": den, "unmeasured": [r["axis"] for r in rows
                                                   if r["score"] is None]}


def render(sc: dict, title: str = "") -> str:
    out = []
    if title:
        out.append(title)
    out.append("%-10s %4s  %-6s  %-40s" % (i18n.t("sc.col.axis"), i18n.t("sc.col.weight"),
                                            i18n.t("sc.col.score"), i18n.t("sc.col.what")))
    out.append("-" * 74)
    for r in sc["axes"]:
        s = "  —  " if r["score"] is None else "%5.1f" % r["score"]
        raw = "" if r["raw"] is None else "  (raw %.3f)" % r["raw"]
        out.append("%-8s %3d%%  %s  %-40s%s" % (r["axis"], r["weight"], s, r["what"], raw))
    out.append("-" * 74)
    if sc["total"] is None:
        out.append(i18n.t("sc.total_none"))
    else:
        out.append(i18n.t("sc.total", total="%.1f" % sc["total"], mw=sc["measured_weight"],
                           unmeasured=i18n.t("sc.total_unmeasured_suffix", names=", ".join(sc["unmeasured"]))
                           if sc["unmeasured"] else ""))
    ex = sc.get("extra") or {}
    # ⛔ ★Break the big number down★ — automatic 0.324 alone does not say whether hits are low or noise is high.
    if ex.get("_auto_hit") is not None and ex.get("_auto_false_fire") is not None:
        out.append(i18n.t("sc.auto_breakdown", hit="%.1f" % (100 * ex["_auto_hit"]),
                           quiet="%.1f" % (100 * (1 - ex["_auto_false_fire"])),
                           n=ex.get("_auto_control_n") or 0,
                           ff="%.0f" % round(ex["_auto_false_fire"] * (ex.get("_auto_control_n") or 0))))
    if ex.get("_auto_full_hit") is not None:
        out.append(i18n.t("sc.auto_full", hit="%.1f" % (100 * ex["_auto_full_hit"]),
                           quiet="%.1f" % (100 * (1 - ex["_auto_full_false_fire"]))))
        if ex.get("_auto_word_only") is not None:
            out.append(i18n.t("sc.auto_word_only", v="%.3f" % ex["_auto_word_only"]))
    if ex.get("_auto_full_error"):
        out.append(i18n.t("sc.auto_full_error", err=ex["_auto_full_error"]))
    if ex.get("_efficiency_ratio"):
        out.append(i18n.t("sc.efficiency_note", docs="%.0f" % ex["_grep_median_docs"],
                           ratio="%.0f" % ex["_efficiency_ratio"]))
    out.append(i18n.t("sc.unmeasured_warning"))
    return "\n".join(out)


def _store_home() -> str:
    """⛔ The home is decided by §store.brain_home — no path is hardcoded here."""
    from brain import store as _s
    return _s.brain_home()


def save(sc: dict, note: str = "", path: str = "") -> str:
    """★Kept so that points in time can be compared★ — that is the whole reason this table exists."""
    import time
    path = path or os.path.join(_store_home(), "scorecard.jsonl")
    ex = sc.get("extra") or {}
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note,
           # ⛔ ★Stamp the ruler version into the record★ — without it, a definition change reads as a performance change
           "axis_version": AXIS_VERSION,
           # ⛔ ★Stamp which pipeline measured it, too.★ Automatic is measured on the deployed
           #    pipeline (stage 2 when lexical is silent) under `--full`, and on ★the lexical path only★ by default.
           #    Without the stamp both values share one column and ★cherry-picking becomes possible again★.
           "pipeline": "deployed" if ex.get("_auto_full_hit") is not None else "word",
           "total": sc["total"],
           "axes": {r["key"]: r["score"] for r in sc["axes"]},
           "raw": {r["key"]: r["raw"] for r in sc["axes"]}}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def history(path: str = "", limit: int = 10) -> List[dict]:
    path = path or os.path.join(_store_home(), "scorecard.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        return []
    return rows[-limit:]


# ★Axes that cost remote calls★ — `collect(cheap=True)` leaves these empty (§collect).
REMOTE_AXES = ("precision", "stability")

# ⛔ ★How long a carried-over measurement may still be shown★ — grounded in the drift this
#    project actually measured, not in taste: precision read 91.7 on 2026-09-22 and 87.0 on
#    2026-09-28. Six days moved it 4.7 points, so a value older than about a week is not a
#    description of today. Past that it goes back to "not measured", which is the honest state.
CARRY_MAX_DAYS = float(os.environ.get("BRAIN_SCORE_CARRY_DAYS", "7") or 7)


def carry_remote(raw: Dict[str, Optional[float]],
                 now: Optional[float] = None) -> Tuple[Dict[str, Optional[float]], List[str], str]:
    """Fill the remote axes from the most recent ★full★ measurement. → (raw, carried keys, when).

    ## Why this exists (2026-09-28)

    Opening the dashboard must not spend the judge's daily budget, so those two axes come back
    empty and the screen said *"not measured"* — while a full measurement from that same morning
    sat in `scorecard.jsonl`. The total was 65.9 on screen against 73.5 actually measured, and the
    two numbers disagreeing is the fastest way to make a screen untrustworthy.

    ⛔ ★A carried value is never presented as a fresh one.★ The caller gets the keys and the
    timestamp back precisely so the screen can say where the number came from; a total that
    quietly folds in last week's figure is worse than an honest gap.

    ⛔ ★Only fills what is empty★ — a value measured in this pass always wins.
    ⚠️ Lives here, not in a renderer: the classic screen grew its own copy of this logic first,
    and "the same rule in two places" is the failure this repository keeps meeting.
    """
    carried: List[str] = []
    missing = [k for k in REMOTE_AXES if raw.get(k) is None]
    if not missing:
        return raw, carried, ""
    now = time.time() if now is None else now
    for row in reversed(history(limit=60)):
        src = row.get("raw") or {}
        have = [k for k in missing if src.get(k) is not None]
        if not have:
            continue
        age = _age_days(row.get("at", ""), now)
        if age is None or age > CARRY_MAX_DAYS:
            break                                        # ★the newest one is already too old★
        for k in have:
            raw[k] = src[k]
            carried.append(k)
        return raw, carried, row.get("at", "")
    return raw, carried, ""


def _age_days(stamp: str, now: float) -> Optional[float]:
    """`2026-09-28T09:49:59` → days old. None when it cannot be read (then nothing is carried)."""
    try:
        t = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None
    return (now - t) / 86400.0


# ── ★Actually collect the values★ ───────────────────────────────────────────
def collect(db, cheap: bool = True) -> Dict[str, Optional[float]]:
    """Collect per-axis raw values (0–1). ⛔ If it cannot be measured, ★None★ — never filled with 0.

    `cheap=True` ★skips the axes that cost remote calls★ (precision, stability). Those need the
    judge, which has a daily limit, so calling them every time eats interactive recall's share.
    A skipped axis stays `None` and leaves the total's denominator as well.
    """
    import json as _j
    from brain import calibrate as _cal, health, hook, lexicon, rerank, search, store
    out: Dict[str, Optional[float]] = {k: None for _, k, _, _ in AXES}

    # ── Recall · automatic — local (lexical path + hook) ──────────────────
    path = _short_json()
    A = []
    C = []
    try:
        with open(path, encoding="utf-8") as fh:
            ev = _j.load(fh)
        A = [(r["q"], r["gold"]) for r in ev.get("A_memory_needed", [])
             if isinstance(r, dict) and r.get("gold")]
        C = _ei.load_C(path)          # ★loading lives in one canonical place★ (evalinit)
    except (OSError, ValueError):
        A = []
        C = []
    if A:
        thr = _cal.threshold(db)
        top3 = fired = 0
        reads = []
        for q, gold in A:
            rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
            names = [r["name"] for r in rows if not r.get("related")]
            if any(_ei.gold_hit(gold, n) for n in names[:3]):
                top3 += 1
            keep = hook.select(rows, thr)
            if keep:
                reads.append(len(keep))
                # ⛔⛔ ★Measuring by firing rate lets noise raise the score★ — a trap this
                #    repository warns about repeatedly (last line of `verify_short`), and the
                #    first version fell straight into it.
                if any(_ei.gold_hit(gold, r["name"]) for r in keep):
                    fired += 1
        out["recall"] = top3 / float(len(A))
        # ── Automatic ─ ★count both directions★ (corrected 2026-09-01 · axis_version 2)
        #
        # ⛔ Version 1 was ★the firing rate★ → noise raised the score (fixed 08-31).
        # ⛔ Version 2 counted ★hits only★ → this time ★reducing noise earned no credit.★
        #    Measured (09-01): the threshold moved itself 8.5 → 9.45, hits went 20→19 (−1) and
        #    false firings 5→1 (−4). An obvious improvement, and the axis recorded it as −3.
        #    ★That is two rulers in a row that blocked only one direction — now both are counted.★
        #
        # automatic = (share that surfaced the right thing) × (share that stayed quiet when no memory was needed)
        #   · both are in [0,1], so the scale is preserved (0.333 → 0.324 at the current ruler; history stays readable)
        #   · zero on either side is zero — neither "always fires" nor "never fires" is automatic
        #   · ⛔ chosen after confirming the ranking agrees with an independent ruler, ★net gain (hits − false firings)★
        #     (across 9 thresholds swept, both rulers put 9.45 first and 7.50 second)
        false_fire = 0
        for q in C:
            if hook.select(search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False), thr):
                false_fire += 1
        hit_rate = fired / float(len(A))
        spec = (1.0 - false_fire / float(len(C))) if C else None
        out["_auto_hit"] = hit_rate
        out["_auto_false_fire"] = (false_fire / float(len(C))) if C else None
        out["_auto_control_n"] = float(len(C))
        # ⛔ With no control, ★do not measure half of it — leave it unmeasured★. That trap was
        #    stepped on again today (a wrong key name loaded 0 controls and the table reported "false firing 0.0%").
        out["automatic"] = (hit_rate * spec) if spec is not None else None
        # ── Efficiency — ★against how many documents grep OR must read for the same query★
        # The brain reads the top 3 (the hook reads 2). grep OR must read ★every★ match.
        gsz = []
        docs = [(r["name"], ((r["title"] or "") + " " + (r["body"] or "")).lower())
                for r in db.execute(
                    "SELECT name, title, body FROM docs WHERE source='memory'")]
        for q, _g in A:
            ws = _query_words(q)
            gsz.append(sum(1 for _n, blob in docs if any(w in blob for w in ws)))
        gsz.sort()
        med = gsz[len(gsz) // 2] if gsz else 0
        # ★Full marks at 50× less reading★ — the goal's word "efficient", turned into a number.
        # ⛔ The ratio is recorded separately — a score of 100 must not hide the real multiple.
        out["_efficiency_ratio"] = (med / 3.0) if med else None
        out["_grep_median_docs"] = float(med)
        out["efficiency"] = min(1.0, (med / 3.0) / 50.0) if med else None

    # ── Autonomy — of the three self-setting thresholds, how many actually got set ────
    tuned = 0
    total = 3
    # ⛔ ★A calibration that was refused is not "it set itself"★ (2026-09-01) — the rule that
    #    already existed for the judge threshold now applies to the lexical one too. Today the
    #    lexical ruler chose 12.15 for itself (automatic 0.324 → 0.175) and the guard refused it.
    #    The guard doing its job is good, but ★that round the ruler did not set itself★.
    if store.get_meta(db, "hook_threshold", "") and not store.get_meta(db, "hook_threshold_blocked", ""):
        tuned += 1                                   # lexical threshold: automatic on every index
    if not store.get_meta(db, "rerank_calibration_blocked", ""):
        tuned += 1                                   # judge threshold: was it blocked?
    if store.get_meta(db, "lexicon_decided_at", ""):
        tuned += 1                                   # lexicon layer: did it decide for itself?
    out["autonomy"] = tuned / float(total)

    # ── Liveness — did the scheduled jobs run recently ────────────────────
    try:
        sch = health.schedules()
        jobs = sch["jobs"]
        out["liveness"] = sum(1 for j in jobs if j["ok"]) / float(len(jobs) or 1)
    except Exception:                                # noqa: BLE001
        out["liveness"] = None

    if cheap:
        return out                                   # precision and stability are remote — skipped

    # ── Automatic ★re-measured — exactly as deployed★ (added 2026-09-01) ───
    #
    # ⛔⛔ The automatic figure above measures ★the lexical path only★. But the deployed hook
    #    ★always calls stage 2 (semantic search) when lexical goes silent★ (`hook.fallback`).
    #    So that value is ★half★ of what a user actually experiences.
    #
    #    ★This is the third face of the same trap in this repository★ — on 08-25 the benefit was
    #    measured with stage 2 and the cost with lexical (false firing 0/45 versus a real 15/45),
    #    and this morning hits were counted without noise. Now hits and noise are measured on the
    #    same path — but ★that path is not the deployed one.★
    #
    #    So `--full` re-measures on the deployed pipeline. ⛔ There is exactly one reason not to
    #    do it by default: stage 2 costs an embedding round trip (~508ms) and budget. ★When there
    #    are two ways to measure, both go in the table★ — so neither can be cherry-picked.
    # ⛔ ★Off by default and off under --full — enabled only by its own env flag.★
    #    Two reasons: ① of 91 queries, each silent one calls the judge, about 55 calls. And the
    #    same wallet is used by self-improvement (brain-rules-daily, 300 calls) — `--full` must
    #    not quietly eat it. ② `verify_score_honesty` counts `--full`'s judge calls as
    #    ★queries × R★, and calling from here breaks that arithmetic (that check caught the first
    #    version of this code exactly — it had the cache on and made 52 calls).
    if A and C and os.environ.get("BRAIN_SCORE_AUTO_DEPLOYED") == "1":
        try:
            def deployed(q):
                rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
                keep = hook.select(rows, thr)
                if keep:
                    return keep
                # ⛔ ★Cache off★ — a measurement that reads its own earlier answer reproduces anything
                return hook.fallback(db, q, strict=True, cache=False) or []
            hit2 = 0
            for q, gold in A:
                keep = deployed(q)
                if keep and any(_ei.gold_hit(gold, r["name"]) for r in keep):
                    hit2 += 1
            ff2 = sum(1 for q in C if deployed(q))
            h2 = hit2 / float(len(A))
            s2 = 1.0 - ff2 / float(len(C))
            out["_auto_full_hit"] = h2
            out["_auto_full_false_fire"] = ff2 / float(len(C))
            out["_auto_word_only"] = out.get("automatic")   # keep the ★lexical-only★ value
            out["automatic"] = h2 * s2                      # ★the deployed one is canonical★
        except Exception as exc:                             # noqa: BLE001
            # ⛔ Never swallow a failure and call the lexical value "the deployed value" — leave a trace
            out["_auto_full_error"] = str(exc)[:120]

    # ── Precision + stability — ★the unlabelled control, R times★ (remote) ───
    #
    # ⛔ ★This measurement runs with the cache off★ (fixed by measurement, 2026-08-31)
    #
    #    It used to call `rerank.score(q, cands, db=db)` — that is, with the cache on. The result:
    #    25 queries finished spending ★0★ of the budget, because every one of them read
    #    ★its own earlier answer★. That value (88%) came back identical twice and I nearly read
    #    it as "reproduced". With the cache off, it split ★76% ↔ 88%★.
    #
    #    ★A control query is almost always a first encounter in real use★ — people do not repeat
    #    the same small talk. So a cached value is not a real-use value but ★the measurement
    #    reading its own answer★.
    #
    # The same pass also produces ★stability★ — the two axes share one root.
    #    precision = share of judgements that stayed silent
    #    stability = share of queries whose decision (fire/silent) ★never split across R runs★
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mjp", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tests", "measure_judge_precision.py"))
        mjp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mjp)
        thr = rerank.min_score(db)
        reps = max(1, int(os.environ.get("BRAIN_SCORE_REPEATS", "3") or 3))
        n_q = max(1, int(os.environ.get("BRAIN_SCORE_CONTROL_N", "25") or 25))
        fires, per_q = [], []
        for q, cands in mjp.blind_control(db, A)[:n_q]:
            decisions = []
            for _ in range(reps):
                sc = rerank.score(q, cands, db=db, cache=False)
                if sc is None:
                    continue                         # a network failure is not a judgement
                decisions.append((max(sc) if sc else 0.0) >= thr)
            if not decisions:
                continue
            fires.extend(decisions)
            per_q.append(decisions)
        if fires:
            out["precision"] = 1.0 - sum(1 for f in fires if f) / float(len(fires))
            out["_precision_n"] = float(len(per_q))
            out["_precision_repeats"] = float(reps)
        if per_q and reps > 1:
            # share of queries ★whose decision did not split★ — target 0.90 (§TARGET)
            steady = sum(1 for d in per_q if len(set(d)) == 1 and len(d) == reps)
            counted = sum(1 for d in per_q if len(d) == reps)
            if counted:
                out["stability"] = steady / float(counted)
                out["_stability_n"] = float(counted)
    except Exception:                                # noqa: BLE001
        out["precision"] = None
    return out


# ── ★Side by side with the old way★ ─────────────────────────────────────────
# User instruction (2026-08-31): *"beyond the brain's own numbers there must be figures that
# ★compare against the existing memory search★, and they must be comparable."*
#
# ⛔ The old way is not one thing but ★two★. Being honest means recording both:
#    ① `the index file`  — only memories named in it can be called (what is not written may as well not exist)
#    ② `grep, content words OR'd` — searches everything, and finds it, but ★read volume★ explodes
#
# ⛔ And ★the axes the old way wins are recorded as they are★. Stability and liveness are full
#    marks for the old way — it does nothing by itself, so there is nothing to wobble or to stop.
#    Hiding that would turn this table back into "picking the favourable number".
LEGACY_NOTE = {
    "recall": "grep has no ranking, so ★3 at random★ (matched for the same budget)",
    "precision": "grep cannot say ★there is no answer★ — if it matches, it returns it",
    "efficiency": "1.0 by definition (it is the baseline)",
    "automatic": "★0 — the old way only works when called★ (ask nothing and nothing happens)",
    "stability": "1.0 — it is deterministic",
    "autonomy": "0 — a human maintains the index file by hand",
    "liveness": "1.0 — nothing runs, so nothing can stop",
}


def collect_legacy(db) -> Dict[str, Optional[float]]:
    """Raw values for the old way (index file + grep OR) on the same axes. ⛔ All local."""
    import json as _j
    out: Dict[str, Optional[float]] = {k: None for _, k, _, _ in AXES}
    path = _short_json()
    try:
        with open(path, encoding="utf-8") as fh:
            ev = _j.load(fh)
        A = [(r["q"], r["gold"]) for r in ev.get("A_memory_needed", [])
             if isinstance(r, dict) and r.get("gold")]
    except (OSError, ValueError):
        return out
    if not A:
        return out
    docs = [(r["name"], ((r["title"] or "") + " " + (r["body"] or "")).lower())
            for r in db.execute("SELECT name, title, body FROM docs WHERE source='memory'")]
    hit_all, sizes, ctrl_always = 0, [], 0
    for q, gold in A:
        ws = _query_words(q)
        got = [n for n, blob in docs if any(w in blob for w in ws)]
        sizes.append(len(got))
        if any(_ei.gold_hit(gold, n) for n in got):
            hit_all += 1
        if got:
            ctrl_always += 1
    sizes.sort()
    med = sizes[len(sizes) // 2] if sizes else 0
    # ⛔⛔ ★Recall is unified at 'when both read the same amount'★ (corrected 2026-08-31)
    #
    #    The first version measured grep's recall as "when you read ★every★ match" and the brain's
    #    as "within the top 3". That gives the old way 100 and the brain −44 — ★placing side by side
    #    two things that are not the same measurement★, which is exactly the asymmetry this
    #    repository keeps getting burnt by (the place where recall@191 was compared with recall@3).
    #
    #    grep has no ranking. So "read only 3" means ★3 at random★:
    #        recall@3 = (share of queries whose matches contain the answer) × min(1, 3/matches)
    #    That is the honest value at "the same budget (read 3)".
    #    ⛔ The exhaustive value is not discarded — it is kept separately as `_recall_exhaustive`.
    ex_hits = 0.0
    for q, gold in A:
        ws = _query_words(q)
        got = [n for n, blob in docs if any(w in blob for w in ws)]
        if got and any(_ei.gold_hit(gold, n) for n in got):
            ex_hits += min(1.0, 3.0 / len(got))
    out["recall"] = ex_hits / float(len(A))
    out["_recall_exhaustive"] = hit_all / float(len(A))
    out["efficiency"] = 1.0 / 50.0        # itself as baseline = 1× → 1/50 of the target line (50×)
    out["_grep_median_docs"] = float(med)
    out["_efficiency_ratio"] = 1.0
    out["automatic"] = 0.0                # ★only works when called★
    out["stability"] = 1.0                # deterministic
    out["autonomy"] = 0.0                 # maintained by hand
    out["liveness"] = 1.0                 # nothing runs
    # Precision — grep cannot say "there is no answer". Measured as the share of control queries it still answers.
    try:
        C = [r if isinstance(r, str) else r["q"] for r in ev.get("C_no_memory_needed", [])]
        noise = 0
        for q in C:
            ws = _query_words(q)
            if any(any(w in blob for w in ws) for _n, blob in docs):
                noise += 1
        out["precision"] = 1.0 - noise / float(len(C)) if C else None
    except Exception:                     # noqa: BLE001
        out["precision"] = None
    return out


def three_way(db, full: bool = False) -> dict:
    """★Three approaches under the same conditions★ — the brain · grep OR · the agent alone.

    ⛔ It does not hide that the three have different natures:
       · agent alone — arrives ★without being called★ but reaches ★only what is written down★
       · grep OR     — searches everything but ★only when called★, and read volume explodes
       · the brain   — arrives uncalled and reads only 3. In exchange ★it has a lot running by
                       itself, and that can stop★ (the liveness axis measures that cost)
    """
    return {
        "brain": compute(collect(db, cheap=not full)),
        "grep": compute(collect_legacy(db)),
        "claude": compute(collect_claude_only(db)),
    }


def render_three(t: dict, title: str = "") -> str:
    out = []
    if title:
        out.append(title)
    out.append("%-8s %4s │ %8s %8s %10s │ %s"
               % ("axis", "weight", "brain", "grep OR", "agent alone", "what it measures"))
    out.append("-" * 100)
    for a, g, c in zip(t["brain"]["axes"], t["grep"]["axes"], t["claude"]["axes"]):
        f = lambda v: "   —  " if v is None else "%6.1f" % v
        out.append("%-8s %3d%% │ %8s %8s %10s │ %s"
                   % (a["axis"], a["weight"], f(a["score"]), f(g["score"]), f(c["score"]), a["what"]))
    out.append("-" * 100)
    fb = lambda k: ("—" if t[k]["total"] is None else "%.1f" % t[k]["total"])
    out.append("★total   brain %s   ↔   grep OR %s   ↔   agent alone %s★"
               % (fb("brain"), fb("grep"), fb("claude")))
    ce = t["claude"].get("extra") or {}
    if ce.get("_named") is not None:
        out.append("⛔ Why the agent alone has low recall — of the gold memories, only ★%d are named "
                   "in the index file★. A memory that is not written there is never called, "
                   "however much its file exists (the reason this project began)." % ce["_named"])
    return "\n".join(out)


def render_compare(mine: dict, legacy: dict, title: str = "") -> str:
    """★Two columns side by side★ — which axes win and lose must be visible at a glance."""
    out = []
    if title:
        out.append(title)
    out.append("%-8s %4s │ %7s %7s │ %6s │ %s" %
               ("axis", "weight", "brain", "old way", "diff", "what the old way is"))
    out.append("-" * 96)
    win = lose = 0
    for a, b in zip(mine["axes"], legacy["axes"]):
        ms, ls = a["score"], b["score"]
        if ms is None or ls is None:
            d = "  —  "
        else:
            diff = ms - ls
            d = "%+6.1f" % diff
            if diff > 0.5:
                win += 1
            elif diff < -0.5:
                lose += 1
        out.append("%-8s %3d%% │ %7s %7s │ %6s │ %s"
                   % (a["axis"], a["weight"],
                      "  —  " if ms is None else "%6.1f" % ms,
                      "  —  " if ls is None else "%6.1f" % ls,
                      d, LEGACY_NOTE.get(a["key"], "")))
    out.append("-" * 96)
    mt, lt = mine["total"], legacy["total"]
    if mt is not None and lt is not None:
        out.append("★total  brain %.1f  ↔  old way %.1f   (diff %+.1f)★" % (mt, lt, mt - lt))
    out.append("axes won %d · axes lost %d" % (win, lose))
    mex = mine.get("extra") or {}
    if mex.get("_precision_n"):
        out.append("⚠️ Precision is measured on ★a fixed sample of %d★ — if the sample wobbles so "
                   "does this axis (before fixing it, 12%%, 36%% and 12%% came out)." % mex["_precision_n"])
    lex = legacy.get("extra") or {}
    if lex.get("_grep_median_docs"):
        out.append("★Recall is 'having read only 3' for both★ — grep has no ranking, so that is "
                   "3 at random (median %.0f matches)." % lex["_grep_median_docs"])
    if lex.get("_recall_exhaustive") is not None:
        out.append("   ↳ For reference: if the old way ★reads all %.0f★ its recall reaches %.0f%% "
                   "— the efficiency axis measures that price."
                   % (lex["_grep_median_docs"], 100 * lex["_recall_exhaustive"]))
    return "\n".join(out)


def collect_claude_only(db) -> Dict[str, Optional[float]]:
    """★The agent alone★ — no brain, no grep, only what is loaded automatically.

    ## Why this baseline is needed separately (user instruction, 2026-08-31)

    *"So we can compare performance against using the coding agent on its own."*

    grep works ★when a human calls it★. The agent alone does not even have that — only the
    index file (and CLAUDE.md) that every session loads automatically. Therefore:

    ⛔ ★Only memories 'named' in the index file can be reached.★ One that is not written there
       is never called however much its file exists, so it ★may as well not exist★ — which is
       exactly why this project began (205 of 434 had been pushed out of the index).

    So recall here is ★the share of gold memories named inside the index file★.
    ⚠️ This is a generous calculation — being named is no guarantee of surfacing at that moment.
       It is left generous deliberately, so as not to stack the comparison unfairly.
    """
    import json as _j
    from brain import store
    out: Dict[str, Optional[float]] = {k: None for _, k, _, _ in AXES}
    path = _short_json()
    try:
        with open(path, encoding="utf-8") as fh:
            ev = _j.load(fh)
        A = [(r["q"], r["gold"]) for r in ev.get("A_memory_needed", [])
             if isinstance(r, dict) and r.get("gold")]
    except (OSError, ValueError):
        return out
    from brain import hosts
    # ⛔ what ★this host★ loads by itself (§hosts.Host.autoloaded_index) — it used to be `MEMORY.md` for all
    idx = hosts.active().autoloaded_index(store.memory_dir())
    try:
        with open(idx, encoding="utf-8") as fh:
            index_text = fh.read()
    except OSError:
        return out
    named = sum(1 for _q, g in A if any(x in index_text for x in _ei.golds(g)))
    out["recall"] = named / float(len(A)) if A else None
    out["_named"] = float(named)
    out["_index_chars"] = float(len(index_text))
    # ⛔ It is loaded ★whole, regardless of the query★, every session — that is this approach's read volume.
    #    Converted to a document count to compare with the brain's 3 (≈13KB of excerpts).
    row = db.execute("SELECT AVG(LENGTH(body)) a FROM docs WHERE source='memory'").fetchone()
    avg = float(row["a"] or 1500)
    docs_equiv = max(1.0, len(index_text) / avg)
    out["_efficiency_ratio"] = docs_equiv / 3.0
    out["_grep_median_docs"] = docs_equiv
    out["efficiency"] = min(1.0, (docs_equiv / 3.0) / 50.0)
    # Precision — ★it attaches the same thing regardless of the query★, even when there is no answer.
    #   That is, the ability to "say nothing when there is nothing" is ★structurally 0★.
    out["precision"] = 0.0
    out["automatic"] = out["recall"]      # what is written arrives automatically (that is the whole approach)
    out["stability"] = 1.0                # always the same thing, so deterministic
    out["autonomy"] = 0.0                 # maintained by hand (this repository's starting point)
    out["liveness"] = 1.0                 # nothing runs
    return out


# ── ★Direct comparison — raw values with units★ ──────────────────────────────
# User instruction (2026-08-31): *"You should not compare the agent itself by a composite
# score — some values may not exist. Surely the important comparisons are ★accurate memory,
# retrieval, storage density and analysis speed★?"*
#
# ⛔⛔ ★The point is right — totals must not be compared.★ The agent-alone total contains
#    stability 100 and liveness 100, values that are true ★because it does nothing★, while the
#    brain's same axes are unmeasured ("—"). ★That is summing across different measurement
#    scopes★, precisely the asymmetry this repository keeps catching.
#
# ⇒ The total is used only for ★the brain's own comparison over time★ (did it rise after a change).
#   Approaches are compared only by ★raw values with units★. That is the table below.
#   ⛔ Where something does not structurally apply, write 'n/a' instead of a number.
def bench(db, n_speed: int = 30) -> dict:
    """Per-approach ★raw values★ — accuracy, retrieval, storage density, speed. All local, zero remote calls."""
    import json as _j
    import time as _t
    from brain import calibrate as _cal, hook, search, store

    path = _short_json()
    # ⛔ ★A fresh install has no labelled sample★ — this used to open it unguarded, so `brain score` and
    #    `brain dashboard` died with a traceback on a stranger's first day (measured in a clean room,
    #    2026-10-06). The rows that need the sample say n/a; the rest are still measured.
    try:
        with open(path, encoding="utf-8") as fh:
            ev = _j.load(fh)
    except (OSError, ValueError):
        ev = {}
    A = [(r["q"], r["gold"]) for r in ev.get("A_memory_needed", [])
         if isinstance(r, dict) and r.get("gold")]
    docs = [(r["name"], ((r["title"] or "") + " " + (r["body"] or "")).lower())
            for r in db.execute("SELECT name, title, body FROM docs WHERE source='memory'")]
    n_mem = len(docs)
    from brain import hosts
    try:
        with open(hosts.active().autoloaded_index(store.memory_dir()), encoding="utf-8") as fh:
            index_text = fh.read()
    except OSError:
        index_text = ""
    named = sum(1 for n, _b in docs if n in index_text)

    thr = _cal.threshold(db)
    b_top3 = b_fire_ok = b_fired = 0
    b_bytes = 0
    for q, gold in A:
        rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
        names = [r["name"] for r in rows if not r.get("related")]
        if any(_ei.gold_hit(gold, n) for n in names[:3]):
            b_top3 += 1
        keep = hook.select(rows, thr)
        if keep:
            b_fired += 1
            # ⛔ ★Writing the brain's "automatic read volume" as 0 would be false★ — when the hook
            #    fires it attaches excerpts. The honest way is to ★actually build★ the injected text and measure it.
            try:
                b_bytes += len(hook._format(keep))
            except Exception:                            # noqa: BLE001
                b_bytes += sum(len((r.get("excerpt") or "")) for r in keep)
            if any(_ei.gold_hit(gold, r["name"]) for r in keep):
                b_fire_ok += 1

    g_hit3 = 0.0
    g_sizes = []
    for q, gold in A:
        ws = _query_words(q)
        got = [n for n, b in docs if any(w in b for w in ws)]
        g_sizes.append(len(got))
        if got and any(_ei.gold_hit(gold, n) for n in got):
            g_hit3 += min(1.0, 3.0 / len(got))       # no ranking, so 3 at random
    g_sizes.sort()
    g_med = g_sizes[len(g_sizes) // 2] if g_sizes else 0
    c_hit = sum(1 for _q, g in A if any(x in index_text for x in _ei.golds(g)))

    sample = A[:n_speed]
    t0 = _t.time()
    for q, _g in sample:
        search.recall(db, q, k=5, log=False)
    b_ms = (_t.time() - t0) / max(1, len(sample)) * 1000
    t0 = _t.time()
    for q, _g in sample:
        ws = _query_words(q)
        sum(1 for _n, b in docs if any(w in b for w in ws))
    g_ms = (_t.time() - t0) / max(1, len(sample)) * 1000

    N = float(len(A)) or 1.0
    per_named = (len(index_text) / named) if named else 0.0

    def per(v):                                          # a value that needs the labelled sample
        return v if A else None
    # (metric, unit, brain, grep OR, agent alone, higher-is-better, note, ★key★, ★unit key★)
    #
    # ⛔ ★The 8th and 9th entries are keys★ (2026-09-02). The dashboard renders in six languages,
    #    and with only a sentence there is nothing to translate — these eight rows actually stayed
    #    in one language on the English screen and the user caught it. The CLI keeps using the
    #    first column and the screen looks up `bench.<key>` and `unit.<u>`. ★Both in one row, not two copies.★
    rows = [
        ("Answer found (reading only 3)", "%", per(100 * b_top3 / N), per(100 * g_hit3 / N),
         per(100 * c_hit / N), True, "matched for the same budget — grep has no ranking, so 3 at random",
         "answer", "pct"),
        ("Right thing surfaces unprompted", "%", per(100 * b_fire_ok / N), per(0.0),
         per(100 * c_hit / N), True, "grep only works when called · the agent alone attaches the same thing regardless of query",
         "unprompted", "pct"),
        ("Memories that can be reached", "items", float(n_mem), float(n_mem), float(named), True,
         "the agent alone reaches ★only what is named in the index file★", "reachable", "items"),
        ("Share that can be reached", "%", 100.0, 100.0, 100.0 * named / max(1, n_mem), True, "",
         "reach_pct", "pct"),
        ("★Storage density★ always-on index per memory", "bytes", 0.0, 0.0, per_named, False,
         "the brain grows the index by ★0 characters★ per new memory (files declare their triggers)",
         "density", "bytes"),
        ("Injected automatically (per firing)", "KB",
         per((b_bytes / max(1, b_fired)) / 1024.0), None, len(index_text) / 1024.0, False,
         "the brain injects 2–3 excerpts ★only when it fires★ (ones matching the query) · the agent "
         "alone loads the whole file ★every session★ · grep injects nothing (n/a)", "injected", "kb"),
        ("Documents read per answer", "docs", per(3.0), per(float(g_med)), float(named), False,
         "★the heart of efficiency★", "reads", "docs"),
        ("Latency per recall", "ms", per(b_ms), per(g_ms), None, False,
         "⛔ ★grep is faster★ (and that is not hidden) — in exchange it reads as much as the row "
         "above. The agent alone has ★no retrieval action at all★ (n/a — not 0)",
         "latency", "ms"),
    ]
    return {"rows": rows, "n_queries": len(A), "n_mem": n_mem, "named": named, "no_sample": not A,
            "grep_median": g_med,
            "db_mb": os.path.getsize(store.db_path()) / 1e6}


def render_bench(b: dict, title: str = "") -> str:
    out = []
    if title:
        out.append(title)
    out.append("%-32s %-6s %10s %10s %12s" %
               (i18n.t("sc.bench.col_metric"), i18n.t("sc.bench.col_unit"),
                i18n.t("sc.bench.col_brain"), i18n.t("sc.bench.col_grep"), i18n.t("sc.bench.col_agent")))
    out.append("-" * 100)
    for label, unit, mv, gv, cv, hi, note, *_keys in b["rows"]:
        # ★label/unit are English fallbacks★ — when the row carries a bench./unit. key
        # (added 2026-09-02 for the dashboard), the catalog translation wins over the raw text.
        bkey, ukey = (_keys + [None, None])[:2]
        label = i18n.t("bench." + bkey) if bkey else label
        unit_disp = i18n.t("unit." + ukey) if ukey else unit
        # ★only translate the note if the row actually has one★ — reach_pct's raw note is ""
        note = i18n.t("sc.note." + bkey) if (note and bkey) else note   # keys claimed by I18N_PREFIX
        # ⛔ ★Scoring a structurally inapplicable cell as 0 makes that approach look like the winner.★
        #    Showing "0ms because there is no retrieval" as the fastest is exactly that trap.
        vals = [v for v in (mv, gv, cv) if v is not None]
        best = (max(vals) if hi else min(vals)) if vals else None
        def f(v):
            if v is None:
                return i18n.t("sc.bench.na")
            t = ("%.1f" % v) if unit not in ("items", "docs") else ("%.0f" % v)
            return ("★%s★" % t) if best is not None and v == best else t
        out.append("%-32s %-6s %10s %10s %12s" % (label, unit_disp, f(mv), f(gv), f(cv)))
        if note:
            out.append("%-32s %s" % ("", note))
    out.append("-" * 100)
    out.append(i18n.t("sc.bench.summary", n=b["n_queries"], mem=b["n_mem"],
                       med=b["grep_median"], mb="%.0f" % b["db_mb"]))
    if b.get("no_sample"):
        out.append(i18n.t("sc.bench.no_sample"))
    out.append(i18n.t("sc.bench.composite_warning"))
    out.append(i18n.t("sc.bench.total_hint"))
    return "\n".join(out)
