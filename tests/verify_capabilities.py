#!/usr/bin/env python3
"""`brain_capabilities` — ★what a client is told must be what the code does★ (orbit-1, 2026-10-29).

## Why this check exists

A client (Cosmos Orbit, or any agent) reads this answer once after connecting and then trusts it: it
stores to an outbox instead of writing when `remember.idempotency_key` is false, and parses JSON only
when `recall.formats` says so. A flag that is true while the code path is missing turns a safe client
into one that writes where it should not — the exact failure the contract exists to prevent.

## What is held down

  ① the answer is read live — versions, corpus size and limits equal what the modules say right now
  ② ★the tool declaration decides a feature flag★ — add `scope` to recall's declared properties and
     the flag turns true; take it away and it turns false (the control: a flag that cannot move would
     always have passed)
  ③ every entry of `unsupported` is a feature whose flag is false — the list and the flags cannot disagree
  ④ the tool is in `tools/list` with no input, so a host can call it before it knows anything else

Runs with an empty home too (no personal data is read).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _needs  # noqa: E402
_needs.private_index()                 # ⛔ never the live index — a check must not touch what the hook reads

from brain import calibrate, capabilities, server, store, textindex  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + str(detail)[:160]) if detail else ""))
    if not cond:
        FAIL.append(label)


def main() -> int:
    db = store.connect()
    print("=" * 72 + "\n④ the tool is declared, and takes nothing\n" + "=" * 72)
    tools = server.tools_for(db)
    decl = [t for t in tools if t["name"] == "brain_capabilities"]
    check("brain_capabilities is in tools/list", len(decl) == 1, [t["name"] for t in tools])
    check("it declares no input", decl and decl[0]["inputSchema"].get("properties") == {}, decl and decl[0]["inputSchema"])

    print("\n" + "=" * 72 + "\n① read live, not copied\n" + "=" * 72)
    out = json.loads(server.call_tool(db, "brain_capabilities", {}))
    check("contract is orbit-1", out.get("contract") == "orbit-1", out.get("contract"))
    srv = out.get("server") or {}
    check("server version is the server's", srv.get("version") == server.SERVER_VERSION, srv)
    check("code generation is the ruler's", srv.get("code_generation") == calibrate.CODE_GENERATION, srv.get("code_generation"))
    check("schema version is the store's", srv.get("schema_version") == store.SCHEMA_VERSION, srv.get("schema_version"))
    check("tokenizer version is the tokenizer's", srv.get("tokenizer_version") == textindex.TOKENIZER_VERSION, srv.get("tokenizer_version"))
    n = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    check("corpus size is counted now", (out.get("corpus") or {}).get("docs") == n, (out.get("corpus") or {}).get("docs"))
    check("excerpt limit is the server's", (out.get("limits") or {}).get("excerpt_chars") == server.SHOWN, out.get("limits"))
    check("an embedding engine block is present with a readiness verdict",
          isinstance((out.get("embed") or {}).get("ready"), bool), out.get("embed"))
    check("stores are reported per role", set((out.get("stores") or {}).keys()) >= {"vector", "graph"}, list((out.get("stores") or {}).keys()))

    print("\n" + "=" * 72 + "\n② the declaration decides the flag (control)\n" + "=" * 72)
    base = capabilities.describe(db, tools=tools, protocol="x", memory_dir="", shown=server.SHOWN)
    recall_now = base["tools"]["recall"]
    modified = json.loads(json.dumps(tools))
    for t in modified:
        if t["name"] == "recall":
            t["inputSchema"]["properties"]["scope"] = {"type": "string"}
            t["inputSchema"]["properties"]["format"] = {"type": "string", "enum": ["text", "json"]}
        if t["name"] == "remember":
            t["inputSchema"]["properties"]["idempotency_key"] = {"type": "string"}
    with_it = capabilities.describe(db, tools=modified, protocol="x", memory_dir="", shown=server.SHOWN)
    check("declaring `scope` turns recall.scope true", with_it["tools"]["recall"]["scope"] is True, with_it["tools"]["recall"])
    check("declaring `format` adds json to recall.formats", "json" in with_it["tools"]["recall"]["formats"], with_it["tools"]["recall"]["formats"])
    check("declaring `idempotency_key` turns remember.idempotency_key true",
          with_it["tools"]["remember"]["idempotency_key"] is True, with_it["tools"]["remember"])
    stripped = json.loads(json.dumps(tools))
    for t in stripped:
        if t["name"] in ("recall", "remember"):
            for k in ("scope", "format", "idempotency_key", "request_id", "expected_sha", "project_id"):
                t["inputSchema"]["properties"].pop(k, None)
    without = capabilities.describe(db, tools=stripped, protocol="x", memory_dir="", shown=server.SHOWN)
    check("(control) with the properties removed, the flags are false and json is gone",
          without["tools"]["recall"]["scope"] is False and "json" not in without["tools"]["recall"]["formats"]
          and without["tools"]["remember"]["idempotency_key"] is False, without["tools"])
    check("the live answer equals the answer for the live declaration",
          out["tools"] == base["tools"], "")
    _ = recall_now

    print("\n" + "=" * 72 + "\n③ `unsupported` and the flags cannot disagree\n" + "=" * 72)
    for cap in (out, with_it, without):
        listed = set(cap.get("unsupported") or [])
        derived = set(capabilities.unsupported(cap["tools"]))
        check("unsupported is derived from the flags", listed == derived, sorted(listed ^ derived))
    check("a flag that is true is never listed as unsupported",
          "recall.scope" not in set(with_it["unsupported"]) and "recall.scope" in set(without["unsupported"]),
          (with_it["unsupported"], without["unsupported"]))

    print("\n" + "=" * 72)
    if FAIL:
        print("❌ failed: %d — %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
