# Setup prompts — paste these into a coding-agent session

`./install.sh` assumes a **standard layout**. Other people's machines are laid out
differently, and sometimes a conversation finds things a script cannot. Paste the
block below into your agent session instead.

---

## ① First install

> Copy the whole grey box below and paste it into your coding agent.

```
I want to install local-brain, a local memory and retrieval brain, on this machine.
Repository: <path where you cloned it — e.g. ~/tools/local-brain>

What this tool is for: as an agent's memories grow (an index file plus memory/*.md),
the index file hits the read-size limit and gets compressed. Every compression pushes
entries out, and you end up with memories that exist as files but that nobody can
recall any more. local-brain replaces the list with search, so that limit stops
mattering. Zero dependencies — python3 standard library only.

Work through the steps below. Show me the result of each one, and ask me when
something is ambiguous.

1. Prerequisites
   - is python3 3.8+ available?
   - is the agent CLI available? (if not, just tell me the MCP registration command)

2. ★Find where my memories are★
   - Coding agents keep per-project memories under a home directory of their own
     (for Claude Code: ~/.claude/projects/<path-with-slashes-replaced>/memory/).
   - List the candidates, pick the one with the most .md files, and ask me
     "is this the right one?" before proceeding.
   - If there are none, say so — that just means there are no memories yet, and
     they can be added later.

3. ★Find what else is worth indexing★
   Look around my machine and propose candidates. Not everything has to go in —
   I decide what does.
   - markdown documentation folders (docs/ and the like)
   - personal wikis and notes (an Obsidian vault, for instance)
   - project rule files (CLAUDE.md / AGENTS.md)
   For each candidate, **count the files** and show me the number. Registering an
   empty folder achieves nothing.

4. Write config.json
   - include only what I picked
   - prior (source weight) goes like this: personal memories 1.45 · project rules
     1.25 · docs 1.0 · wiki 0.85 — the more a human curated it, the heavier it is
   - use config.example.json as the template

5. Index it and show me the result: ./bin/brain index --full
   Report document count, term count, and documents per source.

6. ★Verify that recall actually works★ — this step matters most
   - pick any 5 of my memories, search using each one's own description, and check
     that the memory itself comes back near the top
   - then ask me "what have you been working on lately?", run a real recall on my
     answer, and show me the result. I will judge by eye whether it is relevant.

7. Register MCP: <agent-cli> mcp add brain --scope user -- <repo>/bin/brain-mcp
   Then confirm it reports Connected.

8. Wire the automatic recall hook ★only if I say I want it★.
   Explain it to me first: it runs on every prompt and quietly adds one or two
   memories, but only when the score clears a threshold that is calibrated to my
   own corpus. If I say yes, wire it with ./install.sh --with-hook, and
   ★back up my settings file and preserve any existing hooks★.

⛔ Important:
- Do not overwrite my settings file wholesale. If hooks already exist, add beside them.
- Do not put a path in config.json that does not exist. It silently indexes nothing
  and nobody notices.
- Do not skip step 6. "Installed" and "working" are different claims.
```

---

## ② Later, to tidy up your memories

```
Call brain_suggest to review the state of my memories, and tell me what should be
done with each item — but ★check with me before changing anything★.

Please separate them like this:
- stale evidence: which ones need re-confirming versus which can simply be dropped
  (rules and lessons stay true over time; state, numbers and progress reports age)
- broken [[links]]: was the target renamed, or has it not been written yet?
- suspected duplicates: confirm they really say the same thing, and if so propose
  which one to merge into
- never-recalled memories: useless, or merely worded so they cannot be found?
```

---

## ③ Saving a memory (day to day)

With the hook or MCP wired you rarely need to ask, but when you want to be explicit:

```
Save what we just worked out with remember — ★only the parts that stay true later★.
Do not save momentary numbers or one-off state. If related memories exist, link them.
```

---

## Why a prompt instead of just the script

| | `./install.sh` | This prompt |
|---|---|---|
| Standard layout | ✅ fast and deterministic | unnecessary |
| Unusual layout | detection can miss | ✅ looks around and asks |
| Deciding what to index | guesses by rule | ✅ a human chooses |
| Verification | automatic (recall by self-description) | ✅ plus a human's own eyes |

★Both is fine★ — installing with the script and then tidying with prompt ② is the
most common combination.
