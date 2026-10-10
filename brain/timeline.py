"""Timeline — answers ★a question with no topic word★.

Why search doesn't work (measured 2026-08-18)
---------------------------------------
The user's third purpose axis is "remember and search history well too." Of 120 real prompts,
13 (10.8%) asked about history, and recalling on those showed:

    [silent] check on the earlier conversation or dev progress
    [silent] when and why did this rule change
    [silent] why did we decide not to do this last time
    [fired·wrong] explain what the workaround feature does → (a lesson about calibration probes) (15.03)

4 of 6 stayed silent, and 1 of the 2 that fired was wrong.

⛔ **Tried fixing it by growing the index, and that was disproven** — git commit messages (231 of them) were added
as a field on each document, and ★not a single one★ of the 6 history queries changed — only a regression (store._W_HISTORY comment).
The reason is simple: **these questions have no topic word.** "the earlier conversation" · "last time" · "when did it change" never
say what they're about. A word-matching index goes silent when there's no word to match.

★So this is a lookup, not a search★ — no ranking, but chronological order. The two questions differ:

    "what do you know about X"  → recall (found by topic word)
    "what did you do recently"  → timeline (swept by time)

What it uses as material
------------------
① **commits** — "what and why did it change." This repo's commit messages hold the most detailed
   history that carries *what existed → what was done → measured*.
② **recently updated memories** — "what was learned." Carries an evidence date (`verified_at`).
③ **session tails** — "what the last few sessions did" + ★what the user said at the time★ (`user_says`).
   The last one is especially valuable — what a session was trying to do stays in the user's own words.

⚠️ ③ only shows what remains in `pending` (can be cleared after notifying) — not the full history.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import subprocess
import time
from typing import Dict, List, Optional

from . import i18n, store

PENDING_DIR = os.path.join(store.brain_home(), "pending")   # ⛔ the home is decided by §store.brain_home
# ⛔ never hardcodes a repo path — store finds the repo each config source belongs to.
#    Someone else's environment has a different folder layout, and a personal path never belongs in a shared repo.


def _commits(days: int, limit: int) -> List[dict]:
    since = "%d.days.ago" % max(1, days)
    out = []
    for repo in store.git_repos():                 # there can be more than one repo
        try:
            p = subprocess.run(
                ["git", "-C", repo, "log", "--no-merges", "--since", since,
                 "-n", str(limit), "--format=%ad%x01%s%x01%an", "--date=short"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        except Exception:                                    # noqa: BLE001
            continue
        for line in p.stdout.splitlines():
            parts = line.split("\x01")
            if len(parts) >= 2:
                out.append({"date": parts[0], "subject": parts[1],
                            "author": parts[2] if len(parts) > 2 else "",
                            "repo": os.path.basename(repo)})
    out.sort(key=lambda c: c["date"], reverse=True)
    return out[:limit]


def _memories(db: sqlite3.Connection, days: int, limit: int) -> List[dict]:
    cutoff = time.time() - days * 86400
    # ⛔ excludes the index file (MEMORY.md) — it's not an answer to what was learned, and since it's
    #    edited nearly every session, it would hog the whole list by itself. Same reason as search._always_in_context.
    rows = db.execute(
        "SELECT name, kind, description, evidence_date, mtime FROM docs "
        "WHERE source='memory' AND name NOT IN ('MEMORY') AND mtime >= ? "
        "ORDER BY mtime DESC LIMIT ?",
        (cutoff, limit)).fetchall()
    return [{"name": r["name"], "kind": r["kind"] or "",
             "description": r["description"] or "",
             "evidence_date": r["evidence_date"] or "",
             "days_ago": int((time.time() - r["mtime"]) / 86400)} for r in rows]


def _sessions(days: int, limit: int) -> List[dict]:
    cutoff = time.time() - days * 86400
    out = []
    for f in sorted(glob.glob(os.path.join(PENDING_DIR, "*.json")),
                    key=lambda x: os.path.getmtime(x), reverse=True):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        upd = d.get("updated_at") or 0
        if upd and upd < cutoff:
            continue
        tail = d.get("tail") or {}
        says = [s for s in (tail.get("user_says") or [])
                # ⛔ a skill·plugin body sometimes rides in as the prompt — that is not the user's own words
                if not s.startswith("Base directory for this skill")]
        out.append({
            "session": (d.get("session_id") or "")[:8],
            "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(upd)) if upd else "",
            "cwd": os.path.basename(d.get("cwd") or ""),
            "code_writes": tail.get("code_writes", 0),
            "commits": tail.get("commits", 0),
            "saves": d.get("saves", 0),
            # the last thing said is the closest match to what that session was trying to do
            "user_says": [s.replace("\n", " ")[:140] for s in says[-3:]],
        })
        if len(out) >= limit:
            break
    return out


def recent(db: sqlite3.Connection, days: int = 7, limit: int = 25) -> dict:
    return {
        "days": days,
        "commits": _commits(days, limit),
        "memories": _memories(db, days, limit),
        "sessions": _sessions(days, limit),
    }


def render(res: dict) -> str:
    out = [i18n.t("timeline.title", days=res["days"])]

    ss = res["sessions"]
    if ss:
        out.append("")
        out.append(i18n.t("timeline.sessions_header", n=len(ss)))
        for s in ss:
            out.append("  " + i18n.t("timeline.session_row", when=s["when"], cwd=s["cwd"] or "-",
                                     code=s["code_writes"], commits=s["commits"], saves=s["saves"]))
            for q in s["user_says"]:
                out.append("      “%s”" % q)

    cs = res["commits"]
    if cs:
        out.append("")
        out.append(i18n.t("timeline.commits_header", n=len(cs)))
        for c in cs:
            out.append("  %s  %s" % (c["date"], c["subject"][:96]))

    ms = res["memories"]
    if ms:
        out.append("")
        out.append(i18n.t("timeline.memories_header", n=len(ms)))
        for m in ms:
            warn = ""
            if m["evidence_date"] and m["kind"] == "project":
                warn = i18n.t("timeline.evidence_warn", date=m["evidence_date"])
            out.append("  %-8s [%s] %s%s" % (i18n.t("timeline.days_ago", days=m["days_ago"]),
                                            m["kind"] or "?", m["name"][:44], warn))
            if m["description"]:
                out.append("        %s" % m["description"][:96])

    if not (ss or cs or ms):
        out.append("")
        out.append(i18n.t("timeline.empty"))
    return "\n".join(out)
