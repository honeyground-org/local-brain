"""Keep the auto-loaded index under its host's read limit — ★by the audit's own verdicts★.

Why this exists
---------------
A host such as Claude Code tells every session "save a memory, then add a one-line pointer to the
index". So the index grows by one line per memory, forever, and the size warning (§indexaudit.
size_notice) only says so. Nobody acts on a warning in a file several sessions write at once: each
session leaves it for the next (measured 2026-11-20 — 21,701 characters, every session declined).

`brain index-audit` already knows which lines are safe to shorten or drop: those the hook still
attaches on a short query once the line is gone. This module ★acts on that verdict and nothing else★.

What it may touch — every condition must hold
---------------------------------------------
  · the audit says `safe` (never a directive, never `if-asked` · `needs-terms` · `at-risk`)
  · the section is not a `directives` or `signals` section (§indexaudit.section_role)
  · every fact the line carries is written in its source memory (§indexaudit.facts) — a line whose
    fact lives only in the index is never shortened or dropped
  · its source memory exists and has not changed for `keep_recent_days` (current work stays whole)
  · ★dropping★ also needs the source to be older than its kind's stale limit (§search.stale_limit,
    the host's own types — Claude Code: project 21 days, reference 180, user/feedback 360)

Two stages, and only while the file is over its target (`target_ratio` × the host's warning size):
  ① shorten — a `- [Title](file.md) — hook` line keeps its link and the hook's first clause
  ② drop    — still over: the oldest droppable lines go, oldest source first, until under

Writing is guarded: the file is re-read just before the write and the write is refused if another
session changed it after the plan was made (the next run plans again). A backup of the whole file
and one ledger line per change go to `<brain home>/index-trim/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Dict, List, Optional

from . import i18n, indexaudit as ia, search, store

DEFAULTS = {"auto": False, "target_ratio": 0.75, "hook_chars": 80, "keep_recent_days": 7}
_BOUNDS = {"target_ratio": (0.2, 1.0), "hook_chars": (20, 400), "keep_recent_days": (0, 365)}
KEEP_BACKUPS = 10

# The pointer form the host itself asks for: `- [Title](file.md) — hook`
_POINTER = re.compile(r"^([-*] \[[^\]]+\]\([A-Za-z0-9_./-]+\.md\))\s*(?:[—–-]\s*)?(.*)$")
_CLAUSE_END = re.compile(r"(?<=[.!?。])\s| · |; | — | → ")


def settings(cfg: Optional[dict] = None) -> dict:
    """Config `index_trim` over the defaults. A value out of its bounds falls back to the default."""
    if cfg is None:
        cfg = store.load_config(tolerant=True)
    raw = cfg.get("index_trim")
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    if isinstance(raw.get("auto"), bool):
        out["auto"] = raw["auto"]
    for key, (lo, hi) in _BOUNDS.items():
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
            out[key] = type(DEFAULTS[key])(v)
    return out


def target_chars(st: dict) -> int:
    """The size to trim down to — a share of ★the host's own measured warning size★. 0 = no limit."""
    from brain import hosts
    limit = hosts.active().index_read_limit()
    if not limit:
        return 0
    return int(limit["warn_chars"] * st["target_ratio"])


def first_clause(hook: str, cap: int) -> str:
    """The hook's first clause, at most `cap` characters, never ending inside a code span."""
    cut = _CLAUSE_END.split(hook.strip(), maxsplit=1)[0].strip()
    if len(cut) > cap:
        cut = cut[:cap - 1].rstrip() + "…"
    if cut.count("`") % 2:
        cut = cut[:cut.rfind("`")].rstrip()
        cut = (cut + "…") if cut else ""
    return cut


def shorten(line: str, cap: int) -> str:
    """The shortened pointer line, or '' when the line is not in pointer form or would not shrink."""
    m = _POINTER.match(line)
    if not m:
        return ""
    head, hook = m.groups()
    cut = first_clause(hook, cap) if hook else ""
    new = "%s — %s" % (head, cut) if cut else head
    return new if len(new) < len(line) else ""


def _sources(db, golds: List[str]) -> List[dict]:
    out = []
    for g in golds:
        row = search.resolve(db, g)
        if not row:
            continue
        r = db.execute("SELECT path, kind FROM docs WHERE id=?", (row["id"],)).fetchone()
        if r:
            out.append({"name": row["name"], "path": r["path"], "kind": r["kind"] or ""})
    return out


def _age_days(path: str, now: float) -> float:
    return max(0.0, (now - os.path.getmtime(path)) / 86400.0)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan(path: str = "", now: Optional[float] = None, audit: Optional[dict] = None) -> dict:
    """What a trim would do to the index right now — nothing is written."""
    path = path or ia.MEMORY_INDEX()
    now = time.time() if now is None else now
    st = settings()
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    target = target_chars(st)
    res = {"path": path, "sha": _sha(text), "chars": len(text), "target": target,
           "settings": st, "actions": [], "skipped": {}, "after": len(text)}
    if not target or len(text) <= target:
        res["reason"] = "under-target"
        return res
    audit = audit if audit is not None else ia.audit(path)
    db = store.connect()
    lines = text.split("\n")
    skipped: Dict[str, int] = {}

    def skip(why: str) -> None:
        skipped[why] = skipped.get(why, 0) + 1

    shortenable, droppable = [], []
    for row in audit["rows"]:
        it = row["item"]
        if row["verdict"] != "safe":
            skip(row["verdict"])
            continue
        if it.role in ("directives", "signals"):
            skip("section")
            continue
        # ⛔ the audit numbers lines of the text it read; a line that moved since is not touched
        if lines[it.line_no - 1:it.line_no - 1 + len(it.lines)] != it.lines:
            skip("moved")
            continue
        srcs = _sources(db, it.golds)
        if not srcs or len(srcs) < len(it.golds) or not all(os.path.exists(s["path"]) for s in srcs):
            skip("no-source")
            continue
        hay = ia._bodies(db, it.golds)
        if any(f not in hay for f in ia.facts(it)):
            skip("fact-only-here")
            continue
        age = min(_age_days(s["path"], now) for s in srcs)
        if age < st["keep_recent_days"]:
            skip("recent")
            continue
        entry = {"line_no": it.line_no, "lines": list(it.lines), "names": [s["name"] for s in srcs],
                 "age_days": round(age, 1)}
        if len(it.lines) == 1:
            new = shorten(it.lines[0], st["hook_chars"])
            if new:
                shortenable.append(dict(entry, new=new))
        if all(age >= search.stale_limit(s["kind"]) for s in srcs):
            droppable.append(entry)

    size = len(text)
    acted: Dict[int, dict] = {}
    for e in shortenable:                                 # ① shorten
        if size <= target:
            break
        acted[e["line_no"]] = {"action": "shorten", "line_no": e["line_no"], "names": e["names"],
                               "old": e["lines"], "new": [e["new"]], "age_days": e["age_days"]}
        size -= len(e["lines"][0]) - len(e["new"])
    for e in sorted(droppable, key=lambda d: -d["age_days"]):   # ② drop, oldest first
        if size <= target:
            break
        cur = acted.get(e["line_no"])
        kept = cur["new"] if cur else e["lines"]
        acted[e["line_no"]] = {"action": "drop", "line_no": e["line_no"], "names": e["names"],
                               "old": e["lines"], "new": [], "age_days": e["age_days"]}
        size -= sum(len(x) + 1 for x in kept)
    res["actions"] = [acted[k] for k in sorted(acted)]
    res["skipped"] = skipped
    res["after"] = size
    res["reason"] = "planned" if res["actions"] else "nothing-safe"
    return res


def _render_text(text: str, actions: List[dict]) -> str:
    lines = text.split("\n")
    for a in sorted(actions, key=lambda x: -x["line_no"]):
        i = a["line_no"] - 1
        lines[i:i + len(a["old"])] = a["new"]
    return "\n".join(lines)


def trim_dir() -> str:
    return os.path.join(store.brain_home(), "index-trim")


def apply(p: dict) -> dict:
    """Write the plan — ★only if the file is still exactly what the plan read★."""
    out = {"applied": False, "path": p["path"], "backup": "", "changes": len(p["actions"])}
    if not p["actions"]:
        out["reason"] = p.get("reason", "nothing-safe")
        return out
    with open(p["path"], encoding="utf-8") as fh:
        text = fh.read()
    if _sha(text) != p["sha"]:
        out["reason"] = "changed"
        return out
    new = _render_text(text, p["actions"])
    d = trim_dir()
    os.makedirs(d, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(d, "%s-%s" % (stamp, os.path.basename(p["path"])))
    with open(backup, "w", encoding="utf-8") as fh:
        fh.write(text)
    tmp = p["path"] + ".brain-trim.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(new)
    with open(p["path"], encoding="utf-8") as fh:           # one more look right before the swap
        if _sha(fh.read()) != p["sha"]:
            os.remove(tmp)
            out["reason"] = "changed"
            return out
    os.replace(tmp, p["path"])
    with open(os.path.join(d, "ledger.jsonl"), "a", encoding="utf-8") as fh:
        for a in p["actions"]:
            fh.write(json.dumps({"at": stamp, "file": p["path"], "action": a["action"],
                                 "names": a["names"], "old": a["old"], "new": a["new"]},
                                ensure_ascii=False) + "\n")
    backups = sorted(f for f in os.listdir(d) if f != "ledger.jsonl")
    for f in backups[:-KEEP_BACKUPS]:
        os.remove(os.path.join(d, f))
    out.update(applied=True, backup=backup, reason="applied", chars=len(new))
    return out


def auto(path: str = "") -> dict:
    """The daily job's step: plan and apply, when the person turned it on. Never raises."""
    st = settings()
    if not st["auto"]:
        return {"skipped": "off"}
    try:
        path = path or ia.MEMORY_INDEX()
        if not path or not os.path.exists(path):
            return {"skipped": "no-index"}
        p = plan(path)
        r = apply(p)
        return {"before": p["chars"], "target": p["target"], "after": r.get("chars", p["chars"]),
                "changes": r["changes"], "reason": r["reason"]}
    except Exception as exc:                              # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


I18N_PREFIX = ("it.action.", "it.skip.", "it.reason.")


def render(p: dict, applied: Optional[dict] = None) -> str:
    out = ["=" * 78, i18n.t("it.title"), "=" * 78,
           i18n.t("it.size", file=os.path.basename(p["path"]), chars=f"{p['chars']:,}",
                  target=f"{p['target']:,}", after=f"{p['after']:,}")]
    for a in p["actions"]:
        old = sum(len(x) for x in a["old"])
        new = sum(len(x) for x in a["new"])
        out.append("  L%-4d %s  %s  %d → %d" % (a["line_no"], i18n.t("it.action." + a["action"]),
                                                ", ".join(a["names"])[:48], old, new))
    if p["skipped"]:
        out.append(i18n.t("it.skipped"))
        for why, n in sorted(p["skipped"].items(), key=lambda kv: -kv[1]):
            out.append("  · %s — %d" % (i18n.t("it.skip." + why.replace("-", "_")), n))
    out.append("")
    if applied is None:
        out.append(i18n.t("it.reason." + p.get("reason", "planned").replace("-", "_")))
        if p["actions"]:
            out.append(i18n.t("it.dry_run"))
    else:
        out.append(i18n.t("it.reason." + applied["reason"].replace("-", "_")))
        if applied.get("backup"):
            out.append(i18n.t("it.backup", path=applied["backup"]))
    return "\n".join(out)
