"""Recall — ★words open the door, links walk the path★.

The structure comes from an earlier memory system this one learned from: search is strong at finding
**entrances** and cannot follow connections; a graph follows **connections** precisely
but does not know where to start. Together they reach *"the memory whose words never appear in the query"*.

What we changed is the material of the door. That system used vectors (a remote API);
we use ★IDF-weighted n-grams★ — for three reasons:

  ① CJK bigrams drop out of FTS5 entirely (§textindex) — without fixing that, nothing else matters
  ② IDF fixes "common words drown the signal" ★at the cause★
     Measured: some function words have df=134 and df=58 while content words have df=8 and df=6.
     OR them together unweighted and the common word wins (1 of 6 questions failed exactly this way)
  ③ Text never leaves the machine — our memories contain references to production credentials

★What semantic search replaces is not this★ — it is **who builds the expansion terms**.
Today the caller (the agent) builds them (measured 1/6 → 5/6); embeddings do that job automatically.
So `extra_terms` stays a core parameter: this file does not change when the source of expansion does.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

from brain import i18n, store, textindex

# ---- Knobs (every number has a reason) -------------------------------------
K1 = 1.2          # BM25 saturation — a word appearing 100 times is not 10× better than 10 times
B = 0.6           # length normalisation — a long document must not win merely by being long
SEED_HITS = 3     # starting points for the graph bridge. Use them all and expansion drags in half the graph
# ⛔ EXPANSION_SHARE (34% reserved for the graph) was removed 2026-08-20 — the reservation
#    ★took the answer's slot★ and showed up as a regression (contract B, top-8: 9→8). Related context is appended after k.
MAX_RELATED = 2   # cap on "related context" appended after k — side branches must not blur the answer
COVERAGE_FLOOR = 0.4     # floor of the coverage bonus (one matching word still leaves it a candidate)
STOP_DF_RATIO = 0.55     # a gram present in over half the documents has no discriminating power → skip

# ★Korean inflection steals IDF★ (2026-08-19)
#
# A word's worth is set by "the rarest of its grams". But Korean attaches particles and endings,
# so a long gram ★accidentally★ ends up with df 1–2. Measured (corpus 1,158):
#
#   word              min df  max df   what it is
#   (function word)      1     278     a function word whose inflected form (df 1) gets the top IDF
#   (function word)      1     234     same disease
#   (function word)      1     291     same disease
#   (content word)       1      19     ★content★ — even its short gram is rare (19)
#   (brand name)         6       6     ★proper noun★ — every gram is evenly rare
#
# ⛔ So for one long query every match was a function word and the actual content word never
#    shaped the ranking. It is ★the Korean version of the same disease★ as the English function
#    words fixed on 2026-08-12 (where `should` outweighed `terraform`).
#
# ★Judge it by statistics, not by a list★ — the old "Korean adverb filter" was a word list and
# cost 4 points of ceiling recall. Here we look at the df distribution of the grams within one word:
#   "short grams common but only the long gram rare" = inflection accident → do not trust that rare gram.
#   If every gram is evenly rare (a proper noun), leave it alone.
RARE_DF = int(os.environ.get("BRAIN_RARE_DF", "0") or 0)        # 0 = rule disabled
TRANSLIT = os.environ.get("BRAIN_TRANSLIT", "1") not in ("0", "false", "")
INFLECTION_COMMON_DF = int(os.environ.get("BRAIN_INFLECTION_COMMON_DF", "50") or 50)

# ★A ceiling on rarity★ — cutting a query into n-grams produces **meaningless fragments**, and
# such a fragment usually has df=1, so it gets the highest IDF. With no ceiling, one document
# that happens to contain that fragment comes to represent the entire query.
# df 1, 2 and 3 are pressed to the same value, refusing to distinguish "rare" from "noise".
IDF_CAP_DF = 3
EXCERPT_RADIUS = 160

# ★Where "a memory is stale" is defined — this is canonical★ (`health` reads it)
#
# ⛔ This rule used to exist in **two copies**: `_STALE_MULT` here (× a hardcoded 90) and
#   `health.EVIDENCE_STALE_DAYS` (90) + `health.STALE_MULTIPLIER`. The same meaning in two places
#   means one of them gets fixed. They were merged on 2026-08-12.
#
# ★Not every memory ages at the same speed★ (measured 2026-08-10)
# Flagging 19 memories as "stale" by evidence date alone turned up mostly **rules and lessons**.
# Those stay true over time — discard them for being old and you lose perfectly good rules.
# What actually ages is ★a memory holding state★: an open PR, a current number, a deployment status.
#
# ⛔ project was 90 days, and that was **far too generous** (user's point, 2026-08-12):
#   a three-month-old progress report surfacing without a warning reads as the present. Measured,
#   80 of 145 project memories sat in the 21–90 day band, and opening them showed all of them to
#   be two or three months old. Dropping to 21 days takes warnings from 12 (2%) to 92 (19%) —
#   and that 19% is ★genuinely stale★, so it is not crying wolf; there is a wolf.
#
# ⛔ ★Which kind ages how fast is not ours to fix★ (2026-10-06) — the table above was this author's kinds.
#    Now: config `stale_days` (a person's own kinds) > the host's own memory types (§hosts.Host.stale_days —
#    Claude Code's `project` is the 21-day one measured above) > `STALE_DAYS_DEFAULT` for everything else.
#    Not learned from edit times: this corpus is young enough that a percentile would age rules in 60 days.
STALE_DAYS_DEFAULT = 180     # a kind nobody described (domain docs, wikis, project rules, other hosts' kinds)
_STALE_CACHE: dict = {}


def stale_table() -> dict:
    """kind → days, as this machine resolves it (the host's types, then the person's config on top)."""
    try:
        path = store.default_config_path()
        stamp = (path, os.path.getmtime(path) if os.path.exists(path) else 0.0)
    except OSError:
        stamp = ("", 0.0)
    try:
        from brain import hosts
        host_name = hosts.active().name
    except Exception:                                    # noqa: BLE001
        host_name = ""
    key = stamp + (host_name,)
    if key not in _STALE_CACHE:
        table: dict = {}
        try:
            from brain import hosts
            table.update(hosts.active().stale_days())
        except Exception:                                # noqa: BLE001
            pass
        own = store.load_config(tolerant=True).get("stale_days") or {}
        table.update({k: int(v) for k, v in own.items() if isinstance(v, (int, float)) and v > 0})
        _STALE_CACHE.clear()
        _STALE_CACHE[key] = table
    return _STALE_CACHE[key]

_WS = re.compile(r"\s+")


def _idf(df: int, n: int) -> float:
    df = max(df, IDF_CAP_DF)
    return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


def _age_days(mtime: float) -> float:
    return max(0.0, (time.time() - mtime) / 86400.0)


def stale_limit(kind: str) -> int:
    """After how many days is a memory of this kind stale — the reasoning is in the comment above `stale_table`."""
    return stale_table().get(kind or "", STALE_DAYS_DEFAULT)


def _is_stale(kind: str, evidence_age: int, file_age: float) -> bool:
    # Use the evidence date when there is one — ★the file may be from yesterday and the number inside from six months ago★.
    return (evidence_age if evidence_age >= 0 else file_age) > stale_limit(kind)


def _excerpt(body: str, needles: Sequence[str]) -> str:
    """The fragment that lets a human see with their own eyes why this document came back."""
    low = body.lower()
    best_pos, best_hits = -1, 0
    for nd in needles:
        p = low.find(nd.lower())
        if p >= 0:
            hits = sum(1 for x in needles if x.lower() in
                       low[max(0, p - EXCERPT_RADIUS):p + EXCERPT_RADIUS])
            if hits > best_hits:
                best_pos, best_hits = p, hits
    if best_pos < 0:
        snippet = body[:EXCERPT_RADIUS * 2]
    else:
        s = max(0, best_pos - EXCERPT_RADIUS)
        snippet = ("…" if s else "") + body[s:best_pos + EXCERPT_RADIUS] + "…"
    return _WS.sub(" ", snippet).strip()


def _score_terms(db: sqlite3.Connection, pairs, n_docs: int, avg_len: float,
                 kinds: Optional[Sequence[str]], sources: Optional[Sequence[str]]
                 ) -> Tuple[Dict[int, float], Dict[int, List[str]], Dict[str, float]]:
    """Accumulate scores per word — summing per gram automatically favours longer words."""
    df_cache: Dict[str, int] = {}
    all_terms = sorted({g for _, gs in pairs for g in gs})
    if all_terms:
        qmarks = ",".join("?" * len(all_terms))
        for row in db.execute(
                "SELECT term, df FROM terms WHERE term IN (%s)" % qmarks, all_terms):
            df_cache[row["term"]] = row["df"]

    doc_score: Dict[int, float] = {}
    doc_words: Dict[int, List[str]] = {}
    word_idf: Dict[str, float] = {}
    stop_df = max(2, int(n_docs * STOP_DF_RATIO))

    for word, grams in pairs:
        usable = [g for g in grams if df_cache.get(g, 0) > 0]
        if not usable:
            continue
        discriminative = [g for g in usable if df_cache[g] < stop_df]
        # ★Filter out inflection accidents★ — if a word has both common grams and a very rare one,
        # that rare one is rare only because a particle or ending attached (see the RARE_DF comment above).
        if RARE_DF > 0 and len(discriminative) > 1:
            hi = max(df_cache[g] for g in usable)
            if hi >= INFLECTION_COMMON_DF:
                trimmed = [g for g in discriminative if df_cache[g] > RARE_DF]
                if trimmed:                      # if everything got cut, put it back (better than empty-handed)
                    discriminative = trimmed
        # If every gram is common, still use the rarest one (better than empty-handed)
        if not discriminative:
            discriminative = [min(usable, key=lambda g: df_cache[g])]
        per_doc: Dict[int, float] = {}
        # ★A word's worth is how rare it is★. The coverage judgement uses this:
        # counting a document matched on a common word and one matched on a rare word as the same
        # single hit lets the common word dominate the ranking (several of 6 questions failed this way).
        word_idf[word] = max(_idf(df_cache[g], n_docs) for g in discriminative)
        for g in discriminative:
            df = df_cache[g]
            idf = _idf(df, n_docs)
            for row in db.execute(
                    "SELECT p.doc_id, p.tf, d.doclen, d.weight*d.prior AS weight FROM postings p "
                    "JOIN docs d ON d.id=p.doc_id WHERE p.term=?", (g,)):
                tf = row["tf"]
                norm = tf / (tf + K1 * (1 - B + B * (row["doclen"] / avg_len)))
                val = idf * norm * row["weight"]
                did = row["doc_id"]
                if val > per_doc.get(did, 0.0):
                    per_doc[did] = val      # among grams of the same word, keep only the maximum
        for did, val in per_doc.items():
            doc_score[did] = doc_score.get(did, 0.0) + val
            doc_words.setdefault(did, []).append(word)

    if kinds or sources:
        keep = set()
        q = "SELECT id FROM docs WHERE 1=1"
        args: List[str] = []
        if kinds:
            q += " AND kind IN (%s)" % ",".join("?" * len(kinds)); args += list(kinds)
        if sources:
            q += " AND source IN (%s)" % ",".join("?" * len(sources)); args += list(sources)
        for row in db.execute(q, args):
            keep.add(row["id"])
        doc_score = {k: v for k, v in doc_score.items() if k in keep}
        doc_words = {k: v for k, v in doc_words.items() if k in keep}
    return doc_score, doc_words, word_idf


def _hydrate(db: sqlite3.Connection, doc_id: int) -> Optional[sqlite3.Row]:
    return db.execute(
        "SELECT id,name,path,source,kind,title,description,body,mtime,weight,evidence_date "
        "FROM docs WHERE id=?", (doc_id,)).fetchone()


def _neighbors(db: sqlite3.Connection, doc_ids: Sequence[int]) -> Dict[int, str]:
    """One hop only. ★Two hops reaches nearly everything in a personal graph★ (the earlier system's own finding).

    The traversal is the chosen graph store's (§graphstore); "via" is the first seed, in `doc_ids` order,
    that reaches a neighbour — so it does not depend on the order a backend returns rows in.
    """
    from brain import graphstore
    seeds = [d for d in doc_ids if db.execute("SELECT 1 FROM docs WHERE id=?", (d,)).fetchone()]
    hops = graphstore.ask(db, "neighbors", seeds)
    out: Dict[int, str] = {}
    for did in seeds:
        src = db.execute("SELECT name FROM docs WHERE id=?", (did,)).fetchone()
        for nb in sorted(hops.get(int(did), ())):
            out.setdefault(nb, src["name"])
    out.pop(0, None)
    for did in doc_ids:
        out.pop(did, None)
    return out


# ★A document already always in context is not returned as a recall result★
#
# The index file is loaded into the model's context automatically every session. And because it
# mentions every topic a little, it ranks highly in recall often (measured 2026-08-18: top-8 for
# 7 of 18 queries, 5 of those at rank 2–3). The hook has only **two** slots, so filling one with
# something already in hand pushes the actual content out.
#
# ⛔ Compressing it makes it worse — BM25 normalises by length, so the shorter the file the heavier
#    its remaining words (right after compressing 22,882 → 17,051 characters it rose to rank 3).
# ⛔ It is not removed from the index — it is the starting point of the [[link]] graph, and removing
#    it cuts the neighbours. It is excluded ★from search results only★. resolve and neighbors still reach it.
def _always_in_context() -> set:
    """The documents the host already put in context (§hosts.Host.autoloaded_index) — by indexed name.
    ⛔ It used to be `{"MEMORY"}` for everyone: on a host that loads no index, the index file was dropped
    from results ★although nothing had put it in hand★."""
    try:
        from brain import hosts
        f = hosts.active().autoloaded_index(store.memory_dir())
    except Exception:                                    # noqa: BLE001
        return set()
    return {os.path.splitext(os.path.basename(f))[0]} if f else set()

# ★Identity bonus — separating "a document about X" from "a document citing X as an example"★ (2026-08-18)
#
# The ranker's aboutness defect: for one query about terraform, ranks 1–2 went to documents that
# **cited** terraform as an example and the right answer fell to rank 6. The cause was on the indexing
# side — field weights were added straight into tf, so 11 body occurrences (tf 11) beat 1 name occurrence (tf 6).
#
# ⛔ Fixing it in the index failed (measured A/B, the record is in `textindex.TF_MODE`):
#    saturating in-field repetition (sat) meets the contract for recall B top-3 (6→9) but ★short-query
#    recall collapses 41%→18%★, because capping the body's contribution cuts signal more than noise.
#    Compared at the same false-firing level, recall is sum 79% > log 65% > sat 47% — the old way is better.
#
# ★So the axes were separated★ — aboutness is a **ranking** problem, short queries an **absolute score** one.
# tf is left alone (preserving short queries) and a re-ranking bonus is given to top candidates only:
# if a query word appears in that document's **name, title or description**, add that word's IDF.
# A document with the word only in its body gets no bonus.
#
# Being a re-rank, it cannot rescue anything outside the top RERANK_TOP — every measured failure was rank 6–7.
ID_BOOST = float(os.environ.get("BRAIN_ID_BOOST", "0.8") or 0)
RERANK_TOP = 40

# How many history entries ride along in the result — too many and the output pushes the body out
HISTORY_IN_RESULT = int(os.environ.get("BRAIN_HISTORY_IN_RESULT", "4") or 4)

# ★Measured A/B (2026-08-18 · corpus 1147 · experiment DB isolated via BRAIN_HOME)★
#
#   boost/stage    threshold  A top-8  B top-3  short recall  false fire  gold top-3
#   0 (control)      9.54        2        6         41%          1          15
#   0.4 after      9.54      3        7         47%          1        18
#   0.8 before     9.99      3        7         50%          1        18
#   1.2 before    11.61      3        8 ✅      29% ❌       2        18
#   ★0.8 rank★       9.54        3 ✅     8 ✅      41%          1          18 ★adopted★
#   1.6 rank       9.54      3        8         38%          1        18
#
# before/after add the bonus **to the score** → the noise floor rises too, ★the threshold follows★,
# and short queries die (at 1.2, threshold 9.54→11.61 and recall 50%→29%).
# rank uses the bonus **for ordering only** → the threshold stays at 9.54 and there is no regression.
# All three paths are kept so the next session can reproduce this table.
ID_STAGE = os.environ.get("BRAIN_ID_STAGE", "rank")


def _identity_bonus(db: sqlite3.Connection, doc_ids: Sequence[int],
                    doc_words: Dict[int, List[str]],
                    word_idf: Dict[str, float]) -> Dict[int, float]:
    """For each document, the IDF sum of matched query words that ★also appear in an identity field★."""
    if not doc_ids or ID_BOOST <= 0:
        return {}
    out: Dict[int, float] = {}
    qmarks = ",".join("?" * len(doc_ids))
    for row in db.execute(
            "SELECT id, name, title, description FROM docs WHERE id IN (%s)" % qmarks,
            list(doc_ids)):
        idtext = " ".join((row["name"].replace("_", " "),
                           row["title"] or "", row["description"] or ""))
        # ⛔ Never compare by substring — the same tokenisation as the index is required to count the same things
        idwords = set(textindex.words(idtext))
        bonus = sum(word_idf.get(w, 0.0)
                    for w in doc_words.get(row["id"], ())
                    if w in idwords)
        if bonus:
            out[row["id"]] = bonus
    return out



def recall(db: sqlite3.Connection, query: str, k: int = 8,
           extra_terms: Sequence[str] = (), kinds: Optional[Sequence[str]] = None,
           sources: Optional[Sequence[str]] = None, graph: bool = True,
           bridge_floor: Optional[float] = None,
           log: bool = True, drop_index_docs: bool = True) -> List[dict]:
    stats = store.corpus_stats(db)
    n_docs = max(1, stats["docs"])
    avg_len = max(1.0, stats["avg_doclen"])
    # ★Transliteration bridge★ — if the query spells an English word phonetically, add the original as an expansion term.
    # Measured (2026-08-19): 8 of 20 silent short queries were this problem (a phonetic spelling of
    # "plan", of "apply"). Three rounds of threshold tuning all failed here; you cannot manufacture a missing signal with statistics.
    # ⛔ Done on the query side only — inflating the index in both languages shifts the whole IDF scale.
    bridged: Set[str] = set()
    if TRANSLIT and query:
        try:
            from brain import translit
            ko = [w for w in textindex.words(query) if textindex.script_of(w) == "hangul"]
            got = translit.bridge(db, ko, n_docs)
            if got:
                bridged = {v for v in got.values() if v not in (extra_terms or ())}
                extra_terms = list(extra_terms) + sorted(bridged)
        except Exception:                                    # noqa: BLE001
            pass                                             # recall still runs without the bridge

    # ★Personal lexicon bridge★ — connects the words the user says to the words the documents use (§lexicon).
    # Measured (2026-08-26): across the same 43 questions, removing synonyms takes 98% → 51%. Today a human
    # fills that 47pp by passing `terms` every time — a new user has neither that instruction nor their own term list.
    # ⛔ Same place and same rule as translit: widen on the query side only, and put the added words in
    #    `bridged` so they are ★excluded from the coverage calculation★ (a bridge must not inflate the score).
    if query:
        try:
            from brain import lexicon
            got = lexicon.bridge(db, query)
            add = [w for w in got if w not in (extra_terms or ())]
            if add:
                bridged |= set(add)
                extra_terms = list(extra_terms) + add
        except Exception:                                # noqa: BLE001
            pass                                         # recall still runs without the bridge

    pairs = textindex.query_terms(query, extra_terms)
    n_words = max(1, len(pairs))
    own_words = {w for w, _ in textindex.query_terms(query)}   # bridges and expansions removed

    doc_score, doc_words, word_idf = _score_terms(
        db, pairs, n_docs, avg_len, kinds, sources)

    # ⛔ ★Automatic query expansion (pseudo-relevance feedback) measured **worse**★ (2026-08-10)
    #   Borrowing rare words from the first-pass top documents and searching again: top-3 2 → **1**, top-8 3 → **2**.
    #   The cause is clear — a natural-language query with no expansion terms is already wrong in the
    #   first pass, and **words borrowed from the wrong document drag the query further from the answer**.
    #   PRF only works when the first pass is already decent, and that condition does not hold here.
    #   So it was not added. What fills this gap is ★whoever knows the synonyms★:
    #     · today = the caller (the agent) widens it via `extra_terms` → 9/9 (verified below)
    #     · later = a local embedding does it automatically (where no human is present, like the recall hook)
    # ★Coverage is measured by the sum of rarity, not by word count★
    # Counting a document matched on a common query word and one matched on a rare word as the
    # same single hit lets a word present in any document dominate the ranking.
    # ★Penalise a document that fails to cover the query's most valuable word★
    #
    # Coverage is "covered IDF / total IDF". In a long query that lets ★a document covering nine
    # function words beat one covering a single content word★, because the function words make up
    # most of the total IDF (measured: the top result for one long query did not match the key
    # content word at all). Summed, the absence of that one word is buried.
    # → So the presence of the highest-IDF word is checked ★separately★.
    #
    # ⛔⛔ ★And this too was rejected by measurement (2026-08-19). Default 0 = off.★
    #    At PENALTY 0.2 the recall contract already breaks (B top-3 8→7) and false firings go 1→3.
    #    At 0.5, B top-8 9→7 and gold-in-top-3 18→15.
    #    The reason: ★the query's highest-IDF word cannot be trusted.★ It may be an inflection
    #    accident (RARE_DF above), an emotional aside, or a typo. Penalising against an untrustworthy
    #    reference penalises the wrong documents.
    #
    #    ★What the two prescriptions together revealed★ — the problem is not "function words have IDF"
    #    but that ★the query is long and its content-word ratio is low★ (that query had 3 content words
    #    out of 14). That is a problem of understanding the query; df statistics cannot solve
    #    it — it is the ceiling of the lexical approach.
    #
    # ⛔ A third prescription (deciding to fire from the gap between rank 1 and 2) was also rejected —
    #    it never entered the code. The distributions overlap: the gaps for the queries needing rescue
    #    were 5.56, 2.81, 2.19, −0.29, −0.65, while ★the false-firing controls had 4.16, 4.07, 3.86,
    #    3.09, 2.78★. Wherever the threshold goes, rescuing 3 costs 6 or more false firings. Small talk spikes too.
    TOP_PENALTY = float(os.environ.get("BRAIN_TOP_TERM_PENALTY", "0") or 0)
    _top_word = max(word_idf, key=word_idf.get) if (TOP_PENALTY > 0 and word_idf) else None

    # ★Words added by a bridge do not enter the coverage denominator★
    #
    # Coverage is "how much of the query's worth was covered". Putting bridge words in the denominator
    # makes the query effectively longer and ★lowers every document's score★ — measured (2026-08-19):
    # turning the bridge on raised the correct document 9.21→10.5 but broke the recall contract
    # (B top-3 8→7) and collapsed short-query recall 47%→35%. A bridge is ★a bonus if present★ and
    # not a condition the user asked for, so it may contribute to the score but not to the judgement.
    # (The same prescription as the aboutness fix — used for ranking only, never for the threshold.)
    total_idf = sum(v for k, v in word_idf.items() if k not in bridged) or 1.0
    if ID_BOOST > 0 and ID_STAGE == "before" and doc_score:
        _head = sorted(doc_score, key=lambda d: -doc_score[d])[:RERANK_TOP]
        for _did, _b in _identity_bonus(db, _head, doc_words, word_idf).items():
            doc_score[_did] = doc_score.get(_did, 0.0) + ID_BOOST * _b
    for did in list(doc_score):
        matched = set(doc_words[did])
        cov = sum(word_idf.get(w, 0.0) for w in matched if w not in bridged) / total_idf
        doc_score[did] *= (COVERAGE_FLOOR + (1 - COVERAGE_FLOOR) * cov)
        if _top_word is not None and _top_word not in matched:
            doc_score[did] *= (1.0 - TOP_PENALTY)

    ranked = sorted(doc_score.items(), key=lambda kv: kv[1], reverse=True)

    # ★Identity re-rank★ — top candidates only (see the ID_BOOST comment above). Additive, not multiplicative:
    # multiplying widens an already-high score further and disturbs even whether a short query clears the threshold.
    if ID_BOOST > 0 and ID_STAGE in ("after", "rank") and ranked:
        head = [d for d, _ in ranked[:RERANK_TOP]]
        bonus = _identity_bonus(db, head, doc_words, word_idf)
        if bonus:
            if ID_STAGE == "rank":
                # ★Use the bonus for ordering only; never put it in the returned score★
                #
                # Why: a bonus raises signal **and noise together**. Put it in the score and the noise
                # floor rises, the threshold follows (measured: BOOST 1.2 → threshold 9.54→11.61) and
                # short-query recall dies 50%→29%. Aboutness is a ranking problem, so fix ranking only;
                # whether to fire must be judged on the bonus-free score to keep the ruler steady.
                key = {d: s + ID_BOOST * bonus.get(d, 0.0)
                       for d, s in doc_score.items()}
                ranked = sorted(doc_score.items(), key=lambda kv: -key[kv[0]])
            else:
                for did, b in bonus.items():
                    doc_score[did] = doc_score.get(did, 0.0) + ID_BOOST * b
                ranked = sorted(doc_score.items(), key=lambda kv: kv[1], reverse=True)

    # ★The most this query could earn★ — the IDF sum of the words the user actually wrote.
    # A single score term is capped at idf×1 (saturation and weight at maximum), so this sum is the effective ceiling.
    # A short query has a low ceiling and can never reach an absolute threshold — the firing decision uses this value
    # (§hook.select). ⛔ Bridge-generated expansions are excluded from the denominator: the user did not ask for them.
    q_mass = sum(v for w, v in word_idf.items() if w in own_words) or 1.0

    # ★Direct matches use all of k★ (2026-08-20) — EXPANSION_SHARE (34%) used to ★reserve★ slots for the
    # graph. That reservation took the answer's slot, and it showed up as a regression (contract B top-8 9→8).
    # Related context does not compete inside k; it is ★an extra appended afterwards★ (§MAX_RELATED below).
    direct_slots = k
    picked = ranked[:max(1, direct_slots)]
    picked_ids = [d for d, _ in picked]
    results: List[dict] = []
    needles = [w for w, _ in pairs]

    for did, score in picked:
        row = _hydrate(db, did)
        if row is None:
            continue
        results.append(_present(row, score, sorted(set(doc_words[did])), None,
                                needles, db, q_mass))

    # If slots remain, fill them with direct matches (so k is not lost when no bridge opens)
    if len(results) < k:
        have = {r["name"] for r in results}
        for did, score in ranked[len(picked):]:
            if len(results) >= k:
                break
            row = _hydrate(db, did)
            if row is None or row["name"] in have:
                continue
            results.append(_present(row, score, sorted(set(doc_words[did])), None,
                                needles, db, q_mass))


    # ★The graph is not a search channel — it is a supporting slot that conveys relatedness★ (2026-08-20)
    #
    # User instruction: *"Think about what the graph is for. It conveys the relatedness of information
    # through connected data. It is not the whole of a conversation or a memory in itself."*
    #
    # Measured three times, same answer three times — questions where a neighbour ★rescued★ the answer:
    # 08-13 0/34 · 08-19 0/34 · 08-20 ★1/43★. Useless as a channel.
    # Where it is useful is ★after the answer is already right★, telling you "what this is tied to".
    # The links are `[[links]]` a human wrote by hand, so their evidence of relatedness beats any score.
    #
    # Four rules (all of them came from measurement):
    #   ① Open it only when the seed cleared ★the confidence line (the hook threshold)★ — neighbours of a weak guess are noise.
    #   ② ★Never intrude on k★ — it is an extra appended afterwards (the regression where a reservation took the answer's slot).
    #   ③ Pick what also overlaps the query — following the seed's links alone returns anything from a hub.
    #   ④ A document that clears the threshold on its own is not demoted to "related" — it goes to the answer slot.
    # ⛔ So this bridge ★cannot turn silence into a firing★ (new false firings are structurally zero).
    if graph and picked:
        floor = bridge_floor
        if floor is None:
            try:
                from brain import calibrate           # deferred import (avoids a cycle)
                floor = calibrate.threshold(db)
            except Exception:                          # noqa: BLE001
                floor = float("inf")                   # no ruler → the bridge stays shut (§calibrate.NO_RULER)
        if picked[0][1] >= floor:
            shown = {r["name"] for r in results}
            seeds = picked_ids[:SEED_HITS]
            cand = sorted(_neighbors(db, seeds).items(),
                          key=lambda kv: -doc_score.get(kv[0], 0.0))
            added = 0
            for did, via in cand:
                if added >= MAX_RELATED:
                    break
                if doc_score.get(did, 0.0) >= floor:
                    continue                           # this one belongs in the answer slot
                row = _hydrate(db, did)
                if row is None or row["name"] in shown:
                    continue
                results.append(_present(row, 0.0, [], via, needles, db, q_mass,
                                        related=True))
                shown.add(row["name"])
                added += 1

    if drop_index_docs:
        in_hand = _always_in_context()
        results = [r for r in results if r["name"] not in in_hand]

    if log:
        # ★Recording must never kill recall★ — this function's job is to return memories, and
        # noting "it was used" is a side task. After usage keys moved rowid→path on 2026-08-11, an
        # MCP server still holding the old module blew up right here and ⛔ **recall itself failed**
        # (the user could not get their memories out and had to grep files by hand). A side task's
        # failure must not stop the main one — but it must not be **swallowed silently** either (say it on stderr).
        try:
            _record(db, query, extra_terms, results)
        except Exception as exc:                             # noqa: BLE001
            print("[brain] " + i18n.t("search.usage_record_failed", err=exc) + "\n"
                  "[brain] " + i18n.t("search.schema_diverged"), file=sys.stderr)
    return results


def _present(row: sqlite3.Row, score: float, matched: List[str],
             via: Optional[str], needles: Sequence[str],
             db: Optional[sqlite3.Connection] = None,
             q_mass: float = 0.0, related: bool = False) -> dict:
    age = _age_days(row["mtime"])
    ev = row["evidence_date"] or ""
    ev_age = -1
    if ev:
        try:
            y, m, d = (int(x) for x in ev.split("-"))
            ev_age = int((time.time() - time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))) / 86400)
        except (ValueError, OverflowError):
            ev_age = -1
    return {
        "name": row["name"],
        "path": row["path"],
        "source": row["source"],
        "kind": row["kind"],
        "title": row["title"],
        "description": row["description"],
        "score": round(score, 4),
        # ★Ratio against the most this query could earn★ — used to decide firing for short queries (§hook.select).
        # An absolute score cannot reach the threshold when the query is short and its ceiling low. This value is
        # "how much of the asked rarity was covered", so it is length-neutral. It can exceed 1 (document weights multiply in).
        "ratio": round(score / q_mass, 3) if q_mass else 0.0,
        "matched": matched,
        "via_graph": via,
        # ★This line is related context, not a search result★ (2026-08-20)
        # User instruction: *"Think about what the graph is for. It conveys the relatedness of
        # information through connected data. It is not the whole of a conversation or a memory."*
        # Whoever consumes this (hook, MCP, a person) must be able to tell an answer from a side branch.
        "related": bool(related),
        "why": ("link: one hop from %s" % via) if via else ("words: " + ", ".join(matched[:6])),
        "age_days": int(age),
        # ★Always return the evidence date with the result★ — not as a warning but as **material for judgement**.
        # However recent the file, if the evidence is six months old that number must not be quoted as current fact.
        "evidence_date": ev,
        "evidence_age_days": ev_age,
        "stale": _is_stale(row["kind"], ev_age, age),
        "excerpt": _excerpt(row["body"], needles),
        # ★Return the change history with the result★ — this is where goal ③ (history) belongs.
        #
        # Putting it in tf was disproven (see the store._W_HISTORY comment): meta questions have no topic
        # word, so enlarging the index does not catch them. History is useful **after** something is found —
        # "when and how did this memory change" is the question of someone already holding the document,
        # and its answer is what lets them judge "is this still true". (Same nature as the evidence date.)
        # The git mapping is built once and cached, so the per-query cost is effectively zero.
        "history": store.history_of(row["path"], db)[:HISTORY_IN_RESULT],
    }


def _record(db: sqlite3.Connection, query: str, extra_terms, results: List[dict]) -> None:
    """★Record that it was used★ — the only evidence self-improvement has (§health.suggestions)."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db:
        db.execute(
            "INSERT INTO recalls(ts,query,terms,hits,top) VALUES(?,?,?,?,?)",
            (now, query[:500], json.dumps(list(extra_terms), ensure_ascii=False)[:500],
             len(results), json.dumps([r["name"] for r in results[:5]], ensure_ascii=False)))
        for r in results:
            row = db.execute("SELECT path FROM docs WHERE name=?", (r["name"],)).fetchone()
            if not row:
                continue
            db.execute(
                "INSERT INTO usage(path,hits,last_hit) VALUES(?,1,?) "
                "ON CONFLICT(path) DO UPDATE SET hits=hits+1, last_hit=excluded.last_hit",
                (row["path"], now))
        db.execute("DELETE FROM recalls WHERE id < (SELECT MAX(id)-5000 FROM recalls)")


def resolve(db: sqlite3.Connection, name: str) -> Optional[sqlite3.Row]:
    """Called by name or by alias, it reaches the same document."""
    return db.execute(
        "SELECT d.id, d.name FROM name_map m JOIN docs d ON d.id=m.doc_id "
        "WHERE m.key=? LIMIT 1", (name,)).fetchone()


def semantic(db: sqlite3.Connection, query: str, k: int = 3,
             min_cos: Optional[float] = None) -> List[dict]:
    """★Memories close in meaning★ — found even with no word in common (remote embeddings).

    ⛔ ★Deliberately not folded into `recall`★ (2026-08-21). The day before, folding the graph into
    recall let a slot reservation push the answer out and break the contract (B top-8 9→8). The lexical
    path is already bound by contracts and works well (98% when synonyms are given). So the semantic path
    stays ★a separate function★, and each caller combines it to suit its own place:
      · MCP `recall`  — appended after the lexical results as "close in meaning"
      · the hook      — called ★only when lexical goes silent★ (508ms round trip, measured, and it costs money)

    Returned rows have the same shape as recall rows, with `via_vector=True` and `cosine` attached.
    `score` is 0 — ★mixing it into the BM25 scale would rob the threshold of meaning★.
    """
    from brain import vectors
    try:
        hits = vectors.search(db, query, k=k)
    except Exception:                                    # noqa: BLE001
        return []                                        # key, quota and network failures stay quiet
    # ★The gate is a ratio★ (2026-08-24) — an absolute cosine cannot separate under this model's
    # distribution (controls reach 0.73–0.76). §vectors.min_ratio
    floor = vectors.min_ratio(db) if min_cos is None else min_cos
    out: List[dict] = []
    in_hand = _always_in_context()
    for doc_id, cos, ratio in hits:
        if ratio < floor:
            continue
        row = _hydrate(db, doc_id)
        if row is None or row["name"] in in_hand:
            continue
        r = _present(row, 0.0, [], None, [], db, 0.0)
        r["via_vector"] = True
        r["cosine"] = round(cos, 4)
        r["ratio"] = round(ratio, 4)
        r["why"] = "close in meaning (ratio %.3f · cosine %.3f)" % (ratio, cos)
        out.append(r)
    return out


class JudgeUnavailable(RuntimeError):
    """The judge ★could not be called★ — this must be distinguished from "there is no answer".

    ⛔ On 2026-08-25 this produced a false green in a check. `judged()` returned `[]` even on a call
    failure, and `verify_short` counted that `[]` as ★zero false firings★ — measuring the same control
    right before had given 15/45. One layer down, `rerank.score` already knew this trap and returned
    None on failure (§rerank, the RPM comment: "counting that failure as a score of 0 produces the
    false conclusion that the reranker is useless"), and the layer above flattened it again.
    ★A defect comes back to life above the layer that fixed it — wherever one signal-swallowing place remains.★
    Production (the hook) must stay quiet, so the default is still silence; ★only checks★ call with strict.
    """


def judged(db: sqlite3.Connection, query: str, k: int = 20,
           strict: bool = False, cache: bool = True) -> List[dict]:
    """★Two-stage search★ — cosine picks k candidates, the reranker judges whether they are the answer.

    Why this shape (measured 2026-08-25): neither absolute cosine nor a ratio is a ruler for "is this the
    answer". Controls (real utterances needing no memory) often score a higher ratio than the correct
    answers, so no threshold separated them (above threshold: 3/34 correct · 0/8 silences rescued).
    Putting up a judge that ★reads query and candidate together★ separated them (top-1 correct 15/34 · 3/8 rescued).
    · Anthropic contextual retrieval: contextual embeddings −35% → +BM25 −49% → ★+reranking −67%★
    """
    from brain import rerank, vectors
    try:
        hits = vectors.search(db, query, k=k)
    except Exception as exc:                             # noqa: BLE001
        if strict:
            raise JudgeUnavailable("vector search failed: %s" % exc) from exc
        return []
    cands, rows = [], []
    in_hand = _always_in_context()
    for doc_id, cos, ratio in hits:
        row = _hydrate(db, doc_id)
        if row is None or row["name"] in in_hand:
            continue
        rows.append((row, cos, ratio))
        cands.append({"name": row["name"], "description": row["description"],
                      "body": (row["body"] or "")[:500]})
    if not cands:
        return []
    # ★Pass the connection that is already open★ — the budget counter writes, so opening a new one here
    # would make the hook create a second connection every prompt and possibly wait on a WAL lock (time budget matters here).
    # ★If chunking is on, ask in parts★ (default 0 = off · §rerank.CHUNK)
    #   It reduces answers splitting on identical input: 20 at once 62% → 5 at a time 100% (2026-08-31).
    # ⛔ `cache=False` exists for ★measurement★ — a measurement reading its own earlier answer
    #    "reproduces" anything (that is what happened to the precision axis on 2026-08-31). Production keeps the cache on.
    scores = rerank.score_chunked(query, cands, db=db, cache=cache)
    if scores is None:                                   # ★call failure★ — not the absence of an answer
        if strict:
            raise JudgeUnavailable("judge call failed (quota or network) — this is not silence")
        return []                                        # ⛔ production stays quiet
    if not scores:                                       # [] = no candidates
        return []
    floor = rerank.min_score(db)
    best = max(range(len(scores)), key=lambda i: scores[i])
    if scores[best] < floor:
        return []
    row, cos, ratio = rows[best]
    r = _present(row, 0.0, [], None, [], db, 0.0)
    r["via_vector"] = True
    r["cosine"] = round(cos, 4)
    r["ratio"] = round(ratio, 4)
    r["rerank"] = scores[best]
    r["why"] = "found by meaning, chosen by the judge (relevance %.0f/10)" % scores[best]
    # ★The expensive layer teaches the cheap one★ (2026-08-26) — that lexical went silent while meaning
    # found it means some word in this document ★means the same as a query word but is spelled differently★.
    # Written down, it is found for free next time (508ms paid remote round trip → 0ms local).
    # ⛔ Statistics cannot manufacture this knowledge — a 2026-08-26 attempt via corpus co-occurrence
    #    produced 720,000 noisy pairs and was discarded (§lexicon comment). Knowledge comes from the layer that has it.
    try:
        from brain import lexicon, textindex
        body = (row["description"] or "") + " " + (row["body"] or "")[:1200]
        lexicon.learn_from_vector(db, query, row["name"], textindex.words(body))
    except Exception:                                    # noqa: BLE001
        pass                                             # a learning failure must not block recall
    return [r]


def neighbors(db: sqlite3.Connection, name: str, depth: int = 1) -> dict:
    row = resolve(db, name)
    if not row:
        like = db.execute("SELECT name FROM docs WHERE name LIKE ? LIMIT 5",
                          ("%" + name + "%",)).fetchall()
        return {"error": "not found: %s" % name,
                "did_you_mean": [r["name"] for r in like]}
    out = {"name": row["name"], "outgoing": [], "incoming": [], "dangling": []}
    for r in db.execute("SELECT dst_name FROM links WHERE src_id=?", (row["id"],)):
        hit = resolve(db, r["dst_name"])
        if hit:
            out["outgoing"].append(hit["name"])
        else:
            out["dangling"].append(r["dst_name"])
    from brain import graphstore
    for src_id in graphstore.ask(db, "incoming", row["id"]):
        r = db.execute("SELECT name FROM docs WHERE id=?", (src_id,)).fetchone()
        if r:
            out["incoming"].append(r["name"])
    out["outgoing"] = sorted(set(out["outgoing"]))
    out["incoming"] = sorted(set(out["incoming"]))
    out["dangling"] = sorted(set(out["dangling"]))
    return out
