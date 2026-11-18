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
are refused. The selection is recorded in existing lifecycle evidence, with a link to the anchor.
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

These are assessed directions, not shipped features. Prefer privacy and current-context correctness
before adding another storage backend or copying language-specific assertion heuristics.
Identity-edge follow-up (2.5.5): exact-scope SHA-pinned identity provenance no longer becomes an unqualified graph name link. Cross-project homonyms cannot acquire a new automatic identity edge. Existing saved content is preserved; typed identity-edge traversal is not implemented.
