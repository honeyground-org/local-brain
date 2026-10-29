"""★What this brain supports — read live, never copied from a constant★ (contract `orbit-1`, 2026-10-29).

## Why this module exists

A client (Cosmos Orbit, or any agent) connects, reads this answer once and then trusts it: it keeps a
memory in its outbox instead of writing when `remember.idempotency_key` is false, parses JSON only when
`recall.formats` lists it, and filters by project only when `recall.scope` is true. A feature advertised
while its code path is missing turns a careful client into one that writes where it must not — the one
failure the contract exists to prevent. So every value here is ★derived at call time★:

  · versions from the modules that own them (server · ruler · store · tokenizer)
  · the engine, the stores and the corpus from what is configured and indexed right now
  · ★per-tool feature flags from the tool declarations themselves★ (`tools/list` is what a host sees;
    a property that is not declared cannot be called, so the declaration is the one honest source)
  · `unsupported` from the flags — the two cannot disagree (§tests/verify_capabilities.py)

Nothing here is personal: it runs on an empty home.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional

CONTRACT = "orbit-1"

# The lanes recall can take — documented facts of §search / §server, listed so a client can name them.
RECALL_LANES = ("words", "bridged", "fused", "meaning", "graph")


def _props(tools: List[dict], name: str) -> Dict[str, Any]:
    for t in tools:
        if t.get("name") == name:
            return dict((t.get("inputSchema") or {}).get("properties") or {})
    return {}


def _formats(props: Dict[str, Any]) -> List[str]:
    fmt = props.get("format") or {}
    enum = fmt.get("enum") if isinstance(fmt, dict) else None
    out = ["text"]
    if isinstance(enum, list) and "json" in enum:
        out.append("json")
    return out


def tool_flags(tools: List[dict]) -> Dict[str, Any]:
    """Per-tool features, ★decided by the declaration★ — add a property to the schema and the flag moves."""
    rc, rm, fb, an = _props(tools, "recall"), _props(tools, "remember"), _props(tools, "brain_feedback"), \
        _props(tools, "brain_analyse")
    signals = ((fb.get("signal") or {}).get("enum") if isinstance(fb.get("signal"), dict) else None) or []
    return {
        "recall": {
            "formats": _formats(rc),
            "scope": "scope" in rc,
            "request_id": "request_id" in rc,
            "k_default": 8,
            "k_max": None,                       # no limit is enforced — reported as such, not invented
            "semantic_k": "semantic_k" in rc,
            "lanes": list(RECALL_LANES),
            "budget_ms": "budget_ms" in rc,
        },
        "remember": {
            "formats": _formats(rm),
            "idempotency_key": "idempotency_key" in rm,
            "expected_sha": "expected_sha" in rm,
            "project_id": "project_id" in rm,
            "source": "source" in rm,
            "observed_at": "observed_at" in rm,
            "embed_now": "embed_now" in rm,
            "ack": (["stored", "indexed_words", "embedded", "served"] if "json" in _formats(rm) else []),
        },
        "feedback": {
            "signals": list(signals),
            "extended_signals": any(s in signals for s in ("delivered", "used", "verified")),
        },
        "analyse": {
            "paginated": {"offset", "limit"} <= set(an),
            "revision": True,                    # brain_view answers with a revision number (§observatory)
            "corpus_fingerprint": False,         # not implemented — a client must not assume one
        },
    }


def unsupported(flags: Dict[str, Any]) -> List[str]:
    """The explicit list a client reads first — every entry is a flag that is false, nothing else."""
    out: List[str] = []
    rc, rm = flags["recall"], flags["remember"]
    if "json" not in rc["formats"]:
        out.append("recall.format=json")
    for k in ("scope", "request_id", "budget_ms"):
        if not rc[k]:
            out.append("recall." + k)
    if "json" not in rm["formats"]:
        out.append("remember.format=json")
    for k in ("idempotency_key", "expected_sha", "project_id", "source", "observed_at", "embed_now"):
        if not rm[k]:
            out.append("remember." + k)
    if not flags["feedback"]["extended_signals"]:
        out.append("feedback.delivered|opened|used|verified")
    if not flags["analyse"]["corpus_fingerprint"]:
        out.append("analyse.corpus_fingerprint")
    return out


def _embed() -> Dict[str, Any]:
    from brain import engines, vectors
    c = engines.choice("embed")
    out: Dict[str, Any] = {"provider": c.get("provider"), "model": c.get("model"),
                           "local": bool(engines.is_local(c)), "dim": vectors.DIM,
                           "space": None, "ready": False, "reason": "", "refresh": _refresh()}
    try:
        out["space"] = vectors.model_tag()
        out["ready"] = bool(engines.available("embed"))
    except Exception as exc:                            # noqa: BLE001 — "not ready" with its reason, never a crash
        out["reason"] = str(exc)[:200]
    return out


def _refresh() -> str:
    """When new memories get their meaning vectors — the scheduled job, read from the scheduler."""
    try:
        from brain import health
        for j in health.schedules().get("jobs", []):
            if j.get("job") == "brain-vec-daily":
                at = j.get("at") or []
                when = ("daily %02d:%02d" % (at[0], at[1])) if len(at) == 2 else "daily"
                return when + (" (registered)" if j.get("registered") else " (not registered)") + \
                    " — `brain vec build` to force"
    except Exception:                                   # noqa: BLE001
        pass
    return "no scheduled job — `brain vec build` to force"


def _judge() -> Dict[str, Any]:
    from brain import engines
    c = engines.choice("judge")
    return {"provider": c.get("provider"), "model": c.get("model"),
            "used_by": ["hook stage 2", "brain score --full", "rules discovery"],
            "used_by_recall": False}


def _stores(db: sqlite3.Connection) -> Dict[str, Any]:
    """Both roles, always — a client plans around `vector` and `graph` whether or not they answer today.
    When the health view cannot be built (an engine manifest missing, a database down), each role still
    names its chosen backend with `ok: false` and the reason, instead of the block disappearing."""
    out: Dict[str, Any] = {}
    err = ""
    try:
        from brain import health
        out = dict(health.stores_state(db) or {})
    except Exception as exc:                            # noqa: BLE001
        err = str(exc)[:200]
    from brain import stores as _stores_mod
    for role in ("vector", "graph"):
        if isinstance(out.get(role), dict):
            continue
        try:
            backend = _stores_mod.choice(role).get("backend")
        except Exception as exc:                        # noqa: BLE001
            backend, err = None, err or str(exc)[:200]
        out[role] = {"backend": backend, "ok": False, "in_sync": False, "error": err or "unavailable"}
    return out


def _corpus(db: sqlite3.Connection, memory_dir: str) -> Dict[str, Any]:
    from brain import store
    try:
        st = store.corpus_stats(db)
    except Exception:                                   # noqa: BLE001
        st = {}
    try:
        names = [s.get("name") for s in (store.load_config().get("sources") or []) if s.get("name")]
    except Exception:                                   # noqa: BLE001
        names = []
    try:
        kinds = [r[0] for r in db.execute(
            "SELECT kind, COUNT(*) n FROM docs WHERE kind != '' GROUP BY kind ORDER BY n DESC LIMIT 12")]
    except sqlite3.Error:
        kinds = []
    try:
        docs = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    except sqlite3.Error:
        docs = st.get("docs", 0)
    return {"docs": docs, "last_index": st.get("last_index") or store.get_meta(db, "last_index"),
            "sources": names, "kinds": kinds, "memory_dir": memory_dir}


def describe(db: sqlite3.Connection, tools: List[dict], protocol: str, memory_dir: str,
             shown: int) -> Dict[str, Any]:
    """The whole answer. `tools` is the declaration the host was given (§server.tools_for)."""
    from brain import calibrate, hook, store, textindex
    flags = tool_flags(tools)
    try:
        db_schema: Optional[str] = store.get_meta(db, "schema_version") or None
    except Exception:                                   # noqa: BLE001
        db_schema = None
    from brain import server as _srv
    return {
        "contract": CONTRACT,
        "server": {"name": _srv.SERVER_NAME, "version": _srv.SERVER_VERSION, "protocol": protocol,
                   "code_generation": calibrate.CODE_GENERATION,
                   "schema_version": store.SCHEMA_VERSION, "db_schema_version": db_schema,
                   "tokenizer_version": textindex.TOKENIZER_VERSION},
        "embed": _embed(),
        "judge": _judge(),
        "stores": _stores(db),
        "corpus": _corpus(db, memory_dir),
        "tools": flags,
        "limits": {"excerpt_chars": shown,
                   "local_embed_timeout_s": float(os.environ.get("BRAIN_LOCAL_EMBED_TIMEOUT", "5") or 5),
                   "hook_lexical_budget_s": hook.TIME_BUDGET_SEC,
                   "hook_wall_s": hook.HOOK_TIMEOUT_SEC,
                   "mcp_concurrency": "one request at a time per host process"},
        "unsupported": unsupported(flags),
    }
