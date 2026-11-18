# Memory validity and classification optimisation

> Status: Phases 1–3 implemented — 2026-11-18
> Owner: Maintainers

## Goal

Long-term memory must distinguish what is currently applicable, what changed, what is only proposed,
and what has not been verified. Work and everyday memories share the same controls in every language.
A saved or recently mentioned statement is not automatically a verified current fact.

## Decisions

Keep semantic judgement in the calling host and deterministic validity checks in Brain. Keep one
independently changing fact per tracked record. Reuse exact scope/topic/facet identifiers across
languages; neither translated labels nor embedding similarity authorise merging facts. Memory kinds
remain open vocabulary, and Observatory lenses remain interpretations rather than factual validity.
Do not bulk rewrite, split, certify or delete existing private memories.

## Ordered delivery

| Phase | Deliverable | Completion evidence |
|---|---|---|
| 1 — implemented | Read-only `memory_review` inventory of validity gaps, explicit conflicts, proposals, invalid/cyclic links and classification coverage | Labelled controls, no false flags for complete active facts or scheduled/expired records, exact scope, stable pagination, no writes/body reads/remote inference; installed API check |
| 2 — implemented | Host-assisted extraction of independent facts from mixed notes, with exact source passages and explicit uncertainty | Original notes retained; no partial correction retires an entire mixed record; reviewed proposed facts trace back to evidence |
| 3 — implemented | Reviewed identity reuse across languages and explicit disambiguation of similar subjects | Multilingual same-fact and different-fact controls; no semantic or spelling-based automatic merge; correction history retained |
| 4 — follow-on | Paired native-host outcome study and targeted retrieval improvement | Same model, permissions, normal native memory and input material within each host; report losses and gains, current/history accuracy, time, usage and failures |

Phase 4 remains follow-on work. Stage retrieval changes using the inventory and paired studies.
Identity controls are not evidence of better answers.

## Phase 1 API contract

`memory_review` reads the current SQLite index without refreshing source files, contacting serving
databases, reading full bodies, invoking embeddings, or saving memories. It reports the indexed state,
not a guarantee that source files have not changed since indexing.

- Omitted scope includes all indexed scopes. An explicit scope matches exactly; empty means unscoped
  only. Counts and returned names must not reveal excluded scopes.
- The first request freezes `as_of`. Subsequent pages require that value and `expected_snapshot` from
  the first response. Changed indexed metadata returns `snapshot_changed`; the caller restarts.
- `only_attention=true` is the default. `false` includes healthy metadata too. Counts always describe
  the selected scope, independently of the current page. `limit` is 1–100.
- Reasons distinguish untracked validity, pending confirmation, conflicting claims, invalid links,
  cycles, missing recorded evidence, missing effective start and missing kind. Missing metadata means
  review may be useful, not that the fact is false. Evidence presence is not source credibility.
- Exact recorded kinds and sources are counted, not translated, case-folded or silently merged.
- Items carry source paths and hashes, stable identity fields and bounded current/conflict references.
  Read the source or `memory_history` before making a judgement. Continue with existing
  `prepare_revision` / guarded `remember` only after confirming the change and its effective date.

## Phase 2: source-bound proposals

The host reads the mixed source and authors one candidate claim. `prepare_fact` checks an exact
scope/name/SHA and unique verbatim source passages, returns a proposed lifecycle with structured
`extracted_from` provenance, and does not write or infer. The existing `remember` writer validates
source version and quote spans again before new writes. It persists provenance without changing the
mixed original; successful identical retries remain noops after later source changes. Old retry
hashes omit absent provenance and remain compatible.

Extracted proposals are always `embed:false`. Revision preparation carries that opt-out forward,
and the writer rejects a new revision that drops it. This avoids exporting derived quotations merely
because the target memory source permits embeddings. The proposal is not a currently applicable fact. A reviewed confirmation uses `prepare_revision`
against the proposal, with explicit dates, reasons and evidence. It never retires the entire mixed
original. Quotes use Unicode code-point offsets into the parsed, trimmed source body. Repeated
passages require more context. One call handles one proposed fact, not an atomic multi-fact migration.
The host still judges semantic support, independent fact boundaries and source trustworthiness.
Direct external editors do not participate in the writer lock; no cross-file transaction is claimed.

Controls cover five languages, exact quotations, stale sources before preparation and saving,
wrong scope, tampered spans, proposal-only writes, immutable originals, retry compatibility and
confirmation of the proposal alone. These checks do not establish general answer-quality improvement.

## Phase 3: reviewed identity reuse

`review_identity` compares up to ten explicit candidates in one exact scope (omitted means unscoped).
It returns bounded source/evidence previews, recorded identifiers, current fact names, conflicts and
revision references. Grouping is exact after NFC normalisation, never translation or case folding.
The host discovers candidates with existing recall and reads full sources/history before choosing.

`prepare_fact.identity_reference` requires a name, source SHA and explicit reason. A unique, fresh,
tracked identity with valid metadata/links supplies topic/facet verbatim; contradictory identifiers
are refused. The selection is recorded in lifecycle evidence and structured provenance.
Old releases also appended bare-name source/anchor links; 2.7.0 excludes those generated footer
entries from the derived graph without rewriting original files or history. New proposals do
not append provenance links; separately authored body links remain explicit relationships.
Server 2.5.1 also stores structured `identity_reference` frontmatter and revalidates its unique name,
scope, source SHA, exact identifiers and revision links immediately before new writes. Absent-reference
payloads retain their original retry hashes; identical successful retries skip the new-write guard.
No database migration is needed. Re-prepare unsaved 2.5.0 requests to obtain the structured guard.
The existing fact snapshot additionally guards the
prepared save against changes to the selected group, including changed source files. Preserve the
returned arguments; the host remains responsible for semantic identity, not just matching strings.

Identity reuse never merges existing groups, certifies a fact, or creates a revision edge. A new
proposal remains proposed/local-only. Explicit correction uses existing revision/conflict controls.
Controls compare five translated caller-created identifiers against one explicitly reused identity;
include distinct subjects/facets/scopes/case, NFC-equivalent labels, invalid/cyclic/stale references,
no SQL writes during review, source preservation, history membership, save-time changes and retries.
Native-host benefit remains unmeasured.

## Measurement and acceptance

Use fixed-time, isolated labelled fixtures spanning current, future, expired, proposed, conflicting,
untracked, broken and cyclic records; include several scripts and exact scope controls. Check issue
recall and false flags separately. Verify page union equals an unpaged response; metadata changes must
invalidate the token. An SQL authorizer rejects writes and traced SQL must contain no body reads.
Check existing revision/save and capability contracts in the clean room on supported platforms.

Measure metadata inventory latency and output size separately from retrieval or model quality. Record
corpus size, code revision, repetitions and limits. A private read-only snapshot can describe existing
coverage, but must not publish memory names, bodies or paths. No new work enters the automatic recall
hot path; no timers or background model calls are added.

For user-level claims use `tests/audit/measure_hosts.py` with native Codex and native Claude baselines,
normal memory preserved, frozen inputs and independently checked traces. Until that study runs,
answer-quality and work-efficiency benefit remain unmeasured. Synthetic controls validate the tool,
not general multilingual comprehension or native-host superiority.

## Implementation checks (2026-11-17)

The labelled fixture covers 17 records in one scope: all 9 review candidates are returned, with no
false flags on the 8 complete active, scheduled or expired records. Separate unscoped/other-scope
controls stay excluded. Five scripts remain current without language-specific parsing. SQL write
authorisation is denied during the request; traced queries read no bodies. Targeted clean-room
revision, capability, English-only and code-generation checks pass. This is deterministic fixture
coverage, not a real-host answer-quality result.

## Rollback and limitations

Remove the additive tool declaration/module and capability flag, or revert this change. No data
migration is needed. Metadata scans and explicit chain resolution still cost work as an index grows;
this is an on-demand review, not a per-prompt operation. Legacy topic mixtures and semantic
contradictions require host review. Unobserved conversations cannot be reconstructed.

## Related

- [Phase 2 implementation and release checks: PR #92](https://github.com/honeyground-org/local-brain/pull/92)

- [Phase 1 implementation and release checks: PR #90](https://github.com/honeyground-org/local-brain/pull/90)

- [Current facts and history](../../README.md#current-facts-and-change-history)
- [Standing native-host comparison criterion](../../CONTRIBUTING.md#native-host-comparison-is-a-standing-release-criterion)
- [Roadmap](../ROADMAP.md)
- Personal wiki companion (vault-relative): `01 Projects/local-brain/Memory validity optimisation.md`

Save-guard follow-up (2.5.2): legacy evidence-carried references receive the same new-write validation without changing their request hash or stored bytes. Successful old retries remain noop. Candidate review and preparation also compare ancestor file SHAs with indexed versions before reuse; an unindexed ancestor edit makes the candidate unavailable. This adds ancestor file reads only to explicit identity operations, not general recall.

Entry-point follow-up (2.5.3): read-only MCP identity review and discovery never initialise the index. Legacy reference recognition excludes exact source-span evidence, and both reference formats require the group snapshot for new writes. Existing successful retries remain compatible.

Target-name follow-up (2.5.4): source-bound fact writes recheck the target name in the refreshed exact scope, preventing a competing source from making history ambiguous after preparation. Other scopes and successful retries remain compatible.

## Adaptive serving follow-up (proposal #101)

Confirmed successful reads and structured exposure handling precede automatic serving. Versioned
observations cannot reuse earlier collector evidence. A held-out winner serves only the context it
was measured for; baseline, observation-only mode and explicit disable remain available. Supporting
versions, measurement generation and embedding space are checked before applying it. Capability
output distinguishes selected from effective policy. No answer-quality benefit is inferred from
synthetic promotion/rollback checks.

### Further general memory capabilities to evaluate

1. **Explicit forgetting and reingestion prevention**: distinguish reversible archival from deletion
   and suppress reimport of an explicitly forgotten source. Measure leakage after reindex, restoration,
   cross-host retrieval and paraphrased reingestion. Costs: deletion across derived stores, backups and
   false-positive suppression. Requires a separate privacy contract; not implemented by this change.
2. **Bounded current-context projection**: reuse current validity and existing hook context to create a
   compact provenance-backed view for host context budgets. Measure stale-current answers, coverage,
   token usage and regeneration cost against existing hooks. Do not create a second truth store.
3. **Typed entity relationships**: document links already exist; optional evidence-backed entity,
   predicate and time-qualified relations would enable finer questions. Measure relationship accuracy,
   ambiguity and multi-hop retrieval against document links. Extraction and conflict management add cost.
4. **Events and plans as optional views**: topic/facet and lifecycle already represent generic memories;
   event timelines and plan transitions would be an application view, not a mandatory domain ontology.
   Require language-independent structured fields and retain source quotations and uncertainty.

Source audit: Ollo commit `94c33d87` (inspected 2026-11-18), not a claim about enabled production flags.

| Brain candidate | Inspected Ollo source at that commit | Assessment and priority |
|---|---|---|
| Explicit forgetting | `services/api/src/memory.ts`, `deleteNote`: recursive predecessor deletion, `memory_forgotten` keys; `forgottenKeys` guards incoming writes | First: archival alone cannot prevent reimport. Adopt an explicit privacy contract and test all derived stores before shipping. |
| Bounded current context | `services/agent/src/memory/rank.ts`, `pickMemory` and `noteLine`: item/character budgets, core-first selection, archive exclusion, previous-version annotation | Next: a useful projection over Brain validity/history. Measure budget coverage; do not copy Ollo's fixed weights or language-specific rendering. |
| Typed relationships | `services/api/src/memory-graph/store.ts`, `pgMemoryGraph` and `lockGraph`: PostgreSQL projection from fact/history rows with per-user locking and an optional switch | Later: complement Brain document edges with entity/predicate provenance only if relation accuracy improves. No additional graph database is implied. |
| Events and plans | `packages/contracts/src/memory.ts`, `MemoryPlanKind`, `MemoryPlanCertainty`, `MemoryPlanStatus`; `docs/LONG-TERM-MEMORY.ko.md` sections 3-4 distinguish event and plan stores | Later optional views: retain source/time/uncertainty and avoid imposing a task-specific ontology on general memories. |

These are assessed directions, not shipped features. Prefer privacy and current-context correctness
before adding another storage backend or copying language-specific assertion heuristics.
Identity-edge follow-up (2.5.5): exact-scope SHA-pinned identity provenance no longer becomes an unqualified graph name link. Cross-project homonyms cannot acquire a new automatic identity edge. Existing saved content is preserved; typed identity-edge traversal is not implemented.
New writes also reject old prepared identity requests containing automatic bare-name provenance links; successful prior retries bypass new-write guards and remain compatible.

Provenance-boundary follow-up (2.6.1, generation 51): quoted frontmatter evidence never creates graph edges or aliases. Normal indexing performs a one-time derived-link rebuild without editing sources. Source/target filesystem identity is checked before extraction writes, so case aliases and hard links cannot replace the source even when expected_sha is correct.


### Read-only inspection workspace (2.7.0, proposal #105)

The default dashboard now separates recorded operating state, memory inspection, historical
performance and explicit relationships. It reads the existing index and saved score history;
opening it no longer runs score collection or a benchmark. A bounded memory preview and
paginated client-side list make source/scope/validity filters and same-identity version
navigation available offline. Unknown validity and unmeasured host benefit remain explicit.
This UI release does not change retrieval ranking, calibration or claim a quality gain.
Validation extends the existing dashboard check with unknown/current/superseded states,
cross-scope isolation, missing measurements, escaping, read-only snapshots and output aliases.


2.7.0 also refuses an occupied extraction destination during preparation and immediately
before a new write, even when the file belongs to another scope or an expected SHA matches.
Source aliases are rejected in both phases. Successful identical retries still return noop,
and names in other scopes remain valid when their actual paths differ. Graph provenance
version 2 triggers one normal full index refresh to remove legacy generated footer edges;
no source bytes or historical evidence are changed.

2.7.1 makes extraction graph provenance explicit (`extracted_from.graph_policy=explicit`).
New preparation emits the marker and no automatic source links. Unsaved legacy requests must
be prepared again; successful legacy retries keep their original payload hash and return noop.
Authored body relationships, including a terminal `related:` line, remain relationships.
For unmarked historical records, a matching payload receipt can prove that the footer was
part of the authored body. Otherwise matching provenance tokens are conservatively excluded
from the derived graph, using the serializer's literal strings even for bracketed filenames.
Historical metadata cannot always reconstruct original request whitespace, so ambiguous legacy
footers cannot be classified perfectly; source bytes and evidence remain available for review.
Graph provenance version 3 rebuilds only derived links during normal indexing. Generation 53
includes the receipt helper in the measurement fingerprint. This is a correctness repair,
not evidence of improved retrieval scores or native-host work efficiency.

2.7.2 closes the new-file check/publish race: the fully flushed temporary file is atomically
linked into an absent destination instead of replaced over it. Concurrent file or symlink
creation causes a conflict; unsupported hard-link filesystems fail without overwriting. An
existing contract fixture creates a foreign-scope record immediately after the final SHA read
and checks preservation, cleanup and a subsequent successful create/noop pair. Generation 54
tracks the updated receipt/write module. No new database or runtime service is introduced.

2.7.3 applies the no-write preparation contract at the MCP entry point, including cold sessions
and revision/inventory planners. Missing indexes return index_pending rather than creating
state. Historical graph parsing isolates malformed optional JSON and evidence types so that
known source provenance still gets filtered. Graph provenance version 4 repairs derived links
without rewriting sources; generation 55 fingerprints the repair. Existing contract checks
exercise first-call subprocesses and null/scalar/object evidence plus malformed optional JSON.

2.7.4 limits the legacy mixed-footer fallback to a complete terminal sequence of serializer
tokens. Body `related:` lines followed by prose retain their links, with or without a payload
receipt and with or without a later real automatic footer. Existing checks verify both parser
output and canonical graph restoration. Graph provenance version 5 rebuilds derived links;
source bytes remain unchanged. Generation 56 fingerprints this parsing correction.

The 2.7.4 CI run also exposed the session-check PID-file publication gap twice on Linux:
a second starter could interpret the empty newly created file as a dead owner and unlink it.
The checker now uses a nonblocking OS lock on a persistent inode, with automatic crash release.
An existing session-events check reproduces the empty-PID interleaving and confirms exclusion
and subsequent reuse; the worker lease and its retry budget are unchanged.

2.7.5 aligns rendered-file retry hashes and lifecycle body comparisons with universal-newline
file reads. It preserves the original request payload hash, key and stored bytes, including
previously saved CRLF records. Existing tests cover LF/CRLF/CR reviewed preparation, unchanged
new/noop retries, byte/receipt preservation, real-edit refusal and ordinary saves. Generation 57
tracks the receipt-module repair; graph provenance remains version 5.

### Read-only dashboard entry point

The default dashboard CLI now opens the existing index read-only, before rendering. Missing, unavailable or incompatible schema versions return an actionable message without initialisation or migration. The actual CLI is checked against absent, old-schema and current indexes; this covers the connection boundary that renderer-only tests missed. This does not measure retrieval-quality improvements.

### Linked legacy history in the inspection view

Dashboard and memory_history now share the same scoped history-members helper. A predecessor pinned by legacy_whole_record_sha remains reachable from either revision even without topic/facet metadata. Untracked records include themselves. The existing dashboard verifier compares both directions with standard history and keeps same-name records in other scopes separate; it also checks index immutability.

### Certification route and transitive history closure

Automatic-policy measurement now leaves observations without cached query vectors pending instead of substituting semantic_k=0. Serving also retains baseline when the query vector is absent. Warm measurements use the same automatic semantic route as eligible serving, without measurement-triggered embedding calls. Generation changes invalidate prior certificates. History traversal expands the scoped identity group at every reached revision, so a SHA-pinned legacy record and a translated proposal reach the same closure while other scopes and facts stay separate.

### Distinct legacy-source branches and degraded semantic retrieval

A shared SHA-pinned legacy source does not make different topic/facet identities one history. Each fact retains its own revisions plus the source; a source with multiple identities offers separate branches. Dashboard and memory_history expose those branches without editing originals. Cache presence alone is not semantic availability: baseline and candidate retrieval failures leave observations pending, and a degraded live baseline is not reranked by a semantic certificate. A generation bump invalidates earlier certificates. These are correctness guards, not evidence of user-level quality superiority.

### Resolved revision vectors in linked ranking

Graph neighbors can name an earlier revision. Linked candidates now use the hydrated current document ID for vector lookup, matching their body and its SHA/model/dimension checks. Old-only, current-only and both-vector cases are checked through candidate measurement and certified serving. No old vector is attributed to a new body; absence of the current vector leaves that expansion out. No operational use of the faulty candidate or user-level quality advantage was observed.


### Semantic scores and exact graph seeds

The native vector search now filters superseded revisions rather than assigning their cosine to a successor. Current memories need their own current-SHA vector; a predecessor cannot contribute a score or reference-distribution value. Actual SQLite vector search is exercised through policy measurement, certification and MCP serving for old-only, current-only and conflicting vectors. Graph expansion identifies a selected semantic seed by its unique path, not a potentially shared name across scopes. These are correctness checks on synthetic fixtures, not a measured user-quality gain.


### Retired-candidate budget

The semantic candidate budget now counts every document excluded by current validity, including SHA-pinned legacy predecessors without lifecycle metadata. Forty-one such predecessors cannot hide the one current vector at default k=8 or contaminate its score distribution. With no current vector the result remains empty. Counting uses the same resolver as filtering; the cost grows with the indexed corpus and the retired-candidate allowance rather than an arbitrary larger fixed width.


### Scoped connections and current policy support

Connection nodes and edges retain document IDs; labels disambiguate same-name memories with their scope or path. An isolated homonym remains separate from connected homonyms. Current-only policy certification and training reject superseded support even when the original source bytes and SHA remain unchanged; historical retrieval still preserves that source. Measurement and serving compare the same baseline-retrieval plus reranking interval, excluding final response serialization in both. Synthetic 10ms baseline + 5ms reranking exceeds an 11ms certificate and returns baseline, whereas 10ms + 0.5ms remains eligible. These controls are not evidence of user-level quality improvement.


### Exact path edges and complete policy overhead

Path links now join directly to indexed document IDs instead of becoming ambiguous name links. Graph provenance generation 6 rebuilds old derived links at a full index; original files and history are untouched, and the dedicated graph receives the corrected edge difference. SQLite and remote graph sources share the same canonical edges. Policy application starts timing before state, certificate and query-cache checks. Prospective certification adds those same checks to held-out costs before promotion; excessive validation cost can reject a candidate as well as roll back serving.


### Scoped semantic results and offline inspection

MCP recall removes duplicates by exact document path, both before and after automatic reranking. Same-name memories in other scopes remain visible in the meaning lane; legacy direct callers can still explicitly exclude names. The dashboard graph reads the canonical SQLite snapshot rather than the remote serving/healing path. Unavailable or unsynchronised Neo4j does not prevent inspecting memories and recorded performance/connection state, and opening the dashboard never triggers remote graph sync. Existing MCP and CLI verifiers exercise both counterexamples without network calls. Generation 67 invalidates policies measured before this retrieval correction; user-level outcome benefit remains unmeasured.
