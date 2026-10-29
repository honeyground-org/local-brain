"""★a save that can be retried without fear★ — the `orbit-1` remember contract (2026-10-29).

## Why this module exists

`remember` has always been "write this name, overwrite if it exists". A person typing that is fine; a
client retrying after a timeout is not: it cannot tell whether the first attempt landed, and a second
write with the same name would silently replace a memory someone else wrote in between. So the contract
adds three things a retrying client needs and nothing else:

  · an `idempotency_key` with a `payload_sha` — the same request again is a `noop` with the same answer
  · an `expected_sha` — to change an existing memory you prove which version you read (compare-and-set)
  · an answer that separates ★stored★ (the file is there) from ★indexed_words★ (findable by words),
    ★embedded★ (findable by meaning — the daily job) and ★served★ (pushed to a dedicated database)

A memory written by a person (no key) is never overwritten by a keyed write unless the writer proves
the version it read: that is `name_taken`. The legacy text behaviour is untouched when neither
`idempotency_key` nor `format: "json"` is given (§tests/verify_remember_contract.py ①).

What is not known is `null`: `embedded` when the vector table cannot be asked, `project_id` when none
was given.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

CONTRACT = "orbit-1"
KEY_MAX = 128
PROJECT_MAX = 128
SOURCE_MAX = 512
# The fields a retry is compared on — the date of saving is not one of them (a retry tomorrow is the same request).
PAYLOAD_FIELDS = ("description", "type", "body", "links", "search_aliases", "project_id", "source", "observed_at")
_SHA = re.compile(r"[0-9a-f]{40}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def wants_contract(args: dict) -> bool:
    return args.get("idempotency_key") is not None or args.get("format") == "json"


def error(code: str, message: str, detail: Optional[dict] = None) -> Dict[str, Any]:
    return {"contract": CONTRACT, "ok": False,
            "error": {"code": code, "message": message, "detail": detail or {}}}


def validate(args: dict) -> Optional[Dict[str, Any]]:
    """None when acceptable, else the error envelope — a bad argument is an answer, not an exception."""
    fmt = args.get("format")
    if fmt not in (None, "text", "json"):
        return error("invalid_argument", "format must be \"text\" or \"json\"", {"format": fmt})
    key = args.get("idempotency_key")
    if key is not None and (not isinstance(key, str) or not key.strip() or len(key) > KEY_MAX):
        return error("invalid_argument", "idempotency_key must be a non-empty string of at most %d characters" % KEY_MAX,
                     {"max": KEY_MAX})
    exp = args.get("expected_sha")
    if exp is not None and (not isinstance(exp, str) or not _SHA.fullmatch(exp)):
        return error("invalid_argument", "expected_sha must be 40 lowercase hex characters", {"expected_sha": str(exp)[:60]})
    pid = args.get("project_id")
    if pid is not None and (not isinstance(pid, str) or not pid.strip() or len(pid) > PROJECT_MAX):
        return error("invalid_argument", "project_id must be a non-empty string of at most %d characters" % PROJECT_MAX,
                     {"max": PROJECT_MAX})
    src = args.get("source")
    if src is not None and (not isinstance(src, str) or len(src) > SOURCE_MAX):
        return error("invalid_argument", "source must be a string of at most %d characters" % SOURCE_MAX, {"max": SOURCE_MAX})
    obs = args.get("observed_at")
    if obs is not None and (not isinstance(obs, str) or not _DATE.fullmatch(obs)):
        return error("invalid_argument", "observed_at must be YYYY-MM-DD", {"observed_at": str(obs)[:40]})
    for f in ("description", "type", "body"):
        if not isinstance(args.get(f), str) or not args.get(f, "").strip():
            return error("invalid_argument", "%s is required" % f, {"field": f})
    return None


def aliases(args: dict) -> List[str]:
    return list(dict.fromkeys(unicodedata.normalize("NFKC", a).strip()
                              for a in (args.get("search_aliases") or []) if isinstance(a, str) and a.strip()))


def payload_sha(args: dict) -> str:
    """What a retry is compared on — canonical JSON of the content fields, never the date of saving."""
    body = {f: args.get(f) for f in PAYLOAD_FIELDS}
    body["search_aliases"] = aliases(args)
    body["links"] = [l for l in (args.get("links") or []) if isinstance(l, str)]
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def file_sha(path: str) -> Optional[str]:
    """The index's own document hash — sha1 of the file text (§store) — so `sha` means one thing everywhere."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def current(path: str) -> Dict[str, Any]:
    """What is on disk under that name right now — the only state a decision may read."""
    from brain import store
    out: Dict[str, Any] = {"exists": False, "sha": None, "idempotency_key": None, "payload_sha": None,
                           "verified_at": None, "project_id": None}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return out
    meta, _ = store.parse_frontmatter(raw)
    out.update({"exists": True, "sha": hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest(),
                "idempotency_key": (meta.get("idempotency_key") or "").strip() or None,
                "payload_sha": (meta.get("payload_sha") or "").strip() or None,
                "verified_at": (meta.get("verified_at") or "").strip() or None,
                "project_id": (meta.get("project_id") or "").strip() or None})
    return out


def decide(cur: Dict[str, Any], args: dict) -> Tuple[str, Optional[Dict[str, Any]]]:
    """new · noop · updated · conflict — from what is on disk and what was asked, nothing else.

    | on disk                                   | asked                          | result                  |
    | nothing                                   | —                              | new                     |
    | same key · same payload                   | —                              | noop                    |
    | same key · other payload                  | expected_sha == current sha    | updated                 |
    | same key · other payload                  | otherwise                      | conflict: sha_mismatch  |
    | other key, or a memory written with none  | expected_sha == current sha    | updated (proved version)|
    | other key, or a memory written with none  | otherwise                      | conflict: name_taken    |
    """
    if not cur.get("exists"):
        return "new", None
    key = args.get("idempotency_key")
    expected = args.get("expected_sha")
    detail = {"current_sha": cur.get("sha"), "current_idempotency_key": cur.get("idempotency_key"),
              "current_payload_sha": cur.get("payload_sha")}
    if key and cur.get("idempotency_key") == key:
        if cur.get("payload_sha") == payload_sha(args):
            return "noop", None
        if expected and expected == cur.get("sha"):
            return "updated", None
        return "conflict", dict(detail, reason="sha_mismatch")
    if expected and expected == cur.get("sha"):
        return "updated", None
    return "conflict", dict(detail, reason="name_taken")


def render(name: str, args: dict, verified_at: str) -> str:
    """The file — the legacy shape with the contract's fields added in the frontmatter."""
    lines = ["---", "name: %s" % name, "description: %s" % args["description"].replace("\n", " "),
             "verified_at: %s" % verified_at]
    if args.get("idempotency_key"):
        lines.append("idempotency_key: %s" % args["idempotency_key"])
        lines.append("payload_sha: %s" % payload_sha(args))
    if args.get("project_id"):
        lines.append("project_id: %s" % args["project_id"].strip())
    if args.get("source"):
        lines.append("source: %s" % args["source"].replace("\n", " "))
    al = aliases(args)
    if al:
        lines.append("search_aliases: " + json.dumps(al, ensure_ascii=False))
    lines += ["metadata:", "  node_type: memory", "  type: %s" % args["type"], "---", ""]
    links = [l for l in (args.get("links") or []) if isinstance(l, str)]
    link_line = ("\n\nrelated: " + " · ".join("[[%s]]" % l for l in links)) if links else ""
    return "\n".join(lines) + "\n" + args["body"].rstrip() + link_line + "\n"


def _served(db: sqlite3.Connection) -> str:
    try:
        from brain import stores
        for role in stores.ROLES:
            if stores.choice(role).get("backend", "sqlite") != "sqlite":
                return "pending"
    except Exception:                                    # noqa: BLE001
        return "unknown"
    return "n/a"


def _embedded(db: sqlite3.Connection, path: str, sha: Optional[str]) -> Optional[bool]:
    if not sha:
        return None
    try:
        from brain import vectors
        row = db.execute("SELECT 1 FROM vectors v JOIN docs d ON d.id = v.doc_id "
                         "WHERE d.path = ? AND v.sha = ? AND v.model = ? AND v.dim = ? LIMIT 1",
                         (path, sha, vectors.model_tag(), vectors.DIM)).fetchone()
        return bool(row)
    except Exception:                                    # noqa: BLE001 — no table, no engine: unknown, not false
        return None


def ack(db: sqlite3.Connection, *, name: str, path: str, result: str, conflict: Optional[dict], args: dict,
        stats: dict, t0: float, verified_at: Optional[str], cur: Dict[str, Any]) -> Dict[str, Any]:
    """The answer: what is on disk now, and how far it has travelled into the index."""
    sha = file_sha(path)
    indexed = None
    try:
        row = db.execute("SELECT sha FROM docs WHERE path = ?", (path,)).fetchone()
        indexed = bool(row and sha and row[0] == sha)
    except sqlite3.Error:
        indexed = None
    stored = result in ("new", "updated", "noop") and sha is not None
    key = args.get("idempotency_key") if result != "conflict" else cur.get("idempotency_key")
    idx = {k: stats.get(k) for k in ("added", "updated", "removed", "seconds", "error") if k in stats}
    return {
        "contract": CONTRACT,
        "name": name,
        "path": path,
        "sha": sha,
        "result": result,
        "stored": stored,
        "indexed_words": indexed if stored else False,
        "embedded": _embedded(db, path, sha) if stored else False,
        "served": _served(db) if stored else "n/a",
        "index": idx,
        "conflict": conflict,
        "project_id": (args.get("project_id") or None) if result != "conflict" else cur.get("project_id"),
        "idempotency_key": key,
        "payload_sha": (payload_sha(args) if result != "conflict" else cur.get("payload_sha")),
        "verified_at": verified_at,
        "meta": {"latency_ms": int((time.time() - t0) * 1000)},
    }
