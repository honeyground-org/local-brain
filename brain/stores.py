"""Storage backends — ★which database serves similarity search and graph traversal★, chosen by the person.

## Why (user instruction, 2026-10-07)

*"The vector DB and the graph DB must be replaceable with databases of that nature."* Until now both
lived only in the local SQLite file: embeddings as BLOBs scanned in full, links as a table joined by
name. That is the right default for one person's notes and the wrong ceiling for everyone else — a
team that already runs a vector database or a graph database should be able to point brain at it.

## The shape: the local copy stays, the dedicated database serves

SQLite keeps the ★canonical copy★ of both. Embeddings are earned (each one an API call) and the graph is
derived from your own files, so neither may live only on someone else's server. A dedicated database
is a ★serving index★ for the one capability it is built for:

  vector   similarity search over note embeddings  →  sqlite (default) · qdrant
  graph    traversal of the links between notes      →  sqlite (default) · neo4j

  · switching is a ★sync, not a re-embed★ — `brain stores --sync` pushes the local copy
  · a ★diff★ decides what is sent (a ledger of what each target already holds), so unchanged data
    costs no network, and an interrupted sync resumes where it stopped
  · the dedicated database answers only once it is ★known to be in sync★; before that, or when it
    does not answer, the local copy answers — and `brain stores` says so (never silently)
  · `brain stores --check` asks both the same questions and reports where they disagree

A new backend is one class in `vecstore.py` or `graphstore.py` and one row in `BACKENDS`.

## ⛔ Chosen, never guessed

Order: `BRAIN_<ROLE>_STORE` → `config.json` `"stores"` → `sqlite`. A database that happens to be running
is not a choice. Credentials come from the environment or `secrets.json` (0600), never from config.

## ⛔ What leaves this machine

  qdrant   the vectors and `{doc_id, chunk_no}` — no text
  neo4j    document ids and the links between them (document names only with `"names": true`, and then
           through `privacy.scrub` like every other outbound text — §graphstore.Neo4jGraph.upsert_nodes)

Standard library only, like the rest of brain: each adapter speaks the database's own HTTP API.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Iterable, List, Optional, Tuple

ROLES = ("vector", "graph")

# backend → the role it can fill, its usual address, and the secret it may need.
BACKENDS: Dict[str, dict] = {
    "sqlite": {"roles": ("vector", "graph"), "url": "", "secret_env": "", "secret_field": ""},
    "qdrant": {"roles": ("vector",), "url": "http://localhost:6333",
               "secret_env": "QDRANT_API_KEY", "secret_field": "qdrant_api_key"},
    "neo4j": {"roles": ("graph",), "url": "http://localhost:7474",
              "secret_env": "NEO4J_PASSWORD", "secret_field": "neo4j_password"},
}

QUERY_TIMEOUT = float(os.environ.get("BRAIN_STORE_TIMEOUT", "3") or 3)      # one search · one traversal
SYNC_TIMEOUT = float(os.environ.get("BRAIN_STORE_SYNC_TIMEOUT", "60") or 60)
DOWN_RETRY_SEC = 60.0      # after a failure, this process asks the local copy for this long


class StoreError(RuntimeError):
    """A dedicated database did not answer, or answered with an error."""


# ── which backend fills a role ─────────────────────────────────────────────
def _config_stores() -> dict:
    try:
        from brain import store
        return store.load_config(tolerant=True).get("stores") or {}
    except Exception:                                    # noqa: BLE001
        return {}


def choice(role: str) -> dict:
    """The backend filling `role`: {backend, url, source, options, error}. source = env | config | default."""
    env = role.upper()
    cfg = dict(_config_stores().get(role) or {})
    backend = (os.environ.get("BRAIN_%s_STORE" % env) or "").strip().lower()
    source = "env" if backend else ""
    if not backend and cfg.get("backend"):
        backend, source = str(cfg["backend"]).strip().lower(), "config"
    if not backend:
        backend, source = "sqlite", "default"
    spec = BACKENDS.get(backend)
    if spec is None or role not in spec["roles"]:
        return {"role": role, "backend": backend, "url": "", "source": source, "options": cfg,
                "error": "unknown"}
    url = (os.environ.get("BRAIN_%s_STORE_URL" % env) or cfg.get("url") or spec["url"]).rstrip("/")
    return {"role": role, "backend": backend, "url": url, "source": source, "options": cfg, "error": ""}


def is_local(c: dict) -> bool:
    """Does this backend keep everything on this machine."""
    if c.get("backend") == "sqlite":
        return True
    host = urllib.parse.urlsplit(c.get("url") or "").hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


def secret(c: dict) -> str:
    """The backend's credential: its variable → `secrets.json`. "" when there is none."""
    spec = BACKENDS.get(c.get("backend", ""), {})
    if not spec.get("secret_env"):
        return ""
    k = os.environ.get(spec["secret_env"], "").strip()
    if not k:
        try:
            from brain import engines
            with open(engines.secrets_path(), encoding="utf-8") as fh:
                k = (json.load(fh).get(spec["secret_field"]) or "").strip()
        except (OSError, ValueError):
            k = ""
    return k


def namespace() -> str:
    """Which brain this is, inside a database several brains may share — derived, never configured by hand."""
    from brain import store
    return hashlib.sha1(os.path.realpath(store.brain_home()).encode("utf-8")).hexdigest()[:10]


def set_choice(role: str, backend: str, url: str = "", options: Optional[dict] = None) -> dict:
    """Write one role's backend into config.json. ⛔ Only a person (the CLI) calls this."""
    from brain import store
    backend = backend.strip().lower()
    if role not in ROLES:
        raise ValueError("role must be one of %s" % ", ".join(ROLES))
    if backend not in BACKENDS or role not in BACKENDS[backend]["roles"]:
        ok = [n for n, s in BACKENDS.items() if role in s["roles"]]
        raise ValueError("%s cannot serve the %s role — choose one of %s" % (backend, role, ", ".join(ok)))
    cfg = store.load_config()             # ⛔ not tolerant — no config means not installed yet
    entry = {"backend": backend}
    if url:
        entry["url"] = url.rstrip("/")
    for k, v in (options or {}).items():
        if v not in (None, ""):
            entry[k] = v
    cfg.setdefault("stores", {})[role] = entry
    store.save_config(cfg)
    return entry


def parse_assignments(items: List[str]) -> Dict[str, str]:
    """`vector=qdrant graph=neo4j` → {role: backend}."""
    out = {}
    for it in items:
        role, _, backend = it.strip().lower().partition("=")
        if role not in ROLES or not backend:
            raise ValueError("expected role=backend, e.g. vector=qdrant — got %r" % it)
        out[role] = backend
    return out


# ── HTTP (standard library only) ───────────────────────────────────────────
def http_json(method: str, url: str, body=None, headers: Optional[Dict[str, str]] = None,
              timeout: float = QUERY_TIMEOUT) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    h = {"Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise StoreError("%s %s → HTTP %d %s" % (method, _redact(url), exc.code, detail)) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise StoreError("%s %s → %s" % (method, _redact(url), getattr(exc, "reason", exc))) from None
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise StoreError("%s %s → not JSON" % (method, _redact(url))) from None


def basic_auth(user: str, password: str) -> Dict[str, str]:
    tok = base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + tok}


def _redact(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.hostname or "", p.path, "", "")) if p.password else url


# ── the ledger: what each target already holds ─────────────────────────────
def _ensure_ledger(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS store_ledger("
               " role TEXT NOT NULL, target TEXT NOT NULL, key TEXT NOT NULL, sig TEXT NOT NULL,"
               " PRIMARY KEY(role, target, key)) WITHOUT ROWID")


def ledger(db: sqlite3.Connection, role: str, target: str) -> Dict[str, str]:
    _ensure_ledger(db)
    return {r[0]: r[1] for r in db.execute(
        "SELECT key, sig FROM store_ledger WHERE role=? AND target=?", (role, target))}


def ledger_apply(db: sqlite3.Connection, role: str, target: str,
                 upserts: Dict[str, str], deletes: Iterable[str] = ()) -> None:
    _ensure_ledger(db)
    with db:
        dl = [(role, target, k) for k in deletes]
        if dl:
            db.executemany("DELETE FROM store_ledger WHERE role=? AND target=? AND key=?", dl)
        if upserts:
            db.executemany("INSERT OR REPLACE INTO store_ledger(role,target,key,sig) VALUES(?,?,?,?)",
                           [(role, target, k, v) for k, v in upserts.items()])


def ledger_clear(db: sqlite3.Connection, role_prefix: str, target: str) -> None:
    _ensure_ledger(db)
    with db:
        db.execute("DELETE FROM store_ledger WHERE role LIKE ? AND target=?", (role_prefix + "%", target))


# ── health: is the target in sync, and did it answer ────────────────────────
def _health_key(role: str) -> str:
    return "store_health_" + role


def health(db: sqlite3.Connection, role: str) -> dict:
    from brain import store
    try:
        h = json.loads(store.get_meta(db, _health_key(role), "") or "{}")
    except ValueError:
        h = {}
    return h if isinstance(h, dict) else {}


def record(db: sqlite3.Connection, role: str, **fields) -> None:
    from brain import store
    h = health(db, role)
    h.update(fields)
    h["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with db:
            store.set_meta(db, _health_key(role), json.dumps(h, ensure_ascii=False))
    except sqlite3.Error:
        pass                                             # a status note must never block an answer


_DOWN: Dict[str, float] = {}


def serve(db: sqlite3.Connection, role: str, target: str, remote: Callable[[], object],
          local: Callable[[], object]):
    """Ask the dedicated database — ★only if it is known to hold this target in sync★ — else the local copy.

    ⛔ A failure is recorded (so `brain stores` shows it) and this process uses the local copy for
       `DOWN_RETRY_SEC`, so a database that is down costs one timeout, not one per question.
    """
    h = health(db, role)
    if h.get("synced") != target or time.time() - _DOWN.get(role, 0.0) < DOWN_RETRY_SEC:
        return local()
    try:
        return remote()
    except StoreError as exc:
        _DOWN[role] = time.time()
        lost = {"synced": ""} if "HTTP 404" in str(exc) else {}   # it no longer holds this copy — the next sync heals it
        record(db, role, ok=False, error=str(exc)[:300], fallbacks=int(h.get("fallbacks") or 0) + 1, **lost)
        return local()


# ── the two roles, built from their choice ─────────────────────────────────
def vector_backend(c: Optional[dict] = None):
    from brain import vecstore
    return vecstore.make(c or choice("vector"))


def graph_backend(c: Optional[dict] = None):
    from brain import graphstore
    return graphstore.make(c or choice("graph"))


def sync(db: sqlite3.Connection, roles: Tuple[str, ...] = ROLES, full: bool = False,
         progress: bool = False) -> Dict[str, dict]:
    """Push the local copy to every chosen dedicated database. Never raises — the error is the result."""
    out: Dict[str, dict] = {}
    for role in roles:
        c = choice(role)
        if c["backend"] == "sqlite" or c.get("error"):
            out[role] = {"backend": c["backend"], "skipped": "local" if not c.get("error") else "unknown"}
            continue
        try:
            if role == "vector":
                from brain import vecstore
                out[role] = vecstore.sync(db, vector_backend(c), full=full, progress=progress)
            else:
                from brain import graphstore
                out[role] = graphstore.sync(db, graph_backend(c), full=full, progress=progress)
            record(db, role, ok=True, error="", synced=out[role]["target"])
            _DOWN.pop(role, None)
        except StoreError as exc:
            record(db, role, ok=False, error=str(exc)[:300])
            out[role] = {"backend": c["backend"], "error": str(exc)[:300]}
    return out


def sync_if_chosen(db: sqlite3.Connection, roles: Tuple[str, ...] = ROLES) -> Dict[str, dict]:
    """The cheap call indexing makes — nothing at all unless a dedicated database is chosen."""
    wanted = tuple(r for r in roles if choice(r)["backend"] != "sqlite")
    return sync(db, wanted) if wanted else {}


# ── what the screens show ──────────────────────────────────────────────────
def _ledger_role(role: str) -> str:
    return "vector" if role == "vector" else "graph.node"


def describe(db: sqlite3.Connection) -> List[dict]:
    """One row per role: the choice, whether the target answers, what it holds, and whether it serves."""
    from brain import vectors
    rows = []
    for role in ROLES:
        c = choice(role)
        row = dict(c, local=is_local(c), health=health(db, role))
        if role == "vector":
            vectors.ensure_table(db)
            row["local_count"] = db.execute(
                "SELECT COUNT(DISTINCT v.doc_id) FROM vectors v JOIN docs d ON d.id=v.doc_id "
                "WHERE v.model=? AND v.dim=?", (vectors.model_tag(), vectors.DIM)).fetchone()[0]
        else:
            row["local_count"] = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        if c["backend"] != "sqlite" and not c.get("error"):
            be = vector_backend(c) if role == "vector" else graph_backend(c)
            target = be.target(vectors.model_tag(), vectors.DIM) if role == "vector" else be.target()
            row["target"] = target
            row["held"] = len(ledger(db, _ledger_role(role), target))
            row["in_sync"] = row["health"].get("synced") == target
            try:
                row["ping"] = be.ping()
            except StoreError as exc:
                row["ping"] = {"ok": False, "error": str(exc)[:200]}
        rows.append(row)
    return rows


def check(db: sqlite3.Connection, roles: Tuple[str, ...] = ROLES, sample: int = 40) -> Dict[str, dict]:
    """★Ask the local copy and the dedicated database the same questions★ and say where they disagree.

    vector  the last `sample` cached questions (no embedding call): top-1 agreement, overlap of the top 10,
            the largest score difference, and how long each took
    graph   every document's one-hop neighbours, every edge, every link target
    """
    out: Dict[str, dict] = {}
    for role in roles:
        c = choice(role)
        if c["backend"] == "sqlite" or c.get("error"):
            out[role] = {"backend": c["backend"], "skipped": "local"}
            continue
        try:
            out[role] = _check_vector(db, c, sample) if role == "vector" else _check_graph(db, c)
        except StoreError as exc:
            out[role] = {"backend": c["backend"], "error": str(exc)[:300]}
    return out


def _check_vector(db: sqlite3.Connection, c: dict, sample: int) -> dict:
    from brain import vectors, vecstore
    be, local = vecstore.make(c), vecstore.SqliteVectors()
    model, dim = vectors.model_tag(), vectors.DIM
    try:
        qs = [vecstore._unpack(r[0]) for r in db.execute(
            "SELECT vec FROM vec_qcache WHERE model=? AND dim=? ORDER BY rowid DESC LIMIT ?", (model, dim, sample))]
    except sqlite3.Error:
        qs = []
    top1 = 0
    overlap = delta = t_local = t_store = 0.0
    for q in qs:
        t = time.perf_counter()
        a = local.search(db, q, model, dim, 10)
        t_local += time.perf_counter() - t
        t = time.perf_counter()
        b = be.search(db, q, model, dim, 10)
        t_store += time.perf_counter() - t
        top1 += 1 if a[:1] and b[:1] and a[0][0] == b[0][0] else 0
        overlap += len({d for d, _ in a} & {d for d, _ in b}) / float(max(1, len(a)))
        got = dict(b)
        delta = max([delta] + [abs(s - got[d]) for d, s in a if d in got])
    n = len(qs)
    return {"backend": c["backend"], "target": be.target(model, dim), "queries": n, "top1_agree": top1,
            "overlap_at_10": round(overlap / n, 4) if n else None, "max_score_delta": round(delta, 6),
            "ms_local": round(1000 * t_local / n, 1) if n else None,
            "ms_store": round(1000 * t_store / n, 1) if n else None,
            "chunks_local": local.count(db, model, dim), "chunks_store": be.count(db, model, dim)}


def _check_graph(db: sqlite3.Connection, c: dict) -> dict:
    from brain import graphstore
    be, local = graphstore.make(c), graphstore.LOCAL
    ids = [r[0] for r in db.execute("SELECT id FROM docs ORDER BY id")]
    t = time.perf_counter()
    a = local.neighbors(db, ids)
    t_local = time.perf_counter() - t
    t = time.perf_counter()
    b = be.neighbors(db, ids)
    t_store = time.perf_counter() - t
    same = sum(1 for i in ids if a.get(i, set()) == b.get(i, set()))
    ea, eb = set(local.edges(db)), set(be.edges(db))
    return {"backend": c["backend"], "target": be.target(), "docs": len(ids), "neighbors_same": same,
            "edges_local": len(ea), "edges_store": len(eb), "edges_same": ea == eb,
            "targets_same": local.targets(db) == be.targets(db),
            "ms_local": round(1000 * t_local, 1), "ms_store": round(1000 * t_store, 1)}
