# Codex host support — what was measured, what was wrong, what is still open

**Date:** 2026-09-21 · **Environment:** macOS 15 (arm64), codex-cli **0.154.0**, Claude Code, both
hosts installed on the same machine · **Commit:** `c667027`

This is a hand-off for whoever owns the host-adapter layer. The 2026-09-02 notes in
`brain/hosts.py` were written from inspection; this round **wired Codex up for real and read the
payloads**, so three of those notes turned out to be wrong. All numbers below are measured on one
machine on one day — treat them as observations, not constants.

---

## 1. Summary

The brain was not reaching Codex at all, and it was not a configuration oversight alone — three
independent layers were each silently empty:

| layer | state before | why it stayed invisible |
|---|---|---|
| MCP | not registered in `~/.codex/config.toml` | nothing errors when a tool simply isn't there |
| hooks | zero brain entries in `~/.codex/hooks.json` | same |
| learning | `TRANSCRIPTS` hardcoded to `~/.claude/projects/*/*.jsonl` | a host that is never read looks identical to a host where nobody works |

Meanwhile Codex was carrying roughly half the actual work on this machine: **5,600 tool calls in
ten days**, against 6,008 for Claude Code. Feeding the last 8 Codex prompts into `recall` put a
memory above the hook threshold for **7 of them** (9.5–39.3 points against a threshold of 9.22),
so the cost was not hypothetical — those sessions ran without answers that already existed.

---

## 2. Measured facts about Codex 0.154.0

These are the transferable part of this report.

### 2.1 The hook payload is field-identical to Claude Code

```json
{
  "session_id": "01a0c266-…", "turn_id": "01a0c266-…",
  "transcript_path": "/Users/…/.codex/sessions/2026/09/21/rollout-….jsonl",
  "cwd": "…", "hook_event_name": "PreToolUse",
  "model": "gpt-6-astra", "permission_mode": "bypassPermissions",
  "tool_name": "Bash", "tool_input": {"command": "echo hi"},
  "tool_use_id": "exec-a070376e-…"
}
```

`UserPromptSubmit` carries `prompt` under the same names. **No field renaming is needed.**

### 2.2 Tool names are *mixed* — this is the trap

| action | name Codex actually sends | what `hosts.py` assumed |
|---|---|---|
| shell | **`Bash`** | `shell` (does not exist) |
| edit | **`apply_patch`**, with the patch inside `tool_input.command` | `apply_patch` (correct) |
| read / notebook | **not observed** | `Read` / — |

So Codex speaks Claude Code's name for the shell and its own name for edits. On a machine with
both hosts installed, an unpinned `active()` resolves to `claude-code` and **the shell rules keep
firing while every edit rule goes silent.** Half-working and quiet about it — worse than not
installed, because it looks installed.

### 2.3 Hook output: `additionalContext` is honoured

The JSON schema is embedded in the `codex` binary; `PreToolUseHookSpecificOutputWire` accepts
`additionalContext`, `permissionDecision`, `permissionDecisionReason`, `updatedInput`.

⛔ **Injected context does not appear in the rollout transcript.** Reading the session log is
therefore not a way to verify injection — see §4 for a probe that actually works.

### 2.4 Hooks may live in two files, and each entry is trusted by hash

- `~/.codex/hooks.json` (same `matcher` + `hooks[]` shape as Claude Code's `settings.json`), **or**
  `[[hooks.SessionStart]]` style tables in `config.toml`. Using both at once prints
  `warning: loading hooks from both …; prefer a single representation for this layer`.
- `config.toml` holds `[hooks.state."<file>:<event>:<i>:<j>"] trusted_hash = "sha256:…"`.
  **Editing a hook file invalidates that hash**, and a non-interactive `codex exec` then *blocks*
  (no output, no error) until a human approves. `--dangerously-bypass-hook-trust` runs anyway.
  This is the single biggest operational difference from Claude Code, and an installer that writes
  hooks and says nothing about trust leaves the user with a session that appears to hang.

### 2.5 MCP registration is a first-class command

```bash
codex mcp add brain --env BRAIN_HOST=codex -- /path/to/local-brain/bin/brain-mcp
codex mcp get brain      # enabled · transport · command · env
```

`--env` is the clean way to pin the host for the MCP path (no wrapper needed).

### 2.6 The session log needs its own reader

`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, one JSON object per line:

```json
{"timestamp":"2026-09-21T…","type":"response_item",
 "payload":{"type":"custom_tool_call","name":"exec",
            "input":"text( await tools.exec_command({cmd:\"git status\"}) )"}}
```

Two things break a Claude-shaped reader:

1. **`"tool_use"` never appears** (0 occurrences in an 898-line session).
2. **The command is not a field.** The dominant tool is `exec` (4,103 of ~5,600 calls in ten days),
   whose `input` is a *JavaScript snippet* calling `tools.exec_command({cmd:"…"})`. Edits arrive as
   a patch inside a quoted string (`*** Update File: <path>`).

Extraction over ten days on this machine: **2,411 commands and 612 patches** that were previously
invisible. `timestamp` is at the top level in both hosts, so date filtering needed no change.

---

## 3. Changes made (`c667027`)

1. **`hosts.pin(name)` / `hosts.pin_from_argv()`** — `--host <name>` is taken out of `argv` and
   pins the adapter; `BRAIN_HOST` is exported for anything spawned further down. A flag rather
   than an `env VAR=x` prefix, because the same hook line has to work on Windows.
   Wired into `brain.hook`, `brain.guard`, `brain.tail:cli_notice`; the three `bin/` wrappers now
   forward `"$@"` (only the `notice` call in `brain-session`, since `brain.cli index` uses argparse).
2. **`Codex.tool_names()` corrected** to the measured names, with the docstring recording what was
   measured versus what is still a candidate.
3. **`Host.transcripts()` + `Host.tool_calls(line)`** — transcript reading moved out of
   `ruledisc` and into the adapter, one implementation per host. `usage_from_transcripts()` now
   asks **every detected host**, and `scope["hosts"]` reports the per-host call count, so a zero can
   be read as "the reader cannot see this host" rather than "nobody works there".
   Re-measured: 10,290 calls over ten days (claude-code 3,796 · **codex 6,494**).
4. **Installer** writes `--host <name>` on every hook command it creates. Two follow-on fixes were
   needed and are worth knowing about:
   - the "already wired" test compared the **whole command string**, so appending a flag made our
     own older line look like a stranger's and would have wired a **second** copy of the guard;
   - the matcher was compared as a string, but a matcher is a **set of tool names** that grows over
     time (`Edit|Write|NotebookEdit` → `Edit|MultiEdit|Write|NotebookEdit`). It now matches on the
     command with the flag stripped plus matcher-set overlap, rewrites our own line in place, and
     only widens the matcher when our hook is the only one under that entry.
5. **`tests/verify_hosts.py` +12 rows**: the pin (including an unknown name being dropped
   silently), both transcript shapes, and **two control rows proving each reader is blind to the
   other host's shape** — without those, a parser that matched everything would score perfectly.

---

## 4. How it was verified — including one probe that was worthless

- **Behaviour layer:** editing a `.tsx` through Codex wrote the guard marker
  `<session-id>.edit-ui`, and that session id matches the Codex rollout filename. So a rule fired
  inside a real Codex session on an `apply_patch`.
- **Automatic recall:** the first attempt asked the model *"was there extra context this turn,
  YES/NO"*. It answered YES — and so did the control with the hook disabled. **A probe that cannot
  fail.** The model was inferring from the prompt.
  The working version plants something unguessable: a nonce written into a memory file with a
  declared trigger, then asked back.

  ```
  hook on  → QX7K2M9-ZR4T8V     (returned verbatim)
  hook off → NONE
  ```

  Recommend keeping this recipe for any future host: **never ask a model whether context arrived;
  plant something it could not invent and ask for it back.**

---

## 5. Open items

| # | item | note |
|---|---|---|
| 1 | **Hook trust is not handled by the installer** | after writing hooks, `codex exec` blocks until a human approves. The installer should say so, and ideally detect `[hooks.state]`. |
| 2 | **Installer's Codex MCP advice is outdated** | it prints a manual `[mcp_servers.brain]` TOML block using `python3 -m brain.server`. `codex mcp add … --env BRAIN_HOST=codex -- <bin/brain-mcp>` is a single command and can be run for the user. |
| 3 | **`read_file` / notebook names on Codex are unmeasured** | no such call appeared in the samples. Candidates are listed; a wrong name never fires, a missing one silences a rule. |
| 4 | **`cmd:[...]` array form would be missed** | only the quoted `cmd:"…"` form was observed. This undercounts, never miscounts — the safer direction, but it should be confirmed. |
| 5 | **Latent: the last-resort `entry_command` fallback for `brain-session` is a no-op** | it produces `<python> -m brain.tail` with **no subcommand**, and `brain/tail.py`'s `__main__` maps a missing argv to `lambda: 0`. Verified: running it does nothing, silently. Only reachable when neither the console script nor the POSIX `bin/` script exists. Fix is one line: emit `brain.tail notice` for that module. |
| 6 | **Codex injects but does not log** | anything that verifies injection by reading the rollout file will report a false negative. |

### Two checks that went red this week, unrelated to Codex

- `verify_automatic_axis` — the monotonicity row (`the new ruler writes the improvement down as a
  rise`) inverted on the fixed sample: `0.2312` vs `0.2399`. The corpus moved (1,438 → 1,481 docs)
  between measurements.
- `verify_vectors` ⑤ cost — `a cached query has no round trip (<120ms)` fails at 125/126/132ms over
  three runs, while the *uncached* call is 131–138ms. A 6–13ms gap is not a network round trip, so
  both are almost certainly served from cache and the row is comparing **process start-up cost**
  against an absolute limit. This is the same shape as the guard cost row that was fixed by
  measuring two poles instead of one constant, and it wants the same treatment.

### One product observation worth the roadmap

On the 57-question no-synonym sample: lexical alone 15/57 (26%), **semantic alone 0/57**, either
one 15/57. The semantic path does no harm (it never lowers automatic recall) but it is not
currently a lever for the reach axis — which is the heaviest axis and has now fallen two
measurements in a row (50.4 → 48.2 → 46.1) as the corpus grows.

---

## 6. Reproducing the measurements

```bash
# what Codex actually sends — plant a probe hook that just dumps the payload
#   ~/.codex/hooks.json → {"hooks": {"PreToolUse": [{"matcher": "", "hooks": [
#     {"type": "command", "command": "cat > /tmp/probe-$$.json"}]}]}}
codex exec --dangerously-bypass-hook-trust "run `echo hi` in the shell"

# the adapters, end to end
python3 tests/verify_hosts.py

# what rule discovery can now see, per host
python3 -c "import sys; sys.path.insert(0,'.'); from brain import ruledisc; \
            print(ruledisc.usage_from_transcripts(since_days=10)[1])"
```
