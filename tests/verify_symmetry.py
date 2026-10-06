#!/usr/bin/env python3
"""★does the index and the query land in the same space★ — an invariant that, when broken, kills recall **silently**.

## Why this net exists (2026-08-13)

An earlier memory system published `multilingual-e5-small` at 24/35, and when we asked, we found the cause —
**it wasn't attaching the `query:`/`passage:` prefix.** Put documents and queries into the vector space
under different rules, and what got measured stops being the model and becomes ★**the measuring method**★.
No error. The score just quietly gets worse.

Our equivalent is n-grams. Split a document into 2·3-grams on the way in and throw the query in as whole
words, and the two representations never meet. Right now both sides pass through **the same two functions**
(`words()` → `grams_of_word()`), so it's symmetric — but that symmetry was **a convention nobody ever wrote down as a rule to keep**.
So this net gets woven.

## ⛔ There is exactly one intentional asymmetry

`_EN_STOP` (English function words) is filtered **only on the query side**. Drop it from the index and `doclen`
changes, shaking every score and cascading into threshold recalibration. So it only filters "what not to ask about."
This net **explicitly exempts** that one case, and treats every other difference as a failure.

How to run:  PYTHONPATH=. python3 tests/verify_symmetry.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import textindex  # noqa: E402

# shapes that actually occur in our corpus — Korean 2-char words · particles · identifiers · mixed
PROBES = [
    "테라폼 apply 금지",
    "협찬",                                   # ★2 characters★ — the exact spot FTS5 used to miss
    "세션이 만료됐다",                          # a form with a particle attached
    "paidContentOverlay 를 확인해",
    "ads#888 세션 만료",
    "terraform",
    "v1.2 릴리즈 노트",
    "recall 이 죽었다 grep 으로 훑었다",
    "한",                                     # 1 Hangul character — can't make a gram
    "what should i do with this",             # entirely function words
    # ★the other shapes★ (2026-09-08 · §4) — each must land in the same space as its own query
    "Die Größe wurde für Änderungen geprüft",  # de — letters with diacritics
    "el niño comía en la habitación",          # es
    "l'élève est à l'école après le déjeuner", # fr — apostrophes are separators
    "セッションの上限を超えたので再起動した",       # ja — no spaces, cut by script
    "２０２６年の東京 です こと",                # ja — full-width digits · function words (the exemption)
    "topic×engine 100×5",                     # × is a separator, not a letter
]

FAILS: list[str] = []


def index_terms(text: str) -> set:
    return set(textindex.term_frequencies([(text, 1.0)]).keys())


def query_gram_set(text: str) -> set:
    return set(g for _, grams in textindex.query_terms(text) for g in grams)


def stopword_grams(text: str) -> set:
    """What only drops on the query side — the one permitted difference."""
    out = set()
    for w in textindex.words(text):
        if textindex.is_stop(w):
            out |= set(textindex.grams_of_word(w))
    return out


print("=" * 74)
print("★1. index term == query term (0 difference besides the function-word exemption)")
print("=" * 74)
for p in PROBES:
    idx, qry, stop = index_terms(p), query_gram_set(p), stopword_grams(p)
    missing_in_query = idx - qry - stop     # in the index but can't be asked — never findable again
    extra_in_query = qry - idx              # only in the query — asks for something not in the index
    ok = not missing_in_query and not extra_in_query
    if not ok:
        FAILS.append("asymmetric %r\n     unaskable term %s\n     spinning term %s"
                     % (p, sorted(missing_in_query)[:6], sorted(extra_in_query)[:6]))
    print("  %s %-34s index %3d · query %3d · stopword-exempt %2d"
          % ("✅" if ok else "❌", p[:32], len(idx), len(qry), len(stop)))

print("\n" + "=" * 74)
print("★2. does an expansion word split exactly like the query")
print("=" * 74)
# ⛔ Measured (2026-08-10): hand in `"deep link"` whole and it becomes a term with a space in it,
#   which can never exist in the index, and it ★silently returns 0★. One entry point stops this from happening.
for phrase in ["deep link", "AI presence", "협찬 라벨"]:
    via_extra = set(g for _, gs in textindex.query_terms("", [phrase]) for g in gs)
    via_query = set(g for _, gs in textindex.query_terms(phrase) for g in gs)
    ok = via_extra == via_query and not any(" " in t for t in via_extra)
    if not ok:
        FAILS.append("an expansion word splits differently than the query: %r → %s" % (phrase, sorted(via_extra)[:6]))
    print("  %s %-18s expansion-path %2d term · query-path %2d term · term(s) with a space %d"
          % ("✅" if ok else "❌", phrase, len(via_extra), len(via_query),
             sum(1 for t in via_extra if " " in t)))

print("\n" + "=" * 74)
print("★3. is 2-character Korean still alive (the reason this project built its own index)")
print("=" * 74)
for w in ["협찬", "세션", "상한", "중단", "배포"]:
    grams = textindex.grams_of_word(w)
    ok = w in grams
    if not ok:
        FAILS.append("the 2-char word %r can't make its own term: %s" % (w, grams))
    print("  %s %-6s → %s" % ("✅" if ok else "❌", w, grams))

print("\n" + "=" * 74)
print("★4. the three shapes (2026-09-08) — an accent is a letter · × is a separator · Japanese is cut by script")
print("=" * 74)
SHAPES = [
    ("prüft niño école", ["prüft", "niño", "école"]),          # the old class cut these into pr·ft / ni / cole
    ("topic×engine brand÷2", ["topic", "engine", "brand"]),   # × ÷ live in the Latin-1 letter block but are not letters
    ("e\u0301cole", ["école"]),                               # NFD (macOS file names) folds to the same word
    ("２０２６ ＡＢＣ", ["2026", "abc"]),                         # full-width → ASCII
    ("セッションの上限", ["セッション", "上限"]),                    # katakana · (の dropped) · kanji
    ("です・ます調", ["です", "ます", "調"]),                       # ・ is a separator
    ("の", []),                                                 # a lone hiragana particle is not a word
]
for text, want in SHAPES:
    got = textindex.words(text)
    ok = got == want
    if not ok:
        FAILS.append("shape %r → %s (wanted %s)" % (text, got, want))
    print("  %s %-22s → %s" % ("✅" if ok else "❌", text, got))
for w, want in [("セッション", "セッシ"), ("上限", "上限"), ("犬", "犬")]:
    grams = textindex.grams_of_word(w)
    ok = want in grams and textindex.script_of(w) in ("kata", "han")
    if not ok:
        FAILS.append("Japanese run %r makes no usable gram: %s" % (w, grams))
    print("  %s %-6s → %s" % ("✅" if ok else "❌", w, grams))

print("\n" + "=" * 74)
if FAILS:
    print("❌ %d failure(s)" % len(FAILS))
    for f in FAILS:
        print("  · " + f)
    sys.exit(1)
print("✅ all passed — the index and the query live in the same space")
