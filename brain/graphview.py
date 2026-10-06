"""Connection graph — ★how memories link together, in one picture★.

## What it shows (and why this shape)

It answers two questions at once:
  ① **the brain's shape** — clustered or scattered, where the hubs are
  ② **an issue** — ★an unlinked memory becomes visible★ (set apart in the outer band)

Measured (2026-08-10): of 442 memories, **325 connected · 867 edges**, one giant component (317).
So it is not a hairball but a real structure. Median degree 4, max 45 (a reference note many others point to).

## Three design decisions

★① the layout is computed **at generation time**★ — no physics simulation runs in the browser.
   It appears instantly, never jitters, looks the same every time, and adds no dependency.

★② color is **three kinds + other**★ — a limit set by the validator (`validate_palette.js --pairs all`).
   A node-link graph can put any two nodes next to each other, so it must be verified all-pairs,
   and a fourth color **drops** dark-mode CVD ΔE to 1.9 (measured).
   So only three kinds get color and the rest fold into neutral gray — when color runs short,
   the rule is to fold into "other", not invent one.
   ⛔ ★Which three is this corpus's to say★ (2026-10-06) — the three most common kinds in the
   picture, or the ones a person names in config `graph_kinds`. It used to be the author's
   feedback/project/lesson: on anyone else's notes every node was gray and the legend named kinds
   they did not have.

★③ labels go **only on hubs**★ — put one on all 325 and the text overlaps itself into nothing readable.
   The rest appear on hover (SVG `<title>`, no javascript).
"""
from __future__ import annotations

import json as _json
import math
import random
import sqlite3
from typing import Dict, List, Tuple

# validator-passed combination (validate_palette.js --pairs all)
#   light: blue #2a78d6 · orange #eb6834 · aqua #1baf7a  → all checks PASS
#   dark : #3987e5 · #d95926 · #199e70                  → all checks PASS
# a fourth color failed dark mode no matter which slot it went in → "other" stays neutral gray.
def _L(key: str, fallback: str, **kw) -> str:
    """A translated UI word. ⛔ ★if missing, the source string★ — a translation gap must never break the picture.

    2026-09-02: this graph shipped on a 6-language dashboard, and the legend/meta/tooltip stayed
    Korean — a user caught it. ★A memory's 'name' is data, so it is never translated★ — what gets
    translated is only the human-facing words (rule/progress/lesson/other · link · main graph).
    """
    try:
        from brain import i18n
        v = i18n.t(key, **kw)
        return v if v != key else fallback
    except Exception:                                    # noqa: BLE001
        return fallback


PALETTE = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70")]   # (light, dark)
OTHER = ("other", "#898781", "#898781")


def colored_kinds(kinds: List[str], cfg: dict = None) -> List[str]:
    """★Which kinds get the colors★ — the ones a person named (`graph_kinds`), else the most common
    in the picture. Ties go by name, so the same corpus always draws the same picture."""
    if cfg is None:
        try:
            from brain import store
            cfg = store.load_config(tolerant=True)
        except Exception:                                # noqa: BLE001
            cfg = {}
    named = [k for k in (cfg.get("graph_kinds") or []) if isinstance(k, str) and k]
    if named:
        return named[:len(PALETTE)]
    count: Dict[str, int] = {}
    for k in kinds:
        if k:
            count[k] = count.get(k, 0) + 1
    return sorted(count, key=lambda k: (-count[k], k))[:len(PALETTE)]

W, H = 900, 560           # SVG coordinate space
ITER = 260                # layout iterations
LABEL_TOP = 7             # number of hubs that get a label
ISOLATE_BAND = 92         # height of the bottom band holding orphans


def fetch(db: sqlite3.Connection) -> Tuple[List[dict], List[Tuple[int, int]], List[dict]]:
    """(connected nodes, edges, orphan nodes). Resolves a link through either a name or an alias."""
    rows = db.execute(
        "SELECT s.name a, d.name b FROM links l "
        "JOIN docs s ON s.id=l.src_id "
        "JOIN name_map m ON m.key=l.dst_name "
        "JOIN docs d ON d.id=m.doc_id "
        "WHERE s.source='memory' AND d.source='memory' AND s.id!=d.id").fetchall()
    pairs = sorted({tuple(sorted((r["a"], r["b"]))) for r in rows})

    kinds = {r["name"]: (r["kind"] or "", r["description"] or r["title"] or "")
             for r in db.execute(
                 "SELECT name, kind, description, title FROM docs WHERE source='memory'")}

    connected = sorted({n for p in pairs for n in p})
    idx = {n: i for i, n in enumerate(connected)}
    deg: Dict[str, int] = {n: 0 for n in connected}
    for a, b in pairs:
        deg[a] += 1
        deg[b] += 1

    nodes = [{"name": n, "kind": kinds.get(n, ("", ""))[0],
              "desc": kinds.get(n, ("", ""))[1], "deg": deg[n]} for n in connected]
    edges = [(idx[a], idx[b]) for a, b in pairs]
    isolates = [{"name": n, "kind": k, "desc": d}
                for n, (k, d) in sorted(kinds.items()) if n not in idx]
    return nodes, edges, isolates


def layout(n: int, edges: List[Tuple[int, int]], deg: List[int]) -> List[Tuple[float, float]]:
    """Fruchterman-Reingold. Uses numpy if present, a pure-Python fallback if not.

    ★Zero dependency is a promise★ — the picture must not vanish just because numpy is missing.
    Without it, fewer iterations run (it's slower), and instead the starting points are placed on
    degree-based concentric rings so hubs land in the middle: less pretty, but **the structure still reads**.
    """
    if n == 0:
        return []
    rnd = random.Random(20260810)          # deterministic — the same picture every time
    try:
        import numpy as np
    except ImportError:
        return _layout_fallback(n, edges, deg, rnd)

    pos = np.array([[rnd.uniform(-1, 1), rnd.uniform(-1, 1)] for _ in range(n)])
    k = math.sqrt(1.0 / n) * 1.9
    src = np.array([e[0] for e in edges], dtype=int)
    dst = np.array([e[1] for e in edges], dtype=int)
    temp = 0.12
    for step in range(ITER):
        diff = pos[:, None, :] - pos[None, :, :]
        d2 = (diff ** 2).sum(-1) + 1e-9
        rep = (k * k) / d2
        np.fill_diagonal(rep, 0.0)
        disp = (diff * rep[:, :, None]).sum(1)

        if len(src):
            ed = pos[src] - pos[dst]
            dist = np.sqrt((ed ** 2).sum(-1)) + 1e-9
            att = (ed / dist[:, None]) * (dist ** 2 / k)[:, None]
            np.add.at(disp, src, -att)
            np.add.at(disp, dst, att)

        # pull in toward the center a little — otherwise small components drift away forever
        disp -= pos * 0.012
        norm = np.sqrt((disp ** 2).sum(-1))[:, None] + 1e-9
        pos += disp / norm * np.minimum(norm, temp)
        temp *= 0.985
    return [(float(x), float(y)) for x, y in pos]


def _layout_fallback(n, edges, deg, rnd):
    """Degree-based concentric rings — hubs inside, leaves outside. Eases overlap a little."""
    order = sorted(range(n), key=lambda i: -deg[i])
    pos = [(0.0, 0.0)] * n
    for rank, i in enumerate(order):
        r = 0.12 + 0.88 * (rank / max(1, n - 1)) ** 0.65
        a = rank * 2.399963            # golden angle — spreads evenly in a spiral
        pos[i] = (r * math.cos(a), r * math.sin(a))
    adj = [[] for _ in range(n)]
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    for _ in range(40):                # pull a little toward neighbours each pass
        for i in range(n):
            if not adj[i]:
                continue
            mx = sum(pos[j][0] for j in adj[i]) / len(adj[i])
            my = sum(pos[j][1] for j in adj[i]) / len(adj[i])
            pos[i] = (pos[i][0] * 0.94 + mx * 0.06, pos[i][1] * 0.94 + my * 0.06)
    return pos


def _color_key(kind: str, colored: List[str]) -> int:
    return colored.index(kind) if kind in colored else -1


def _components(n: int, edges: List[Tuple[int, int]]) -> List[List[int]]:
    adj: Dict[int, set] = {i: set() for i in range(n)}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    seen, out = set(), []
    for i in range(n):
        if i in seen:
            continue
        stack, comp = [i], []
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.append(x)
            stack.extend(adj[x] - seen)
        out.append(comp)
    out.sort(key=len, reverse=True)
    return out


def _pick_labels(order: List[int], P: List[Tuple[float, float]],
                 want: int, min_gap: float = 118.0) -> List[int]:
    """★labels are chosen only among ones far apart from each other★ — just taking the top 7 by degree
    put hubs too close together and the text overlapped solid (seen in a screenshot). Even a high
    degree gets skipped if it sits near an already-placed label: an unreadable label is worse than none."""
    chosen: List[int] = []
    for i in order:
        if len(chosen) >= want:
            break
        if all(math.dist(P[i], P[j]) >= min_gap for j in chosen):
            chosen.append(i)
    return chosen


def render(db: sqlite3.Connection) -> str:
    nodes, edges, isolates = fetch(db)
    if not nodes:
        return ('<p class="empty">No connected memories yet. '
                'Write <code>[[another-memory-name]]</code> in a memory and it appears here.</p>')

    # ★draws only the main graph★ — a small cluster (2~3 items) widened the bounding box and pushed
    # the giant component into a corner (measured: 317 items crammed into 1/6 of the screen). Small clusters go to the bottom band.
    comps = _components(len(nodes), edges)
    main = set(comps[0]) if comps else set()
    small = [i for c in comps[1:] for i in c]
    remap = {old: new for new, old in enumerate(sorted(main))}
    sub_nodes = [nodes[i] for i in sorted(main)]
    sub_edges = [(remap[a], remap[b]) for a, b in edges
                 if a in main and b in main]
    small_nodes = [nodes[i] for i in small]
    nodes, edges = sub_nodes, sub_edges
    colored = colored_kinds([x["kind"] for x in nodes + small_nodes + isolates])

    deg = [x["deg"] for x in nodes]
    pts = layout(len(nodes), edges, deg)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    spanx = (maxx - minx) or 1.0
    spany = (maxy - miny) or 1.0
    pad = 26
    plot_h = H - ISOLATE_BAND

    def sx(x):
        return pad + (x - minx) / spanx * (W - 2 * pad)

    def sy(y):
        return pad + (y - miny) / spany * (plot_h - 2 * pad)

    P = [(sx(x), sy(y)) for x, y in pts]

    # edges — thin and set back (show structure without covering the nodes)
    lines = "".join(
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>'
        % (P[a][0], P[a][1], P[b][0], P[b][1]) for a, b in edges)

    # nodes — size is degree (√-compressed so a hub doesn't swallow the screen)
    maxdeg = max(deg) or 1
    circles = []
    for i, nd in enumerate(nodes):
        r = 2.6 + math.sqrt(nd["deg"] / maxdeg) * 7.0
        ck = _color_key(nd["kind"], colored)
        cls = "k%d" % ck if ck >= 0 else "ko"
        circles.append(
            '<g class="nd %s" data-nd="%s"><circle cx="%.1f" cy="%.1f" r="%.1f"/>'
            '<title>%s&#10;%s · %s %d</title></g>'
            # ⛔ ★the tooltip must follow the language too★ — ship only a finished sentence from the
            #    server and switching language on screen leaves 676 tooltips in the old one. Ship the
            #    raw materials (name · kind · degree) instead and let JS reassemble it.
            % (cls, _esc("%s|%s|%d" % (nd["name"], nd["kind"] or "other", nd["deg"])),
               P[i][0], P[i][1], r,
               _esc(nd["name"]), _esc(_L("kind." + (nd["kind"] or "other"),
                                            nd["kind"] or "-")),
               _esc(_L("dash.links", "links")), nd["deg"]))

    # labels go only on hubs, and ★only ones far apart from each other★
    order = sorted(range(len(nodes)), key=lambda i: -nodes[i]["deg"])
    labels = "".join(
        '<text class="lbl" x="%.1f" y="%.1f" text-anchor="%s">%s</text>'
        % (P[i][0] + (10 if P[i][0] < W * 0.72 else -10), P[i][1] + 3.5,
           "start" if P[i][0] < W * 0.72 else "end",
           _esc(_short(nodes[i]["name"])))
        for i in _pick_labels(order, P, LABEL_TOP))

    # the bottom band — ★keeps unlinked items and small clusters separate★.
    # Mix them into the main graph and the fact that they are "floating" disappears from the picture.
    band = [(x, True) for x in small_nodes] + [(x, False) for x in isolates]
    iso_rows = []
    per = max(1, (W - 2 * pad) // 13)
    for j, (it, is_small) in enumerate(band):
        col, row = j % per, j // per
        if row > 4:
            break
        cx = pad + 6 + col * 13
        cy = plot_h + 32 + row * 12
        ck = _color_key(it["kind"], colored)
        cls = "k%d" % ck if ck >= 0 else "ko"
        note = (_L("graph.small_cluster", "small cluster") if is_small
                else _L("graph.no_links", "no links"))
        iso_rows.append(
            '<g class="nd iso %s"><circle cx="%.1f" cy="%.1f" r="2.6"/>'
            '<title>%s&#10;%s · %s</title></g>'
            % (cls, cx, cy, _esc(it["name"]), _esc(it["kind"] or "-"), note))

    legend = "".join(
        '<span class="lg"><i class="k%d"></i>%s</span>' % (i, _kind_label(kind))
        for i, kind in enumerate(colored)) + \
        ('<span class="lg"><i class="ko"></i>'
         '<span data-t="kind.other">%s</span></span>' % _esc(_L("kind.other", OTHER[0])))

    return _SVG_WRAP % {
        "w": W, "h": H, "legend": legend,
        "meta_v": _json.dumps({"n": len(nodes), "e": len(edges)}),
        "band_v": _json.dumps({"n": len(isolates)}),
        "meta": _esc(_L("graph.meta",
                        "main graph %d · links %d · size = link count · hover for the name"
                        % (len(nodes), len(edges)), n=len(nodes), e=len(edges))),
        "band": _esc(_L("graph.isolates",
                        "unlinked memories %d — reachable by search, not by path"
                        % len(isolates), n=len(isolates))),
        "lines": lines, "circles": "".join(circles), "labels": labels,
        "iso": "".join(iso_rows),
        "iso_y": plot_h + 14,
        "n_nodes": len(nodes), "n_edges": len(edges),
        "n_iso": len(isolates), "n_small": len(small_nodes),
        "iso_more": (" · showing only the first %d" % len(iso_rows)) if len(iso_rows) < len(band) else "",
    }


def _kind_label(kind: str) -> str:
    """A kind the catalogs translate switches language with the screen; any other kind is ★data★
    (the corpus's own word) and is shown as written."""
    try:
        from brain import i18n
        known = ("kind." + kind) in i18n.catalog("en")
    except Exception:                                    # noqa: BLE001
        known = False
    if known:
        return '<span data-t="kind.%s">%s</span>' % (_esc(kind), _esc(_L("kind." + kind, kind)))
    return '<span>%s</span>' % _esc(kind)


def _short(name: str, n: int = 26) -> str:
    return name if len(name) <= n else name[:n - 1] + "…"


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


_SVG_WRAP = """
<div class="graph">
<div class="g-legend">%(legend)s
  <span class="g-meta" data-t="graph.meta" data-v='%(meta_v)s'>%(meta)s</span>
</div>
<svg viewBox="0 0 %(w)d %(h)d" role="img"
     aria-label="memory connection graph: %(n_nodes)d nodes in the main graph, %(n_edges)d links, %(n_iso)d unlinked memories">
  <g class="edges">%(lines)s</g>
  <g class="nodes">%(circles)s</g>
  <g class="labels">%(labels)s</g>
  <line class="divider" x1="18" y1="%(iso_y).1f" x2="%(w)d" y2="%(iso_y).1f"
        transform="translate(-18,0)"/>
  <text class="band-label" x="20" y="%(iso_y).1f" dy="17"
        data-t="graph.isolates" data-v='%(band_v)s'>
    %(band)s
  </text>
  <g class="nodes iso-band">%(iso)s</g>
</svg>
</div>
"""


def css_vars(dark: bool = False) -> str:
    """Color variables the dashboard plants on :root. ★Dark is not an automatic inversion — it is a **chosen** value★ —
    the same eight colors re-stepped for a dark surface, confirmed with the validator."""
    idx = 1 if dark else 0
    return "".join("--g%d:%s;" % (i, c[idx]) for i, c in enumerate(PALETTE))
