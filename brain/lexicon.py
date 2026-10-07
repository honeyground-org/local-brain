"""Personal vocabulary bridge — ★connects the user's words to the words in the documents★ (2026-08-26).

## Why this layer is needed (the measurement gives its size)

Over the same 43 questions, ★dropping only the synonyms (`terms`)★ takes 98% → **51%**. That is 47 points.
A human fills that gap by hand every single time today — `MEMORY.md` says *"you must pass terms"*,
and the author reads that instruction and passes them, every time.

⛔ **A new user has neither that instruction nor a list of their own words.** Ship this to a team and
   they get the 51%. That gap is exactly what this layer is for.

## Where it learns — ★just by being used★

① **`terms` the user gave** — the `recalls` table already collects (query, expansion, document found).
   A human supplied that knowledge, so it is not circular.
② **When meaning-search succeeds** — when vectors + judge find a document sharing no word with the query,
   that pair is ★distilled★ into the word dictionary. **The expensive layer (remote API · 508ms) teaches the cheap one (local · 0ms).**
   The more it is used, the less it depends on the remote.
③ (next) bootstrapping from past transcripts — so day one already speaks the user's own idiom.

## ⛔ Safety catches (each one came from being burnt)

- **Expand on the query side only** — inflating the index in both languages shifts the whole IDF scale
  (§translit made the same choice for the same reason).
- **A pair observed once is not used** (`MIN_SEEN`). Automatic expansion adds false fires —
  on 2026-08-25 loosening the judge prompt bought +2 hits and cost +6 false fires.
- **A word that is too common is not a bridge** (`MAX_DF_RATIO`). Attaching a word half the corpus holds
  only grows the denominator.
- An attached word is ★left out of the coverage calculation★ — the caller puts it in `bridged`
  (§search.recall). If a bridge inflates the score, the threshold loses its meaning.
"""
from __future__ import annotations

from brain import evalinit as _ei  # ★gold judging lives in exactly one place★

import json
import os
import sqlite3
import time
from typing import Dict, Iterable, List, Optional, Sequence, Set

from . import langdata, textindex

# ★Only pairs observed twice or more★ — once can be a coincidence.
MIN_SEEN = int(os.environ.get("BRAIN_LEXICON_MIN_SEEN", "2") or 2)
# ⛔ ★Off until it has accumulated★ (2026-08-26). Source ① alone (terms the user gave) yielded 29 bridges,
#    and over 43 questions the hits rose by ★exactly 0★ while `verify_short` false fires went 0→1.
#    A net loss. It turns on once source ② (meaning-search distillation) accumulates — the criterion is that check, not taste.
# ⛔⛔ ★A human does not configure this — it turns itself on and off★ (user instruction 2026-08-27:
#    "not the individual configuring it, but improving automatically and optimising dynamically")
#
#    The old version was an environment variable, `BRAIN_LEXICON=1`. The comment said *"it turns on once
#    source ② accumulates — the criterion is that check, not taste"*, but ★nothing ran that check and
#    turned it on★. So the layer stayed off while 1,318 bridges piled up.
#    A criterion that is written down and never executed is not a criterion.
#
#    Now `decide()` actually runs that check and records the result in meta. The environment variable
#    survives only as a ★forced override★ (1 = on · 0 = off · absent = the measurement decides).
_ENV = os.environ.get("BRAIN_LEXICON", "")

# ★The setting `decide()` is trying — held by ★this process only★★ (2026-10-07) · (on, knobs) or None.
# ⛔ `decide()` used to try each setting by ★writing it to the shared meta★ and restoring it after.
#    Every other process on the same index — the hook, the MCP servers, a person's own measurement —
#    read those trial settings as the real one for as long as the sweep ran (37 settings × 91 queries).
#    Measured: the daily job swept while a bench ran three times in a row and the bench read
#    19/12 · 18/7 · 16/11 (hits/false fires) where the real setting gives 15/3 every time; switching
#    the lexicon on with the sweep's knobs on a copy of the index reproduces 15–21 / 5–15.
#    A trial is a question this process asks, so the answer must stay in this process.
_TRIAL: Optional[tuple] = None


def enabled(db=None) -> bool:
    """Is the bridge in use right now. ⛔ Not a value but a ★measured decision★.

    Priority: environment variable (a human nailing it down) → the setting `decide()` is trying in this
    process → measured decision (meta) → off by default.
    A design where a human must remember "turn it on once it accumulates" ★never gets turned on★ — that is what happened.
    """
    if _ENV in ("1", "0"):
        return _ENV == "1"
    if _TRIAL is not None:
        return bool(_TRIAL[0])
    if db is None:
        return False                                     # asked without a db → answer conservatively
    try:
        from . import store
        return store.get_meta(db, "lexicon_enabled", "") == "1"
    except Exception:                                    # noqa: BLE001
        return False
# How many expansions one word may pull in · the cap for the whole query
PER_WORD = int(os.environ.get("BRAIN_LEXICON_PER_WORD", "3") or 3)
MAX_BRIDGE = int(os.environ.get("BRAIN_LEXICON_MAX", "8") or 8)
# df range for a word usable as a bridge — too common carries no signal
MIN_DF = 1
MAX_DF_RATIO = 0.25
# ★An anchor is not 'the rarest' but 'rare yet a real topic word'★ (measured 2026-08-26)
# The first version took the word with the smallest df as the anchor. In Korean that picks ★an inflectional
# ending★ — the learned bridges were (conjugated form)→engine · (conjugated form)→16 · (conjugated form)→health-check.
# An inflected form appears with df=1, so a single lower bound catches it. (§translit was burnt in the same
# place: a common verb stem and a pronoun each bridged to a person's name.)
ANCHOR_MIN_DF = int(os.environ.get("BRAIN_LEXICON_ANCHOR_MIN_DF", "3") or 3)
# ★A pair is verified by co-occurrence★ — measured contrast (2026-08-26):
#   three conjugated-form pairs scored 0 · 1 · 1 co-occurrences          (all rubbish)
#   favicon→logo 4 · a Korean spelling of terraform→terraform 8          (both real)
# The ratio (cooc/min(df)) does not separate them (0.24 is a good pair, 0.33 is rubbish). The absolute count does.
MIN_COOC = int(os.environ.get("BRAIN_LEXICON_MIN_COOC", "3") or 3)
# ★Lift separates function words★ (measured 2026-08-26) — the ratio did not:
#   rubbish  favicon→"now" 4.4 · terraform→"is" 6.4 · terraform→"however" 6.8
#   real     favicon→logo 26.3 · terraform→terraform 13.7 · favicon→favicon 44.1
# lift = P(dst|src) / P(dst) — "does it appear unusually often beside this word".
# A function word appears everywhere, so its denominator is large and its lift is low.
# ⚠️ Common real pairs (rebase→origin 5.4 · rebase→merge 3.1) are lost. Those are found without a bridge.
MIN_LIFT = float(os.environ.get("BRAIN_LEXICON_MIN_LIFT", "8") or 8)
# ★Inflected forms come last in anchor order★ — above the df floor, pairs like (conjugated form)→16 still survived.
# ⛔ This filter is used ★only when choosing an anchor★. A filter that removes Korean words from the
#    search score was already measured harmful here (a 23-adverb filter: ceiling recall 16 → 12).
#    Nothing is discarded here — only the order is deferred.
# ⛔ ★Corpus-language data★, so it lives in `langdata` — translating it silently stops the match.
_ENDINGS = langdata.KO_ENDINGS


def _inflected(w: str) -> bool:
    """Is it Hangul and does it end in an inflectional ending — likely not a topic word."""
    if not w or not langdata.is_hangul(w):
        return False
    return len(w) >= 3 and w.endswith(_ENDINGS)


def ensure(db: sqlite3.Connection) -> None:
    """The table — created if absent. It is derived, so deleting it only relearns."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS lexicon("
        " src TEXT NOT NULL,"          # the word the user wrote
        " dst TEXT NOT NULL,"          # the word written in the documents
        " seen INTEGER NOT NULL DEFAULT 1,"
        " source TEXT NOT NULL DEFAULT '',"
        " last TEXT,"
        " PRIMARY KEY(src, dst))")
    db.execute("CREATE INDEX IF NOT EXISTS lexicon_src ON lexicon(src, seen DESC)")


def observe(db: sqlite3.Connection, src: str, dst: str, source: str = "") -> None:
    """Record one observation of a pair (cumulative)."""
    if not src or not dst or src == dst:
        return
    db.execute(
        "INSERT INTO lexicon(src, dst, seen, source, last)"
        " VALUES(?,?,1,?,datetime('now'))"
        " ON CONFLICT(src, dst) DO UPDATE SET"
        "  seen = seen + 1, last = datetime('now'),"
        "  source = CASE WHEN instr(lexicon.source, excluded.source) > 0"
        "                THEN lexicon.source"
        "                ELSE lexicon.source || '+' || excluded.source END",
        (src, dst, source))


def _content_words(text: str) -> List[str]:
    """Content words of the query — too short to be a bridge's starting point."""
    return [w for w in textindex.words(text or "") if len(w) >= 2]


def _df(db: sqlite3.Connection, term: str) -> int:
    row = db.execute("SELECT df FROM terms WHERE term=?", (term.lower(),)).fetchone()
    return int(row[0]) if row else 0


def _anchor(db: sqlite3.Connection, words: Sequence[str]) -> Optional[str]:
    """The word ★that carries the query's identity★ = the rarest one.

    Why one and not all — registering the full cross product of query words × expansions lets a common
    word ("check" · "work") drag in every expansion and the bridge becomes noise. Starting from one rare
    word only, that pair fires ★on that topic alone★.
    """
    # ★Identifier > noun form > inflected form★, and within a tier the rarest one.
    tiers: Dict[int, List[tuple]] = {0: [], 1: [], 2: []}
    for w in words:
        df = _df(db, w)
        if df < ANCHOR_MIN_DF:                           # ⛔ typos and one-offs cannot be anchors
            continue
        if langdata.is_hangul(w):
            tier = 2 if _inflected(w) else 1
        else:
            tier = 0                                     # a latin/numeric identifier is the best anchor
        tiers[tier].append((df, w))
    for t in (0, 1, 2):
        if tiers[t]:
            return min(tiers[t])[1]
    return None


def _cooc(db: sqlite3.Connection, a: str, b: str) -> int:
    """How many times two words appeared ★in the same document★ — the evidence for a pair."""
    try:
        row = db.execute(
            "SELECT COUNT(DISTINCT p1.doc_id) FROM postings p1"
            " JOIN postings p2 ON p1.doc_id = p2.doc_id"
            " WHERE p1.term = ? AND p2.term = ?",
            (a.lower(), b.lower())).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def _usable(db: sqlite3.Connection, term: str, n_docs: int) -> bool:
    """Is this word usable as a bridge — present in the corpus, and not too common."""
    if not term or len(term) < 2:
        return False
    df = _df(db, term)
    cap = max(MIN_DF + 1, int((n_docs or 1) * MAX_DF_RATIO))
    return MIN_DF <= df <= cap


def learn_from_recalls(db: sqlite3.Connection, limit: int = 0) -> Dict[str, int]:
    """① Learn from the `terms` the user gave.

    One row of `recalls` is (query, the expansion a human gave, the document actually found). Of those
    expansions, ★the ones absent from the query★ are the knowledge the human added, and if such a word
    really exists in the corpus it is linked to the query's anchor word.
    """
    ensure(db)
    n_docs = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0] or 1
    sql = "SELECT query, terms FROM recalls ORDER BY id DESC"
    if limit:
        sql += " LIMIT %d" % int(limit)
    pairs = seen_rows = 0
    for row in db.execute(sql).fetchall():
        seen_rows += 1
        try:
            terms = json.loads(row["terms"] or "[]")
        except (ValueError, TypeError):
            continue
        if not terms:
            continue
        qw = _content_words(row["query"])
        anchor = _anchor(db, qw)
        if not anchor:
            continue
        have = {w.lower() for w in qw}
        for t in terms:
            for tok in _content_words(str(t)):
                if tok.lower() in have or not _usable(db, tok, n_docs):
                    continue
                if _cooc(db, anchor, tok) < MIN_COOC:    # ⛔ a pair without evidence is not written
                    continue
                observe(db, anchor, tok, "terms")
                pairs += 1
    db.commit()
    return {"rows": seen_rows, "pairs": pairs}


def learn_from_vector(db: sqlite3.Connection, query: str, doc_name: str,
                      doc_terms: Iterable[str]) -> int:
    """② ★Distil★ what meaning-search found into the word dictionary.

    Words fell silent and meaning-search still found it — that means some word in that document carries
    ★the same sense with a different spelling★. Write the pair down and it is found for free next time.
    """
    ensure(db)
    n_docs = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0] or 1
    qw = _content_words(query)
    anchor = _anchor(db, qw)
    if not anchor:
        return 0
    have = {w.lower() for w in qw}
    added = 0
    for t in doc_terms:
        tok = str(t)
        if tok.lower() in have or not _usable(db, tok, n_docs):
            continue
        if _cooc(db, anchor, tok) < MIN_COOC:
            continue
        observe(db, anchor, tok, "vector")
        added += 1
    db.commit()
    return added


def learn_from_corpus(db: sqlite3.Connection, min_cooc: int = 3,
                      min_ratio: float = 0.6, src_max_df: int = 60,
                      progress=None) -> Dict[str, int]:
    """③ ★Learn from the corpus itself★ — even a new user with no conversation history, from day one.

    The documents already hold both spellings of the same thing side by side (a Korean spelling of
    "favicon" next to `favicon`, "server worker" next to `server_worker_enabled`). Counting those pairs
    builds bridges before the user says a word — ★the only path by which this layer works for a new user.★

    ⛔ ★Do not count from postings★ (measured 2026-08-26). That table is the ★index★, which cuts Korean
       into 2-grams, so counting there yields ★fragments★ — pieces of a syllable, not words at all.
       So the words are taken again from the body with `textindex.words()`.

    ⛔ ★Co-occurrence alone gives related words, not synonyms★ ("deploy" and `terraform` appear together
       but are not the same thing). So the conditional share is used — dst must be present in at least
       `min_ratio` of the documents holding src. Two spellings of one thing almost always travel together.
    """
    from collections import Counter, defaultdict
    ensure(db)
    docs: Dict[int, Set[str]] = {}
    for row in db.execute("SELECT id, name, description, body FROM docs").fetchall():
        text = " ".join(str(row[k] or "") for k in ("name", "description", "body"))
        docs[row["id"]] = {w for w in textindex.words(text) if len(w) >= 2}
    n_docs = len(docs) or 1
    df: Counter = Counter()
    for ws in docs.values():
        df.update(ws)
    # ⛔ The dst cap for corpus learning is ★far tighter★ than the recall one (MAX_DF_RATIO) — it keeps
    #    function words out of the candidates from the start ("now" df 170 · "is" 195 · "however" 187).
    dst_cap = max(3, int(n_docs * 0.08))
    dst_ok = {w for w, d in df.items() if MIN_DF < d <= dst_cap}
    inv: Dict[str, Set[int]] = defaultdict(set)
    for did, ws in docs.items():
        for w in ws:
            inv[w].add(did)
    # src candidates — the range of a topic word. Too common attaches anywhere, too rare never returns.
    srcs = [w for w, d in df.items()
            if ANCHOR_MIN_DF <= d <= src_max_df and not _inflected(w)]
    pairs = 0
    for i, src in enumerate(srcs):
        if progress and i % 500 == 0:
            progress(i, len(srcs), pairs)
        dids = inv[src]
        base = len(dids)
        if base < min_cooc:
            continue
        cnt: Counter = Counter()
        for did in dids:
            cnt.update(docs[did] & dst_ok)
        for dst, c in cnt.items():
            if dst == src or c < min_cooc or c / base < min_ratio:
                continue
            if dst in src or src in dst:                 # ⛔ a substring is not a bridge
                continue
            lift = (c / base) / max(1e-9, df[dst] / n_docs)
            if lift < MIN_LIFT:                          # ⛔ function words are dropped here
                continue
            # ★Put the evidence into seen★ — corpus learning happens once, so the observation count is
            # 1. Left alone it is blocked by the MIN_SEEN threshold and ★learnt but never used★.
            # The evidence for this layer is the co-occurrence count, not the number of observations.
            db.execute(
                "INSERT INTO lexicon(src, dst, seen, source, last)"
                " VALUES(?,?,?,'corpus',datetime('now'))"
                " ON CONFLICT(src, dst) DO UPDATE SET"
                "  seen = MAX(lexicon.seen, excluded.seen), last = datetime('now')",
                (src, dst, int(c)))
            pairs += 1
    db.commit()
    return {"docs": n_docs, "srcs": len(srcs), "pairs": pairs}


# ★If the table has grown this much beyond what the decision approved, the approval has expired.★
#
# Measured grounds (2026-09-01 16:40 · same threshold 9.45 · same moment):
#     bridge off   fires 28 · gold in top 3 17 · false fires 1   automatic 0.2895
#     bridge on    fires 39 · gold in top 3 19 · false fires 5   automatic ★0.2843★  ← below break-even
# Yet the grounds the 09:30 decision left behind say ★off noise 0 · on noise 0★.
#
# ⛔ ★That 0 could not be reproduced at any threshold that day★ (false fires > 0 across 7.0~12.45).
#    So the state at decision time differs from now. What differed is ★still unknown★ —
#    because the approval ★did not record the state it approved★. That is why this constant exists.
#
# ⚠️ I first wrote "the table doubled" and that was ★wrong★: 5,344 is `pairs` (all candidates), while what
#    the bridge actually uses is `usable` (seen ≥ MIN_SEEN), which went 2,557 → 3,004 = ★+17.5%★.
#    ⇒ ★First check that the two numbers being compared count the same thing.★
STALE_GROWTH = 0.40


def decision_stale(db) -> dict:
    """★How far the current table is from the one the decision approved.★

    ⛔ Why this is needed: this layer is judged ★once a day★ (`brain-rules-daily` 09:30) while the bridge
    table ★grows with every prompt★. So "what was approved" and "what is running" drift apart.
    Nobody was watching that gap.

    ⛔ The layer is not switched off here — as this file's §judge comment warns, "asking only on/off
    throws away the whole layer". The right prescription is ★tightening the knobs★, and that is a grid
    sweep (36 runs), which is not work for an indexing hook. ⇒ ★Say it loudly, and judge sooner.★
    """
    from brain import store                           # ⛔ local import — a module-level one cycles
    # ⛔ ★Compare usable bridges against usable bridges★ — `count(*)` also counts candidates with
    #    `seen < MIN_SEEN`, a number unrelated to how the bridge behaves (5,344 ↔ usable is fewer).
    now = int(stats(db)["usable"])
    at_dec = 0
    raw = store.get_meta(db, "lexicon_rows_at_decision", "")
    if raw:
        try:
            at_dec = int(raw)
        except (ValueError, TypeError):
            at_dec = 0
    if not at_dec:                                    # an older decision lacks this key —
        try:                                          # ★use the value already in the grounds★
            at_dec = int((json.loads(store.get_meta(db, "lexicon_evidence", "") or "{}")
                          ).get("usable_bridges") or 0)
        except (ValueError, TypeError):
            at_dec = 0
    if not at_dec:
        # ⛔ ★Not knowing is not the same as 'no'★ — without grounds, say 'unknown'
        return {"stale": None, "why": "the bridge count at decision time is unknown — the next decision records it",
                "usable": now, "at_decision": 0}
    growth = (now - at_dec) / float(at_dec)
    if growth <= STALE_GROWTH:
        return {"stale": False, "why": "", "usable": now, "at_decision": at_dec,
                "growth": growth}
    return {"stale": True, "usable": now, "at_decision": at_dec, "growth": growth,
            "why": ("the lexicon-bridge approval has ★expired★ — the decision was made on %d usable "
                    "bridges and there are %d now (+%.0f%%). The `noise` that justified it is a value "
                    "about ★the table as it was then★." % (at_dec, now, 100 * growth))}


def knobs(db) -> "tuple":
    """The knobs in use right now (min_seen, per_word, max_bridge).

    ⛔ ★Not the defaults in the code but the values the measurement chose★ (§decide). Idiom and corpus
    differ per person, so one set of constants cannot fit everyone — so the values are not nailed down,
    ★the way of choosing them★ is (the same choice §calibrate made for the word threshold).
    """
    if _TRIAL is not None and _TRIAL[1]:
        return tuple(int(x) for x in _TRIAL[1])         # §_TRIAL — a trial, never the shared meta
    try:
        from . import store
        raw = store.get_meta(db, "lexicon_knobs", "")
        if raw:
            k = json.loads(raw)
            return (int(k[0]), int(k[1]), int(k[2]))
    except Exception:                                    # noqa: BLE001
        pass
    return (MIN_SEEN, PER_WORD, MAX_BRIDGE)


def bridge(db: sqlite3.Connection, query: str,
           min_seen: int = 0, per_word: int = 0, max_bridge: int = 0) -> List[str]:
    """Query → the expansions to attach. Empty list if none (recall runs without a bridge)."""
    if not (min_seen or per_word or max_bridge) and not enabled(db):
        return []                                        # during a sweep, measure regardless of on/off
    try:
        ensure(db)
    except sqlite3.Error:
        return []
    k_seen, k_per, k_max = knobs(db)
    min_seen = min_seen or k_seen
    per_word = per_word or k_per
    max_bridge = max_bridge or k_max
    out: Set[str] = set()
    have = {w.lower() for w in _content_words(query)}
    for w in _content_words(query):
        try:
            rows = db.execute(
                "SELECT dst FROM lexicon WHERE src=? AND seen>=?"
                " ORDER BY seen DESC, dst LIMIT ?",
                (w, min_seen, per_word)).fetchall()
        except sqlite3.Error:
            return []
        for r in rows:
            if r["dst"].lower() not in have:
                out.add(r["dst"])
    return sorted(out)[:max_bridge]


def stats(db: sqlite3.Connection) -> dict:
    ensure(db)
    row = db.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(seen), 0) s,"
        " COALESCE(SUM(CASE WHEN seen >= ? THEN 1 ELSE 0 END), 0) live"
        " FROM lexicon", (MIN_SEEN,)).fetchone()
    top = [dict(r) for r in db.execute(
        "SELECT src, dst, seen, source FROM lexicon"
        " WHERE seen >= ? ORDER BY seen DESC, src LIMIT 12", (MIN_SEEN,))]
    return {"pairs": row["n"], "observations": row["s"],
            "usable": row["live"], "min_seen": MIN_SEEN, "top": top}


def forget(db: sqlite3.Connection, src: str, dst: str = "") -> int:
    """A human looks and deletes — what was learnt automatically can be automatically wrong."""
    ensure(db)
    if dst:
        cur = db.execute("DELETE FROM lexicon WHERE src=? AND dst=?", (src, dst))
    else:
        cur = db.execute("DELETE FROM lexicon WHERE src=?", (src,))
    db.commit()
    return cur.rowcount


# ── ★It turns itself on and off★ ────────────────────────────────────────────
def decide(db, force: bool = False) -> dict:
    """Decide ★by measuring★ whether to use the bridge — a human does not configure it.

    ## Why this function exists

    This layer sat ★switched off behind an environment variable★ with "it turns on once it accumulates,
    the criterion is that check" written on it. Nobody ran that check while 1,318 bridges piled up.
    ⛔ ★A criterion that is written down and never executed is not a criterion.★

    ## What is measured — the same set twice, bridge off and bridge on

    Using A (short queries that need memory) and C (talk that needs none) from `tests/eval/short.json`,
    it measures ★hits★ and ★false fires★. Every path is lexical, so it is ★local with 0 remote calls★
    (unrelated to the judge budget — running it daily is free).

    ## The condition for turning it on — ⛔ more hits do not count if false fires rise

    On 2026-08-26 it was turned on with source ① alone: hits rose by ★exactly 0★ and false fires went 0→1.
    A net loss. So there are two conditions and ★both★ must hold:
      ① hits do not fall  ② false fires do not rise  (and only turn on when ① actually rises)

    ⛔ ★What a human switched off is not revived★ — with `BRAIN_LEXICON=0` it stays off regardless of
       the measurement (the same principle as §ruledisc's dismissed: a layer whose automation overrides
       a human's judgement loses their trust).
    """
    from . import calibrate as _cal, evalinit as _evalinit, hook as _hook, search as _search, store
    if _ENV == "0" and not force:
        return {"decision": "a human turned it off (BRAIN_LEXICON=0)", "enabled": False}

    path = os.path.join(_evalinit.eval_dir(), "short.json")
    try:
        with open(path, encoding="utf-8") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:                 # noqa: BLE001
        return {"decision": "could not read the evaluation set: %s" % exc, "enabled": enabled(db)}

    def qs(key):
        return [r if isinstance(r, str) else r["q"] for r in ev.get(key, [])]
    A = [(r["q"], r["gold"]) for r in ev.get("A_memory_needed", [])
         if isinstance(r, dict) and r.get("gold")]
    C = qs("C_no_memory_needed")
    if len(A) < 10 or len(C) < 10:
        return {"decision": "the sample is small (A %d · C %d) — not deciding" % (len(A), len(C)),
                "enabled": enabled(db)}

    thr = _cal.threshold(db)

    def run(on: bool, k=None) -> dict:
        global _TRIAL
        _TRIAL = (on, tuple(k) if k else None)          # §_TRIAL — ⛔ never the shared meta
        try:
            hit = gold3 = 0
            for q, gold in A:
                rows = _search.recall(db, q, k=_hook.MAX_ITEMS + 2, log=False)
                keep = _hook.select(rows, thr)
                if keep:
                    hit += 1
                    if any(_ei.gold_hit(gold, r["name"]) for r in keep[:3]):
                        gold3 += 1
            noise = sum(1 for q in C
                        if _hook.select(_search.recall(db, q, k=_hook.MAX_ITEMS + 2,
                                                       log=False), thr))
            return {"fire": hit, "gold3": gold3, "noise": noise}
        finally:
            _TRIAL = None

    # ⛔ With the environment variable at 1, `enabled()` ignores meta, so the measurement returns the
    #    same value twice. In that state it must not claim to have "measured" anything.
    if _ENV == "1" and not force:
        return {"decision": "a human turned it on (BRAIN_LEXICON=1) — not measuring", "enabled": True}

    off = run(False)
    # ★Sweep the knobs instead of asking a yes/no★ (user instruction 2026-08-27: "optimise dynamically")
    # ⛔ Asking only on/off throws away the whole layer at places like "hits +2, false fires +2".
    #    That happened — the first decision said 'not turned on' for exactly that reason. Constrained
    #    ★more strictly★, a bridge may shed only noise; folding the layer without looking at that axis is hasty.
    grid = [(a, b, c) for a in (2, 3, 4, 6) for b in (3, 2, 1) for c in (8, 4, 2)]
    tried = []
    for k in grid:
        r = run(True, k)
        r["knobs"] = list(k)
        tried.append(r)
    # ⛔ ★Any rise in false fires is disqualifying★, first — noise erodes trust in the whole layer.
    ok = [r for r in tried
          if r["noise"] <= off["noise"] and r["gold3"] >= off["gold3"]]
    # ⛔⛔ ★A rise in fires is not a gain★ — the warning this repository wrote on the last line of
    #    `verify_short` has exactly this shape: "if only the firing rate rises and the gold rank stays,
    #    noise has risen". The first version was `gold3 > off or fire > off`, so a setting with
    #    ★gold3 16→16 and hits only +1★ was called a 'gain' and the layer was turned on. That +1 is a
    #    fire with no gold in the top 3 — merely absent from control set C, and possibly noise.
    #    ★It is a gain only when the right answer rises.★
    gain = [r for r in ok if r["gold3"] > off["gold3"]]
    best = max(gain, key=lambda r: (r["gold3"], r["fire"], -sum(r["knobs"]))) if gain else None
    want = best is not None
    st = stats(db)
    with db:
        # ★An approval is an approval of 'the table as it was'★ — keep its size so expiry can be measured
        store.set_meta(db, "lexicon_rows_at_decision", str(int(st["usable"])))
        store.set_meta(db, "lexicon_enabled", "1" if want else "0")
        # ⛔ ★When not turning it on, erase the knobs★ — otherwise the value left by an earlier decision
        #    survives and, if some other path turns the layer on later, it runs with ★a setting nobody
        #    chose★. (That happened once: tightening the criterion flipped it to 'not turned on' while knobs=[4,2,2] stayed.)
        store.set_meta(db, "lexicon_knobs",
                       json.dumps(best["knobs"]) if want else "")
        store.set_meta(db, "lexicon_decided_at", time.strftime("%Y-%m-%d"))
        # ⛔ ★An approval must record the state it approved★ (burnt on 2026-09-01).
        #    The grounds said `off.noise 0`, and that afternoon the 0 could not be reproduced at any
        #    threshold. Without ★the threshold, corpus and time★ of that moment there was no way to tell
        #    what differed — grounds that cannot be reproduced are not grounds.
        store.set_meta(db, "lexicon_evidence", json.dumps(
            {"off": off, "best": best, "grid": len(tried), "no_noise": len(ok),
             "usable_bridges": st["usable"], "pairs": st["pairs"],
             "threshold": thr, "docs": store.corpus_stats(db)["docs"],
             "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False))
    why = ("hits %d→%d · gold3 %d→%d · false fires %d→%d · knobs %s"
           % (off["fire"], best["fire"], off["gold3"], best["gold3"],
              off["noise"], best["noise"], best["knobs"])) if want else (
          "of %d combinations ★none produced a gain without adding noise★ "
          "(%d combinations added no noise)" % (len(tried), len(ok)))
    return {"decision": ("★turned on★" if want else "not turned on"), "enabled": want,
            "off": off, "best": best, "grid": len(tried),
            "usable_bridges": st["usable"], "why": why}
