# Optional EmbeddingGemma runtime

Implementation status: a local reference adapter, exact-token planner, resident service and isolated
evaluation runner are implemented. Wiring checks use a deterministic fake encoder and establish no
retrieval quality. A pinned-model offline CPU smoke check has also passed for Korean/English
768-dimensional output, official query/document prompt parity and complete token-planned source coverage.
A local full-corpus diagnostic and repeated runtime latency/process-RSS measurements have completed.
Complete source-span coverage passed; the initial component comparison did not establish retrieval
parity. A separate three-arm host comparison has completed for the integrated pipeline. Optimise the
current Brain across its ingestion and retrieval interfaces, then re-evaluate. Keep the runtime
experimental: independent chronological evidence and full accelerator-memory accounting remain pending.
Automatic profile
promotion, overlap/section-policy tuning, quantisation selection, idle unloading and query-segment
fusion are not implemented. Oversized queries fail explicitly rather than being silently truncated.

The Brain core still runs on the Python standard library. This optional service uses a separate
Python 3.11+ environment with the declared dependencies; do not install them into the core's system
Python. The repository import check permits only `torch` and `sentence_transformers` in `service.py`.
All other runtime code and all `brain/` modules remain subject to the standard-library rule.

## Prepare the runtime and model

From the repository root:

```sh
uv venv --python 3.11 .venv-embeddinggemma
uv pip install --python .venv-embeddinggemma/bin/python -r runtime/embeddinggemma/requirements.txt
```

On Windows use `.venv-embeddinggemma/Scripts/python.exe` and the corresponding executable paths.
Access to the original [model](https://huggingface.co/google/embeddinggemma-300m) may require accepting
its terms with a Hugging Face account. Complete that step and authenticate with `hf auth login` in
the runtime environment. The service does not extract subscription tokens or accept licence terms.
Download explicitly before serving, using a pinned commit:

```sh
.venv-embeddinggemma/bin/hf download google/embeddinggemma-300m --revision 57c266a740f537b4dc058e1b0cda161fd15afa75
```

The command reports the local snapshot path. Start the service with that path, a manifest path and
an inference-cache path outside the checkout:

```sh
.venv-embeddinggemma/bin/python runtime/embeddinggemma/service.py \
  --model-path /path/to/downloaded/snapshot \
  --revision 57c266a740f537b4dc058e1b0cda161fd15afa75 \
  --manifest /path/to/experiment/embeddinggemma.json \
  --cache /path/to/experiment/inference.sqlite \
  --device cpu --port 18081 --batch 4
```

`cpu` is the portable reference. `mps` or `cuda` is an explicit alternative to measure; the device,
library versions, model-file hashes, precision and format are part of the space fingerprint. The
manifest is published only after the service successfully binds loopback. Startup and inference
are offline (`local_files_only`, no remote model code). A permanently blocked model job terminates
the service with a failure exit code; restart it explicitly. Pending Brain work remains resumable.

The model stays resident until the process exits. A bounded priority queue gives new query work
priority over waiting document batches; a batch already in progress cannot be interrupted for a
query. `--batch`, `--queue-size` and `--timeout` are administrative bounds, not automatically learned
values. Normalisation and optional MRL shortening happen after inference. A byte-bounded persistent
full-vector cache avoids a second forward pass for repeated inputs or another output dimension.

## Evaluate without changing the live engine

The runner creates a new workspace and uses SQLite's backup API to copy the source index. It preserves
source-level embedding exclusions, forces local stores, disables the LLM judge and experimental
personal reranking, and leaves the original index/config untouched. All supplied labels must be real;
it does not invent questions or infer labels from filenames.

```sh
python3 runtime/embeddinggemma/evaluate.py \
  --source-index /path/to/current/index.db \
  --source-config /path/to/current/config.json \
  --workspace /path/to/new-experiment \
  --manifest /path/to/experiment/embeddinggemma.json \
  --labels /path/to/short.json /path/to/recall.json \
  --dimension 768 --chunk-tokens 768 --repeats 5
```

The labels may be a list of `{q, gold, terms?}` records or the repository's `cases`/`A_memory_needed`
format. The output `measurement.json` records corpus/model/label identity, build calls and cache hits,
per-case failures, vector Hit@3/8, warm-query MCP Hit@3 and elapsed time. Repeated queries do not count
as independent quality evidence. This runner is not the two-axis release gate, a cold-load/RSS
benchmark or an automatic engine switch. Retain the existing engine until those checks are complete.

## Adapter and storage contract

An isolated Brain config can select `engines.embed.provider = "embeddinggemma"`, model
`google/embeddinggemma-300m`, and base URL `http://127.0.0.1:18081/v1`. Set `BRAIN_EMBED_MANIFEST` to the
published manifest; otherwise it is read from the brain home as `embeddinggemma.json`.
`BRAIN_EMBED_CHUNK_TOKENS` selects the provisional total formatted budget (default 768, maximum 2048).
`BRAIN_EMBED_DIM` supports 768/512/256/128. `BRAIN_LOCAL_EMBED_TIMEOUT` bounds each local HTTP request
(default 5 seconds); the hook's process watchdog remains the outer deadline.
Background document batches use `BRAIN_LOCAL_EMBED_BUILD_TIMEOUT` (default 30 seconds), independently
of the interactive query deadline, so a cold document batch can finish without lengthening recall.

The new vector key preserves `(doc_id, chunk_no, model, dim)` spaces. Existing vectors migrate
transactionally without changing their values. Current source versions alone are served. Plans and
source spans are persisted per document/version/profile, so evidence extraction uses the same cuts
as ingestion. A completed document is published atomically; reusable chunk batches checkpoint before
publication. A failed batch never publishes half of that new document.

`BRAIN_LOCAL_EMBED_CACHE_BYTES` bounds the core's reusable chunk cache (default 256 MiB), independently
of served vectors and the service's 256 MiB full-vector cache. The query-vector cache keys the model
space, not the chunk budget. Semantic gate calibration is scoped to the local profile and dimension;
old Gemini thresholds do not arm the local model. Selecting a profile is explicit; automatic
activation/rollback across whole index generations is still pending.

## Verification

```sh
python3 tests/clean_room.py verify_embeddinggemma verify_vectors verify_engines verify_stores
```

The fake encoder checks the adapter rather than language understanding: complete Unicode source
coverage, oversize rejection, exact prompts, persistent reuse, dimension reuse, namespace migration,
incremental edits, interruption/resume and matching evidence offsets. Real language quality must be
measured with the downloaded model on held-out user data before activation.

## Compare the complete memory paths

The two-profile embedding diagnostic above is not a comparison against native host memory.
Use `compare_hosts.py` for three explicit profiles: `native`, `old`, and `current`. Keep the old
checkout and its earned index as they were; rebuilding the old index would erase missing/stale
coverage from that baseline. Optimise the current runtime rather than treating an unchanged legacy
integration path as its final performance.

```sh
python3 runtime/embeddinggemma/compare_hosts.py \
  --spec /path/to/profiles.json --cases /path/to/real-cases.json \
  --files /path/to/frozen-evidence.json --workspace /path/to/new-run
```

The profile JSON declares `arms`, shared `read_roots`, a shared `prompt_prefix`, optional
`response_language` (a language tag or `auto`, defaulting to the question's language), and optional
`code_paths` to fingerprint. Each arm declares `host`, `cwd`, an explicit `command` argument array,
and optional `env`. All three use the same selected host. Commands can reference `{prompt}`,
`{schema}` (a generated schema file), `{schema_json}`, and `{artifact}`. No shell is involved.
The validated live route is Claude Code's JSONL output with structured evidence. The Codex JSONL
parser remains an unvalidated integration; missing served-model identity prevents a completed claim.
The runner never replaces host homes, selects an API provider, or changes saved host settings.

Cases are real `{q, gold}` records; an explicit empty `gold` list is a no-memory control. Evidence is
an array of `{path, names}` records mapping frozen file paths to accepted document names. If a host
actually autoloads part of an index, record only that injected prefix as `autoloaded_text`.
An index mentioning a name is never scored as an answer. Gold labels are not put in host prompts.

The final answer must cite up to three paths with verbatim quotes. The runner checks those against
the frozen files and observed retrieval output, records actual host usage and elapsed time, and
rejects native runs connected to MCP or reads outside the declared roots. A matching quote alone
is not a semantic answer grade. Keep that limitation and alternative valid evidence visible when
interpreting named-document labels. Repeated requests are not independent quality observations.

`--resume` requires the same profiles, questions, evidence, code and repeat count. Errors remain
unmeasured, and the default stops at the first incomplete host run. Failed models are not silently
replaced. Record scheduled generation or source-capture delays separately from retrieval-only hits.

For isolated Brain MCP profiles, `readonly_mcp.py` exports only `recall` and `neighbors`. It requires
an SQLite backup with `comparison-isolation.json` containing `isolated: true` and the original
`source_index`; the copy must not alias that source. Pass `--source-root`, `--home`, `--config`, and
`--host` explicitly. Optional `--path-map` rewrites original paths to frozen evidence paths. Query
embedding misses are blocked unless `--allow-embedding` explicitly permits the selected provider;
record cache-only limitations when comparing an older remote provider. This is a host-evaluation
bridge, not an embedding path through a CLI. Optional `--manifest`, `--dimension` and `--chunk-tokens`
pin the local profile, and `--audit` records backend query-cache state without logging credentials.

Request-local vector-store reuse is now generation-checked. It reuses an identical query/space/backend
only inside the same recall, checks both this connection and other connections for changes, and
fetches again for a larger candidate pool. It changes neither ranking policy nor the persistent
query-vector cache. Full integration assessment must include the selected vector/graph backends,
hook liveness, source coverage, host evidence and total usage rather than a single component score.
