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
PAYLOAD_FIELDS = ("description", "type", "body", "links", "search_aliases", "project_id", "source", "observed_at", "lifecycle", "extracted_from", "embed", "identity_reference")
_SHA = re.compile(r"[0-9a-f]{40}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def error(code: str, message: str, detail: Optional[dict] = None) -> Dict[str, Any]:
    return {"contract": CONTRACT, "ok": False,
            "error": {"code": code, "message": message, "detail": detail or {}}}


def validate(args: dict) -> Optional[Dict[str, Any]]:
    """None when acceptable, else the error envelope — a bad argument is an answer, not an exception."""
    if not isinstance(args, dict):
        return error("invalid_argument", "arguments must be an object")
    if "embed" in args and type(args["embed"]) is not bool:
        return error("invalid_argument", "embed must be boolean")
    name = args.get("name")
    # Combining marks are letters' components in many scripts (including Indic text).
    # Preserve the supplied filename; filesystem aliases remain protected by the SHA write guard.
    if (not isinstance(name, str) or not 1 <= len(name) <= 128
            or any(not (c.isalnum() or unicodedata.category(c).startswith('M') or c in '._-') for c in name)
            or name in (".", "..")):
        return error("invalid_argument", "name must be a 1–128 character slug using letters, digits, dots, underscores or hyphens")
    fmt = args.get("format")
    if 'prepare_only' in args and not isinstance(args['prepare_only'], bool):
        return error('invalid_argument', 'prepare_only must be a boolean')
    if 'expected_fact_sha' in args and (not isinstance(args['expected_fact_sha'], str) or
                                      not re.fullmatch('[0-9a-f]{64}', args['expected_fact_sha'])):
        return error('invalid_argument', 'expected_fact_sha must be the SHA-256 returned by prepare_only')
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
    if any(ord(c) < 32 or c in "\u0085\u2028\u2029" for c in args["type"]):
        return error("invalid_argument", "type must be a single-line value")
    for field in ("links", "search_aliases"):
        values = args.get(field, [])
        if (not isinstance(values, list) or len(values) > 100
                or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in values)):
            return error("invalid_argument", "%s must be an array of at most 100 nonempty strings" % field)
    if "lifecycle" in args:
        from brain import lifecycle
        issue = lifecycle.validate(args["lifecycle"])
        if issue:
            return error("invalid_argument", issue)
    if "extracted_from" in args:
        issue = extraction_shape(args)
        if issue:
            return error("invalid_argument", issue)
    if "identity_reference" in args:
        issue = identity_reference_shape(args["identity_reference"])
        if issue:
            return error("invalid_argument", issue)
        if (not args.get("extracted_from") or not args.get("expected_fact_sha")
                or args["identity_reference"]["name"] == name):
            return error("invalid_argument", "Identity reuse requires a distinct source-bound proposal and its fact snapshot")
    return None


# Inputs accepted by the read-only revision planner; save arguments use the existing contract.
REVISION_CONTENT = ("name", "description", "type", "body", "project_id", "source", "observed_at",
                    "links", "search_aliases", "embed")
REVISION_FIELDS = REVISION_CONTENT + ("predecessor", "topic", "facet", "status", "valid_from",
                                      "valid_to", "change_reason", "evidence", "replace_whole_record")


def prepare_revision(db, args: dict) -> dict:
    """Build a reviewable remember request from explicit authority, without saving or inference."""
    from brain import lifecycle
    if not isinstance(args, dict) or set(args) - set(REVISION_FIELDS):
        return error("invalid_argument", "Use only the declared revision preparation fields")
    predecessor = args.get("predecessor")
    if not isinstance(predecessor, str) or not predecessor.strip() or len(predecessor) > 2000:
        return error("invalid_argument", "predecessor must be an existing memory name")
    if "replace_whole_record" in args and not isinstance(args["replace_whole_record"], bool):
        return error("invalid_argument", "replace_whole_record must be a boolean")
    if args.get("status") not in ("active", "proposed", "disputed"):
        return error("invalid_argument", "Explicitly choose active, proposed or disputed for the new claim")
    save = {k: args[k] for k in REVISION_CONTENT if k in args}
    bad = validate(save)
    if bad:
        return bad
    if save["name"] == predecessor:
        return error("invalid_argument", "Use a new name; the predecessor must remain in history")
    scope = save.get("project_id") or ""
    snap = lifecycle.snapshot(db)
    ids = snap.by_name.get((scope, predecessor), [])
    if len(ids) != 1:
        return error("predecessor_unavailable", "Select one indexed predecessor in the exact project_id scope")
    if snap.by_name.get((scope, save["name"])):
        return error("name_taken", "Choose an unused name for the new revision")
    prior = snap.docs[ids[0]]
    if file_sha(prior["path"]) != prior["sha"]:
        return dict(error("index_pending", "The predecessor source changed; refresh the index and prepare again"), retryable=True)
    old = prior["meta"]
    if old and lifecycle.validate(old):
        return error("invalid_predecessor", "The predecessor has invalid or incomplete validity metadata")
    meta = {k: args[k] for k in ("topic", "facet", "status", "valid_from", "valid_to", "change_reason", "evidence") if k in args}
    for k in ("topic", "facet"):
        if k in meta and not isinstance(meta[k], str):
            return error("invalid_argument", k + " must be a string")
    if old:
        for k in ("topic", "facet"):
            if k in meta and lifecycle.identity({k: meta[k]}) != lifecycle.identity({k: old[k]}):
                return error("invalid_argument", "A revision must reuse its predecessor's topic and facet")
            meta[k] = old[k]
    elif not args.get("replace_whole_record"):
        return error("whole_record_confirmation_required",
                     "Read the entire untracked predecessor. Confirm whole-record replacement explicitly; "
                     "do not retire unrelated facts in a mixed-topic note. Supply topic and facet for this fact.")
    meta["supersedes"] = [predecessor]
    if not old:
        meta["legacy_whole_record_sha"] = {predecessor: prior["sha"]}
    save["lifecycle"] = meta
    if requires_local_only(db, save):
        if save.get("embed") is True:
            return error("privacy_required", "Preserve embed:false from the predecessor")
        save["embed"] = False
    bad = validate(save)
    if bad:
        return bad
    issue = lifecycle.check_write(db, save["name"], save, {})
    if issue:
        return error("invalid_argument", issue)
    review = lifecycle.prepare(db, save["name"], save)
    if not review["source_matches_index"]:
        return dict(error("index_pending", "A fact source changed; refresh the index and prepare again"), retryable=True)
    save["expected_fact_sha"] = review["expected_fact_sha"]
    code, conflict = lifecycle.reconcile_write(db, save["name"], save)
    if code:
        return error(code, "Resolve competing current facts before preparing this revision", conflict)
    row = db.execute("SELECT body FROM docs WHERE id=?", (ids[0],)).fetchone()
    body = row["body"] or ""
    return dict(review, save_tool="remember", save_arguments=save,
                predecessor={"name": predecessor, "sha": prior["sha"], "body": body[:2000],
                             "body_truncated": len(body) > 2000, "lifecycle": snap.info(ids[0])},
                next="Review the predecessor and proposed content, then send save_arguments to remember. "
                     "If a source or competing fact changes, prepare again. This does not certify semantic truth.")


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
    if not args.get("lifecycle"):
        body.pop("lifecycle", None)  # Preserve retry hashes written before lifecycle support.
    if "embed" not in args:
        body.pop("embed", None)
    if not args.get("extracted_from"):
        body.pop("extracted_from", None)  # Keep existing retry hashes unchanged.
    if "identity_reference" not in args:
        body.pop("identity_reference", None)  # Preserve all pre-reference retry hashes.
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
    meta, body = store.parse_frontmatter(raw)
    from brain import lifecycle
    out["lifecycle"] = lifecycle.from_meta(meta)
    out["body"] = body.strip()
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


def frontmatter_json(value, **kwargs) -> str:
    """Keep JSON on one physical line for the frontmatter splitlines reader."""
    text = json.dumps(value, ensure_ascii=False, **kwargs)
    for char in ("\u0085", "\u2028", "\u2029"):
        text = text.replace(char, "\\u%04x" % ord(char))
    return text


def frontmatter_scalar(value: str) -> str:
    """Flatten every splitlines separator without changing other scalar bytes."""
    return value.translate({ord(c): " " for c in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"})


def render(name: str, args: dict, verified_at: str) -> str:
    """The file — the legacy shape with the contract's fields added in the frontmatter."""
    lines = ["---", "name: %s" % name, "description: %s" % frontmatter_scalar(args["description"]),
             "verified_at: %s" % verified_at]
    if args.get("extracted_from") or "embed" in args:
        lines.insert(1, "embed: " + ("false" if args.get("extracted_from") or args["embed"] is False else "true"))
    if args.get("extracted_from"):
        lines.append("extracted_from: " + frontmatter_json(args["extracted_from"], sort_keys=True))
    if args.get("identity_reference"):
        lines.append("identity_reference: " + frontmatter_json(args["identity_reference"], sort_keys=True))
    if args.get("lifecycle"):
        lines.append("lifecycle: " + frontmatter_json(args["lifecycle"], sort_keys=True))
    if args.get("idempotency_key"):
        lines.append("idempotency_key: %s" % args["idempotency_key"])
        lines.append("payload_sha: %s" % payload_sha(args))
    if args.get("project_id"):
        lines.append("project_id: %s" % args["project_id"].strip())
    if args.get("source"):
        lines.append("source: %s" % frontmatter_scalar(args["source"]))
    al = aliases(args)
    if al:
        lines.append("search_aliases: " + frontmatter_json(al))
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
        "lifecycle": args.get("lifecycle") or cur.get("lifecycle") or None,
        "extracted_from": args.get("extracted_from"),
        "identity_reference": args.get("identity_reference"),
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


# Extraction is a proposal, not replacement authority over a mixed source note.
def extraction_shape(args):
    value = args.get("extracted_from")
    if args.get("embed") is True:
        return "Extracted proposals must keep embed:false"
    if not isinstance(value, dict) or set(value) != {"name", "sha", "spans"}:
        return "extracted_from requires name, sha and spans"
    if not isinstance(value["name"], str) or not 1 <= len(value["name"]) <= 200 or value["name"] == args.get("name"):
        return "Use a distinct source name of at most 200 characters"
    if not isinstance(value["sha"], str) or not _SHA.fullmatch(value["sha"]):
        return "Pin the source SHA returned when read"
    spans = value["spans"]
    if not isinstance(spans, list) or not 1 <= len(spans) <= 10:
        return "Provide 1..10 exact source spans"
    for span in spans:
        if not isinstance(span, dict) or set(span) != {"start", "end", "text"}:
            return "Each span requires start, end and text"
        if (type(span["start"]) is not int or type(span["end"]) is not int or span["start"] < 0
                or not isinstance(span["text"], str) or not span["text"].strip()
                or not 1 <= len(span["text"]) <= 1000 or span["end"]-span["start"] != len(span["text"])):
            return "Spans use nonnegative Unicode code-point offsets and 1..1000 exact characters"
    meta = args.get("lifecycle") or {}
    if (meta.get("status") != "proposed" or meta.get("supersedes") or meta.get("superseded_by")
            or meta.get("legacy_whole_record_sha")):
        return "An extracted fact must remain proposed and cannot retire its source or any other memory"
    return None


def extraction_source(db, name, sha, scope):
    from brain import lifecycle
    snap = lifecycle.snapshot(db)
    ids = snap.by_name.get((scope, name), [])
    if len(ids) != 1:
        return None, error("source_unavailable", "Select one indexed source in the exact project_id scope")
    row = snap.docs[ids[0]]
    try:
        actual = current(row["path"])
    except (OSError, UnicodeError):
        return None, error("source_unavailable", "The source cannot be read")
    if row["sha"] != sha or actual["sha"] != sha:
        return None, error("source_changed", "Read the current source and refresh its index before preparing again")
    return actual, None


def check_extraction(db, args):
    value = args.get("extracted_from")
    if value is None:
        return None
    source, bad = extraction_source(db, value["name"], value["sha"], args.get("project_id") or "")
    if bad:
        return bad
    for span in value["spans"]:
        if source["body"][span["start"]:span["end"]] != span["text"]:
            return error("quote_mismatch", "Every span must exactly match the pinned source body")
    return None


def identity_reference_shape(value):
    if (not isinstance(value, dict) or set(value) != {"name", "sha", "reason"}
            or not isinstance(value["name"], str) or not 1 <= len(value["name"]) <= 200
            or not isinstance(value["sha"], str) or not _SHA.fullmatch(value["sha"])
            or not isinstance(value["reason"], str) or not value["reason"].strip() or len(value["reason"]) > 500):
        return "identity_reference requires name, sha and an explicit reason of 1..500 characters"
    return None


def check_identity(db, args):
    """New writes must still have the uniquely selected, valid, pinned identity."""
    value = args.get("identity_reference")
    if value is None:
        return None
    record, bad = identity_record(db, value["name"], value["sha"], args.get("project_id") or "")
    if bad:
        return bad
    if any(args["lifecycle"].get(k) != record["meta"][k] for k in ("topic", "facet")):
        return error("identity_mismatch", "The proposal must preserve the selected identity verbatim")
    return None


def identity_record(db, name, sha, scope, snap=None, cyclic=None):
    """Resolve an explicitly reviewed, version-pinned identity; never infer sameness."""
    from brain import lifecycle, memoryreview
    snap = lifecycle.snapshot(db) if snap is None else snap
    if cyclic is None:
        cyclic = memoryreview._cycles(snap, {did for did,r in snap.docs.items() if r["project_id"] == scope})
    ids = snap.by_name.get((scope, name), [])
    if len(ids) != 1:
        return None, error("identity_unavailable", "Select one indexed identity in the exact project_id scope")
    record = snap.docs[ids[0]]
    if (not record["meta"] or lifecycle.validate(record["meta"]) or ids[0] in snap.issues
            or ids[0] in cyclic):
        return None, error("invalid_identity", "Use a tracked identity with valid metadata and revision links")
    try:
        actual_sha = file_sha(record["path"])
    except (OSError, UnicodeError):
        actual_sha = None
    if sha != record["sha"] or actual_sha != sha:
        return None, error("identity_changed", "Read the identity source and refresh its index before choosing again")
    return record, None


def review_identity(db, args):
    """Compare caller-selected candidates without translation, ranking or merging."""
    from brain import lifecycle, memoryreview
    if not isinstance(args, dict) or set(args) - {"names", "project_id"}:
        return error("invalid_argument", "Use only declared identity review fields")
    names, scope = args.get("names"), args.get("project_id", "")
    if (not isinstance(names, list) or not 1 <= len(names) <= 10
            or any(not isinstance(n, str) or not n.strip() or len(n) > 200 for n in names)
            or len(set(names)) != len(names) or not isinstance(scope, str) or len(scope) > PROJECT_MAX):
        return error("invalid_argument", "Provide 1..10 distinct names and one exact project_id scope; omitted means unscoped only")
    db.execute("SAVEPOINT identity_review")
    try:
        snap = lifecycle.snapshot(db)
        cyclic = memoryreview._cycles(snap, {did for did,r in snap.docs.items() if r["project_id"] == scope})
        items, groups = [], {}
        at = lifecycle.now()
        with lifecycle.view("current", at):
            for name in names:
                ids = snap.by_name.get((scope, name), [])
                if len(ids) != 1:
                    items.append(dict(name=name, reusable=False, issue="identity_unavailable"))
                    continue
                row = snap.docs[ids[0]]
                record, bad = identity_record(db, name, row["sha"], scope, snap, cyclic)
                info = snap.info(ids[0])
                body = db.execute("SELECT body FROM docs WHERE id=?", (ids[0],)).fetchone()[0] or ""
                item = dict(name=name, sha=row["sha"], reusable=bad is None,
                            issue=bad["error"]["code"] if bad else None,
                            topic=row["meta"].get("topic"), facet=row["meta"].get("facet"),
                            status=info["status"], current=info["current"], body=body[:1000], body_truncated=len(body)>1000)
                for key in ("valid_from", "valid_to"):
                    item[key] = row["meta"].get(key)
                item["change_reason"] = (row["meta"].get("change_reason") or "")[:500]
                evidence = row["meta"].get("evidence", [])
                item["evidence"] = [v[:400] for v in evidence[:3]]
                item["evidence_total"] = len(evidence)
                item["evidence_truncated"] = len(evidence)>3 or any(len(v)>400 for v in evidence[:3])
                peers = [(snap.docs[n]["name"], snap.info(n)) for n in snap.fact_members(ids[0])]
                for key, predicate in (("current_fact_names", lambda v: v["current"] is True),
                                       ("conflicting_fact_names", lambda v: v["status"] == "disputed")):
                    values = sorted(n for n, state in peers if predicate(state))
                    item[key], item[key+"_total"] = values[:10], len(values)
                for key in ("current_names", "conflicts", "predecessors", "successors"):
                    item[key] = info[key][:10]
                    item[key+"_total"] = len(info[key])
                items.append(item)
                if record:
                    groups.setdefault(lifecycle.identity(record["meta"]), []).append(name)
        return dict(ok=True, stored=False, scope=scope, as_of=at, items=items,
                    groups=[dict(topic=k[0], facet=k[1], names=v) for k,v in groups.items()],
                    coverage="Only supplied candidates in one exact scope. Groups reflect recorded NFC identities, not semantic equivalence, truth or current applicability.",
                    next="Read complete sources and memory_history before choosing. For the same independently changing fact, pass identity_reference with name, sha and your reason to prepare_fact. Different subjects or facets need separate identities. No existing identities or histories are merged.")
    finally:
        db.execute("RELEASE identity_review")


def prepare_fact(db, args):
    """Keep reference selection and the fact guard on one SQLite read snapshot."""
    db.execute("SAVEPOINT fact_preparation")
    try:
        return _prepare_fact(db, args)
    finally:
        db.execute("RELEASE fact_preparation")


def _prepare_fact(db, args):
    """Prepare one host-authored proposal grounded in unique verbatim source passages."""
    from brain import lifecycle
    fields = {"source_name", "source_sha", "quotes", "name", "description", "type", "body", "project_id", "topic", "facet", "identity_reference"}
    if not isinstance(args, dict) or set(args) - fields:
        return error("invalid_argument", "Use only declared fact preparation fields")
    name, sha, quotes = args.get("source_name"), args.get("source_sha"), args.get("quotes")
    if (not isinstance(name, str) or not name.strip() or len(name) > 200
            or not isinstance(sha, str) or not _SHA.fullmatch(sha)
            or not isinstance(quotes, list) or not 1 <= len(quotes) <= 10
            or any(not isinstance(q, str) or not q.strip() or len(q) > 1000 for q in quotes)):
        return error("invalid_argument", "Provide source_name, source_sha and 1..10 verbatim quotes of at most 1000 characters")
    save = {k: args[k] for k in ("name", "description", "type", "body", "project_id") if k in args}
    selected = None
    reference = args.get("identity_reference")
    if "identity_reference" in args:
        issue = identity_reference_shape(reference)
        if issue:
            return error("invalid_argument", issue)
        bad = validate(save)
        if bad:
            return bad
        selected, bad = identity_record(db, reference["name"], reference["sha"], save.get("project_id") or "")
        if bad:
            return bad
        for key in ("topic", "facet"):
            if key in args and args[key] != selected["meta"][key]:
                return error("identity_mismatch", "Omit topic/facet or reuse the selected identity verbatim; translations cannot redefine it")
    save["lifecycle"] = dict(topic=selected["meta"]["topic"] if selected else args.get("topic"),
                             facet=selected["meta"]["facet"] if selected else args.get("facet"), status="proposed")
    bad = validate(save)
    if bad:
        return bad
    if save["name"] == name:
        return error("invalid_argument", "Keep the mixed source; use a distinct new fact name")
    scope = save.get("project_id") or ""
    snap = lifecycle.snapshot(db)
    if snap.by_name.get((scope, save["name"])):
        return error("name_taken", "Choose an unused fact name; retry the previous save request if already saved")
    source, bad = extraction_source(db, name, sha, scope)
    if bad:
        return bad
    spans = []
    for quote in quotes:
        start = source["body"].find(quote)
        if start < 0:
            return error("quote_mismatch", "Quotes must match the source body verbatim, without translation or normalisation")
        if source["body"].find(quote, start+1) >= 0:
            return error("ambiguous_quote", "Include enough surrounding source text to identify one passage")
        spans.append(dict(start=start, end=start+len(quote), text=quote))
    save["extracted_from"] = dict(name=name, sha=sha, spans=spans)
    save["links"] = [name]
    save["embed"] = False
    save["lifecycle"]["evidence"] = ["%s@%s [%s:%s]: %s" % (name, sha, s["start"], s["end"], s["text"]) for s in spans]
    if selected:
        save["lifecycle"]["evidence"].append("Identity selected: " + json.dumps(reference, ensure_ascii=False, sort_keys=True))
        if reference["name"] not in save["links"]:
            save["links"].append(reference["name"])
    bad = validate(save)
    if bad:
        return bad
    review = lifecycle.prepare(db, save["name"], save)
    if not review["source_matches_index"]:
        return error("index_pending", "Refresh changed fact sources before preparing again")
    save["expected_fact_sha"] = review["expected_fact_sha"]
    if selected:
        save["identity_reference"] = dict(reference)
    bad = validate(save)
    if bad:
        return bad
    return dict(review, save_tool="remember", save_arguments=save,
                extraction={"source_name": name, "source_sha": sha, "spans": spans, "status": "proposed"},
                identity_reference=dict(reference) if selected else None,
                next="Review whether the passages support one independent claim. Quotation matching proves provenance, not truth or completeness. Save proposals individually; this is not a batch transaction. The original remains searchable and is never superseded. Confirmation requires a new explicit revision with effective time and evidence.")


def requires_local_only(db, args):
    """An explicit source opt-out must not disappear when a new revision is saved."""
    from brain import lifecycle, privacy
    snap = lifecycle.snapshot(db)
    scope = args.get("project_id") or ""
    for name in (args.get("lifecycle") or {}).get("supersedes", []):
        for did in snap.by_name.get((scope, name), []):
            if privacy.is_local_only(snap.docs[did]["path"]):
                return True
    return False
