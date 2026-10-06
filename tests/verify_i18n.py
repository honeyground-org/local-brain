"""i18n check — ★does a translation quietly rot★. (local · budget 0)

## Why this check exists

A translation ★rots for certain if it isn't counted★ — code changes and the catalog doesn't follow.
And the way it rots is silent: `t()` falls back to English when it can't find a key, so ★a translation
was added but never shows up★ stays that way with no error at all.

## This scanner exposed ★four of its own defects★ on 2026-09-02 (all four are guarded by this check)

    ① counted something with only dots (`t("...")`) as a key        → it must have at least one character
    ② counted the doc's own example (`t("a.b" if x else "c.d")`) as a key → strips comments·docstrings
    ③ read a newline inside parens as a sentence start, so ★it deleted a string inside a list as a docstring★
    ④ broke a multi-line call at the newline, so ★4 actually-used keys were reported as 'unused'★
       (deleting them on that word would have broken the screen)

★If the counting tool is wrong, it invents problems that don't exist and hides the ones that do.★
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import i18n                                    # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    print("=" * 72 + "\ni18n — does a translation quietly rot\n" + "=" * 72)

    # ── ① is English canonical and complete ────────────────────────────────────────
    a = i18n.audit()
    check("actually finds the keys the source calls (0 means the scanner is dead)",
          a["used"] > 0, "%d found" % a["used"])
    check("★English (canonical) has no missing key★", not a["en_missing"],
          ", ".join(a["en_missing"][:5]) or "none")
    check("★no unused key★ (calling it by an alias gets caught here)", not a["en_unused"],
          ", ".join(a["en_unused"][:5]) or "none")

    # ── ② language-decision order ──────────────────────────────────────────────────
    saved = i18n._lang
    try:
        os.environ["BRAIN_LANG"] = "de"
        i18n._lang = None
        # ⛔ if config.json has a `language`, it wins — skip that case
        if not i18n._from_config():
            check("BRAIN_LANG beats the OS locale", i18n.lang(refresh=True) == "de",
                  i18n.lang())
        # ⛔ ★the check must never rely on the environment★ — the OS locale is planted on purpose, so a
        #    pass means "the locale was there and was ignored", not "there happened to be no locale".
        saved_env = {k: os.environ.get(k) for k in ("BRAIN_LANG", "LC_ALL", "LANG")}
        os.environ.pop("BRAIN_LANG", None)
        os.environ["LC_ALL"] = "fr_FR.UTF-8"
        os.environ["LANG"] = "ko_KR.UTF-8"
        i18n._lang = None
        if not i18n._from_config():
            check("★the OS locale never picks the language★ (another language is chosen, not inferred)",
                  i18n.lang(refresh=True) == "en", i18n.lang())
        os.environ["BRAIN_LANG"] = "kl_KL"                 # an unknown language (a typo)
        i18n._lang = None
        if not i18n._from_config():
            check("★an unknown BRAIN_LANG skips that slot★ (one typo shouldn't kill the screen)",
                  i18n.lang(refresh=True) == "en", i18n.lang())
        for k in ("BRAIN_LANG", "LC_ALL", "LANG"):
            os.environ.pop(k, None)
        i18n._lang = None
        if not i18n._from_config():
            check("★with nothing at all, it's en★ (English is the default)",
                  i18n.lang(refresh=True) == "en", i18n.lang())
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
    finally:
        os.environ.pop("BRAIN_LANG", None)
        i18n._lang = saved

    # ── ③ ★never dies on screen★ ─────────────────────────────────────────
    check("a missing key returns the key itself (never raises)",
          i18n.t("no.such.key.here") == "no.such.key.here")
    check("never dies even with no placeholder given",
          isinstance(i18n.t("guard.off.body"), str))

    # ── ④ ★does it catch a placeholder mismatch in advance★ — deliberately break one ─────────
    #    This is the most dangerous kind of rot: a translation was added and English quietly comes out anyway.
    code = "de"
    path = os.path.join(i18n.CATALOG_DIR, "%s.json" % code)
    with open(path, encoding="utf-8") as fh:
        original = fh.read()
    try:
        broken = json.loads(original)
        broken["guard.off.body"] = "Kein Beispiel ({a} Antworten)"   # {c} etc. are missing
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(broken, fh, ensure_ascii=False, indent=1)
        i18n._cache.pop(code, None)
        row = i18n.audit()["langs"][code]
        check("★catches a translation whose placeholders differ★ (miss it and English quietly comes out)",
              "guard.off.body" in row["placeholder_mismatch"],
              "%d mismatch(es)" % len(row["placeholder_mismatch"]))
        # And even in that state ★the screen must not die★ and has to fall back to English
        i18n._lang = code
        out = i18n.t("guard.off.body", a=1, c=2, need_a=30, need_c=20)
        check("even with a broken translation, ★it falls back to English and never dies★",
              "evaluation sample" in out, out[:50])
    finally:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original)
        i18n._cache.pop(code, None)
        i18n._lang = saved

    # ── ⑤ does the scanner avoid counting ★an example inside a docstring★ as a key (defect ②) ────────
    sample = 'def f():\n    """doc t("fake.in.doc") end"""\n    # t("fake.in.comment")\n    return t("real.one")\n'
    keys = set(i18n._keys_in(i18n._code_only(sample, "x.py")))
    check("never counts an example inside a docstring·comment as a key", keys == {"real.one"}, str(sorted(keys)))

    # ── ⑥ does it catch a multi-line call (defect ④) ────────────────────────────
    multi = 'x = t(\n    "multi.line.key",\n    a=1,\n)\ny = t("second" if p else "second.alt")\n'
    keys2 = set(i18n._keys_in(i18n._code_only(multi, "x.py")))
    check("★finds every key, including a multi-line call and inside a ternary★",
          {"multi.line.key", "second.alt"} <= keys2, str(sorted(keys2)))

    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAIL), ", ".join(FAIL)) if FAIL else "✅ all passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
