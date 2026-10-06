# Prompt — paste this whole file into your coding agent

Install local-brain, a local memory and retrieval brain, on this computer. Do the
work yourself, and only come back to me when a prerequisite is missing.

## 0. What this is (summarise this back to me once, before installing)

It removes the problem where **an agent cannot remember past work and answers from
stale information**. It folds the memory files under your agent's project home, the
project `docs/`, the `CLAUDE.md` rules and your personal notes into one index, and
brings those memories back four different ways.

| Layer | What it does | Needs a key |
|---|---|---|
| **Lexical search** | Own inverted index (BM25 + CJK bigrams + transliteration bridge) | No |
| **Behaviour layer** | Right before an action like `terraform` or `git push`, surfaces the memory of being burnt there | No |
| **Related context** | Follows the `[[links]]` a human wrote and attaches what they point to | No |
| **Semantic search** | Finds documents that share no words with your question (embeddings → judge) | **Yes (remote)** |

**Scorecard measured on the author's corpus** (53 questions · 605 memory files):

| Method | Correct | Documents you must read |
|---|---|---|
| grep, one keyword | 16/53 (30%) | 1 |
| grep, all content words OR'd | 42/53 (79%) | **126** |
| brain `recall` + synonyms | 42/53 (79%) | **3** |
| brain hook — fires without being asked | 22/53 (42%) | 1 |

The point is not "it gets more right" — it is that **it reads 40× less**
(126 documents × 4.2KB average ≈ 100k tokens, which nobody can actually read). And
the last row is a capability grep **does not have at all**.

It also removes the size problem structurally: **605** memories, an index file of
**14.2KB**, only **4%** of memories named in it — and a **100% reach rate**
(all 200 sampled memories came back first). Saving one more memory grows the index
by **0 characters**.

## 1. Prerequisites — check these, and stop and tell me if one is missing

1. `python3` (3.9+). ⛔ **There are no other dependencies** — standard library only.
2. `git`.
3. Your agent's CLI (for MCP registration). Installation works without it; the
   registration command is printed instead.
4. The repository — ask me where it is if you cannot reach
   `https://github.com/honeyground-org/local-brain`. Written as `<REPO>` below.
5. (optional) An AI engine — only for semantic search and the second-stage judge: Gemini, OpenAI or any
   OpenAI-compatible server (a local one sends nothing out), or Anthropic for judging. Nothing is used until
   it is named (`brain engines` shows the roles and what each one sends). **Everything else works without it.**

## 2. Install

```bash
git clone <REPO> ~/work/local-brain     # anywhere you like
cd ~/work/local-brain
./install.sh --help                      # read what you are turning on first
```

**Confirm one of these two combinations with me before running it.**

```bash
# ⓐ Fully local — no key, no data leaves this computer
./install.sh --with-hook --with-guard

# ⓑ Everything, including meaning-based search — name the AI engine for each role
#    (⛔ note chunks and excerpts are sent to that provider; a local server keeps them here)
./install.sh --all --embed gemini --judge gemini        # or openai · anthropic (judge) · none
#    then `brain engines` shows each role, its engine, and what it sends
```

What `install.sh` does (idempotent — safe to run repeatedly):

1. Check prerequisites
2. **Detect what is worth remembering** — memory directories, `docs/`, `CLAUDE.md`,
   Obsidian vaults, note folders — and write `config.json`
   (if no memory directory exists, it **creates one**)
3. Index
4. Register MCP (`brain` — `recall`, `remember`, `neighbors`, `brain_status`, …)
5. Wire hooks — automatic recall (UserPromptSubmit) + session start +
   the **behaviour layer** (PreToolUse on shell/edit tools)
6. Semantic search — store the key in `secrets.json` (mode 0600, **outside the
   repository**) and run the first ingest
7. Code-index syncing — worktree added/removed → index syncs; code edited → reindex
   after 60 seconds
8. Scheduled job — vector ingest relay, daily at 09:00
9. Verify — ask a sample of memories using their own descriptions and report how
   many came back in the top three

⛔ **It never overwrites your existing configuration.** Settings files and scheduled
jobs are backed up and then **merged**.

## 3. Report back to me after installing

1. `./bin/brain sources` output — which corpora were picked up, and **whether any
   path does not exist** (⛔ a missing path is not an error; it is a **silent empty
   index**)
2. `./bin/brain detect` output — anything else worth attaching. To attach:
   `./bin/brain add <path> --name notes --prior 0.9`
3. The result of verification step 9
4. If semantic search is on: the ingest ratio from `./bin/brain vec status`

## 4. Privacy — check this one with me

Semantic search **sends document bodies to a remote embedding API**. Three choices:

- **Send nothing** — leave out `--with-vectors`. Lexical search, the behaviour layer
  and related context all still work, **fully locally** (the 79% / 3-documents row
  above is measured in exactly this state).
- **Send some** — a corpus registered with `./bin/brain add <path> --no-embed` stays
  **searchable but never leaves**.
- **Send everything** — `--all`.

## 5. Produce a scorecard on your own corpus (recommended)

The numbers above are from **the author's corpus**. To check them on your data:

```bash
./bin/brain eval-init                 # draft questions from your own history
# open tests/eval/short.json and fill in gold (the answer memory's name) — ~20 minutes
python3 tests/verify_vs_grep.py       # side by side against the old way (grep)
python3 tests/verify_reachability.py  # reachable even when not in the index?
python3 tests/verify_guard.py         # does every behaviour rule actually fire?
```

⛔ **Do not invent evaluation queries.** The author once measured with 10 invented
questions and produced a false alarm that "the hook never fires". Measured against
real history, the median was 11.3.

## 6. ⛔ Traps worth knowing up front (all of them hit for real)

- **The hook only fires from a new session** (or after `/clear`). Silence right after
  installation is normal.
- **Semantic search stays silent until it is calibrated.** Not attaching memories
  while the threshold is unknown is the safe default. Once ingest passes 90%, the
  index **arms itself**.
- **Free tiers**: 1,000 embedding chunks per day (a large corpus takes several days),
  15 judge calls per minute. Hitting a limit is treated as **a state, not a failure**,
  and resumes the next day.
- **Pass synonyms when recalling** — `recall("question", terms=["synonym", "spelling"])`.
  Memories are written in different words than your question (measured: 2 hits
  without terms, 8 in the top three with them).
- **Temporary checkouts (`/tmp`, `/private/tmp`) are not indexed** — no point paying
  to index what the OS is about to delete.
- **Half the value is the habit, not the tool.** This brain answers well because the
  memory files are written as *"⛔ do this and it breaks like that, measured X"*.
  Written any other way, it becomes a search engine over your own documents — still
  useful, but much smaller.

## 7. Undoing it

```bash
./bin/brain remove <name>                # drop one corpus
# scheduled job: remove the brain-vec-daily entry from your scheduler
# hooks: restore the settings backup the installer made (.bak.*), or delete just the
#        brain-hook / brain-guard entries
rm -rf ~/.brain                          # delete index and key (memory files untouched)
```

⛔ **Your memory files are never deleted.** The index is a derivative and can be
rebuilt at any time with `./bin/brain index --full`.
