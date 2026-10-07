"""Automatic threshold calibration — ★makes the hook work on someone else's corpus too★.

## Why it is needed (a release-blocking defect found in a clean-room test)

The hook threshold was hardcoded to **10.0** — a good value, measured on my own corpus (867
documents). Then a fresh-user environment with 8 documents was tested and ★the hook never fired★:
the correct document scored 3.87.

The cause is IDF: `idf = log(1 + (N-df+0.5)/(df+0.5))` **grows with N (the document count)**.
The same strength of match scores lower on a smaller corpus. That is, ★the score has a different
scale on every corpus★.

A hardcoded absolute fails in two directions:
  · small corpus → permanent silence (the memory exists and never surfaces)
  · very large corpus → fires too often (noise mixes into judgement)

So ★fix the **method of measuring**, not the value★. On every index, measure that corpus's
**noise floor** and place the threshold above it.

## How the noise floor is measured — two kinds of "matched but meaningless" query

① **domain-neutral sentences** — everyday matters (lunch, weather, travel, pets). They barely
   match any technical corpus. And if someone's corpus is cooking notes? Then the floor is set
   high and the hook becomes conservative — ★a failure in the safe direction★.

② **common-word queries** — built from the highest-df words in that corpus. The corpus itself
   produces the worst case of "matched on the surface with no discriminating power".
   This catches **noise from inside the domain**, which ① cannot.

threshold = max(①,②) × margin. The margin (1.35) was back-computed from my corpus:
measured noise floor 7.41 · adopted threshold 10.0 → ratio 1.35.

## ③ A positive control — ★a calibration that measures only noise cannot disprove itself★ (added 2026-08-11)

However precisely the floor is measured, **whether there is signal above it is a separate question**.
If the signal is below the threshold the hook dies without a sound and the diagnostics say only
"calibrated". So prompts are drawn from real history and **what share fires at this threshold** is
measured alongside; out of range, that fact rides along in the result (the value is not changed —
a human judges).

Measured (2026-08-11 · 905 documents · 300 real prompts):
  noise ① max 9.19 · ② max 3.48 → threshold 12.41
  10 hand-picked "real utterances needing no memory" max 8.77 — all below the threshold ✅
  real prompts, top score p50 11.3 · firing rate 43–47%

★This is what an earlier memory system could not do★ — being cosine similarity, the same value there meant
opposite things (0.730 correct · 0.650 rubbish · 0.647 correct). With IDF-weighted BM25 the score
**is the strength of the signal itself**, so a threshold stands between noise at 8.77 and signal at
11.3. ⛔ But **only for long prompts** — a short one-line question has few words and scores low, and
that place is still empty.
"""
from __future__ import annotations

import json
import os
import sys
import sqlite3
import time
from typing import List

from brain import i18n, langdata, search, store

# Domain-neutral control queries — everyday sentences that barely occur in a technical corpus.
#
# ⛔ ★These must be in the language of the corpus★ (2026-09-03). The noise floor is "what does a
#    meaningless query score here", and a sentence in a language the corpus does not use scores near
#    zero — the floor collapses, the threshold follows it down, and the hook becomes noise. The six
#    sentences live in the message catalogs (`calib.noise.1..6`, one set per language).
# ⛔ ★Every language's six are used, never just the screen language's★ (2026-10-06). Choosing them
#    by the screen language was a proxy for "the corpus language" that only held while the screen
#    followed the OS locale — and even then not for a process without LANG (a scheduled job, another
#    host). Measured on one Korean corpus, same code, same minute: ko set → floor 7.17, threshold 9.68
#    · en set → floor 5.99, threshold 8.08. That pair ★is★ the 9.6x ↔ 8.0x flapping in
#    `hook_threshold_history` (09-30 → 10-06): whichever process calibrated last decided which set won.
#    So the languages are ★read off the corpus★ (§corpus_languages) — no setting, on anyone's machine.
#    ⛔ Not "every language" either: see `langdata.LANG_MARKERS` for the Spanish control that scored
#    10.90 on a Korean corpus through function words alone.
_NEUTRAL_EN: List[str] = [
    "what should i cook for dinner tonight with potatoes",
    "how long does it take to fly from seoul to paris",
    "my houseplant leaves are turning yellow what do i do",
    "best way to tie shoelaces so they do not come undone",
]


# ★Declared for the key scanner★ — the six keys are built at runtime below, so `verify_i18n` cannot see
# them through `t("...")` and reported them unused (2026-09-08). Deleting them on that word would have
# collapsed the noise floor for every non-English corpus. Same declaration shape as the dashboard's UI_PREFIX.
I18N_PREFIX = ("calib.noise.",)


# A language is ★in the corpus★ when the median document share of its markers reaches this (%).
# Measured 2026-10-06 on 1,818 documents (Korean prose, English identifiers): ko 50.9 · en 26.0 ·
# de · es · fr · ja 0.1 each — two orders of magnitude apart, so the bar sits in the empty middle.
LANG_PRESENT_PCT = 5.0


def language_shares(db: sqlite3.Connection) -> dict:
    """{language: median % of documents containing its markers} — what the corpus is written in."""
    n = max(1, store.corpus_stats(db)["docs"])
    out = {}
    for code, words in langdata.LANG_MARKERS.items():
        pcts = sorted(100.0 * _df(db, w) / n for w in words)
        mid = len(pcts) // 2
        out[code] = round(pcts[mid] if len(pcts) % 2 else (pcts[mid - 1] + pcts[mid]) / 2, 1)
    return out


def corpus_languages(db: sqlite3.Connection) -> List[str]:
    """The languages this corpus is written in — ★read off the corpus, never off a setting★.

    Nothing clears the bar (a tiny or brand-new corpus) → the strongest one if it has any trace,
    else English. ⛔ Never the screen language: that is the dependency this replaced.
    """
    share = language_shares(db)
    present = [c for c in i18n.LANGS if share.get(c, 0.0) >= LANG_PRESENT_PCT]
    if present:
        return present
    best = max(i18n.LANGS, key=lambda c: share.get(c, 0.0))
    return [best] if share.get(best, 0.0) > 0 else [i18n.DEFAULT]


def _df(db: sqlite3.Connection, term: str) -> int:
    row = db.execute("SELECT df FROM terms WHERE term=?", (term.lower(),)).fetchone()
    return int(row[0]) if row else 0


def neutral_probes(db: sqlite3.Connection) -> List[str]:
    """The domain-neutral control set: the six of ★each language the corpus is written in★, plus
    four English.

    ⛔ Independent of the screen language — read straight from each catalog, not through `i18n.t`
       (which answers in whatever language this process happened to resolve).
    """
    out: List[str] = []
    for code in corpus_languages(db):
        cat = i18n.catalog(code)
        for i in range(1, 7):
            val = cat.get("calib.noise.%d" % i)
            if val and val not in out:
                out.append(val)
    return out + [q for q in _NEUTRAL_EN if q not in out]

# Margin placed above the noise floor — ★the prior★ of a per-person filter (§brain/adaptive.py). Chosen once on
# a reference corpus (the author's notes: floor 7.41 → 10.0 adopted); each person's own sample then moves it,
# measure-only at first, weighted by how much each observation can be trusted (a Kalman filter — not a sweep
# that keeps the best, which would be choosing a threshold with the sample it is judged on). A person who
# wants a fixed one can say so: BRAIN_CALIB_MARGIN.
MARGIN = float(os.environ.get("BRAIN_CALIB_MARGIN", "") or 1.35)

# However small the corpus, the threshold never goes below this — near 0 it becomes
# "fire on anything that matches" and the hook turns into noise.
FLOOR_MIN = 1.5

# ★An absolute bar for "common"★ — a word must appear in at least this share of documents.
# Taken by relative rank (top N by df), a small corpus mistakes a rare word for a common one.
COMMON_DF_RATIO = 0.25

# Must equal search.IDF_CAP_DF — every df below it receives the same IDF, so the lower bound of
# "common" only means something if it sits above that.
IDF_CAP_DF = search.IDF_CAP_DF

# The normal range for the positive control — outside it, the threshold either missed the signal
# or drowned in noise. Measured on the reference corpus (2026-08-11, 904 documents, 300 real prompts): 43%.
# ⛔ ★Not learned from a person's own firing history★ — if the ruler drifts into noise the history drifts
#    with it, and the band would follow the drift it exists to catch. It is how often a memory hook should
#    speak, which a person may set: BRAIN_FIRE_RATE_OK="15,75".
def _band(raw: str, default: tuple) -> tuple:
    try:
        lo, hi = (float(x) for x in raw.split(","))
        return (lo, hi) if 0 <= lo < hi <= 100 else default
    except ValueError:
        return default


FIRE_RATE_OK = _band(os.environ.get("BRAIN_FIRE_RATE_OK", ""), (15.0, 75.0))

_META_KEY = "hook_threshold"
_META_DETAIL = "hook_threshold_detail"
_META_HISTORY = "hook_threshold_history"

# ★How many rounds to keep★ — just enough to attribute a score change. Unbounded, meta grows.
HISTORY_KEEP = 60


def _remember_move(db: sqlite3.Connection, result: dict) -> None:
    """★A record of the ruler moving★ — one line, only when the value changes.

    ⛔ Why it is needed (burnt on 2026-09-01): this threshold re-sets itself on every index, on top
    of ★real prompts★. That is, **having conversations moves the ruler.** But saving was an
    overwrite, so yesterday's and this morning's values were gone, and when the automatic axis fell
    20/57 → 19/57 it was ★impossible to prove whether the ruler or the corpus caused it★ (it had to
    be back-computed from hit counts).

    ★A self-moving ruler with no record of its movements makes score changes unattributable.★
    """
    try:
        hist = json.loads(store.get_meta(db, _META_HISTORY, "") or "[]")
        if not isinstance(hist, list):
            hist = []
    except (ValueError, TypeError):
        hist = []
    cur = round(float(result.get("threshold") or 0), 2)
    if hist and round(float(hist[-1].get("threshold") or 0), 2) == cur:
        return                                       # nothing written if it did not move
    hist.append({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "threshold": cur,
        "docs": result.get("docs"),
        "prompts": result.get("real_prompts"),
        "fire_rate_pct": result.get("fire_rate_pct"),
        # ★Who measured it★ (2026-10-06) — the 9.6x ↔ 8.0x flapping went unexplained for a week
        # because a line said only ★what★ moved. Which control languages and which code generation
        # wrote it, and from which entry point, make the next disagreement explain itself.
        "langs": result.get("control_languages"),
        "gen": CODE_GENERATION,
        "via": os.path.basename(sys.argv[0] or "") or "?",
    })
    store.set_meta(db, _META_HISTORY, json.dumps(hist[-HISTORY_KEEP:], ensure_ascii=False))


def history(db: sqlite3.Connection) -> list:
    """When and to what the ruler moved — ⛔ contains ★only the rounds where it changed★."""
    try:
        h = json.loads(store.get_meta(db, _META_HISTORY, "") or "[]")
        return h if isinstance(h, list) else []
    except (ValueError, TypeError):
        return []


def _common_word_queries(db: sqlite3.Connection, n_docs: int,
                         n_queries: int = 8) -> List[str]:
    """A query built from words that are **genuinely common** in that corpus — the worst case of zero discrimination.

    ⛔ ★"The highest-df words" are not common words★ (burnt in the 2026-08-10 clean room)
    Running this control on an 8-document corpus produced **2.64**, which pushed the floor up, and
    the correct query (2.43) fell below the threshold so the hook went permanently silent.
    The reason: in 8 documents, df=2 still discriminates (and with `IDF_CAP_DF=3`, df 1, 2 and 3 all
    receive **the same maximum IDF**). That is, "relatively common" was mistaken for "absolutely common".

    So ★the bar is set as an absolute★ — only words appearing in at least a set share of documents
    count as common. With no such word, **this control does not hold and is not used**
    (measuring with one piece of evidence beats inventing evidence that is not there).
    """
    min_df = max(IDF_CAP_DF + 1, int(n_docs * COMMON_DF_RATIO))
    rows = db.execute(
        "SELECT term FROM terms WHERE length(term) >= 2 AND df >= ? "
        "ORDER BY df DESC LIMIT 40", (min_df,)).fetchall()
    words = [r["term"] for r in rows]
    if len(words) < 4:
        return []
    out = []
    for i in range(min(n_queries, max(1, len(words) // 4))):
        out.append(" ".join(words[i * 4:(i + 1) * 4]))
    return [q for q in out if q.strip()]


def _real_prompts(limit: int = 120) -> List[str]:
    """★The positive control★ — real prompts drawn from actual conversation history.

    Never invented. On 2026-08-11 I measured with 10 short questions I had made up and raised a false
    alarm that "the hook never fires". Re-measured against real history, the median was 11.3
    (the synthetic questions were 4.8–9.1).
    ⛔ **Invent the evaluation set and what you measure is your imagination, not the system.**

    With no history it returns an empty list — evidence that is absent is not invented.
    """
    # ⛔ ★Which tool the history belongs to is not this function's business★ (2026-09-02) — the brain
    #    must attach to more than one coding agent (a user requirement). How to read history differs
    #    per host, so the `hosts` adapter answers that and this function only ★filters★.
    import random as _random

    from brain import hosts
    raw = hosts.active().prompts(limit=limit * 8)
    out: List[str] = []
    for c in raw:
        c = (c or "").strip()
        # ⛔ Keep only ★natural language a human typed★ — slash commands, shell and system notices are not controls
        if 12 <= len(c) <= 600 and not c.startswith(("/", "!", "<")) \
                and "system-reminder" not in c:
            out.append(c)
    out = list(dict.fromkeys(out))
    if not out:
        return []
    _random.seed(7)
    return _random.sample(out, min(limit, len(out)))


def _quotes_the_probe(db: sqlite3.Connection, name: str, probe: str) -> bool:
    """★Does this document quote the control sentence itself★ (from the 2026-08-12 incident)

    Write a noise control into a document and calibration **finds itself**. It happened:
    a memory file explaining this very defect quoted the control sentence verbatim, and that
    query's top result became 15.91 (next 6.83), so ★the threshold jumped 9.28 → 34.51★.
    At that value the hook is effectively dead — and it dies **quietly, with no error**.

    No proxy metric such as an outlier ratio. The cause is known, so **check the cause
    directly**: is the query sentence present verbatim in that document's body?
    """
    row = db.execute("SELECT body FROM docs WHERE name=?", (name,)).fetchone()
    if not row or not row["body"]:
        return False
    return probe.lower() in row["body"].lower()


def measure(db: sqlite3.Connection) -> dict:
    """Measure the noise floor and compute the threshold. Stores nothing (a pure measurement)."""
    self_quoted: List[str] = []

    def top(q: str, guard: bool = True) -> float:
        # ★Skip documents quoting a control★ — that score measures "did we leak our own exam
        # paper into the corpus", not "how much noise matches".
        #
        # ⛔ ★This guard applies to the noise control only★ — never to the positive control
        #   (real prompts). It is **normal** for what a user said to be written into a memory,
        #   and when they say it again that document surfacing is the real behaviour.
        #   Excluding that too measures the firing rate lower than reality (measured: one real
        #   prompt was wrongly excluded as a quotation of the memory it had produced).
        # ⛔ Keep k generous — as documents **explaining** this incident accumulate, so do
        #   quoting documents (currently 2). With a small k they are all skipped and 0.0 is
        #   returned, the floor becomes 0, the threshold falls to FLOOR_MIN, and **the
        #   opposite** failure explodes: noise.
        # ⛔ ★The maximum score, not the top-ranked one★ (2026-08-19) — the hook fires by
        #   `[r for r in rows if r["score"] >= min_score]`, looking at **all** results.
        #   Until 2026-08-12 the two were identical. The 2026-08-18 aboutness fix made the
        #   identity bonus apply to ranking only (§search.ID_STAGE), so ★rank 1 stopped being
        #   the maximum★, and from then this function reported a floor below the score the
        #   hook could actually fire at. A low floor means a low threshold, and noise leaks in.
        best = 0.0
        for r in search.recall(db, q, k=8, log=False, graph=False):
            if guard and _quotes_the_probe(db, r["name"], q):
                self_quoted.append("%s ← %s" % (r["name"], q[:30]))
                continue
            best = max(best, r["score"])
        return best

    neutral = [top(q) for q in neutral_probes(db)]
    n_docs = store.corpus_stats(db)["docs"]
    common_qs = _common_word_queries(db, n_docs)
    common = [top(q) for q in common_qs]

    floor = max([0.0] + neutral + common)
    # ★The margin is learned per person★ (§brain/adaptive.py) — the prior 1.35 until enough observations
    from brain import adaptive
    margin = adaptive.current(db)
    threshold = max(FLOOR_MIN, round(floor * margin, 2))

    # ★The positive control — what share of real prompts fires at this threshold★
    # ⛔ A calibration measuring only noise **cannot disprove itself**. However precisely the
    # floor is measured, whether signal sits above it is a separate question, and with no
    # signal the hook dies quietly. The value is not changed — anything odd rides along.
    real = _real_prompts()
    # ★Keep the scores, not just the count★ (2026-09-09) — the same pass now also tells the guard
    # what the firing rate would be at ★any other★ threshold, which is what decides whether a
    # label-free sample is allowed to veto an increase (§proxy.may_judge). Recomputing it later
    # would mean another 120 recalls; this costs nothing extra.
    real_tops = [top(q, guard=False) for q in real]
    fire_rate = round(100.0 * sum(1 for s0 in real_tops if s0 >= threshold) / len(real), 1) if real else None
    out = {
        "threshold": threshold,
        "noise_floor": round(floor, 2),
        "neutral_max": round(max(neutral) if neutral else 0.0, 2),
        "common_word_max": round(max(common) if common else 0.0, 2),
        "margin": margin,
        "_floor": floor,
        "docs": n_docs,
        "common_control_used": bool(common_qs),
        "control_languages": corpus_languages(db),
        "real_prompts": len(real),
        "fire_rate_pct": fire_rate,
        # ⛔ Popped by the caller before the record is stored — 120 numbers do not belong in `meta`.
        "_real_tops": real_tops,
    }
    # ★Never exclude quietly★ — excluding is right, but the fact that the exam paper leaked
    # into the corpus has to be known before it can be fixed.
    if self_quoted:
        out["self_quoted_probes"] = sorted(set(self_quoted))
    if fire_rate is not None and not (FIRE_RATE_OK[0] <= fire_rate <= FIRE_RATE_OK[1]):
        out["warning"] = (
            "real-prompt firing rate %.1f%% — expected %d-%d%%. %s"
            % (fire_rate, FIRE_RATE_OK[0], FIRE_RATE_OK[1],
               "the threshold sits above the signal (the hook goes silent)" if fire_rate < FIRE_RATE_OK[0]
               else "the threshold drowned in noise (the hook becomes noise)"))
    return out


# ★The generation of the measuring code★ — the ordering key for "may this process overwrite the
# stored ruler". ⛔ Bump it whenever a change moves the numbers `measure()` / `_bench()` produce
# (that is: any behaviour change inside `RULER_MODULES`). `tests/verify_code_generation.py` fails
# if you forget, so this is a checked rule, not a hope.
#
#   1 (2026-09-09)  the first explicit generation.
#       ⛔ Before this the order was ★the newest mtime among brain/*.py★, and that ★locked a
#       legitimate copy out forever★: `git worktree add`, a re-clone and `pip install .` all stamp
#       every file with "now", so whichever copy was created ★last★ outranked the others regardless
#       of what code it actually held. Measured 2026-09-09 — one comparison worktree, run once,
#       froze this machine's threshold (every later calibration discarded with one stderr line) and
#       dropped the scorecard's autonomy axis 66.7 → 33.3. mtime says ★when a file was written to
#       disk★, never ★which generation of the code it holds★, and those two come apart the moment
#       a second copy exists — which the release path (`pip install .`, §8-c) guarantees.
#   2 (2026-09-09)  the guard can now run on a ★label-free proxy sample★ (§brain/proxy.py), so a
#       machine with no gold is no longer unprotected. `_bench` takes the sample as an argument and
#       `bench_sample()` decides which one applies — the labelled numbers are unchanged, but the
#       module set moved, and this rule errs toward bumping rather than toward guessing.
#   3 (2026-09-10)  `evalinit` stopped hard-coding the evaluation directory as a repo-relative path.
#       In an installed copy that resolved to ★site-packages/tests/eval★ — outside the package,
#       wiped by an upgrade, often unwritable, and `brain eval-init` would have ★written★ a person's
#       labelled sample there. It now follows the same order as `store.default_config_path`
#       (BRAIN_EVAL_DIR → beside the repository → brain_home()/eval). Which sample a machine loads
#       is exactly what decides the ruler, so this is a generation.
#   4 (2026-09-29)  `hook` gained a watchdog that ends the process before the host's wall, and its
#       `main()` was split (`_answer` · `_with_triggers` · `_record`). `_bench` reaches only
#       `hook.select`, which did not change, so no number is expected to move — but the module's
#       code did, and as with 2, this rule errs toward bumping rather than toward guessing.
#   5 (2026-10-06)  the noise control is ★every language's six★, not the screen language's
#       (§neutral_probes). On a Korean corpus a process resolving to English measured 8.08 where one
#       resolving to Korean measured 9.68 — the numbers moved, and a gen-4 process without LANG must
#       not keep writing the collapsed one over this.
#   6 (2026-10-06)  `store` gained `adopt_home_from_argv` — the behaviour hook's `--home`, so its shell
#       and Python halves read one home. No measurement path calls it and no number is expected to
#       move; bumped by the same rule as 2 and 4 (the module's code changed).
#   7 (2026-10-06)  which filename prefixes are kinds is ★learned from the corpus★
#       (§store.learn_kind_prefixes) instead of a fixed list of four. On the author's corpus the
#       name→document map is the old one plus one name (`user_` was learned), so no number is
#       expected to move; bumped because `store` changed.
#   8 (2026-10-06)  `langdata` lost the author's section headings and its condition markers became one
#       table per language (read by `indexaudit` only). No measurement path reads either; bumped
#       because `langdata` is a ruler module and its code changed.
#   9 (2026-10-06)  host and path neutrality: `store.index_file_name` split out, `lexicon` finds the eval
#       sample through `evalinit.eval_dir`, `hook` prints through the host adapter. The sample file and
#       the hook's selection are unchanged; bumped because ruler modules changed.
#   10 (2026-10-06)  the host decides what is ★already in context★ (`search._always_in_context`, the
#       auto-loaded index) and which prompts are ★its own commands★ (`hook`). On Claude Code both are the
#       old values ({"MEMORY"} · `/ ! @ #`); bumped because ruler modules changed.
#   11 (2026-10-06)  numbers given owners: no ruler → `NO_RULER` (was 10.0) · the deploy margin counted in
#       hits (0.020 → 0.026 on the author's 57-item sample) · the relative-rule floor as a share of the
#       threshold (author's labelled set unchanged: 15/57 · 3/34) · staleness from config/host · "short"
#       from the person's prompts. ★Numbers can move★ — measure after this, not before.
#   12 (2026-10-06)  the fingerprint rule ignores blank lines (deleting a comment-only line no longer moves
#       it). The only code change in the ruler modules; no measurement path changed.
#   13 (2026-10-06)  ★the margin is learned per person★ (§brain/adaptive.py — a Kalman filter, measure-only for
#       the first observations, then used). The threshold can move once the filter deploys.
#   14 (2026-10-07)  a measurement no longer changes the index under everyone else: `lexicon.decide`
#       tries its settings in its own process (§lexicon._TRIAL) instead of the shared meta, and
#       `store._init` replaces `name_map` only when its definition changed, inside one transaction.
#       The decision's own result is unchanged (not turned on · off 28/15/3 on the author's copy) and
#       no number is expected to move; bumped because ruler modules changed.
#   15 (2026-10-07)  the margin filter weighs an observation by its ★novelty★ — the share of questions that
#       answered differently from the last observation it used (§adaptive). Three observations in seven
#       minutes on almost the same notes had each counted in full; a state without the novelty total
#       goes back to measuring only. ★The threshold can move★ (back towards the prior until warmed up).
#   16 (2026-10-07)  the bridge cache is keyed by (word, generation) in a new table — a long-running process
#       of another generation could replace the current generation's row of a word. No bridge rule changed;
#       bumped because `translit` changed (and the new table starts empty either way).
#   17 (2026-10-07)  graph traversal and similarity search go through ★a chosen store★ (§stores · §graphstore ·
#       §vecstore). With the default (sqlite) the questions are the same SQL as before; neighbours tied on
#       score are now taken in document-id order (it was SQLite's row order, which no other store can repeat).
#   18 (2026-10-07)  a sync compares what a dedicated database holds with what its ledger says it was sent and
#       refills it from the local copy when they differ; an answer of "not found" marks the target out of sync.
#       No question is answered differently; bumped because `stores` and `graphstore` changed.
CODE_GENERATION = 18

# ★What actually decides the ruler★ — the modules `measure()` and `_bench()` reach through.
# Read off the call graph, not guessed: measure → search.recall → textindex · translit · lexicon ·
# store(postings/idf) ; _bench → hook.select · evalinit.gold_hit ; neutral_probes → langdata (markers).
# ⚠️ Known gap, on purpose: the noise-control sentences live in the message catalogs (`calib.noise.*`),
#    so editing those moves the floor without moving this fingerprint. They are frozen control
#    text that nobody edits casually, and `_bench` still guards the deployment.
RULER_MODULES = ("adaptive", "calibrate", "evalinit", "graphstore", "hook", "langdata", "lexicon", "search",
                 "store", "stores", "textindex", "translit")


def fire_rate_at(real_tops, threshold: float):
    """What share of this user's real prompts would fire at `threshold` · None with no prompts.

    ★A label-free reading of "is the current ruler too noisy or too quiet"★ — the band it is judged
    against, `FIRE_RATE_OK`, is this project's own and predates the proxy.
    """
    if not real_tops:
        return None
    return round(100.0 * sum(1 for s in real_tops if s >= threshold) / len(real_tops), 1)


def code_stamp() -> int:
    """The newest mtime among brain/*.py. ⛔ ★Diagnostic only — never an ordering key★ (see
    `CODE_GENERATION`). Kept because the stored record still carries it so a human can tell
    ★which copy on disk★ wrote a ruler, and because a pre-2026-09-09 process still reads it."""
    import glob
    here = os.path.dirname(os.path.abspath(__file__))
    best = 0
    for f in glob.glob(os.path.join(here, "*.py")):
        try:
            best = max(best, int(os.path.getmtime(f)))
        except OSError:
            continue
    return best


_FINGERPRINT_MEMO = None            # (cheap signature, fingerprint) — see `code_fingerprint`


def code_fingerprint() -> str:
    """★Which code★ this process holds — a hash of `RULER_MODULES`' source, ★identity, not order★.

    ⛔ ★Memoised per process behind a stat-only signature★ — reading and tokenising these eight
    modules measures ★49ms★ (2026-09-09), and the first version of this function was called from
    the transliteration bridge on every query, whose ★whole budget is 60ms★. The signature
    (name · size · mtime) costs eight `stat` calls, so a repeat call is free and a genuinely
    changed file is still picked up inside one long-running process.

    ⛔ Comments and docstrings are stripped first (`i18n._code_only`, the same stripper the key
    scanner uses). This repository's design comments are long and edited constantly; hashing them
    would make the generation check cry wolf on every comment fix, and ★a check that cries wolf
    gets ignored★ — which is how the review-gate failure in this repo's own memory happened.
    """
    global _FINGERPRINT_MEMO
    here = os.path.dirname(os.path.abspath(__file__))
    sig = []
    for name in RULER_MODULES:
        try:
            st = os.stat(os.path.join(here, name + ".py"))
            sig.append((name, int(st.st_size), int(st.st_mtime)))
        except OSError:
            sig.append((name, -1, -1))
    key = tuple(sig)
    if _FINGERPRINT_MEMO is not None and _FINGERPRINT_MEMO[0] == key:
        return _FINGERPRINT_MEMO[1]
    sources = {}
    for name in RULER_MODULES:
        try:
            with open(os.path.join(here, name + ".py"), encoding="utf-8") as fh:
                sources[name] = fh.read()
        except OSError:
            sources[name] = ""                       # a missing module is itself a distinguishing fact
    _FINGERPRINT_MEMO = (key, fingerprint_of(sources))
    return _FINGERPRINT_MEMO[1]


def fingerprint_of(sources: dict) -> str:
    """The hashing rule itself — `{module name: source text}` → fingerprint.

    ⛔ Public so `tests/verify_code_generation.py` can fingerprint ★the committed version★ with the
    exact same rule. Two copies of a hashing rule drift, and this repository's own ⛔ rule is that
    a rule written in two places gets fixed in one.
    """
    import hashlib
    h = hashlib.sha256()
    for name in RULER_MODULES:                       # ★fixed order★ — the tuple is a literal
        body = i18n._code_only(sources.get(name) or "", name + ".py")
        # ⛔ ★Blank lines are layout, not code★ (2026-10-06) — the stripper turns a comment into blanks and
        #    keeps the line, so deleting a comment-only line moved the hash and the check cried wolf on a
        #    comment fix, the very thing the stripping exists to prevent.
        body = "\n".join(line.rstrip() for line in body.splitlines() if line.strip())
        h.update(name.encode())
        h.update(b"\0")
        h.update(body.encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()


def code_identity() -> dict:
    """(generation · fingerprint · mtime) — what gets written next to a measured ruler."""
    return {"gen": CODE_GENERATION, "fp": code_fingerprint(), "mtime": code_stamp()}


def code_cache_key() -> int:
    """The cache generation for anything that stores a ★computed rule result★ (§translit).

    It is simply `CODE_GENERATION`, and that is the point: "the rules changed" is now a ★declared
    and checked★ fact rather than one inferred from the filesystem.

    ⛔ Why not the fingerprint: this is read on the query path and hashing costs 49ms against a
       60ms budget. Why not mtime (what it used to be): a re-clone or a worktree changes every
       file's clock without changing one rule, and threw away a warm cache that was still correct.
    ⛔ The discipline that makes this safe: `translit` is in `RULER_MODULES`, so changing its rules
       without bumping `CODE_GENERATION` ★fails `tests/verify_code_generation.py`★. If you edit the
       bridge rules and measure before bumping, you are reading last generation's cache — the exact
       disease the `code` column was added for on 2026-08-20. Bump first, then measure.
    """
    return int(CODE_GENERATION)


def older_ruler(db: sqlite3.Connection, mine: dict) -> str:
    """`''` when this process may write the ruler · otherwise ★why it must not★.

    ⛔ The rule this protects (measured 2026-08-19): a stale MCP process from another session wrote
    threshold 32.07 and the hook went ★completely silent★. It held the code it started with, so it
    measured the noise floor with pre-transliteration scores. The corpus-drift check cannot see
    that — it was the ★code★ that differed. So writes stay monotonic: an older generation never
    overwrites a newer one (the same axis as never lowering SCHEMA_VERSION).
    ⛔ Do not "re-measure when they differ" instead — two processes would ping-pong, 3.3s each.

    ★Equal generations always pass★, whatever the file times or the install path say. That is the
    whole 2026-09-09 fix: two copies of the ★same★ code are the same ruler, and one must never
    silence the other.
    ⛔ A record written before generations existed has no `code_id`; it counts as generation 0, so
    the first write by this code upgrades it — which also un-sticks a machine already locked out.
    """
    try:
        prev = json.loads(store.get_meta(db, _META_DETAIL, "") or "{}")
        got = prev.get("code_id")
        if not isinstance(got, dict):
            return ""
        stored_gen = int(got.get("gen") or 0)
    except (ValueError, TypeError, AttributeError):
        return ""                                    # unreadable record → do not block on it
    if stored_gen > int(mine.get("gen") or 0):
        return "%s < %s" % (mine.get("gen"), stored_gen)
    return ""


_META_BLOCKED = "hook_threshold_blocked"

# Do not deploy a new value that is ★this much worse★ — counted in ★hits★, so it means the same on any
# sample: losing one hit passes, losing two blocks (1.5 is the midpoint), and a single false firing does
# not block. ⛔ It used to be `0.02` — one hit in the author's 57-item sample — which on a 30-item proxy
# sample blocked the loss of a single hit (2026-10-06; on the author's sample it moves 0.020 → 0.026).
DEPLOY_HITS = 1.5


def deploy_margin(n_a: int) -> float:
    return DEPLOY_HITS / max(1, n_a)


MIN_A, MIN_C = 30, 20                             # a sample smaller than this judges nothing


def bench_sample(db: sqlite3.Connection):
    """(positives, controls, kind) — ★which sample this machine can be judged on★.

    `kind` is `"labelled"` (this person's own gold), `"proxy"` (built from their corpus with no
    labels — §brain/proxy.py) or `"none"`. ⛔ The three are ★not interchangeable★ and the caller has
    to look at `kind`: a proxy may only judge one direction, which is why this returns the kind
    rather than quietly handing back a sample and letting everyone assume it is gold.
    """
    from brain import evalinit as ei                  # ⛔ local import — a module-level one would cycle
    A, C = ei.load_A(), ei.load_C()
    if len(A) >= MIN_A and len(C) >= MIN_C:
        return A, C, "labelled"
    try:
        from brain import proxy
        pa, pc = proxy.sample(db)
        if len(pa) >= proxy.MIN_A and len(pc) >= MIN_C:
            return pa, pc, "proxy"
    except Exception:                                 # noqa: BLE001
        pass                                          # a corpus too small for a proxy is not an error
    return [], [], "none"


def _bench(db: sqlite3.Connection, thresholds, pairs=None, ctrl=None,
           min_a: int = 0, min_c: int = 0) -> dict:
    """Measure (hits · false firings · automatic) at each threshold on a fixed sample. ⛔ No remote calls.

    ★Recall is independent of the threshold★, so each query runs once and only the threshold is swapped (91 runs ≈ 2.3s).
    ⛔ One implementation, whichever sample is passed — the labelled one and the proxy must be
    measured by ★the same arithmetic★ or their verdicts cannot be compared at all.
    """
    from brain import evalinit as ei, hook            # ⛔ local import — a module-level one would cycle
    if pairs is None or ctrl is None:
        A, C, _kind = bench_sample(db)
    else:
        A, C = pairs, ctrl
    if len(A) < (min_a or MIN_A) or len(C) < (min_c or MIN_C):
        return {}                                     # ★with no sample, make no judgement★
    ra = [(g, search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)) for q, g in A]
    rc = [search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False) for q in C]
    out = {}
    for t in thresholds:
        hit = sum(1 for g, rows in ra
                  if (lambda k: bool(k) and any(ei.gold_hit(g, r["name"]) for r in k))(
                      hook.select(rows, t)))
        ff = sum(1 for rows in rc if hook.select(rows, t))
        out[t] = {"hit": hit, "ff": ff, "n_a": len(A), "n_c": len(C),
                  "auto": (hit / float(len(A))) * (1 - ff / float(len(C)))}
    return out


_META_UNGUARDED = "hook_threshold_unguarded"


def guard_status(db: sqlite3.Connection) -> dict:
    """★Is the calibration guard actually working on this machine.★

    ⛔ Why this function exists (measured 2026-09-02): the regression guard built on 09-01 compares a
    new ruler against the old one on a fixed sample (`tests/eval/short.json`). But that sample is
    ★personal data and is excluded from distribution by `.gitignore`★ (the right decision). Then on a
    new user's machine `_bench` returns `{}`, the body of `if b:` never runs, and
    ★any ruler at all is deployed — with no warning whatsoever.★

    What was caught on this machine on 09-01 is exactly that accident (9.45 → 13.21 · automatic
    0.284 → 0.128). On someone else's machine it happens with no brake at all.

    ★Worse than being unprotected is not knowing you are unprotected.★
    """
    from brain import evalinit as ei                    # ⛔ local import — a module-level one would cycle
    n_a, n_c = len(ei.load_A()), len(ei.load_C())
    if n_a >= MIN_A and n_c >= MIN_C:
        return {"protected": True, "kind": "labelled", "a": n_a, "c": n_c, "why": ""}
    # ★A sample with no labels still protects one direction★ (2026-09-09) — the proxy is measured to
    # agree with real gold when the candidate is ★above★ the incumbent, which is the direction the
    # guard exists for, and to be wrong below it, which is why it never judges there (§brain/proxy.py).
    # ⛔ Reported as its own kind, never as "protected" full stop: claiming a protection you do not
    #    have is the failure this whole section was written about.
    pa, pc, kind = bench_sample(db)
    if kind == "proxy":
        return {"protected": True, "kind": "proxy", "a": len(pa), "c": len(pc),
                "why": "%s %s — %s" % ("⚠️", i18n.t("guard.proxy.title"),
                                       i18n.t("guard.proxy.body", a=len(pa), c=len(pc)))}
    return {
        "protected": False, "kind": "none", "a": n_a, "c": n_c,
        "why": "%s %s — %s" % (
            "⛔", i18n.t("guard.off.title"),
            i18n.t("guard.off.body", a=n_a, c=n_c, need_a=MIN_A, need_c=MIN_C)),
    }


def blocked(db: sqlite3.Connection) -> dict:
    """Was the last calibration ★refused deployment★, and on what grounds."""
    try:
        return json.loads(store.get_meta(db, _META_BLOCKED, "") or "{}")
    except (ValueError, TypeError):
        return {}


def unguarded(db: sqlite3.Connection) -> dict:
    """Was the last calibration deployed ★unverified★, and why (no sample)."""
    try:
        return json.loads(store.get_meta(db, _META_UNGUARDED, "") or "{}")
    except (ValueError, TypeError):
        return {}


_CALIBRATING = False   # ★reentrancy guard★ (2026-09-07 · a hook actually spun for minutes on this)
#
# `calibrate()` calls `_bench()`, which calls `search.recall()` for every candidate query.
# `recall()` in turn calls `calibrate.threshold()` to find its graph-bridge floor. If the stored
# threshold looks stale (§_measured_on_another_corpus), `threshold()` calls `calibrate()` again —
# and that nested call re-benches every query too, fanning out exponentially with each level of
# recursion. Measured: a single hook invocation spun at 100% CPU for minutes before this guard
# existed, timing out the UserPromptSubmit hook on every single prompt.
# ⛔ The fix is not "stop `recall()` from calling `threshold()`" — that lookup is needed to know
#    whether the graph bridge may open. It is "a nested `calibrate()` must not repeat the full
#    measurement" — the outer call is already in the middle of deciding the value; the inner one
#    only needs *a* usable number to keep scoring candidates with, not a fresh recalibration.


def calibrate(db: sqlite3.Connection, force: bool = False) -> dict:
    """Measure and store in the DB. Called after indexing (a changed corpus changes the ruler).

    ⛔ Reentrant calls (via `search.recall`'s bridge-floor lookup, mid-calibration) skip the
    measurement and answer with the best value already known — see `_CALIBRATING` above.
    """
    global _CALIBRATING
    if _CALIBRATING:
        raw = store.get_meta(db, _META_KEY, "")
        try:
            return {"threshold": float(raw), "reentrant": True}
        except ValueError:
            return {"threshold": NO_RULER, "reentrant": True}
    _CALIBRATING = True
    try:
        return _calibrate_locked(db, force)
    finally:
        _CALIBRATING = False


def _calibrate_locked(db: sqlite3.Connection, force: bool = False) -> dict:
    """The actual measurement — only ever called with `_CALIBRATING` held (see `calibrate` above)."""
    result = measure(db)
    real_tops = result.pop("_real_tops", [])         # ⛔ never stored — see `measure`
    floor_exact = result.pop("_floor", None)
    if floor_exact is None:
        floor_exact = result.get("noise_floor")
    mine = code_identity()
    # ⛔ ★`code` stays the mtime★ — a process still running pre-2026-09-09 code reads this field and
    #    compares it the old way. Give it what it expects; the decision below uses `code_id`.
    result["code"] = mine["mtime"]
    result["code_id"] = mine
    conflict = older_ruler(db, mine)
    if conflict and not force:
        # ★This is old code — do not overwrite someone else's ruler.★ And do not pass over it quietly.
        gen_mine, gen_stored = conflict.split(" < ")
        print("[brain] " + i18n.t("calibrate.stale_code", mine=gen_mine, stored=gen_stored) + "\n"
              "[brain] " + i18n.t("calibrate.stale_code_discarded", thr="%.2f" % result["threshold"]),
              file=sys.stderr)
        result["not_saved"] = True
        return result
    # ── ★The adaptive margin★ (2026-10-06 · §brain/adaptive.py) ─────────────────
    # One observation per calibration, on half the sample, then a Kalman update. ⛔ After the stale-code
    # check: an older process must not write the filter either. If the update changed the margin in use,
    # the threshold is recomputed here — and the guard below judges ★that★ threshold as usual.
    from brain import adaptive
    filt = adaptive.step(db, floor_exact or 0.0)
    result["margin_filter"] = filt
    # ⛔ only a result that says which floor and margin it used can be re-derived (a stub may not)
    if floor_exact and "margin" in result and abs(filt["in_use"] - result["margin"]) > 1e-9:
        result["margin"] = filt["in_use"]
        result["threshold"] = max(FLOOR_MIN, round(floor_exact * filt["in_use"], 2))
        result["fire_rate_pct"] = fire_rate_at(real_tops, result["threshold"])
    # ── ⛔ ★Never deploy a worse ruler★ (2026-09-01) ────────────────────────
    #
    # This repository set the same rule for ★the judge★ threshold on 08-31 ("an untrustworthy
    # calibration is not deployed"). ★The lexical★ threshold had no such rule, and so this happened:
    #
    #     15:52  threshold 9.45   hits 19/57 · false 1/34 · automatic 0.324
    #     16:09  threshold 12.15  hits 10/57 · false 0/34 · automatic 0.175   ← it chose this itself
    #
    #   ★A trade of 9 hits for one false firing.★ The corpus was not the cause (1311, unchanged) —
    #   this ruler uses ★the last 120 real prompts★ as its positive control, and a session spent
    #   entirely on one topic filled those 120 with long technical sentences; the firing rate fell
    #   70.8 → 58.3 and the ruler followed it up. ★That is, the ruler is dragged by conversation.★
    #
    # ⛔ So on a fixed sample, ★if the new value is worse than the current one, keep the old★.
    #    This is not ★choosing★ a threshold with the sample (that would be overfitting) but merely
    #    ★refusing★ an obvious regression. Equal or better, and it moves freely.
    # ⛔⛔ ★A guard measuring only against "the current value" cannot stop drift★ (burnt 2026-09-01 18:40)
    #
    # The first version compared only (current ↔ new). It was breached like this:
    #     9.45 ↔ 13.20  → refused ✅  (9.45 was kept)
    #     ...then by some path 13.19 was stored...
    #     13.19 ↔ 13.21 → no difference, so it passed ❌  ← ★the bad value became the new baseline★
    # Automatic stayed frozen at 0.128, down from 0.284. ★A one-step brake cannot stop drift★ —
    # for a guard to do "never get worse", it must ★remember the value it defended★.
    #
    # ⇒ There are only three candidates: the newly measured value, the currently stored one, and
    #   ★the last defended one★. Measure all three on the same fixed sample and keep the best
    #   (on a tie take ★the new value★, so the ruler is not frozen).
    # ⚠️ An honest caveat — this is ★choosing with a sample★ (an overfitting risk). But the candidates
    #   are only ★values the calibrator produced itself★, not a free sweep, and the purpose is not to
    #   find a better value but ★to return to one already measured as better★. A free sweep would overfit.
    prev_raw = store.get_meta(db, _META_KEY, "")
    try:
        prev_t = float(prev_raw) if prev_raw else None
    except ValueError:
        prev_t = None
    kept_t = None
    try:
        kept_t = float((blocked(db) or {}).get("kept") or 0) or None
    except (ValueError, TypeError):
        kept_t = None
    new_t = float(result["threshold"])
    cands = [c for c in dict.fromkeys([new_t, prev_t, kept_t]) if c is not None]
    # ★Which sample may judge, and what it may judge★ (2026-09-09)
    #
    # With this person's own labelled gold, everything below runs as it always has. With a ★proxy★
    # (built from their corpus, no labels) the competence is narrower and ★measured★: it agrees with
    # real gold when the candidate sits above the incumbent (23/25 · 18/18 for incumbents ≥ 8.0) and
    # disagrees below it (0/3). So a proxy is handed only that one comparison — new vs incumbent,
    # upward. Anything else falls through to the unguarded path, which deploys and ★says so★.
    # ⛔ Narrowing what it judges is the point. A guard that answers a question it gets wrong is
    #    worse than one that abstains, because the trace stops being written.
    _pairs, _ctrl, _kind = bench_sample(db)
    result["guard_sample"] = _kind
    abstained = False
    if _kind == "proxy":
        from brain import proxy as _proxy
        if _proxy.may_judge(prev_t, new_t, fire_rate_at(real_tops, prev_t) if prev_t else None):
            cands = [c for c in dict.fromkeys([new_t, prev_t]) if c is not None]
        else:
            # ⛔ ★Abstaining must still leave the trace★ — the first version emptied `cands` here and
            #    the whole block below was skipped, so a move the guard never judged looked exactly
            #    like one it approved. Silence is what this repository's "probe that cannot fail"
            #    lesson is about: go through the unguarded branch and ★say★ it went unverified.
            abstained, cands = True, []
    if not force and (len(cands) > 1 or abstained):
        b = {} if abstained else _bench(db, cands, _pairs, _ctrl)
        if not b:
            # ⛔ ★If it cannot be measured, do not pass over it quietly★ — that is the "probe that
            #    cannot fail" this repository keeps meeting. Deploy anyway (there is no alternative)
            #    but ★leave a trace and let the status screen say it★.
            g = guard_status(db)
            store.set_meta(db, _META_UNGUARDED, json.dumps(
                {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "deployed": new_t,
                 "from": prev_t, "why": g["why"], "a": g["a"], "c": g["c"]},
                ensure_ascii=False))
            if prev_t is not None and abs(prev_t - new_t) > 1e-9:
                print("[brain] %s\n[brain] %s"
                      % (g["why"], i18n.t("guard.unverified",
                                          before="%.2f" % prev_t, after="%.2f" % new_t)),
                      file=sys.stderr)
            result["unguarded"] = True
        else:
            store.set_meta(db, _META_UNGUARDED, "")      # measurable again — clear the alarm
        if b:
            result["bench"] = {str(k): v for k, v in b.items()}
            # ★On a tie take the new value★ — otherwise the ruler freezes (the sort key includes 'is it new').
            best_t = max(cands, key=lambda t: (round(b[t]["auto"], 4), t == new_t))
            if (abs(best_t - new_t) > 1e-9
                    and b[new_t]["auto"] + deploy_margin(b[new_t]["n_a"]) < b[best_t]["auto"]):
                why = i18n.t(
                    "guard.rejected", new="%.2f" % new_t, kept="%.2f" % best_t,
                    kind=i18n.t("guard.rejected.kind.kept" if best_t == kept_t
                                else "guard.rejected.kind.current"),
                    kept_auto="%.3f" % b[best_t]["auto"], new_auto="%.3f" % b[new_t]["auto"],
                    kept_hit=b[best_t]["hit"], new_hit=b[new_t]["hit"],
                    kept_ff=b[best_t]["ff"], new_ff=b[new_t]["ff"])
                store.set_meta(db, _META_BLOCKED, json.dumps(
                    {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kept": best_t,
                     "rejected": new_t, "why": why,
                     "bench": {str(k): v for k, v in b.items()}}, ensure_ascii=False))
                print("[brain] ⛔ %s" % why, file=sys.stderr)
                result["not_deployed"] = True
                result["threshold"] = best_t          # ★roll back to the defended value★
                if prev_t is None or abs(prev_t - best_t) > 1e-9:
                    # ⛔ If the stored value is already the bad one, ★put it back★ — otherwise the next
                    #    round baselines on that bad value and the drift sets (which is exactly what happened).
                    _remember_move(db, dict(result, threshold=best_t))
                    store.set_meta(db, _META_KEY, str(best_t))
                    print("[brain] ↩︎ %s" % i18n.t(
                        "guard.restored", before=prev_raw or "-", after="%.2f" % best_t),
                        file=sys.stderr)
                return result
        store.set_meta(db, _META_BLOCKED, "")         # it passed — clear the alarm
    _remember_move(db, result)                       # record the movement ★before overwriting★
    store.set_meta(db, _META_KEY, str(result["threshold"]))
    store.set_meta(db, _META_DETAIL, json.dumps(result, ensure_ascii=False))
    return result


CORPUS_DRIFT_TOLERANCE = 0.10          # a corpus change this large makes the stored threshold untrustworthy


def _measured_on_another_corpus(db: sqlite3.Connection) -> bool:
    """Was the stored threshold ★measured on a different corpus★?

    ⛔ Without this check the hook died quietly on 2026-08-18: the stored value was 31.94, measured on
    1,345 documents, while the real corpus was 1,158 (re-measured: 9.53). At three times the threshold
    the hook attaches nothing while ★raising no error and writing no log★. The same accident had
    happened on 2026-08-13 (another session's reindex moved the corpus 1,090 → 1,227) — twice.

    Because IDF depends on the document count N, a changed corpus changes the whole scale. So
    "it is stored" is a different statement from "it is valid now".
    """
    detail = store.get_meta(db, _META_DETAIL, "")
    if not detail:
        return True                        # with no evidence, do not trust it
    try:
        saved_docs = int(json.loads(detail).get("docs") or 0)
    except (ValueError, TypeError):
        return True
    if saved_docs <= 0:
        return True
    now = store.corpus_stats(db)["docs"]
    return abs(now - saved_docs) / float(saved_docs) > CORPUS_DRIFT_TOLERANCE


# ★No ruler, no firing★ (2026-10-06) — what a caller gets when no threshold can be measured. It used to be
# `10.0` in seven places: a value that worked on the author's 867 documents, so a corpus where calibration
# failed fired at whatever 10.0 meant there. Infinity means ★nothing clears it★ — the hook stays silent,
# the graph bridge stays shut — which is what every other "cannot tell" path in this repository does.
NO_RULER = float("inf")


def threshold(db: sqlite3.Connection, default: float = NO_RULER) -> float:
    """The value the hook reads. If it has never been calibrated, calibrate right here —
    ★so that a skipped installation step does not break the hook quietly★."""
    raw = store.get_meta(db, _META_KEY, "")
    if raw:
        try:
            saved = float(raw)
        except ValueError:
            saved = None
        if saved is not None and not _measured_on_another_corpus(db):
            return saved
        # a stale ruler falls through to be re-measured on the spot
    try:
        return calibrate(db)["threshold"]
    except Exception:                                    # noqa: BLE001
        return default
