"""★recall in a form a program can read★ — the `orbit-1` JSON mode (2026-10-29).

## Why a second shape

The text answer was written for a person and a model: numbered lines, a `why`, the evidence age, a
warning when it is old. A client that keeps an outbox and a ledger (Cosmos Orbit) needs the same facts
as fields: a stable id (`name`, with `path`, `source`, `sha`), the lane the answer came from, whether
part of the request could not be served and why, and how long it took. Nothing here changes what is
found or in which order — the JSON carries ★the rows the text mode shows★, no more and no fewer
(§tests/verify_recall_json.py holds the two together).

What is not known is `null`, never 0 or "": `evidence_date` when the memory carries none, `embed_cached`
when the meaning lane was not used, `project_id` until a memory declares one (§O4).
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence

CONTRACT = "orbit-1"
REQUEST_ID_MAX = 128


def error(code: str, message: str, detail: Optional[dict] = None) -> Dict[str, Any]:
    """The error envelope of JSON mode — a conflict or a bad argument is an answer, not an exception."""
    return {"contract": CONTRACT, "ok": False,
            "error": {"code": code, "message": message, "detail": detail or {}}}


def validate(args: dict) -> Optional[Dict[str, Any]]:
    """None when the JSON-mode arguments are acceptable, else the error envelope to return."""
    fmt = args.get("format")
    if fmt not in (None, "text", "json"):
        return error("invalid_argument", "format must be \"text\" or \"json\"", {"format": fmt})
    scope = args.get("scope")
    if scope is not None and (not isinstance(scope, str) or not scope.strip() or len(scope) > REQUEST_ID_MAX):
        return error("invalid_argument", "scope must be a non-empty string of at most %d characters" % REQUEST_ID_MAX,
                     {"max": REQUEST_ID_MAX})
    rid = args.get("request_id")
    if fmt == "json" and rid is not None and (not isinstance(rid, str) or len(rid) > REQUEST_ID_MAX):
        return error("invalid_argument", "request_id must be a string of at most %d characters" % REQUEST_ID_MAX,
                     {"max": REQUEST_ID_MAX})
    return None


def _shas(db: sqlite3.Connection, paths: Sequence[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for i in range(0, len(paths), 400):
        chunk = list(paths[i:i + 400])
        if not chunk:
            continue
        try:
            for path, sha in db.execute(
                    "SELECT path, sha FROM docs WHERE path IN (%s)" % ",".join("?" * len(chunk)), chunk):
                out[path] = sha
        except sqlite3.Error:
            pass
    return out


def _project_ids(db: sqlite3.Connection, paths: Sequence[str]) -> Dict[str, Optional[str]]:
    """`project_id` per path — null until the index carries the column (§O4); never a guess."""
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(docs)")}
    except sqlite3.Error:
        cols = set()
    if "project_id" not in cols:
        return {}
    out: Dict[str, Optional[str]] = {}
    for i in range(0, len(paths), 400):
        chunk = list(paths[i:i + 400])
        if not chunk:
            continue
        for path, pid in db.execute(
                "SELECT path, project_id FROM docs WHERE path IN (%s)" % ",".join("?" * len(chunk)), chunk):
            out[path] = pid or None
    return out


def _row(r: dict, rank: Optional[int], via: str, shas: Dict[str, str], pids: Dict[str, Optional[str]],
         shown: int) -> Dict[str, Any]:
    ev = r.get("evidence_date") or None
    ev_age = r.get("evidence_age_days", -1)
    return {
        "rank": rank,
        "name": r.get("name"),
        "path": r.get("path"),
        "source": r.get("source"),
        "kind": r.get("kind") or None,
        "sha": shas.get(r.get("path") or "", None),
        "score": r.get("score"),
        "why": r.get("why"),
        "evidence_date": ev,
        "evidence_age_days": (ev_age if (ev and isinstance(ev_age, int) and ev_age >= 0) else None),
        "file_age_days": r.get("age_days"),
        "stale": bool(r.get("stale")),
        "excerpt": (r.get("excerpt") or "")[:shown],
        "excerpt_chars": shown,
        "via": via,
        "related": bool(r.get("related")),
        "project_id": pids.get(r.get("path") or "", None),
    }


def response(db: sqlite3.Connection, *, query: str, terms: Sequence[str], k: int, rows: List[dict],
             mrows: Optional[List[dict]], why_none: str, asked_meaning: int, auto_meaning: bool,
             request_id: Optional[str], unsure: bool, bridged: Sequence[str], reordered: bool,
             embed_cached: Optional[bool], t0: float, shown: int,
             scope: Optional[str] = None, scope_applied: bool = False) -> Dict[str, Any]:
    """The whole JSON answer for one recall. `rows` are exactly what the text mode lists."""
    direct = [r for r in rows if not r.get("related")]
    related = [r for r in rows if r.get("related")]
    paths = [r.get("path") or "" for r in rows] + [r.get("path") or "" for r in (mrows or [])]
    shas, pids = _shas(db, paths), _project_ids(db, paths)
    lane = "fused" if reordered else ("bridged" if bridged else "words")
    meaning = None
    if asked_meaning > 0:
        meaning = {"asked": asked_meaning, "auto": bool(auto_meaning),
                   "rows": [_row(r, None, "meaning", shas, pids, shown) for r in (mrows or [])],
                   "reason": why_none or ""}
    partial = bool(asked_meaning > 0 and not mrows)
    # `embed_cached` says something only when the meaning lane was used (asked, or fused when unsure);
    # the server peeks at the cache earlier than that, and a peek is not a use.
    if not (asked_meaning > 0 or reordered):
        embed_cached = None
    try:
        corpus_docs = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    except sqlite3.Error:
        corpus_docs = None
    try:
        from brain import calibrate, store
        index_at = store.get_meta(db, "last_index") or None
        gen = calibrate.CODE_GENERATION
    except Exception:                                    # noqa: BLE001
        index_at, gen = None, None
    return {
        "contract": CONTRACT,
        "request_id": request_id,
        "query": query,
        "terms": list(terms or []),
        "k": k,
        "lane": lane,
        "unsure": bool(unsure),
        "bridged_terms": list(bridged or []),
        "scope": {"project_id": scope, "applied": bool(scope_applied)},
        "results": [_row(r, i, ("graph" if r.get("via_graph") else ("meaning" if r.get("via_vector") else "words")),
                         shas, pids, shown) for i, r in enumerate(direct, 1)],
        "related": [{"name": r.get("name"), "via_graph": r.get("via_graph"),
                     "description": r.get("description") or r.get("title"),
                     "project_id": pids.get(r.get("path") or "", None)} for r in related],
        "meaning": meaning,
        "meta": {"latency_ms": int((time.time() - t0) * 1000), "embed_cached": embed_cached,
                 "partial": partial, "fallback_reason": (why_none or None) if partial else None,
                 "excerpt_chars": shown, "corpus_docs": corpus_docs, "index_at": index_at,
                 "code_generation": gen},
    }
