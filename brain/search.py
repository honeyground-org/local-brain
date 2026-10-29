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


def _df_table(db: sqlite3.Connection, pairs) -> Dict[str, int]:
    """Document frequency of every gram the query's words cut into (absent = never indexed)."""
    df_cache: Dict[str, int] = {}
    all_terms = sorted({g for _, gs in pairs for g in gs})
    if all_terms:
        qmarks = ",".join("?" * len(all_terms))
        for row in db.execute(
                "SELECT term, df FROM terms WHERE term IN (%s)" % qmarks, all_terms):
            df_cache[row["term"]] = row["df"]
    return df_cache


def _discriminative(grams: Sequence[str], df_cache: Dict[str, int], stop_df: int) -> List[str]:
    """The grams that carry a word — [] when none of them was ever indexed."""
    usable = [g for g in grams if df_cache.get(g, 0) > 0]
    if not usable:
        return []
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
    return discriminative


def query_weights(db: sqlite3.Connection, query: str) -> List[Tuple[str, float, List[str]]]:
    """The question's own words, each with its worth and the grams that carry it — ★weighed exactly as
    `recall` weighs them★ (same grams, same IDF), without bridges or expansions. [(word, idf, grams)]"""
    n_docs = max(1, store.corpus_stats(db)["docs"])
    pairs = textindex.query_terms(query)
    df_cache = _df_table(db, pairs)
    stop_df = max(2, int(n_docs * STOP_DF_RATIO))
    out: List[Tuple[str, float, List[str]]] = []
    for word, grams in pairs:
        disc = _discriminative(grams, df_cache, stop_df)
        if disc:
            out.append((word, max(_idf(df_cache[g], n_docs) for g in disc), disc))
    return out


def _score_terms(db: sqlite3.Connection, pairs, n_docs: int, avg_len: float,
                 kinds: Optional[Sequence[str]], sources: Optional[Sequence[str]],
                 scope: Optional[str] = None
                 ) -> Tuple[Dict[int, float], Dict[int, List[str]], Dict[str, float]]:
    """Accumulate scores per word — summing per gram automatically favours longer words."""
    df_cache = _df_table(db, pairs)

    doc_score: Dict[int, float] = {}
    doc_words: Dict[int, List[str]] = {}
    word_idf: Dict[str, float] = {}
    stop_df = max(2, int(n_docs * STOP_DF_RATIO))

    for word, grams in pairs:
        discriminative = _discriminative(grams, df_cache, stop_df)
        if not discriminative:
            continue
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

    if kinds or sources or scope:
        keep = set()
        q = "SELECT id FROM docs WHERE 1=1"
        args: List[str] = []
        if kinds:
            q += " AND kind IN (%s)" % ",".join("?" * len(kinds)); args += list(kinds)
        if sources:
            q += " AND source IN (%s)" % ",".join("?" * len(sources)); args += list(sources)
        if scope:
            # ★A scope keeps its own memories and the unscoped ones★ (orbit-1 O4) — another project's
            # scoped memories are out, a memory that declared nothing is in, flagged as unscoped by the caller.
            q += " AND (project_id = ? OR project_id = '')"; args.append(scope)
        for row in db.execute(q, args):
            keep.add(row["id"])
        doc_score = {k: v for k, v in doc_score.items() if k in keep}
        doc_words = {k: v for k, v in doc_words.items() if k in keep}
    return doc_score, doc_words, word_idf


def _hydrate(db: sqlite3.Connection, doc_id: int) -> Optional[sqlite3.Row]:
    return db.execute(
        "SELECT id,name,path,source,kind,title,description,body,mtime,weight,evidence_date,project_id "
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
           log: bool = True, drop_index_docs: bool = True,
           bridge: Optional[bool] = None, scope: Optional[str] = None) -> List[dict]:
    """`bridge` — the person's learned synonyms (§lexicon): None follows the measured decision (§lexicon.decide,
    made on the hook's firing and noise), True asks for them, False leaves them out."""
    _LAST_RECALL["bridged"] = []
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
    if query and bridge is not False:
        try:
            from brain import lexicon
            # asked for → the knobs the measurement chose, passed out loud (§lexicon.bridge measures regardless)
            got = lexicon.bridge(db, query, *lexicon.knobs(db)) if bridge else lexicon.bridge(db, query)
            add = [w for w in got if w not in (extra_terms or ())]
            if add:
                bridged |= set(add)
                extra_terms = list(extra_terms) + add
                _LAST_RECALL["bridged"] = sorted(add)
        except Exception:                                # noqa: BLE001
            pass                                         # recall still runs without the bridge

    pairs = textindex.query_terms(query, extra_terms)
    n_words = max(1, len(pairs))
    own_words = {w for w, _ in textindex.query_terms(query)}   # bridges and expansions removed

    doc_score, doc_words, word_idf = _score_terms(
        db, pairs, n_docs, avg_len, kinds, sources, scope)

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
                if scope and (row["project_id"] or "") not in (scope, ""):
                    continue                           # a neighbour from another project's scope stays out
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
        "project_id": ((row["project_id"] or "") if "project_id" in row.keys() else "") or None,
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
      · MCP `recall`  — on request (`semantic_k`), apart from the word results, through `meaning()`
                        (until 2026-10-13 this line said it was appended; it never was)
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


def meaning(db: sqlite3.Connection, query: str, k: int, exclude: Sequence[str] = (),
            kinds: Optional[Sequence[str]] = None, sources: Optional[Sequence[str]] = None,
            width: int = 0,
            scope: Optional[str] = None) -> Tuple[List[dict], str]:
    """★The k memories closest in meaning that the caller does not already have★ — for a caller that
    asked for them by number (MCP `recall`'s `semantic_k`). Returns (rows, why-none).

    Unlike `semantic()` (the hook's unattended fallback) there is no ratio gate: the caller chose how many to
    read, and every row says how close it is. Measured 2026-10-13 on the author's 66 labelled questions,
    question alone: meaning's top 3 held the answer for 24 (words' for 22), and words and meaning found
    different answers (13 only by words, 15 only by meaning). Each row reads the passage of the chunk that
    matched (§evidence), not the head — cut to `width`, the length the caller shows, so the words that
    matched are inside what is shown. `exclude` (names already shown), `kinds` and `sources` apply as in
    `recall`; memories always in context are left out. ⛔ A failure is said, not swallowed — why-none
    carries the reason (no embedding engine chosen, a quota, the network) when rows is empty for that reason.
    """
    from brain import vectors
    if k <= 0:
        return [], ""
    skip = set(exclude) | set(_always_in_context())
    out: List[dict] = []
    want = k + len(skip)
    n_docs = max(1, store.corpus_stats(db)["docs"])
    while True:
        try:
            hits = vectors.search(db, query, k=want)
        except Exception as exc:                         # noqa: BLE001
            return [], "%s: %s" % (type(exc).__name__, str(exc)[:160])
        out = []
        rows = []
        for doc_id, cos, ratio in hits:
            row = _hydrate(db, doc_id)
            if row is None or row["name"] in skip:
                continue
            if kinds and row["kind"] not in kinds:
                continue
            if sources and row["source"] not in sources:
                continue
            if scope and (row["project_id"] or "") not in (scope, ""):
                continue
            rows.append((row, cos, ratio))
            if len(rows) == k:
                break
        # ★Filters can eat the pool★ — ask again for twice as many until k are found or none are left.
        if len(rows) == k or len(hits) < want or want >= n_docs:
            break
        want *= 2
    chunk_of = vectors.best_chunks(db, query, [row["id"] for row, _, _ in rows])
    weights = query_weights(db, query)
    for row, cos, ratio in rows:
        r = _present(row, 0.0, [], None, [], db, 0.0)
        r["via_vector"] = True
        r["cosine"] = round(cos, 4)
        r["ratio"] = round(ratio, 4)
        r["why"] = "close in meaning, not a word match (cosine %.3f · ratio %.3f)" % (cos, ratio)
        if row["id"] in chunk_of:
            ev = evidence(db, query, row, chunk_of[row["id"]][0], weights, width=width)
            if ev is not None:
                r["evidence"] = {key: ev[key] for key in ("chunk", "start", "end", "rule")}
                r["excerpt"] = _WS.sub(" ", ev["text"]).strip()
        out.append(r)
    return out, ""


_LAST_RECALL: Dict[str, list] = {"bridged": []}


def last_bridged() -> List[str]:
    """The learned synonyms the last `recall` in this process added to the question."""
    return list(_LAST_RECALL.get("bridged") or [])


# ★When the words are unsure, three rankings decide the order★ (2026-10-14 · an agent's `recall` only)
#
# The answer is usually already among the candidates — on the author's 66 labelled questions it sat in the
# top 50 of words or meaning for 63 (question alone) — and was lost in the ordering: 28 sat at 4–50. Three
# rankings, each blind where another sees: words (BM25 over the whole body, which docks long documents),
# meaning (the chunks embedded), and ★the passage★ — how grep sees a document: the one window that holds the
# most of the question, wherever it sits, with no penalty for the length around it. Merged by reciprocal rank.
# Measured, no model, as `recall` runs with the agent's terms (unsure → learned synonyms first, then this):
# top 3 43 → 48 of 66 (paired 95% +0..+10); answers anywhere in what is shown 52 → 52, read 9.0 → 8.3.
# ⛔ Not in the hook: there the order of what it attaches did not move (15 → 13–14 · 18 → 19), and synonyms
#    deciding ★whether★ to attach took prompts that need no memory from 3 to 17 of 34 attached.
# ⛔ Graph agreement (what the top candidates link to) was measured as a fourth ranking and lowered every arm.
FUSE_POOL = 20        # how deep each ranking is read
FUSE_K = 60           # the reciprocal-rank constant — the usual one, not tuned here
FUSE_WIDTH = 1200     # the passage window — one embedded chunk's width (§vectors.CHUNK_CHARS)


def passage_worth(text: str, weights, width: int = FUSE_WIDTH) -> float:
    """The most question worth one window of `width` characters holds anywhere in `text` — a word counts
    once, with its IDF, if a gram that carries it (§query_weights) starts inside the window."""
    low = (text or "").lower()
    occ: List[Tuple[int, int, float]] = []
    for wi, (_word, idf, grams) in enumerate(weights):
        for g in grams:
            g = g.lower()
            i = low.find(g)
            while i >= 0:
                occ.append((i, wi, idf))
                i = low.find(g, i + 1)
    if not occ:
        return 0.0
    occ.sort()
    best = cur = 0.0
    j = 0
    held: Dict[int, int] = {}
    for pos, wi, idf in occ:
        held[wi] = held.get(wi, 0) + 1
        if held[wi] == 1:
            cur += idf
        while occ[j][0] < pos - width:
            _p, wj, idfj = occ[j]
            held[wj] -= 1
            if held[wj] == 0:
                cur -= idfj
            j += 1
        best = max(best, cur)
    return best


def fused(db: sqlite3.Connection, query: str, rows: List[dict], k: int,
          extra_terms: Sequence[str] = (), kinds: Optional[Sequence[str]] = None,
          sources: Optional[Sequence[str]] = None, width: int = 0,
          scope: Optional[str] = None) -> Tuple[List[dict], str]:
    """Words (`rows`, best first), meaning and the whole-body passage merged by reciprocal rank — the top `k`
    as `recall` rows (a document only meaning found reads the passage of the chunk that matched, §meaning).
    Returns (rows, why meaning gave nothing) — without meaning the other two still order."""
    words = [r for r in rows if not r.get("related")][:FUSE_POOL]
    mrows, why = meaning(db, query, FUSE_POOL, kinds=kinds, sources=sources, width=width, scope=scope)
    by_path: Dict[str, dict] = {}
    for r in mrows + words:                              # a document words found keeps its words row
        by_path[r["path"]] = r
    pool = list(dict.fromkeys([r["path"] for r in words] + [r["path"] for r in mrows]))
    weights = query_weights(db, " ".join([query] + list(extra_terms)))
    text: Dict[str, str] = {}
    for p in pool:
        d = db.execute("SELECT title, description, body FROM docs WHERE path=?", (p,)).fetchone()
        text[p] = "\n".join(x or "" for x in (d["title"], d["description"], d["body"])) if d else ""
    lists = [[r["path"] for r in words], [r["path"] for r in mrows]]
    base = {p: i for i, p in enumerate(pool)}
    worth = {p: passage_worth(text[p], weights) for p in pool}
    lists.append(sorted(pool, key=lambda p: (-worth[p], base[p])))
    score: Dict[str, float] = {}
    for lst in lists:
        for i, p in enumerate(lst):
            score[p] = score.get(p, 0.0) + 1.0 / (FUSE_K + i + 1)
    order = sorted(pool, key=lambda p: (-score[p], base[p]))
    return [by_path[p] for p in order[:k]], why


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


EXCERPTS = ("head", "evidence")     # what the judge reads of each candidate (§judged)


def judge_policy(k: int = 20, excerpt: str = "head") -> str:
    """★What the judge is shown★, named from the settings in force — which candidates and which part of
    each. A judge score is on the scale of (model, prompt, ★this★): change the candidates or the excerpt
    and the same model scores differently, so a record says which one it was measured under."""
    from brain import rerank
    return "vector%d+%s%d" % (min(k, rerank.MAX_CAND), excerpt, rerank.EXCERPT)


def _passage(text: str, weights, lo: int, hi: int, width: int, empty: str) -> Tuple[int, int, str]:
    """The window of at most `width` characters in `text[lo:hi]` that holds the most query worth.

    A word counts once, with its IDF, if any gram that carries it (§query_weights) lies wholly inside the
    window — repeats and overlapping grams add nothing. Of equally good windows the earliest wins. With no
    word in the region the window is the region's centre (`empty="centre"`) or its start (`"start"`).
    Returns (start, end, rule) in `text` coordinates.
    """
    low = text.lower()
    if len(low) != len(text):                            # a case fold that changes length moves offsets
        low = text
    occ: List[Tuple[int, int, int]] = []
    for wi, (_word, _idf, grams) in enumerate(weights):
        for g in grams:
            g = g.lower()
            p = low.find(g, lo, hi)
            while p >= 0:
                occ.append((p, p + len(g), wi))
                p = low.find(g, p + 1, hi)
    if hi - lo <= width:
        if not occ:
            return lo, hi, "short-" + empty
        return lo, hi, "words"
    if not occ:
        s = lo + (hi - lo - width) // 2 if empty == "centre" else lo
        return s, s + width, empty
    starts = {lo}
    for a, b, _ in occ:
        starts.add(max(lo, min(a, hi - width)))          # the window that opens on this match
        starts.add(max(lo, min(b - width, hi - width)))  # and the one that closes on it
    best = None
    for s in sorted(starts):
        e = s + width
        worth = sum(weights[wi][1] for wi in {w for a, b, w in occ if a >= s and b <= e})
        if best is None or worth > best[0]:
            best = (worth, s, e)
    return best[1], best[2], "words"


def evidence(db: sqlite3.Connection, query: str, row, chunk_no: Optional[int] = None,
             weights=None, width: int = 0) -> Optional[dict]:
    """★The part of a document that answers★ — a passage of the body for the judge to read instead of its head.

    Measured 2026-10-13: the judge read a candidate's description and the first 420 characters of its
    body, while for half the answers in the vector top 20 the chunk that matched was further in. Given the
    chunk the vector search matched (§vectors.best_chunks), the passage is taken from ★that chunk's body★;
    without one (a document only words found) from the whole body. Inside it, the window that holds the
    most of the question's worth (§_passage). Rules fixed before the experiment that tests them
    (docs/notes/retrieval-fusion-plan.md). {chunk, start, end, rule, text} in `body.strip()` coordinates.
    """
    from brain import rerank, vectors
    width = width or rerank.EXCERPT
    body = (row["body"] or "").strip()
    if chunk_no is not None:
        if vectors.PROVIDER == "embeddinggemma":
            from brain import localembed
            spans = localembed.spans(db, row["id"])
        else:
            spans = vectors.chunk_spans(body)
        if not 0 <= chunk_no < len(spans):
            return None
        lo, hi, empty = spans[chunk_no][0], spans[chunk_no][1], "centre"
    else:
        lo, hi, empty = 0, len(body), "start"
    w = weights if weights is not None else query_weights(db, query)
    s, e, rule = _passage(body, w, lo, hi, width, empty)
    return {"chunk": chunk_no, "start": s, "end": e, "rule": rule, "text": body[s:e]}


def judge_observation() -> dict:
    """An empty record of one judge stage, every field present from the start.

    ⛔ ★Every key exists before anyone writes to it★ — the hook's watchdog may serialise this record from
       another thread while the main thread is still filling it in, and a dict that grows while it is
       being read raises. Values change; the set of keys never does.

    outcome  pending · no_vectors · uncalibrated · vector_failed · no_candidates · failed · below · fired
             · error · cut (still pending when the watchdog ended the run)
    phase    where it is — vector (embedding and the store) · inputs · judge · done. A cut run says
             where it was cut (2026-10-14: "cut" alone could not tell the judge from the embedding)
    vector   how the vector search went (§vectors.last_search) — embedded or cached, which store answered
    sent · returned   whether the judge request went out and came back (§rerank.call_state)
    valid    whether a fresh answer scored every candidate once, within 0–10 (None: cached or none)
    kind · fail   a fixed failure code (§rerank.last_failure_kind) — never the provider's words
    decision below · fired — the judge's verdict against the threshold; what reached the person is the
             hook's own record
    gate     whether a fresh request was let through and why (§judgegate) — `skipped` when it was not
    waited · retry_after   how long the request was out, and how long the provider asked to wait
    """
    return {"outcome": "pending", "n": None, "top": None, "second": None, "ties": None,
            "floor": None, "model": None, "scale": None, "policy": None, "cached": None,
            "secs": None, "fail": None, "phase": None, "vector": None, "sent": None,
            "returned": None, "valid": None, "kind": None, "decision": None, "gate": None,
            "waited": None, "retry_after": None}


def judge_inputs(db: sqlite3.Connection, query: str, hits, excerpt: str = "head",
                 vector_ids=None) -> Tuple[list, List[dict], List[Optional[dict]]]:
    """★What the judge is given for these hits★ — one definition for `judged()` and for every measurement
    that asks the judge about other candidates (the union of words and meaning, for one).

    `hits` are (doc_id, cosine, ratio) in the order the judge reads them. Documents always in context
    (§_always_in_context) are left out. With `excerpt="evidence"` each candidate reads a passage
    (§evidence): from the chunk that matched if the document is among `vector_ids` (all hits when None)
    and its stored chunks are this version of it; from the whole body if only words found it; otherwise
    its head. Returns (rows, candidates, passages) — passages[i] is None where the head is read.
    """
    from brain import vectors
    rows, cands = [], []
    in_hand = _always_in_context()
    for doc_id, cos, ratio in hits:
        row = _hydrate(db, doc_id)
        if row is None or row["name"] in in_hand:
            continue
        rows.append((row, cos, ratio))
        cands.append({"name": row["name"], "description": row["description"],
                      "body": (row["body"] or "")[:500]})
    found: List[Optional[dict]] = [None] * len(rows)
    if excerpt == "evidence" and rows:
        by_vector = ({row["id"] for row, _, _ in rows} if vector_ids is None
                     else {int(d) for d in vector_ids})
        # ⛔ A document whose stored chunks are another version gives no passage — it keeps its head.
        chunk_of = vectors.best_chunks(db, query, [row["id"] for row, _, _ in rows
                                                   if row["id"] in by_vector])
        weights = query_weights(db, query)
        for i, (row, _, _) in enumerate(rows):
            if row["id"] in chunk_of:
                ev = evidence(db, query, row, chunk_of[row["id"]][0], weights)
            elif row["id"] not in by_vector:
                ev = evidence(db, query, row, None, weights)
            else:
                ev = None
            if ev is not None:
                found[i] = ev
                cands[i]["excerpt"] = ev["text"]
    return rows, cands, found


def judged(db: sqlite3.Connection, query: str, k: int = 20,
           strict: bool = False, cache: bool = True,
           observe: Optional[dict] = None, excerpt: str = "head",
           deadline: Optional[float] = None) -> List[dict]:
    """★Two-stage search★ — cosine picks k candidates, the reranker judges whether they are the answer.

    Why this shape (measured 2026-08-25): neither absolute cosine nor a ratio is a ruler for "is this the
    answer". Controls (real utterances needing no memory) often score a higher ratio than the correct
    answers, so no threshold separated them (above threshold: 3/34 correct · 0/8 silences rescued).
    Putting up a judge that ★reads query and candidate together★ separated them (top-1 correct 15/34 · 3/8 rescued).
    · Anthropic contextual retrieval: contextual embeddings −35% → +BM25 −49% → ★+reranking −67%★

    `observe` (§judge_observation) is filled in with ★what the judge said before the threshold★ — the
    candidates sent, the top two scores, ties at the top, the threshold, the model that answered and why
    it ended. ⛔ It never changes what is returned and never asks the judge again: it is the record a
    threshold measured from a person's own prompts needs, and until 2026-10-13 everything below the
    threshold was thrown away unseen. No query or document text goes into it.

    `excerpt` is what the judge reads of each candidate: "head" (the description and the start of the
    body — what the hook uses) or "evidence" (the passage of the chunk that matched, §evidence) — an
    experiment path until it is measured (docs/notes/retrieval-fusion-plan.md). A threshold measured
    under one is not a threshold under the other (§judge_policy).
    """
    from brain import rerank, vectors
    if excerpt not in EXCERPTS:
        raise ValueError("excerpt must be one of %s" % (EXCERPTS,))
    obs = observe if observe is not None else {}
    obs["policy"] = judge_policy(k, excerpt)
    obs["phase"] = "vector"
    try:
        hits = vectors.search(db, query, k=k)
    except Exception as exc:                             # noqa: BLE001
        obs["vector"] = vectors.last_search()
        obs["outcome"] = "vector_failed"
        obs["fail"] = type(exc).__name__
        if strict:
            raise JudgeUnavailable("vector search failed: %s" % exc) from exc
        return []
    obs["vector"] = vectors.last_search()
    obs["phase"] = "inputs"
    rows, cands, found = judge_inputs(db, query, hits, excerpt)
    if not cands:
        obs["outcome"] = "no_candidates"
        return []
    # ★Pass the connection that is already open★ — the budget counter writes, so opening a new one here
    # would make the hook create a second connection every prompt and possibly wait on a WAL lock (time budget matters here).
    # ★If chunking is on, ask in parts★ (default 0 = off · §rerank.CHUNK)
    #   It reduces answers splitting on identical input: 20 at once 62% → 5 at a time 100% (2026-08-31).
    # ⛔ `cache=False` exists for ★measurement★ — a measurement reading its own earlier answer
    #    "reproduces" anything (that is what happened to the precision axis on 2026-08-31). Production keeps the cache on.
    # ★Only if it can answer in the time left★ (§judgegate) — the hook passes its wall as `deadline`; an
    #   answer already in the cache costs nothing and is never held back. Measurements pass no deadline.
    probe = False
    if deadline is not None and not (cache and rerank.cached(query, cands, db) is not None):
        from brain import judgegate
        remaining = round(deadline - time.time(), 3)
        ask, why, probe = judgegate.decide(remaining)
        obs["gate"] = {"ask": ask, "why": why, "probe": probe, "remaining": remaining}
        if not ask:
            obs["outcome"], obs["phase"] = "skipped", "gated"
            return []
    obs["phase"] = "judge"
    t = time.time()
    try:
        scores = rerank.score_chunked(query, cands, db=db, cache=cache)
    finally:
        if probe:
            from brain import judgegate
            judgegate.release_probe()
    obs["secs"] = round(time.time() - t, 3)
    obs["phase"] = "done"
    obs["cached"] = rerank.last_was_cached()
    call = rerank.call_state()
    obs["sent"], obs["returned"] = call["sent"], call["returned"]
    obs["waited"], obs["retry_after"] = call["waited"], call["retry_after"]
    obs["kind"] = rerank.last_failure_kind() or None
    shape = rerank.last_answer_shape()
    obs["valid"] = (None if shape is None else
                    shape["answered"] == shape["n"] and not shape["duplicates"] and not shape["out_of_range"])
    obs["model"] = rerank.answering(db) or None
    obs["scale"] = rerank.scale_id(db)
    if scores is None:                                   # ★call failure★ — not the absence of an answer
        obs["outcome"] = "failed"
        obs["fail"] = obs["kind"] or "unknown"
        if strict:
            raise JudgeUnavailable("judge call failed (quota or network) — this is not silence")
        return []                                        # ⛔ production stays quiet
    if not scores:                                       # [] = no candidates
        obs["outcome"] = "no_candidates"
        return []
    floor = rerank.min_score(db)
    ranked = sorted(scores, reverse=True)
    obs.update(n=len(scores), top=ranked[0], second=ranked[1] if len(ranked) > 1 else None,
               ties=ranked.count(ranked[0]), floor=floor)
    best = max(range(len(scores)), key=lambda i: scores[i])
    if scores[best] < floor:
        obs["outcome"] = obs["decision"] = "below"
        return []
    obs["outcome"] = obs["decision"] = "fired"
    row, cos, ratio = rows[best]
    r = _present(row, 0.0, [], None, [], db, 0.0)
    r["via_vector"] = True
    r["cosine"] = round(cos, 4)
    r["ratio"] = round(ratio, 4)
    r["rerank"] = scores[best]
    r["why"] = "found by meaning, chosen by the judge (relevance %.0f/10)" % scores[best]
    if found[best] is not None:                          # show the passage the judge read
        ev = found[best]
        r["evidence"] = {k: ev[k] for k in ("chunk", "start", "end", "rule")}
        r["excerpt"] = _WS.sub(" ", ev["text"]).strip()
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
