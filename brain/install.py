"""The installer — ★one set for Mac·Windows·Linux★. `python3 -m brain.install`

## Why Python (user requirement 2026-09-02: download and install on a different PC)

Right now `install.sh` is a POSIX shell, and ★Windows has no `sh`, so not a single line of it runs.★
But making a separate `install.ps1` reproduces exactly the trap this repo has been burned by repeatedly —
★"something written in two places gets fixed in only one."★ In fact `install.sh` was already half Python
heredoc (5 blocks). So gathering it into Python is the shape it already wanted to be.

## ⛔ Four things this installer protects

1. **Never erase someone else's settings.** Hook config (`settings.json`·`hooks.json`) is ★backed up, then
   merged★. Breaking someone else's environment is the installer's worst possible failure.
2. **Safe to run more than once.** What's already wired doesn't get put in again.
3. **Never claims something exists when it doesn't.** With no host·scheduler·key, that step ★is skipped,
   and says so★. A silently half-installed state is the worst outcome.
4. **Asks the adapter about the platform.** Hook file·event names come from `brain/hosts.py`,
   the scheduler knows from `brain/scheduler.py`. Never hardcode `~/.claude` here.

"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Optional

from brain import i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WARN: List[str] = []

# ⛔ ★the installer does things that reach outward★ — it edits someone else's config files, calls
#    `claude mcp add`, and registers scheduled jobs. ★With no switch able to stop that★, even verification
#    can touch a person's real machine (2026-09-02: in a clean room, `claude mcp add` actually went out —
#    harmless only because HOME was faked, but ★relying on that is a design flaw★).
#    It's needed for the user too: it can ★show first★ "what this will do to my machine."
DRY = False


def _would(msg: str) -> None:
    print("  --  would %s" % msg)


class _Skip(Exception):
    """The signal that exits dry-run ★right before writing★ (this is the normal flow)."""


def say(msg: str = "") -> None:
    print(msg)


def ok(msg: str) -> None:
    print("  OK  %s" % msg)


def warn(msg: str) -> None:
    WARN.append(msg)
    print("  !!  %s" % msg)


def bad(msg: str) -> None:
    print("  XX  %s" % msg)


# ── building the entry-point command ──────────────────────────────────────────────────────
def _quote(part: str) -> str:
    """Wraps in quotes if there's a space. ⛔ Spaces are common in Windows paths (`Program Files`)."""
    return '"%s"' % part if (" " in part and not part.startswith('"')) else part


def entry_command(script: str, module: str) -> str:
    """One line for a command a hook·scheduled job will call.

    ⛔ ★Pointing at a shell script doesn't run on Windows★ — the order is:
        1. an installed entry point (`brain-hook` / `brain-hook.exe`) on PATH, if there is one
        2. the repo's `bin/<script>`, if it exists and is ★POSIX★ (the shell prefilter is fast)
        3. `<this python> -m <module>` — ★the last resort that runs anywhere★
    """
    found = shutil.which(script)
    if found:
        return _quote(found)
    local = os.path.join(ROOT, "bin", script)
    if os.name != "nt" and os.path.isfile(local):
        return _quote(local)
    return "%s -m %s" % (_quote(sys.executable), module)


# ── 1. prerequisites ────────────────────────────────────────────────────────────────
def step_prereq() -> bool:
    say("\n" + i18n.t("install.step1_title"))
    if sys.version_info < (3, 8):
        bad(i18n.t("install.python_too_old", major=sys.version_info[0], minor=sys.version_info[1]))
        return False
    ok(i18n.t("install.python_ok", major=sys.version_info[0], minor=sys.version_info[1]))
    try:
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.execute("PRAGMA journal_mode=WAL")
        c.close()
        ok(i18n.t("install.sqlite_ok"))
    except Exception as exc:                             # noqa: BLE001
        bad(i18n.t("install.sqlite_bad", err=exc))
        return False

    from brain import hosts, scheduler
    found = hosts.detected()
    if found:
        for h in found:
            i = h.info()
            ok(i18n.t("install.host_ok", label=h.label, root=i["root"],
                      auto_recall=i18n.t("install.yes") if i["can_auto_recall"] else i18n.t("install.no"),
                      guard=i18n.t("install.yes") if i["can_guard"] else i18n.t("install.no")))
    else:
        warn(i18n.t("install.no_host"))
    s = scheduler.active()
    if s.available():
        ok(i18n.t("install.scheduler_ok", label=s.label, evidence=s.evidence))
    else:
        warn(i18n.t("install.no_scheduler"))
    return True


# ── 2. what to remember ─────────────────────────────────────────────────────
def step_config() -> bool:
    from brain import store
    say("\n" + i18n.t("install.step2_title"))
    path = store.default_config_path()
    if os.path.isfile(path):
        ok(i18n.t("install.config_exists", path=path))
        return True
    from brain import discover
    mem = _memory_folder()
    sources = [{"name": "memory", "path": _tilde(mem), "include": ["*.md"],
                "max_depth": 1, "prior": 1.45, "note": "personal memories — the source of truth"}]
    try:
        for c in discover.detect(min_files=3)[:6]:
            if os.path.realpath(c.path) == os.path.realpath(mem):
                continue                                 # the memory folder chosen above
            sources.append({"name": c.name, "path": c.tilde, "include": ["*.md"],
                            "prior": 1.0, "note": "auto-detected"})
            ok(i18n.t("install.detected_source", name="%-10s" % c.name,
                      files="%5d" % c.files, path=c.tilde))
    except Exception as exc:                             # noqa: BLE001
        warn(i18n.t("install.autodetect_failed", err=str(exc)[:80]))
    if not sources:
        warn(i18n.t("install.nothing_detected"))
    if DRY:
        _would(i18n.t("install.would_write_config", path=path, n=len(sources)))
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"_comment": ["What the brain remembers. Add more with `brain add`."],
                   "language": "", "sources": sources}, fh,
                  ensure_ascii=False, indent=1)
    ok(i18n.t("install.wrote_config", path=path))
    return True


def _memory_folder() -> str:
    """★The memory folder★ — the fullest one any detected host keeps, else one in the brain home with
    the starter index planted (§brain/templates/MEMORY.seed.md).

    ⛔ It used to take the first host's ★alphabetically first★ folder — on a Claude Code machine with
       several projects that is whichever project sorts first, not the one with the notes. And with no
       host folder at all there was no memory source, so `remember` had nowhere to write.
    """
    from brain import discover, hosts, store
    best, best_n = "", -1
    for h in hosts.detected():
        for d in h.memory_dirs():
            n = int(discover._count(d, ["*.md"], 1)["files"])
            if n > best_n:
                best, best_n = d, n
    if best:
        ok(i18n.t("install.memories_found", path=best))
        return best
    path = os.path.join(store.brain_home(), "memory")
    if DRY:
        _would(i18n.t("install.would_create_memory", path=path))
        return path
    os.makedirs(path, exist_ok=True)
    index = os.path.join(path, store.index_file_name())
    seed = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "MEMORY.seed.md")
    if not os.path.exists(index) and os.path.isfile(seed):
        shutil.copyfile(seed, index)
    ok(i18n.t("install.memory_created", path=path))
    return path


def _tilde(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p.startswith(home) else p


# ── 3. index ────────────────────────────────────────────────────────────────
def step_index() -> bool:
    from brain import health, store
    say("\n" + i18n.t("install.step3_title"))
    if DRY:
        _would(i18n.t("install.would_index"))
        return True
    try:
        db = store.connect()
        stats = store.reindex(db, full=True)
    except Exception as exc:                             # noqa: BLE001
        bad(i18n.t("install.index_failed", err=str(exc)[:160]))
        return False
    st = health.status(db)
    c = st["corpus"]
    ok(i18n.t("install.index_ok", docs=c["docs"], terms=format(c["terms"], ","),
              sources=" · ".join("%s %d" % kv for kv in sorted(st["by_source"].items()))))
    if not st.get("calibration_guard", {}).get("protected", True):
        warn(st["calibration_guard"]["why"])
    return True


# ── 3b. AI engines ──────────────────────────────────────────────────────────
def step_engines(embed: str, judge: str) -> None:
    """Record the engines a person named, then show every role and what it would send.

    ⛔ ★A key found here chooses nothing★ (§engines) — `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` may
       belong to another tool, and choosing on its presence is the one path where notes leave a machine
       whose owner never decided they should. So this step only writes what was named on the command
       line, and otherwise shows the table: which keys exist, and that none is in use until chosen.
    """
    from brain import engines, store
    say("\n" + i18n.t("install.engines_title"))
    for role, prov in (("embed", embed), ("judge", judge)):
        if not prov:
            continue
        if DRY:
            _would(i18n.t("install.would_set_engine", role=role, provider=prov))
            continue
        try:
            engines.set_choice(role, prov)
            ok(i18n.t("install.engine_set", role=role, provider=prov))
        except Exception as exc:                         # noqa: BLE001
            warn(str(exc)[:160])
    if DRY:
        return
    from brain import cli
    for line in cli.engines_text(store.connect()).splitlines():
        say(("  " + line) if line else "")
    if not (embed and judge):
        say("  " + i18n.t("install.engines_hint"))


# ── 3c. dedicated databases (optional, one role at a time) ─────────────────
def store_plan(mode: str, vector: str = "", graph: str = "") -> Dict[str, tuple]:
    """What the store flags ask for: {role: (backend, url)} — url "" means run it in Docker.

    `--stores docker` = every role's default in Docker (§stores.DOCKER_DEFAULT); `--vector-store NAME` and
    `--graph-store NAME` choose a role on its own (`NAME=URL` for a server that already runs). A role
    named on its own wins over `--stores`. Raises ValueError(role, value) for a backend that is not there.
    """
    from brain import stores
    plan = {r: (n, "") for r, n in stores.DOCKER_DEFAULT.items()} if mode == "docker" else {}
    for role, value in (("vector", vector), ("graph", graph)):
        if not value:
            continue
        name, _, url = value.strip().partition("=")
        name = name.strip().lower()
        if name not in stores.names(role):
            raise ValueError(role, value)
        if name == "sqlite":
            plan.pop(role, None)
        else:
            plan[role] = (name, url.strip())
    return plan


def step_stores(plan: Dict[str, tuple]) -> None:
    """Each chosen role: started in Docker with its data in a folder of the brain's home, or pointed at the
    address given — then filled from the local copy (§dockerstores · §stores).

    ⛔ Optional and never automatic: the local SQLite copy answers everything without it. A machine without
       Docker gets a warning, not a failed install — and a role not named is not touched.
    """
    if not plan:
        return
    from brain import dockerstores, store, stores
    say("\n" + i18n.t("install.stores_title"))
    in_docker = {r: n for r, (n, url) in plan.items() if not url}
    for role, name in list(in_docker.items()):
        if stores.spec(name).docker is None:
            warn(i18n.t("install.stores_no_docker", backend=name, role=role))
            del in_docker[role]
    if DRY:
        if in_docker:
            dockerstores.up(in_docker, dry_run=True, say=say)
        for role, (name, url) in plan.items():
            if url:
                _would(i18n.t("install.stores_set", role=role, backend=name, url=url))
        return
    chosen = []
    if in_docker:
        res = dockerstores.up(in_docker)
        if not res["ok"]:
            warn(i18n.t("install.stores_failed", why=res["why"]))
        else:
            chosen += list(in_docker)
            for role, version in res["roles"].items():
                ok(i18n.t("install.stores_up", role=role, version="%s %s" % (in_docker[role], version),
                          data=", ".join(res["data"][role])))
    for role, (name, url) in plan.items():
        if url:
            stores.set_choice(role, name, url)
            ok(i18n.t("install.stores_set", role=role, backend=name, url=url))
            chosen.append(role)
    if not chosen:
        return
    got = stores.sync(store.connect(), tuple(r for r in stores.ROLES if r in chosen))
    for role, r in got.items():
        if r.get("error"):
            warn(i18n.t("install.stores_failed", why=r["error"]))
        else:
            ok(i18n.t("install.stores_synced", role=role, target=r.get("target", "")))


# ── 4. MCP ─────────────────────────────────────────────────────────────────
def step_mcp() -> None:
    from brain import hosts
    say("\n" + i18n.t("install.step4_title"))
    cmd = entry_command("brain-mcp", "brain.server")
    # ⛔ ★How★ each host registers it is the adapter's to say (§hosts.Host.mcp_setup) — this used to be
    #    `if h.name == "claude-code" … elif "codex"`, so a third host meant editing the installer.
    for h in hosts.detected():
        spec = h.mcp_setup(cmd.replace('"', "").split())
        if spec.get("add"):
            if not shutil.which(spec["add"][0]):
                say("  " + i18n.t("install.mcp_run_yourself", cmd=cmd))
                continue
            if DRY:
                _would(i18n.t("install.would_mcp_add", cmd=cmd))
                continue
            try:
                p = subprocess.run(spec["check"], capture_output=True, timeout=10)
                if p.returncode == 0:
                    ok(i18n.t("install.mcp_already"))
                    continue
                p = subprocess.run(spec["add"], capture_output=True, timeout=20)
                ok(i18n.t("install.mcp_registered")) if p.returncode == 0 else warn(
                    i18n.t("install.mcp_auto_failed", cmd=cmd))
            except (OSError, subprocess.SubprocessError) as exc:   # noqa: BLE001
                warn(i18n.t("install.mcp_exc", err=str(exc)[:80]))
        elif spec.get("file"):
            # ⛔ The path is built outside the `t(` call — the key scanner (`i18n._keys_in`) treats every
            #    key-shaped string inside the call as a key, and reported `config.toml` as a missing one.
            _file = spec["file"]
            say("  " + i18n.t("install.codex_add", path=_file))
            for line in spec["snippet"]:
                say("      " + line)


# ── 5. hooks ──────────────────────────────────────────────────────────────────
def step_hooks(with_hook: bool, with_guard: bool) -> None:
    from brain import hosts
    say("\n" + i18n.t("install.step5_title"))
    if not (with_hook or with_guard):
        say("  " + i18n.t("install.hooks_skipped"))
        return
    for h in hosts.detected():
        _wire_host(h, with_hook, with_guard)


def _wire_host(host, with_hook: bool, with_guard: bool) -> None:
    path = host.hooks_file()
    if not path:
        warn(i18n.t("install.no_hooks_file", host=host.name))
        return
    data: Dict = {}
    if os.path.isfile(path):
        backup = "%s.bak.%d" % (path, int(time.time()))
        try:
            if DRY:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                _would(i18n.t("install.would_backup", file=os.path.basename(path)))
                raise _Skip
            shutil.copy2(path, backup)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            ok(i18n.t("install.backed_up", host=host.name, file=os.path.basename(backup)))
        except _Skip:
            pass
        except ValueError:
            # ⛔ ★never touches a broken config★ — trying to fix it only breaks someone else's config worse
            bad(i18n.t("install.invalid_json", host=host.name, path=path))
            return
        except OSError as exc:                           # noqa: BLE001
            bad(i18n.t("install.cannot_read", host=host.name, path=path, err=exc))
            return
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)

    hooks = data.setdefault("hooks", {})
    ev = host.hook_events()
    added = 0

    def wire(event_key: str, cmd: str, timeout: int, status: str,
             matcher: str = "", extra: Optional[Dict] = None) -> None:
        nonlocal added
        event = ev.get(event_key)
        if not event:
            warn(i18n.t("install.no_event", host=host.name, event=event_key))
            return
        arr = hooks.setdefault(event, [])
        # ★never overwrites an existing hook★ — erasing someone else's config is the worst possible failure
        # ⛔ …but ★our own line from an older install★ has to be recognised as ours, or appending
        #    `--host` would quietly wire the same hook ★twice★ on every machine that already had it.
        #    So the comparison is on the command ★without the flag★, and a match is repinned in place.
        base = cmd.split(" --host ")[0]
        want = set(filter(None, matcher.split("|")))

        def ours(e: Dict, x: Dict) -> bool:
            """Is this line ★one we wrote before★.

            ⛔ The command alone is the safety property — we only ever touch a line whose command is
            our own binary. The matcher is compared as ★a set★, not a string: the tool list behind a
            matcher grows over time (`Edit|Write|NotebookEdit` → `Edit|MultiEdit|Write|NotebookEdit`),
            and string equality would read our own older line as a stranger's and wire ★a second copy
            of the guard on every edit★ (that is exactly what this machine was about to get).
            The two groups we write are disjoint by construction (shell names vs edit names), so an
            overlap can only mean the same wiring.
            """
            if x.get("command", "").split(" --host ")[0] != base:
                return False
            mine = set(filter(None, e.get("matcher", "").split("|")))
            return e.get("matcher", "") == matcher or bool(mine & want)

        for e in arr:
            for x in e.get("hooks", []):
                if not ours(e, x):
                    continue
                if x.get("command") == cmd and e.get("matcher", "") == matcher:
                    ok(i18n.t("install.hook_already", host=host.name, event=event,
                              matcher=matcher or i18n.t("install.matcher_all")))
                    return
                x["command"] = cmd                       # an older line of ours
                # ⚠️ the matcher belongs to the ★entry★, so widening it would move anything else
                #    parked under it. Only widen when ours is the only hook there.
                if len(e.get("hooks", [])) == 1:
                    e["matcher"] = matcher
                added += 1
                ok(i18n.t("install.hook_repinned", host=host.name, event=event,
                          matcher=matcher or i18n.t("install.matcher_all")))
                return
        entry = next((e for e in arr if e.get("matcher", "") == matcher), None)
        if entry is None:
            entry = {"matcher": matcher, "hooks": []}
            arr.append(entry)
        item = {"type": "command", "command": cmd, "timeout": timeout,
                "statusMessage": status}
        if extra:
            item.update(extra)
        entry.setdefault("hooks", []).append(item)
        added += 1
        ok(i18n.t("install.hook_wired", host=host.name, event=event,
                  matcher=matcher or i18n.t("install.matcher_all")))

    # ⛔ ★every hook command says which host wrote it★ (2026-09-21) — `detect()` only answers "is it
    #    installed here", and on a machine with two hosts that is a different question from "who is
    #    calling me". Unpinned, the second host's edit rules go silent while its shell rules keep
    #    firing (measured: Codex sends `Bash` for shell but `apply_patch` for edits).
    #    ⚠️ A flag, not `env VAR=x` — the same line has to work on Windows, which has neither.
    def with_host(cmd: str) -> str:
        return "%s --host %s" % (cmd, host.name)

    if with_hook:
        # ⛔ The limit comes from the hook itself — its watchdog fires just before this wall, so the
        #    two numbers must never drift apart (§hook.HOOK_TIMEOUT_SEC).
        from brain import hook as _hook
        wire("user_prompt_submit", with_host(entry_command("brain-hook", "brain.hook")),
             _hook.HOOK_TIMEOUT_SEC, "recalling")
        wire("session_start", with_host(entry_command("brain-session", "brain.tail")),
             20, "refreshing index", extra={"async": True})
    if with_guard:
        # ⛔ ★the home goes into the line★ — the shell half cannot ask Python where it is, and a guess
        #    (`~/.claude/brain`) read a different folder from the one the rules were written to on a
        #    Codex-only machine (§store.adopt_home_from_argv).
        from brain import store as _store
        guard = "%s --home %s" % (with_host(entry_command("brain-guard", "brain.guard")),
                                  _quote(_store.brain_home()))
        if os.name == "nt":
            # ⚠️ ★Windows has no shell prefilter — that spot gets slower.★ Never hidden.
            warn(i18n.t("install.windows_slower", host=host.name))
        shell = host.tool_names("run_shell")
        edit = list(dict.fromkeys(
            n for c in ("edit_file", "write_file", "edit_notebook")
            for n in host.tool_names(c)))
        if shell:
            wire("pre_tool_use", guard, 5, "checking rules", matcher="|".join(shell))
        if edit:
            wire("pre_tool_use", guard, 5, "checking rules", matcher="|".join(edit))

    if added and DRY:
        _would(i18n.t("install.would_save_hooks", n=added, path=path))
    elif added:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        ok(i18n.t("install.hooks_saved", host=host.name))

    # ⛔ ★Written is not running★ — Codex trusts each hook entry by hash and refuses the rest.
    #    Measured 2026-09-28: hooks wired on 09-21 never ran in 11 of 13 real sessions because
    #    nobody was told they needed approving. Saying "wired" and stopping there is the lie.
    pending = getattr(host, "untrusted_hooks", lambda: [])()
    if pending:
        warn(i18n.t("install.hooks_untrusted", host=host.name, n=len(pending)))


# ── 6. scheduled jobs ───────────────────────────────────────────────────────
def step_schedule(enable: bool) -> None:
    from brain import health, scheduler, store
    say("\n" + i18n.t("install.step6_title"))
    if not enable:
        say("  " + i18n.t("install.cron_skipped"))
        return
    s = scheduler.active()
    if not s.available():
        warn(i18n.t("install.no_scheduler_platform"))
        return
    home = store.brain_home()
    os.makedirs(home, exist_ok=True)
    jobs = (("brain-vec-daily", "brain.jobs.vec_daily", (9, 0), "vec-daily.log"),
            ("brain-rules-daily", "brain.jobs.rules_daily", (9, 30), "rules-daily.log"))
    for name, module, at, log in jobs:
        label = "com.local-brain.%s" % name.replace("brain-", "")
        cmd = entry_command(name, module)
        parts = [p.strip('"') for p in cmd.split(" -m ")]
        argv = ([parts[0], "-m", parts[1]] if len(parts) == 2 else [cmd.strip('"')])
        if DRY:
            _would(i18n.t("install.would_register_job", label=label,
                         time="%02d:%02d" % (at[0], at[1]), scheduler=s.name,
                         argv=" ".join(argv)))
            continue
        r = s.install(label, argv, at, os.path.join(home, log))
        ok(i18n.t("install.job_ok", name=name, time="%02d:%02d" % (at[0], at[1]),
                  scheduler=s.name)) if r.get("ok") else \
            warn(i18n.t("install.job_failed", name=name,
                       why=r.get("why") or i18n.t("install.job_install_failed")))


# ── wrap-up ─────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python3 -m brain.install",
        description="Install the local brain (macOS / Windows / Linux).")
    ap.add_argument("--with-hook", action="store_true",
                    help="automatic recall (runs on every prompt)")
    ap.add_argument("--with-guard", action="store_true",
                    help="behaviour layer (runs before tool calls)")
    ap.add_argument("--with-cron", action="store_true",
                    help="daily self-improvement jobs")
    ap.add_argument("--all", action="store_true", help="all of the above")
    ap.add_argument("--embed", default="", metavar="PROVIDER",
                    help="engine for meaning-based search: gemini · openai · none (see `brain engines`)")
    ap.add_argument("--judge", default="", metavar="PROVIDER",
                    help="engine that judges candidates: gemini · openai · anthropic · none")
    from brain import stores as _st
    ap.add_argument("--stores", default="", choices=["", "local", "docker"],
                    help="local (default) · docker = every role's default in Docker (%s)"
                         % " + ".join("%s %s" % kv for kv in _st.DOCKER_DEFAULT.items()))
    for role in _st.ROLES:
        ap.add_argument("--%s-store" % role, default="", metavar="NAME[=URL]",
                        help="%s: %s — in Docker, or NAME=URL for a server that already runs"
                             % (role, " · ".join(_st.names(role))))
    ap.add_argument("--lang", default="", help="UI language (en de es fr ja ko)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change and touch NOTHING")
    a = ap.parse_args(argv)
    global DRY
    DRY = a.dry_run
    if a.all:
        a.with_hook = a.with_guard = a.with_cron = True
    if a.lang:
        os.environ["BRAIN_LANG"] = a.lang

    say("")
    say(i18n.t("install.header", root=ROOT))
    say("=" * 60)
    if DRY:
        say(i18n.t("install.dry_run_banner"))
    if not step_prereq():
        return 1
    if not step_config():
        return 1
    if a.lang:
        _save_language(a.lang)
    if not step_index():
        return 1
    try:
        plan = store_plan(a.stores, a.vector_store, a.graph_store)
    except ValueError as exc:
        role, value = exc.args
        say(i18n.t("install.stores_bad", role=role, value=value, choices=", ".join(_st.names(role))))
        return 2
    step_engines(a.embed, a.judge)
    step_stores(plan)
    step_mcp()
    step_hooks(a.with_hook, a.with_guard)
    step_schedule(a.with_cron)

    say("")
    say("=" * 60)
    if WARN:
        say(i18n.t("install.done_with_warnings", n=len(WARN)))
        for w in WARN:
            say("  - %s" % w.splitlines()[0])
    else:
        say(i18n.t("install.done"))
    say("")
    say("  brain status        " + i18n.t("install.hint_status"))
    say("  brain dashboard     " + i18n.t("install.hint_dashboard"))
    say("  brain recall <text> " + i18n.t("install.hint_recall"))
    return 0


def _save_language(lang: str) -> None:
    """⛔ never overwrites the whole config — changes only the language slot."""
    from brain import store
    path = store.default_config_path()
    if DRY:
        _would(i18n.t("install.would_set_language", lang=lang, path=path))
        return
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        d["language"] = lang
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        ok(i18n.t("install.language_set", lang=lang))
    except (OSError, ValueError) as exc:                 # noqa: BLE001
        warn(i18n.t("install.language_save_failed", err=str(exc)[:80]))


if __name__ == "__main__":
    sys.exit(main())
