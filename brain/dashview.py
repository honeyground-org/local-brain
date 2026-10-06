"""Dashboard renderer — graphs first, six languages, entrance animation only.

## Why this exists (user, 2026-09-02)

> *"The dashboard is bad. Show it as graphs, not text. Add real animation.
>  And let me switch language — es / en / fr / de / ko / ja."*

The old renderer wrote **51,988 visible characters, 4 tables, 49 rows and exactly
one SVG**. It was built to be *read*: every number carried its reasoning. The reasoning
then ate the screen. This renderer keeps the same numbers and draws them instead.

## ⛔ Rules this file keeps

- **Zero dependencies.** Every chart is hand-written SVG. Nothing is fetched from a CDN,
  so the page works with no network — a local private tool must not phone out.
- **Entrance animation only.** Values grow once, on first paint. ⛔ Nothing loops forever:
  a dashboard left open on a laptop must not burn the battery. `prefers-reduced-motion`
  is honoured, and there is a switch in the header as well.
- **Colour means state**, never decoration: green = target met, amber = short,
  red = broken, grey = ★not measured★.
- ⛔ **`not measured` is never drawn as zero.** Zero is a claim; absence is not.
  Unmeasured axes are hatched, not filled.
- **Six languages ship inside the file.** The switcher is client-side, so a single
  static HTML file serves everyone. The choice is remembered per browser.
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Sequence, Tuple

from brain import i18n, dashstyle

# ★Keys the client-side switcher needs.★ Declared as a prefix so `brain i18n --check`
# can see them as used (see i18n.export_prefix).
# ⛔ ★a prefix missing from this list freezes to the server language★ (2026-09-02: dropping `bench.`·`health.`
#    left the comparison-table row names on the Japanese screen in English — the catalog had the
#    translation, but the client never received it). ★Make a key and it has to go into this list too.★
UI_PREFIX = ("dash.", "axis.", "unit.", "bench.", "health.", "kind.", "graph.")

GREEN, AMBER, RED, GREY, INK = "var(--g)", "var(--a)", "var(--r)", "var(--grey)", "var(--ink)"


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def tone(score: Optional[float], target: float = 100.0) -> str:
    """Colour for a 0~100 axis score. ⛔ `None` is grey — it is not a bad score."""
    if score is None:
        return GREY
    if score >= 99.5:
        return GREEN
    return AMBER if score >= target * 0.6 else RED


def _t(key: str, **kw) -> str:
    return esc(i18n.t(key, **kw))


def tspan(key: str, **kw) -> str:
    """A translated node that ★keeps its values when the language changes★.

    ⛔ The first version emitted only `data-t="key"` and let the switcher write
    `textContent = catalogue[key]`. That printed the ★raw template★ — the header
    literally read *"{at} created"* after the first switch. The values live on the
    server side, so they must ride along: `data-v` carries them as JSON and the
    switcher substitutes `{name}` itself.
    """
    attr = ""
    if kw:
        attr = " data-v='%s'" % esc(json.dumps(kw, ensure_ascii=False)).replace("'", "&#39;")
    return '<span data-t="%s"%s>%s</span>' % (key, attr, _t(key, **kw))


def tspan_keys(key: str, slot: str, keys: Sequence[str], sep: str = ", ", **kw) -> str:
    """When the placeholder ★value itself is the thing being translated★.

    ⛔ 2026-09-02: putting the ★translated★ axis names into `{names}` of `not measured: {names}` meant
    that switching to German still left that spot as *"Precision, Stability"* — frozen at the server
    language. ★If the value is a key, ship the key★: the switcher translates it fresh each time.
    """
    attr = ""
    if kw:                                               # ⛔ plain values ride along too — the
        # switcher already substitutes `data-v` before `data-slot`, so one span can carry both
        # a translated list and a literal (a date). Without this the date freezes to the
        # server language's rendering or, worse, shows as the raw `{date}`.
        attr = " data-v='%s'" % esc(json.dumps(kw, ensure_ascii=False)).replace("'", "&#39;")
    filled = dict(kw)
    filled[slot] = sep.join(i18n.t(k) for k in keys)
    return ('<span data-t="%s" data-slot="%s" data-keys=\'%s\' data-sep="%s"%s>%s</span>'
            % (key, slot, esc(json.dumps(list(keys))).replace("'", "&#39;"), esc(sep), attr,
               _t(key, **filled)))


# ── chart: radar ───────────────────────────────────────────────────────────
def radar(axes: List[dict], size: int = 380) -> str:
    """Seven axes with the target ring drawn behind. ⛔ Unmeasured spokes are hatched.

    Why a radar and not a bar chart: the question this answers is *"where is the shape
    dented"*, and a closed polygon shows that in one look. The target is a second
    polygon, so "how far to go" needs no arithmetic.
    """
    n = len(axes)
    if not n:
        return ""
    cx = cy = size / 2.0
    # ⛔ a label attaches at radius 1.20, so it needs that much clearance —
    #    2026-09-02: "Autonomy" got cut down to "onomy" (clearance was 46).
    r = size / 2.0 - 64
    def pt(i, frac):
        a = -math.pi / 2 + i * 2 * math.pi / n
        return (cx + r * frac * math.cos(a), cy + r * frac * math.sin(a))

    out = ['<svg class="radar" viewBox="0 0 %d %d" role="img">' % (size, size)]
    out.append('<defs><pattern id="hatch" width="6" height="6" '
               'patternTransform="rotate(45)" patternUnits="userSpaceOnUse">'
               '<line x1="0" y="0" x2="0" y2="6" stroke="%s" stroke-width="2"/>'
               '</pattern></defs>' % GREY)
    for frac in (0.25, 0.5, 0.75, 1.0):                  # rings
        out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" '
                   'stroke="var(--line)" stroke-width="1"/>' % (cx, cy, r * frac))
    for i in range(n):                                   # spokes + labels
        x, y = pt(i, 1.0)
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="var(--line)"/>'
                   % (cx, cy, x, y))
        lx, ly = pt(i, 1.20)
        anchor = "middle" if abs(lx - cx) < 6 else ("start" if lx > cx else "end")
        a = axes[i]
        out.append('<text class="rl" x="%.1f" y="%.1f" text-anchor="%s" '
                   'data-t="axis.%s">%s</text>' % (lx, ly + 4, anchor, a["key"],
                                                   _t("axis." + a["key"])))
        val = "—" if a["score"] is None else "%.0f" % a["score"]
        out.append('<text class="rv" x="%.1f" y="%.1f" text-anchor="%s">%s</text>'
                   % (lx, ly + 18, anchor, val))

    tgt = " ".join("%.1f,%.1f" % pt(i, 1.0) for i in range(n))
    out.append('<polygon class="r-target" points="%s"/>' % tgt)

    # ⛔⛔ An unmeasured axis must inflate NOTHING. (2026-09-02: the first version
    #    held it at the outer ring so it would not read as zero — and that made the
    #    shape look ★better★ than the measurement supports. That is worse than zero.)
    #    So the polygon is drawn over ★measured axes only★ and breaks where data is
    #    missing; the missing spoke gets a hatched marker and no area.
    measured = [(i, a) for i, a in enumerate(axes) if a["score"] is not None]
    if len(measured) >= 2:
        pts = [pt(i, max(0.03, a["score"] / 100.0)) for i, a in measured]
        closed = all(a["score"] is not None for a in axes)
        tag = "polygon" if closed else "polyline"
        out.append('<%s class="r-now" points="%s"/>'
                   % (tag, " ".join("%.1f,%.1f" % q for q in pts)))
    for i, a in enumerate(axes):
        if a["score"] is not None:
            continue
        x, y = pt(i, 1.0)
        out.append('<g class="r-hole"><circle cx="%.1f" cy="%.1f" r="8" '
                   'fill="url(#hatch)" stroke="%s" stroke-width="1.5"/>'
                   '<title>%s</title></g>'
                   % (x, y, GREY, esc(i18n.t("dash.unmeasured", names=""))))
    # ★Three states, one scale★: filled = measured in this pass · hollow = a real measurement
    #   carried over from an earlier run (§scorecard.carry_remote) · hatched = never measured.
    #   ⛔ A carried value keeps its place in the shape — it ★is★ data — but it must not look
    #   like something just measured, or the screen quietly ages without saying so.
    for i, a in enumerate(axes):
        if a["score"] is None:
            continue
        x, y = pt(i, max(0.03, a["score"] / 100.0))
        col = tone(a["score"], 100.0)
        if a.get("carried"):
            out.append('<circle class="r-dot" cx="%.1f" cy="%.1f" r="4.5" fill="var(--card)" '
                       'stroke="%s" stroke-width="2"/>' % (x, y, col))
        else:
            out.append('<circle class="r-dot" cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>'
                       % (x, y, col))
    out.append("</svg>")
    return "".join(out)


# ── chart: score ring ──────────────────────────────────────────────────────
def ring(total: Optional[float], measured_pct: float, size: int = 190) -> str:
    """One number, drawn. The arc sweeps in; the digits count up."""
    r = size / 2.0 - 16
    c = 2 * math.pi * r
    frac = 0.0 if total is None else max(0.0, min(1.0, total / 100.0))
    col = tone(total, 100.0)
    return (
        '<svg class="ring" viewBox="0 0 %d %d" role="img">'
        '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="var(--track)" stroke-width="12"/>'
        '<circle class="ring-arc" cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" '
        'stroke-width="12" stroke-linecap="round" transform="rotate(-90 %.1f %.1f)" '
        'style="stroke-dasharray:%.2f %.2f;--to:%.2f"/>'
        '<text class="ring-num" x="%.1f" y="%.1f" text-anchor="middle" '
        'data-count="%s">%s</text>'
        '<text class="ring-cap" x="%.1f" y="%.1f" text-anchor="middle" '
        'data-t="dash.overall">%s</text>'
        "</svg>"
        % (size, size, size / 2.0, size / 2.0, r, size / 2.0, size / 2.0, r, col,
           size / 2.0, size / 2.0, c, c, c * (1 - frac),
           size / 2.0, size / 2.0 + 8, "" if total is None else "%.1f" % total,
           "—" if total is None else "%.1f" % total,
           size / 2.0, size / 2.0 + 34, _t("dash.overall")))


# ── chart: log bars ────────────────────────────────────────────────────────
def logbars(rows: Sequence[tuple], labels: Tuple[str, str, str]) -> str:
    """Three-way comparison on a ★log scale★.

    ⛔ Linear bars hide this data. "3 documents ↔ 195 documents" is the single most
    important number in the project and on a linear axis the 3 is invisible. Log scale
    is the honest choice here, and the axis says so.
    """
    out = ['<div class="bars">']
    any_log = False
    for row in rows:
        name, unit, a, b, cc, higher, note = row[:7]
        key = row[7] if len(row) > 7 else ""
        ukey = row[8] if len(row) > 8 else ""
        # ⛔ ★only a key can be translated★ — with none, it falls back to the source text (Korean).
        if key:
            name = '<span data-t="bench.%s">%s</span>' % (key, _t("bench." + key))
        else:
            name = esc(name)
        if ukey:
            unit = '<span data-t="unit.%s">%s</span>' % (ukey, _t("unit." + ukey))
        else:
            unit = esc(unit)
        # ⛔ ★a long note is dropped from the screen★ — the user's instruction is "minimize text," and
        #    carrying eight lines of prose into six languages is the exact opposite of that. The evidence
        #    is still spoken by `brain score --compare` (that's where prose belongs). The screen draws values.
        note = ""
        # ⛔ hand a label over as ★only a string★ and it freezes to the server language — carry the key too.
        vals = [(labels[0], a, "b0", "dash.compare.brain"),
                (labels[1], b, "b1", "dash.compare.grep"),
                (labels[2], cc, "b2", "dash.compare.alone")]
        real = [v for _, v, _, _ in vals if v is not None]
        top = max(real) if real else 1.0
        # ⛔ ★never use a log scale on every row★ (2026-09-02): applying it to the percent row made
        #    the `1.6%` bar look like 1/4 of the `42%` one — a ★26x★ difference, not 4x.
        #    A log scale is a tool for ★a range that doesn't fit one screen★ (3 ↔ 195, 20ms ↔ 4ms).
        #    So each row ★measures its actual range★ to decide, and only a row using log says so.
        lo = min(v for v in real if v > 0) if any(v > 0 for v in real) else 1.0
        # ⛔⛔ ★never use a log scale on a percentage, ever★ (2026-09-02, the second correction):
        #    picking by range alone made `42.1% ↔ 1.6%` (★26x★) look like 4x on a log scale.
        #    A percent is already normalized to 0~100, and a small value ★should look small★.
        #    A log scale is a tool for ★an unbounded count or duration★ (3 ↔ 195 docs, 675 ↔ 24 items).
        pct = unit.strip() in ("%", "percent")
        use_log = (not pct) and top / max(lo, 1e-9) >= 20.0
        any_log = any_log or use_log
        out.append('<div class="bg"><div class="bh"><span>%s</span>'
                   '<em>%s%s</em></div>' % (name, unit,
                                            "" if higher else
                                            " · <span data-t='dash.lower_better'>%s</span>"
                                            % _t("dash.lower_better")))
        for lab, v, cls, lk in vals:
            if v is None:
                out.append('<div class="br"><i data-t="%s">%s</i>'
                           '<span class="bna" data-t="dash.na">%s</span></div>'
                           % (lk, esc(lab), _t("dash.na")))
                continue
            if top <= 0:
                w = 0.0
            elif use_log:
                w = math.log10(1 + max(0.0, v)) / max(1e-9, math.log10(1 + top))
            else:
                w = max(0.0, v) / top
            best = (v == max(real)) if higher else (v == min(real))
            out.append('<div class="br"><i data-t="%s">%s</i>'
                       '<div class="bt"><div class="bf %s%s" style="--w:%.1f%%"></div></div>'
                       '<b>%s</b></div>'
                       % (lk, esc(lab), cls, " best" if best else "", w * 100,
                          _fmt(v)))
        if use_log:
            out.append('<div class="axis-note" data-t="dash.compare.log">%s</div>'
                       % _t("dash.compare.log"))
        if note:
            out.append('<div class="bn">%s</div>' % esc(note))
        out.append("</div>")
    out.append("</div>")
    return "".join(out)


def _fmt(v: float) -> str:
    if v == int(v) and abs(v) < 1e6:
        return format(int(v), ",")
    return "%.1f" % v


# ── chart: donut ───────────────────────────────────────────────────────────
def donut(parts: Sequence[Tuple[str, float, str]], centre: str, size: int = 170) -> str:
    """Budget as a ring. ⛔ Booked-by-cron is its own colour — "left" is not "yours"."""
    total = sum(max(0.0, p[1]) for p in parts) or 1.0
    r = size / 2.0 - 14
    c = 2 * math.pi * r
    out = ['<svg class="donut" viewBox="0 0 %d %d" role="img">' % (size, size)]
    off = 0.0
    for i, (name, val, col) in enumerate(parts):
        frac = max(0.0, val) / total
        out.append('<circle class="dseg" cx="%.1f" cy="%.1f" r="%.1f" fill="none" '
                   'stroke="%s" stroke-width="14" transform="rotate(-90 %.1f %.1f)" '
                   'style="stroke-dasharray:%.2f %.2f;stroke-dashoffset:%.2f;'
                   '--d:%.2fs"><title>%s</title></circle>'
                   % (size / 2.0, size / 2.0, r, col, size / 2.0, size / 2.0,
                      c * frac, c, -c * off, 0.15 * i, esc("%s %s" % (name, _fmt(val)))))
        off += frac
    out.append('<text class="dnum" x="%.1f" y="%.1f" text-anchor="middle">%s</text>'
               % (size / 2.0, size / 2.0 + 6, esc(centre)))
    out.append("</svg>")
    return "".join(out)


# ── chart: treemap ─────────────────────────────────────────────────────────
def treemap(items: Sequence[Tuple[str, int]], w: int = 560, h: int = 170) -> str:
    """Sources by size. A row of bars cannot show "which corpus dominates"; area can."""
    items = [(k, v) for k, v in items if v > 0]
    if not items:
        return ""
    items = sorted(items, key=lambda kv: -kv[1])
    total = float(sum(v for _, v in items))
    out = ['<svg class="tree" viewBox="0 0 %d %d" role="img">' % (w, h)]
    x, i = 0.0, 0
    for name, v in items:
        bw = max(2.0, w * v / total)
        col = "var(--soft)"
        out.append('<g class="tm" style="--d:%.2fs"><title>%s</title>'
                   '<rect x="%.1f" y="0" width="%.1f" height="%d" fill="%s" rx="6"/>'
                   % (0.06 * i, esc("%s — %s" % (name, format(v, ","))),
                      x, bw - 3, h, col))
        if bw > 74:
            out.append('<text class="tml" x="%.1f" y="26">%s</text>'
                       '<text class="tmv" x="%.1f" y="48">%s</text>'
                       % (x + 12, esc(name), x + 12, format(v, ",")))
        out.append("</g>")
        x += bw
        i += 1
    out.append("</svg>")
    return "".join(out)


# ── chart: growth bars ─────────────────────────────────────────────────────
def growth(monthly: Sequence[Tuple[str, int]], w: int = 560, h: int = 120) -> str:
    if not monthly:
        return ""
    top = max(v for _, v in monthly) or 1
    n = len(monthly)
    bw = w / float(n)
    out = ['<svg class="grow" viewBox="0 0 %d %d" role="img">' % (w, h + 22)]
    for i, (label, v) in enumerate(monthly):
        bh = (h - 6) * v / float(top)
        out.append('<rect class="gb" x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                   'rx="4" style="--d:%.2fs;--h:%.1f"><title>%s: %d</title></rect>'
                   % (i * bw + 3, h - bh, bw - 6, bh, 0.05 * i, bh, esc(label), v))
        out.append('<text class="gl" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                   % (i * bw + bw / 2.0, h + 16, esc(label)))
        if bh > 22:
            out.append('<text class="gv" x="%.1f" y="%.1f" text-anchor="middle">%d</text>'
                       % (i * bw + bw / 2.0, h - bh + 15, v))
    out.append("</svg>")
    return "".join(out)


# ── chart: traffic lights ──────────────────────────────────────────────────
def lights(issues: Sequence[dict], metrics: Sequence[dict]) -> str:
    """Keep severity, count and next action visible without relying on colour."""
    out = ['<div class="lights">']
    for kind, rows in (("issue", issues), ("metric", metrics)):
        for r in rows:
            n = r.get("count")
            state = r.get("status", "good") if kind == "issue" else "metric"
            if n is None:
                state = "unmeasured"
            icon, col = {"good": ("●", GREEN), "warning": ("▲", AMBER),
                         "serious": ("◆", RED), "critical": ("■", RED),
                         "metric": ("○", GREY), "unmeasured": ("—", GREY)}.get(
                             state, ("—", GREY))
            state_key = "dash.health.metric" if state == "metric" else "dash.state." + state
            # ⛔ ★a number with no name is not a metric★ (2026-09-02: the first version drew just
            #    "9 · 2 · 132" — no reason to look at a 9 if you can't tell what it's 9 of). The
            #    canonical key is `title`, and `meaning`/`action` follow as the tooltip explaining why.
            k = r.get("key") or ""
            # ⛔ translated if there's a key, source text if not. `health.<key>` is canonical.
            kk = {"dangling_links": "dangling", "duplicates": "dup",
                  "never_recalled": "never", "stale_evidence": "stale"}.get(k, k)
            tr = i18n.t("health." + kk) if kk else ""
            name = tr if (tr and tr != "health." + kk) else (
                r.get("title") or r.get("label") or k or "?")
            of = r.get("of")
            action_key = {"dangling": "health.check.dangling_links.action",
                          "dup": "health.check.duplicate_candidates.action"}.get(kk)
            action = ('<p class="next">%s</p>' % tspan(action_key)
                      if kind == "issue" and n and action_key else "")
            out.append('<article class="lt" style="--c:%s">'
                       '<div class="state"><span aria-hidden="true">%s</span> %s</div>'
                       '<b data-count="%s">%s</b>'
                       '<span%s>%s</span>%s%s</article>'
                       % (col, icon, tspan(state_key), "" if n is None else n,
                          "—" if n is None else format(n, ","),
                          ' data-t="health.%s"' % kk if kk else "", esc(name),
                          '<em>/ %s</em>' % format(of, ",") if of else "", action))
    out.append("</div>")
    return "".join(out)


def comparison_details(rows: Sequence[tuple]) -> str:
    """Raw numbers behind the charts, translated from keys rather than old HTML."""
    out = ['<details class="comparison"><summary>%s</summary><div class="table-scroll">'
           '<table><caption>%s</caption><thead><tr>' % (tspan("dash.raw"), tspan("dash.compare"))]
    for key in ("dash.measure", "dash.unit", "dash.compare.brain",
                "dash.compare.grep", "dash.compare.alone"):
        out.append('<th scope="col">%s</th>' % tspan(key))
    out.append('</tr></thead><tbody>')
    for row in rows:
        name, unit, a, b, c, higher = row[:6]
        name = tspan("bench." + row[7]) if len(row) > 7 and row[7] else esc(name)
        unit = tspan("unit." + row[8]) if len(row) > 8 and row[8] else esc(unit)
        out.append('<tr><th scope="row">%s%s</th><td>%s</td>' % (
            name, '<small>↓ %s</small>' % tspan("dash.lower_better") if not higher else "", unit))
        for value in (a, b, c):
            out.append('<td>%s</td>' % (tspan("dash.state.unmeasured") if value is None else esc(_fmt(value))))
        out.append('</tr>')
    out.append('</tbody></table></div></details>')
    return "".join(out)


# ── the page ───────────────────────────────────────────────────────────────
CSS = """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:26px 20px 60px}
header{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:22px}
h1{font-size:21px;margin:0;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:12px;margin-top:4px}
.spacer{flex:1}
.langs{display:flex;gap:4px;background:var(--card);padding:4px;border-radius:11px;
border:1px solid var(--line)}
.langs button{border:0;background:transparent;color:var(--dim);font:600 11px/1 inherit;
padding:7px 9px;border-radius:8px;cursor:pointer;letter-spacing:.04em}
.langs button[aria-pressed=true]{background:var(--ink);color:var(--card)}
.mtoggle{border:1px solid var(--line);background:var(--card);color:var(--dim);
border-radius:10px;padding:8px 11px;font:600 11px/1 inherit;cursor:pointer}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
margin:30px 0 12px;font-weight:700}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px}
.grid{display:grid;gap:14px}
.hero{grid-template-columns:210px 1fr;align-items:center}
.kpis{grid-template-columns:repeat(4,1fr);margin-top:14px}
.kpi b{display:block;font-size:26px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi span{color:var(--dim);font-size:11px}
@media(max-width:820px){.hero,.kpis{grid-template-columns:1fr}}

/* ── radar ─────────────────────────────────────────────────────────── */
.radar{width:100%;max-width:420px;height:auto;display:block;margin:0 auto}
.rl{font-size:11px;fill:var(--dim);font-weight:600}
.rv{font-size:13px;fill:var(--ink);font-weight:700;font-variant-numeric:tabular-nums}
.r-target{fill:none;stroke:var(--blue);stroke-width:1.5;stroke-dasharray:4 4;opacity:.5}
.r-now{fill:rgba(37,99,235,.16);stroke:var(--blue);stroke-width:2.5;
transform-origin:50% 50%;animation:grow .9s cubic-bezier(.22,1,.36,1) both}
.r-hole{fill:url(#hatch);stroke:var(--grey);stroke-width:1.5}
.r-dot{animation:pop .45s cubic-bezier(.22,1,.36,1) both;animation-delay:.55s}
@keyframes grow{from{transform:scale(.04);opacity:0}to{transform:scale(1);opacity:1}}
@keyframes pop{from{transform:scale(0)}to{transform:scale(1)}}

/* ── ring ──────────────────────────────────────────────────────────── */
.ring{width:190px;height:190px;display:block;margin:0 auto}
.ring-arc{animation:sweep 1.1s cubic-bezier(.22,1,.36,1) both}
@keyframes sweep{from{stroke-dashoffset:var(--from,600)}to{stroke-dashoffset:var(--to)}}
.ring-num{font-size:38px;font-weight:800;letter-spacing:-.03em;
font-variant-numeric:tabular-nums;fill:var(--ink)}
.ring-cap{font-size:10px;letter-spacing:.1em;text-transform:uppercase;fill:var(--dim);
font-weight:700}

/* ── bars ──────────────────────────────────────────────────────────── */
.bars{display:grid;gap:13px}
.bg{border-bottom:1px solid var(--line);padding-bottom:11px}
.bg:last-of-type{border:0}
.bh{display:flex;justify-content:space-between;font-weight:650;margin-bottom:7px}
.bh em{color:var(--dim);font-style:normal;font-weight:500;font-size:11px}
.br{display:grid;grid-template-columns:78px 1fr 74px;align-items:center;gap:9px;
margin:3px 0}
.br i{color:var(--dim);font-style:normal;font-size:11px;font-weight:600}
.br b{text-align:right;font-variant-numeric:tabular-nums;font-size:12px}
.bt{background:var(--track);border-radius:6px;height:15px;overflow:hidden}
.bf{height:100%;width:var(--w);border-radius:6px;transform-origin:left;
animation:slide .85s cubic-bezier(.22,1,.36,1) both}
.b0{background:var(--blue)}.b1{background:var(--series2)}.b2{background:var(--series3)}
.bf.best{box-shadow:inset 0 0 0 2px rgba(15,23,42,.16)}
.bna{color:var(--grey);font-size:11px}
.bn{color:var(--dim);font-size:11px;margin-top:5px}
.axis-note{color:var(--grey);font-size:10px;letter-spacing:.06em;text-transform:uppercase;
text-align:right;font-weight:700}
@keyframes slide{from{transform:scaleX(0)}to{transform:scaleX(1)}}

/* ── donut · treemap · growth ──────────────────────────────────────── */
.donut{width:170px;height:170px}
.dseg{animation:dash .9s cubic-bezier(.22,1,.36,1) both;animation-delay:var(--d)}
@keyframes dash{from{stroke-dasharray:0 9999}}
.dnum{font-size:20px;font-weight:800;fill:var(--ink);font-variant-numeric:tabular-nums}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--dim);
margin-top:8px}
.legend i{width:9px;height:9px;border-radius:3px;display:inline-block;margin-right:5px}
.tree,.grow{width:100%;height:auto;display:block}
.tm{animation:fade .6s ease both;animation-delay:var(--d)}
.tml{fill:var(--ink);font-size:12px;font-weight:700}
.tmv{fill:var(--dim);font-size:11px;font-variant-numeric:tabular-nums}
.gb{fill:var(--blue);transform-origin:50% 100%;
animation:rise .7s cubic-bezier(.22,1,.36,1) both;animation-delay:var(--d)}
.gl{font-size:9px;fill:var(--grey)}
.gv{font-size:11px;fill:var(--on-accent);font-weight:700;font-variant-numeric:tabular-nums}
@keyframes rise{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@keyframes fade{from{opacity:0;transform:translateY(8px)}to{opacity:1}}

/* ── lights ────────────────────────────────────────────────────────── */
.lights{display:grid;grid-template-columns:repeat(auto-fill,minmax(126px,1fr));gap:11px}
.lt{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--c);
border-radius:12px;padding:12px 13px;animation:fade .5s ease both}
.lt b{display:block;font-size:21px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.lt span{color:var(--dim);font-size:11px;display:block;line-height:1.35}
.lt em{color:var(--grey);font-size:10px;font-style:normal;font-variant-numeric:tabular-nums}
details{margin-top:10px}
details>summary{cursor:pointer;color:var(--dim);font-size:12px;font-weight:600}

/* ── memory graph ──────────────────────────────────────────────────────
   ⛔ these rules are ★paired★ with the classes `brain/graphview.py` emits (k0·k1·k2·ko·nd·lbl).
   2026-09-02: they lived only inside the old dashboard's CSS, so moving the picture to a new page
   made ★every point come out black★. A kind's color is exactly what the legend means, so losing the color loses the picture's meaning.
   ⇒ moving a picture means ★moving its rules along with it★. */
.graph svg{width:100%;height:auto;display:block}
.g-legend{display:flex;align-items:center;gap:14px;flex-wrap:wrap;font-size:11px;
color:var(--dim);margin-bottom:8px}
.lg{display:inline-flex;align-items:center;gap:5px}
.lg i{width:9px;height:9px;border-radius:50%;display:inline-block}
.g-meta{color:var(--grey);font-size:11px;margin-left:auto}
.edges line{stroke:var(--line);stroke-width:.7;opacity:.4}
.nd circle{stroke:var(--card);stroke-width:1.3;animation:pop .5s cubic-bezier(.22,1,.36,1) both}
.nd:hover circle{stroke:var(--ink);stroke-width:1.8}
.k0 circle{fill:var(--g0)} .lg i.k0{background:var(--g0)}
.k1 circle{fill:var(--g1)} .lg i.k1{background:var(--g1)}
.k2 circle{fill:var(--g2)} .lg i.k2{background:var(--g2)}
.ko circle{fill:#898781} .lg i.ko{background:#898781}
.lbl{font-size:9.5px;fill:var(--dim);paint-order:stroke;stroke:var(--card);stroke-width:2.6px}
.divider{stroke:var(--line);stroke-width:1}
.band-label{font-size:10.5px;fill:var(--grey)}
.iso circle{opacity:.5}
.motion-settled *{animation:none!important}
.reduced *{animation:none!important;transition:none!important}
@media(prefers-reduced-motion:reduce){*{animation:none!important}}
"""

RENEWAL_CSS = """
body{font-size:14px;line-height:1.6}
.wrap{max-width:1320px;padding:32px 32px 64px}
header{align-items:center;gap:12px;padding-bottom:24px;border-bottom:1px solid var(--line);margin-bottom:24px}
h1{font-size:26px;font-weight:750;letter-spacing:-.045em}
h2{font-size:15px;text-transform:none;letter-spacing:-.015em;color:var(--ink);margin:28px 0 12px}
.sub,.bn{font-size:12px;line-height:1.6}
.card{border-radius:14px;padding:24px;min-width:0}
.grid{gap:18px}.hero{grid-template-columns:minmax(240px,.7fr) minmax(0,1.3fr);align-items:stretch;margin-top:24px}
.hero>.card{display:flex;flex-direction:column;justify-content:center}
.hero .ring{width:210px;height:210px;max-width:100%}
.radar{max-width:350px}
.kpis{margin:20px 0;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.kpi{position:relative;overflow:hidden;padding:20px 22px}
.kpi b{font-size:34px;font-weight:650;letter-spacing:-.05em;line-height:1.3}
.kpi span{font-size:12px}.kpi .sub{overflow-wrap:anywhere}
.lights{grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:12px}
.lt{padding:16px 18px;border-left-width:3px;border-radius:12px;min-width:0}
.lt b{font-size:30px;font-weight:650;letter-spacing:-.04em;line-height:1.3;margin:12px 0 5px}
.lt>span{font-size:12px;color:var(--ink);font-weight:600}
.lt .state{display:flex;gap:6px;align-items:center;color:var(--c);font-size:11px;font-weight:650}
.lt .state span{display:inline;color:inherit;font-size:inherit}
.next{font-size:11px;color:var(--dim);line-height:1.5;margin:12px 0 0;padding-top:10px;border-top:1px solid var(--line)}
.next span{font-size:inherit;color:inherit}
.health-section h2{margin-top:0}
.status-summary{display:flex;align-items:center;gap:12px;padding:16px 20px;background:var(--card);
border:1px solid var(--line);border-left:4px solid var(--c);border-radius:12px;margin-bottom:18px}
.status-summary strong{font-size:18px;letter-spacing:-.02em}.status-summary .status-icon{color:var(--c)}
.status-summary .state-word{margin-left:auto;font-size:12px;color:var(--dim)}
.resources{grid-template-columns:minmax(240px,.7fr) minmax(0,1.3fr);margin-top:18px}
.bars{grid-template-columns:repeat(2,minmax(0,1fr));gap:20px 32px}
.bg{min-width:0}.bh{gap:8px;flex-wrap:wrap}.br{grid-template-columns:84px minmax(0,1fr) 68px}
.bt{height:12px}.bf{border-radius:4px}.br i{overflow-wrap:anywhere}.br b{font-size:11px}
.tree{min-height:140px}.tm rect{stroke:var(--line);stroke-width:1}
.legend{gap:10px;font-size:12px}.legend>span{display:inline-flex;align-items:center;gap:5px}
.legend i{flex:none}.graph{padding:8px 0}.g-legend{font-size:12px;gap:12px}
.graph svg{max-height:640px}.lg i{width:10px;height:10px}
.edges line{opacity:.55}.nd circle{transform-box:fill-box;transform-origin:center}
.nd:nth-child(3n) circle{animation-delay:.08s}.nd:nth-child(3n+1) circle{animation-delay:.16s}
.nd:hover circle{stroke-width:3}.lbl{font-size:10px}
.comparison{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-top:24px;overflow:hidden}
.comparison>summary{padding:18px 22px;font-size:13px;color:var(--ink)}
.table-scroll{overflow-x:auto;padding:0 22px 20px}
.comparison table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}
.comparison caption{text-align:left;color:var(--dim);padding:8px 0 16px}
.comparison th,.comparison td{padding:12px;border-bottom:1px solid var(--line);text-align:right}
.comparison th:first-child{text-align:left}.comparison th{font-weight:600}
.comparison thead{color:var(--dim)}.comparison small{display:block;font-weight:400;color:var(--dim)}
.langs button,.mtoggle{font-family:inherit;font-size:11px;min-height:34px;transition:background .15s,color .15s,transform .15s}
.langs button:hover,.mtoggle:hover{background:var(--soft);color:var(--ink);transform:translateY(-1px)}
.mtoggle[aria-pressed=true]{background:var(--soft);border-color:var(--dim);color:var(--ink)}
.mtoggle:disabled{cursor:default}.appearance{margin-left:4px}
/* Distinct one-shot entrances; there are no timers or perpetual loops. */
.kpi{animation:card-enter .6s cubic-bezier(.22,1,.36,1) both}
.kpi:nth-child(2){animation-delay:.06s}.kpi:nth-child(3){animation-delay:.12s}.kpi:nth-child(4){animation-delay:.18s}
.lt:nth-child(2){animation-delay:.05s}.lt:nth-child(3){animation-delay:.1s}.lt:nth-child(4){animation-delay:.15s}.lt:nth-child(5){animation-delay:.2s}
.bg:nth-child(2n) .bf{animation-delay:.12s}
.r-dot{transform-box:fill-box;transform-origin:center}
@keyframes card-enter{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:1000px){.bars{grid-template-columns:1fr}.wrap{padding:24px}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:640px){.wrap{padding:20px 14px 40px}header{gap:12px}header>.spacer{display:none}
header>div:first-child{width:100%}h1{font-size:24px}.hero,.resources{grid-template-columns:1fr}
.card{padding:18px}.kpis{gap:10px}.kpi b{font-size:28px}.kpi{padding:16px}
.lights{grid-template-columns:repeat(2,minmax(0,1fr))}.lt{padding:14px}.next{font-size:11px}
.status-summary{padding:14px;flex-wrap:wrap}.status-summary strong{font-size:16px}
.br{grid-template-columns:70px minmax(0,1fr) 60px;gap:6px}.appearance{margin-left:0}
.g-meta{margin-left:0;width:100%}.table-scroll{padding:0 12px 12px}}
@media(max-width:360px){.lights{grid-template-columns:1fr}.langs{gap:0}.langs button{padding:7px}}
.motion-settled *{animation:none!important}
.reduced *{animation:none!important;transition:none!important}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

JS = """
(function(){
 var T=%(TABLE)s, LANGS=%(LANGS)s, cur=%(CUR)s;
 function apply(code){
  cur=code; var d=T[code]||{}, en=T.en||{};
  document.documentElement.lang=code;
  var group=document.querySelector('.langs');
  if(group)group.setAttribute('aria-label',d['dash.lang']||en['dash.lang']);
  document.querySelectorAll('[data-t]').forEach(function(el){
   var k=el.getAttribute('data-t'); var v=d[k]||en[k]; if(v==null) return;
   // ⛔ Fill {placeholders} from data-v. Without this the raw template shows up.
   var raw=el.getAttribute('data-v');
   if(raw){ try{ var o=JSON.parse(raw);
     v=v.replace(/\{(\w+)\}/g,function(m,n){return (n in o)?o[n]:m;}); }catch(e){} }
   // ⛔ a spot where the value itself is a key — translate it fresh each time (so it never freezes to the server language)
   var kl=el.getAttribute('data-keys');
   if(kl){ try{ var ks=JSON.parse(kl), sp=el.getAttribute('data-sep')||', ',
     slot=el.getAttribute('data-slot')||'names';
     var joined=ks.map(function(k){return d[k]||en[k]||k;}).join(sp);
     v=v.replace(new RegExp('\\{'+slot+'\\}','g'), joined); }catch(e){} }
   el.textContent=v;});
  // ⛔ 676 node tooltips have to follow the language too — reassemble from raw materials (§graphview)
  document.querySelectorAll('.nd[data-nd]').forEach(function(g){
   var raw=g.getAttribute('data-nd')||'', p=raw.split('|');
   var t=g.querySelector('title'); if(!t||p.length<3) return;
   var kind=d['kind.'+p[1]]||en['kind.'+p[1]]||p[1];
   var links=d['dash.links']||en['dash.links']||'links';
   t.textContent=p[0]+String.fromCharCode(10)+kind+' \u00b7 '+links+' '+p[2];});
  document.querySelectorAll('.langs button').forEach(function(b){
   b.setAttribute('aria-pressed', String(b.dataset.lang===code));});
  try{localStorage.setItem('brain.dash.lang',code);}catch(e){}
 }
 // ⛔ Numbers count up ONCE. A dashboard that animates forever eats the battery.
 var frames=[];
 function finishCounts(){
  frames.forEach(cancelAnimationFrame);frames=[];
  document.querySelectorAll('[data-count]').forEach(function(el){
   if(el.getAttribute('data-count'))el.textContent=el.getAttribute('data-count');
  });
 }
 function count(){
  document.querySelectorAll('[data-count]').forEach(function(el){
   var to=parseFloat(el.getAttribute('data-count')); if(isNaN(to))return;
   var dec=(el.getAttribute('data-count').split('.')[1]||'').length, t0=null;
   function step(ts){ if(!t0)t0=ts; var p=Math.min(1,(ts-t0)/900);
    p=1-Math.pow(1-p,3); el.textContent=(to*p).toFixed(dec);
    if(p<1)frames.push(requestAnimationFrame(step));} frames.push(requestAnimationFrame(step));});
 }
 var saved=null; try{saved=localStorage.getItem('brain.dash.lang');}catch(e){}
 if(saved&&LANGS.indexOf(saved)>=0) apply(saved); else apply(cur);
 document.querySelectorAll('.langs button').forEach(function(b){
  b.addEventListener('click',function(){apply(b.dataset.lang);});});
 var mt=document.getElementById('mtoggle'), root=document.documentElement;
 var media=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)');
 var reduce=media&&media.matches;
 function syncMotion(){
  var off=root.classList.contains('reduced')||(media&&media.matches);
  if(mt){mt.setAttribute('aria-pressed',String(off));mt.disabled=!!(media&&media.matches);}
  if(off){root.classList.add('motion-settled');finishCounts();}
 }
 if(mt)mt.addEventListener('click',function(){
  var off=root.classList.toggle('reduced');
  try{localStorage.setItem('brain.dash.motion',off?'off':'on');}catch(e){}
  syncMotion();
 });
 if(media&&media.addEventListener)media.addEventListener('change',syncMotion);
 syncMotion();
 if(!root.classList.contains('reduced')&&!reduce)count();else finishCounts();
})();
"""


def _table_for_client() -> dict:
    """All six languages for the UI keys — shipped ★inside★ the file.

    ⛔ No fetch. A local private tool must work with the network off, and must not
    tell a CDN when someone opens their own dashboard.
    """
    out = {}
    for code in i18n.LANGS:
        cat = i18n.catalog(code)
        out[code] = {k: v for k, v in cat.items() if k.startswith(UI_PREFIX)}
    return out


def render(p: dict) -> str:
    """Assemble the page from a pure-JSON payload (§dashboard.payload)."""
    axes = p["axes"]
    kpi = p["kpi"]
    body = []
    body.append('<div class="wrap"><header>')
    body.append('<div><h1 data-t="dash.title">%s</h1>'
                '<div class="sub">%s</div></div>'
                % (_t("dash.title"), tspan("dash.generated", at=p["generated_at"])))
    body.append('<div class="spacer"></div>')
    body.append('<div class="langs" role="group" aria-label="%s">' % _t("dash.lang"))
    for code in i18n.LANGS:
        body.append('<button data-lang="%s" aria-pressed="%s">%s</button>'
                    % (code, "true" if code == i18n.lang() else "false", code.upper()))
    body.append('</div>')
    body.append('<button class="mtoggle" id="mtoggle" data-t="dash.motion_off">%s</button>'
                % _t("dash.motion_off"))
    body.append(dashstyle.controls())
    body.append('</header>')

    states = ("good", "warning", "serious", "critical")
    worst = max((r.get("status", "good") for r in p["issues"]),
                key=lambda value: states.index(value), default="good")
    n_bad = sum(r.get("status", "good") != "good" for r in p["issues"])
    icon, colour = {"good": ("●", GREEN), "warning": ("▲", AMBER),
                    "serious": ("◆", RED), "critical": ("■", RED)}[worst]
    verdict = (tspan("dash.health.clean") if not n_bad else
               "%d %s" % (n_bad, tspan("dash.health.issue")))
    body.append('<div class="status-summary" style="--c:%s">'
                '<span class="status-icon" aria-hidden="true">%s</span><strong>%s</strong>'
                '<span class="state-word">%s</span></div>'
                % (colour, icon, verdict, tspan("dash.state." + worst)))

    # ── kpi strip ─────────────────────────────────────────────────────────
    body.append('<div class="grid kpis">')
    for key, val, sub in kpi:
        # ⛔⛔ ★never mix a source name with a kind value★ (2026-09-02 mixed them): `memory`·`docs`
        #    ·`wiki` are ★corpus names★ and `feedback`·`lesson` are ★a memory's kind★.
        #    Running both through the same word table made the first card read "Other 676 · In progress 160"
        #    — ★a corpus name turned into a kind name.★ Applies only to the kind card.
        if key == "dash.memories":
            # ⛔ ★swap it in on the server and the screen can't change language★ — each kind has to
            #    go out as a span with a `data-t` so the switcher can fix it.
            # A kind the catalogs translate gets a key; any other kind is the corpus's own word, shown as written.
            known = i18n.catalog("en")
            outp = []
            for chunk in sub.split(" · "):
                bits = chunk.rsplit(" ", 1)
                tk = None
                if len(bits) == 2:
                    tk = "kind.none" if bits[0] == "(none)" else "kind." + bits[0]
                    tk = tk if tk in known else None
                if tk:
                    outp.append('<span data-t="%s">%s</span> %s'
                                % (tk, _t(tk), esc(bits[1])))
                else:
                    outp.append(esc(chunk))
            sub = " · ".join(outp)
            body.append('<div class="card kpi"><b data-count="%s">%s</b>'
                        '<span data-t="%s">%s</span><div class="sub">%s</div></div>'
                        % (val if str(val).replace(".", "").isdigit() else "", val,
                           key, _t(key), sub))
            continue
        body.append('<div class="card kpi"><b data-count="%s">%s</b>'
                    '<span data-t="%s">%s</span><div class="sub">%s</div></div>'
                    % (val if str(val).replace(".", "").isdigit() else "", val,
                       key, _t(key), esc(sub)))
    body.append('</div>')

    # Diagnostics lead; score and comparisons explain the state below.
    body.append('<section class="health-section"><h2 data-t="dash.health">%s</h2>%s</section>'
                % (_t("dash.health"), lights(p["issues"], p["metrics"])))

    # ── hero: one number + the shape ──────────────────────────────────────
    body.append('<div class="grid hero"><div class="card">%s'
                '<div class="sub" style="text-align:center">%s</div>'
                % (ring(p["total"], p["measured_weight"]),
                   tspan("dash.measured", pct="%.0f" % p["measured_weight"])))
    if p.get("unmeasured"):
        body.append('<div class="sub" style="text-align:center">%s</div>'
                    % tspan_keys("dash.unmeasured", "names",
                                 ["axis." + k for k in p["unmeasured"]]))
    if p.get("carried"):
        body.append('<div class="sub" style="text-align:center">%s</div>'
                    % tspan_keys("dash.carried", "names",
                                 ["axis." + k for k in p["carried"]],
                                 date=p.get("carried_at", "")))
    body.append('</div><div class="card">%s</div></div>' % radar(axes))

    # ── head to head ──────────────────────────────────────────────────────
    body.append('<h2 data-t="dash.compare">%s</h2><div class="card">%s'
                '<div class="bn" data-t="dash.compare.note">%s</div></div>'
                % (_t("dash.compare"),
                   logbars(p["bench_rows"], (_t("dash.compare.brain"),
                                             _t("dash.compare.grep"),
                                             _t("dash.compare.alone"))),
                   _t("dash.compare.note")))

    # ── budget + sources side by side ─────────────────────────────────────
    b = p["budget"]
    body.append('<div class="grid resources">')
    body.append('<div class="card"><h2 style="margin-top:0" data-t="dash.budget">%s</h2>%s'
                '<div class="legend">'
                '<span><i style="background:%s"></i><span data-t="dash.budget.used">%s</span></span>'
                '<span><i style="background:%s"></i><span data-t="dash.budget.reserved">%s</span></span>'
                '<span><i style="background:var(--series2)"></i>'
                '<span data-t="dash.budget.interactive">%s</span></span>'
                '<span><i style="background:%s"></i><span data-t="dash.budget.free">%s</span></span>'
                '</div></div>'
                % (_t("dash.budget"),
                   donut(((_t("dash.budget.used"), b["used"], "var(--blue)"),
                          (_t("dash.budget.reserved"), b["reserved"], AMBER),
                          (i18n.t("dash.budget.interactive"),
                           b.get("interactive", 0), "var(--series2)"),
                          (_t("dash.budget.free"), b["free"], "var(--track)")),
                         "%d" % b["free"]),
                   "var(--blue)", _t("dash.budget.used"),
                   AMBER, _t("dash.budget.reserved"),
                   _t("dash.budget.interactive"),
                   "var(--track)", _t("dash.budget.free")))
    body.append('<div class="card"><h2 style="margin-top:0" data-t="dash.sources">%s</h2>%s'
                '</div></div>' % (_t("dash.sources"), treemap(p["sources"])))

    # ── memory graph ──────────────────────────────────────────────────────
    if p.get("graph_html"):
        body.append('<h2 data-t="dash.graph">%s</h2><div class="card">%s'
                    '<div class="bn" data-t="dash.graph.note">%s</div></div>'
                    % (_t("dash.graph"), p["graph_html"], _t("dash.graph.note")))

    # ── growth ────────────────────────────────────────────────────────────
    body.append('<h2 data-t="dash.growth">%s</h2><div class="card">%s</div>'
                % (_t("dash.growth"), growth(p["monthly"])))

    body.append(comparison_details(p["bench_rows"]))

    # ── everything textual moves behind a disclosure ───────────────────────
    if p.get("details_html"):
        body.append('<details><summary data-t="dash.details">%s</summary>%s</details>'
                    % (_t("dash.details"), p["details_html"]))
    body.append('</div>')

    js = dashstyle.INIT + "\ndocument.addEventListener('DOMContentLoaded', function(){\n" + JS % {"TABLE": json.dumps(_table_for_client(), ensure_ascii=False),
               "LANGS": json.dumps(list(i18n.LANGS)),
               "CUR": json.dumps(i18n.lang())} + "\n});"
    return ("<!doctype html><html lang=\"%s\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style><script>%s</script></head><body>%s"
            "</body></html>" % (i18n.lang(), _t("dash.title"), CSS + dashstyle.css() + RENEWAL_CSS,
                                js, "".join(body)))
