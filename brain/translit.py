"""Transliteration bridge — connects an English word spelled phonetically in Korean to the original.

Why it is needed (measured 2026-08-19)
----------------------------
Of 34 short queries, 20 were silent, and the largest group (8) was a **spelling problem**:

    a query written phonetically for "plan" and "apply"  → the documents say `plan` · `apply`
    a query written phonetically for "linkedin"          → the documents say `linkedin`

No threshold, however tuned, makes a phonetic spelling reach the original — **the knowledge is absent**.
That is why three statistical prescriptions the same day (inflection IDF filter, top-IDF penalty,
separability) were all rejected: they measured the same signal differently, and none could invent a missing one.

⛔ How this differs from "a corpus is not a dictionary"
-------------------------------------------
An earlier attempt mined translation pairs from co-occurrence and failed (rescued 0/19, false 11/57).
That asked the corpus *"which words are a pair"*, and TF-IDF eliminates translation pairs first.

Here the order is reversed:

    ① phonetic rules **generate candidates**  (a Korean spelling → "plan", "plaen", …)
    ② the corpus **only verifies existence**  (does `plan` exist as a term? is its df usable?)

The corpus does not judge, it confirms. A wrong candidate is dropped silently if the corpus lacks it.

⚠️ This bridge is used **on the query side only** — inflating the index in both languages shifts the
whole IDF scale (doubling the document count is the most dangerous axis in this project).
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Dict, List, Optional, Sequence, Set

# Jamo → romanisation candidates. ★Keep them few★ — an explosion means some unrelated word that happens to be in the corpus matches.
_CHO = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "",
        "j", "jj", "ch", "k", "t", "p", "h"]
_CHO_ALT = {0: ["k"], 5: ["l"], 7: ["v", "p"], 9: ["sh"], 12: ["z"],
            15: ["c"], 17: ["f"], 11: [""]}
_JUNG = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
         "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_JUNG_ALT = {1: ["a", "e"], 4: ["e", "u", "o"], 8: ["o", "au"], 13: ["oo", "u"],
             18: ["", "u"],            # ★this vowel is the joint between consonant clusters — the omission candidate is the key★
             20: ["y", "ee", "i"], 11: ["o"], 19: ["i"]}
_JONG = ["", "k", "k", "k", "n", "n", "n", "t", "l", "l", "l", "l", "l", "l",
         "l", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]
_JONG_ALT = {1: ["c", "ck"], 17: ["b"], 21: ["n"], 8: ["ll", "r"]}

MAX_CANDIDATES = int(os.environ.get("BRAIN_TRANSLIT_MAX", "120") or 120)
BEAM = int(os.environ.get("BRAIN_TRANSLIT_BEAM", "64") or 64)   # combinations with fewest deviations carried per syllable
NEAR_PROBES = int(os.environ.get("BRAIN_TRANSLIT_PROBES", "24") or 24)  # candidates used for the nearest-word search

# ★Give the bridge its own budget★ (measured 2026-08-19) — the hook's budget is 200ms, and one prompt
# full of uncached words ate all of it: a 20-word Korean prompt took ★429ms★ (the original version was
# 655ms — this was not a regression I introduced but a hole that was always there). The hook does not
# turn that delay into silence (the budget check sits before recall), but the user's prompt is that much slower.
# → Past the budget, remaining words are ★simply skipped★. The bridge is a bonus, not a requirement.
# ⛔ A skipped word is never cached as "none" — that would permanently freeze "no time" into
#    "no bridge". The next prompt warms it from there (gradual pre-warming).
BUDGET_MS = float(os.environ.get("BRAIN_TRANSLIT_MS", "60") or 0)
CACHE_DOC_TOL = float(os.environ.get("BRAIN_TRANSLIT_CACHE_TOL", "0.02") or 0)
# Verification threshold — even if it exists in the corpus, too common (stop-word level) means no bridge
MIN_DF, MAX_DF_RATIO = 2, 0.35

# ★Prevent forcing a bridge onto a native Korean word★
#
# The first implementation produced three native words mapped to unrelated English. All three had
# ★a romanisation shorter than the original word★. A loanword transliteration grows instead
# (its filler vowels carry consonants): 2 syllables → commit (6) · 2 → session (7) · 2 → plan (4).
# Filter by the length ratio against syllable count — a shape condition, not a word list.
MIN_LEN_PER_SYL, MAX_LEN_PER_SYL = 2.0, 4.5
# ★But long words compress★ (measured 2026-08-19) — from 3 syllables the ratio drops to 1.5:
# 4 syllables → prompt (6) = 1.5 · 4 → persona (7) = 1.75 · 5 → instagram (9) = 1.8.
# Fixed at 2.0, all four are rejected. But lowering the ratio to 1.5 across the board lets 2-syllable
# native words leak in — ★the same ratio carries different risk★. Short words have 3-character
# candidates where coincidental matches are common, so the condition is split by syllable count.
# Measured: 2.0 everywhere → 0 false, 16 recalled · 1.5 everywhere → 2 false, 20 · ★split → 0 false, 20★
MIN_LEN_PER_SYL_LONG = 1.5
LONG_WORD_SYL = 3

# ★A word existing in the corpus is not on its own evidence for a bridge★ (measured 2026-08-19)
#
# The first version verified by "does the candidate exist as a term". English vocabulary is broad
# enough that a 2-syllable Korean word's romanisations almost always hit something. Measured against
# ★the top 400 words of 2,233 real prompts★, 38 of 53 bridges were false links to native Korean words.
#
#   Eight frequent native Korean words each mapped to an unrelated English word
#   (210, 154, 129, 128, 126, 123, 118 and 107 occurrences respectively).
#
# ⛔ The first version recorded "0 false links" because ★I measured with 10 hand-picked words★.
# That broke a rule this project had already written down — *"draw the sample from our own long tail"*
# and *"a small set makes a bad change look neutral"*. The set is now drawn from real volume
# (§tests/verify_translit.py).
#
# ★The discriminator is the pair's co-occurrence★ — a transliterated word and its original ★meet in
# the same document★ (half our documents are bilingual). A native word and a coincidentally matched
# P(original | Korean word) ÷ P(original) — divided by the base rate, so common words gain nothing.
#
#   label    pair              lift         label     pair            lift
#   correct  →terraform        13.0         false     →hash           0.8
#   correct  →canary           11.2         false     →mode           1.0  ← matched via the intersection
#   correct  →domain            2.2         false     →hangs          0.0
#   correct  →session           1.3         false     →jordan         4.6  ← only 2 documents intersect
#
# Grid measurement (correct kept / false passed): lift≥1.0 & intersection≥1 → 19/8 · ★1.2 & 3 → 16/0★ ·
# 1.5 & 3 → 13/0 · 2.0 & 3 → 12/0. Lift alone cannot block it (one false link had lift 4.6) — the minimum
# intersection count blocks ★a high lift from a small sample★. The two work as one.
#
# ⛔ This does not contradict "a corpus is not a dictionary" from 2026-08-13 (mining translation pairs
# from co-occurrence rescued 0/19) — there the corpus was asked to ★find★ candidates, and here it is
# asked to ★disprove★ candidates the rules generated. A signal too weak to find with is strong enough
# to reject with (some pairs have recall 0.09, but candidate generation never produced them anyway, so nothing is lost).
MIN_LIFT = float(os.environ.get("BRAIN_TRANSLIT_LIFT", "1.2") or 0)
MIN_COOC = int(os.environ.get("BRAIN_TRANSLIT_COOC", "1") or 0)
MIN_SHARE = float(os.environ.get("BRAIN_TRANSLIT_SHARE", "0.12") or 0)

# ★The discriminating axis is a share, not an absolute count★ (changed by a second measurement, 2026-08-19)
#
# It began as pass-if `intersection ≥ 3` OR `share ≥ 0.3`. That afternoon, cleaning up duplicate indexing
# took the corpus from 1,369 back to 1,173 and ★one false link came back to life★ — its intersection was exactly 3.
# Looking at what those 3 documents were: ⛔**three documents I had written that day** (the lesson, the
# project record and the index, all explaining this very defect). ★Documenting the defect created the defect's evidence.★
# The same trap is already written down here — quoting a calibration probe sentence in a document made
# calibration find itself and the threshold went 9.28 → 34.51 (a calibration probe quoted in a document finds itself).
#
# → Drop the absolute count and use ★the share★ (of the documents holding that Korean word, what fraction also hold the original) as an AND condition.
#   A share ★does not move for a few articles★: a word in 213 documents shifts by 0.014 when 3 are added.
#   Measured (corpus 1,173 · 201 words · without the veto: 33 correct, 43 false):
#     lift≥1.2 & share≥0.05 → 29/3 · 0.10 → 29/1 · ★0.12 → 28/0★ · 0.15 → 26/0 · 0.20 → 22/0
#   What 0.12 loses is four borderline pairs (0.10 · 0.10 · 0.05 · 0.02).
#   ⛔ Dropping to 0.10 gains one correct pair and takes one false link — the hook's axis is precision.


def _syllable_parts(ch: str):
    code = ord(ch) - 0xAC00
    if not 0 <= code < 11172:
        return None
    return code // 588, (code % 588) // 28, code % 28


def _variants(idx: int, table: Sequence[str], alt: Dict[int, List[str]]) -> List[str]:
    out = [table[idx]]
    for a in alt.get(idx, []):
        if a not in out:
            out.append(a)
    return out


def candidates(word: str) -> List[str]:
    """A Korean word → romanisation candidates. ★Rules only generate; the corpus judges.★

    ⛔ ★Cutting candidates in table order starves whole alternative pronunciations★ (measured 2026-08-19)
    The first version cut at 192 while joining syllables. Because the cut followed table order, the
    **second value** of each table entry never survived past three syllables. So a phonetic spelling of
    "linkedin" had one romanisation and ★not★ the one that matters —
    which is why no bridge was built even though `linkedin` (df 37) was right there in the corpus.

    → Change the cut order to ★length first, fewest deviations within the same length★.
    Length first is kept because of measurement — ordering by deviations dropped recall 20 → 17.
    An original is ★shorter★ than its transliteration (filler vowels carry consonants, so omission is
    often correct) and that omission is exactly a 'deviation': local, commit and slack all died that way.
    Using deviation order only within the same length lets both spellings survive side by side.
    Candidate count can grow safely because of §MIN_LIFT — verification moved from existence to pairs.
    """
    parts = [_syllable_parts(c) for c in word]
    if not parts or any(p is None for p in parts):
        return []
    per_syl: List[List[tuple]] = []
    for cho, jung, jong in parts:                       # type: ignore[misc]
        pieces: Dict[str, int] = {}
        for ci, c in enumerate(_variants(cho, _CHO, _CHO_ALT)):
            for vi, v in enumerate(_variants(jung, _JUNG, _JUNG_ALT)):
                for ji, j in enumerate(_variants(jong, _JONG, _JONG_ALT)):
                    piece = c + v + j
                    dev = (1 if ci else 0) + (1 if vi else 0) + (1 if ji else 0)
                    if piece and dev < pieces.get(piece, 99):
                        pieces[piece] = dev
        per_syl.append(sorted(pieces.items(), key=lambda x: (x[1], len(x[0]))))

    forms: List[tuple] = [("", 0)]
    for pieces in per_syl:
        nxt = [(base + piece, bdev + dev)
               for base, bdev in forms for piece, dev in pieces]
        nxt.sort(key=lambda x: (len(x[0]), x[1]))
        forms = nxt[:BEAM]                              # carry only the ones with fewest deviations

    # ★Add a candidate with a doubled consonant collapsed★
    # A syllable ending and the next syllable beginning with the same sound are pronounced as one in
    # Korean — cheap to express as a rule.
    # (The other direction — the doubled consonants in commit and terraform — explodes as a rule, so
    #  it is found by edit distance in the corpus: §_nearest_in_corpus)
    squeezed: List[tuple] = []
    for f, dev in forms:
        t = f
        for ch in "bcdfghjklmnprstvz":
            t = t.replace(ch * 2, ch)
        if t != f:
            squeezed.append((t, dev))
    forms = forms + squeezed

    # ★Fixed suffix correspondences★ (2026-08-19) — a few places the rule table cannot generate.
    # One Korean syllable is the standard rendering of English -tion/-sion, and its consonant splits
    # s↔t, so it fails the skeleton check. Word-final syllables also map to -ge and -xy commonly.
    # These are ★positional spelling correspondences★, not a word list, so they work on new words too.
    # correspondences at a word-final position★, so they work on new words too.
    tails = []
    for f, dev in forms:
        for src, dsts in (("syeon", ("tion", "sion")), ("syon", ("tion", "sion")),
                          ("ji", ("ge",)), ("csi", ("xy",)), ("ksi", ("xy",))):
            if f.endswith(src):
                for d in dsts:
                    tails.append((f[:-len(src)] + d, dev + 1))
    forms = forms + tails

    seen: Dict[str, int] = {}
    for f, dev in forms:
        if 2 <= len(f) <= 24 and dev < seen.get(f, 99):
            seen[f] = dev
    return [f for f, _ in sorted(seen.items(), key=lambda x: (len(x[0]), x[1]))
            ][:MAX_CANDIDATES]


def _code_stamp() -> int:
    """★Which generation of the code★ produced a cached bridge — used to separate cache
    generations (§_ensure_cache `code`).

    ⛔ It used to be `brain/*.py`'s newest ★mtime★. So a re-clone, a worktree or a reinstall — none
    of which change a single rule — threw away a warm cache that was still exactly correct, and
    (worse, same root cause) let file age stand in for code identity. It is now derived from the
    ★content★ of the modules that decide a bridge (§calibrate.code_cache_key, 2026-09-09).
    """
    try:
        from brain import calibrate
        return int(calibrate.code_cache_key())
    except Exception:                                    # noqa: BLE001
        return 0


def _ensure_cache(db: sqlite3.Connection) -> None:
    """Persist bridge results in the DB — ★the hook is a new process every prompt★, so there is no in-memory cache.
    Bridge results change with the corpus, so the document count at the time is stored and a mismatch is ignored.
    (Absences are stored too — generating and scanning candidates for a native word every time is the most expensive part.)

    ★Put the code age in the cache key★ (2026-08-20) — the old key was (word, document count).
    So ★fixing the bridge rules still used the old results★: in one session the rules were changed,
    the measurement would not move, and only clearing the cache by hand revealed the real value.

    ⛔ ★One row per word ★and★ generation★ (2026-10-07) — `code` used to be a column of a table keyed by
       the word alone, so a write from ★any★ generation replaced the row of every other. A long-running MCP
       server holds the code it started with (§lesson: a running MCP holds the old code): on 2026-10-07,
       21 of them (the oldest from 9/28) were alive, and each recall they served overwrote the current
       generation's row with theirs — the current code then found the word missing and recomputed it
       under the hook's time budget. Keyed by (word, generation), an old process writes only its own rows.
       The old `translit_cache` table is left to the old processes that still write it; this code never reads it.
    """
    db.execute("CREATE TABLE IF NOT EXISTS translit_cache_gen("
               "ko TEXT NOT NULL, code INTEGER NOT NULL, en TEXT, docs INTEGER,"
               " PRIMARY KEY(ko, code)) WITHOUT ROWID")


def _docs_of_term(db: sqlite3.Connection, term: str) -> set:
    try:
        return {r["doc_id"] for r in db.execute(
            "SELECT doc_id FROM postings WHERE term=?", (term,))}
    except sqlite3.Error:
        return set()


def _docs_of_word(db: sqlite3.Connection, word: str) -> set:
    """The documents containing that Korean word — ★the intersection of its grams★.

    A long word is not indexed whole (it is cut into overlapping grams).
    So one gram's document set is broader than the word's, and the intersection is closest to the word.
    """
    from brain import textindex
    sets = [d for d in (_docs_of_term(db, g)
                        for g in textindex.grams_of_word(word)) if d]
    if not sets:
        return set()
    out = set(sets[0])
    for d in sets[1:]:
        out &= d
    return out


def _pair_supported(db: sqlite3.Connection, ko: str, en: str, n_docs: int) -> bool:
    """★Does the pair actually meet in the corpus★ — the MIN_LIFT/MIN_COOC comments above are the grounds."""
    if MIN_LIFT <= 0 and MIN_COOC <= 0:
        return True                                  # control (for A/B)
    dk = _docs_of_word(db, ko)
    de = _docs_of_term(db, en)
    if not dk or not de:
        return False
    inter = len(dk & de)
    if inter < MIN_COOC:
        return False
    # ★The share★ — of the documents holding that Korean word, what fraction also hold the original. §MIN_SHARE above.
    if inter / len(dk) < MIN_SHARE:
        return False
    base = len(de) / max(1, n_docs)
    lift = (inter / len(dk)) / base if base else 0.0
    return lift >= MIN_LIFT


# ⛔ ★What this verification structurally cannot see★ (measured 2026-08-19) — the evidence for a pair is
# ★the two spellings meeting inside one document★. If our documents do not mix languages per document, a true pair also has intersection 0:
#
#   →notion     9 Korean docs · 37 English docs · ★intersection 0★   (the two never share a document)
#   →solution   7 Korean docs ·  6 English docs · ★intersection 0★
#   →lambda     ★0 Korean docs★ (those docs are written in English only) — the evidence cannot exist
#
# Measured cost: of 45 loanwords, 27 get a bridge, and this limit blocks 6 of them. It is left as is
# because ★the hook's axis is precision★ (false links 35 → 1). Opening it was tried too — applying only
# the shape condition to words absent from the corpus (dfk 0) leaked ★2 of 23★ in that group among the
# top 500 real prompts. ⛔ "No value ≠ unlimited."
#   → The prescription here comes from ★outside the corpus★ (a dictionary, or embeddings).
#     docs/domains/local-brain/embedding-decision.md covers that decision.


def bridge(db: sqlite3.Connection, words: Sequence[str],
           n_docs: Optional[int] = None) -> Dict[str, str]:
    """Korean words → the romanisation that ★actually exists★ in the corpus. Absent, the word is dropped."""
    out: Dict[str, str] = {}
    if n_docs is None:
        try:
            n_docs = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        except sqlite3.Error:
            return out
    cap = max(MIN_DF + 1, int((n_docs or 1) * MAX_DF_RATIO))
    try:
        _ensure_cache(db)
        # ★Do not throw away the whole cache because the corpus changed slightly★ (2026-08-19)
        # It began as an exact `docs=?` match. But saving one memory changes the document count and
        # ★wipes the cache at that instant★, and with a budget (§BUDGET_MS) the remaining words get no
        # bridge for that prompt — measured, short-query recall fell 62% → 59%. And the moment right
        # after saving a memory is exactly when recall matters most. Bridge results are only weakly
        # sensitive to document count (they change only when df or lift crosses a threshold), so ★2%
        # tolerance★ is allowed — the same axis as the threshold calibration's "measured on another corpus" guard (§calibrate).
        tol = max(1, int((n_docs or 1) * CACHE_DOC_TOL))
        cached = {r["ko"]: r["en"] for r in db.execute(
            "SELECT ko, en FROM translit_cache_gen "
            "WHERE code=? AND docs BETWEEN ? AND ?",
            (_code_stamp(), n_docs - tol, n_docs + tol))}
    except sqlite3.Error:
        cached = {}
    fresh: List[tuple] = []
    started = time.time()
    for w in words:
        if BUDGET_MS > 0 and (time.time() - started) * 1000 > BUDGET_MS:
            break                                       # over budget — the rest wait for another chance
        if not any("\uac00" <= c <= "\ud7a3" for c in w):
            continue                                    # not Korean, so no bridge is needed
        if len(w) < 2:
            # ⛔ A single syllable gives 2–3 character candidates, so ★coincidental matches are far too common★.
            #    Single-syllable loanwords are rare, and the risk of false links outweighs that loss.
            continue
        if w in cached:
            if cached[w]:
                out[w] = cached[w]
            continue
        per_syl = (MIN_LEN_PER_SYL_LONG if len(w) >= LONG_WORD_SYL
                   else MIN_LEN_PER_SYL)
        lo = int(len(w) * per_syl)
        hi = int(len(w) * MAX_LEN_PER_SYL) + 1
        best: Optional[str] = None
        best_df = -1
        cands = candidates(w)
        for cand in cands:
            row = db.execute("SELECT df FROM terms WHERE term=?", (cand,)).fetchone()
            if not row:
                continue
            df = row["df"]
            if df < MIN_DF or df > cap or not lo <= len(cand) <= hi:
                continue
            # ★Pick the most common candidate★ — a word that really denotes the concept appears in
            # more documents than a short fragment that matched by chance (df 2–3).
            if df > best_df:
                best, best_df = cand, df
        # ★Prepare ★every★ candidate path in order — if one fails pair verification, try the next★
        #
        # ⛔ A bug measured on 2026-08-20: it used to be an `if best is None:` chain. If the nearest-word
        #    search picked ★a wrong word★ first, `best` was filled, and when pair verification then
        #    discarded it ★the result became "no bridge"★ — the skeleton path was never even tried. So
        #    several real pairs were quietly dead (their skeletons were in the table and the length
        #    window matched). What fails verification is ★discarded, then next★.
        tries: List[tuple] = []
        if best is not None:
            tries.append((best, best_df))
        # ⛔ ★The skeleton path only from 3 syllables★ (measured 2026-08-20) — all 13 leaks were
        #    2-syllable native words. Conversely every pair this path rescued is 3 syllables or more.
        #    2-syllable loanwords are already reached by direct matching.
        #    ★Syllable count is the axis that cuts leaks without losing recall.★
    #    (they are reached directly). ★Syllable count cuts leaks without losing recall.★
        near_best, near_df = _nearest_in_corpus(db, cands, cap, lo, hi)
        if near_best and near_best not in [t[0] for t in tries]:
            tries.append((near_best, near_df))
        if len(w) >= MIN_SYL_FOR_SKEL:
            for term, tdf in _by_skeleton(db, cands, cap, lo, hi):
                if term not in [t[0] for t in tries]:
                    tries.append((term, tdf))
        if len(w) >= MIN_SYL_FOR_LOOSE and LOOSE_ON:
            for term, tdf in _by_skeleton(db, cands, cap, lo, hi, loose=True):
                if term not in [t[0] for t in tries]:
                    tries.append((term, tdf))
        best, best_df = None, -1
        for cand_best, cand_df in tries:
            if _pair_supported(db, w, cand_best, n_docs or 1):
                best, best_df = cand_best, cand_df
                break
        if best:
            out[w] = best
        fresh.append((w, best or "", n_docs))
    if fresh:
        try:
            gen = _code_stamp()
            with db:
                db.executemany(
                    "INSERT OR REPLACE INTO translit_cache_gen(ko,code,en,docs) "
                    "VALUES(?,?,?,?)", [(k, gen, v, d) for k, v, d in fresh])
                # older generations go — ★never a newer one★ (a newer process may be running beside this one)
                db.execute("DELETE FROM translit_cache_gen WHERE code < ?", (gen,))
        except sqlite3.Error:
            pass                                        # a cache failure must not block recall
    return out


_VOWELS = set("aeiouy")


# ★Consonant-skeleton key★ (2026-08-20) — different vowel spellings, same consonant frame, same word.
#
# Why it is needed: splitting the remaining 17 failures showed ★all of them were candidate-generation
# failures★ (not rejected by verification — the English word was never generated at all). And the
# failures had one shape: the consonants match and ★the vowel spelling differs★:
#
#     t·m·t   ↔ timeout  t·m·t
#     d·m·n   ↔ admin    d·m·n
#     l·n·k·d·n ↔ linkedin l·n·k·d·n
#     r·b·s   ↔ rebase   r·b·s
#
# Generating all of these by extending the syllable table explodes combinatorially, and loosening edit
# distance lets native words in first (the half-price-vowel experiment was rejected for that: recall 24→26, false links 2→4).
# ⇒ So ★do not loosen the distance; change the axis★ — require the consonant frames to ★match★ (not contain).
#
# Three reasons it is safe:
#   ① matching is stricter than containment — a consonant in the term but not the candidate fails.
#   ② the spelling equivalence classes are an already-verified list (§_skeleton_ok: c↔k · l↔r · f↔p · s↔z · b↔v).
#      Anything outside them does not join — the rejected experiment's false links are blocked here.
#      (h,g) ↮ haiku (h,k) · (b,l,s) ↮ polls (p,l,s) · (b,n,t,m) ↮ vintage (v,n,t,g)
#   ③ the final gatekeeper is still ★pair verification★ (§_pair_supported) — they must meet in one document.
# ⛔ z↔s is ★not included★ (measured 2026-08-20) — one Korean consonant's romanisation alternative is
#    "z" (§_CHO_ALT), so z↔s in the classes would make that consonant ≈ s and loosen it twice over:
#    one word was read as s·k·p and attached to `scope`. It stays in the edit-distance path
#    (§_skeleton_ok) — there the distance blocks it too. Skeleton ★matching★ has no such protection, so its classes stay narrow.
_SKEL_CLASS = {"c": "k", "q": "k", "l": "r", "f": "p", "v": "b"}
MIN_SKEL_LEN = 3       # a 2-consonant skeleton coincides too often (sn · nk · tr)
MIN_SYL_FOR_SKEL = 3   # ★2-syllable native words were the whole of the leakage★ (measured 13/13)
SKEL_TRIES = 5         # how many skeleton siblings to pass to pair verification (most common first)
# ★Allow ±1 consonant — opened by syllable count only★ (A/B 2026-08-20)
#   from 3 syllables: recall 35→36 but ★false links 1→8★  → rejected (same shape as the half-price-vowel experiment)
#   from 4 syllables: recall 36 · false links 1
#   from 5 syllables: recall 36 · false links 1  ← ★same gain with the least exposure★, so this is used
# ⚠️ 4 and 5 may be equal only because long words are rare in the control set (156 native words) —
#    "no value ≠ unlimited". Open it only where the gain was measured (one 5-syllable word).
MIN_SYL_FOR_LOOSE = int(os.environ.get("BRAIN_TRANSLIT_LOOSE_SYL", "5") or 5)  # syllable count that opens the ±1 consonant allowance (experimental — tightened as leaks appear)
_SKEL_ALPHABET = "bdghjkmnprstw"   # representative characters of the skeleton equivalence classes
LOOSE_ON = os.environ.get("BRAIN_TRANSLIT_LOOSE", "1") != "0"


def skeleton_key(word: str) -> str:
    """Keep only consonants, normalise by spelling class, then ★collapse repeated adjacent sounds★.

    Why collapse repeats: English doubled consonants do not appear in Korean transliteration
    (m·b·d·ng ↔ `embedding` m·b·d·d·n·g). This is the same phenomenon `_nearest_in_corpus` caught
    by edit distance, handled here once when the key is built.
    """
    out = []
    for ch in word.lower():
        if ch in _VOWELS or not ("a" <= ch <= "z"):
            continue
        cls = _SKEL_CLASS.get(ch, ch)
        if not out or out[-1] != cls:
            out.append(cls)
    return "".join(out)


def _by_skeleton(db: sqlite3.Connection, cands: Sequence[str], cap: int,
                 lo: int, hi: int, loose: bool = False):
    """Corpus words whose ★consonant skeleton matches★ the candidate. (None, -1) if there is none.

    ⛔ Do not narrow by prefix — the failures here are exactly the cases where the first letter differs.
    Instead, look up the indexed skeleton table in O(1).
    """
    keys: Dict[str, None] = {}
    for cand in cands[:NEAR_PROBES]:
        if len(cand) < 4:
            continue                         # short words have 2-character skeletons and coincide often
        k = skeleton_key(cand)
        if len(k) >= MIN_SKEL_LEN:
            keys.setdefault(k, None)
    if not keys:
        return []
    found: Dict[str, int] = {}
    ks = list(keys)
    if loose:
        # ★Where one consonant is off★ — a Korean transliteration inserts a consonant English does not
        # have, or drops one it does (a trailing r, an h in the middle).
        # This widens the key, so it is still ★one lookup★.
        wide = set(ks)
        for k in ks:
            for i in range(len(k)):
                wide.add(k[:i] + k[i + 1:])                      # the candidate has an extra
            for i in range(len(k) + 1):
                for c in _SKEL_ALPHABET:
                    wide.add(k[:i] + c + k[i:])                  # the term has an extra
        ks = [k for k in wide if len(k) >= MIN_SKEL_LEN]
    try:
        qs = ",".join("?" * len(ks))
        rows = db.execute(
            "SELECT term, df FROM skeletons WHERE skel IN (%s) AND df>=? AND df<=? "
            "AND length(term) BETWEEN ? AND ?" % qs,
            (*ks, MIN_DF, cap, lo, hi)).fetchall()
    except sqlite3.Error:
        return []                            # no table (an old index) — skip quietly
    for r in rows:
        found[r["term"]] = max(found.get(r["term"], 0), r["df"])
    # ⛔ ★Do not reduce to one★ (measured 2026-08-20) — when several words share a skeleton it used to
    #    pick the most common and return that. For one word the skeleton siblings were `domain` (df 163)
    #    and `admin` (df 158); it picked the common one, ★and when pair verification rejected it, admin
    #    was never tried★. When verification comes later, an earlier stage must not narrow to one.
    return sorted(found.items(), key=lambda x: -x[1])[:SKEL_TRIES]


def _skeleton_ok(cand: str, term: str) -> bool:
    """Does the candidate's ★consonant skeleton★ appear in order within the target?

    ⛔ Edit distance alone attaches native Korean words to unrelated English words. Vowels wobble with
    every transliteration but ★consonants survive★ — (k,m,t) → commit (c,m,t) · (t,r,f,m) → terraform
    (t,r,r,f,r,m). Meanwhile one native word (t,m,s,k) fails against tanstack (t,n,s,t,c,k) because m
    is absent. A shape condition, not a list.
    """
    skel = [c for c in cand if c not in _VOWELS]
    if not skel:
        return False
    i = 0
    for c in term:
        if i < len(skel) and (c == skel[i] or (c, skel[i]) in
                              (("c", "k"), ("k", "c"), ("f", "p"), ("p", "f"),
                               ("l", "r"), ("r", "l"), ("s", "z"), ("z", "s"),
                               ("b", "v"), ("v", "b"), ("j", "z"))):
            i += 1
    return i >= len(skel) - (1 if len(skel) >= 4 else 0)   # a long word may miss one character


def _edit_within(a: str, b: str, limit: int) -> bool:
    """Is the edit distance within limit (stopping at the limit instead of computing it fully)?

    ⛔ ★A version counting vowels at half price was built and rejected★ (A/B 2026-08-19) — it seemed
    right to apply `_skeleton_ok`'s rationale ("vowels wobble, consonants survive") to distance too.
    Measured: recall 24→26 · ★false links 2→4★. And the pairs it was aimed at did not come back —
    those are not distance problems but different syllable correspondences. Loosening distance lets native words in first.
    they are places where the syllable correspondence itself differs. Loosening distance lets native words in first.
    """
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > limit:
            return False
        prev = cur
    return prev[-1] <= limit


def _nearest_in_corpus(db: sqlite3.Connection, cands: Sequence[str], cap: int,
                       lo: int = 0, hi: int = 99):
    """Find ★a near word★ when the candidate is absent from the corpus.

    ⛔ Why it is needed: English doubled consonants do not appear in Korean transliteration —
    a spelling of `commit` comes out as "comit", of `terraform` as "terafom". Generating candidates
    that ★insert★ doublings explodes as a rule, so a near word from the corpus is chosen instead.

    ⛔ The tens of thousands of terms are not scanned — narrowed by the first two characters and by length first.

    ★One lookup per prefix, not per candidate★ (2026-08-19) — going from 8 to 24 candidates raised
    recall 20→24 but cost 16.5→38.9ms per word (the hook budget is 200ms). Candidates share prefixes,
    so they are grouped by prefix, looked up once, and matched against every candidate in Python.
    and the returned words are matched against every candidate in Python.
    """
    groups: Dict[str, List[str]] = {}
    for cand in cands[:NEAR_PROBES]:
        if len(cand) < 4:
            continue                             # short prefixes coincide too often
        groups.setdefault(cand[:2], []).append(cand)
    best, best_df = None, -1
    for prefix, cs in groups.items():
        lens = [len(c) + (1 if len(c) <= 6 else 2) for c in cs]
        mins = [len(c) - (1 if len(c) <= 6 else 2) for c in cs]
        rows = db.execute(
            "SELECT term, df FROM terms WHERE term LIKE ? "
            "AND length(term) BETWEEN ? AND ? AND df >= ? AND df <= ?",
            (prefix + "%", min(mins), max(lens), MIN_DF, cap)).fetchall()
        for r in rows:
            term, tdf = r["term"], r["df"]
            if tdf <= best_df or not lo <= len(term) <= hi:
                continue                         # length condition (blocks native-word false links)
            for cand in cs:
                limit = 1 if len(cand) <= 6 else 2
                if (_skeleton_ok(cand, term)
                        and _edit_within(cand, term, limit)):
                    best, best_df = term, tdf
                    break
    return best, best_df
