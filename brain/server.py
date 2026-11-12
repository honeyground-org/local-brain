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
import os
import sys
import time
import traceback
from typing import Any, Dict, List

from brain import health, search, store

SERVER_NAME = "brain"
SERVER_VERSION = "2.2.0"      # JSON defaults and safe writes; orbit-1 fields remain compatible.
PROTOCOL_FALLBACK = "2024-11-05"
_PROTOCOL = PROTOCOL_FALLBACK     # what `initialize` negotiated — `brain_capabilities` reports it

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
    "Give 5~10 other spellings{langs}, abbreviations, and related identifiers. "
    "Words and meaning find different answers: when the words are unsure, recall adds your learned "
    "synonyms, orders the list by words, meaning and the passage that matched together, and adds, in a "
    "separate list, a few memories closest in ★meaning★ that are not shown above. Pass `semantic_k` "
    "to always get that many (for example 3), or 0 for none. It adds that many to read, and one embedding "
    "call for a question not asked before."
)
_LANG_EN = {"en": "English", "ko": "Korean", "de": "German", "es": "Spanish",
            "fr": "French", "ja": "Japanese"}

_REMEMBER_DESC = (
    "Writes a new memory to a file and indexes it immediately. Keep reusable facts, preferences and decisions; "
    "record effective dates and evidence when facts change. Avoid transient telemetry or raw event logs. "
    "JSON is the default. Repeating an unchanged request is a noop; changing an existing memory requires "
    "expected_sha from recall or a prior save. project_id is always persisted when supplied. "
    "Connect related memories with links. format=text changes presentation, never overwrite protection."
)

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "brain_analyse",
        "description": "Read paginated evidence, earlier analysis and user feedback to discover NEW perspectives on the user's activity, repeated problems and memory. Use the current host to interpret; no separate AI API is invoked. Read every page for a whole-corpus claim, cite document ids, then publish an evolving view with brain_view.",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 3650},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}},
    },
    {
        "name": "brain_view",
        "description": "Publish a cited, host-authored analysis as a new local Observatory revision; preserves original memories and earlier analyses. Supply snapshot from brain_analyse, question, changes, lenses [{title,why,categories:[{label,ids:[document ids]}]}], insights [{kind:observation|hypothesis|question,text,ids:[document ids]}]. Adapt perspectives to evidence and prior feedback, never a fixed taxonomy. References are validated; claims remain model interpretations.",
        "inputSchema": {"type": "object", "properties": {"analysis": {"type": "object"}}, "required": ["analysis"]},
    },
    {
        "name": "recall",
        "description": _RECALL_DESC,
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "the natural-language question, as-is"},
                "terms": {"type": "array", "items": {"type": "string"},
                          "description": "5~10 synonyms, Latin spellings, related identifiers. Omit it and performance drops sharply."},
                "k": {"type": "integer", "minimum": 1, "maximum": 100, "default": 8, "description": "number of results (default 8)"},
                "sources": {"type": "array", "items": {"type": "string"},
                            "description": "narrow to memory | docs | wiki | guide"},
                "kinds": {"type": "array", "items": {"type": "string"},
                          "description": "narrow to kinds of memory (the frontmatter `type`){kinds}"},
                "semantic_k": {"type": "integer", "minimum": 0, "maximum": 100,
                               "description": "also return up to this many memories close in ★meaning★ that "
                                              "the words did not find, in a separate list. Omitted: a few are "
                                              "added only when the words are unsure; 0: never, and meaning "
                                              "does not order the list either. Words and "
                                              "meaning find different answers; this is more to read, and one "
                                              "embedding call unless the question was asked before."},
                "format": {"type": "string", "enum": ["text", "json"], "default": "json",
                           "description": "json (default) or text — the orbit-1 structured answer: the same rows "
                                          "with name·path·sha, the lane, partial/fallback and latency"},
                "request_id": {"type": "string", "maxLength": 128,
                               "description": "echoed back in json mode (at most 128 characters)"},
                "scope": {"type": "string", "maxLength": 128,
                          "description": "orbit-1: an opaque project_id — keeps that project's memories and the unscoped "
                                         "ones, never another project's; unscoped rows carry project_id null"},
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
                "search_aliases": {"type": "array", "items": {"type": "string"},
                                   "description": "optional alternate phrases for finding this memory; stored with the document, not as global synonyms"},
                "idempotency_key": {"type": "string", "maxLength": 128, "x-generated-when-omitted": True,
                                    "description": "Optional; a stable content key is generated when omitted. The same key and unchanged content is a noop; "
                                                   "with other content it needs expected_sha (otherwise conflict: sha_mismatch)"},
                "expected_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$", "x-required-for-update": True,
                                 "description": "orbit-1: the sha of the version you read — required to change an existing memory "
                                                "(a memory written without a key is otherwise conflict: name_taken)"},
                "project_id": {"type": "string", "maxLength": 128,
                               "description": "orbit-1: an opaque project scope written to the frontmatter; omitted = unscoped"},
                "source": {"type": "string", "maxLength": 512,
                           "description": "orbit-1: where this memory came from (a task, a session, the evidence) — stored, not indexed"},
                "observed_at": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                                "description": "orbit-1: the date the content was true (YYYY-MM-DD); default = today"},
                "format": {"type": "string", "enum": ["text", "json"], "default": "json",
                           "description": "json (default) = protected save ACK: new/updated/noop/conflict, stored, indexed_words, embedded, served, sha. text changes presentation only"},
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
        "name": "brain_capabilities",
        "description": ("What this brain supports, read live — contract `orbit-1`: server, protocol, code "
                        "generation and schema versions; the embedding engine, its space and whether it is "
                        "ready; the stores; the corpus; per-tool features (recall/remember formats, scope, "
                        "idempotency, ack fields) derived from the tool declarations themselves; limits; and "
                        "an explicit `unsupported` list. Call it once after connecting; a feature it does not "
                        "name does not exist."),
        "inputSchema": {"type": "object", "properties": {}},
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
        if t["name"] in ("recall", "remember", "prepare_revision"):
            from brain import i18n
            t["description"] += " " + i18n.host_language_instruction()
    return out


# ------------------------------------------------------------------ presentation
SHOWN = 280      # characters of a result's excerpt shown to the caller
# ★How many memories of meaning are added when the words are unsure★ (2026-10-14) — the same number an
#  agent was advised to ask for. Measured on the author's 66 labelled questions, asked with their terms as
#  agents ask (no model): words' 8 held the answer for 46; adding meaning's 3 ★always★ → 52, at 11 to read;
#  ★only when the hook's own rule finds nothing to attach★ → the same 52, adding on 22 of 66 (9.0 to read).
#  Where the words were sure (44), they held the answer 43 times and meaning added none.
AUTO_MEANING = 3


# Validity belongs to the memory, not to a development-specific document type.
for _tool in TOOLS:
    _properties = _tool["inputSchema"].get("properties", {})
    if _tool["name"] == "recall":
        _properties.update({"view": {"type": "string", "enum": ["current", "history"], "default": "current"},
                            "as_of": {"type": "string", "description": "Effective date or ISO timestamp with timezone; omitted means now"}})
    if _tool["name"] == "remember":
        _properties['prepare_only'] = {'type': 'boolean', 'description': 'Preview same-scope fact revisions without saving. Use before changing a fact; returns expected_fact_sha for a guarded save.'}
        _properties['expected_fact_sha'] = {'type': 'string', 'pattern': '^[0-9a-f]{64}$', 'description': 'From prepare_only. Reject a save if indexed fact revisions or their source files changed. Retrying a completed identical save remains a noop.'}
        _properties["lifecycle"] = {"type": "object", "required": ["topic", "facet"],
            "additionalProperties": False, "properties": {
                "topic": {"type": "string"}, "facet": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "proposed", "disputed", "superseded", "retracted", "archived"]},
                "valid_from": {"type": "string"}, "valid_to": {"type": "string"},
                "supersedes": {"type": "array", "items": {"type": "string"}},
                "legacy_whole_record_sha": {"type": "object", "additionalProperties": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                    "description": "Only when replacing an entire untracked memory: map its supersedes name to the exact SHA read. Never use for a partial correction to a mixed-topic note; retain unrelated facts."},
                "superseded_by": {"type": "string"}, "change_reason": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}}}}
        _tool["description"] += (" For changing facts, preferences, arrangements or decisions, use lifecycle topic/facet "
            "and evidence. To correct an entire untracked memory, add its exact SHA in legacy_whole_record_sha and name in supersedes. Do not retire mixed-topic notes for a partial correction. Keep one independently changing fact per memory. Separate plans from actual state with different facets. "
            "Read the prior memory first; save confirmed changes under a new name with supersedes, valid_from and change_reason. "
            "Never infer replacement from recency alone. Use proposed for tentative ideas; never silently turn them into facts. "
            "Before saving a changing fact, recall related memories in the user's language and reuse their topic/facet identifiers across languages. "
            "Use prepare_revision for an explicitly identified predecessor, review its save_arguments, then send them to remember. "
            "Alternatively call remember with prepare_only=true, review the records, then save with expected_fact_sha. "
            "Resolve competing active facts with explicit supersedes and evidence, or store an uncertain claim as disputed. "
            "Do not translate identifiers or infer a replacement from semantic similarity. Stored does not mean verified current: inspect validity in the acknowledgement.")
TOOLS.append({"name": "memory_history", "description": "Read current state, previous revisions and change evidence for a memory, including everyday life and work. Old names also resolve to the current topic history. document_history preserves observed file edits, with diffs; observation time is not a fact validity date.",
              "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "scope": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["name"]}})

# Revision preparation shares content validation and the existing protected remember writer.
_revision_properties = {k: dict(next(t for t in TOOLS if t["name"] == "remember")["inputSchema"]["properties"][k])
                        for k in ("name", "description", "type", "body", "project_id", "source", "observed_at", "links", "search_aliases")}
_revision_properties["type"] = {"type": "string", "description": "Memory kind; any short single-line word"}
_revision_properties.update({
    "predecessor": {"type": "string", "description": "Exact indexed memory name in project_id; omitted project_id means unscoped only"},
    "topic": {"type": "string", "description": "Required for untracked predecessors; inherited unchanged for tracked facts"},
    "facet": {"type": "string", "description": "One independently changing fact; inherited for tracked predecessors"},
    "status": {"type": "string", "enum": ["active", "proposed", "disputed"]},
    "valid_from": {"type": "string", "description": "When the change becomes effective, not when it is saved"},
    "valid_to": {"type": "string"}, "change_reason": {"type": "string"},
    "evidence": {"type": "array", "items": {"type": "string"}},
    "replace_whole_record": {"type": "boolean", "description": "Only for an untracked predecessor: explicitly confirm replacement of its entire content, never a partial correction"}})
TOOLS.append({"name": "prepare_revision",
    "description": "Prepare a change to an explicitly identified memory without saving. Reuses tracked topic/facet across languages and returns guarded save_arguments for remember, with predecessor and fact snapshot checks. Requires a new name, explicit status, effective date, reason and evidence. Untracked notes additionally require topic/facet and whole-record confirmation. Never infer replacement from similar wording or recency; retain unrelated facts. Review the returned request before saving.",
    "annotations": {"readOnlyHint": True},
    "inputSchema": {"type": "object", "additionalProperties": False, "properties": _revision_properties,
                    "required": ["predecessor", "name", "description", "type", "body", "status", "valid_from", "change_reason", "evidence"]}})


def _fmt_recall(rows: List[dict]) -> str:
    if not rows:
        return ("No results. ⛔ Call again with synonyms in `terms` — "
                "a memory is usually written in different words than the question.")
    from brain import lifecycle
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
               r["why"], age, flag, r["excerpt"][:SHOWN], r["path"]))
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
    return "\n\n".join(out) + "\n" + "\n".join(lifecycle.format_line(r) for r in rows if r.get("lifecycle"))


def _fmt_meaning(rows: List[dict], why_none: str, cost: str, start: int, auto: bool = False) -> str:
    """The `semantic_k` list — kept apart from the word results, because it was found another way."""
    head = ("— close in meaning (%s · %s) —"
            % ("the words were unsure, so meaning was asked too" if auto
               else "found by meaning, not by the words", cost))
    if not rows:
        return head + "\n  " + ("unavailable — %s" % why_none if why_none else "nothing more found")
    from brain import lifecycle
    out = [head]
    for i, r in enumerate(rows, start):
        if r.get("evidence_age_days", -1) >= 0:
            age = "evidence %s (%dd ago)" % (r["evidence_date"], r["evidence_age_days"])
        else:
            age = "file updated %dd ago" % r["age_days"]
        flag = " ⚠︎ the evidence is old — check it is still true before quoting" if r["stale"] else ""
        out.append("%d. %s  [%s/%s]\n   %s\n   why: %s · %s%s\n   %s\n   path: %s"
                   % (i, r["name"], r["source"], r["kind"] or "-", r["description"] or r["title"],
                      r["why"], age, flag, r["excerpt"][:SHOWN], r["path"]))
    return "\n\n".join(out) + "\n" + "\n".join(lifecycle.format_line(r) for r in rows if r.get("lifecycle"))


def _note_recall(args: dict, rows: List[dict], sk: int, mrows, why: str, cached, t0: float,
                 auto: bool = False, unsure: bool = False, bridged: int = 0, reordered: bool = False) -> None:
    """One row per `recall` (§observe) — whether an agent asked for meaning too, and what each way returned.
    ⛔ No query, no terms, no filter values: counts, a fixed failure code and which database answered."""
    from brain import observe, stores, vectors
    row = {"k": int(args.get("k") or 8), "terms": len(args.get("terms") or []),
           "filtered": bool(args.get("kinds") or args.get("sources")),
           "semantic_k": int(args.get("semantic_k") or 0), "auto": auto,
           "unsure": unsure, "bridged": bridged, "reordered": reordered,
           "words": len([r for r in rows if not r.get("related")]),
           "related": len([r for r in rows if r.get("related")]),
           "graph": stores.last_served("graph") or None,
           "meaning": None if mrows is None else len(mrows),
           "meaning_fail": (why.split(":")[0] or None) if why else None,
           "embed_cached": cached, "vector": vectors.last_search() if sk > 0 or reordered else None,
           "secs": round(time.time() - t0, 3)}
    observe.write("recall", row)


def _memory_dir() -> str:
    import os
    cfg = store.load_config()
    for s in cfg.get("sources", []):
        if s["name"] == "memory":
            return os.path.expanduser(s["path"])
    raise RuntimeError("config.json has no memory source")


def _do_remember(db, args: dict) -> str:
    """All callers use the same protected save path, regardless of presentation format."""
    from brain import remembercontract as rc
    import sqlite3
    t0 = time.time()
    bad = rc.validate(args)
    if bad:
        return json.dumps(bad, ensure_ascii=False)
    args = dict(args)
    name = args["name"]
    directory = _memory_dir()
    path = os.path.join(directory, name + ".md")
    try:
        os.makedirs(directory, exist_ok=True)
        with rc.write_lock(directory):
            if os.path.islink(path):
                return json.dumps(rc.error("invalid_argument", "A memory write cannot replace a symbolic link"))
            cur = rc.current(path)
            if "project_id" not in args and cur.get("project_id"):
                args["project_id"] = cur["project_id"]
            from brain import lifecycle
            if args.get('prepare_only'):
                if 'lifecycle' not in args and cur.get('lifecycle'):
                    args['lifecycle'] = cur['lifecycle']
                try:
                    store.reindex(db, recalibrate=False, sync=False, heal=False, queue_embedding=False)
                except Exception as exc:
                    return json.dumps(dict(rc.error('index_pending', str(exc)[:200]), retryable=True), ensure_ascii=False)
                preview = lifecycle.prepare(db, name, args)
                preview['write_issue'] = lifecycle.check_write(db, name, args, cur)
                return json.dumps(preview, ensure_ascii=False)
            issue = lifecycle.check_write(db, name, args, cur)
            if issue:
                return json.dumps(rc.error("invalid_argument", issue), ensure_ascii=False)
            if args.get('expected_fact_sha') and not args.get('lifecycle'):
                return json.dumps(rc.error('invalid_argument', 'A fact snapshot requires lifecycle topic/facet'))
            if cur.get("lifecycle") and (
                    cur.get("body") != store.parse_frontmatter(rc.render(name, args, cur.get("verified_at") or ""))[1].strip()
                    or cur.get("lifecycle") != args.get("lifecycle")
                    or (cur.get("payload_sha") and cur["payload_sha"] != rc.payload_sha(args))):
                return json.dumps(rc.error("revision_required", "Keep the old revision; create a new name with lifecycle.supersedes"))
            args = rc.prepared(args)
            result, conflict = rc.decide(cur, args)
            if result in ('new', 'updated'):
                if args.get('lifecycle'):
                    try:
                        # Reconcile against fresh source observations under the shared writer lock.
                        # No remote sync, trigger healing or inference inside this precondition check.
                        store.reindex(db, recalibrate=False, sync=False, heal=False, queue_embedding=False)
                    except Exception as exc:
                        return json.dumps(dict(rc.error('index_pending', str(exc)[:200]), retryable=True), ensure_ascii=False)
                    issue = lifecycle.check_write(db, name, args, cur)
                    if issue:
                        return json.dumps(rc.error('invalid_argument', issue), ensure_ascii=False)
                code, review = lifecycle.reconcile_write(db, name, args, unchanged_fact=bool(cur.get('lifecycle')))
                if code:
                    review['records'] = [{k: r[k] for k in ('name', 'sha', 'source_matches_index', 'lifecycle')}
                                         for r in review['records']]
                    return json.dumps(rc.error(code, 'Review the current fact before saving; no memory was written', review), ensure_ascii=False)
            verified_at = (cur.get("verified_at") if result == "noop"
                           else (args.get("observed_at") or ("" if args.get("lifecycle") else time.strftime("%Y-%m-%d"))))
            stats: dict = {}
            if result in ("new", "updated"):
                try:
                    rc.atomic_write(path, rc.render(name, args, verified_at), cur.get("sha"))
                except FileExistsError:
                    cur = rc.current(path)
                    result, conflict = "conflict", {"reason": "sha_mismatch", "current_sha": cur.get("sha")}
            if result != "conflict":
                try:
                    # No embedding calls or calibration in a client's save/ACK round trip.
                    stats = store.reindex(db, recalibrate=False)
                except Exception as exc:                 # Stored but not indexed: a retry catches up.
                    stats = {"error": str(exc)[:200]}
            out = rc.ack(db, name=name, path=path, result=result, conflict=conflict, args=args,
                         stats=stats, t0=t0, verified_at=verified_at, cur=cur)
            if out.get('stored'):
                row = db.execute('SELECT id,sha FROM docs WHERE path=?', (path,)).fetchone()
                out['validity'] = (lifecycle.snapshot(db).info(row['id']) if row and row['sha'] == out['sha']
                                   else {'status': 'unknown', 'current': None, 'issue': 'index_pending'})
                out['meta']['validity_as_of'] = out['validity'].pop('as_of', None)
    except sqlite3.OperationalError as exc:
        busy = "locked" in str(exc).lower() or "busy" in str(exc).lower()
        out = dict(rc.error("busy" if busy else "write_failed",
                            "Another writer holds the memory directory; retry the same request" if busy else str(exc)[:200]),
                   retryable=True)
    except OSError as exc:
        out = dict(rc.error("write_failed", str(exc)[:200]), retryable=True)
    if out.get("ok"):
        from brain import deferred
        deferred.kick(db)  # ACK describes the completed save; inference stays outside this request
    if args.get("format") == "text" and out.get("ok"):
        return "%s: %s\nindex: %s\npath: %s\nsha: %s\nvalidity: %s" % (
            {"new": "saved", "updated": "updated", "noop": "unchanged"}[out["result"]],
            name, json.dumps(out["index"], ensure_ascii=False), path, out["sha"], json.dumps(out.get('validity'), ensure_ascii=False))
    return json.dumps(out, ensure_ascii=False)


def _words_unsure(db, rows: List[dict]) -> bool:
    """★The hook's own rule★ (§hook.select with the calibrated threshold) finds nothing to attach in what the
    words returned — the one definition of "the words were unsure" brain has, measured per person. Not a
    new number: a threshold chosen here would be one more rule written in two places."""
    try:
        from brain import calibrate, hook
        return not hook.select(rows, calibrate.threshold(db))
    except Exception:                                    # noqa: BLE001
        return False


def call_tool(db, name: str, args: dict, *, _measure: bool = False, _capture=None) -> str:
    if name == "recall":
        from brain import vectors
        from brain import lifecycle
        with vectors.search_session(), lifecycle.view(args.get("view", "current") if isinstance(args, dict) else "current",
                                                       args.get("as_of") if isinstance(args, dict) else None):
            return _call_tool(db, name, args, _measure=_measure, _capture=_capture)
    return _call_tool(db, name, args, _measure=_measure, _capture=_capture)


def _call_tool(db, name: str, args: dict, *, _measure: bool = False, _capture=None) -> str:
    if name == "timeline":
        from brain import timeline
        return timeline.render(timeline.recent(
            db, int(args.get("days") or 7), int(args.get("limit") or 25)))
    if name == "recall":
        t0 = time.time()
        from brain import recalljson
        bad = recalljson.validate(args)
        if bad:
            return json.dumps(bad, ensure_ascii=False)
        q, k = args["query"], args.get("k", 8)
        terms = args.get("terms") or []
        as_json = args.get("format", "json") != "text"
        rows = search.recall(db, q, k=k, extra_terms=terms, kinds=args.get("kinds"), sources=args.get("sources"), log=not _measure,
                             scope=args.get("scope") or None)
        hint = ""
        if not args.get("terms"):
            hint = ("\n\n⛔ hint: called with no `terms`. A memory is usually worded differently "
                    "from the question — call again with 5~10 synonyms.")
        # ★When the words are unsure★ (2026-10-14 · §search.fused) — ① the person's learned synonyms, asked for
        #   here although the measured decision keeps them out of the hook (there they decide whether to attach;
        #   here an agent asked); ② still unsure → words, meaning and the whole-body passage order the list.
        #   Where the words are sure the answer is exactly what it was.
        from brain import vectors
        unsure = _words_unsure(db, rows)
        was_unsure, bridged, reordered, head = unsure, [], False, ""
        cached = vectors._qcache_get(db, q) is not None if unsure else None
        if unsure:
            wide = search.recall(db, q, k=search.FUSE_POOL, extra_terms=terms, kinds=args.get("kinds"),
                                 sources=args.get("sources"), log=False, bridge=True, scope=args.get("scope") or None)
            bridged = search.last_bridged()
            if bridged:
                unsure = _words_unsure(db, wide)
                head = "— learned synonyms added: %s —\n\n" % ", ".join(bridged)
            if unsure and args.get("semantic_k") != 0:          # 0: meaning is never used
                # ★Two lanes, the better rank wins★ (2026-11-02 · §search.union) — measured against the
                #   three-ranking merge it replaces: question alone 22 → 33 of 66, with terms 36 → 46, one lost.
                rows, _why = search.union(db, q, wide, k, extra_terms=list(terms) + bridged,
                                          kinds=args.get("kinds"), sources=args.get("sources"), width=SHOWN,
                                          scope=args.get("scope") or None)
                reordered = True
                if rows:
                    head += ("— the words were unsure: ordered by words and meaning together, "
                             "the better rank first —\n\n")
            elif bridged:
                rows = [r for r in wide if not r.get("related")][:k] + [r for r in wide if r.get("related")]
        more = ""
        sk = int(args.get("semantic_k") or 0)
        auto = args.get("semantic_k") is None and unsure
        if auto:
            sk = AUTO_MEANING
        mrows, why = None, ""
        if sk > 0:
            # ★Apart from the answer list★ (2026-10-13 · docs/notes/retrieval-fusion-plan.md step 3) — a second
            #   list of what is close in meaning and not shown above, never a re-rank of it.
            if cached is None:
                cached = vectors._qcache_get(db, q) is not None
            cost = "query embedding cached" if cached else "one embedding call"
            mrows, why = search.meaning(db, q, sk, exclude=[r["name"] for r in rows],
                                        kinds=args.get("kinds"), sources=args.get("sources"),
                                        width=SHOWN, scope=args.get("scope") or None)
            start = len([r for r in rows if not r.get("related")]) + 1
            # ⛔ Added unasked, a failure is recorded (§observe) and not shown — a person with no embedding
            #    engine chosen would read "unavailable" under every unsure answer.
            if mrows or not auto:
                more = "\n\n" + _fmt_meaning(mrows, why, cost, start, auto=auto)
        if not _measure:
            if not args.get("scope") and not args.get("kinds") and not args.get("sources") and k == 8 and args.get("semantic_k") != 0:
                from brain import personal
                rows = personal.apply(db, q, rows, k, terms=terms)
                if mrows is not None:
                    shown = {r["name"] for r in rows}
                    mrows = [r for r in mrows if r["name"] not in shown]
                    start = len([r for r in rows if not r.get("related")]) + 1
                    more = ("\n\n" + _fmt_meaning(mrows, why, cost, start, auto=auto)
                            if mrows or not auto else "")
            _note_recall(args, rows, sk, mrows, why, cached, t0, auto=auto,
                         unsure=was_unsure, bridged=len(bridged), reordered=reordered)
        if _capture is not None:
            _capture.extend(rows)
        if as_json:
            from brain import recalljson
            return json.dumps(recalljson.response(
                db, query=q, terms=terms, k=k, rows=rows, mrows=mrows, why_none=why, asked_meaning=sk,
                auto_meaning=auto, request_id=args.get("request_id"), unsure=was_unsure, bridged=bridged,
                reordered=reordered, embed_cached=cached, t0=t0, shown=SHOWN,
                scope=args.get("scope") or None, scope_applied=bool(args.get("scope"))), ensure_ascii=False)
        return head + _fmt_recall(rows) + more + hint
    if name == "prepare_revision":
        from brain import remembercontract
        return json.dumps(remembercontract.prepare_revision(db, args), ensure_ascii=False)
    if name == "remember":
        return _do_remember(db, args)
    if name == "memory_history":
        from brain import lifecycle
        if not isinstance(args, dict) or not isinstance(args.get("name"), str):
            return json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "name is required"}})
        offset, limit = args.get("offset", 0), args.get("limit", 25)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            return json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "offset >= 0 and limit 1..100 required"}})
        return json.dumps(lifecycle.history(db, args["name"], args.get("scope"), offset, limit), ensure_ascii=False)
    if name == "neighbors":
        return json.dumps(search.neighbors(db, args["name"]), ensure_ascii=False, indent=2)
    if name == "brain_analyse":
        from brain import observatory
        return json.dumps(observatory.analysis_context(db, days=int(args.get("days", 30)),
                          offset=int(args.get("offset", 0)), limit=int(args.get("limit", 200))), ensure_ascii=False)
    if name == "brain_view":
        from brain import observatory
        record = observatory.save_analysis(db, args.get("analysis"))
        path = observatory.write(db, open_browser=False)
        return json.dumps({"revision": record["revision"], "path": path,
                           "live_command": "brain dashboard --explore --serve"}, ensure_ascii=False)
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
    if name == "brain_capabilities":
        from brain import capabilities
        try:
            memory_dir = _memory_dir()
        except Exception:                                # noqa: BLE001 — no config yet: the answer still comes, with no folder
            memory_dir = ""
        return json.dumps(capabilities.describe(db, tools=tools_for(db), protocol=_PROTOCOL,
                                                memory_dir=memory_dir, shown=SHOWN),
                          ensure_ascii=False, indent=2)
    raise ValueError("unknown tool: %s" % name)


# ------------------------------------------------------------------ protocol
def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(mid, payload) -> None:
    _send({"jsonrpc": "2.0", "id": mid, "result": payload})


def _error(mid, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


# ── ★a long-running server answers with the code on disk★ (2026-10-07) ─────────────────────
#
# ⛔ A host keeps this process for the whole session — days, on a busy machine. It held the code it
#    started with: on 2026-10-07 21 of them were alive, the oldest from 9/28, sixteen code generations
#    behind. Each recall they served wrote that old code's answers into the shared index (the bridge
#    cache, the margin filter), and every fix to the brain reached a session only when a human restarted it.
#    So before answering, the server compares its modules on disk with the ones it loaded; if they changed,
#    it checks that the new code imports, then ★becomes it★ (`exec` keeps the PID and the host's pipes).
#    Whatever it had already read from the host is handed over, so no request is lost.
#    ⛔ Not on Windows — there `exec` starts a new process and ends this one, which the host sees as a crash.
_PENDING_ENV = "BRAIN_SERVER_PENDING"
_PENDING_MAX = 256 * 1024


def _code_on_disk() -> tuple:
    """(name, mtime, size) of every module beside this one — "has my code changed since I started"."""
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    try:
        for n in sorted(os.listdir(here)):
            if n.endswith(".py"):
                st = os.stat(os.path.join(here, n))
                out.append((n, st.st_mtime_ns, st.st_size))
        # Engine choice is cached at import. Existing hosts must reload configuration too.
        config = store.default_config_path()
        try:
            st = os.stat(config)
            out.append((config, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((config, 0, 0))
    except OSError:
        return ()
    return tuple(out)


def _package_parent() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _new_code_loads() -> bool:
    """Does the code on disk import — a half-saved file must not take the session's brain down."""
    import subprocess
    try:
        p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import brain.server"
                            % _package_parent()], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def _become_new_code(pending: bytes) -> None:
    import base64
    env = dict(os.environ)
    env[_PENDING_ENV] = base64.b64encode(pending).decode("ascii")
    env["PYTHONPATH"] = os.pathsep.join(x for x in (_package_parent(), env.get("PYTHONPATH", "")) if x)
    sys.stderr.write("brain: the code on disk changed — continuing as it (pid %d)\n" % os.getpid())
    sys.stderr.flush()
    try:
        from brain import launch                         # never `-m` from the host's folder (§launch)
        av = launch.argv("brain.server")
        os.execve(av[0], av + sys.argv[1:], env)
    except OSError as exc:                               # keep answering with the code in hand
        sys.stderr.write("brain: could not switch to the new code (%s) — staying on the old\n" % exc)


def _requests():
    """Request lines from the host, read ★without a hidden buffer★ — so the bytes not yet answered are
    always known and can be handed to the new code (§_become_new_code)."""
    import base64
    pending = base64.b64decode(os.environ.pop(_PENDING_ENV, "") or b"")
    loaded = tried = _code_on_disk()
    fd = sys.stdin.fileno()
    while True:
        while b"\n" not in pending:
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            pending += chunk
        now = _code_on_disk()
        if now != tried and os.name != "nt" and len(pending) <= _PENDING_MAX:
            tried = now                                  # a broken save is tried once, not on every request
            if now != loaded and _new_code_loads():
                yield None                               # the caller closes what it holds
                _become_new_code(pending)
        raw, pending = pending.split(b"\n", 1)
        yield raw.decode("utf-8", "replace")


def serve() -> int:
    from brain import hosts
    hosts.utf8_stdio()                                   # replies go out as UTF-8 JSON (§hosts.utf8_stdio)
    db = None
    for line in _requests():
        if line is None:                              # about to become the new code (§_requests)
            if db is not None:
                db.close()
                db = None
            continue
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
                from brain import i18n
                pv = (req.get("params") or {}).get("protocolVersion") or PROTOCOL_FALLBACK
                global _PROTOCOL
                _PROTOCOL = pv
                _result(mid, {"protocolVersion": pv,
                              "capabilities": {"tools": {"listChanged": False}},
                              "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                              "instructions": i18n.host_language_instruction()})
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
                tool_name, arguments = params.get("name"), params.get("arguments", {})
                if tool_name in ("recall", "memory_history") and isinstance(arguments, dict):
                    from brain import recalljson
                    valid = tool_name != "recall" or recalljson.validate(arguments) is None
                    if valid and store.needs_reindex(db):
                        store.reindex(db, recalibrate=False)
                text = call_tool(db, tool_name, arguments)
                result = {"content": [{"type": "text", "text": text}]}
                try:
                    structured = json.loads(text)
                except (ValueError, TypeError):
                    structured = None
                if isinstance(structured, dict):
                    if _PROTOCOL >= "2025-06-18":
                        result["structuredContent"] = structured
                    if structured.get("ok") is False or structured.get("result") == "conflict":
                        result["isError"] = True
                _result(mid, result)
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
