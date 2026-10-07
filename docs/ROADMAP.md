# Roadmap and working notes

This is the living plan for local-brain: what it is for, the rules every change follows, where it
stands, and what comes next. It replaces the private planning notes kept before the repository went
public (2026-10-07); only their conclusions are carried here.

## The goal

A memory for coding agents that is **more efficient and performs better than the flat memory index plus
`grep`**, and that keeps improving itself on each person's own notes.

- **Done means measured.** A change is finished when it wins under the same conditions against what you
  had before — not when the code runs.
- **Methods are not compared by a composite score.** They measure different things, so adding them up is
  meaningless. The composite (`brain score --history`) only compares *your own* points in time.

## Principles every change follows

| Principle | What it means in practice |
|---|---|
| Measured, not assumed | Every check runs a **control group first**: if it cannot fail, its green is not evidence. |
| Nothing tuned to one person | Every number has an owner — you, your data, your host or engine, or the method — and is measured or chosen, never baked in from the author's notes. |
| Chosen, never guessed | AI engines and storage backends are used only because someone named them. A key or a database that happens to exist is not a choice. |
| The local copy is canonical | Embeddings are earned and the graph is derived from your files, so both always live locally; dedicated databases are serving indexes. |
| Zero dependencies | Standard library only, on the system `python3` (3.8+). Each adapter speaks its service's HTTP API. |
| English in the repository | Code, comments, docs and commit messages. User-facing text goes through `brain/locales/` (six languages). |
| Silence is not success | A layer that stops working must say so in `brain status`, not fall quiet. |
| CI costs nothing | Every check runs on free runners and no step calls a paid service; `verify_pr_report` enforces it. |

## Where it stands (2026-10-07)

**Retrieval**
- Recall attaches memories as you type, above a threshold measured on your own corpus.
- The margin over the noise floor is learned per person by a Kalman filter; an observation counts for its
  *novelty* (the share of questions that answered differently), so near-repeats cannot fake confidence.
- Trigger phrases bring a declared thread back exactly; a Korean ↔ English loanword bridge connects
  mixed-language notes.

**Behaviour layer**
- No rules ship; they are learned from your own tool history, and firing rates are measured with the same
  matcher that fires them.

**Storage**
- Vector search and graph traversal can each be served by a dedicated database: `vector` = sqlite · Qdrant,
  `graph` = sqlite · Neo4j. A ledger sends only the difference; a database answers only once it is in sync.
- `brain stores --docker` runs both in Docker with the data in plain folders under the brain's home, so
  removing containers or reinstalling Docker keeps it; a database that comes back empty is refilled.
- Measured on the author's notes (1,917 documents): identical answers on every question compared,
  similarity search 263 → 9 ms, one-hop neighbours for every document 4.6 → 0.43 s.

**Robustness**
- A measurement never changes shared state that other processes read (trial settings stay in-process).
- Long-running MCP servers pick up new code on disk by themselves.
- A fresh clone with an empty home passes every check that does not need personal data
  (`python3 tests/clean_room.py`: 41 green, 13 skipped, 0 red on macOS with a local Qdrant running).

**Release and contribution**
- Public under Apache-2.0. This repository is the single source: there is no private copy to sync from.
- Open to contributors (2026-10-07): CI runs the clean room on Linux, macOS and Windows (Python 3.8 –
  3.13), the live store checks against Qdrant and Neo4j, the Docker check and the release gates on every
  pull request. The PR rules check the title, the description, the sign-off (DCO) and imports; the PR
  report classifies the impact and asks the right reviewers; high-impact changes need two approvals
  including a maintainer, and the PR report sets each of the five answers beside what the diff shows.
  `main` is protected and merges through a merge queue. CI costs nothing — no paid API, action or runner.
  See CONTRIBUTING.md and GOVERNANCE.md; the roster is `.github/governance.json`.

## What comes next

### 1. Open contribution
- **A public evaluation corpus** — quality checks today run on the author's notes and skip in CI. A synthetic,
  shareable corpus with labelled questions lets CI catch a change that lowers recall,
  and lets the PR report show a recall number for every pull request.
- **More reviewers** — area owners for storage, retrieval, privacy and the behaviour layer, so a
  high-impact change no longer needs an admin bypass (GOVERNANCE.md → *Adding a maintainer or an area
  owner — how*; the `Bypass: roster` record narrows by itself as the roster grows).
- **Windows required** — the Windows clean room runs in CI and is shown, not required. First run
  (2026-10-07): 32 green, 15 skipped, 7 red — `verify_docker_stores`, `verify_first_day`, `verify_guard`,
  `verify_help`, `verify_host_neutral`, `verify_package`, `verify_public_scrub`. Make it required once they
  are green.
- Lower `tests/english_only_ratchet.txt` as the remaining Korean data lines move into locale or data files.

### 2. Quality
- Close the gap to OR'd `grep` on hit rate while keeping the reading load far lower (top-3 against hundreds
  of files).
- Measure short-query recall on real corpora in other languages; the method settings were first set on
  one corpus.
- Verify Windows on real hardware.

### 3. Storage
- More backends, one class each: pgvector, Chroma, Milvus (vector); Memgraph (graph).
- Per-role installer flags (`--vector-store`, `--graph-store`) next to the existing `--stores docker`.

### 4. Checks to tighten
- `verify_vs_grep` should skip (exit 77) when there is no evaluation set, and honour `BRAIN_EVAL_DIR`.
- `verify_translit` should not clear the live bridge cache.
- Mask secret-shaped values in the `sk-…` family in `brain/privacy.py`.
- Replace absolute millisecond bars in `verify_vectors` with relative ones.
- Keep checks from writing to the live threshold history.

## Working on brain

```bash
PYTHONPATH=. python3 tests/verify_<name>.py      # any single check
python3 tests/verify_public_scrub.py             # before every push: internal names, secrets, identity
python3 tests/verify_secret_scanner.py .         # gitleaks rules over files and history
```

- **The clean room** is the release bar: copy the tracked files into an empty folder and run every
  `tests/verify_*.py` with a fresh, empty `HOME` per check, with and without `PYTHONPATH`. Keep the home and
  logs outside the package folder.
- **Ruler modules** (`brain/calibrate.py` → `RULER_MODULES`) decide the threshold. Changing one requires
  bumping `CODE_GENERATION`, or `verify_code_generation` fails — measure only after the bump.
- **Translations** are called with literal keys (`i18n.t("cli.x.y")`) so the unused-key check can see them.
- Test against a copy: point `BRAIN_HOME` and `BRAIN_CONFIG` at a temporary folder; a `--set` against the
  real config changes the brain you are using.

## Further notes

- [`docs/notes/dashboard-renewal-brief.md`](notes/dashboard-renewal-brief.md) — the pages the project
  renders, measured, for whoever renews them.
- [`docs/notes/codex-host-report-2026-09-21.md`](notes/codex-host-report-2026-09-21.md) — what wiring
  Codex as a host showed (commit hashes in it refer to the pre-release history).
