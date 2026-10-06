# local-brain

A local memory and retrieval layer for coding agents — and, unusually, one that
**measures whether it actually beats what you had before** instead of assuming it.

**Zero third-party dependencies.** Standard library only, runs on the system
`python3` (3.8+). No pip install, no build step, nothing phoning home unless you
choose an AI engine yourself — Gemini, OpenAI, Anthropic, or a local server.

```bash
git clone https://github.com/honeyground-org/local-brain ~/tools/local-brain
cd ~/tools/local-brain && ./install.sh --with-hook --with-guard
```

---

## What it does

It indexes your notes, memories and project docs into a local SQLite file, and
surfaces the relevant ones **automatically, as you type** — through a host hook,
not by you remembering to ask. Two more layers sit on top:

- **Behaviour layer** — right before a risky action (a push, a rebase, a
  migration), the guidance you once wrote about that action is put in front of
  the agent. The rule matches the tool call; the *wording* lives in your memory
  file, so editing the memory changes what is said. No rules ship with brain —
  they are learned from your own tool history (`brain rules --discover`), so on
  a fresh install this layer stays silent until it has something of yours to say.
- **Self-calibration** — thresholds are measured against your own corpus, not
  hardcoded. A score that means "confident" on 900 documents means nothing on 40.

### Why "measures itself" is the point

Retrieval tools are easy to believe in and hard to verify. This one ships with a
scorecard that compares it against the alternatives you actually had — a flat
index file plus `grep`, and the coding agent working alone — on accuracy, recall,
how much text had to be read, and speed. `brain score --compare` prints that
table. Every check in `tests/` runs a **control group first**: if a check cannot
fail, its green result is not evidence.

---

## Commands

All commands work from any directory once installed (`brain <command>`).
`brain help` prints this same grouping with live paths.

### Everyday

| Command | What it does |
|---|---|
| `brain status` | Health check — what to fix, separated from what to merely watch |
| `brain dashboard` | Status + performance comparison as a local HTML page, opened in your browser |
| `brain recall <text> -t <synonyms>` | Recall, right in the terminal. ⛔ Always pass `-t` — see below |
| `brain recent` | Chronological view — the answer to "what was I doing?", which has no keyword |
| `brain neighbors <name>` | Graph neighbours — widen from a hit to its related context |
| `brain why <file>` | Why is this file the way it is — purpose, principles, and what changed |

### Measuring and comparing

| Command | What it does |
|---|---|
| `brain score` | Raw comparison table + the 7-axis score |
| `brain score --compare` | brain ↔ grep ↔ agent-alone: accuracy, recall, text read, speed |
| `brain score --axes` | The 7 axes only. ⛔ The composite is for comparing *your own* points in time |
| `brain score --full` | Measures the accuracy axis for real — calls the judge model, spends budget |
| `brain score --history` | Compare points in time: what moved after you changed something |
| `brain budget` | Judge-model daily budget, and the three thresholds the tool sets for itself |

### Feeding and fixing memory

| Command | What it does |
|---|---|
| `brain index` | Incremental index. `--full` rebuilds everything |
| `brain detect` | Find what is worth remembering, with evidence. `--add` registers it |
| `brain suggest` | Self-improvement suggestions — stale, orphaned, duplicated memories |
| `brain triggers` | Every declared trigger phrase, plus checks (uniqueness, resolution, shadowing) |
| `brain index-audit` | Is each line of your index file still reachable if you delete it? |
| `brain feedback <name> useful\|wrong\|stale` | Rate a recall — it changes future ranking |

### Corpus (what is in scope)

| Command | What it does |
|---|---|
| `brain sources` | Registered corpora + measured reality (file count, indexed documents) |
| `brain add <path>` | Add a corpus — `--name --prior --include --exclude --depth --no-embed` |
| `brain remove <name>` | Remove a corpus by name or path |

### Semantic search and calibration

| Command | What it does |
|---|---|
| `brain engines` | Which AI engine fills each role (embed · judge), what it is sent, how to change it |
| `brain vec status\|build\|calibrate\|search` | Meaning-based search via remote embeddings; `calibrate` also re-measures the judge |
| `brain calibrate` | Re-measure the lexical threshold against this corpus |
| `brain eval-init` | Draft an evaluation set from *your* history (someone else's gold is theirs) |

### Behaviour layer

| Command | What it does |
|---|---|
| `brain rules` | Active rules — learned from your use, or written by you in `rules.json` (none ship) |
| `brain rules --discover` | Ask the judge model for new rule candidates (incremental, cached) |
| `brain rules --stale` | Rules whose actions nobody has performed in the period |
| `brain rules --approve <signal>` / `--disable <id>` | Turn one on / off. ⛔ Disabled stays disabled |
| `brain rules --auto` | Promote candidates over the threshold without a human |

### Moving between machines

| Command | What it does |
|---|---|
| `brain export` | What you *earned* in one file — calibration is deliberately excluded |
| `brain import <bundle>` | Merge another machine's bundle. ⛔ Dry-run by default; `--apply` to commit |

### Safety and upkeep

| Command | What it does |
|---|---|
| `brain privacy` | Audit the outbound door — which memories hold secret-shaped values (values never shown) |
| `brain i18n --check` | Translation catalogs — missing keys, unused keys, placeholder mismatches |

⛔ **Always pass `-t` (synonyms) to `recall`.** Memories are written in different
words than your question. On the author's own notes (9 questions): 2/9 found without synonyms, 8/9 with.

---

## Languages

Two different things are localized, and they are **not** at the same maturity.
This table says what has been measured, not what was intended.

### Interface (dashboard, CLI status output)

Six languages ship as JSON catalogs in `brain/locales/`:

| | English | German | Spanish | French | Japanese | Korean |
|---|---|---|---|---|---|---|
| Dashboard | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

English is the source of truth; a missing key falls back to English rather than
breaking the screen. The language is chosen by, in order: `"language"` in
`config.json` → `BRAIN_LANG` → English. Your OS locale is deliberately not
used: another language is something you choose (`./install.sh --lang ko`),
never something inferred.
`brain i18n --check` counts missing keys and placeholder mismatches, because a
translation nobody counts will rot as soon as the code moves.

### Search (tokenizer)

Localizing the screen is not localizing the search. Retrieval quality per language:

| Language | State | What is missing |
|---|---|---|
| English | 🟢 Works | — |
| Korean | 🟢 Works | — |
| German | 🟡 Mostly | Compound splitting (`Donaudampfschifffahrt` stays one token) |
| Spanish · French | 🟡 Mostly | Inflection; words are space-separated so the damage is limited |
| Japanese | 🔴 **Not yet** | No spaces, so a document collapses into one token — needs n-gram splitting |

⛔ Only English and Korean have a labelled evaluation set behind them. The rest
are marked by inspection of the tokenizer, not by measurement — which is why they
are not claimed as "supported".

---

## What runs automatically

- **Recall hook** — on every prompt; adds memories only when the score clears the
  calibrated threshold.
- **Index refresh** — at session start, and again just before recall if a new
  memory appeared. Write a memory file by hand and the next recall finds it.
  ⛔ Without this refresh the brain does not know about the memory you just saved
  — that gap was found the hard way.
- **Scheduled jobs** — semantic indexing and rule discovery, once a day.
  ⛔ Verify them by their **log**, not by whether they are registered:
  `brain status`. On macOS use `launchd` (`install/launchd.sh`); `cron` silently
  skips runs that were due while the machine slept.

---

## MCP

```bash
claude mcp add brain --scope user -- "$(pwd)/bin/brain-mcp"
```

Tools: `recall` · `remember` · `neighbors` · `brain_status` · `brain_suggest` ·
`brain_feedback` · `reindex` · `timeline`

The MCP server and the CLI call the **same core functions** — a rule implemented
in two places only ever gets fixed in one of them.

---

## Install

```bash
./install.sh                            # detect → config → index → register MCP → verify
./install.sh --with-hook --with-guard   # fully local: no AI engine, nothing leaves the machine
./install.sh --all --embed gemini --judge gemini        # + meaning-based search (GEMINI_API_KEY)
./install.sh --all --embed openai --judge anthropic     # mix providers (OPENAI_API_KEY, ANTHROPIC_API_KEY)
./install.sh --help                     # choose what to enable
```

A key in your environment turns nothing on by itself — an engine is used only
when you name it (see *AI engines and their roles* below).

## AI engines and their roles

Word search, the hook, the behaviour layer and every self-measurement run
locally. An outside AI model is used for exactly **two jobs**, and you choose
which model fills each one — or none:

| Role | What it does | What it is sent | Used by | Engines |
|---|---|---|---|---|
| `embed` | turns notes and questions into vectors, so a question finds a note written in other words | note chunks (≤1200 chars) and each searched question | meaning-based search | `gemini` · `openai` (or any OpenAI-compatible server) · `none` |
| `judge` | reads a question and up to 20 candidate notes and scores 0–10 whether each one answers it | the question and candidate excerpts (≤420 chars each) | two-stage recall when word search is silent · `brain score --full` · behaviour-rule discovery | `gemini` · `openai` (or any OpenAI-compatible server) · `anthropic` · `none` |

Secret-looking values are masked before anything is sent (`brain privacy`).

```bash
brain engines                                     # which engine fills each role, where that choice came from, what it sends
brain engines --set judge=anthropic               # Claude as the judge (ANTHROPIC_API_KEY)
brain engines --set embed=openai --base-url http://localhost:11434/v1 --model nomic-embed-text
                                                  # a local Ollama — nothing leaves the machine, no key
brain engines --set embed=none judge=none         # turn both off
```

- **Nothing is chosen for you.** A key that happens to be set (`GEMINI_API_KEY`,
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — often another tool's) is listed as
  *found, not used* until you choose that engine.
- **A judge's threshold belongs to that judge.** Scores from different models
  (or the same model asked a different question) sit on different scales, so the
  threshold is stamped with the judge it was measured on. After switching, the
  judge adds nothing until `brain vec calibrate` measures it again.
- **A different embedder is a different vector space** — after switching, run
  `brain vec build`.
- Default models are conveniences, not recommendations: `gemini-embedding-2` /
  `gemini-flash-lite-latest`, `text-embedding-3-small` / `gpt-4.1-mini`,
  `claude-opus-5-5` (as a judge, at low effort). Override with `--model`.

Idempotent — safe to run repeatedly; it merges into your existing settings rather
than overwriting them. If your machine is laid out unusually, paste
`SETUP-PROMPT.md` into your coding agent instead and let it install by looking around.

Data lives in `~/.brain/` (or under your host's home — `brain help` prints the one in use). The SQLite
index is a derivative — delete it and `brain index --full` rebuilds it.

---

## Who sets which number

Nothing here is one person's setting. Every number comes from one of four places, in this order:

| Owner | Examples | Where you change it |
|---|---|---|
| **You** | which kinds the graph colours · how fast each kind goes stale · which sections of your index are standing rules · which kinds count as rules | `config.json`: `graph_kinds` · `stale_days` · `index_sections` · `directive_kinds` (or `<!-- brain: … -->` after a heading) |
| **Your data** | the hook threshold (noise floor of *your* corpus) · which filename prefixes are kinds · what "short" means (your shortest 30% of prompts) · the corpus's languages | measured — `brain calibrate`, `brain status` |
| **Your host / engine** | which index the host loads by itself and its measured read limit · its memory folders, rule files and command prefixes · a provider's rate limits (only Gemini's are known; any other provider is unpaced until it refuses, and the refusal teaches the real limit) | the host adapter (`brain/hosts.py`) and `brain engines` |
| **The method** | the margin over the noise floor (1.35) · the expected hook firing band (15–75 %) · the regression guard (1.5 hits) · the identity bonus · lexicon and proxy settings | environment variables — `BRAIN_CALIB_MARGIN`, `BRAIN_FIRE_RATE_OK="15,75"`, `BRAIN_ID_BOOST`, `BRAIN_LEXICON_*`, `BRAIN_PROXY_*`, `BRAIN_RULE_*` |

The method settings were chosen once on a reference corpus and are **not re-fitted to your sample**:
choosing a threshold with the sample you then judge it on is overfitting. Your own sample is used the
other way round — it can only *refuse* a calibration that does worse than the one in place. When
there is no ruler at all (a corpus too small to measure), the hook stays silent rather than guess.

---

## Tests

Each of these states its own pass criteria in the file, and runs a control group
before trusting its result.

Two kinds live side by side. Most run anywhere on fixtures (`verify_host_neutral`, `verify_user_values`,
`verify_corpus_kinds`, `verify_index_roles`, `verify_first_day`, `verify_engines`…). Others measure
**your own** notes, labelled questions or session history — on a machine without them they stop with
**exit code 77 (skipped)** and say what is missing, rather than pass on nothing or fail for no reason.
A fresh clone with an empty home runs 32 green, 13 skipped, 0 red (measured 2026-10-06).

```bash
python3 tests/verify_recall.py          # recall quality regression
python3 tests/verify_reachability.py    # "still reachable after removal from the index?"
python3 tests/verify_vs_grep.py         # ★does it beat grep★ — reads less, hits more
python3 tests/verify_related.py         # the graph must not steal the answer's slot
python3 tests/verify_vectors.py         # semantic search: 3 harm checks + 1 usefulness
python3 tests/verify_guard.py           # every behaviour rule actually fires
python3 tests/verify_engines.py         # each AI engine adapter, and that a stray key chooses nothing
python3 tests/verify_ruledisc.py        # does discovery re-find the hand-written rules?
python3 tests/verify_privacy.py         # intercepts HTTP and inspects the real request body
python3 tests/verify_i18n.py            # translations do not rot silently
python3 tests/verify_english_only.py    # no Korean left in any shipping file
python3 tests/verify_first_day.py       # someone else's first day: install, index, every screen renders
```

---

## Layout

| File | Role |
|---|---|
| `brain/textindex.py` | Tokenization — Korean 2/3-grams + ASCII words. **Why not FTS5** is documented here |
| `brain/store.py` | SQLite schema, incremental indexing, migrations, evidence-date extraction |
| `brain/search.py` | IDF-weighted recall + one-hop graph bridge |
| `brain/health.py` | Distortion diagnostics (orphans, broken links, stale evidence, duplicates) |
| `brain/privacy.py` | The outbound door — masks **values** before embedding/judging; key names survive |
| `brain/guard.py` | Behaviour layer — surfaces the guidance attached to an action |
| `brain/ruledisc.py` | Discovers behaviour rules from use: transcripts × memories → judge |
| `brain/hosts.py` | Host adapters — Claude Code, Codex, generic |
| `brain/i18n.py` | Message catalogs, English-first |
| `brain/server.py` | MCP over stdio (JSON-RPC, no SDK) |
| `brain/cli.py` | Terminal entry point — same core as the server |
| `config.json` | Single source of truth for what gets indexed. A new corpus is one entry |

## Privacy

Nothing leaves your machine unless you choose an AI engine (`brain engines`). When you do,
everything on the way out passes through `brain/privacy.py`, which masks
credential-shaped **values** while keeping key names searchable. Per-source
(`--no-embed`) and per-document (`embed: false`) opt-outs exist for anything that
should never be sent at all. `brain privacy` audits what is still exposed —
and never prints the values themselves.

### What a key turns on ⛔ read this before you set one

Word search is **always local** — it never needs a key and never opens a socket.
Two things can leave, and only through an engine you chose (`brain engines`):

| | |
|---|---|
| turns it on | choosing an engine — `--embed` / `--judge` at install, or `brain engines --set`. Its key comes from the provider's variable or `secrets.json` (0600) |
| what then goes out | **embed**: chunks of **every source, by default** — your personal memory included (`embed: true` is the default and no corpus is special-cased). **judge**: the question and short excerpts of up to 20 candidate notes |
| what does **not** go out | anything a source or a document marked `embed: false`; and everything, always, while no engine is chosen. A local OpenAI-compatible server (`--base-url http://localhost…`) keeps both on the machine |
| who sends it | `brain vec build` and the daily `brain-vec-daily` job (embed); recall when word search is silent, `brain score --full` and the daily `brain-rules-daily` job (judge) |

⛔ **A key that is merely present is never used.** `GEMINI_API_KEY`,
`OPENAI_API_KEY` and especially `ANTHROPIC_API_KEY` are often another tool's —
your coding agent's own, say. Choosing an engine because its key happened to be
set would be the one path where content leaves a machine whose owner never
decided it should, so brain lists such keys as *found, not used*.
(The scheduled jobs are narrower still: they do not inherit your shell, so they
only see a key written into `secrets.json`.)

Turning a corpus off is one field — and `brain privacy` prints the result, so you
never have to trust this paragraph:

```bash
brain add ~/work/secret-docs --no-embed    # or "embed": false in config.json
brain privacy                              # → blocked per source: [...]
```

Once an embedder is chosen, the default is "everything may be embedded" — deliberately
(2026-09-14): the alternative is a brain whose semantic half is silently off,
which is the failure this project keeps measuring for. Until one is chosen,
`brain engines` and the installer say plainly that it is off and how to turn it on.

## License

Apache License 2.0 — the full text is in [LICENSE](LICENSE). Use it, change it, ship it
inside something commercial; keep the notice and say where it came from.
