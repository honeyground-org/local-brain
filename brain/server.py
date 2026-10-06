"""MCP server — the door a session reaches the brain through. ★0 dependencies★ (standard library only).

Why no SDK: MCP stdio is line-delimited JSON-RPC 2.0 and we use exactly three methods —
`initialize` · `tools/list` · `tools/call`. Add the SDK and it no longer runs directly on this
machine's system python (3.9) — a venv and a boot delay come with it. Once a tool gets heavy it
stops being used, and a brain nobody uses is no brain at all.

⛔ **stdout is protocol-only** — one stray print breaks the session's MCP connection.
   Every log line goes to stderr instead.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Any, Dict, List

from brain import health, search, store

SERVER_NAME = "brain"
SERVER_VERSION = "1.0.0"
PROTOCOL_FALLBACK = "2024-11-05"

# ⛔ ★Nothing in a tool description may be one person's★ (2026-10-06) — this text is sent to every
#    agent on every machine. It used to quote the author's own measurement ("2 of 9 → 8 of 9") as a
#    fact about the tool, assume Korean/English notes, and give the author's memory name as the
#    example slug. The languages and kinds below are read off ★this★ corpus at `tools/list`.
_RECALL_DESC = (
    "Recalls from personal memory, domain documents, wikis, and project rules (⛔). "
    "Call it when: referencing a past decision, lesson, or measurement, "
    "when a phrase like 'last time', 'what we did before', or 'what was that again' comes up, "
    "when checking a project rule or prohibition, when trying not to repeat the same mistake. "
    "⛔ **Always pass synonyms in `terms`** — a memory is usually written in different words than "
    "the question (a question about 'stopped' → a memory that says 'halted'), and a question with no "
    "word in common finds nothing. "
    "Give 5~10 other spellings{langs}, abbreviations, and related identifiers."
)
_LANG_EN = {"en": "English", "ko": "Korean", "de": "German", "es": "Spanish",
            "fr": "French", "ja": "Japanese"}

_REMEMBER_DESC = (
    "Writes a new memory to a file and indexes it immediately. Keep only what stays true later "
    "(never a point-in-time number or a one-off state). "
    "Connect related memories with `links` — with no connection, one is unreachable when the words do not match."
)

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "recall",
        "description": _RECALL_DESC,
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "the natural-language question, as-is"},
                "terms": {"type": "array", "items": {"type": "string"},
                          "description": "5~10 synonyms, Latin spellings, related identifiers. Omit it and performance drops sharply."},
                "k": {"type": "integer", "description": "number of results (default 8)"},
                "sources": {"type": "array", "items": {"type": "string"},
                            "description": "narrow to memory | docs | wiki | guide"},
                "kinds": {"type": "array", "items": {"type": "string"},
                          "description": "narrow to kinds of memory (the frontmatter `type`){kinds}"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "remember",
        "description": _REMEMBER_DESC,
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "a kebab/snake slug, e.g. deploy_needs_the_migration_first"},
                "description": {"type": "string", "description": "a one-line summary — this is what recall shows when picking"},
                "type": {"type": "string",
                         "description": "the kind of memory (written as the frontmatter `type`){kinds}"},
                "body": {"type": "string", "description": "the body (markdown). Write why it matters and how to apply it"},
                "links": {"type": "array", "items": {"type": "string"},
                          "description": "related memory names — stored as [[links]]"},
            },
            "required": ["name", "description", "type", "body"],
        },
    },
    {
        "name": "neighbors",
        "description": "Shows one memory's graph neighbours (incoming/outgoing links · broken links). "
                       "Use it starting from a recall result to widen the related context.",
        "inputSchema": {"type": "object",
                        "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "name": "brain_status",
        "description": "Brain status + ★memory-distortion diagnosis★ (orphans · broken links · stale memories · duplicate candidates · "
                       "never-recalled memories). Use it when tidying memory or checking how much to trust it.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "timeline",
        "description": "★the answer for a question with no topic word★ — \"check the earlier conversation\" · \"what did we do last time\" · "
                       "\"what changed last week\" never says what it is about, and `recall` falls silent "
                       "or guesses on those. "
                       "This is not search but ★lookup★ — it walks by time, not by rank. "
                       "It gives three things: sessions (what the user said then) · commits (what changed and why) · new memories (what was learnt). "
                       "Use `recall` when you know the topic, this when you only know the time.",
        "inputSchema": {"type": "object",
                        "properties": {
                            "days": {"type": "integer",
                                     "description": "how many days back (default 7)"},
                            "limit": {"type": "integer",
                                      "description": "max entries per section (default 25)"}}},
    },
    {
        "name": "brain_suggest",
        "description": "A suggestion grounded in real usage records (a stale memory needing a "
                       "recheck, a broken link to reconnect, a duplicate to merge, a question that "
                       "found nothing). Start here when tidying memory.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "brain_feedback",
        "description": "Tells the brain the quality of a recall result (useful/wrong/stale). "
                       "This signal reshapes the next recall's ranking — leave it when something helped or was wrong.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"},
                           "signal": {"type": "string",
                                      "enum": ["useful", "wrong", "stale", "open"]}},
            "required": ["name", "signal"]},
    },
    {
        "name": "reindex",
        "description": "Reflects a file change into the index (incremental). Call it after editing a memory file directly.",
        "inputSchema": {"type": "object",
                        "properties": {"full": {"type": "boolean"}}},
    },
]


def _corpus_facts(db) -> Dict[str, List[str]]:
    """What the schema may say about ★this★ corpus — its languages and the kinds it already uses."""
    langs: List[str] = []
    kinds: List[str] = []
    try:
        from brain import calibrate
        langs = [_LANG_EN.get(c, c) for c in calibrate.corpus_languages(db)]
    except Exception:                                    # noqa: BLE001
        pass
    try:
        kinds = [r[0] for r in db.execute(
            "SELECT kind, COUNT(*) n FROM docs WHERE kind != '' GROUP BY kind "
            "ORDER BY n DESC LIMIT 12").fetchall()]
    except Exception:                                    # noqa: BLE001
        pass
    return {"langs": langs, "kinds": kinds}


def tools_for(db) -> List[Dict[str, Any]]:
    """The tool list with its descriptions filled from this corpus (never from the author's).

    ⛔ `type` has ★no enum★ — a fixed list rejected the kinds other hosts write (Claude Code's own
       memory uses `user`), and named one person's taxonomy for everyone. The kinds already used here
       are offered as examples; a new one is allowed.
    """
    facts = _corpus_facts(db) if db is not None else {"langs": [], "kinds": []}
    langs = facts["langs"]
    lang_txt = (" — in every language your notes use (%s)" % ", ".join(langs)
                if len(langs) > 1 else (" (your notes are in %s)" % langs[0] if langs else ""))
    kinds_txt = ("; already used here: %s" % " | ".join(facts["kinds"])) if facts["kinds"] else ""
    out = json.loads(json.dumps(TOOLS))                  # a fresh copy per call
    for t in out:
        if t["name"] == "recall":
            t["description"] = _RECALL_DESC.replace("{langs}", lang_txt)
            props = t["inputSchema"]["properties"]
            props["kinds"]["description"] = props["kinds"]["description"].replace("{kinds}", kinds_txt)
        if t["name"] == "remember":
            props = t["inputSchema"]["properties"]
            props["type"]["description"] = (props["type"]["description"].replace("{kinds}", kinds_txt)
                                            + ("" if kinds_txt else "; any short word"))
    return out


# ------------------------------------------------------------------ presentation
def _fmt_recall(rows: List[dict]) -> str:
    if not rows:
        return ("No results. ⛔ Call again with synonyms in `terms` — "
                "a memory is usually written in different words than the question.")
    out = []
    direct = [r for r in rows if not r.get("related")]
    rel = [r for r in rows if r.get("related")]
    for i, r in enumerate(direct, 1):
        if r.get("evidence_age_days", -1) >= 0:
            age = "evidence %s (%dd ago)" % (r["evidence_date"], r["evidence_age_days"])
        else:
            age = "file updated %dd ago" % r["age_days"]
        flag = " ⚠︎ the evidence is old — check it is still true before quoting" if r["stale"] else ""
        out.append(
            "%d. %s  [%s/%s]\n"
            "   %s\n"
            "   why: %s · %s%s\n"
            "   %s\n"
            "   path: %s"
            % (i, r["name"], r["source"], r["kind"] or "-",
               r["description"] or r["title"],
               r["why"], age, flag, r["excerpt"][:280], r["path"]))
    # ★Related context is shown ★separately★ from the answer★ (user instruction 2026-08-20)
    #   *"Connected data conveys the relatedness of information. This itself is not the whole
    #     conversation or memory."*
    # Mixed into the answer list, a 0-score side branch reads as an answer. Keep it short — name and one line.
    if rel:
        out.append("— related context (connected by a human-written [[link]] · not an answer) —\n"
                   + "\n".join(
                       "  · %s ← %s\n    %s" % (r["name"], r["via_graph"],
                                                r["description"] or r["title"])
                       for r in rel))
    return "\n\n".join(out)


def _memory_dir() -> str:
    import os
    cfg = store.load_config()
    for s in cfg.get("sources", []):
        if s["name"] == "memory":
            return os.path.expanduser(s["path"])
    raise RuntimeError("config.json has no memory source")


def _do_remember(db, args: dict) -> str:
    import os
    import re
    name = re.sub(r"[^\w.-]", "_", args["name"]).strip("_")
    if not name:
        return "the name is empty."
    path = os.path.join(_memory_dir(), name + ".md")
    links = args.get("links") or []
    link_line = ("\n\nrelated: " + " · ".join("[[%s]]" % l for l in links)) if links else ""
    # ★The evidence date is always written★ (added 2026-08-12)
    # Skip it and that memory becomes **a memory with no known age** — no evidence date attaches to
    # a recall result, and it drops out of the staleness diagnosis too (`health.stale`). Measured: of
    # ⛔ **493 memories, 76 (15%)** had piled up that way. Writing a date in the body catches it,
    # but skip it and the field is blank, and that hole grows with every save.
    # The moment of saving is exactly "the moment this content was judged true", so today's date is right.
    # An edit overwrites it with today again too — fixing something means confirming it again right then.
    doc = ("---\nname: %s\ndescription: %s\nverified_at: %s\n"
           "metadata:\n  node_type: memory\n  type: %s\n---\n\n%s%s\n"
           % (name, args["description"].replace("\n", " "),
              time.strftime("%Y-%m-%d"), args["type"],
              args["body"].rstrip(), link_line))
    existed = os.path.exists(path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    stats = store.reindex(db)
    return ("%s: %s\nindex: %s\npath: %s"
            % ("updated" if existed else "saved", name, json.dumps(stats, ensure_ascii=False), path))


def call_tool(db, name: str, args: dict) -> str:
    if name == "timeline":
        from brain import timeline
        return timeline.render(timeline.recent(
            db, int(args.get("days") or 7), int(args.get("limit") or 25)))
    if name == "recall":
        rows = search.recall(db, args["query"], k=int(args.get("k") or 8),
                             extra_terms=args.get("terms") or [],
                             kinds=args.get("kinds"), sources=args.get("sources"))
        hint = ""
        if not args.get("terms"):
            hint = ("\n\n⛔ hint: called with no `terms`. A memory is usually worded differently "
                    "from the question — call again with 5~10 synonyms.")
        return _fmt_recall(rows) + hint
    if name == "remember":
        return _do_remember(db, args)
    if name == "neighbors":
        return json.dumps(search.neighbors(db, args["name"]), ensure_ascii=False, indent=2)
    if name == "brain_status":
        return json.dumps(health.status(db), ensure_ascii=False, indent=2)
    if name == "brain_suggest":
        return json.dumps(health.suggestions(db), ensure_ascii=False, indent=2)
    if name == "brain_feedback":
        return json.dumps(health.feedback(db, args["name"], args["signal"]),
                          ensure_ascii=False)
    if name == "reindex":
        return json.dumps(store.reindex(db, full=bool(args.get("full"))),
                          ensure_ascii=False)
    raise ValueError("unknown tool: %s" % name)


# ------------------------------------------------------------------ protocol
def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(mid, payload) -> None:
    _send({"jsonrpc": "2.0", "id": mid, "result": payload})


def _error(mid, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def serve() -> int:
    db = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method = req.get("method")
        mid = req.get("id")
        try:
            if method == "initialize":
                pv = (req.get("params") or {}).get("protocolVersion") or PROTOCOL_FALLBACK
                _result(mid, {"protocolVersion": pv,
                              "capabilities": {"tools": {"listChanged": False}},
                              "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
            elif method in ("notifications/initialized", "initialized"):
                continue                      # a notification gets no reply
            elif method == "ping":
                _result(mid, {})
            elif method == "tools/list":
                if db is None:
                    db = store.connect()
                _result(mid, {"tools": tools_for(db)})
            elif method == "tools/call":
                params = req.get("params") or {}
                if db is None:
                    db = store.connect()
                text = call_tool(db, params.get("name"), params.get("arguments") or {})
                _result(mid, {"content": [{"type": "text", "text": text}]})
            elif mid is not None:
                _error(mid, -32601, "method not found: %s" % method)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            if mid is not None:
                # ★A tool failure is not a protocol error★ — return it as content instead of
                # breaking the session, only flagged as an error.
                if method == "tools/call":
                    _result(mid, {"content": [{"type": "text",
                                               "text": "brain error: %s" % exc}],
                                  "isError": True})
                else:
                    _error(mid, -32603, str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(serve())
