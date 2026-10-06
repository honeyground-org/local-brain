"""Health check — ★an exhaustive look at the five paths by which memory distorts★.

Memory does not only break by disappearing. The shapes of distortion this repository has actually met:

  ① Index compression loss — pushed against a read-size limit, 205 entries fell out of the index.
     The files existed, but nothing pointed at them, so they **may as well not have existed**.
  ② Stale numbers — a number quoted as current long after it was measured misled a decision once.
     Quote an old memory as present fact and a decision goes wrong.
  ③ Broken links — `[[a name that does not exist]]` quietly goes nowhere. Not an error, an empty hand.
  ④ Duplication and divergence — the same fact split across two files means one of them gets fixed
     (the same lesson as a rule written in three places).
  ⑤ Accumulated but never used — the failure an earlier memory system hit seven times. A memory never once recalled.

★The core discipline★: a recall result **always carries its age** (§search._present). What prevents
distortion is not a warning but **handing over the material for judgement** — remove it and the user is more confused.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Dict, List, Optional

from brain import i18n, search, store

HOT_HITS = 3          # recalled this often counts as "frequently used"
RARE_DF = 3           # the rarity bar for terms used to narrow duplicate candidates
DUP_MIN_SHARED = 4    # sharing this many rare terms raises a duplicate suspicion

_W_DELTA = {"useful": 0.15, "wrong": -0.5, "stale": -0.2, "open": 0.05}
_W_MIN, _W_MAX = 0.2, 3.0


def _rows(db: sqlite3.Connection, sql: str, args=()) -> List[sqlite3.Row]:
    return db.execute(sql, args).fetchall()


def orphans(db: sqlite3.Connection, source: str = "memory") -> List[str]:
    """Memories nothing points at. ★It now means "no connection", not "invisible"★ —
    search still reaches them. So this list is not an incident but **a list of linking candidates**."""
    return [r["name"] for r in _rows(db,
        "SELECT d.name FROM docs d WHERE d.source=? AND NOT EXISTS "
        "(SELECT 1 FROM links l JOIN name_map m ON m.key=l.dst_name WHERE m.doc_id=d.id) "
        "ORDER BY d.name", (source,))]


def dangling_split(db: sqlite3.Connection, source: str = "memory") -> dict:
    """Split broken links into ★what I can fix / what only a human can fix★.

    ⛔ This repository's discipline demands it — *"a diagnostic that counts what you cannot fix
       becomes a wolf within a day"*. Of 7 broken links, ★2 were declared aliases that were not being
       read★ (a system defect, fixed 2026-08-31), ★1 was a prefix mirror form★ (fixed the same day),
       and the rest ★point at a memory that does not exist at all★. The first two are for code to fix,
       the rest for a human to write. Put them in one number and a person checks a few times and never looks again.

    ★If a similar name exists, say so★ — "did you mean this one" produces action where "it is missing" does not.
    """
    rows = dangling(db, source)
    names = [r["name"] for r in db.execute("SELECT name FROM docs")]
    out = {"missing": [], "typo": []}
    for r in rows:
        dst = r["dst"] if "dst" in r.keys() else r.get("to")
        near = [n for n in names if dst in n or n in dst]
        near = [n for n in near if n != dst][:3]
        item = {"from": r.get("src") or r.get("from"), "to": dst, "near": near}
        (out["typo"] if near else out["missing"]).append(item)
    return {
        "typo": out["typo"], "missing": out["missing"],
        "meaning": ("typo = ★a similar name really exists★ (fix the name or declare an alias and it opens) · "
                    "missing = there is no such memory (★a human has to write it★)"),
    }


def dangling(db: sqlite3.Connection, source: str = "") -> List[Dict[str, str]]:
    """Broken links. ★Given `source`, count only the ones **leaving** that source★.

    ⛔ At first everything was counted as one number. That produced **476**, the dashboard showed
    "critical", and unpacking it revealed only 37 came from memories; the rest were **relative-path
    Markdown links** in docs and wikis (pointing outside the indexed scope, which is normal).
    That is, someone else's document situation was being counted as our issue.

    ★Put the fixable and the unfixable in one number and that number cries wolf★ —
    a person checks a few times, finds nothing to do, and stops looking.
    """
    q = ("SELECT s.name src, l.dst_name dst FROM links l JOIN docs s ON s.id=l.src_id "
         "WHERE NOT EXISTS (SELECT 1 FROM name_map m WHERE m.key=l.dst_name)")
    args: List[str] = []
    if source:
        q += " AND s.source=?"
        args.append(source)
    return [{"from": r["src"], "to": r["dst"]}
            for r in _rows(db, q + " ORDER BY s.name", args)]


def is_cold_start(db: sqlite3.Connection, min_recalls: int = 40) -> bool:
    """★Has this only just started being used★ — skip this question and the diagnostic lies from day one.

    "Memories never recalled" is **almost all of them** if the brain was built today.
    That is not an issue; it is that nothing has happened yet.
    """
    n = _rows(db, "SELECT COUNT(*) c FROM recalls")[0]["c"]
    return n < min_recalls


# ⛔ ★The staleness rule is not kept here — `search.STALE_DAYS` is canonical★ (merged 2026-08-12)
#   There used to be `EVIDENCE_STALE_DAYS = 90` and `STALE_MULTIPLIER` here, with a table meaning
#   the same thing in `search`. A rule written in two places gets fixed in one.


def stale(db: sqlite3.Connection, days: Optional[int] = None) -> List[dict]:
    """★By evidence date, with a per-kind threshold★.

    The file may be from yesterday and the number inside from six months ago (§store.evidence_date).
    A document with no evidence date is not flagged — **we do not declare what we do not know to be old**.
    """
    out = []
    for r in _rows(db,
            "SELECT d.name, d.kind, d.evidence_date, d.mtime, u.hits FROM docs d "
            "LEFT JOIN usage u ON u.path=d.path "
            "WHERE d.source='memory' AND d.evidence_date != '' "
            "ORDER BY COALESCE(u.hits,0) DESC, d.evidence_date"):
        ev = r["evidence_date"]
        try:
            y, m, d = (int(x) for x in ev.split("-"))
            age = int((time.time() - time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))) / 86400)
        except (ValueError, OverflowError):
            continue
        threshold = days if days is not None else search.stale_limit(r["kind"])
        if age < threshold:
            continue
        out.append({"name": r["name"], "kind": r["kind"], "evidence_date": ev,
                    "evidence_age_days": age, "threshold_days": int(threshold),
                    "file_age_days": int((time.time() - r["mtime"]) / 86400),
                    "hits": r["hits"] or 0})
    out.sort(key=lambda d: d["evidence_age_days"] / max(1, d["threshold_days"]), reverse=True)
    return out


def _count_kinds(rows) -> dict:
    """Counts per kind — ★if they pile up in one kind, that is a property, not a fault.★"""
    out = {}
    for r in rows:
        k = r.get("kind") or "(none)"
        out[k] = out.get(k, 0) + 1
    return out


DUP_MAX_SIZE_RATIO = 4.0   # a bigger size gap than this is a summary/body relation, not a duplicate
DUP_MIN_OVERLAP = 0.18     # must share at least this share of the smaller side's rare words to be suspected


def _shared_rare_pairs(db: sqlite3.Connection) -> Dict[tuple, int]:
    """Memory pairs sharing rare terms, and how many. Duplicate detection and link suggestion both use it."""
    buckets: Dict[str, List[int]] = {}
    for r in _rows(db,
            "SELECT p.term, p.doc_id FROM postings p JOIN terms t ON t.term=p.term "
            "JOIN docs d ON d.id=p.doc_id "
            "WHERE t.df BETWEEN 2 AND ? AND d.source='memory'", (RARE_DF,)):
        buckets.setdefault(r["term"], []).append(r["doc_id"])
    pair_hits: Dict[tuple, int] = {}
    for docs in buckets.values():
        if len(docs) > RARE_DF:
            continue
        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                key = (min(docs[i], docs[j]), max(docs[i], docs[j]))
                pair_hits[key] = pair_hits.get(key, 0) + 1
    return pair_hits


def _pair_facts(db: sqlite3.Connection) -> Dict[int, dict]:
    """Per-document facts + ★how many rare terms that document has★.

    Why the rare-term count is needed: by shared **count** alone, a big document matches anything.
    In measurement, one 22KB document shared 10 terms each with four documents on different topics —
    they did not overlap, it simply **had a lot of words**.
    So the share is **divided by the smaller side's rare-term count** and read as a ratio.
    """
    facts = {r["id"]: {"name": r["name"], "kind": r["kind"],
                       "size": len(r["body"] or ""), "rare": 0}
             for r in _rows(db, "SELECT id,name,kind,body FROM docs WHERE source='memory'")}
    for r in _rows(db,
            "SELECT p.doc_id, COUNT(*) c FROM postings p JOIN terms t ON t.term=p.term "
            "WHERE t.df BETWEEN 2 AND ? GROUP BY p.doc_id", (RARE_DF,)):
        if r["doc_id"] in facts:
            facts[r["doc_id"]]["rare"] = r["c"]
    return facts


def _is_linked(db: sqlite3.Connection, a_id: int, b_id: int) -> bool:
    row = db.execute(
        "SELECT 1 FROM links l JOIN name_map m ON m.key=l.dst_name "
        "WHERE (l.src_id=? AND m.doc_id=?) OR (l.src_id=? AND m.doc_id=?) LIMIT 1",
        (a_id, b_id, b_id, a_id)).fetchone()
    return row is not None


def duplicates(db: sqlite3.Connection, limit: int = 25) -> List[dict]:
    """★Real duplicates only★ — same kind · similar size · **unaware of each other**.

    ⛔ At first "shares many rare words" meant duplicate. That produced 25 pairs, and measuring showed
    ★28 of 30 were already linked★. Two documents that link to each other sharing rare words is obvious —
    that is why the link exists. The detector was **counting normal graph structure as an issue**.
    counting normal graph structure as an issue**.

    Three filtering axes (all of them from measurement):
      · ★already linked★ → a relation, not a duplicate (28/30)
      · different kind → a lesson and the project it came from are parent and child (20/30)
      · size gap over 4× → a summary and a body (16/30, up to 44×)

    What remains: pairs *"saying the same thing and unaware of each other"* — where the risk that only one gets fixed is real.
    """
    facts = _pair_facts(db)
    out = []
    for (a, b), n in _shared_rare_pairs(db).items():
        if n < DUP_MIN_SHARED or a not in facts or b not in facts:
            continue
        fa, fb = facts[a], facts[b]
        if fa["kind"] != fb["kind"]:
            continue
        ratio = max(fa["size"], fb["size"]) / max(1, min(fa["size"], fb["size"]))
        if ratio > DUP_MAX_SIZE_RATIO:
            continue
        if _is_linked(db, a, b):
            continue
        # ★Read it as a ratio★ — what share of the smaller side's rare words are shared with the other.
        # By count the bigger document wins; by ratio **what really says the same thing** wins.
        denom = max(1, min(fa["rare"], fb["rare"]))
        overlap = n / denom
        if overlap < DUP_MIN_OVERLAP:
            continue
        out.append({"a": fa["name"], "b": fb["name"], "shared_rare_terms": n,
                    "kind": fa["kind"], "size_ratio": round(ratio, 1),
                    "overlap": round(overlap, 2)})
    out.sort(key=lambda d: d["overlap"], reverse=True)
    return out[:limit]


def link_candidates(db: sqlite3.Connection, limit: int = 20,
                    min_shared: int = 8) -> List[dict]:
    """★Candidates that should be linked★ — pairs saying the same thing with no link between them.

    This is the signal duplicate detection was misreading, turned into something useful. "Shares many rare
    words but they do not know each other" is **a linking suggestion, not a duplicate warning**, and once
    linked, recall's graph bridge widens by that much (fewer orphans).
    """
    facts = _pair_facts(db)
    out = []
    for (a, b), n in _shared_rare_pairs(db).items():
        if n < min_shared or a not in facts or b not in facts:
            continue
        if _is_linked(db, a, b):
            continue
        out.append({"a": facts[a]["name"], "b": facts[b]["name"],
                    "shared_rare_terms": n})
    out.sort(key=lambda d: d["shared_rare_terms"], reverse=True)
    return out[:limit]


def never_recalled(db: sqlite3.Connection, limit: int = 20) -> List[str]:
    return [r["name"] for r in _rows(db,
        "SELECT d.name FROM docs d LEFT JOIN usage u ON u.path=d.path "
        "WHERE d.source='memory' AND COALESCE(u.hits,0)=0 ORDER BY d.mtime DESC LIMIT ?",
        (limit,))]


# ── ★Issue or metric★ — the ★one place★ that rule is written ────────────────
#
# ⛔ This distinction lived only inside the dashboard (created on 2026-08-10 after a user's point). So
#    `brain status` printed five numbers ★with no distinction★, and whoever read them —
#    on 2026-08-31, that was me — read "459 things to fix".
#    ★A rule in two places gets fixed in one★ is this repository's recurring mistake.
#    The judgement now lives here alone, and the CLI and the dashboard read it.
#
# ⛔ The grounds for calling something a metric are ★measurement★, not taste. To reverse one, disprove that measurement first.
# ★values are i18n keys, resolved in classify() at call time★ — a module-level dict is built once
#    at import, so resolving text here would freeze it to whatever language was active then.
CHECK_KIND = {
    "dangling_links": ("issue", "health.check.dangling_links.action", ""),
    # ⛔ 2026-08-31 ★issue → metric★ — broken down, it was not a task (§stale comment).
    "stale_evidence": ("metric", "", "health.check.stale_evidence.why_metric"),
    "duplicate_candidates": ("issue", "health.check.duplicate_candidates.action", ""),
    "orphans": ("metric", "", "health.check.orphans.why_metric"),
    "never_recalled": ("metric", "", "health.check.never_recalled.why_metric"),
}


def classify(checks: dict) -> dict:
    """Attach ★issue/metric★ and the grounds to every diagnostic. ⛔ The judgement happens only here."""
    out = {}
    for key, val in checks.items():
        kind, action_key, why_key = CHECK_KIND.get(key, ("issue", "", ""))
        row = dict(val)
        row["kind"] = kind
        if action_key:
            row["action"] = i18n.t(action_key)
        if why_key:
            row["why_metric"] = i18n.t(why_key)
        out[key] = row
    return out


def status(db: sqlite3.Connection) -> dict:
    st = store.corpus_stats(db)
    by_source = {r["source"]: r["c"] for r in _rows(db,
        "SELECT source, COUNT(*) c FROM docs GROUP BY source")}
    by_kind = {(r["kind"] or "(none)"): r["c"] for r in _rows(db,
        "SELECT kind, COUNT(*) c FROM docs WHERE source='memory' GROUP BY kind")}
    dl = dangling(db, "memory")
    dl_other = len(dangling(db)) - len(dl)
    cold = is_cold_start(db)
    orp = orphans(db)
    stl = stale(db)
    hot_stale = [s for s in stl if s["hits"] >= HOT_HITS]
    # ⛔ ★A layer that died quietly must show up in the distortion diagnostics★ (2026-08-26) — when the
    #    judge hits its daily wall, stage-2 search goes completely silent, and that fact ★appeared nowhere★.
    #    So "the brain stops answering this afternoon" became a symptom with no known cause.
    try:
        from brain import rerank
        judge = rerank.budget(db)
    except Exception:                                    # noqa: BLE001
        judge = {}
    # ⛔ ★Is the guard actually working on this machine★ (2026-09-02) — the fixed sample is personal
    #    data and is excluded from distribution. Then the regression guard quietly blocks nothing.
    #    ★Worse than being unprotected is not knowing you are unprotected.★
    try:
        from brain import calibrate as _cal
        guard = _cal.guard_status(db)
        guard["last_unguarded"] = _cal.unguarded(db)
    except Exception:                                    # noqa: BLE001
        guard = {}
    return {
        "corpus": st,
        "calibration_guard": guard,
        "by_source": by_source,
        "memory_by_kind": by_kind,
        "judge_budget": judge,
        # ⛔ ★A diagnostic that counts an absence★ — when a scheduled job stops, nothing is written to the log.
        "schedules": schedules(),
        "distortion_checks": classify({
            "orphans": {"count": len(orp), "sample": orp[:10],
                        "meaning": i18n.t("health.check.orphans.meaning")},
            "dangling_links": {"count": len(dl), "sample": dl[:10],
                               "outside_memory": dl_other,
                               # ⛔ ★Report the fixable and the unfixable separately★ (§dangling_split)
                               "split": dangling_split(db, "memory"),
                               "meaning": i18n.t("health.check.dangling_links.meaning")},
            "stale_evidence": {"count": len(stl), "threshold_days": search.stale_table(),
                      "sample": stl[:10],
                      # ★Break the big number down★ — 127 on its own supports no action.
                      #   called versus never called is this metric's only actionable axis.
                      "split": {"recalled": sum(1 for x in stl if x["hits"] > 0),
                                "never": sum(1 for x in stl if x["hits"] == 0),
                                "kinds": _count_kinds(stl)},
                      "meaning": i18n.t("health.check.stale_evidence.meaning")},
            "duplicate_candidates": {"count": len(duplicates(db)),
                                     "sample": duplicates(db)[:5],
                                     "meaning": i18n.t("health.check.duplicate_candidates.meaning")},
            "never_recalled": {"count": len(never_recalled(db, 100000)),
                               "sample": never_recalled(db, 10),
                               "cold_start": cold,
                               # ⛔ The old description ★was not true★ — reachability is 100%.
                               "meaning": i18n.t("health.check.never_recalled.meaning")},
        }),
    }


def suggestions(db: sqlite3.Connection) -> dict:
    """★Self-improvement★ — the brain proposes its own next task without being asked.

    The evidence is entirely **real usage records** (the recalls/usage tables, not guesswork).
    """
    out: Dict[str, List] = {"reconnect": [], "verify": [], "merge": [], "prune": [],
                            "missing": []}

    for d in dangling(db, "memory")[:15]:
        out["reconnect"].append(
            {"action": "the link target does not exist", "from": d["from"], "to": d["to"],
             "why": "create [[%s]] or fix the name and this path opens" % d["to"]})

    for s in stale(db):
        out["verify"].append(
            {"action": "re-confirm the measurement or discard it", "name": s["name"], "kind": s["kind"],
             "evidence_date": s["evidence_date"], "evidence_age_days": s["evidence_age_days"],
             "threshold_days": s["threshold_days"], "hits": s["hits"],
             "why": "the evidence is %d days old (%s) and kind %s has a threshold of %d days — confirm it is still true, or drop it"
                    % (s["evidence_age_days"], s["evidence_date"], s["kind"] or "?",
                       s["threshold_days"])})
    out["verify"] = out["verify"][:20]

    for d in duplicates(db, 10):
        out["merge"].append({"action": "suspected duplicate", **d,
                             "why": "shares %d%% of the smaller side's rare words with no link between them"
                                    % round(100 * d["overlap"])})
    # ★Not duplicates but things that should be linked★ — pairs saying the same thing, unaware of each other
    for c in link_candidates(db, 8):
        out["reconnect"].append(
            {"action": "linking candidate", "from": c["a"], "to": c["b"],
             "why": "shares %d rare words with no link — connecting them widens recall's bridge"
                    % c["shared_rare_terms"]})

    for r in _rows(db,
            "SELECT d.name, u.wrong FROM usage u JOIN docs d ON d.path=u.path "
            "WHERE u.wrong >= 2 ORDER BY u.wrong DESC LIMIT 10"):
        out["prune"].append({"action": "memory marked as wrong", "name": r["name"],
                             "wrong": r["wrong"], "why": "repeatedly irrelevant or wrong in recall"})

    for r in _rows(db,
            "SELECT query, COUNT(*) c FROM recalls WHERE hits <= 1 "
            "GROUP BY query ORDER BY c DESC LIMIT 10"):
        out["missing"].append({"action": "question that found nothing", "query": r["query"],
                               "times": r["c"],
                               "why": "there is no memory on this topic, or the words do not match"})
    return out


def feedback(db: sqlite3.Connection, name: str, signal: str) -> dict:
    if signal not in _W_DELTA:
        return {"error": "unknown signal: %s" % signal}
    row = db.execute("SELECT id, path, weight FROM docs WHERE name=?", (name,)).fetchone()
    if not row:
        return {"error": "not found: %s" % name}
    new_w = max(_W_MIN, min(_W_MAX, row["weight"] + _W_DELTA[signal]))
    col = {"useful": "useful", "wrong": "wrong", "stale": "stale", "open": "opens"}[signal]
    with db:
        db.execute("UPDATE docs SET weight=? WHERE id=?", (new_w, row["id"]))
        db.execute("INSERT INTO usage(path,%s) VALUES(?,1) "
                   "ON CONFLICT(path) DO UPDATE SET %s=%s+1" % (col, col, col),
                   (row["path"],))
    return {"name": name, "signal": signal, "weight": round(new_w, 3)}


# ── ★Notice on its own when the automation has stopped★ (2026-08-31) ─────────
# ⛔ Never hardcode the log path — the home is decided by §store.brain_home (it differs per platform and host)
# ★"what" holds an i18n key★, resolved in schedules() at call time (a test's own `_jobs` can
#    still pass literal text — an unknown key falls back to returning itself, so injection is unaffected).
SCHEDULED = (
    ("brain-vec-daily", "{home}/vec-daily.log", "health.job.vec_daily", 1,
     "com.local-brain.vec-daily"),
    ("brain-rules-daily", "{home}/rules-daily.log",
     "health.job.rules_daily", 1, "com.local-brain.rules-daily"),
)


def _log_path(tpl: str) -> str:
    """Expand `{home}` to the current home. The old `~/...` spelling is still accepted."""
    if "{home}" in tpl:
        return tpl.format(home=store.brain_home())
    return os.path.expanduser(tpl)


def _launchd(label: str, job: str = "") -> dict:
    """Ask ★the platform's scheduler★ — it is not only macOS (2026-09-02).

    The name is kept from before (several places call it). The actual query is done by the adapter in
    `brain/scheduler.py`, and that adapter also states ★how strong the evidence it can give is★:
    macOS gives a run count, Windows the last run time, cron ★nothing at all★.
    """
    from brain import scheduler as _sch
    got = _sch.active().query(job or label, label)
    got["scheduler"] = _sch.active().name
    got["evidence"] = _sch.active().evidence
    return got


def _launchd_macos_only(label: str) -> dict:
    """★How many times★ has the scheduler run this job. ⛔ Not whether it is registered.

    `launchctl print` gives `runs` and `stdout path` — a job that is registered but has never run
    honestly shows `runs = 0`. On anything but macOS it returns empty-handed and the caller falls
    back to the older judgement (log age).
    """
    import os
    import re
    import subprocess
    out = {"known": False, "runs": None, "stdout": None,
           "last_exit": None, "at": None}
    try:
        p = subprocess.run(
            ["launchctl", "print", "gui/%d/%s" % (os.getuid(), label)],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return out
    if p.returncode != 0:
        return out                                   # not registered — known=False
    txt = p.stdout
    out["known"] = True
    m = re.search(r"^\s*runs = (\d+)", txt, re.M)
    if m:
        out["runs"] = int(m.group(1))
    m = re.search(r"^\s*stdout path = (.+)$", txt, re.M)
    if m:
        out["stdout"] = m.group(1).strip()
    m = re.search(r"^\s*last exit code = (.+)$", txt, re.M)
    if m:
        out["last_exit"] = m.group(1).strip()
    # ★Never hardcode the scheduled time★ — read the value the scheduler itself knows.
    #    (The same rule in two places gets fixed in one — a place this repository has been burnt repeatedly)
    h = re.search(r'"Hour"\s*=>\s*(\d+)', txt)
    mi = re.search(r'"Minute"\s*=>\s*(\d+)', txt)
    if h:
        out["at"] = (int(h.group(1)), int(mi.group(1)) if mi else 0)
    return out


# ⛔ Without `re.M`, `^` matches ★only the first line of the file★ — on 2026-09-01 that made it miss
#    a 09:48 run and answer "7.8 days ago". The check passed (the test log's first line happened to
#    match). ★Whether it finds the last of several lines★ is what a check must verify.
_START_RE = __import__("re").compile(
    # Both dash styles the job writer has used. (A pre-release localized "start" word was dropped on
    # 2026-10-06: only the newest start line is read, and every run since writes `start`.)
    r"^(?:--|──) (\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}) start",
    __import__("re").M)


# ★Same day and after the scheduled time counts as "it ran on its own"★ (widened by measurement 2026-09-01)
#
# ⛔ The window was ±5 minutes at first. But on 09-01 both ran at ★09:48★ — scheduled for 09:00 and
#    09:30, and the scheduler ★caught up★ after the machine woke from sleep. That catch-up is exactly
#    why we moved from cron, and the narrow window was demoting that success to "wired only".
#    ★What we meant to measure is not "did it run on time" but "did it run without a human".★
#    but 'did it run without a human'.★
#
#    The scheduler's catch-up happens ★only after the scheduled time★ (never before).
#    So the window is "scheduled time → end of that day".
CATCHUP_HOURS = float(os.environ.get("BRAIN_SCHED_CATCHUP_H", "14") or 14)


def _calendar_run(log_path: str, at, now: float, window_min: int = 5):
    """Age in days of the most recent run that ★started at the scheduled time★. None if there is none.

    ## Why parse the start time (2026-08-31)

    Waking a job by hand with `launchctl kickstart` is also a scheduler run, so `runs` increases and
    the stdout log is refreshed — that is, ★verifying the wiring becomes green by itself★. But what we
    are really asking is *"does it fire ★on its own★ at that time every day"*.

    The stdout log's age cannot separate them (that file is written when the job ★finishes★, so a
    13-minute ingest starting at 09:00 is stamped 09:13). So we read ★the start time★ the job log
    writes itself and match it against the scheduled time.

    ⛔ This is not perfect either — the scheduler's ★catch-up★ run (right after waking) is not at the
       scheduled time. In that case this function returns None and the caller says
       "the scheduler did run it (wiring OK) · scheduled firing not confirmed".
       ★It errs towards not claiming to have seen what it did not.★
    """
    import os
    import time as _t
    if not at:
        return None
    try:
        with open(os.path.expanduser(log_path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 200000))
            txt = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    best = None
    for m in _START_RE.finditer(txt):
        y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
        # ★Accept it if within CATCHUP_HOURS after the scheduled time★ — including catch-up after waking.
        #   ⛔ ★Before★ the scheduled time is not accepted (that was a human).
        delta = (hh * 60 + mm) - (at[0] * 60 + at[1])
        if delta < -window_min or delta > CATCHUP_HOURS * 60:
            continue
        try:
            t = _t.mktime((y, mo, d, hh, mm, ss, 0, 0, -1))
        except (OverflowError, ValueError):
            continue
        if best is None or t > best:
            best = t
    return None if best is None else (now - best) / 86400.0


def _exit_code(raw) -> "int | None":
    """`last exit code` string → number. ⛔ If it cannot be judged, ★None★.

    `(never exited)` covers two cases — it never ran, or ★it is running right now★.
    Neither is "failure", so it returns None and the caller does not count it as one.
    """
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        return int(raw)
    except ValueError:
        return None


def schedules(now: float = 0.0, _probe=None, _jobs=None) -> dict:
    """Did the scheduled job run ★on its own★ recently. ⛔ Not registered, and not run by a human.

    ## Why this diagnostic is needed (measured 2026-08-31)

    Two cron jobs ★did not run once in two days★ after 08-29. The registration was fine —
    the machine was simply asleep at 09:00. macOS `cron` ★does not catch up★ on runs missed while
    asleep (`launchd`'s `StartCalendarInterval` does).

    ⛔ And this failure ★leaves nothing in the log★ — there is no opportunity to leave anything.
       It is a place where "no errors" and "did not run" are indistinguishable. So there has to be
       a separate diagnostic that **counts an absence**.

    ★This is the fourth member of the same family in this repository★:
      ① "registered" is not "it ran" (the cron registration was later than the cron time)
      ② a criterion written down is not a criterion if it is never executed (a calibrate nobody called)
      ③ "it ran" is not "it keeps running" (cron → launchd)
      ④ ★"I ran it by hand" is not "it runs on its own"★ (this one)

    ## Why ④ is a new hole

    The earlier judgement looked only at ★the job log's age★. But that log is refreshed merely by my
    typing `./bin/brain-vec-daily` in a terminal — and then the diagnostic goes ★green★ even with the
    schedule dead. A probe that cannot fail on its own is not a probe.

    So three signals are read separately:
      · `ran_days`   did the job run recently (including by a human)
      · `auto_days`  did ★the scheduler★ run it recently (age of the scheduler-only stdout log —
                     running it from a terminal does not touch this file)
      · `runs`       how many times the scheduler has run this job (`0` = registered only)

    ★Green depends on `auto_days` alone.★ The other two explain why it is red.
    """
    import os
    import time as _t
    now = now or _t.time()

    def _age(path):
        try:
            return (now - os.path.getmtime(os.path.expanduser(path))) / 86400.0
        except OSError:
            return None

    # ⛔ `_probe` and `_jobs` are ★injection points for tests★ — this judgement is tied to the real
    #    scheduler and real file ages, and without injection there is no way to measure "did it really block a false green".
    probe = _probe or _launchd
    out = []
    for name, log_tpl, what, every, label in (_jobs or SCHEDULED):
        log = _log_path(log_tpl)                     # ⛔ the home is decided by §store.brain_home
        ld = probe(label)
        ran = _age(log)
        auto = _age(ld["stdout"]) if ld.get("stdout") else None
        cal = _calendar_run(log, ld.get("at"), now)
        row = {"job": name, "what": i18n.t(what), "log": log, "label": label,
               "registered": ld["known"], "runs": ld["runs"],
               "at": ld.get("at"), "wired": None,
               "cal_days": None if cal is None else round(cal, 1),
               "ran_days": None if ran is None else round(ran, 1),
               "auto_days": None if auto is None else round(auto, 1),
               "days_since": None if ran is None else round(ran, 1)}

        if not ld["known"]:
            # ⛔ A machine where the scheduler cannot be queried (not macOS · using cron) — fall back to the old judgement.
            #    ★Never claim to know what we do not★: leave `by` as "unknown".
            row["by"] = "unknown"
            row["ok"] = ran is not None and ran <= every + 1.0
            row["why"] = "" if row["ok"] else (
                "★has not run for %.1f days★ — the schedule has stopped" % ran if ran is not None
                else "⛔ there is no log at all — it has never run")
            out.append(row)
            continue

        # ★Green depends on one thing — did it fire ★on its own★ at the scheduled time.★
        #    The wiring (can the scheduler launch it) is reported separately. Counting a hand-woken
        #    run as "runs on its own" makes this axis measure nothing.
        row["wired"] = auto is not None and auto <= every + 1.0
        code = _exit_code(ld["last_exit"])
        row["last_exit"] = code

        # ★Green only when both pieces of evidence are present ★together★★ (2026-09-01)
        #   · cal   = the job started after the scheduled time (the job log writes this itself)
        #   · wired = the scheduler launched it recently (the scheduler-only stdout log)
        #  ⛔ One alone is not enough — widening the window created a regression where ★a fresh job log
        #     alone★ turned it green (running the script directly from a terminal). A check caught it.
        #  ⚠️ Limit: `launchctl kickstart` (a human telling the scheduler to run it) ★cannot be
        #     distinguished★ from a catch-up — the scheduler does not report that. But it is an
        #     exceptional action, and green on a day without a kickstart is the evidence.
        if cal is not None and cal <= every + 1.0 and row["wired"]:
            row["by"] = "scheduled"
            if code not in (None, 0):
                # ⛔ ★"the scheduler called it" is not "the job succeeded"★ — the job writes its start
                #    line to stdout the moment it runs (§jobs._say), so that log is fresh even if the job
                #    died halfway. ⚠️ launchd itself only ★opens★ the file: with no write its clock does
                #    not move — that is how a silent wrapper read as "not run for 2.1 days" (2026-09-30).
                row["ok"] = False
                row["why"] = ("⛔ it did run at the scheduled time but ★the job failed★ "
                              "(exit code %d) — read the log: %s" % (code, log))
            else:
                row["ok"] = True
                row["why"] = ""
        elif row["wired"]:
            # The scheduler did launch it — either woken by hand (kickstart) or catching up after waking.
            # ★The wiring is alive; a scheduled firing has not been seen yet.★
            row["by"] = "wired-only"
            row["ok"] = False
            row["why"] = ("★the wiring is alive★ (the scheduler launched it %.1f days ago%s) — but "
                          "an automatic firing after the scheduled %02d:%02d has ★not been seen yet★. "
                          "The next %02d:%02d is the first verification."
                          % (auto, "" if code in (None, 0) else " · exit code %d" % code,
                             ld["at"][0], ld["at"][1], ld["at"][0], ld["at"][1])
                          if ld.get("at") else
                          "★the wiring is alive★ — a scheduled firing has not been seen yet")
        elif ld["runs"] == 0:
            row["by"] = "never"
            row["ok"] = False
            row["why"] = ("⛔ registered, but the scheduler has ★never run it★ "
                          "(runs=0) — either the first scheduled time has not come, or it does not fire")
        else:
            row["by"] = "never"
            row["ok"] = False
            row["why"] = ("★the scheduler has not run it %s★"
                          % ("for %.1f days" % auto if auto is not None else "recently"))

        if (not row["ok"] and row["by"] != "scheduled"
                and ran is not None and ran <= every + 1.0 and not row["wired"]):
            row["why"] += ("  · the job log is fresh, %.1f days old — ★that was a human running it by "
                           "hand, not evidence that the schedule works★" % ran)
        out.append(row)

    bad = [r for r in out if not r["ok"]]
    return {"jobs": out, "stale": len(bad),
            "meaning": "if the scheduled jobs stop, ★the entire self-improving layer stops★ "
                       "(ingest · rule discovery · lexicon self-assessment · threshold calibration) — "
                       "⛔ green means ★the scheduler ran it★, not that the log is fresh"}
