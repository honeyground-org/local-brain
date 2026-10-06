"""Judge whether each item in MEMORY.md ★is still reachable by recall once deleted★.

Why this is needed
------------------
MEMORY.md grows every session. Reach the read limit and the tail is **silently** not read, so it gets
compressed, and every compression loses something. The accident this project was born from is
exactly that (205 of 434 fell outside the index).

Compression was dangerous until now because **there was no way to know what would be lost**.
With the brain there is: delete an item and if its content comes back through recall it is safe,
and if it does not, that is a loss. This module makes that judgement automatically.

★Two kinds must be separated★
- **Things never asked about** (⛔ rules and prohibitions) — a user does not ask "shall I set a daily cap?".
  It must jump out at the moment I am about to propose one on my own. Recall cannot take this place
  → **kept regardless of size.**
- **Things you can ask for** (progress · measurements · grounds) — if asked and reached, it need not be in the index.

So the judgement does not end at a recall rank. An item in a `directives` section, or one linking a
directive kind (§directive_kinds), is classified as "keep" even when recall puts it first.

⚠️ This check's bias (why a control group was added)
--------------------------------------
Using the item's text as the query makes it an **oracle** — the item is a summary of that memory, so the
words overlap. A user does not ask that way. So two rulers are measured:

    ceiling (full)  = the whole item as the query   → "if even this cannot reach it, it is certainly at risk"
    floor (short)   = only the item's heading phrase → closer to a real short query

The gap between the two is this check's uncertainty. Look at one alone and safety is overestimated.
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Dict, List, Optional

from . import calibrate, i18n, langdata, search, store

# ⛔ The index path is not nailed in — it is derived from config (store.index_file).
#    Left as a module constant, a personal path freezes at import time.
def MEMORY_INDEX() -> str:                # noqa: N802  (kept for existing callers)
    return store.index_file()

LINK_RE = re.compile(r"\[[^\]]*\]\(([A-Za-z0-9_./-]+)\.md\)")
# Heading phrase: the leading **bold text** of an item, or the label of [label](...)
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


class Item:
    """One item of MEMORY.md (a single bullet — it may span several lines)."""

    def __init__(self, section: str, line_no: int, role: str = ""):
        self.section = section
        self.role = role                  # §section_role — '' when the heading declares none
        self.line_no = line_no
        self.lines: List[str] = []
        self.golds: List[str] = []

    @property
    def raw(self) -> str:
        return "\n".join(self.lines)

    @property
    def chars(self) -> int:
        return len(self.raw)

    def query(self, full: bool) -> str:
        """The query text. With full=False, only the heading phrase (the floor control)."""
        t = self.raw
        # Links are removed from the query — a filename passed through finds itself
        t = LINK_RE.sub(" ", t)
        if full:
            return _plain(t)
        bolds = BOLD_RE.findall(t)
        return _plain(" ".join(bolds[:2])) if bolds else _plain(t)[:60]


def _plain(t: str) -> str:
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\*\*?", "", t)
    t = re.sub(r"[⛔⚠️✅❌🔴🟡🟢🔵★▶·|—]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# ★What a section is for★ — declared by the person, never read off their wording (2026-10-06)
#
# The audit reasons about three roles:
#   directives — standing instructions: kept regardless of recall, never asked about
#   signals    — "touch this topic and recall first": in the file to make you ask
#   progress   — current work, cleanup: reachable by asking, ★never★ kept as a directive
# ⛔ It used to find them by three of the author's own Korean headings and the English word
#    `recall`: the seed that ships is English, so on everyone else's index ★no section had a role★
#    and every item fell to "judge it by the kinds it links".
# ---------------------------------------------------------------------------
ROLES = ("directives", "signals", "progress")
_ROLE_MARK = re.compile(r"<!--\s*brain:\s*([A-Za-z]+)\s*-->")


def section_role(heading: str, cfg: Optional[dict] = None) -> str:
    """'directives' | 'signals' | 'progress' | '' — what this `## heading` is for.

    ① a marker in the heading itself — `## ⛔ Standing instructions <!-- brain: directives -->`.
       The seed ships them; an HTML comment does not show when the file is rendered.
    ② config `index_sections` — `{"directives": ["Standing instructions"], ...}`, each a piece of
       the heading text. For an index file whose headings the person would rather not touch.
    ③ neither → '' (an item there is judged by the kinds it links, §_is_directive).
    """
    m = _ROLE_MARK.search(heading)
    if m and m.group(1).lower() in ROLES:
        return m.group(1).lower()
    if cfg is None:
        cfg = store.load_config(tolerant=True)
    plain = _plain(_ROLE_MARK.sub(" ", heading))
    table = cfg.get("index_sections") or {}
    for role in ROLES:
        for piece in table.get(role) or []:
            if isinstance(piece, str) and piece and piece in plain:
                return role
    return ""


def directive_kinds(cfg: Optional[dict] = None) -> List[str]:
    """The kinds that mark a directive when an item's section has no role — config `directive_kinds`,
    else the host's own format (§hosts.Host.directive_kinds). ⛔ It used to be `feedback`, always:
    Claude Code's word, wrong for any other host's notes. An explicit `[]` means none."""
    if cfg is None:
        cfg = store.load_config(tolerant=True)
    named = cfg.get("directive_kinds")
    if isinstance(named, list):
        return [k for k in named if isinstance(k, str) and k]
    try:
        from brain import hosts
        return list(hosts.active().directive_kinds())
    except Exception:                                    # noqa: BLE001
        return []


def parse(path: str = "") -> List[Item]:
    """Split by bullet. An indented line is appended to the preceding item."""
    path = path or MEMORY_INDEX()
    cfg = store.load_config(tolerant=True)
    items: List[Item] = []
    section, role = "(preamble)", ""
    cur: Optional[Item] = None
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if line.startswith("## "):
                role = section_role(line[3:], cfg)
                section, cur = _plain(_ROLE_MARK.sub(" ", line[3:])), None
                continue
            if re.match(r"^[-*] ", line):
                cur = Item(section, i, role)
                items.append(cur)
            elif cur is None:
                continue
            elif not line.strip():
                cur = None                      # a blank line = end of item
                continue
            cur.lines.append(line)
    for it in items:
        seen: Dict[str, None] = {}
        for m in LINK_RE.finditer(it.raw):
            seen.setdefault(os.path.basename(m.group(1)), None)
        it.golds = list(seen)
    return items


def _kinds(db: sqlite3.Connection, names: List[str]) -> Dict[str, str]:
    """Memory name → kind. '(none)' when the file is not in the index."""
    out: Dict[str, str] = {}
    for nm in names:
        row = search.resolve(db, nm)          # gives id and name only
        if not row:
            out[nm] = "(not indexed)"
            continue
        k = db.execute("SELECT kind FROM docs WHERE id=?", (row["id"],)).fetchone()
        out[nm] = (k["kind"] if k and k["kind"] else "(none)")
    return out


def _thr(t: float) -> str:
    """A threshold for the screen — "—" when there is no ruler (§calibrate.NO_RULER), never "inf"."""
    return "—" if t == calibrate.NO_RULER else "%.2f" % t


def _rank(results, needle: str):
    for i, r in enumerate(results, 1):
        if needle in r["name"]:
            return i, r["score"]
    return 0, 0.0


def audit(path: str = "", k: int = 8) -> dict:
    path = path or MEMORY_INDEX()
    db = store.connect()
    thr = calibrate.threshold(db)
    items = parse(path)
    dkinds = directive_kinds()
    rows = []
    for it in items:
        if not it.golds:
            v = "directive-kept" if _is_directive(it.role, {}, dkinds) else "no-link"
            rows.append({"item": it, "verdict": v, "detail": []})
            continue
        kinds = _kinds(db, it.golds)
        detail = []
        for mode in (True, False):
            q = it.query(full=mode)
            res = search.recall(db, q, k=k, log=False) if q else []
            # ★Judge by the product's own rule, not by a proxy metric★ (2026-08-19) — it used to measure
            #   "in the top 3 and score ≥ threshold". The hook does not choose that way (`hook.select`
            #   looks at the absolute threshold and the relative rule together and attaches ★only 2★).
            #   What we really ask is "delete this line and does the hook actually attach the original memory in that situation".
            fired = res[:0]
            if q:
                from brain import hook
                res_hook = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
                fired = hook.select(res_hook, thr)
            attached = {r["name"] for r in fired}
            for g in it.golds:
                pos, sc = _rank(res, g)
                detail.append({"gold": g, "kind": kinds[g], "full": mode,
                               "rank": pos, "score": sc, "query": q,
                               "attached": any(g in nm for nm in attached)})
        rows.append({"item": it, "detail": detail,
                     "verdict": _verdict(detail, kinds, thr, it.role, dkinds)})
    return {"threshold": thr, "rows": rows,
            "n_docs": db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]}


def _is_directive(role: str, kinds: Dict[str, str], dkinds: List[str]) -> bool:
    """Is this item ★one of the things never asked about★.

    ⛔ The section's role is the primary axis. Looking at kind alone is wrong — if a "current work" item
    links one directive memory as a side note, the whole progress entry gets classified as a directive
    (measured: 6 progress items were caught that way). An item's nature is decided by where it sits.
    """
    if role == "directives":
        return True
    if role == "progress":
        return False
    # The rest (recall signals · no declared role) — a directive if a linked kind is a directive kind
    return any(v in dkinds for v in kinds.values())


def _verdict(detail, kinds: Dict[str, str], thr: float, role: str = "",
             dkinds: Optional[List[str]] = None) -> str:
    """The verdict for one item.

    ★A directive is kept regardless of its recall rank★ — because it is never asked about.
    """
    if _is_directive(role, kinds, dkinds if dkinds is not None else directive_kinds()):
        return "directive-kept"
    full = [d for d in detail if d["full"]]
    short = [d for d in detail if not d["full"]]

    def hit(ds, top=3):
        return [d for d in ds if 0 < d["rank"] <= top]

    if not hit(full) and not any(d.get("attached") for d in full):
        return "at-risk"                        # unreachable even by the oracle
    if any(d.get("attached") for d in short):
        return "safe"                           # ★the hook actually attaches it on a short query★
    if hit(short):
        return "if-asked"                       # reachable, but the hook does not attach it
    return "needs-terms"                        # reachable only through the full text


# ★Declared for the key scanner★ — the three tables below (`_VERDICT_KEY` · `_LAYER_KEY` · `_COND_LAYER_KEY`)
# hold keys as data and `t()` is called on the looked-up value, so `verify_i18n` cannot see them through
# `t("...")` (11 keys reported unused on 2026-09-08). Same declaration shape as dashview's UI_PREFIX.
I18N_PREFIX = ("ia.verdict.", "ia.layer.", "ia.cond.layer.")

_VERDICT_KEY = {"safe": "ia.verdict.safe", "if-asked": "ia.verdict.if_asked",
                "needs-terms": "ia.verdict.needs_terms", "at-risk": "ia.verdict.at_risk",
                "directive-kept": "ia.verdict.directive_kept", "no-link": "ia.verdict.no_link"}


def _ic(n: int, chars: int) -> str:
    return i18n.t("ia.n_items_chars", n=n, items_unit=i18n.t("unit.items"),
                  chars=chars, chars_unit=i18n.t("unit.chars"))


def render(res: dict) -> str:
    out = []
    thr, rows = res["threshold"], res["rows"]
    out.append("=" * 78)
    out.append(i18n.t("ia.title"))
    out.append(i18n.t("ia.corpus", n=res["n_docs"], unit=i18n.t("unit.docs"), thr=_thr(thr)))
    out.append("=" * 78)

    order = ["at-risk", "needs-terms", "if-asked", "safe", "directive-kept", "no-link"]
    mark = {"safe": "🟢", "if-asked": "🟡", "needs-terms": "🟠",
            "at-risk": "🔴", "directive-kept": "⛔", "no-link": "·"}
    tally = {v: [0, 0] for v in order}
    for r in rows:
        t = tally[r["verdict"]]
        t[0] += 1
        t[1] += r["item"].chars

    for v in order:
        sel = [r for r in rows if r["verdict"] == v]
        if not sel:
            continue
        out.append("")
        out.append(i18n.t("ia.group_header", mark=mark[v], label=i18n.t(_VERDICT_KEY[v]),
                          stats=_ic(tally[v][0], tally[v][1])))
        for r in sel:
            it = r["item"]
            best = ""
            hits = [d for d in r["detail"] if not d["full"] and 0 < d["rank"] <= 3]
            if hits:
                b = min(hits, key=lambda d: d["rank"])
                best = i18n.t("ia.hit_short", rank=b["rank"], score="%.1f" % b["score"])
            elif r["detail"]:
                fh = [d for d in r["detail"] if d["full"] and 0 < d["rank"] <= 3]
                if fh:
                    b = min(fh, key=lambda d: d["rank"])
                    best = i18n.t("ia.hit_full", rank=b["rank"], score="%.1f" % b["score"])
            out.append("   L%-4d %5d %s %s%s" % (it.line_no, it.chars, i18n.t("unit.chars"),
                                                _plain(it.raw)[:52], best))

    out.append("")
    out.append("-" * 78)
    total = sum(t[1] for t in tally.values())
    safe = tally["safe"][1] + tally["if-asked"][1]
    out.append(i18n.t("ia.total", stats=_ic(len(rows), total)))
    out.append(i18n.t("ia.recall_backs_up", chars=safe, unit=i18n.t("unit.chars"),
                      pct="%.0f" % (100.0 * safe / total if total else 0)))
    out.append(i18n.t("ia.directives_kept", chars=tally["directive-kept"][1], unit=i18n.t("unit.chars")))
    out.append(i18n.t("ia.at_risk_note", chars=tally["at-risk"][1], unit=i18n.t("unit.chars")))
    out.append("")
    out.append(i18n.t("ia.safe_meaning", safe=i18n.t("ia.verdict.safe"),
                      needs_terms=i18n.t("ia.verdict.needs_terms")))
    return "\n".join(out)


def main(path: str = "") -> int:
    path = path or MEMORY_INDEX()
    print(render(audit(path)))
    return 0


# ---------------------------------------------------------------------------
# ★A two-stage check★ — "is it reachable" and "is it in there" are different questions
#
# The audit() above measures whether the gold file **surfaces** through recall once the item is deleted.
# But a file can surface without holding the fact, and that is a loss. An index item carries PR numbers,
# measurements and resume points, and nothing guarantees those were moved into the topic file.
# Delete on a recall rank alone and it disappears ★silently★.
#
# So the checkable facts (unique tokens) are pulled out of the item and the gold file's body is read
# directly. Absent, it reports "write it into the original before deleting".
# ---------------------------------------------------------------------------

FACT_RES = [
    re.compile(r"\b[a-z][a-z-]{1,20}#\d{2,5}\b"),        # ads#1022 · admin#89 · rx#531
    re.compile(r"\b[\w.-]+\.(?:md|sh|py|ts|tsx|php|json|xml)\b"),
    re.compile(r"\b\d+(?:[/→~-]\d+)+\b"),                # 16/16 · 41→0 · 9:45~10:12
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                # a date
]
# Too common to be usable for fact-checking — present in every file
FACT_STOP = {"MEMORY.md", "CLAUDE.md", "AGENTS.md", "README.md", "config.json"}


def facts(item: "Item") -> List[str]:
    # ⛔ Strip the links first — the path in [label](file.md) matched the .md pattern and got flagged as
    #    "a fact absent from the original" (measured: 53/53 all red = a broken probe).
    #    When everything comes back the same, suspect the measurement rather than the code.
    t = LINK_RE.sub(" ", item.raw)
    out: Dict[str, None] = {}
    for rx in FACT_RES:
        for m in rx.finditer(t):
            s = m.group(0)
            if s not in FACT_STOP and len(s) > 3:
                out.setdefault(s, None)
    return list(out)


def _bodies(db: sqlite3.Connection, golds: List[str]) -> str:
    """The gold memories' bodies + names as one blob. The facts are looked for in here."""
    chunks = []
    for g in golds:
        row = search.resolve(db, g)
        if not row:
            continue
        r = db.execute("SELECT name, title, description, body FROM docs WHERE id=?",
                       (row["id"],)).fetchone()
        if r:
            chunks.append(" ".join(str(r[c] or "") for c in
                                   ("name", "title", "description", "body")))
    return "\n".join(chunks)


def fact_check(path: str = "") -> dict:
    """Is the item's fact actually written in the gold original."""
    path = path or MEMORY_INDEX()
    db = store.connect()
    rows = []
    for it in parse(path):
        if not it.golds:
            continue
        fs = facts(it)
        if not fs:
            continue
        hay = _bodies(db, it.golds)
        missing = [f for f in fs if f not in hay]
        rows.append({"item": it, "facts": fs, "missing": missing})
    return {"rows": rows}


def render_facts(res: dict) -> str:
    out = ["=" * 78,
           i18n.t("ia.facts.title"),
           i18n.t("ia.facts.subtitle"),
           "=" * 78]
    bad = [r for r in res["rows"] if r["missing"]]
    ok = [r for r in res["rows"] if not r["missing"]]
    for r in bad:
        it = r["item"]
        out.append("")
        out.append("🔴 L%-4d %s" % (it.line_no, _plain(it.raw)[:56]))
        out.append("   " + i18n.t("ia.facts.row", missing=len(r["missing"]), total=len(r["facts"]),
                                  list=", ".join(r["missing"][:12])))
        out.append("   " + i18n.t("ia.facts.original", list=", ".join(it.golds[:3])))
    out.append("")
    out.append("-" * 78)
    out.append(i18n.t("ia.facts.all_ok", n=len(ok), unit=i18n.t("unit.items")))
    out.append(i18n.t("ia.facts.needs_look", n=len(bad), unit=i18n.t("unit.items")))
    out.append("   " + i18n.t("ia.facts.caveat1"))
    out.append("      " + i18n.t("ia.facts.caveat2"))
    out.append("   " + i18n.t("ia.facts.caveat3"))
    out.append("      " + i18n.t("ia.facts.caveat4"))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Session-start size warning
#
# ⛔ This warning must not become a cause of false alarms itself. On 2026-08-18 it actually did:
# one session reported MEMORY.md as "22.4KB" but that was the **character** count, while the file was
# 35.6KB (one Hangul character = 3 bytes). Seeing two different numbers, it read the limit as imminent.
# → The warning ★states that it is a character count and gives the bytes alongside★.
#
# ⛔ ★The limit is the host's★ — a read limit exists only for a file the host loads by itself, and it
#    was measured on one host (§hosts.Host.index_read_limit). A host that loads no index gets no warning.
# ---------------------------------------------------------------------------


def size_notice(path: str = "") -> str:
    """⛔ It used to be English text outside the catalogs, naming the author's checkout path and
    explaining Hangul bytes to everyone. The bytes are stated only when they differ from the
    characters — that is the one case where two numbers can be confused."""
    from brain import hosts
    host = hosts.active()
    limit = host.index_read_limit()
    path = path or host.autoloaded_index(store.memory_dir())
    if not (limit and path):
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ""
    chars = len(text)
    if chars < limit["warn_chars"]:
        return ""
    nbytes = len(text.encode("utf-8"))
    return i18n.t("ia.size.notice", file=os.path.basename(path), chars=f"{chars:,}",
                  bytes_note=(i18n.t("ia.size.bytes", bytes=f"{nbytes:,}") if nbytes != chars else ""),
                  ok=f"{limit['ok_chars']:,}", when=limit["measured"])


# ---------------------------------------------------------------------------
# ★Three-layer classification★ — which layer can carry each directive
#
#   behaviour layer (PreToolUse) : caught by "I am about to call this tool" — the most precise place
#   topic layer (prompt hook)    : caught when the user mentions that topic — already running
#   core (MEMORY.md)             : neither can catch it — what must stay in the file
#
# ⛔ Behaviour cues are ★never invented★ — the judgement uses only words **actually written** in the
#    directive body (if a directive says "check before pushing", that is a rule bound to git push).
#    Measure with a mapping I imagined and I measure my imagination.
#
# ⚠️ Firing frequency was measured separately (175 whole transcripts · 43,965 tool calls):
#    Bash 65.7%, of which git push 3.55% · gh pr create 2.51% · terraform 0.93%.
#    The matcher selects by tool name only, so the hook runs on every Bash; but filtering the command
#    immediately in the shell, without invoking Python, makes the cost effectively 0.
# ---------------------------------------------------------------------------

# ⛔ No rule table is hand-written here (a defect removed 2026-08-19)
#
# There used to be a table called `ACTION_SIGNALS` — 10 layer names and a mapping of "if the directive
# body holds this word it binds to that tool". That table was written on a day the behaviour layer ★did
# not exist★ (2026-08-18), and the next day the behaviour layer really appeared as RULES in `brain/guard.py`. The table was never updated.
#
# So the judge lied in both directions (measured 2026-08-19):
#   false green  it answered "the behaviour layer can take it" using ★8 rule names that did not exist★
#                (`run-tests` · `edit-brain` · `edit-i18n` · `agent-tool` and others).
#   false red    a seeder item's original memory really is in the actual rule `seeder-edit`, but the body
#                held none of the table's words, so it was classified as "topic layer".
#
# ★A judge must not transcribe the product's rules; it must read the same material★ — it reads
# `guard.active_rules()`, the list the hook itself fires from (learned and hand-written alike). The question sharpens too: not "could it take it" (imagination) but ★is it
# taking it now★ — is this item's original memory actually in some rule's memories.


def _rules_carrying(golds: List[str]) -> List[dict]:
    """The behaviour-layer rules ★actually carrying★ this item's original memory. Zero imagination."""
    from brain import guard
    out = []
    for r in guard.active_rules():
        mem = set(r.get("memories") or [])
        if any(g in mem for g in golds):
            out.append(r)
    return out


def _all_rule_memories() -> set:
    from brain import guard
    out = set()
    for r in guard.active_rules():
        out |= set(r.get("memories") or [])
    return out


def classify_directives(path: str = "") -> dict:
    """Which layer ★is carrying★ each item. The behaviour verdict reads guard.active_rules()."""
    path = path or MEMORY_INDEX()
    db = store.connect()
    thr = calibrate.threshold(db)
    rows = []
    for it in parse(path):
        if it.role not in ("directives", "signals"):
            continue
        rules = _rules_carrying(it.golds) if it.golds else []
        # Topic layer: does the hook ★actually attach★ on the heading phrase alone (not a rank proxy)
        topic, topic_score = False, 0.0
        if it.golds:
            q = it.query(full=False)
            if q:
                from brain import hook
                res = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
                fired = hook.select(res, thr)
                for r in fired:
                    if any(g in r["name"] for g in it.golds):
                        topic, topic_score = True, r["score"]
                        break
        if rules:
            layer = "behaviour"
        elif topic:
            layer = "topic"
        elif not it.golds:
            layer = "undecidable"                  # no original link — we do not know what to measure
        else:
            layer = "core"
        rows.append({"item": it, "rules": rules, "topic": topic,
                     "topic_score": topic_score, "layer": layer,
                     "orphan_golds": [g for g in it.golds
                                      if g not in _all_rule_memories()]})
    return {"rows": rows, "threshold": thr}


_LAYER_KEY = {"behaviour": "ia.layer.behaviour", "topic": "ia.layer.topic",
              "core": "ia.layer.core", "undecidable": "ia.layer.undecidable"}


def render_layers(res: dict) -> str:
    from brain import guard
    rows = res["rows"]
    out = ["=" * 78,
           i18n.t("ia.layers.title"),
           i18n.t("ia.layers.subtitle", thr=_thr(res["threshold"]), n=len(guard.active_rules())),
           "=" * 78]
    marks = (("behaviour", "🔧"), ("topic", "💬"), ("core", "📌"), ("undecidable", "·"))
    for layer, mark in marks:
        sel = [r for r in rows if r["layer"] == layer]
        if not sel:
            continue
        chars = sum(r["item"].chars for r in sel)
        out.append("")
        out.append(i18n.t("ia.group_header", mark=mark, label=i18n.t(_LAYER_KEY[layer]),
                          stats=_ic(len(sel), chars)))
        for r in sel:
            it = r["item"]
            if r["rules"]:
                tag = "  ← " + ", ".join(
                    "%s(%s)" % (x["id"], "|".join(x["tools"])) for x in r["rules"][:2])
            elif r["topic"]:
                tag = "  " + i18n.t("ia.layers.tag_topic", score="%.1f" % r["topic_score"])
            elif r["layer"] == "undecidable":
                tag = "  " + i18n.t("ia.layers.tag_no_link")
            else:
                tag = "  " + i18n.t("ia.layers.tag_no_rule")
            out.append("   L%-4d %4d %s %s%s"
                       % (it.line_no, it.chars, i18n.t("unit.chars"), _plain(it.raw)[:42], tag))

    out.append("")
    out.append("-" * 78)
    tot = sum(r["item"].chars for r in rows)
    core = sum(r["item"].chars for r in rows
               if r["layer"] in ("core", "undecidable"))
    out.append(i18n.t("ia.layers.total", stats=_ic(len(rows), tot), core_chars=core,
                      unit=i18n.t("unit.chars"), pct="%.0f" % (100.0 * core / tot if tot else 0)))
    orphan = [(r["item"].line_no, g) for r in rows if r["layer"] == "core"
              for g in r["orphan_golds"]]
    if orphan:
        out.append("")
        out.append(i18n.t("ia.layers.orphan_header"))
        for ln, g in orphan[:20]:
            out.append("   L%-4d %s" % (ln, g))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# ★The classification axis was fixed (2026-08-18, the first attempt was wrong in both directions)★
#
# The first attempt looked at two things and both were wrong:
#   ① behaviour cue = a word present in the directive body → it caught incidental words (the directive
#      "the unit of a project" held "test" and "screen", so it was classified as run-tests and edit-ui)
#   ② topic layer = the directive's ★heading phrase★ as the query → put the title in and of course it
#      surfaces (an oracle). At the real moment of danger the user does not say those words.
#
# ★The real axis is the conditional clause★ — objectively readable from the text, and identical to the hook's condition of existence:
#   "when X → do Y"   = conditional → there is a trigger, so a hook can take it
#   "always do Y"     = unconditional → no trigger → ★no hook can catch it★ → it stays in the file
#
# Splitting the conditional ones again (is the condition an action or a topic) is ★for a human to read and judge★.
# 30 is a readable amount, and automatic matching just got it wrong.
# ---------------------------------------------------------------------------

# ⛔ The markers are ★corpus data, not prose★ — they live in `langdata` with the
#    other language-bound tables (translating them would silently stop matching).
#    One table per language the corpus is written in (§calibrate.corpus_languages).
_CODE_SPAN = re.compile(r"`[^`\n]*`")


def _matchers(table: Dict[str, List[str]], langs: List[str]) -> List[tuple]:
    """(marker, compiled) for the corpus's languages. Spaced languages match whole words, any case."""
    out = []
    for code in langs:
        for m in table.get(code, []):
            if code in langdata.SPACED:
                pat = ((r"(?<!\w)" if m[:1].isalnum() else "") + re.escape(m)
                       + (r"(?!\w)" if m[-1:].isalnum() else ""))
                out.append((m, re.compile(pat, re.I)))
            else:
                out.append((m, re.compile(re.escape(m))))
    return out


def classify_by_condition(path: str = "") -> dict:
    path = path or MEMORY_INDEX()
    langs = calibrate.corpus_languages(store.connect())
    cond_m = _matchers(langdata.CONDITION_MARKERS, langs)
    uncond_m = _matchers(langdata.UNCONDITIONAL_MARKERS, langs)
    rows = []
    for it in parse(path):
        if it.role not in ("directives", "signals"):
            continue
        # ⛔ Code is not prose — a pasted `if` or a flag named `--before` is no condition.
        body = _CODE_SPAN.sub(" ", it.raw)
        conds = [m for m, rx in cond_m if rx.search(body)]
        uncond = [m for m, rx in uncond_m if rx.search(body)]
        if conds:
            layer = "conditional (a hook can take it)"
        elif uncond:
            layer = "unconditional (stays in the file)"
        else:
            layer = "undecidable (a human must read it)"
        rows.append({"item": it, "conds": conds, "uncond": uncond, "layer": layer})
    return {"rows": rows, "languages": langs}


_COND_LAYER_KEY = {"conditional (a hook can take it)": "ia.cond.layer.conditional",
                   "unconditional (stays in the file)": "ia.cond.layer.unconditional",
                   "undecidable (a human must read it)": "ia.cond.layer.undecidable"}


def render_conditions(res: dict) -> str:
    rows = res["rows"]
    out = ["=" * 78,
           i18n.t("ia.cond.title"),
           i18n.t("ia.cond.languages", langs=", ".join(res.get("languages") or [])),
           "=" * 78]
    order = ["conditional (a hook can take it)", "unconditional (stays in the file)",
             "undecidable (a human must read it)"]
    for layer in order:
        sel = [r for r in rows if r["layer"] == layer]
        chars = sum(r["item"].chars for r in sel)
        out.append("")
        out.append(i18n.t("ia.cond.group_header", label=i18n.t(_COND_LAYER_KEY[layer]),
                          stats=_ic(len(sel), chars)))
        for r in sel:
            it = r["item"]
            why = (i18n.t("ia.cond.why_condition", list=", ".join(r["conds"][:3])) if r["conds"] else
                   i18n.t("ia.cond.why_unconditional", list=", ".join(r["uncond"][:3])) if r["uncond"] else "")
            out.append("   L%-4d %4d %s %-42s %s"
                       % (it.line_no, it.chars, i18n.t("unit.chars"), _plain(it.raw)[:42], why))
    out.append("")
    out.append("-" * 78)
    tot = sum(r["item"].chars for r in rows)
    for layer in order:
        c = sum(r["item"].chars for r in rows if r["layer"] == layer)
        out.append("  %-28s %5d %s (%2.0f%%)" % (i18n.t(_COND_LAYER_KEY[layer]), c,
                                                  i18n.t("unit.chars"), 100.0*c/tot if tot else 0))
    out.append(i18n.t("ia.cond.footer"))
    return "\n".join(out)
