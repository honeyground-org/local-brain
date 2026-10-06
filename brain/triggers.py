"""Declared trigger phrases — ★lookup, not a score★.

The user's instruction (2026-08-19): *"Don't give it a score threshold — there needs to be a
clearer query and a way to check it."*

## Why

Calling a thread's own name to continue an in-progress thread is **a lookup, not a search**. But
the hook only fires when a BM25 score clears a threshold, so a phrase with the exact same meaning goes
silently quiet ★when the file is long★ (length normalization) or ★when a common word dilutes it★ (a word like
"continue" alone inflates the denominator). Measured on the author's notes (2026-08-19 · threshold 9.46), three
"<thread name> continue" prompts:

    right answer ranked 1st, score 7.41                  → silent
    right answer ranked 1st, score 7.05                  → silent
    right answer 8.81 (a 19,736-character note)          → silent

Declare a phrase in a file's frontmatter as `triggers: ["..."]` and the hook attaches it whenever it
matches ★exactly★ — regardless of score or threshold. So ★MEMORY.md no longer needs to list active threads★:
a thread comes on its own the moment its own name is called.

## How to check (a declaration can be verified)

`brain triggers` measures three things — this is the "way to check it" the user asked for:

  ① uniqueness   if two documents declare the same phrase, which one wins is unknown → fail
  ② resolution    does looking up that phrase surface its own document? if not → fail
  ③ shadowing     if one phrase contains another, the shorter one always wins → fail
  ④ real usage    how many times did it actually fire in real prompts → ★reported only★

⛔ ④ was tried as a verdict and rejected by measurement — the assumption that "firing often means it's
   ordinary conversation" was wrong. Capping it at 2 and measuring: two thread phrases fired 4 times each,
   and every one of those 4 was a legitimate use that ★actually called that thread★. Legitimate
   summoning and noise can't be told apart by frequency. So frequency is shown as evidence, and failure is
   raised only by ★structural conditions★ (length · word count · shadowing · uniqueness · resolution).
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
from typing import Dict, List, Tuple

from . import i18n

_WS = re.compile(r"\s+")

MIN_CHARS = 6          # a phrase shorter than this gets hit by accident
MIN_WORDS = 2          # a one-word phrase gets hit in other contexts (a structural condition)


def norm(text: str) -> str:
    """Normalization for comparison — whitespace collapsed to one, case-insensitive. ⛔ Nothing more:
    stripping particles or endings would stop being 'matches exactly' and make this un-checkable."""
    return _WS.sub(" ", (text or "").strip()).casefold()


_DECL = re.compile(r"^triggers:\s*(\[.*\])\s*$", re.M)


def _frontmatter(fh) -> str:
    """The frontmatter block, ★however long it is★ — not a fixed number of bytes.

    ## Why this is not `read(2000)` any more (measured 2026-09-29)

    It was, and the blind spot cost exactly what this module exists to prevent. A memory that is
    ★worked on for weeks★ grows a long `description:`, which pushes its `triggers:` line past 2,000
    bytes — one long project note sat at ★3,583★. Two things then read past it:

      · `brain triggers` (the audit) — reported ✅ while four declared phrases resolved to nothing
      · `store.heal_triggers()` (the self-healing pass) — it asks this function what the files
        declare, so a declaration it cannot see is never "missing", and ★never repaired★

    The healer exists precisely because an MCP server holding old code writes no trigger rows. With
    this cap, the repair could not reach the files most likely to need it — the long-lived ones,
    which are long ★because★ they are the threads people keep returning to by name.

    ⛔ The indexer never had this limit (`store.read_doc` parses the whole block), so the checker and
    the indexer were reading different amounts of the same file. ★An instrument that sees less than
    the thing it measures reports health it cannot know.★

    Bounded by the closing `---`, and by a generous ceiling so a file with no frontmatter at all is
    not read whole.
    """
    head = fh.read(200_000)
    if not head.startswith("---"):
        return head[:2000]
    end = head.find("\n---", 3)
    return head if end < 0 else head[:end]


def declared_in_files() -> Dict[str, str]:
    """★what is declared in the files★ {normalized phrase: memory name} — never looks at the DB.

    ⛔ Why this is needed (measured 2026-08-19): the audit was reading ★only the table★. So an empty
    table reports "0 phrases · ✅ all passed" — ★an empty pass★ that means this check cannot see
    the exact incident it exists to catch. That day another session tried reflecting a declaration
    via MCP `reindex` (both incremental and full), but no row appeared in the `triggers` table (a
    long-running MCP server was holding stale code) — it only appeared via `./bin/brain index --full`.
    Skip the check and the next session would have typed the phrase and gotten nothing at all.
    """
    out: Dict[str, str] = {}
    from brain import store
    base = store.memory_dir()
    if not base:
        return out
    for path in glob.glob(os.path.join(base, "*.md")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                head = _frontmatter(fh)
        except OSError:
            continue
        m = _DECL.search(head)
        if not m:
            continue
        for t in re.findall(r'"([^"]+)"', m.group(1)):
            if len(t) >= 4:
                out[norm(t)] = os.path.basename(path)[:-3]
    return out


def declared(db: sqlite3.Connection) -> List[Tuple[str, int, str]]:
    """[(phrase, doc_id, doc name)] — everything declared."""
    try:
        rows = db.execute(
            "SELECT t.phrase, t.doc_id, d.name FROM triggers t "
            "JOIN docs d ON d.id=t.doc_id ORDER BY t.phrase").fetchall()
    except sqlite3.Error:
        return []
    return [(r["phrase"], r["doc_id"], r["name"]) for r in rows]


def match(db: sqlite3.Connection, prompt: str) -> List[Tuple[str, int, str]]:
    """Does the prompt contain a declared phrase — ★exact substring match★ (no score involved).

    ⛔ When several match, ★the longer phrase wins★ — a more specific declaration takes priority.
    """
    p = norm(prompt)
    if not p:
        return []
    hits = [(ph, did, nm) for ph, did, nm in declared(db) if ph and ph in p]
    hits.sort(key=lambda x: -len(x[0]))
    return hits


def _real_prompts(limit: int = 4000) -> List[str]:
    """Real user turns from the actual conversation history — the control group for ③ over-firing.
    ⛔ Measuring with invented sentences measures my own imagination.
    ⛔ ★Every host on this machine, each reading its own history★ (§hosts.Host.prompts) — this used to
       glob Claude Code's folder with its own copy of the "user turn" rule, and a Codex user's prompts
       never reached it."""
    from brain import hosts
    out: List[str] = []
    for h in hosts.detected() or [hosts.active()]:
        for p in h.prompts(limit=limit, recent_files=None):
            if "system-reminder" not in p:
                out.append(norm(p[:600]))
    return out[:limit]


def audit(db: sqlite3.Connection) -> dict:
    decl = declared(db)
    by_phrase: Dict[str, List[str]] = {}
    for ph, _did, nm in decl:
        by_phrase.setdefault(ph, []).append(nm)
    dup = {ph: nms for ph, nms in by_phrase.items() if len(nms) > 1}
    short = [ph for ph, _d, _n in decl if len(ph) < MIN_CHARS]
    unresolved = []
    for ph, _did, nm in decl:
        got = match(db, ph)
        if not got or got[0][2] != nm:
            unresolved.append((ph, nm, got[0][2] if got else "none"))
    thin = [ph for ph, _d, _n in decl if len(ph.split()) < MIN_WORDS]
    shadow = []
    for ph, _d, nm in decl:
        for other, _d2, nm2 in decl:
            if other != ph and other in ph:
                shadow.append((ph, nm, other, nm2))
    # ★file↔table cross-check★ — the file is canonical and the table is a derivative (§declared_in_files)
    in_files = declared_in_files()
    in_table = {ph for ph, _d, _n in decl}
    unwritten = {ph: nm for ph, nm in in_files.items() if ph not in in_table}
    orphan = [(ph, nm) for ph, _d, nm in decl if ph not in in_files]

    prompts = _real_prompts()
    used = []
    for ph, _did, nm in decl:
        hits = sum(1 for p in prompts if ph in p)
        used.append((ph, nm, hits))
    return {"declared": decl, "duplicates": dup, "short": short, "thin": thin,
            "shadow": shadow, "used": used, "unresolved": unresolved,
            "in_files": in_files, "unwritten": unwritten, "orphan": orphan,
            "prompts_checked": len(prompts)}


def render(res: dict) -> str:
    out = ["=" * 78,
           i18n.t("triggers.title", files=len(res.get("in_files", {})),
                 rows=len(res["declared"]), prompts=res["prompts_checked"]),
           "=" * 78]
    for ph, _did, nm in res["declared"]:
        out.append("  %-34s → %s" % (ph, nm))
    ok = True
    if res.get("unwritten"):
        ok = False
        out.append("\n" + i18n.t("triggers.unwritten_header"))
        for ph, nm in res["unwritten"].items():
            out.append("   %-34s %s" % (ph, nm))
        out.append("   " + i18n.t("triggers.unwritten_fix"))
    if res.get("orphan"):
        ok = False
        out.append("\n" + i18n.t("triggers.orphan_header"))
        for ph, nm in res["orphan"]:
            out.append("   %-34s %s" % (ph, nm))
    if res["duplicates"]:
        ok = False
        out.append("\n" + i18n.t("triggers.dup_header"))
        for ph, nms in res["duplicates"].items():
            out.append("   %s : %s" % (ph, ", ".join(nms)))
    if res["short"]:
        ok = False
        out.append("\n" + i18n.t("triggers.short_header", n=MIN_CHARS, list=", ".join(res["short"])))
    if res["unresolved"]:
        ok = False
        out.append("\n" + i18n.t("triggers.unresolved_header"))
        for ph, want, got in res["unresolved"]:
            out.append("   " + i18n.t("triggers.unresolved_row", ph=ph, want=want, got=got))
    if res["thin"]:
        ok = False
        out.append("\n" + i18n.t("triggers.thin_header", n=MIN_WORDS, list=", ".join(res["thin"])))
    if res["shadow"]:
        ok = False
        out.append("\n" + i18n.t("triggers.shadow_header"))
        for ph, nm, other, nm2 in res["shadow"]:
            out.append("   %s(%s) ⊃ %s(%s)" % (ph, nm, other, nm2))
    zero = [(ph, nm) for ph, nm, h in res["used"] if h == 0]
    if zero:
        out.append("\n" + i18n.t("triggers.zero_header"))
        for ph, nm in zero[:8]:
            out.append("   %-34s %s" % (ph, nm))
    live = [(ph, nm, h) for ph, nm, h in res["used"] if h]
    if live:
        out.append("\n" + i18n.t("triggers.live_header"))
        for ph, nm, h in sorted(live, key=lambda x: -x[2])[:8]:
            out.append("   %-34s %s (%dx)" % (ph, nm, h))
    out.append("\n%s" % (i18n.t("triggers.all_passed") if ok else i18n.t("triggers.fix_above")))
    return "\n".join(out)
