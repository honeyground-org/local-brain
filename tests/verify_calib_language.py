#!/usr/bin/env python3
"""★The noise control follows the corpus, not the screen★ — the ruler must not depend on a setting. (local · budget 0)

## The incident (09-30 → 10-06)

`hook_threshold_history` flapped 9.6x ↔ 8.0x for a week, each low value reverting 2–12 minutes later.
At 8.0x the hook fired on ~80% of real prompts ("the threshold drowned in noise"). Cause, measured on
one Korean corpus with the same code in the same minute:

    control sentences in Korean  → floor 7.17 → threshold 9.68
    control sentences in English → floor 5.99 → threshold 8.08

The six control sentences were chosen by ★the screen language★ — a proxy for the corpus language that
held only while the screen followed the OS locale, and not even then for a process without LANG (a
scheduled job, another host). Whoever calibrated last decided which set won.

## What this holds down

  ① the corpus decides the languages — synthetic corpora in each language are recognised, and an
     empty one falls back to English (never to the screen language)
  ② ⛔ the control set and the measured threshold are ★identical under every screen language★ — on this
     machine's real index when there is one
  ③ ⛔ not "every language" either — a language the corpus lacks is left out (its function words sit at
     df≈1 and look like rare signal: one Spanish control scored 10.90 on a Korean corpus)
  ④ control group — the old selection (by screen language) ★does★ fail ②, so a pass means something
"""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain import calibrate, i18n, langdata, store  # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", name, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def _fake_corpus(n_docs: int, df: dict) -> sqlite3.Connection:
    """The two tables `language_shares` reads — enough to decide a language, nothing else."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY, doclen INTEGER)")
    db.execute("CREATE TABLE terms(term TEXT PRIMARY KEY, df INTEGER)")
    for t in ("postings", "links", "meta"):
        db.execute("CREATE TABLE %s(k TEXT, v TEXT)" % t)
    db.executemany("INSERT INTO docs(doclen) VALUES (?)", [(10,)] * n_docs)
    db.executemany("INSERT INTO terms VALUES (?, ?)", list(df.items()))
    return db


def _with_lang(code: str, fn):
    saved_env, saved = os.environ.get("BRAIN_LANG"), i18n._lang
    os.environ["BRAIN_LANG"] = code
    i18n._lang = None
    try:
        return fn()
    finally:
        if saved_env is None:
            os.environ.pop("BRAIN_LANG", None)
        else:
            os.environ["BRAIN_LANG"] = saved_env
        i18n._lang = saved


def recognises_each_language() -> None:
    print("\n★① the corpus decides★")
    for code, words in langdata.LANG_MARKERS.items():
        db = _fake_corpus(100, {w: 40 for w in words})
        got = calibrate.corpus_languages(db)
        check("a %s corpus is read as %s" % (code, code), got == [code], str(got))
    mixed = {w: 50 for w in langdata.LANG_MARKERS["ko"]}
    mixed.update({w: 25 for w in langdata.LANG_MARKERS["en"]})
    got = calibrate.corpus_languages(_fake_corpus(100, mixed))
    check("Korean prose with English identifiers → both", got == ["en", "ko"], str(got))
    got = _with_lang("ko", lambda: calibrate.corpus_languages(_fake_corpus(5, {})))
    check("★an empty corpus falls back to English — even with the screen in Korean★", got == ["en"], str(got))
    trace = {w: 1 for w in langdata.LANG_MARKERS["ja"]}
    got = calibrate.corpus_languages(_fake_corpus(1000, trace))
    check("nothing clears the bar → the strongest trace", got == ["ja"], str(got))


def leaves_absent_languages_out() -> None:
    print("\n★③ only the languages the corpus is written in★")
    db = _fake_corpus(100, {w: 50 for w in langdata.LANG_MARKERS["ko"]})
    probes = calibrate.neutral_probes(db)
    es = i18n.catalog("es").get("calib.noise.2", "")
    ko = i18n.catalog("ko").get("calib.noise.2", "")
    check("the Korean six are in", bool(ko) and ko in probes)
    check("⛔ the Spanish six are out (function words at df≈1 would look like signal)", es not in probes)


def independent_of_screen(db: sqlite3.Connection, label: str, select=None) -> bool:
    """Same probes and same threshold under every screen language. Returns whether they agreed."""
    original = calibrate.neutral_probes
    if select is not None:
        calibrate.neutral_probes = select
    try:
        seen = {}
        for code in ("en", "ko", "de"):
            seen[code] = _with_lang(code, lambda: (tuple(calibrate.neutral_probes(db)),
                                                   calibrate.measure(db)["threshold"]))
    finally:
        calibrate.neutral_probes = original
    probes = {v[0] for v in seen.values()}
    thresholds = {c: v[1] for c, v in seen.items()}
    agreed = len(probes) == 1 and len(set(thresholds.values())) == 1
    print("   %s: thresholds %s" % (label, thresholds))
    return agreed


def _old_selection(db: sqlite3.Connection):
    """The pre-2026-10-06 rule, kept only as the control: the screen language's six + four English."""
    out = []
    for i in range(1, 7):
        val = i18n.t("calib.noise.%d" % i)
        if val and val != "calib.noise.%d" % i:
            out.append(val)
    return out + calibrate._NEUTRAL_EN


def main() -> int:
    recognises_each_language()
    leaves_absent_languages_out()

    print("\n★② the ruler does not move with the screen language★")
    path = store.default_db_path() if hasattr(store, "default_db_path") else ""
    try:
        db = store.connect()
        n = store.corpus_stats(db)["docs"]
    except Exception as e:                               # noqa: BLE001
        db, n = None, 0
        print("   (no index on this machine: %s)" % e)
    if db is not None and n >= 50:
        check("★same probes, same threshold under en · ko · de★ (this machine's %d documents)" % n,
              independent_of_screen(db, "now"))
        langs = calibrate.corpus_languages(db)
        print("   corpus languages here: %s · shares %s" % (langs, calibrate.language_shares(db)))
        print("\n★④ control group — the old rule must fail ②★")
        old_agreed = independent_of_screen(db, "old rule", select=_old_selection)
        if len(langs) == 1 and langs[0] == "en":
            print("   (an English-only corpus cannot tell the two rules apart — control not applicable here)")
        else:
            check("⛔ selecting by screen language ★does★ move the ruler (the check can fail)", not old_agreed)
    else:
        print("   ⚠️ skipped — needs an index of 50+ documents (%s)" % (path or "none"))

    print("\n" + "=" * 74)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the noise control follows the corpus — no setting moves the ruler")
    return 0


if __name__ == "__main__":
    sys.exit(main())
