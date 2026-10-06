#!/usr/bin/env python3
"""★Does a query in each of the six languages reach its own document★ — the tokenizer's per-language fixture. (local · budget 0)

## What this check is, and what it is not (2026-09-08 · §4 of the public-release plan)

It builds a ★synthetic★ corpus — two short documents per language (en · de · es · fr · ja · ko) — in a
temporary `BRAIN_HOME`, indexes it with the real `store.reindex`, and asks the real `search.recall`
whether each language's queries put ★their own★ document first.

  ✅ It proves the ★necessary★ condition: the tokenizer cuts a document and a query in that language
     into terms that meet (before this: `prüft` → `pr`+`ft` · a Japanese sentence → zero words).
  ⛔ It does ★not★ measure search quality in that language. Ranking · thresholds · the noise floor need
     ★real★ labelled prompts per language (§0 risk 2 — "a language that was not measured is not 'working'").
     Our only real sample is Korean. So the verdict this check can give is
         "the tokenizer reaches it"      — never "search works in Japanese".

## Why synthetic is allowed here, when the rule says never invent an evaluation query

`lesson_an_evaluation_set_i_invented_measured_my_imagination` forbids ★made-up queries★ for measuring
★quality★ (threshold · recall rate), because they measure the author. This check measures a ★mechanism★
(do the two sides share terms) where the fixture's job is to be a controlled specimen, not a sample of
real use. The rows marked △ below are known gaps recorded on purpose, not counted as passes.

How to run:  PYTHONPATH=. python3 tests/verify_tokenizer_langs.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ⛔ Before any brain import — the provider is read at import time. `stub` is a deterministic hash:
#    no key, no network, no cost. Recall itself is lexical; this only keeps the reindex's vector
#    housekeeping offline as well.
os.environ["BRAIN_EMBED_PROVIDER"] = "stub"
_TMP = tempfile.mkdtemp(prefix="brain-langs-")
_HOME_BEFORE = os.environ.get("BRAIN_HOME")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")

from brain import search, store, textindex  # noqa: E402

# (name, title, body) — two per language, deliberately ★different topics★ so that one query has one home.
DOCS = [
    ("en-a", "Session cap and restart",
     "The session cap was exceeded so the worker restarted. The cap lives in the settings file. "
     "Check the log after the restart."),
    ("en-b", "Weather in London",
     "London weather turns to rain in the afternoon. Take an umbrella when you go out. "
     "The weekend forecast is clear."),
    ("de-a", "Größenänderung prüfen",
     "Die Größe der Bilder wurde für Änderungen geprüft. Wer die Höhe ändert, muss die Prüfung wiederholen. "
     "Änderungen an der Größe brauchen einen Neustart."),
    ("de-b", "Straßenbahn Fahrplan",
     "Der Fahrplan der Straßenbahn ändert sich im Frühjahr. Die Haltestelle am Bahnhof wird verlegt. "
     "Fahrgäste sollten früher losfahren."),
    ("es-a", "La habitación del niño",
     "El niño duerme en la habitación pequeña. Mañana pintaremos la pared de la habitación. "
     "La cama nueva llegó ayer."),
    ("es-b", "Compra de café",
     "Compramos café en la tienda de la esquina. El precio subió después del verano. "
     "Pedimos también azúcar y pan."),
    ("fr-a", "L'élève et l'école",
     "L'élève est arrivé à l'école après le déjeuner. Le professeur a noté le retard. "
     "L'école ferme à dix-sept heures."),
    ("fr-b", "Réunion du mardi",
     "La réunion du mardi a été déplacée au jeudi. Le compte rendu sera envoyé après la réunion. "
     "Prévoir les questions à l'avance."),
    ("ja-a", "セッション上限と再起動",
     "セッションの上限を超えたので再起動した。上限は設定ファイルで変更できる。再起動の後にログを確認する。"),
    ("ja-b", "東京の天気",
     "東京の天気は午後から雨になる。傘を持って出かける。週末は晴れる予報だ。"),
    ("ko-a", "세션 상한과 재시작",
     "세션 상한을 넘어서 재시작했다. 상한은 설정 파일에서 바꿀 수 있다. 재시작 뒤 로그를 확인한다."),
    ("ko-b", "서울 날씨",
     "서울 날씨는 오후부터 비가 온다. 우산을 들고 나간다. 주말엔 맑을 전망이다."),
]

# (lang, query, expected document, strict?) — strict rows fail the check; △ rows are recorded gaps.
QUERIES = [
    ("en", "session cap", "en-a", True),
    ("en", "london weather", "en-b", True),
    ("de", "Änderungen der Größe prüfen", "de-a", True),      # diacritics — the old class split every one
    ("de", "Fahrplan der Straßenbahn", "de-b", True),        # ß
    ("es", "la habitación del niño", "es-a", True),
    ("es", "precio del café", "es-b", True),
    ("fr", "l'élève à l'école", "fr-a", True),               # apostrophes separate · accents stay
    ("fr", "réunion déplacée au jeudi", "fr-b", True),
    ("ja", "セッションの上限", "ja-a", True),                 # katakana · particle dropped · kanji
    ("ja", "東京の天気", "ja-b", True),
    ("ja", "上限を超える", "ja-a", True),                     # inflection: 超えた ↔ 超える meet at the kanji stem
    ("ko", "세션 상한", "ko-a", True),
    ("ko", "서울 날씨", "ko-b", True),
    # △ known gap — a Latin word is one exact term, so inflection does not meet (English has the same gap today:
    #   `restarted` ≠ `restart`). Recorded, not counted. §4-c will decide whether stemming/compounds are worth it.
    ("de", "geprüfte Größen", "de-a", False),
]

FAILS: list[str] = []


def _write_fixture(src_dir: str) -> None:
    os.makedirs(src_dir, exist_ok=True)
    for name, title, body in DOCS:
        with open(os.path.join(src_dir, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("# %s\n\n%s\n" % (title, body))


def _rank(results, needle: str) -> int:
    for i, r in enumerate(results, 1):
        if needle in r["name"]:
            return i
    return 0


def main() -> int:
    src_dir = os.path.join(_TMP, "src")
    _write_fixture(src_dir)
    cfg = {"language": "en",
           "sources": [{"name": "fixture", "path": src_dir, "include": ["*.md"], "max_depth": 1}]}
    db = store.connect()
    t0 = time.time()
    stats = store.reindex(db, cfg, full=True, recalibrate=False)
    print("=" * 74)
    print("tokenizer v%d · fixture: %d documents in %d languages · indexed in %.2fs · terms %d"
          % (textindex.TOKENIZER_VERSION, stats["added"], len({n[:2] for n, _, _ in DOCS}),
             time.time() - t0, db.execute("SELECT COUNT(*) FROM terms").fetchone()[0]))
    print("⛔ synthetic — proves the tokenizer ★reaches★ each language, not that search ★works well★ in it")
    print("=" * 74)

    ok_all = stats["added"] == len(DOCS)
    if not ok_all:
        FAILS.append("indexed %d of %d fixture documents" % (stats["added"], len(DOCS)))
    print("  %s every fixture document indexed (%d/%d)" % ("✅" if ok_all else "❌", stats["added"], len(DOCS)))

    # ① every document produced terms in its own script — a document with none is invisible (the Japanese case)
    for name, _, body in DOCS:
        row = db.execute("SELECT id FROM docs WHERE name LIKE ?", ("%" + name + "%",)).fetchone()
        n_terms = db.execute("SELECT COUNT(*) FROM postings WHERE doc_id=?", (row["id"],)).fetchone()[0] if row else 0
        ok = n_terms >= 5
        if not ok:
            FAILS.append("%s has only %d terms — the text is invisible to search" % (name, n_terms))
    print("  %s every document has terms of its own (the Japanese sentence used to produce zero)"
          % ("✅" if not any(f.endswith("invisible to search") for f in FAILS) else "❌"))

    # ② each language's query puts its own document first
    print("-" * 74)
    passed = strict = 0
    for lang, q, want, is_strict in QUERIES:
        res = search.recall(db, q, k=3, log=False)
        pos = _rank(res, want)
        hit = pos == 1
        if is_strict:
            strict += 1
            passed += 1 if hit else 0
            mark = "✅" if hit else "❌"
            if not hit:
                FAILS.append("[%s] %r → rank %s (wanted %s first; got %s)"
                             % (lang, q, pos or "-", want, [r["name"][-4:] for r in res[:3]]))
        else:
            mark = "△ " if not hit else "✅"
        print("  %s [%s] %-28s → %-4s rank %s%s"
              % (mark, lang, q, want, pos or "-", "" if is_strict else "   (known gap · not counted)"))
    print("-" * 74)
    print("  strict rows first-ranked: %d / %d" % (passed, strict))

    # ③ ★the rebuild trigger★ — an index built by an older tokenizer must be re-read once, and only once,
    #    and never from the hook path (a full pass measured 22s on 1,393 documents against a 200ms budget).
    print("-" * 74)
    print("  the tokenizer stamp (§store.tokenizer_stale)")
    checks = []
    checks.append(("a fresh full pass stamps the index (not stale)", not store.tokenizer_stale(db)))
    store.set_meta(db, "tokenizer", "1")                        # pretend an index from before 2026-09-08
    checks.append(("an index stamped 1 is stale", store.tokenizer_stale(db)))
    st_hook = store.reindex(db, cfg, recalibrate=False)          # the hook's call shape
    checks.append(("the hook path does NOT rebuild (skipped %d · reports tokenizer_stale)" % st_hook["skipped"],
                   st_hook.get("tokenizer_stale") is True and st_hook["updated"] == 0
                   and store.get_meta(db, "tokenizer") == "1"))
    t1 = time.time()
    st_cli = store.reindex(db, cfg)                              # a human-run index (`brain index`)
    checks.append(("a human-run index rebuilds every document once (updated %d · tokenizer_rebuilt=%s · %.2fs)"
                   % (st_cli["updated"], st_cli.get("tokenizer_rebuilt"), time.time() - t1),
                   st_cli.get("tokenizer_rebuilt") == textindex.TOKENIZER_VERSION
                   and st_cli["updated"] == len(DOCS) and not store.tokenizer_stale(db)))
    st_again = store.reindex(db, cfg)
    checks.append(("and the next one is incremental again (skipped %d)" % st_again["skipped"],
                   st_again["skipped"] == len(DOCS) and "tokenizer_rebuilt" not in st_again))
    for label, ok in checks:
        if not ok:
            FAILS.append("stamp: " + label)
        print("  %s %s" % ("✅" if ok else "❌", label))

    print("\n" + "=" * 74)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the tokenizer reaches all six languages — quality in de·es·fr·ja is ★unmeasured★ (no real sample)")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
        if _HOME_BEFORE is None:
            os.environ.pop("BRAIN_HOME", None)
        else:
            os.environ["BRAIN_HOME"] = _HOME_BEFORE
    sys.exit(code)
