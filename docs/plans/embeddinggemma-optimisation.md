# EmbeddingGemma optimisation plan

Status: reference adapter/service implemented; real-model full-corpus diagnostics and repeated runtime
measurements completed. Complete source coverage passed; the initial component comparison did not
establish retrieval parity. The integrated three-arm host comparison is a separate diagnostic.
Continue optimisation on the current Brain rather than treating the first model swap as final.
Production activation and the automatic profile lifecycle remain incomplete.
See [runtime setup and exact remaining scope](../../runtime/embeddinggemma/README.md).
Date: 2026-10-26. Scope: `google/embeddinggemma-300m`, pinned weights and tokenizer revision.

## Objective

Run embeddings directly from Brain on the user's computer. Optimise multilingual retrieval quality,
query latency, resident memory and incremental indexing cost. No reasoning model, subscription CLI
or cloud API is required in this embedding path. Gemini remains an explicitly selected alternative.
This plan does not change the active engine or claim that a local model has been installed.

## 1. Establish a correct reference before optimising

The model accepts at most 2,048 tokens including task formatting and special tokens, and produces
768-dimensional vectors with supported reductions to 512, 256 and 128. Use its pinned tokenizer;
character counts and token counts from another model are not substitutes. A later model with the
same family name is a different evaluation target.

Use the documented retrieval formats once, in one adapter:

- Query: `task: search result | query: {query}`.
- Document: `title: {title or none} | text: {chunk}`.

Count the final formatted input. Never combine these with the existing Gemini task prefix or a
runtime's automatic prompt a second time. Validate reference outputs against the model's query and
document encoding methods. Use supported float32 or bfloat16 for reference inference; the model card
warns against float16 activations. Quantised runtime artifacts are separate candidates, not the
reference and not assumed equivalent.

Brain's Python core remains standard-library-only. Put model libraries and the exact tokenizer in
an optional local service with a separate environment. The first implementation uses a pinned
Sentence Transformers reference service. It exposes capabilities, token counting/planning and
embedding over loopback HTTP. Model artifacts are prepared explicitly; requests never install them.
The service must reject oversized input, malformed vectors and a mismatched profile identity.

An Ollama adapter is a later interchangeable runtime candidate. Its native embed endpoint defaults
to truncation; set `truncate=false`. A generic OpenAI-compatible endpoint alone does not establish
exact token budgeting, model revision, prompt ownership or absence of truncation. Reject a runtime
that cannot fulfil those requirements; do not silently accept shortened text.

## 2. Token-aware chunks that preserve the complete source

Parse headings, paragraphs, lists, tables and code blocks without an LLM. Preserve original source
byte/character offsets and document version; do not reconstruct source text from decoded token IDs.
Pack complete units under the formatted token limit. Split a single oversized unit at progressively
smaller boundaries, ultimately at Unicode-safe source boundaries validated by the tokenizer. Keep
code symbol/table-header context when available. There must be progress even for one oversized line.

Each chunk gets the document title and a bounded section path. Repeated context also consumes the
input budget; shorten metadata deterministically if necessary and retain full metadata separately.
Store document ID, source hash, section, offsets and previous/next chunk links. Overlap uses complete
boundary units when possible, fits inside the same budget and is measured as duplicate work.

No source suffix may disappear. Coverage is the union of primary body spans, excluding repeated
context, against the indexed body. Empty input, tables, long code lines, Korean/English mixtures,
emoji and boundary-spanning conditions need explicit checks. A batch is independent inputs, not a
conversation: later chunks do not inherit earlier context.

For an oversized query, use token-bounded query segments, embed them independently and merge bounded
candidate lists. Preserve the original query for lexical search. This is a separately evaluated
policy, not silent truncation, vector concatenation, or proof of whole-query reasoning.

## 3. Reuse computation and protect interactive latency

Run one model service per selected runtime/profile, not one model load per hook or worker. Document
jobs yield to interactive query jobs between bounded batches. An already running model batch may
not be pre-emptible, so its duration must fit the interactive latency budget. A queue limit and a
whole-request deadline prevent indefinite waits. On overload, return available lexical/graph results
with diagnostic status; never silently call Gemini.

Keep the model warm while the user works, then release it under a configurable memory/idle budget.
Learn idle retention from observed request gaps and measured loading cost; use hysteresis to prevent
load/unload oscillation. Report cold loading separately from warm inference. CPU/GPU selection and
batch sizing must be measured on the user's machine, within explicit resource ceilings.

Cache exact formatted inputs by space identity and content hash. Embedding cache hits need no new
model call. Re-plan an edited document but reuse unchanged formatted chunks; a metadata change that
changes the actual input invalidates those embeddings. Offset changes alone need not invalidate an
identical embedding. Keep occurrence/provenance records separate from deduplicated vectors.

Query embeddings depend on query formatting and vector-space identity, not the corpus generation.
Retrieval-result caches additionally depend on index generation, filters, terms, ranking policy and
output budget. Do not invalidate every query vector after an unrelated document edit. Concurrent
requests for the same missing vector share one computation. Checkpoint complete batches and resume
only missing work. Apply byte/age limits to caches and evidence, retaining required rollback state.

## 4. Separate vector identity from index layout

Define a vector-space fingerprint from weights/revision, tokenizer/revision, runtime and precision,
prompt policy, pooling/normalisation and output dimension. Define a chunk-set fingerprint from
parser/chunking/context/overlap policy and source generation. Record both with every vector occurrence.

The current `(doc_id, chunk_no)` primary key cannot retain competing spaces safely. Introduce a
versioned namespace and an active-profile pointer before building local vectors beside Gemini.
Do not delete the existing vectors to make room for a trial. Query embeddings, collection dimensions,
chunk spans and calibration must all match the active profile. Promotion changes the complete profile
atomically after health checks. Keep a last-known-good profile within a declared disk budget; if
there is insufficient space for an isolated build plus rollback, postpone the trial.

## 5. Tune one source of cost at a time

| Stage | Candidate change | Evaluation and tradeoff |
|---|---|---|
| Reference | Correct formatting, token coverage, 768d, supported precision | Establish correctness and local baseline; compare to the existing selected engine on the same data |
| Reuse | Per-chunk cache, request coalescing, checkpoints | Unchanged rerun: zero embedding calls; identical answers and source coverage |
| Scheduling | Resident lifetime, bounded batch size, device | Warm/cold p95, background throughput, process-tree RSS, queue wait and foreground contention |
| Chunking | Budget, boundary overlap, section context | Language-pair and long-document quality; token duplication, chunk count and end-to-end latency |
| Dimension | 768 to 512, then optionally 256/128 | Truncate the original vector and renormalise; compare quality and vector-store cost independently |
| Quantisation | A pinned supported quantised artifact | Compare to reference precision on the same inputs; measure quality, memory and actual inference time |
| Retrieval | Candidate width, graph expansion, context assembly | Ablations versus vector-only, lexical-only and no-graph; no graph popularity votes |

A starting experiment grid may use body-budget caps of 384, 768 and 1,280 tokens, always below the
remaining formatted limit, and no overlap versus one boundary unit. These are reproducible search
candidates, not personal defaults or claimed optima. Start with the middle candidate and no overlap
as an explicitly provisional engineering baseline; final settings come from evidence. Do not sweep
every combination or repeatedly retune on held-out failures.

Dimension reduction primarily saves vector storage, transfer and similarity work; it does not remove
the encoder's main forward computation. If full 768d vectors remain locally for reranking or rollback,
report that storage too. Quantisation can change inference cost but is a separate quality experiment.

## 6. Vector and graph cooperate through evidence

Retrieve semantic chunk candidates and lexical candidates within a bounded budget. Resolve both to
current document versions and collapse duplicate overlaps. Expand selected candidates through their
section/adjacency links and documented relationships. Score an expanded candidate using its own query
match and current vector when available. A structural neighbour may supply context without being
promoted as a relevance hit; label the distinction. Link count is not relevance evidence.

The storage graph supplies bounded neighbours in its query, not an unbounded adjacency list capped
only after fetching. Preserve source, project and user filters in every expansion. Return the matched
passage and the smallest sufficient surrounding context under the host's token budget. Do not merge
contradictory facts or unrelated occurrences merely because they share text.

## 7. An automatic loop with independent evidence

Observe latency, cache/load cost, resource pressure, corpus/language change and retrieval outcomes.
Exclude exposed reads and editing turns from promotion evidence; explicit feedback is a separate,
stronger signal. Keep independent session groups and source versions. Reads are weak evidence, not
human ground truth. Never use each document's own title as proof that real retrieval works.

When drift or sufficient fresh evidence justifies a trial, use earlier sessions to select one bounded
candidate. Evaluate it on fresh later sessions without changing it. Separate development, validation
and release data. Repeated testing consumes fresh evidence and a predeclared multiple-testing budget;
insufficient or ambiguous results retain the incumbent. Record reason, evidence window, profile,
quality/cost deltas and the rollback target locally. No personal values ship in source defaults.

Promotion requires quality gates first, then a measured cost improvement within resource limits;
use no arbitrary sum of accuracy, milliseconds and bytes. Trial execution runs outside recall and
must itself obey idle/time/disk budgets. Monitor promoted policies; revert on budget/availability
violations immediately and on fresh evidence of quality harm. Do not reinterpret absence of a click
as failure. Cooldown and profile history prevent oscillation.

Until this mechanism passes release evaluation, it remains an experiment. The existing experimental
`personal.py` ranks fusion/linked candidates; it is not an EmbeddingGemma tuner. Its previously rejected
ranking candidates are not re-enabled by choosing this embedding model.

## 8. Release and completion gates

Freeze model/config/data hashes, hardware, backend and candidate set before measuring. Compare both
the current selected engine and the correct 768d local reference. Report mismatched coverage and
cached/uncached requests explicitly; a missing Gemini query vector is not a fair paid-path latency
baseline. Do not incur paid calls merely to fill that gap without the user's selected budget.

Required quality axes: held-out human-labelled retrieval and chronological independent-use replay.
Report Hit@3, MRR@8, candidate recall, losses/gains, irrelevant attachments, and returned tokens;
stratify Korean-to-Korean, Korean-to-English, English-to-Korean, other observed language pairs,
long documents and boundary-spanning evidence. Weak/proxy data alone cannot establish no regression.
Use paired 95% intervals, session-block resampling for replay, and the existing no-loss release gates.
Do not promote an under-sampled language group based only on the global average.

Replay performance in interleaved order for at least five repetitions, separating cold/warm, cached/
uncached, idle/background-load and healthy/unavailable backends. Report p50/p95/max, RSS including the
model service, CPU/GPU usage where measurable, disk, queue time, embedding calls and total tokens.
Compare actual Qdrant/Neo4j separately from SQLite fallback. Count timeouts and omissions, not just
successful requests. A faster setting passes only with quality preserved, a positive paired speed
improvement supported by measurement, and p95/RSS within the declared user/host ceilings. Existing
handoff budgets remain in force; this plan does not relax them.

Mechanical gates: complete source coverage; zero silent truncations; correct task format once;
valid finite/nonzero vectors and matching dimensions; zero duplicate calls for an unchanged rerun;
batch-resume equivalence; query-cache hits without inference; no cross-profile vector mixing;
atomic activation/rollback; prompt-hook completion inside its deadline under model failure; zero
embedding-path subscription CLI, generation-model or unselected external API calls. Include negative
controls for oversized input, wrong prefixes, stale chunks, incompatible dimensions and killed workers.

## 9. Implementation order and current gaps

1. Runtime capabilities, pinned model identity, exact tokenizer and format adapter (`engines.py`,
   `vectors.py`). The current generic Gemini prefixes and character chunking are insufficient.
2. Versioned spaces, chunk occurrences and caches (`store.py`, `vectors.py`, `vecstore.py`, `stores.py`).
   Migrate additively and keep the live profile intact.
3. Token-aware structure-preserving planner and coverage manifest. Use persisted spans in passage
   extraction; current `best_chunks` recomputation must not use a different chunk policy.
4. Local service lifecycle, priority queue and resumable background worker (`deferred.py`, jobs).
5. Bounded retrieval/context assembly (`search.py`, graph adapters, hook/server) and calibration scoped
   to the active profile (`calibrate.py`). Bump ruler generation when measurement behavior changes.
6. Independent evaluation, personal policy lifecycle, diagnostics and reversible activation.

Each step needs an isolated control that can fail. Core changes remain standard-library-only; the
optional model service has explicitly documented dependencies and lifecycle. Add governance ownership
for new modules and preserve platform, localisation and clean-room requirements. Production switching
and quality gains are not established by this document. Real-model diagnostic
results are kept outside the public repository; personal measurements must not become product defaults.

## Sources

- [Pinned model family, input/output, precision, task formats and MRL](https://huggingface.co/google/embeddinggemma-300m)
- [Ollama embedding API: truncation, resident lifetime and timing](https://docs.ollama.com/api/embed)
- [Project principles](../ROADMAP.md)
