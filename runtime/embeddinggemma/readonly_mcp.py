#!/usr/bin/env python3
"""Expose only recall/neighbors from an isolated Brain copy for a host comparison.

The caller chooses the checkout, configuration and embedding permission explicitly.
This bridge never creates embeddings through a host CLI or switches providers.
"""
from __future__ import annotations

import argparse
import datetime
import inspect
import json
import os
from pathlib import Path
import sys
import time
import uuid


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", required=True)
    p.add_argument("--home", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--manifest")
    p.add_argument("--dimension", type=int, choices=(128, 256, 512, 768))
    p.add_argument("--chunk-tokens", type=int)
    p.add_argument("--path-map", help="JSON object mapping original paths to frozen evidence paths")
    p.add_argument("--audit")
    p.add_argument("--host", choices=("claude-code", "codex", "generic"), required=True)
    p.add_argument("--allow-embedding", action="store_true", help="Use the explicitly configured provider on query cache misses")
    a = p.parse_args()
    home, root = Path(a.home).resolve(), Path(a.source_root).resolve()
    marker = home / "comparison-isolation.json"
    isolation = json.loads(marker.read_text(encoding="utf-8"))
    original = Path(isolation["source_index"]).resolve()
    target = home / "index.db"
    if not isolation.get("isolated") or not target.is_file() or original == target.resolve():
        p.error("an isolated SQLite backup and comparison-isolation.json are required")
    if original.exists() and os.path.samefile(str(original), str(target)):
        p.error("the evaluation index must not alias its source")
    if not (root / "brain" / "server.py").is_file():
        p.error("source-root must name the explicitly selected Brain checkout")
    for key in list(os.environ):
        if key.startswith("BRAIN_"):
            del os.environ[key]
    os.environ.update(BRAIN_HOME=str(home), BRAIN_CONFIG=str(Path(a.config).resolve()),
                      BRAIN_PERSONAL_DISABLED="1", BRAIN_HOST=a.host)
    if a.manifest:
        os.environ["BRAIN_EMBED_MANIFEST"] = str(Path(a.manifest).resolve())
    if a.dimension is not None:
        os.environ["BRAIN_EMBED_DIM"] = str(a.dimension)
    if a.chunk_tokens is not None:
        os.environ["BRAIN_EMBED_CHUNK_TOKENS"] = str(a.chunk_tokens)
    sys.path.insert(0, str(root))
    from brain import store, search, server, vectors, i18n
    search._record = lambda *args, **kwargs: None
    server._note_recall = lambda *args, **kwargs: None
    if not a.allow_embedding:
        def blocked(*args, **kwargs):
            raise RuntimeError("query embedding is cache-only in this comparison profile")
        vectors.embed = blocked
    db = store.connect()
    mapping = json.loads(Path(a.path_map).read_text(encoding="utf-8")) if a.path_map else {}
    replacements = sorted(mapping, key=len, reverse=True)
    measured = "_measure" in inspect.signature(server.call_tool).parameters
    run_id = uuid.uuid4().hex
    def note(value):
        if a.audit:
            value.update(run_id=run_id, utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
            with open(a.audit, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(value, ensure_ascii=False) + "\n")
    note({"event": "startup", "source_root": str(root), "home": str(home),
          "allow_embedding": a.allow_embedding})
    for line in sys.stdin:
        req = {}
        try:
            req = json.loads(line)
            if req.get("id") is None:
                continue
            method = req.get("method")
            if method == "initialize":
                result = {"protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "brain", "version": "1.0.0-readonly-comparison"},
                          "instructions": i18n.host_language_instruction()}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                tools = server.tools_for(db) if hasattr(server, "tools_for") else server.TOOLS
                result = {"tools": [t for t in tools if t["name"] in ("recall", "neighbors")]}
            elif method == "tools/call":
                params = req.get("params", {})
                name, args = params.get("name"), params.get("arguments") or {}
                if name not in ("recall", "neighbors"):
                    raise ValueError("comparison tools are read-only")
                start = time.perf_counter()
                cache = vectors._qcache_get(db, args.get("query", "")) is not None if name == "recall" else None
                output = server.call_tool(db, name, args, **({"_measure": True} if measured else {}))
                for original in replacements:
                    output = output.replace(original, mapping[original])
                note({"event": "call", "tool": name, "seconds": time.perf_counter()-start,
                      "query_cache_hit": cache, "returned_chars": len(output),
                      "vector": vectors.last_search() if hasattr(vectors, "last_search") else {}})
                result = {"content": [{"type": "text", "text": output}], "isError": False}
            else:
                raise ValueError("unsupported comparison method")
            response = {"jsonrpc": "2.0", "id": req["id"], "result": result}
        except Exception as exc:
            note({"event": "error", "error": type(exc).__name__})
            response = {"jsonrpc": "2.0", "id": req.get("id"),
                        "error": {"code": -32603, "message": type(exc).__name__}}
        print(json.dumps(response, ensure_ascii=False), flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
