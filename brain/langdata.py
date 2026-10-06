"""Corpus-language knowledge — ★the one place where a natural language is hard-coded★.

## Why this file exists

Everything that ships in this repository is English (`tests/verify_english_only.py`
counts what is left). But a handful of tables are ★not prose★ — they are ★data
matched against the user's own documents★. Translating those breaks the code
silently: the corpus still says the Korean word, and the matcher stops matching.

So the rule is:

    prose (docstrings, comments, screen output) → English, always
    patterns matched against the corpus         → ★the corpus language★, here

Keeping them here rather than scattered across `lexicon`, `indexaudit` and
`textindex` buys three things: the engine files read as English, ⛔ the same
knowledge is not written in two places (a shape this repository has been burnt
by repeatedly), and a second corpus language becomes ★adding a table★ rather
than editing three modules.

## ⛔ This is a declared exception, not a hidden one

`verify_english_only.py` allows Korean in this file — and ★prints how much★ it
allowed, next to the count that must reach zero. An exception that shrinks the
number without saying so would teach us to stop trusting the number.

## ⚠️ Known limit — inflection is Korean-only today

`KO_ENDINGS` exists for Korean alone. A user whose documents are in another language
gets no inflection de-prioritisation in `lexicon` (the anchor tiering degrades to
"identifier vs. everything else"; nothing breaks).

The condition markers `indexaudit` reads are a table ★per language★, chosen by the
languages the corpus is written in (§calibrate.corpus_languages). Section ★headings★ are
not here at all: they are the user's own words, so their roles are declared in the index
file or in config (§indexaudit.section_role) — a table of one person's headings is not
language data.
"""
from __future__ import annotations

# ── Korean ──────────────────────────────────────────────────────────────────
# Hangul syllable block. Kept as escapes because ★this range is code, not data★ —
# every module that asks "is this word Korean" should ask it the same way.
HANGUL_FIRST = "가"
HANGUL_LAST = "힣"


def is_hangul(text: str) -> bool:
    """Does the string hold at least one Hangul syllable."""
    return any(HANGUL_FIRST <= c <= HANGUL_LAST for c in text or "")


# Inflectional endings — a word ending in one of these is a ★conjugated form★,
# not a topic word. Used to push such words down the anchor tiers (§lexicon).
# ⛔ Nothing is discarded by this list; it only reorders. A filter that dropped
#    Korean words from the search score was measured harmful in this repository
#    (a 23-adverb filter took ceiling recall 16 → 12).
KO_ENDINGS = ("고", "서", "면", "나", "가", "는", "다", "지", "게", "니", "자",
              "까", "네", "야", "요", "죠", "며", "듯", "던", "임", "함")

# Condition markers — "when" a rule applies. A rule with one of these has a
# trigger, so a hook can carry it instead of the file (§indexaudit).
# Unconditional markers — with these and no condition marker, the rule has no
# trigger at all, so ★no hook can catch it★ and it stays in the file.
# ⛔ One table per language, picked by the corpus's languages — never all of them at once:
#    on a Korean corpus the English `if` would start matching identifiers in pasted commands.
# Languages written with spaces match ★whole words★ (`if` must not match `different`);
# Korean and Japanese match as written (`SPACED` below).
CONDITION_MARKERS = {
    "ko": ["할 때", "만질 때", "만지면", "쓸 때", "볼 때", "물을 때", "생각할 때", "늘릴 때",
           "고치기 전", "올리기 전", "푸시 전", "착수 전", "넓히기 전", "지우기 전", "묶기 전",
           "직전", "때 →", "면 →", "이면", "라면", "왔을 때", "건드렸으면", "만들 때",
           "빨갈 때", "판정할 때", "추가할 때", "작업이면", "경우", "하려 할 때", "될 때"],
    "en": ["when", "whenever", "before", "after", "if", "once", "unless", "while", "in case"],
    "de": ["wenn", "bevor", "nachdem", "falls", "sobald", "sofern", "beim", "vor dem"],
    "fr": ["quand", "lorsque", "avant de", "avant d'", "après", "si", "dès que", "au cas où"],
    "es": ["cuando", "antes de", "después de", "si", "en caso de", "al"],
    "ja": ["とき", "時は", "前に", "後に", "場合", "たら", "なら", "際は", "ときは"],
}
UNCONDITIONAL_MARKERS = {
    "ko": ["항상", "전부 금지", "언제나", "상시", "모든 ", "절대"],
    "en": ["always", "never", "at all times", "every time", "without exception"],
    "de": ["immer", "niemals", "nie", "stets", "grundsätzlich", "ausnahmslos"],
    "fr": ["toujours", "jamais", "en tout temps", "sans exception"],
    "es": ["siempre", "nunca", "jamás", "sin excepción"],
    "ja": ["常に", "必ず", "絶対", "いつも", "決して", "例外なく"],
}
SPACED = ("en", "de", "fr", "es")


# ── Every supported language: which one is the corpus written in ───────────────
# Six of the most frequent function words per language, as the tokenizer indexes them
# (Korean and Japanese as the 2-grams `textindex` actually stores). Read by
# `calibrate.corpus_languages`: a language counts as ★present★ when the median of its
# markers' document share clears a bar — the noise control is then drawn only from the
# languages the corpus is really written in.
# ⛔ Why not the screen language: it is a setting, and a process without one (a scheduled job,
#    another host) resolved a different language on the same corpus — the floor flapped 9.68 ↔ 8.08.
# ⛔ Why not every language: a language the corpus lacks has its function words at df≈1, so they
#    look like ★rare signal★ — one Spanish control scored 10.90 on a Korean corpus through `un` ·
#    `para` · `la` alone (2026-10-06) and would have silenced the hook (threshold 14.71).
# Prefer words no other listed language uses; the median of six absorbs one collision.
LANG_MARKERS = {
    "en": ("the", "and", "is", "of", "with", "this"),
    "de": ("und", "der", "die", "nicht", "ist", "mit"),
    "es": ("que", "los", "del", "por", "una", "para"),
    "fr": ("les", "des", "est", "une", "pour", "dans"),
    "ja": ("する", "して", "ます", "です", "ない", "いる"),
    "ko": ("하는", "에서", "있다", "한다", "으로", "것은"),
}
