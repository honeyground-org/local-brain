"""Protected memory writes for every client, with the compatible orbit-1 result fields.

JSON is the default presentation. Omitted keys are derived from the request content.
Existing memories require the version read before an update; unchanged retries are noops.
The directory lock serializes cooperating processes, and atomic replacement preserves the
old file on a failed write. Direct filesystem editors do not participate in that lock.
Storage, lexical indexing, embeddings and backend publication are separate ACK stages.
"""
from __future__ import annotations

import hashlib
import contextlib
import datetime
import json
import os
import re
import sqlite3
import time
import tempfile
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


def error(code: str, message: str, detail: Optional[dict] = None) -> Dict[str, Any]:
    return {"contract": CONTRACT, "ok": False,
            "error": {"code": code, "message": message, "detail": detail or {}}}


def validate(args: dict) -> Optional[Dict[str, Any]]:
    """None when acceptable, else the error envelope — a bad argument is an answer, not an exception."""
    if not isinstance(args, dict):
        return error("invalid_argument", "arguments must be an object")
    name = args.get("name")
    if (not isinstance(name, str) or not re.fullmatch(r"[\w.-]{1,128}", name)
            or name in (".", "..")):
        return error("invalid_argument", "name must be a 1–128 character slug using letters, digits, dots, underscores or hyphens")
    fmt = args.get("format")
    if fmt not in (None, "text", "json"):
        return error("invalid_argument", "format must be \"text\" or \"json\"", {"format": fmt})
    key = args.get("idempotency_key")
    if key is not None and (not isinstance(key, str) or not key.strip() or len(key) > KEY_MAX
                            or key != key.strip().strip("\"'\"") or len(key.splitlines()) != 1 or any(ord(c) < 32 for c in key)):
        return error("invalid_argument", "idempotency_key must be a non-empty string of at most %d characters" % KEY_MAX,
                     {"max": KEY_MAX})
    exp = args.get("expected_sha")
    if exp is not None and (not isinstance(exp, str) or not _SHA.fullmatch(exp)):
        return error("invalid_argument", "expected_sha must be 40 lowercase hex characters", {"expected_sha": str(exp)[:60]})
    pid = args.get("project_id")
    if "project_id" in args and (not isinstance(pid, str) or not pid.strip() or len(pid) > PROJECT_MAX
                            or pid != pid.strip().strip("\"'\"") or len(pid.splitlines()) != 1 or any(ord(c) < 32 for c in pid)):
        return error("invalid_argument", "project_id must be a non-empty string of at most %d characters" % PROJECT_MAX,
                     {"max": PROJECT_MAX})
    src = args.get("source")
    if src is not None and (not isinstance(src, str) or len(src) > SOURCE_MAX):
        return error("invalid_argument", "source must be a string of at most %d characters" % SOURCE_MAX, {"max": SOURCE_MAX})
    obs = args.get("observed_at")
    if obs is not None and (not isinstance(obs, str) or not _DATE.fullmatch(obs)):
        return error("invalid_argument", "observed_at must be YYYY-MM-DD", {"observed_at": str(obs)[:40]})
    if obs is not None:
        try:
            datetime.date.fromisoformat(obs)
        except ValueError:
            return error("invalid_argument", "observed_at must be a valid calendar date")
    for f in ("description", "type", "body"):
        if not isinstance(args.get(f), str) or not args.get(f, "").strip():
            return error("invalid_argument", "%s is required" % f, {"field": f})
    if any(ord(c) < 32 for c in args["type"]):
        return error("invalid_argument", "type must be a single-line value")
    for field in ("links", "search_aliases"):
        values = args.get(field, [])
        if (not isinstance(values, list) or len(values) > 100
                or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in values)):
            return error("invalid_argument", "%s must be an array of at most 100 nonempty strings" % field)
    return None


def prepared(args: dict) -> dict:
    """Give ordinary clients stable retries without requiring a caller-generated key."""
    out = dict(args)
    if not out.get("idempotency_key"):
        identity = out["name"] + "\0" + payload_sha(out)
        out["idempotency_key"] = "auto:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return out


@contextlib.contextmanager
def write_lock(directory: str):
    """Serialize cooperating MCP writers even when each host has a separate process/home."""
    db = sqlite3.connect(os.path.join(directory, ".brain-remember-lock.sqlite3"), timeout=5)
    try:
        db.execute("BEGIN IMMEDIATE")
        yield
        db.commit()
    finally:
        db.close()


def atomic_write(path: str, text: str, expected_sha: Optional[str]) -> None:
    """A failed write preserves the old file. Recheck edits made outside the writer lock."""
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".brain-write-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.islink(path) or file_sha(path) != expected_sha:
            raise FileExistsError("Memory changed while preparing the write; read its current version")
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
    except FileNotFoundError:
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
    except FileNotFoundError:
        return out
    meta, _ = store.parse_frontmatter(raw)
    out.update({"exists": True, "name": os.path.basename(path)[:-3],
                "sha": hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest(),
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
    key = args.get("idempotency_key")
    expected = args.get("expected_sha")
    detail = {"current_sha": cur.get("sha"), "current_idempotency_key": cur.get("idempotency_key"),
              "current_payload_sha": cur.get("payload_sha")}
    if not cur.get("exists"):
        return ("conflict", dict(detail, reason="sha_mismatch")) if expected else ("new", None)
    if key and cur.get("idempotency_key") == key:
        if cur.get("payload_sha") == payload_sha(args):
            intended = render(cur["name"], args, cur.get("verified_at") or "")
            if hashlib.sha1(intended.encode("utf-8", "replace")).hexdigest() == cur.get("sha"):
                return "noop", None
            if expected != cur.get("sha"):
                return "conflict", dict(detail, reason="sha_mismatch")
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
        row = db.execute("SELECT id,body,sha FROM docs WHERE path=? AND sha=?", (path, sha)).fetchone()
        if row is None:
            return False
        model = vectors.model_tag()
        if vectors.PROVIDER == "embeddinggemma":
            from brain import localembed
            plan = localembed.cached_plan(db, row, model)
            if plan is None:
                return False
            expected = len(plan)
        else:
            expected = len(vectors.chunk_spans(row["body"] or ""))
        have = list(db.execute("SELECT chunk_no,sha FROM vectors WHERE doc_id=? AND model=? AND dim=?",
                               (row["id"], model, vectors.DIM)))
        return (expected > 0 and len(have) == expected and {r[0] for r in have} == set(range(expected))
                and all(r[1] == sha for r in have))
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
        "ok": stored,
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
        "verified_at": cur.get("verified_at") if result == "conflict" else verified_at,
        "partial": stored and indexed is not True,
        "retryable": stored and indexed is not True,
        "error": error("conflict", "Read the current memory and supply expected_sha to update it", conflict)["error"]
                 if result == "conflict" else None,
        "meta": {"latency_ms": int((time.time() - t0) * 1000)},
    }
