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

## Where it stands (2026-10-12)

**Retrieval**
- Recall attaches memories as you type, above a threshold measured on your own corpus.
- The margin over the noise floor is learned per person by a Kalman filter; an observation counts for its
  *novelty* (the share of questions that answered differently), so near-repeats cannot fake confidence.
- Trigger phrases bring a declared thread back exactly; a Korean ↔ English loanword bridge connects
  mixed-language notes.
- Against `grep` with the same words — the question's own — on the author's 66 labelled questions
  (2026-10-12): the top 3 holds the answer for 32%, OR'd `grep` for 82% somewhere in a median of 222
  files. Held to the same 3 files, `grep` finds it 1% of the time. (`verify_vs_grep` used to give the
  brain the labels' expansion words and `grep` none, and read 65%.) With synonyms an agent wrote from
  the question alone, as a caller does, the top 3 holds it for 53% and OR'd `grep` for 95% in a median
  of 644 files.

**Behaviour layer**
- No rules ship; they are learned from your own tool history, and firing rates are measured with the same
  matcher that fires them.

**Storage**
- Vector search and graph traversal can each be served by a dedicated database: `vector` = sqlite · Qdrant · Chroma ·
  pgvector (over brain's own Postgres client, `brain/backends/_pgwire.py`) · Milvus,
  `graph` = sqlite · Neo4j · Memgraph (over brain's own Bolt client, `brain/backends/_bolt.py`). A ledger sends only the difference; a database answers only once it is in sync.
- A backend is one file in `brain/backends/` (2026-10-10): its choice, option flags, Docker container,
  installer flags and status line come from its declaration, and `verify_store_adapters` proves it by
  dropping two new backends into a copy of brain. Each role installs on its own
  (`--vector-store`, `--graph-store`, `NAME=URL`). What a backend is sent is decided by its role.
- `brain stores --docker` runs both in Docker with the data in plain folders under the brain's home, so
  removing containers or reinstalling Docker keeps it; a database that comes back empty is refilled.
- Measured on the author's notes (1,917 documents): identical answers on every question compared,
  similarity search 263 → 9 ms, one-hop neighbours for every document 4.6 → 0.43 s.

**Robustness**
- A measurement never changes shared state that other processes read (trial settings stay in-process).
- Long-running MCP servers pick up new code on disk by themselves.
- A fresh clone with an empty home passes every check that does not need personal data
  (`python3 tests/clean_room.py`: 44 green, 16 skipped, 0 red on macOS with a local Qdrant running, 2026-10-10).

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
- **Labelled quality checks on someone else's notes** — the checks that judge the threshold against
  labelled questions (`verify_threshold`, `verify_short`, `verify_score_honesty`, `verify_automatic_axis`)
  still skip without a person's own sample. A synthetic labelled set was considered and rejected: this
  repository never invents an evaluation query (§brain/proxy.py). Since 2026-10-10 CI catches a change that
  lowers recall another way — `tests/verify_quality_ab.py` asks the base code and the change the same
  inverse-cloze questions on this repository's own writing, frozen at the base (115 documents, 60
  questions). It catches a fall; a rise there is not evidence of an improvement. What would close this
  item is real people's labelled samples, shared with consent.
- **More reviewers** — area owners for storage, retrieval, privacy and the behaviour layer, so a
  high-impact change no longer needs an admin bypass (GOVERNANCE.md → *Adding a maintainer or an area
  owner — how*; the `Bypass: roster` record narrows by itself as the roster grows).
- Lower `tests/english_only_ratchet.txt` as the remaining Korean data lines move into locale or data files.

### 2. Quality
- Close the gap to OR'd `grep` on hit rate while keeping the reading load far lower (top-3 against hundreds
  of files): 32% against 82% with the same words (2026-10-12, above). The misses are mostly short
  prompts that share only common words with their answer. Measured that day on the question alone:
  - The judge re-ranking lexical search's top 20 raises the top 3 from 32% to 42% (9 gained, 2 lost; the
    same direction as +7 points on 2026-08-31). But it takes a median of 3.9 s against the hook's 5 s,
    and one call per recall. Where a caller passes its own synonyms (MCP `recall`) it does not help:
    with synonyms an agent wrote blind from the question alone, the top 3 went 53% → 50% (8 gained,
    10 lost). It lifts answers buried at ranks 13–14 and drops ones lexical search already had at 1–3.
    Not shipped. (The labels' own synonyms cannot answer this: they are the lane the answers were
    picked in.)
  - Fusing the semantic ranking by reciprocal rank does not help: 37% → 37% on the 57 short prompts
    (5 gained, 5 lost).
  - What a short prompt lacks is the turn before it, and the labelled set cannot test that: none of its
    91 prompts could be traced back to a transcript. Measuring it needs labels that keep their
    preceding turn.
- The two signals never meet (2026-10-13). On the same 66 questions the answer is somewhere in words 20 ∪
  meaning 20 for 89%, but in the top three for 33% (words) and 36% (meaning); words alone find 13 answers
  meaning misses and meaning 15 that words miss. MCP `recall` uses words only, the hook uses meaning only
  when words fall silent, and the judge reads a document's head while the passage that matched is often
  further in. Plan and the experiment fixed before it runs: [`notes/retrieval-fusion-plan.md`](notes/retrieval-fusion-plan.md).
- The judge's threshold cannot re-measure itself: 4 of the 34 hand-labelled "no memory needed" prompts
  (labelled 2026-08-12, when only lexical search existed) are answered by real memories, and the judge
  gives them 10. The noise floor then sits at the top of the scale, so calibration refuses to ship
  (measured again 2026-10-12 on the model now answering). Labels are fixed by a person reading them.
  Without labels it is no better (2026-10-13): real prompts paired with unrelated memories also reach 10
  (2 of 28), so the highest score of a no-answer control is the wrong statistic with or without labels.
  The hook now records what the judge said below the threshold too, so a person's own prompts can become
  the sample.
- Measure short-query recall on real corpora in other languages; the method settings were first set on
  one corpus.
- Verify Windows on real hardware with a real host. CI's Windows clean room is required since 2026-10-10
  (first run 2026-10-07: 32 green · 15 skipped · 7 red; now 41 · 16 · 0 — the recall hook had been silent
  there, §hosts.utf8_stdio), but no person has run brain inside Claude Code or Codex on Windows yet.

### 3. Storage
- More backends, one file each (docs/STORAGE.md): every vector database the roadmap named is in. Another
  Bolt or Postgres database (Apache AGE for the graph role, for example) is one file now that both clients
  exist.
- Switching a role away from Memgraph in Docker leaves that container's `/etc/memgraph` volume (~52 KB, once
  per switch): Compose's `up --remove-orphans` removes the container but not its anonymous volume (measured
  2026-10-12, Compose 5.0.2; recreating a container on a settings change reuses the volume and leaves
  nothing). Removing the dropped service with its volumes before the switch would close it.

### 4. Checks to tighten
- Closed 2026-10-12: tests carried the author's own prompts as fixed control queries (kept on 2026-10-06
  so as not to invent any). `verify_threshold` now reads the person's own labelled no-memory set; the
  mechanism checks in `verify_vectors` and `verify_symmetry` use neutral sentences of the same shape; the
  rest were examples in comments. Korean lines in shipping files 112 → 96.
- Closed 2026-10-12: the author's note names and aliases sat in tests and one comment in `brain/` — and
  `verify_aliases` and `verify_end_to_end` ★asserted★ three of the author's aliases and a link to one of
  the author's notes, so anyone else with notes got red rows. They now read the person's own declarations
  and bring their own link target, and `verify_public_scrub` gains a sixth axis: the publisher's own note
  names, aliases and prompts, read at run time, must not appear in the files that ship.
- The five listed on 2026-10-07 were closed on 2026-10-10 (provider keys masked in #16; checks
  measure a private copy of the index, skip without a set, and count requests instead of timing them).
- Closed 2026-10-12: the Docker check left containers and volumes behind on the machine that ran it — its
  egress watchers ran forever once their `docker run` client was killed (74 ran for 37 hours on the
  author's machine), and every `down` left Memgraph's `/etc/memgraph` volume. CI never showed it: its
  machine is thrown away. Watchers are named and removed, end by themselves, `down` removes volumes, and
  ⑨ counts what is left against a control that leaves one container and one volume on purpose.
- Closed 2026-10-12: the judge's daily count lived in the index, so a measurement on a private copy
  spent the provider's quota where the live counter never saw it — the live count read 190 of 500 when
  the provider's wall came, and the hook's second stage went quiet until Pacific midnight. The count
  now lives in one ledger file that every copy of an index shares (`BRAIN_LEDGER`, or
  `judge-ledger.db` in the brain's home), and `verify_measure_isolation` ④ holds it down.
- Closed 2026-10-12: the judge is the model that answered, not the name asked for. The default judge name
  `gemini-flash-lite-latest` is moved by the provider (asked on 2026-10-12, `gemini-3.5-flash-lite`
  answered), so a threshold "measured for this judge" and the cached scores could not say which model
  they came from. Each provider names the answering model; brain now keeps it, files cached scores
  under it, stamps the threshold with it, records it on every scorecard row, and shows it in
  `brain engines`.
- Closed 2026-10-12: `brain triggers` and the self-healing pass read a declaration with their own pattern
  and missed the one Claude Code writes, indented under `metadata:` — 8 working phrases were reported
  missing (#20). They now use the indexer's parser, and CI runs that reader on every platform.

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
