"""Dashboard language check — ★does the ★whole thing★ come out in the chosen language★. (local · budget 0)

## Why this check exists (2026-09-02, caught by the user)

Labels went into 6 languages and it looked "done," but when the user opened the English screen the
comparison table was ★entirely in Korean★. A full audit found ★761 pieces★, from five different sources.

    graph_html (legend·meta·tooltips)   1,668 chars   ← the picture was carrying its own words
    details_html (the old table)          600 chars   ← thought folding it away was enough (it wasn't)
    bench_rows (scorecard)                18 cells   ← held only Korean sentences, nothing to translate
    issues/metrics (health)               18 cells
    kpi subtitle                           1 cell

★Lesson: translating a screen is not translating labels.★ ★Every producer★ that sends text to the
screen has to carry a key. So this check looks not at labels but at the ★rendered page★ —
it catches it no matter where it came from.

## ⛔ Splits data from UI

A memory's ★name★ (`feedback_no_self_merge`) and its kind value are ★data★ — translating them is wrong.
What gets translated is only the human-facing words. So this check counts only ★Hangul syllables★:
a memory name is ASCII so it never gets caught, and a Korean UI string always does.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import dashboard, dashview, i18n, store                # noqa: E402

HANGUL = re.compile(r"[가-힣]")
FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def visible(html: str) -> str:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", "\n", body)


def verify_observatory():
    import datetime
    import json
    import sqlite3
    import tempfile
    from pathlib import Path
    from brain import observatory
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    store._init(db)
    for n in (1, 2, 3):
        db.execute("INSERT INTO docs(id,name,path,source,kind,sha,evidence_date,description) VALUES(?,?,?,?,?,?,?,?)",
                   (n, "note_%d" % n, "/notes/%d.md" % n, "memory", "project" if n == 1 else "lesson",
                    str(n), "2026-01-02", "</script><script>bad()</script>" if n == 2 else "Evidence"))
    db.execute("INSERT INTO links VALUES(2,'note_1')")
    for day in ("2026-01-01", "2026-01-02"):
        db.execute("INSERT INTO recalls(ts,query,hits,top) VALUES(?,?,?,?)",
                   (day+"T12:00:00", "repeat", 1, '["note_2"]'))
    db.commit()
    before = list(db.iterdump())
    original_home = observatory.analysis_home
    with tempfile.TemporaryDirectory() as temp:
        observatory.analysis_home = lambda: Path(temp)
        try:
            now = datetime.datetime(2026, 1, 3)
            data = observatory.collect(db, days=7, now=now)
            check("explorer counts recorded repeats and distinct days", data["repeated"][0]["count"] == 2 and data["repeated"][0]["days"] == 2)
            check("explorer edges are explicit and directions preserved", data["edges"] == [(2, 1)])
            check("explorer classification carries provenance", data["nodes"][1]["basis"] == "linked")
            packet = {"snapshot": observatory.snapshot_id(db), "question": "Explore evidence", "lenses": [
                {"title": "A new perspective", "why": "Observed connection", "categories": [{"label": "A group", "ids": [1, 2]}]}],
                "insights": [{"kind": "hypothesis", "text": "A possibility", "ids": [2]}]}
            saved = observatory.save_analysis(db, packet)
            observatory.record_feedback({"revision": saved["revision"], "index": 0, "action": "revise", "comment": "Separate these categories"})
            next_context = observatory.analysis_context(db, offset=0, limit=2)
            check("host receives prior revision and correction on the next analysis", next_context["previous"]["revision"] == saved["revision"] and next_context["feedback"][0]["action"] == "revise")
            check("host context is paginated without silently dropping the rest", next_context["total"] == 3 and next_context["next_offset"] == 2)
            for label, changed in (("unknown evidence", dict(packet, lenses=[{"title": "Bad", "why": "Invalid", "categories": [{"label": "Unknown", "ids": [999]}]}])),
                                   ("outdated snapshot", dict(packet, snapshot="obsolete"))):
                rejected = False
                try:
                    observatory.save_analysis(db, changed)
                except ValueError:
                    rejected = True
                check("control: reject " + label, rejected)
            data = observatory.collect(db, days=7, now=now)
            check("current cited analysis can be used", data["analysis_current"])
            page = observatory.render(data)
            check("embedded evidence cannot terminate its JSON script", "</script><script>bad()" not in page and "\\u003c/script" in page)
            check("offline page blocks network calls", "connect-src 'none'" in page)
            for lang in i18n.LANGS:
                check("explorer ships translated controls for " + lang, '"'+lang+'"' in page and i18n.catalog(lang)["obs.title"] in page)
            check("read and publish leave canonical DB untouched", before == list(db.iterdump()))
            db.execute("UPDATE docs SET sha='changed' WHERE id=3")
            check("unrelated changes preserve cited lenses", observatory.collect(db, now=now)["analysis_current"])
            db.execute("UPDATE docs SET sha='changed' WHERE id=2")
            check("control: changed cited evidence disables old lenses", not observatory.collect(db, now=now)["analysis_current"])
            check("only valid published revisions exist", len(list(Path(temp).glob("analysis-*.json"))) == 1)
        finally:
            observatory.analysis_home = original_home
            db.close()


def main():
    print("=" * 72 + "\ndashboard — does everything come out in the chosen language\n" + "=" * 72)
    db = store.connect()
    saved_env, saved_lang = os.environ.get("BRAIN_LANG"), i18n._lang
    try:
        pages = {}
        for code in i18n.LANGS:
            os.environ["BRAIN_LANG"] = code
            i18n._lang = None
            i18n._cache.clear()
            i18n.lang(refresh=True)
            pages[code] = dashview.render(dashboard.payload(db))

        # ── ① not a single Hangul character remains on a non-Korean language ──────────
        for code, html in pages.items():
            if code == "ko":
                continue
            left = [l.strip() for l in visible(html).split("\n")
                    if l.strip() and HANGUL.search(l)]
            check("%s: no Korean remains on screen" % code, not left,
                  "%d found %s" % (len(left), [x[:34] for x in left[:2]]) if left else "")

        # ── ② the Korean screen comes out in Korean (the switcher isn't dead) ────────
        check("ko: the screen comes out in Korean",
              HANGUL.search(visible(pages["ko"])) is not None)

        # ── ③ ★per source★, was it actually translated (looking only at labels misses it) ──────────
        en = pages["en"]
        for what, needle in (("comparison table row name", "Answer found"),
                             ("diagnostic tile name", "Broken links"),
                             ("graph legend", "Rules"),
                             ("graph meta", "connected"),
                             ("budget legend", "held for interactive recall"),
                             ("axis name", "Reach")):
            check("the English screen has %s in English" % what, needle in en, needle)

        # ── ③-b ⛔ ★does it switch on the client side too★ — pins down a spot frozen at server language
        #    (2026-09-02: the catalog had the translation, but `UI_PREFIX` dropped `bench.`·`health.`,
        #     and the comparison table on the Japanese screen stayed in English. ★Make a key and it has
        #     to go into that list too★ — and this check enforces that.)
        from brain import dashview as _dv
        cat_en = i18n.catalog("en")
        used_prefixes = set()
        for html in pages.values():
            for k in re.findall(r'data-t="([A-Za-z0-9_.\-]+)"', html):
                used_prefixes.add(k.split(".")[0] + ".")
        missing_pref = sorted(pfx for pfx in used_prefixes
                              if pfx not in _dv.UI_PREFIX)
        check("★every prefix the screen uses is in the client-side table★",
              not missing_pref, ", ".join(missing_pref) or str(sorted(used_prefixes)))

        # And are the translations of those keys actually in the page (one-language sample)
        ja = i18n.catalog("ja")
        for k in ("bench.answer", "health.dangling", "kind.feedback", "graph.meta"):
            check("the Japanese translation is in the page: %s" % k,
                  ja.get(k) and ja[k] in pages["en"], (ja.get(k) or "")[:24])

        # ── ④ ⛔ a memory's ★name★ is never translated (it's data) ───────────────
        # ⛔ checked with ★this corpus's own names★ — it used to look for the author's prefixes
        names = [r[0] for r in db.execute(
            "SELECT name FROM docs WHERE source='memory' ORDER BY name LIMIT 400")]
        if names:
            check("★a memory name stays as-is★ (translating data is wrong)",
                  any(n in en for n in names), "%d names looked for" % len(names))
        else:
            print("⏭ a memory name stays as-is — no notes here to look for")

        # ── ⑤ all six languages are ★in the page★ (switching is offline) ────
        for code in i18n.LANGS:
            check("%s's catalog is in the page (never calls a CDN)" % code,
                  '"%s"' % code in pages["en"])
        check("no request goes outward (0 http references)",
              "http://" not in pages["en"] and "https://" not in pages["en"])

        # ── ⑤-b ⛔ ★the JS ships without a syntax error★ — a raw newline inside a string kills it
        #    (2026-09-02: a `\n` inside a Python string became an actual newline, `Invalid or
        #     unexpected token` — ★the whole language switcher died while the screen looked fine★.)
        for code, html in pages.items():
            js = re.search(r"<script>(.*)</script>", html, re.S)
            check("%s: no broken string in the JS" % code, js is not None)
            if js:
                bad = [l for l in js.group(1).split("\n")
                       if l.count("'") % 2 == 1 and "var T=" not in l]
                check("%s: quotes are balanced (no raw newline)" % code, not bad,
                      (bad[0][:50] if bad else ""))

        # ── ⑥ a placeholder never leaks through as literal text ──────────────────────────
        for code, html in pages.items():
            raw = re.findall(r"\{(?:at|pct|names|n|e)\}", visible(html))
            check("%s: no placeholder leaks onto the screen" % code, not raw, str(raw[:3]))
    finally:
        os.environ.pop("BRAIN_LANG", None)
        if saved_env:
            os.environ["BRAIN_LANG"] = saved_env
        i18n._lang = saved_lang
        i18n._cache.clear()

    verify_observatory()
    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAIL), ", ".join(FAIL)) if FAIL else "✅ all passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
