"""Tokenization — the layer that makes Korean searchable.

★Why a custom index★ (decided by measurement 2026-08-10)
SQLite FTS5's trigram tokenizer ★entirely misses a 2-character Korean query★:

    MATCH "세션" (session) → 0 hits   MATCH "상한" (cap) → 0 hits   MATCH "배포" (deploy) → 0 hits   (even when the document has that word)

Most core terms in Korean notes are 2 characters (세션·상한·중단·라벨·배포·삭제 — session · cap · halt · label · deploy · delete).
So with FTS5, **the most important search fails silently** — not an error, an empty result.
An earlier memory system was burnt in the same spot (a 2-character place name went entirely to a LIKE fallback), worked around it with LIKE, but
LIKE carries no ranking. So this builds **its own inverted index** instead:

  · Korean → character 2-gram + 3-gram      (catches a 2-character word, and any inflected ending)
  · Latin  → word tokens (lowercased)        (an identifier like ads#888 · paidContentOverlay · prüft · niño)
  · Japanese → runs cut by script (kanji · hiragana · katakana), each 2-gram + 3-gram  (§scripts below)

Measured (434 memory documents · 1.27M characters): 42,803 unique terms · 182,023 postings · 3.8MB DB · 0.2s to index.
Small enough that a full scan needs no shortcuts, and ranking is 100% under our own control (IDF · weight · self-improvement).
"""
from __future__ import annotations

import math
import os
import re
import unicodedata
from typing import Dict, Iterable, List, Tuple

# ★The index remembers which tokenizer built it★ — `store.reindex` compares this with the stamp in `meta`
# and re-reads every file when they differ. Without it, documents indexed by the old tokenizer keep their
# old terms (`pr`·`ft`) while a query tokenized by the new one asks for `prüft` — and they never meet, silently.
# Bump it whenever `words()` / `grams_of_word()` change which terms a text produces.
#   1 = Hangul 2·3-grams + ASCII words (2026-08-10)
#   2 = + Latin letters with diacritics · Japanese by script · full-width fold · NFC (2026-09-08)
TOKENIZER_VERSION = 2

# ---------------------------------------------------------------- scripts
# ★Scripts, not languages★ (2026-09-08 · §4 of the public-release plan)
#
# The six supported languages (en · de · es · fr · ja · ko) fall into three ★shapes★, and the shape —
# not the language — decides how a run of characters becomes terms:
#
#   latin     en · de · es · fr   one word, lowercased. ⛔ A letter with a diacritic is a letter. The old
#                                 class was `[A-Za-z]`, so `prüft` became `pr`+`ft`, `niño` → `ni`, `école`
#                                 → `cole`: a word carrying an accent could ★never★ be found. (Measured
#                                 2026-09-08 — the plan had said de·es·fr "mostly work". They did not.)
#   hangul    ko                  2·3-grams (the module docstring — the 2-character word).
#   han/kana  ja                  no spaces, so a sentence is one run. Cut ★by script first★ — kanji ·
#                                 hiragana · katakana change exactly where words tend to change (漢字 stem
#                                 + ひらがな ending · カタカナ loanword) — then 2·3-grams inside each run,
#                                 the Korean way. Measured before this change: a Japanese sentence produced
#                                 ★zero★ words. The text was not "one big word"; it was invisible.
#
# So "language detection" is per run of characters, from character ranges, with ★no dependency★. A
# document-level language would be wrong here anyway — our documents mix Korean prose with English
# identifiers inside one sentence, and a Japanese note quotes an English error verbatim.
#
# ⛔ `×` (U+00D7) and `÷` (U+00F7) sit inside the Latin-1 letter block and are ★not★ letters —
#    `topic×engine` must stay two words. Measured: 499 of this corpus's 570 "accented" occurrences were `×`.
_LATIN = ("0-9A-Za-z_"
          "\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f"     # Latin-1 letters (minus × ÷) · Extended-A · Extended-B
          "\u1e00-\u1eff")                               # Latin Extended Additional
_HANGUL = "\uac00-\ud7a3"
_HAN = "\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3005"    # CJK unified · Extension A · compatibility · 々
_HIRA = "\u3041-\u3096\u309d-\u309e"                      # letters + ゝゞ (the block's marks ゛゜ are not letters)
_KATA = "\u30a1-\u30fa\u30fc-\u30fe\u31f0-\u31ff\uff66-\uff9f"  # letters + ー ヽ ヾ · phonetic extensions · half-width
# ⛔ `・` (U+30FB) lives inside the katakana block and is a ★separator★ (です・ます調) — it is excluded above.

# One pass, in text order. The groups are disjoint, so the alternation never has to choose.
# A Latin word keeps dots, hyphens and hashes inside it (ads#888 · paid_content · v1.2).
_TOKEN = re.compile(
    "(?P<hangul>[%s]+)|(?P<han>[%s]+)|(?P<hira>[%s]+)|(?P<kata>[%s]+)|(?P<latin>[%s][%s.#-]*)"
    % (_HANGUL, _HAN, _HIRA, _KATA, _LATIN, _LATIN))
_SCRIPT_RE = tuple((name, re.compile("[%s]" % cls)) for name, cls in
                   (("hangul", _HANGUL), ("han", _HAN), ("hira", _HIRA), ("kata", _KATA), ("latin", _LATIN)))

# The scripts that are cut into n-grams. Latin is not — an identifier is written exactly.
_GRAM_SCRIPTS = frozenset(("hangul", "han", "hira", "kata"))

# Full-width ASCII (ＡＢＣ１２３) → ASCII, ideographic space → space. Japanese writes digits and Latin
# in full-width forms routinely; without this `２０２６` and `2026` never meet. ⛔ Only this narrow range
# is folded — NFKC would also turn `™` into `TM` and glue it onto the identifier before it (`Acme™` → `acmetm`).
_FULLWIDTH = {c: c - 0xfee0 for c in range(0xff01, 0xff5f)}
_FULLWIDTH[0x3000] = 0x20


def _fold(text: str) -> str:
    """One normal form for both sides. NFC joins a decomposed accent (`e` + U+0301 — how macOS
    writes file names) back into `é`; the class above knows no combining marks and would split there."""
    return unicodedata.normalize("NFC", text).translate(_FULLWIDTH)


def script_of(word: str) -> str:
    """Which shape a word has — 'hangul' · 'han' · 'hira' · 'kata' · 'latin' · '' (nothing indexable).

    A run from `words()` is single-script by construction, so the first character decides.
    The one caller outside this module is the transliteration gate in `search.recall` (Korean only).
    """
    if not word:
        return ""
    c = word[0]
    for name, rx in _SCRIPT_RE:
        if rx.match(c):
            return name
    return ""


# Korean n-gram lengths (Japanese runs use the same). Drop the 2 and the exact accident this comment describes comes back.
_NGRAMS = (2, 3)

# Latin minimum length. A single character carries zero discriminating power.
_MIN_ASCII = 2

# A lone hiragana character is a particle (の · を · に · が · は · と · で). It appears in every Japanese
# document, so as a term it would carry IDF≈0 and as a ★word★ it would pad the coverage denominator.
# Kanji and katakana single characters are kept, like a single Hangul syllable (犬 · 車 carry meaning).
_MIN_HIRAGANA = 2

# The cap on n-grams from one word — so one very long run-together word cannot
# dominate the index.
_MAX_GRAMS_PER_WORD = 64

# ★An English function word disguises itself as a "rare word"★ (found by measurement 2026-08-12)
#
# IDF assumes **a rare word = a discriminating word**. In a Korean-heavy corpus that assumption
# breaks — an English function word is not rare, it is only that ★our documents hold little English★,
# and looking at df alone cannot tell the two apart. Measured over 918 documents:
#
#     should  df  26 → IDF 3.55   ← carries no meaning
#     terraform df 51 → IDF 2.88   ← stronger than a real identifier
#     turning df   3 → IDF 5.57   = ★the same value★ as ollama (df 2) (because of IDF_CAP_DF)
#
# So "what should i cook for dinner tonight with potatoes" scored 9.22 from `for·should·what·with`
# alone, ★becoming the noise floor★, and ×1.35 of that, 12.45, became the hook's threshold.
# Meaningless words inflated the threshold and silenced every short query that actually needed memory.
#
# ⛔ df cannot fix this — these words' df sits in the same band as real identifiers.
#    It is a closed set, so it is known ★by list★. This is the only lexical knowledge this file holds.
#
# ★Filtering happens on the query side★ (§query_terms) — the index keeps them as-is.
#   The disguise only happens "when a query asks for that word", and dropping it from the index
#   would change doclen and shake every score (a threshold-recalibration cascade). Block only the actual cause.
#
# ⛔ Content words were not added — `best`·`long`·`way`·`done`·`open`·`off`·`down` carry meaning in
#   our documents (long-running · Jira Done · a flag "off" · a server "down").
_EN_STOP = frozenset("""
a an the this that these those
and or but nor so yet if then else than as because while
when where why how what which who whom whose
i me my mine you your yours he him his she her hers it its
we us our ours they them their theirs
am is are was were be been being
do does did doing have has had having
can could will would shall should may might must
of to in on at by for with from into onto over under about
after before between during through until up out against
not no very too just now only also both each few more most
other some such own same all any here there
""".split())

# ★The same disguise in Japanese★ — a Japanese function word (です · ます · こと · ある) is "rare" in a
# Korean-heavy corpus for exactly the reason an English one is, and IDF would crown it. It is a closed
# set, so it is known by list. ⛔ Only a ★whole hiragana run★ is matched — an agglutinated run (`えたので`)
# passes, and IDF handles what it can. Query side only, like `_EN_STOP` (the index keeps them).
_JA_STOP = frozenset("""
です ます でした ません ない ある いる する なる
した して いた いて され される
これ それ あれ どれ この その あの どの ここ そこ あそこ どこ
こと もの ため よう から まで など ので のに けど でも
そして しかし また ただ もう まだ
""".split())


def is_stop(word: str) -> bool:
    """A word that is never asked about (query side only — the index keeps it, see `_EN_STOP`)."""
    key = word.lower()
    return key in _EN_STOP or key in _JA_STOP


def words(text: str) -> List[str]:
    """The unit of search intent. An n-gram is a byproduct cut from this.

    ★Coverage is judged at this unit★ — "how many of the query's words were reached" is closer to
    a human's own judgement than "how many grams were reached" (§search.score).
    """
    out: List[str] = []
    for m in _TOKEN.finditer(_fold(text)):
        t = m.group()
        if m.lastgroup == "latin":
            t = t.strip(".-#").lower()
            if len(t) < _MIN_ASCII:
                continue
        elif m.lastgroup == "hira" and len(t) < _MIN_HIRAGANA:
            continue
        out.append(t)
    return out


def grams_of_word(word: str) -> List[str]:
    """One word → the terms used for indexing and search.

    Latin stays the word as-is (no partial match needed — an identifier is written exactly).
    Korean is cut into 2- and 3-grams: `세션이` and `세션` meet at the `세션` gram. A Japanese run
    (already single-script from `words()`) is cut the same way: `セッション` and `セッションが` meet at `セッシ`.
    """
    if script_of(word) not in _GRAM_SCRIPTS:
        return [word]
    out: List[str] = []
    for n in _NGRAMS:
        for i in range(len(word) - n + 1):
            out.append(word[i:i + n])
            if len(out) >= _MAX_GRAMS_PER_WORD:
                return out
    if not out:            # a single character (한 · 犬 · ー) — no gram can be made
        out.append(word)
    return out


# ★The saturation constant for in-field repetition★ — 0 means the old way (the control group)
#
# Why this switch exists: the old way **added field weight straight into tf**. So a word quoted 11
# times in the body (tf=11) beat a word in the name (tf=6). That is, "a document that uses X as an
# example" beat "a document about X" — this is where the ranker's aboutness defect came from.
#
# The new way **saturates within a field first, then multiplies by weight**. Then one field's contribution
# can never exceed its own field weight: the body maxes at 1.0 no matter how much it repeats, a single mention in the name is 3.0.
# ★Identity is decided by name and description; body repetition becomes secondary.★
#
# ⛔ Change the value and every tf changes, so **a full reindex + threshold recalibration** ship together.
FIELD_K = float(os.environ.get("BRAIN_FIELD_K", "0") or 0)

# ★How to count in-field repetition★ — three ways measured A/B and chosen from (2026-08-18).
#
#   sum : the old way. Adds weight × c as-is.
#         → the body 11 times (11) beats the name once (6) = the cause of the aboutness defect.
#   sat : weight × c/(c+K). One field's contribution can never exceed its weight.
#         → identity survived, but ★the body's 1.0 ceiling was so harsh it cut the signal harder than the noise★
#           (measured: recall B top-3 6→9, but short-query recall 41%→18%).
#   log : weight × (1 + ln c). Body repetition grows ★slowly★.
#         → the name once at 6.0 beats the body 11 times at 3.4, so identity wins while the body's signal still shows through.
TF_MODE = os.environ.get("BRAIN_TF_MODE", "sum")


def term_frequencies(fields: Iterable[Tuple[str, float]]) -> Dict[str, float]:
    """(text, weight) pairs → term → weighted frequency.

    ★Weight is folded in at index time★ — a word in the name or description outweighs one buried
    deep in the body. Walking fields separately at search time turns one query into several round
    trips through the index, and adding a tier (field) would change the search code every time (scalability).

    With `FIELD_K > 0` in-field repetition is saturated first (see the comment above).
    """
    tf: Dict[str, float] = {}
    mode = TF_MODE if TF_MODE != "sum" or FIELD_K > 0 else "sum"
    if FIELD_K > 0 and TF_MODE == "sum":
        mode = "sat"                           # compatibility with the old switch
    if mode == "sum":                          # the control group — adds weight as-is
        for text, weight in fields:
            if not text:
                continue
            for w in words(text):
                for g in grams_of_word(w):
                    tf[g] = tf.get(g, 0.0) + weight
        return tf

    k = FIELD_K if FIELD_K > 0 else 1.0
    for text, weight in fields:
        if not text:
            continue
        counts: Dict[str, int] = {}
        for w in words(text):
            for g in grams_of_word(w):
                counts[g] = counts.get(g, 0) + 1
        for g, c in counts.items():
            if mode == "log":
                bump = 1.0 + math.log(c)       # c=1 → 1.0 · c=11 → 3.40
            else:                              # sat
                bump = c / (c + k)             # cannot exceed the weight
            tf[g] = tf.get(g, 0.0) + weight * bump
    return tf


def query_terms(query: str, extra_terms: Iterable[str] = ()) -> List[Tuple[str, List[str]]]:
    """Query → [(word, that word's terms)].

    `extra_terms` are ★search words the caller expanded with★ (a synonym, a Latin spelling, a related word).
    Measured: 1/6 with no expansion → 5/6 with expansion (2026-08-10, a 6-question baseline).
    This stays a parameter for exactly one reason — whoever does the expanding (an agent, a user, an
    embedding later) never has to touch the search core.
    """
    pairs: List[Tuple[str, List[str]]] = []
    seen = set()
    # ★An expansion word is split exactly like the query★ — hand in `"deep link"` whole and it becomes
    # a term with a space in it that can never exist in the index, and it ★silently returns 0★ (measured
    # 2026-08-10: an awin memory with tf=18 was pushed outside the top 20 anyway). One entry point means this never happens.
    candidates: List[str] = list(words(query))
    for t in extra_terms:
        candidates.extend(words(str(t)))
    for w in candidates:
        key = w.lower()
        if key in seen:
            continue
        # ★A meaningless function word is never asked about★ — the grounds are in the `_EN_STOP` / `_JA_STOP` comments.
        # Applies equally to an expansion word: even if whoever expands accidentally adds `not`·`to`,
        # it must never shake the ranking (one entry point means one rule).
        if is_stop(key):
            continue
        seen.add(key)
        pairs.append((w, grams_of_word(w)))
    return pairs
