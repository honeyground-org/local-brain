"""Dashboard — ★it says first whether there is an issue, then what to do about it★.

## What this screen is for

It is not a screen that shows numbers. There is one question: **is there anything to fix right now?**
So the verdict is at the top, the grounds below it, and every item ends with *"so what should I do"*.
A box with a number and no next action has no reason to be on this screen.

## ⛔ It is a local file — nothing is sent out

The screen shows the **names and summaries** of memories. Ours hold references to production
credentials, client incidents and internal revenue structure. A principle of this system (text does not
leave) that breaks on the dashboard is not a principle. So it is written to a file and opened in a browser.

## The verdict rule

Each diagnostic carries one of **four states** (good · warning · serious · critical). Colour never
carries meaning alone — an icon and a word always travel with it (colour blindness, monochrome print, forced-colour mode).
"""
from __future__ import annotations

import html
import json
import os
import sqlite3
import time
import webbrowser
from typing import Dict, List, Tuple

from brain import calibrate, graphview, health, store, dashstyle

# Status palette — fixed values that do not collide with the series colours. An icon and a word always accompany them.
_STATUS = {
    "good":     ("good", "●", "#0ca30c"),
    "warning":  ("warning", "▲", "#fab219"),
    "serious":  ("serious", "◆", "#ec835a"),
    "critical": ("critical", "■", "#d03b3b"),
}


def _grade(value: int, warn: int, serious: int, critical: int) -> str:
    if value >= critical:
        return "critical"
    if value >= serious:
        return "serious"
    if value >= warn:
        return "warning"
    return "good"


def _monthly_counts(db: sqlite3.Connection, months: int = 18) -> List[Tuple[str, int]]:
    """Memory count per month by ★evidence date★ — ★not the file's modification date★.
    "When was this learnt" is closer to the brain's growth than "when was the file touched"."""
    rows = db.execute(
        "SELECT substr(evidence_date,1,7) m, COUNT(*) c FROM docs "
        "WHERE source='memory' AND evidence_date != '' "
        "GROUP BY m ORDER BY m").fetchall()
    return [(r["m"], r["c"]) for r in rows][-months:]


def _bench_html(db) -> str:
    """The ★raw values with their units★ — approaches are only ever compared through this."""
    from brain import scorecard as sk
    try:
        b = sk.bench(db)
    except Exception:                                    # noqa: BLE001
        return '<p class="scnote">The raw-value comparison could not be measured.</p>'
    rows = []
    for label, unit, mv, gv, cv, hi, note, *_k in b["rows"]:
        vals = [v for v in (mv, gv, cv) if v is not None]
        best = (max(vals) if hi else min(vals)) if vals else None
        def cell(v):
            if v is None:
                return '<td class="na">n/a</td>'
            t = ("%.1f" % v) if unit not in ("items", "docs") else ("%.0f" % v)
            return '<td%s>%s</td>' % (' class="win"' if v == best else "", t)
        rows.append("<tr><td>%s</td><td class=\"na\">%s</td>%s%s%s</tr>"
                    % (_E(label), _E(unit), cell(mv), cell(gv), cell(cv)))
        if note:
            rows.append('<tr><td colspan="5" class="na" style="text-align:left;'
                        'padding-top:0;font-size:11.5px;border-bottom:1px solid var(--grid)">'
                        '%s</td></tr>' % _E(note))
    return ('<table class="sc"><tr><th>measure</th><th>unit</th><th>brain</th>'
            '<th>grep OR</th><th>agent alone</th></tr>' + "".join(rows) + "</table>"
            + '<p class="scnote">%d queries · %d memories · grep OR catches a median of %d documents</p>'
            % (b["n_queries"], b["n_mem"], b["grep_median"]))


def _score_html(db) -> str:
    """★Performance must be visible on the status screen too★ (user instruction 2026-08-31).

    ⛔ The axis that costs remote calls (precision) is ★not called here★ — spending the judge's budget
       every time the dashboard opens eats the interactive path's share. That axis is measured by
       `brain score --full`, which leaves it in `scorecard.jsonl`, and here ★the most recent record★ fills it in.
       (Absent, it stays '—' — the unmeasured is counted neither as 0 nor as full marks.)
    """
    from brain import scorecard as sk
    t = sk.three_way(db, full=False)
    # ⛔ ★One implementation★ (2026-09-28) — this used to carry the value over with its own inline
    #    copy, which filled ★precision only★ while stability stayed blank, even though the two come
    #    out of the same remote pass. The rule now lives in §scorecard.carry_remote and both screens
    #    read it there.
    try:
        raw, carried, _at = sk.carry_remote(
            {r["key"]: r["raw"] for r in t["brain"]["axes"]})
        if carried:
            fresh = sk.compute(raw)
            by_key = {r["key"]: r for r in fresh["axes"]}
            for row in t["brain"]["axes"]:
                row["score"] = by_key[row["key"]]["score"]
            t["brain"]["total"] = fresh["total"]
    except Exception:                                    # noqa: BLE001
        pass
    return _score_table(t)


def collect(db: sqlite3.Connection) -> dict:
    """Gather every value the screen uses in one pass (the screen does not compute)."""
    st = health.status(db)
    sug = health.suggestions(db)
    checks = st["distortion_checks"]
    n_mem = st["by_source"].get("memory", 0) or 1

    n_recalls = db.execute("SELECT COUNT(*) c FROM recalls").fetchone()["c"]
    reach_note = _reachability_note(db, checks["orphans"]["sample"])

    # ★The threshold is not an absolute count but a proportion of scale★ — 50 orphans out of 100
    # memories and 50 out of 10,000 are entirely different stories.
    def pct(n):
        return 100.0 * n / n_mem

    # ★There is one set of cards, and `health.CHECK_KIND` is what splits them into issues/metrics★ (2026-08-31)
    #
    # ⛔ Before that the same rule lived in ★three places★ — `health.CHECK_KIND` · this file's `issues`
    #    array · and even `verify_diagnostics`, which claimed to watch for drift between the two, held its
    #    own constant (`{"dangling","stale","dup"}`). A check that does not read the canonical source
    #    ★cannot catch drift★. So now the code does the splitting and a human only writes the cards.
    _sv = checks["stale_evidence"].get("split") or {}
    cards = {
        "dangling_links": {
            "key": "dangling", "title": "Broken links",
            "count": checks["dangling_links"]["count"], "of": n_mem,
            "status": _grade(checks["dangling_links"]["count"], 15, 40, 120),
            "meaning": "a memory points at [[a name that does not exist]] — not an error, it silently fails empty-handed",
            "action": "check whether it was renamed or is a memory not yet written, then connect it",
            "note": ("%d document links pointing outside the index were excluded — only what can be fixed is counted"
                     % checks["dangling_links"].get("outside_memory", 0)),
            "sample": ["%s → %s" % (d["from"], d["to"])
                       for d in checks["dangling_links"]["sample"][:6]],
        },
        "stale_evidence": {
            # ⛔ 2026-08-31 ★issue → metric★ — decomposing the number showed it was not a task.
            #    100% is `project` (going stale once finished is normal) · 69% was never recalled once ·
            #    the risk is already blocked at recall time by `⚠︎ the evidence is old`.
            "key": "stale", "title": "Memories with stale evidence",
            "count": checks["stale_evidence"]["count"], "of": n_mem,
            "status": _grade(checks["stale_evidence"]["count"], 5, 20, 60),
            "meaning": "the measurement date in the body passed the per-kind threshold — it shows as ⚠︎ on recall",
            "action": "re-confirm and update it, or discard it if it is past",
            "evidence": ("all %s · recalled at least once %d / never %d — "
                         "the risk of quoting a stale number as current fact is already blocked at "
                         "recall time by `⚠︎ the evidence is old` (both MCP and the hook)"
                         % (" · ".join("%s %d" % (k, v)
                                       for k, v in (_sv.get("kinds") or {}).items()),
                            _sv.get("recalled", 0), _sv.get("never", 0))),
            "sample": ["%s (%s · %d days)" % (x["name"], x["kind"], x["evidence_age_days"])
                       for x in checks["stale_evidence"]["sample"][:6]],
        },
        "duplicate_candidates": {
            "key": "dup", "title": "Possible duplicates",
            "count": checks["duplicate_candidates"]["count"], "of": n_mem,
            "status": _grade(checks["duplicate_candidates"]["count"], 3, 8, 20),
            "meaning": "when one fact is split across two places, only one of them gets fixed",
            "action": "see whether they really say the same thing and, if so, merge them into one (otherwise link them)",
            "note": "already-linked pairs, pairs of different kinds and pairs differing more than 4× in size were excluded — that is a relation, not a duplicate",
            "sample": ["%s ↔ %s" % (d["a"], d["b"])
                       for d in checks["duplicate_candidates"]["sample"][:5]],
        },
        "orphans": {
            "key": "orphans", "title": "Memories with no connections",
            "count": checks["orphans"]["count"], "of": n_mem,
            "status": "good", "action": "",
            "meaning": "nothing links to it — the graph cannot reach it, but search can",
            "evidence": reach_note,
            "sample": checks["orphans"]["sample"][:6],
        },
        "never_recalled": {
            "key": "never", "title": "Memories never recalled yet",
            "count": checks["never_recalled"]["count"], "of": n_mem,
            "status": "good", "action": "",
            "meaning": ("the brain was just built — nothing has happened yet, which is not a problem"
                        if checks["never_recalled"].get("cold_start")
                        else "it is being piled up with no door that uses it"),
            "evidence": ("%d recall records — this number gains meaning once %d have accumulated"
                         % (n_recalls, health.is_cold_start.__defaults__[0])
                         if checks["never_recalled"].get("cold_start") else ""),
            "sample": checks["never_recalled"]["sample"][:6],
        },
    }

    # ★One canonical source decides the split★ — it is not divided by hand here.
    issues, metrics = [], []
    for _key, _card in cards.items():
        (issues if checks[_key].get("kind") == "issue" else metrics).append(_card)

    recent = [dict(r) for r in db.execute(
        "SELECT ts, query, hits, top FROM recalls ORDER BY id DESC LIMIT 12")]
    top_hits = [dict(r) for r in db.execute(
        "SELECT d.name, d.kind, u.hits, u.useful, u.wrong FROM usage u "
        "JOIN docs d ON d.path=u.path ORDER BY u.hits DESC LIMIT 10")]

    return {
        "score_html": _score_html(db),
        "bench_html": _bench_html(db),
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "corpus": st["corpus"],
        "by_source": st["by_source"],
        "by_kind": st["memory_by_kind"],
        "issues": issues,
        "metrics": metrics,
        "todo": sug,
        "monthly": _monthly_counts(db),
        "recent": recent,
        "top_hits": top_hits,
        "calibration": calibrate.measure(db),
        "graph": graphview.render(db),
        "db_bytes": os.path.getsize(store.db_path()) if os.path.exists(store.db_path()) else 0,
    }


# ------------------------------------------------------------------ render
_E = html.escape


def _tile(label: str, value: str, sub: str = "") -> str:
    return ('<div class="tile"><div class="t-label">%s</div>'
            '<div class="t-value">%s</div><div class="t-sub">%s</div></div>'
            % (_E(label), _E(value), _E(sub)))


def _cmds_html() -> str:
    """⛔ ★The place to find the commands must not exist only in the terminal★ (user instruction 2026-08-31).
    It uses ★the same source★ as the CLI's `help` — written twice, one copy goes stale."""
    from brain import i18n
    from brain.cli import HELP_GROUPS
    out = []
    for title_key, items in HELP_GROUPS:
        rows = "".join('<div><code>%s</code><span>%s</span></div>'
                       % (_E(c), _E(i18n.t(w))) for c, w in items)
        out.append('<div class="cmdg"><h4>%s</h4>%s</div>' % (_E(i18n.t(title_key)), rows))
    return '<div class="cmds">%s</div>' % "".join(out)


def _score_table(t: dict) -> str:
    """The three-approach score table. ⛔ ★An unmeasured cell is drawn as — , not 0.★"""
    rows = []
    for a, g, c in zip(t["brain"]["axes"], t["grep"]["axes"], t["claude"]["axes"]):
        def cell(v, best=False):
            if v is None:
                return '<td class="na">—</td>'
            return '<td%s>%.1f</td>' % (' class="win"' if best else "", v)
        vals = [x["score"] for x in (a, g, c)]
        top = max([v for v in vals if v is not None], default=None)
        rows.append("<tr><td>%s <span class=\"na\">%d%%</span></td>%s%s%s"
                    "<td class=\"na\" style=\"text-align:left\">%s</td></tr>"
                    % (_E(a["axis"]), a["weight"],
                       cell(a["score"], a["score"] is not None and a["score"] == top),
                       cell(g["score"], g["score"] is not None and g["score"] == top),
                       cell(c["score"], c["score"] is not None and c["score"] == top),
                       _E(a["what"])))
    fb = lambda k: ("—" if t[k]["total"] is None else "%.1f" % t[k]["total"])
    rows.append('<tr class="tot"><td>total / 100</td><td class="win">%s</td>'
                '<td>%s</td><td>%s</td><td></td></tr>'
                % (fb("brain"), fb("grep"), fb("claude")))
    ce = t["claude"].get("extra") or {}
    le = t["grep"].get("extra") or {}
    notes = ['⛔ An unmeasured axis is not 0 points, it is <b>—</b>, and it drops out of the total\'s denominator too.']
    if ce.get("_named") is not None:
        from brain import hosts
        _idx = os.path.basename(hosts.active().autoloaded_index(store.memory_dir())) or "the index file"
        notes.append("⛔ Why <b>the agent alone</b> is low — of the gold memories, only <b>%d</b> have "
                     "their name written in <code>%s</code>, the file it loads by itself. A memory not written "
                     "there is never called by anyone even though the file exists (the reason this project began)."
                     % (int(ce["_named"]), _E(_idx)))
    if le.get("_grep_median_docs"):
        notes.append("Note — reading <b>all of the median %.0f documents</b> that grep OR catches takes "
                     "reach to %.0f%%. The efficiency axis measures that price."
                     % (le["_grep_median_docs"], 100 * (le.get("_recall_exhaustive") or 0)))
    notes.append("⛔ <b>Axes where the old way wins are written down as they are</b> — reproducibility and "
                 "liveness do nothing by themselves, so there is nothing to wobble and nothing to stop.")
    return ('<table class="sc"><tr><th>axis</th><th>brain</th><th>grep OR</th>'
            '<th>agent alone</th><th style="text-align:left">what it measures</th></tr>'
            + "".join(rows) + "</table>"
            + "".join('<p class="scnote">%s</p>' % n for n in notes))


def _issue_card(it: dict) -> str:
    word, icon, color = _STATUS[it["status"]]
    sample = "".join('<li>%s</li>' % _E(s) for s in it["sample"])
    return """
<article class="card" data-status="%s">
  <header>
    <span class="badge" style="--c:%s"><span class="ic">%s</span>%s</span>
    <h3>%s</h3>
    <span class="count">%s</span>
  </header>
  <p class="meaning">%s</p>
  <p class="action"><span class="arrow">→</span> %s</p>
  %s%s
</article>""" % (
        it["status"], color, icon, word, _E(it["title"]), format(it["count"], ","),
        _E(it["meaning"]), _E(it["action"]),
        ('<p class="note">%s</p>' % _E(it["note"])) if it.get("note") else "",
        ('<details><summary>see %d examples</summary><ul class="sample">%s</ul></details>'
         % (len(it["sample"]), sample)) if it["sample"] else "")


def _metric_card(m: dict) -> str:
    pct = 100.0 * m["count"] / max(1, m["of"])
    sample = "".join('<li>%s</li>' % _E(s) for s in m["sample"])
    return """
<article class="card metric">
  <header><h3>%s</h3>
    <span class="count">%s<span class="of"> / %s · %.0f%%</span></span></header>
  <p class="meaning">%s</p>
  %s
  %s
</article>""" % (
        _E(m["title"]), format(m["count"], ","), format(m["of"], ","), pct,
        _E(m["meaning"]),
        ('<p class="evidence">✓ %s</p>' % _E(m["evidence"])) if m.get("evidence") else "",
        ('<details><summary>see %d examples</summary><ul class="sample">%s</ul></details>'
         % (len(m["sample"]), sample)) if m["sample"] else "")


def _reachability_note(db, names) -> str:
    """★Attach grounds to a metric★ — so that "it has no connections" is not read as "it is lost",
    it checks on the spot whether search actually finds it and writes that next to the number."""
    from brain import search
    names = list(names)[:8]
    if not names:
        return ""
    hit = 0
    for n in names:
        row = db.execute(
            "SELECT description, title FROM docs WHERE name=?", (n,)).fetchone()
        if not row:
            continue
        q = (row["description"] or row["title"] or "")[:80]
        if not q:
            continue
        res = search.recall(db, q, k=3, log=False)
        hit += any(x["name"] == n for x in res)
    return "%d of a %d-item sample appear in the top 3 of search — they are not gone" % (hit, len(names))


def _bars(monthly: List[Tuple[str, int]]) -> str:
    """Memories accumulated per month — a single series, so the title carries the name and no legend is needed.
    Bar ends are rounded 4px, with a 2px surface gap between them."""
    if not monthly:
        return '<p class="empty">No memory carries an evidence date yet.</p>'
    top = max(c for _, c in monthly) or 1
    cells = []
    for m, c in monthly:
        h = max(3, round(100.0 * c / top))
        cells.append(
            '<div class="bar-col" title="%s · %d">'
            '<div class="bar-wrap"><div class="bar" style="height:%d%%"></div></div>'
            '<div class="bar-x">%s</div></div>' % (_E(m), c, h, _E(m[2:])))
    return ('<div class="bars">%s</div>'
            '<p class="axis-note">vertical = memories in that month (max %d). '
            'horizontal = month by evidence date.</p>' % ("".join(cells), top))


def _todo_section(todo: dict) -> str:
    titles = {"verify": "re-confirm or discard", "reconnect": "connect the links",
              "merge": "merge", "prune": "prune", "missing": "questions that found nothing"}
    out = []
    for key in ("verify", "reconnect", "merge", "prune", "missing"):
        items = todo.get(key) or []
        if not items:
            continue
        rows = []
        for x in items[:8]:
            # ⛔ ★Do not guess the key names★ — at first only name/query/a·b were read, but
            # `reconnect` items use from/to, so the screen printed **`None → None`** on 15 lines
            # (with no error). It is a bug nobody catches without looking at it.
            if x.get("name"):
                label = x["name"]
            elif x.get("query"):
                label = x["query"]
            elif x.get("from") and x.get("to"):
                label = "%s ⇢ %s" % (x["from"], x["to"])
            elif x.get("a") and x.get("b"):
                label = "%s ↔ %s" % (x["a"], x["b"])
            else:
                continue          # what cannot be expressed is ★omitted rather than lied about★
            rows.append('<li><code>%s</code><span class="why">%s</span></li>'
                        % (_E(str(label)), _E(x.get("why", ""))))
        if not rows:
            continue
        out.append('<div class="todo-group"><h4>%s <span class="n">%d</span></h4>'
                   '<ul class="todo">%s</ul></div>'
                   % (_E(titles[key]), len(items), "".join(rows)))
    return "".join(out) or '<p class="empty">Nothing to do right now.</p>'


def render(data: dict) -> str:
    worst = max((i["status"] for i in data["issues"]),
                key=lambda s: list(_STATUS).index(s))
    n_bad = sum(1 for i in data["issues"] if i["status"] != "good")
    word, icon, color = _STATUS[worst]
    verdict = ("nothing to touch" if n_bad == 0
               else "%d to fix" % n_bad)

    c = data["corpus"]
    cal = data["calibration"]
    tiles = "".join([
        _tile("indexed documents", format(c["docs"], ","),
              " · ".join("%s %d" % kv for kv in sorted(data["by_source"].items()))),
        _tile("memories", format(data["by_source"].get("memory", 0), ","),
              " · ".join("%s %d" % kv for kv in sorted(data["by_kind"].items()))),
        _tile("connections", format(c["links"], ","), "edges of the [[link]] graph"),
        _tile("automatic recall threshold", str(cal["threshold"]),
              "noise floor %s × margin %s" % (cal["noise_floor"], cal["margin"])),
    ])

    recent = "".join(
        '<tr><td class="ts">%s</td><td>%s</td><td class="num">%d</td></tr>'
        % (_E(r["ts"][5:16].replace("T", " ")), _E(r["query"][:70]), r["hits"])
        for r in data["recent"]) or '<tr><td colspan="3" class="empty">No recall records yet.</td></tr>'

    hits = "".join(
        '<tr><td><code>%s</code></td><td class="dim">%s</td><td class="num">%d</td></tr>'
        % (_E(r["name"][:52]), _E(r["kind"] or "-"), r["hits"])
        for r in data["top_hits"]) or '<tr><td colspan="3" class="empty">No usage records yet.</td></tr>'

    from brain import i18n
    return _TEMPLATE % {
        "lang": i18n.lang(),
        "appearance_css": dashstyle.css(),
        "appearance_js": dashstyle.INIT,
        "appearance_controls": dashstyle.controls(),
        "generated": _E(data["generated_at"]),
        "verdict": _E(verdict),
        "v_icon": icon, "v_word": _E(word), "v_color": color,
        "tiles": tiles,
        "cmds": _cmds_html(),
        "bench": data.get("bench_html") or "",
        "score": data.get("score_html") or
                 '<p class="scnote">The performance measurement could not be read yet.</p>',
        "issues": "".join(_issue_card(i) for i in data["issues"]),
        "metrics": "".join(_metric_card(m) for m in data["metrics"]),
        "bars": _bars(data["monthly"]),
        "graph": data["graph"],
        "todo": _todo_section(data["todo"]),
        "recent": recent,
        "hits": hits,
        "db_mb": "%.1f" % (data["db_bytes"] / 1024 / 1024),
        "last_index": _E(c["last_index"] or "-"),
        "terms": format(c["terms"], ","),
    }


_TEMPLATE = """<!doctype html>
<html lang="%(lang)s"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>brain — status</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
  font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;
  padding:32px 20px 72px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:19px;margin:0;letter-spacing:-.01em}
h2{font-size:15px;margin:40px 0 12px;color:var(--ink2);font-weight:600}
h3{font-size:15px;margin:0;font-weight:600}
h4{font-size:13px;margin:0 0 8px;color:var(--ink2);font-weight:600}
.sub{color:var(--muted);font-size:13px;margin:4px 0 0}
.verdict{display:flex;align-items:center;gap:12px;margin:24px 0 8px;
  padding:18px 20px;background:var(--surface);border:1px solid var(--ring);border-radius:12px}
.verdict .ic{font-size:15px;color:var(--vc)}
.verdict .txt{font-size:22px;font-weight:650;letter-spacing:-.02em}
.verdict .st{font-size:13px;color:var(--ink2)}
.cmds{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;margin:10px 0}
.cmdg{border:1px solid var(--grid);border-radius:10px;padding:11px 13px;background:var(--surface)}
.cmdg h4{margin:0 0 7px;font-size:12px;color:var(--muted);font-weight:700;letter-spacing:.02em}
.cmdg div{display:flex;gap:8px;padding:3px 0;font-size:12.5px;line-height:1.5}
.cmdg code{background:var(--plane);border:1px solid var(--grid);border-radius:5px;
  padding:1px 6px;font-size:11.5px;white-space:nowrap;flex:0 0 auto}
.cmdg span{color:var(--ink2);min-width:0}
.sc{width:100%%;border-collapse:collapse;font-size:13px;margin:10px 0 6px}
.sc th,.sc td{padding:7px 9px;border-bottom:1px solid var(--grid);text-align:right}
.sc th:first-child,.sc td:first-child{text-align:left}
.sc th{color:var(--muted);font-weight:600;font-size:12px}
.sc .win{color:var(--bar);font-weight:700}
.sc .lose{color:#c2410c;font-weight:700}
.sc .na{color:var(--muted)}
.sc tr.tot td{border-top:2px solid var(--line);border-bottom:none;font-weight:700;font-size:14px;padding-top:10px}
.scnote{color:var(--muted);font-size:12px;line-height:1.6;margin:2px 0 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:16px 0}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:14px 16px}
.t-label{font-size:12px;color:var(--muted)}
.t-value{font-size:26px;font-weight:650;letter-spacing:-.02em;margin:2px 0}
.t-sub{font-size:11px;color:var(--muted);line-height:1.4}
.cards{display:grid;gap:10px}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:16px 18px}
.card header{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.badge{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;
  color:var(--ink2);border:1px solid var(--ring);border-radius:999px;padding:2px 9px}
.badge .ic{color:var(--c);font-size:11px}
.count{margin-left:auto;font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}
.meaning{margin:8px 0 0;color:var(--ink2);font-size:13.5px}
.action{margin:6px 0 0;font-size:13.5px}
.note{margin:6px 0 0;font-size:12px;color:var(--muted)}
.evidence{margin:6px 0 0;font-size:12.5px;color:#0ca30c}
.card.metric{border-style:dashed}
.of{font-size:12px;color:var(--muted);font-weight:400}
.arrow{color:var(--muted)}
details{margin-top:10px}
summary{cursor:pointer;font-size:12.5px;color:var(--muted)}
.sample{margin:8px 0 0;padding-left:18px;font-size:12.5px;color:var(--ink2)}
.sample li{margin:2px 0;word-break:break-all}
.bars{display:flex;align-items:flex-end;gap:2px;height:120px;
  background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:14px 14px 6px}
.bar-col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%%;min-width:0}
.bar-wrap{flex:1;display:flex;align-items:flex-end}
.bar{width:100%%;background:var(--bar);border-radius:4px 4px 0 0}
.bar-x{font-size:9.5px;color:var(--muted);text-align:center;margin-top:5px;
  white-space:nowrap;overflow:hidden}
.axis-note{font-size:11.5px;color:var(--muted);margin:6px 0 0}
.todo-group{background:var(--surface);border:1px solid var(--ring);border-radius:10px;
  padding:14px 16px;margin-bottom:10px}
.todo-group .n{color:var(--muted);font-weight:400}
ul.todo{margin:0;padding-left:16px;font-size:13px}
ul.todo li{margin:5px 0}
ul.todo code{font-size:12px;background:transparent;color:var(--ink)}
.why{display:block;color:var(--muted);font-size:12px}
table{width:100%%;border-collapse:collapse;background:var(--surface);
  border:1px solid var(--ring);border-radius:10px;overflow:hidden;font-size:13px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--grid)}
th{font-size:11.5px;color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.ts,.dim{color:var(--muted);font-size:12px;white-space:nowrap}
.empty{color:var(--muted);font-size:13px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:760px){.two{grid-template-columns:1fr}}
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--grid);
  color:var(--muted);font-size:12px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.scroll{overflow-x:auto}
.graph{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:12px 14px 6px}
.graph svg{width:100%%;height:auto;display:block}
.g-legend{display:flex;align-items:center;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--ink2);margin-bottom:6px}
.lg{display:inline-flex;align-items:center;gap:5px}
.lg i{width:9px;height:9px;border-radius:50%%;display:inline-block}
.g-meta{color:var(--muted);font-size:11.5px;margin-left:auto}
.edges line{stroke:var(--line);stroke-width:.7;opacity:.42}
.nd circle{stroke:var(--surface);stroke-width:1.4}
.nd:hover circle{stroke:var(--ink);stroke-width:1.8}
.k0 circle{fill:var(--g0)} .lg i.k0{background:var(--g0)}
.k1 circle{fill:var(--g1)} .lg i.k1{background:var(--g1)}
.k2 circle{fill:var(--g2)} .lg i.k2{background:var(--g2)}
.ko circle{fill:#898781} .lg i.ko{background:#898781}
.lbl{font-size:9.5px;fill:var(--ink2);paint-order:stroke;stroke:var(--surface);stroke-width:2.6px}
.divider{stroke:var(--grid);stroke-width:1}
.band-label{font-size:10.5px;fill:var(--muted)}
.iso circle{opacity:.55}
%(appearance_css)s
.cmds{grid-template-columns:repeat(auto-fit,minmax(min(100%%,320px),1fr))}
</style><script>%(appearance_js)s</script></head>
<body><div class="wrap">

<h1>brain — status</h1>
%(appearance_controls)s
<p class="sub">generated %(generated)s · this file exists only locally</p>

<div class="verdict" style="--vc:%(v_color)s">
  <span class="ic">%(v_icon)s</span>
  <div><div class="txt">%(verdict)s</div>
  <div class="st">worst status: %(v_word)s</div></div>
</div>

<div class="tiles">%(tiles)s</div>

<h2>Performance — ★approaches compared directly★</h2>
<p class="sub">Precision · reach · storage density · speed, compared as <b>raw values with units</b>.
⛔ Approaches are never compared by a total score — the measured scopes differ, so a sum does not hold.</p>
%(bench)s

<h2>The brain's 7 axes <span style="font-weight:400;color:var(--muted)">— for comparing against itself over time</span></h2>
<p class="sub">A table for seeing what rose after a change. <b>It does not compare totals against another approach.</b></p>
%(score)s

<h2>Commands — <span style="font-weight:400;color:var(--muted)">how to use them in a terminal</span></h2>
<p class="sub"><code>brain &lt;command&gt;</code> (from a cloned checkout, <code>./bin/brain &lt;command&gt;</code>).
For the full list, <code>brain help</code>.</p>
%(cmds)s

<h2>Diagnostics — the five paths by which memory distorts</h2>
<div class="cards">%(issues)s</div>

<h2>Metrics — not a fault, but worth knowing</h2>
<div class="cards">%(metrics)s</div>

<h2>To do (grounded in actual usage records)</h2>
%(todo)s

<h2>The connections between memories</h2>
%(graph)s

<h2>How the memories accumulated</h2>
%(bars)s

<h2>Usage</h2>
<div class="two">
  <div class="scroll"><table>
    <tr><th>recent recalls</th><th></th><th class="num">results</th></tr>%(recent)s
  </table></div>
  <div class="scroll"><table>
    <tr><th>most-recalled memories</th><th>kind</th><th class="num">times</th></tr>%(hits)s
  </table></div>
</div>

<footer>
  last indexed %(last_index)s · terms %(terms)s · DB %(db_mb)sMB<br>
  rebuild: <code>brain dashboard</code> · index: <code>brain index</code> ·
  suggest: <code>brain suggest</code>
</footer>

</div></body></html>
"""


def payload(db: sqlite3.Connection) -> dict:
    """★Pure JSON★ — it builds not one piece of the picture (plan §5b-a).

    ⛔ The old `collect()` returned tables ★pre-built as HTML★ (`score_html` · `bench_html`). Then the
    data layer has to change every time the screen does, and the same number is rendered in two places
    — the *"a rule in two places only gets fixed in one"* this repository has been burnt by repeatedly.
    The new dashboard eats this function alone.
    """
    from brain import rerank, scorecard as _sk
    d = collect(db)
    # ⛔ ★The screen must not disagree with `brain score`★ (2026-09-28). Opening the dashboard
    #    does not spend judge budget, so the remote axes come back empty — but a full measurement
    #    usually sits in the history, and leaving it out put 65.9 on screen against a measured 73.5.
    #    §scorecard.carry_remote fills them and ★says which ones and from when★, so the screen can
    #    show that rather than pass an older number off as this moment's.
    raw, carried, carried_at = _sk.carry_remote(_sk.collect(db, cheap=True))
    sc = _sk.compute(raw)
    axes = [{"key": r["key"], "score": r["score"], "raw": r["raw"],
             "weight": r["weight"], "target": _sk.TARGET.get(r["key"]),
             "carried": r["key"] in carried}
            for r in sc["axes"]]
    bd = rerank.budget(db)
    booked = sum(x["cost"] for x in rerank.scheduled_spend())
    # ⛔ ★The doughnut must sum to the limit★ (2026-09-02: the first version drew used 6 + reserved 300
    #    + my share 44 = 350, so the limit 500 and 150 did not line up — what was missing is ★the
    #    interactive reserve★. A pie chart that does not sum makes every slice untrustworthy.)
    left = bd.get("left")
    if left is None:                  # no daily wall we know of (§rerank.budget) — nothing to divide
        reserve, free = 0, 0
    else:
        reserve = min(rerank.RESERVE, max(0, left - booked))
        free = max(0, left - booked - reserve)
    cal = d.get("calibration") or {}
    return {
        "generated_at": d["generated_at"],
        "axes": axes,
        "total": sc["total"],
        "measured_weight": sc["measured_weight"],
    # ⛔ ★What can be translated is not a name but a key★ (2026-09-02): `sc["unmeasured"]` gives the
    #    human-readable axis names (in Korean). Put those on the screen and that one place stays Korean
    #    even after switching to English. So ★the key★ is put on the screen and translated there.
        "unmeasured": [a["key"] for a in axes if a["score"] is None],
    # ★Which axes are not from this moment, and when they were★ — the screen says so out loud.
        "carried": carried,
        "carried_at": (carried_at[:10] if carried_at else ""),
        "bench_rows": _sk.bench(db)["rows"],
        "budget": {"used": bd.get("used", 0), "reserved": booked,
                   "interactive": reserve, "free": free,
                   "limit": bd.get("limit", 0)},
        "sources": sorted(d["by_source"].items(), key=lambda kv: -kv[1]),
        "issues": d["issues"],
        "metrics": d["metrics"],
        "monthly": d["monthly"],
        "graph_html": d.get("graph") or "",
        "kpi": [("dash.corpus", d["corpus"]["docs"],
                 " · ".join("%s %d" % kv for kv in sorted(d["by_source"].items()))),
                ("dash.memories", d["by_source"].get("memory", 0),
                 " · ".join("%s %d" % kv for kv in sorted(d["by_kind"].items()))),
                ("dash.links", d["corpus"]["links"], "[[link]]"),
                ("dash.threshold", cal.get("threshold", 0),
                 "fire %.0f%%" % (cal.get("fire_rate_pct") or 0))],
        # ⛔ ★The old table is not put on the screen★ (2026-09-02). Folding it away seemed enough, but
        #    ① the user's instruction is "minimise text" and ② that table is Korean prose, so on the
        #    six-language screen ★600 characters stayed untranslated★. The same numbers are all already
        #    above as graphs, and the place for prose is `brain score --compare` (CLI).
        "details_html": "",
    }


def write(db: sqlite3.Connection, path: str = "", open_browser: bool = True,
          classic: bool = False) -> str:
    """Build the dashboard HTML and (optionally) open it in a browser.

    ⛔ Since 2026-09-02 the default is ★the graph screen★ (`brain/dashview.py`). The user's instruction:
    *"minimise the text and put everything into varied graphs · apply animation · six languages."*
    The old screen stays behind `classic=True` (`--classic`) — some days a table is what is needed, and
    there must be somewhere to fall back to when the new screen breaks.
    """
    if not classic:
        from brain import dashview
        html = dashview.render(payload(db))
        path = path or os.path.join(store.brain_home(), "dashboard.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        if open_browser:
            try:
                webbrowser.open("file://" + os.path.abspath(path))
            except Exception:                            # noqa: BLE001
                pass
        return path
    path = path or os.path.join(store.brain_home(), "dashboard-classic.html")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(collect(db)))
    if open_browser:
        try:
            webbrowser.open("file://" + path)
        except Exception:                                # noqa: BLE001
            pass
    return path
