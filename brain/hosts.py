"""Host adapters — ★the brain must run no matter which LLM tool it's plugged into.★

## Why this file exists (user requirement, 2026-09-02)

> *"This brain system has to be able to plug into any LLM that comes along. Right now it works
>  with claude code, but later it also has to connect with codex."*

The brain's ★substance★ (index · search · scoring · calibration) was never host-specific to begin with. What's
tangled up is only ★four edges★, and those four are pulled together here in one place.

    ① where it lives         data home · memory folder
    ② what it reads          ★real user turns★ — the source for calibration, eval samples, rule discovery
    ③ how it hooks in        hooks (attaching recall · the behaviour layer) — event names and config file
    ④ what it calls          tool names (whether a behaviour-layer rule sees `Bash` or `shell`)

## Measured 2026-09-02 — both were on this machine, so ★it was checked, not guessed★

| | Claude Code | Codex |
|---|---|---|
| home | `~/.claude` | `~/.codex` |
| prompt history | `projects/*/**.jsonl` (each line `type:user`) | ★`history.jsonl`★ (`{session_id, ts, text}`) |
| session log | the same file | `sessions/YYYY/MM/DD/rollout-*.jsonl` |
| hook config | `settings.json` | ★`hooks.json`★ — ★the same shape★ (`matcher` + `hooks[]`) |
| hook events | UserPromptSubmit · SessionStart · PreToolUse · PostToolUse | ★all four exist★ |
| MCP | `.mcp.json` / settings | `[mcp_servers.*]` in `config.toml` |

★So the brain's core (automatic recall + the behaviour layer) holds up exactly the same on Codex.★
The hook file's shape is even the same, so the install only ★differs by path★.

## Measured 2026-09-21 — ★wired up for real and the payloads were read★ (codex-cli 0.154.0)

The 09-02 table was right about the shape and ★wrong about three things that matter★, each of
which fails quietly rather than loudly:

| | what was assumed | what it actually is |
|---|---|---|
| tool name, shell | `shell` | ★`Bash`★ — Codex hands the hook Claude Code's own name |
| tool name, edit | `apply_patch` (guessed) | ★`apply_patch`★ (confirmed) — so unpinned, ★only the edit half goes silent★ |
| hook payload | unknown | ★identical field names★: `tool_name` · `tool_input` · `prompt` · `session_id` · `transcript_path` |
| hook output | unknown | ★`hookSpecificOutput.additionalContext` is honoured★ (schema is embedded in the binary; verified end to end — the model answered from injected memory) |
| hook config | `hooks.json` | `hooks.json` ★or★ `[[hooks.*]]` in `config.toml`; using both at once earns a warning |
| hook trust | — | ⛔ ★each entry is trusted by hash★ (`[hooks.state]` in `config.toml`). Edit the file and a
|   |   | non-interactive `codex exec` ★blocks★ until a human approves, or `--dangerously-bypass-hook-trust` |
| session log line | — | `{"payload":{"type":"custom_tool_call","name":"exec","input":"<JS>"}}` — ★the command lives inside a JS call★, and an edit inside a quoted patch (§Codex.tool_calls) |

⛔ The lesson underneath: ★"the same shape" and "the same names" are different claims★, and only
one of them had been measured. Half a mapping produces a half-working behaviour layer, which is
worse than none — it looks installed.

## ⛔ What this file protects

- **The brain runs with no host at all.** `generic` always exists — used via CLI and MCP alone.
  (With no host hooks, only ★automatic recall★ is missing — asking and searching still works.)
- **Never invents what isn't there.** With no prompt history, it returns an empty list —
  calibration's positive control group is gone, and `guard_status` says so.
- **Tool names are written as ★canonical names★.** Write `Bash` directly into a behaviour-layer rule and
  nothing fires on Codex at all. Rules are written like `run_shell`, and the adapter translates it.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

# ★canonical action names★ — rules are written with these, and the adapter translates them to each host's tool names.
# ⛔ Write a host's own tool name directly into a rule and it ★silently fires on nothing at all★ on a different host.
CANONICAL = ("run_shell", "edit_file", "write_file", "edit_notebook", "read_file")

# ★Codex records a command inside a JS call★, and a patch inside a quoted string (§Codex.tool_calls).
_CODEX_CMD = re.compile(r'cmd:\s*"((?:[^"\\]|\\.)*)"')
_CODEX_PATCH = re.compile(r'\*\*\* (?:Update|Add|Delete) File: ([^"\\\n]+)')


def _js_unescape(s: str) -> str:
    """Just the three escapes that actually appear — ⛔ not `unicode_escape`, which mangles UTF-8."""
    return s.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


# The event names both measured hosts use — the fallback when a host declares none for an event.
_SHARED_EVENTS = {"user_prompt_submit": "UserPromptSubmit", "session_start": "SessionStart",
                  "pre_tool_use": "PreToolUse", "post_tool_use": "PostToolUse"}


class Host:
    """What one LLM tool can give the brain. ⛔ What it can't give, it answers with ★an empty value★."""

    name = "generic"
    label = "no host (CLI + MCP only)"

    # ── ① where it lives ────────────────────────────────────────────────────
    def root(self) -> str:
        return os.path.expanduser("~/.brain")

    def detect(self) -> bool:
        return True                       # generic always holds (the last candidate)

    # ── ② what it reads ────────────────────────────────────────────────────
    def prompts(self, limit: int = 120, recent_files: Optional[int] = 40) -> List[str]:
        """★real user turns★, most recent first. ⛔ empty list if none — never invents any.
        `recent_files` bounds how many of the newest logs are read (None = all) — the calibration sample
        keeps the default; a report that wants every turn passes None."""
        return []

    def memory_dirs(self) -> List[str]:
        """Where this host puts memories (candidates suggested at install time)."""
        return []

    def rule_files(self) -> List[str]:
        """The file names this host reads as a project's standing rules (one per repository folder)."""
        return []

    def index_read_limit(self) -> Dict[str, object]:
        """How much of the auto-loaded index this host was ★measured★ to read uncut — `{warn_chars,
        ok_chars, measured}`, or {} when unmeasured or when it loads no index (then nothing is warned)."""
        return {}

    def command_prefixes(self) -> tuple:
        """First characters that make a prompt ★the host's own command★, not a question to recall for.
        `/` is the one both measured hosts share (Claude Code `/clear`, Codex `/fast off`)."""
        return ("/",)

    def autoloaded_index(self, memory_dir: str) -> str:
        """The index file this host puts in the model's context ★by itself, every session★, for this
        memory folder — or "" when it loads none. The agent-alone baseline and the search's "already in
        hand" rule both stand on this, so it is the host's fact, not a file name we assume."""
        return ""

    def stale_days(self) -> Dict[str, int]:
        """How many days a memory of each of ★this host's own types★ stays current. Empty = the host has
        no types of its own (every kind then takes the default, unless the person's config names it)."""
        return {}

    def directive_kinds(self) -> List[str]:
        """The memory kinds this host's own format reserves for ★standing guidance★ — rules nobody
        asks about, so recall cannot stand in for them (§indexaudit). Empty = the host has no such kind.
        A person's config `directive_kinds` wins over this."""
        return []

    def transcripts(self) -> List[str]:
        """Glob patterns for this host's ★session logs★ (recursive globs allowed). Empty = none."""
        return []

    def user_text(self, line: str) -> str:
        """One log line → ★the human's own words★ in it, or "" (not a user turn · a tool's output).
        ⛔ "" when this host's log shape is unmeasured — never a guess."""
        return ""

    def tool_calls(self, line: str) -> List[tuple]:
        """One log line → `[(tool name, kind, text)]`, kind being `"shell"` | `"path"` | `""`.

        ⛔ ★Every host writes its log in its own shape★, and a reader that knows only one shape does
        not fail — it returns ★zero★, which reads exactly like "this person never does that". Rule
        discovery then learns nothing from that host and never says why. Measured 2026-09-21: Codex
        held ★5,600 tool calls in ten days★ and the reader saw none of them, because the glob pointed
        at the other host's folder ★and★ the line shape was different. Two silent failures stacked.

        A `""` kind still counts as a call — totals must not shrink just because the parser cannot
        reach inside a particular tool.
        """
        return []

    def hook_calls(self, line: str) -> List[tuple]:
        """One log line → `[(tool name, text)]` ★as the behaviour hook would have received them★ — the
        name the hook is called with and the text its rules match by substring (§guard). Rule firing
        rates are measured from this, so what is measured and what fires stay the same thing."""
        return []

    # ── ③ how it hooks in ────────────────────────────────────────────────
    def hooks_file(self) -> str:
        return ""

    def hook_events(self) -> Dict[str, str]:
        """canonical event → this host's event name. Missing ones are left out."""
        return {}

    def mcp_setup(self, cmd: List[str]) -> Dict[str, object]:
        """How this host registers the brain's MCP server, ★as data★ — the installer runs or shows it.
          `{"check": argv, "add": argv}`  a command line the host provides (run if its program exists)
          `{"file": path, "snippet": [lines]}`  a settings file a ★human★ edits (never merged by us)
          `{}`  the host has no MCP — nothing to do
        `cmd` is the server's command line."""
        return {}

    def hook_output(self, event: str, text: str) -> str:
        """What a hook prints to attach `text` as context for a canonical `event` — ★one place★ (the
        recall hook, the behaviour hook and the session hook each used to write their own copy).
        Claude Code and Codex take the same shape (Codex measured 2026-09-21: its wire schema accepts
        `additionalContext`); a host that differs overrides this."""
        return json.dumps({"hookSpecificOutput": {
            "hookEventName": self.hook_events().get(event) or _SHARED_EVENTS.get(event, event),
            "additionalContext": text}, "suppressOutput": True}, ensure_ascii=False)

    # ── ④ what it calls ──────────────────────────────────────────────────
    def tool_names(self, canonical: str) -> List[str]:
        return []

    def info(self) -> dict:
        ev = self.hook_events()
        return {
            "name": self.name, "label": self.label, "detected": self.detect(),
            "root": self.root(), "hooks_file": self.hooks_file(),
            "events": sorted(ev.values()),
            "can_auto_recall": "user_prompt_submit" in ev,
            "can_guard": "pre_tool_use" in ev,
            "prompts": len(self.prompts(limit=5)),
        }


class ClaudeCode(Host):
    name = "claude-code"
    label = "Claude Code"

    def root(self) -> str:
        return os.path.expanduser(os.environ.get("CLAUDE_HOME") or "~/.claude")

    def detect(self) -> bool:
        return os.path.isdir(self.root())

    def memory_dirs(self) -> List[str]:
        base = os.path.join(self.root(), "projects")
        if not os.path.isdir(base):
            return []
        return [os.path.join(base, d, "memory") for d in sorted(os.listdir(base))
                if os.path.isdir(os.path.join(base, d, "memory"))]

    def rule_files(self) -> List[str]:
        return ["CLAUDE.md"]

    def index_read_limit(self) -> Dict[str, object]:
        # 22,882 characters passed uncut in a 2026-08-18 session (the file's last section was in the
        # context). The real limit is unknown, so the warning sits below that with room to spare.
        return {"warn_chars": 20000, "ok_chars": 22882, "measured": "2026-08-18"}

    def command_prefixes(self) -> tuple:
        # its input syntax: `/` commands · `!` shell mode · `#` add to memory · `@` mention
        return ("/", "!", "@", "#")

    def autoloaded_index(self, memory_dir: str) -> str:
        # Claude Code's own memory: `<root>/projects/<project>/memory/MEMORY.md` is loaded each session.
        # ⛔ Compared by ★where it really is★ against the host's own list — a memory folder is often a
        #    symlink (measured: this author's points from the Claude folder into a project checkout), so
        #    "is the path under `projects/`" alone said no for the folder Claude Code does load.
        if not memory_dir:
            return ""
        here = os.path.realpath(os.path.expanduser(memory_dir))
        if not any(os.path.realpath(d) == here for d in self.memory_dirs()):
            return ""
        return os.path.join(memory_dir, "MEMORY.md")

    def stale_days(self) -> Dict[str, int]:
        # Claude Code's four types. `project` (ongoing work, numbers, PRs) measured 2026-08-12: of 145, the
        # ones past 21 days were all two or three months stale on opening. `reference` (pointers) ages
        # slowly; `feedback` (guidance) and `user` (who the person is) barely age.
        return {"project": 21, "reference": 180, "feedback": 360, "user": 360}

    def directive_kinds(self) -> List[str]:
        # Claude Code's memory format names four types (user · feedback · project · reference), and
        # `feedback` is the one for guidance the user gave on how to work.
        return ["feedback"]

    def transcripts(self) -> List[str]:
        return [os.path.join(self.root(), "projects", "*", "*.jsonl")]

    def user_text(self, line: str) -> str:
        """`{"type":"user","message":{"content": str | [{"type":"text","text":…}, …]}}`."""
        if '"user"' not in line:
            return ""
        try:
            d = json.loads(line)
        except ValueError:
            return ""
        if not isinstance(d, dict) or d.get("type") != "user":
            return ""
        content = (d.get("message") or {}).get("content")
        text = content if isinstance(content, str) else " ".join(
            c.get("text", "") for c in content or []
            if isinstance(c, dict) and c.get("type") == "text")
        text = (text or "").strip()
        # ⛔ what a tool handed back is not the human's own words
        if text.startswith(("<", "[Request interrupted")):
            return ""
        return text

    def tool_calls(self, line: str) -> List[tuple]:
        """`message.content[]` holds `{"type":"tool_use","name":…,"input":…}`."""
        if '"tool_use"' not in line:
            return []
        try:
            o = json.loads(line)
        except ValueError:
            return []
        content = (o.get("message") or {}).get("content")
        if not isinstance(content, list):
            return []
        shell = set(self.tool_names("run_shell"))
        edit = {n for c in ("edit_file", "write_file", "edit_notebook")
                for n in self.tool_names(c)}
        out: List[tuple] = []
        for x in content:
            if not (isinstance(x, dict) and x.get("type") == "tool_use"):
                continue
            name, inp = x.get("name") or "", x.get("input") or {}
            if name in shell:
                out.append((name, "shell", inp.get("command") or ""))
            elif name in edit:
                out.append((name, "path", inp.get("file_path") or inp.get("notebook_path") or ""))
            else:
                out.append((name, "", ""))
        return out

    def hook_calls(self, line: str) -> List[tuple]:
        """The hook receives the same `name` and `input` the log records — matched as JSON text."""
        if '"tool_use"' not in line:
            return []
        try:
            o = json.loads(line)
        except ValueError:
            return []
        content = (o.get("message") or {}).get("content")
        if not isinstance(content, list):
            return []
        return [(x.get("name") or "", json.dumps(x.get("input") or {}, ensure_ascii=False))
                for x in content if isinstance(x, dict) and x.get("type") == "tool_use"]

    def hooks_file(self) -> str:
        return os.path.join(self.root(), "settings.json")

    def mcp_setup(self, cmd: List[str]) -> Dict[str, object]:
        return {"check": ["claude", "mcp", "get", "brain"],
                "add": ["claude", "mcp", "add", "brain", "--scope", "user", "--"] + list(cmd)}

    def hook_events(self) -> Dict[str, str]:
        return {"user_prompt_submit": "UserPromptSubmit",
                "session_start": "SessionStart",
                "pre_tool_use": "PreToolUse",
                "post_tool_use": "PostToolUse"}

    def tool_names(self, canonical: str) -> List[str]:
        return {"run_shell": ["Bash"], "edit_file": ["Edit", "MultiEdit"],
                "write_file": ["Write"], "edit_notebook": ["NotebookEdit"],
                "read_file": ["Read"]}.get(canonical, [])

    def prompts(self, limit: int = 120, recent_files: Optional[int] = 40) -> List[str]:
        """From the session log (JSONL), ★only what the human typed★. Reads the newest files first."""
        import glob
        base = os.path.join(self.root(), "projects")
        files = sorted(glob.glob(os.path.join(base, "*", "*.jsonl")),
                       key=lambda p: -os.path.getmtime(p))
        files = files[:recent_files] if recent_files else files
        out: List[str] = []
        for path in files:
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        text = self.user_text(line)
                        if text:
                            out.append(text)
            except OSError:
                continue
            if len(out) >= limit * 3:
                break
        return out[-limit:] if out else []


class Codex(Host):
    """Codex CLI. ★measured on this machine on 2026-09-02 and written down — not a guess.★"""

    name = "codex"
    label = "Codex CLI"

    def root(self) -> str:
        return os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex")

    def detect(self) -> bool:
        return os.path.isdir(self.root())

    def memory_dirs(self) -> List[str]:
        d = os.path.join(self.root(), "memories")
        return [d] if os.path.isdir(d) else []

    def rule_files(self) -> List[str]:
        return ["AGENTS.md"]

    def untrusted_hooks(self) -> List[str]:
        """Our hook entries that Codex will ★refuse to run★ until a human approves them.

        ## Why this has to be asked (measured 2026-09-28)

        Codex trusts a hook ★by hash, per entry★: `config.toml` carries
        `[hooks.state."<file>:<event>:<i>:<j>"] trusted_hash = "sha256:…"`. Writing a hook into
        `hooks.json` therefore installs ★nothing that runs★ — the entry sits there until the person
        approves it in an interactive session. A non-interactive `codex exec` simply blocks.

        ⛔ The cost is silent and it was paid in full: hooks were wired on 2026-09-21, and of the
        13 Codex sessions that followed, the behaviour layer fired in ★2★ — the two runs made with
        `--dangerously-bypass-hook-trust` while testing. Eleven real sessions got no recall and no
        rules, and every screen said the hooks were installed.

        ⚠️ Returns the event names, never a verdict about ★why★ — a missing entry can also mean the
        file was edited after approval. Either way a human has to look.
        """
        path = self.hooks_file()
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        try:
            with open(os.path.join(self.root(), "config.toml"), encoding="utf-8") as fh:
                conf = fh.read()
        except OSError:
            conf = ""
        ev_key = {v: k for k, v in self.hook_events().items()}
        out: List[str] = []
        for event, arr in (data.get("hooks") or {}).items():
            for i, entry in enumerate(arr or []):
                for j, item in enumerate(entry.get("hooks") or []):
                    if "brain" not in (item.get("command") or ""):
                        continue
                    key = "%s:%s:%d:%d" % (path, ev_key.get(event, event.lower()), i, j)
                    if ('"%s"' % key) not in conf:
                        out.append("%s[%d][%d]" % (event, i, j))
        return out

    def transcripts(self) -> List[str]:
        return [os.path.join(self.root(), "sessions", "**", "*.jsonl")]

    def tool_calls(self, line: str) -> List[tuple]:
        """★Measured 2026-09-21★ — one line is `{"payload": {"type": "custom_tool_call", …}}`.

        ⛔ Nothing here looks like Claude Code's `tool_use`, and the command is ★not a field★: the
        dominant tool is `exec`, whose `input` is a ★JavaScript snippet★ that calls the real thing —

            text( await tools.exec_command({cmd:"git status", max_output_tokens:16000}) )

        and edits arrive as a patch ★inside a string★ (`*** Update File: <path>`). So the command and
        the path are pulled back out with a pattern. Measured over ten days on this machine:
        2,411 commands and 612 patches that were previously invisible.

        ⚠️ A quoted `cmd:` is the only shape seen so far. An array form (`cmd:[…]`) would be missed —
        it would ★undercount, never miscount★, which is the failure direction this file prefers.
        """
        if '"payload"' not in line:
            return []
        try:
            o = json.loads(line)
        except ValueError:
            return []
        p = o.get("payload") or {}
        if p.get("type") not in ("custom_tool_call", "function_call", "local_shell_call"):
            return []
        name = p.get("name") or p.get("type") or ""
        raw = p.get("input") or p.get("arguments") or ""
        if isinstance(raw, dict):
            raw = json.dumps(raw, ensure_ascii=False)
        if not isinstance(raw, str):
            return []
        out: List[tuple] = []
        for m in _CODEX_CMD.finditer(raw):
            out.append((name, "shell", _js_unescape(m.group(1))))
        for m in _CODEX_PATCH.finditer(raw):
            out.append((name, "path", m.group(1).strip()))
        return out or [(name, "", "")]

    def hook_calls(self, line: str) -> List[tuple]:
        """What Codex's hook is handed, rebuilt from the log (names measured 2026-09-21, §tool_names):
        a shell command arrives as `Bash` with `{"command": …}`, a patch as `apply_patch` with the
        patch text in `command`.
        ⚠️ The patch text is read back ★out of a JavaScript string★, so escaped characters stay
           escaped — a rule matching inside patch content can be ★undercounted, never overcounted★."""
        out: List[tuple] = []
        for _name, kind, text in self.tool_calls(line):
            if kind == "shell":
                out.append(("Bash", json.dumps({"command": text}, ensure_ascii=False)))
        if '"payload"' in line and _CODEX_PATCH.search(line):
            try:
                p = json.loads(line).get("payload") or {}
            except ValueError:
                p = {}
            raw = p.get("input") or p.get("arguments") or ""
            if isinstance(raw, dict):
                raw = json.dumps(raw, ensure_ascii=False)
            if isinstance(raw, str) and _CODEX_PATCH.search(raw):
                out.append(("apply_patch", json.dumps({"command": raw}, ensure_ascii=False)))
        return out

    def hooks_file(self) -> str:
        # ⛔ ★the same shape★ as Claude Code, but a different file (not settings.json).
        return os.path.join(self.root(), "hooks.json")

    def mcp_setup(self, cmd: List[str]) -> Dict[str, object]:
        # ⛔ Codex keeps it in `config.toml` — ★never merged by hand★ (a mistake there breaks the user's
        #    other settings wholesale), so it is handed to a human as a snippet. A pasted snippet names
        #    ★this python and the module★ rather than `cmd`: it must not depend on what is on PATH.
        import sys
        return {"file": os.path.join(self.root(), "config.toml"),
                "snippet": ["[mcp_servers.brain]", "command = %s" % json.dumps(sys.executable),
                            'args = ["-m", "brain.server"]']}

    def hook_events(self) -> Dict[str, str]:
        # Measured 2026-09-02: hooks.json was carrying these eight —
        # PreToolUse · PermissionRequest · PostToolUse · PreCompact · PostCompact
        # · SessionStart · ★UserPromptSubmit★ · Stop
        return {"user_prompt_submit": "UserPromptSubmit",
                "session_start": "SessionStart",
                "pre_tool_use": "PreToolUse",
                "post_tool_use": "PostToolUse"}

    def tool_names(self, canonical: str) -> List[str]:
        """★Measured 2026-09-21★ — a hook was wired up and the real payloads were read (codex-cli 0.154.0).

        ⛔ The answer is ★mixed★, which is exactly why guessing failed here. Codex hands the hook a
        payload in ★Claude Code's shape★ (`tool_name` · `tool_input` · `prompt` · `session_id`), and
        for the shell it even uses ★Claude's name★:

            shell   → `Bash`          (not `shell`, which is what this adapter used to assume)
            edit    → `apply_patch`   (its own name · `tool_input.command` holds the patch text)

        So on a machine with both hosts installed, an unpinned host resolves to claude-code and
        ★the shell rules keep firing while every edit-side rule goes silent★ — half-working, quietly.
        That is what `--host` (§pin_from_argv) exists for.

        ⚠️ `read_file` and notebooks are ★still unmeasured★ — no such call showed up in the samples.
        Candidates stay listed: a wrong name simply never fires, a missing one silences the rule.
        """
        return {"run_shell": ["Bash", "shell", "local_shell"],
                "edit_file": ["apply_patch", "Edit"],
                "write_file": ["apply_patch", "Write"],
                "edit_notebook": [], "read_file": ["Read"]}.get(canonical, [])

    def prompts(self, limit: int = 120, recent_files: Optional[int] = 40) -> List[str]:
        """`history.jsonl` — one line is `{session_id, ts, text}`. Simpler than Claude Code (one file,
        so `recent_files` has nothing to bound)."""
        path = os.path.join(self.root(), "history.jsonl")
        out: List[str] = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    text = (d.get("text") or "").strip()
                    if text:
                        out.append(text)
        except OSError:
            return []
        return out[-limit:]


_ALL = (ClaudeCode(), Codex(), Host())
_active: Optional[Host] = None


def all_hosts() -> tuple:
    return _ALL


def rule_files() -> List[str]:
    """Every known host's rule-file names — a team repository holds the rules of agents this person
    may not run, and they are worth indexing all the same."""
    return list(dict.fromkeys(f for h in _ALL for f in h.rule_files()))


def detected() -> List[Host]:
    """Hosts actually present on this machine (excluding generic)."""
    return [h for h in _ALL if h.name != "generic" and h.detect()]


def pin(name: str) -> bool:
    """Fix the host explicitly. False if that name is unknown (the caller then leaves it alone).

    ⛔ ★The host is a property of the process that called us, not of the machine.★ `detect()` can
    only answer "is it installed here", and on a machine with both installed that is not the same
    question. A hook knows the answer for certain — it was launched by that host — so it says so,
    and `BRAIN_HOST` is exported for anything spawned further down.
    """
    global _active
    for h in _ALL:
        if h.name == name:
            _active = h
            os.environ["BRAIN_HOST"] = name
            return True
    return False


def pin_from_argv(argv: Optional[List[str]] = None) -> str:
    """Take `--host <name>` / `--host=<name>` out of argv and pin it. Returns the name, or "".

    ⛔ ★A flag, not an `env VAR=x` prefix★ — a hook command is also written on Windows, where
    there is no `env` and no shell in front of the entry point. The flag survives both.
    ⚠️ Unknown names are ★dropped silently★: a hook must never block the host it is attached to,
    and the only cost of not pinning is falling back to `active()`'s first-found.
    """
    import sys as _sys
    from_sys = argv is None
    args = _sys.argv[1:] if from_sys else list(argv or [])
    want = ""
    rest: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--host" and i + 1 < len(args):
            want, i = args[i + 1], i + 2
            continue
        if a.startswith("--host="):
            want, i = a.split("=", 1)[1], i + 1
            continue
        rest.append(a)
        i += 1
    want = want.strip().lower()
    if want and pin(want):
        # ⛔ the flag is ★taken out★ — whatever parses argv next (argparse) would reject it
        if from_sys:
            _sys.argv = _sys.argv[:1] + rest
        return want
    return ""


def active(refresh: bool = False) -> Host:
    """The host to use right now. `BRAIN_HOST` can pin it down, otherwise ★the first one found★.

    ⛔ A machine with both really exists (this machine, on 2026-09-02). What gets picked then is
    ★for a human to decide★ — the settings UI, `BRAIN_HOST` and `--host` are that spot. Pick
    silently and ★what worked yesterday reads a different memory today.★
    """
    global _active
    if _active is not None and not refresh:
        return _active
    want = (os.environ.get("BRAIN_HOST") or "").strip().lower()
    if want:
        for h in _ALL:
            if h.name == want:
                _active = h
                return _active
    found = detected()
    _active = found[0] if found else _ALL[-1]
    return _active


def tool_names(canonical: str, host: Optional[Host] = None) -> List[str]:
    """canonical action name → this host's tool names."""
    return (host or active()).tool_names(canonical)
