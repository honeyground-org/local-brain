#!/usr/bin/env python3
"""★Each host reads its own log, and every path comes from one rule★ — no host's shape is assumed. (local · budget 0)

## What this replaced (2026-10-06, the first half of stage 4)

Several readers parsed ★Claude Code's★ log shape themselves (`tool_use`, `Edit`, `Bash`) or globbed its
folder (`~/.claude/projects/*/*.jsonl`), and several paths were nailed to `~/.claude/brain` or to
`<package parent>/…`. On anyone else's machine:
  · a Codex user's edits and commits were ★never read★ by the session-tail tracker, the trigger report or
    the two rule-firing measurements — and nothing said so
  · an installed copy (no repository) looked for its config and its eval sample next to site-packages
  · the help screen named a data folder the brain was not using

## What this holds down — on fake host folders and a fake home, never the author's

  ① the session tail reads ★the calling host's★ log — a Codex patch and a Codex commit are counted;
     ⛔ control: read as Claude Code, the same lines count nothing (the old behaviour)
  ② `user_text` — a Claude Code user turn is the person's words; a tool's interruption note is not;
     a host whose log shape is unmeasured answers "" (never a guess)
  ③ `hook_calls` — what the behaviour hook would have received, per host
  ④ `transcript_pairs` — every detected host's logs, each read as its own host
  ⑤ the trigger report reads every host's prompts (Codex `history.jsonl` included)
  ⑥ `hook_output` — one shape, the host's event name
  ⑦ paths — the tail's pending folder is in the brain home, its config is the one config rule, the index
     file is recognised by its configured name, the eval sample is in the eval folder, and the help
     screen names the real home
  ⑧ the memory folder — found through every host (not Claude Code's folder alone); the installer takes
     ★the fullest★ one; with none anywhere it makes one in the brain home and ★plants the seed that
     ships★ (which no code used to plant)
  ⑨ what only the host knows — how it registers the MCP server, which rule files it reads, which index
     it loads by itself (a symlinked memory folder included), which first characters are its own
     commands; the search's "already in hand" rule follows that, and a host that loads nothing hides nothing

How to run:  PYTHONPATH=. python3 tests/verify_host_neutral.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-hostneutral-")
_KEYS = ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_HOST", "BRAIN_EVAL_DIR", "CLAUDE_HOME", "CODEX_HOME")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ["BRAIN_EVAL_DIR"] = os.path.join(_TMP, "eval")
os.environ["CLAUDE_HOME"] = os.path.join(_TMP, "claude")
os.environ["CODEX_HOME"] = os.path.join(_TMP, "codex")
os.environ.pop("BRAIN_HOST", None)

from brain import hosts, ruledisc, scorecard, store, tail, triggers  # noqa: E402

FAILS: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", name, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


# ⛔ The project path is ★outside★ the temp folder on purpose — anything under it is throwaway (§tail).
#    Only the path string is classified, so the folder need not exist.
PROJ = "/srv/example/app"
MEM = os.path.join(_TMP, "notes")

# ── one line of each host's log, in the shapes measured on real machines ─────────────────────────────
CLAUDE_LINES = [
    {"type": "user", "message": {"content": "please fix the login bug"}},
    {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": PROJ + "/login.py"}}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "git commit -m fix"}}]}},
]
_PATCH = "*** Begin Patch\\n*** Update File: %s/login.py\\n@@\\n-a\\n+b\\n*** End Patch" % PROJ
CODEX_LINES = [
    {"timestamp": "2026-10-06T00:00:00Z", "payload": {"type": "custom_tool_call", "name": "exec",
     "input": 'text( await tools.exec_command({cmd:"git commit -m fix", max_output_tokens:16000}) )'}},
    {"timestamp": "2026-10-06T00:00:01Z", "payload": {"type": "custom_tool_call", "name": "exec",
     "input": 'text( await tools.apply_patch({patch:"%s"}) )' % _PATCH}},
]


def _write_jsonl(path: str, rows) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _scan(path: str, host: str) -> dict:
    hosts.pin(host)
    rec = {"session_id": "s", "offset": 0, "tail": tail._blank_tail(), "saves": 0}
    return tail.scan(path, rec)["tail"]


def host_facts() -> None:
    print("\n★⑨ what only the host knows★")
    from brain import search
    cc, cx, gen = hosts.ClaudeCode(), hosts.Codex(), hosts.Host()
    cmd = ["/opt/brain/bin/brain-mcp"]
    a = cc.mcp_setup(cmd)
    check("Claude Code registers through its own command line",
          a.get("check") == ["claude", "mcp", "get", "brain"] and a.get("add", [])[-1:] == cmd, str(a))
    b = cx.mcp_setup(cmd)
    check("⛔ Codex is handed a snippet for a human (its config is never merged by us)",
          b.get("file", "").endswith("config.toml") and b.get("snippet", [""])[0] == "[mcp_servers.brain]"
          and "add" not in b, str(b))
    check("a host without MCP → nothing to do", gen.mcp_setup(cmd) == {})
    check("rule files are every host's, once each", hosts.rule_files() == ["CLAUDE.md", "AGENTS.md"],
          str(hosts.rule_files()))
    check("Claude Code's commands are its own syntax", cc.command_prefixes() == ("/", "!", "@", "#"))
    check("⛔ Codex: only `/` (measured: its `@` prompts are requests about files)",
          cx.command_prefixes() == ("/",))
    os.environ["CLAUDE_HOME"] = os.path.join(_TMP, "claude2")
    real = os.path.join(_TMP, "elsewhere", "memory")
    os.makedirs(real)
    link = os.path.join(_TMP, "claude2", "projects", "p", "memory")
    os.makedirs(os.path.dirname(link))
    os.symlink(real, link)
    check("Claude Code loads its own MEMORY.md — through a symlinked folder too",
          cc.autoloaded_index(link) == os.path.join(link, "MEMORY.md"), cc.autoloaded_index(link))
    check("⛔ a folder that is not Claude Code's is not auto-loaded", cc.autoloaded_index(real + "x") == "")
    check("Codex loads no memory index by itself", cx.autoloaded_index(link) == "")
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": link, "include": ["*.md"], "max_depth": 1}]}, fh)
    hosts.pin("claude-code")
    check("search treats the auto-loaded index as already in hand", search._always_in_context() == {"MEMORY"},
          str(search._always_in_context()))
    hosts.pin("codex")
    check("⛔ on a host that loads nothing, nothing is hidden", search._always_in_context() == set(),
          str(search._always_in_context()))
    check("…and no size warning is invented for it",
          __import__("brain.indexaudit", fromlist=["x"]).size_notice() == "")


def memory_folder() -> None:
    print("\n★⑧ the memory folder★")
    from brain import discover, install
    small = os.path.join(_TMP, "claude", "projects", "small", "memory")
    big = os.path.join(_TMP, "codex", "memories")
    for d, n in ((small, 1), (big, 3)):
        os.makedirs(d, exist_ok=True)
        for i in range(n):
            open(os.path.join(d, "n%d.md" % i), "w", encoding="utf-8").close()
    hosts._active = None
    found = {c.path for c in discover.find_host_memory()}
    check("every detected host's memory folders are found (Codex's too)",
          small in found and big in found, str(sorted(found)))
    install.DRY = False
    check("the installer takes the fullest folder, not the first one", install._memory_folder() == big)
    os.environ["CLAUDE_HOME"] = os.path.join(_TMP, "nohost-claude")
    os.environ["CODEX_HOME"] = os.path.join(_TMP, "nohost-codex")
    hosts._active = None
    got = install._memory_folder()
    want = os.path.join(os.environ["BRAIN_HOME"], "memory")
    check("no host folder anywhere → one in the brain home", got == want, got)
    seed = os.path.join(ROOT, "brain", "templates", "MEMORY.seed.md")
    planted = os.path.join(want, "NOTES.md")
    check("…with the seed that ships planted as the configured index file",
          os.path.isfile(planted) and open(planted, encoding="utf-8").read() == open(seed, encoding="utf-8").read(), planted)
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        check("the seed ships in the package (package-data names it)", "templates/*.md" in fh.read())


def main() -> int:
    os.makedirs(MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"index_file": "NOTES.md",
                   "sources": [{"name": "memory", "path": MEM, "include": ["*.md"], "max_depth": 1}]}, fh)
    claude_log = _write_jsonl(os.path.join(_TMP, "claude", "projects", "p", "s1.jsonl"), CLAUDE_LINES)
    codex_log = _write_jsonl(os.path.join(_TMP, "codex", "sessions", "2026", "10", "06", "r.jsonl"),
                             CODEX_LINES)
    _write_jsonl(os.path.join(_TMP, "codex", "history.jsonl"),
                 [{"session_id": "r", "ts": 1, "text": "deploy the staging build"}])

    print("=" * 72 + "\n★① the session tail reads the calling host's log★\n" + "=" * 72)
    t = _scan(codex_log, "codex")
    check("a Codex patch is a code write", t["code_writes"] == 1, str(t["files"]))
    check("a Codex commit is counted", t["commits"] == 1, str(t["commits"]))
    t0 = _scan(codex_log, "claude-code")
    check("⛔ control: read as Claude Code, the same Codex lines count nothing (the old behaviour)",
          t0["code_writes"] == 0 and t0["commits"] == 0, str(t0))
    t = _scan(claude_log, "claude-code")
    check("a Claude Code edit and commit are counted", t["code_writes"] == 1 and t["commits"] == 1, str(t))
    check("the person's words are kept, the interruption note is not",
          t["user_says"] == ["please fix the login bug"], str(t["user_says"]))

    print("\n★② user_text★")
    cc, cx, gen = hosts.ClaudeCode(), hosts.Codex(), hosts.Host()
    line = json.dumps(CLAUDE_LINES[0])
    check("Claude Code: a user turn is the person's words", cc.user_text(line) == "please fix the login bug")
    check("Claude Code: a tool's note is not", cc.user_text(json.dumps(CLAUDE_LINES[1])) == "")
    check("Claude Code: an assistant line is not", cc.user_text(json.dumps(CLAUDE_LINES[2])) == "")
    check("⛔ Codex: unmeasured log shape → \"\" (never a guess)",
          cx.user_text(json.dumps(CODEX_LINES[0])) == "")

    print("\n★③ hook_calls — what the behaviour hook would have received★")
    got = cc.hook_calls(json.dumps(CLAUDE_LINES[3]))
    check("Claude Code: the tool name and its input as JSON",
          got == [("Bash", json.dumps({"command": "git commit -m fix"}))], str(got))
    got = cx.hook_calls(json.dumps(CODEX_LINES[0]))
    check("Codex: a shell command arrives as `Bash`", got and got[0][0] == "Bash"
          and "git commit -m fix" in got[0][1], str(got))
    got = cx.hook_calls(json.dumps(CODEX_LINES[1]))
    check("Codex: a patch arrives as `apply_patch` with the patch text",
          any(n == "apply_patch" and "login.py" in b for n, b in got), str(got)[:120])
    check("a host with no log shape → nothing", gen.hook_calls(line) == [])

    print("\n★④ transcript_pairs — every detected host, each as itself★")
    hosts._active = None
    pairs = [(h.name, os.path.basename(f)) for h, f in ruledisc.transcript_pairs()]
    check("both hosts' logs, each read as its own host",
          ("claude-code", "s1.jsonl") in pairs and ("codex", "r.jsonl") in pairs, str(pairs))

    print("\n★⑤ the trigger report reads every host's prompts★")
    prompts = triggers._real_prompts()
    check("Claude Code's and Codex's prompts both arrive",
          any("login bug" in p for p in prompts) and any("staging build" in p for p in prompts), str(prompts))

    print("\n★⑥ hook_output — one shape, the host's event name★")
    for h in (cc, cx, gen):
        o = json.loads(h.hook_output("pre_tool_use", "hello"))
        check("%s: additionalContext carries the text, event `PreToolUse`" % h.name,
              o["hookSpecificOutput"] == {"hookEventName": "PreToolUse", "additionalContext": "hello"}
              and o["suppressOutput"] is True, str(o))

    print("\n★⑦ paths come from one rule★")
    hosts.pin("claude-code")
    check("the tail's pending folder is in the brain home",
          tail._pending_dir() == os.path.join(os.environ["BRAIN_HOME"], "pending"), tail._pending_dir())
    check("the tail reads the config the one rule finds",
          tail._cfg().get("index_file") == "NOTES.md")
    check("the index file is recognised by its configured name, anywhere",
          tail._classify("/srv/example/other-project/NOTES.md") == "save")
    check("⛔ another name is not an index file (`MEMORY.md` here)",
          tail._classify(os.path.join(PROJ, "MEMORY.md")) == "code")
    check("the system temp folder is throwaway",
          tail._classify(os.path.join(tempfile.gettempdir(), "probe.py")) == "skip")
    check("…written with backslashes, as a Windows transcript writes it, too",
          tail._classify(os.path.join(tempfile.gettempdir(), "probe.py").replace("/", "\\")) == "skip")
    check("the eval sample is in the eval folder",
          scorecard._short_json() == os.path.join(os.environ["BRAIN_EVAL_DIR"], "short.json"),
          scorecard._short_json())
    import io
    from contextlib import redirect_stdout
    from brain import cli
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            cli.main(["help"])
        except SystemExit:
            pass
    check("the help screen names the real home", os.environ["BRAIN_HOME"] in buf.getvalue()
          and "~/.brain/" not in buf.getvalue())

    memory_folder()
    host_facts()

    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAILS), " · ".join(FAILS)) if FAILS
          else "✅ all passed — each host reads its own log, and every path comes from one rule")
    return 1 if FAILS else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for k, v in _SAVED.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
