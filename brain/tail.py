"""★A session's tail★ — what happens after the last memory save never lands anywhere.

## What the problem actually was (measured 2026-08-12 · 163 real sessions)

The user's instruction was *"the memory brain should be made to save information well when handing off"*,
and the design note said *"if it is not called, that session evaporates whole"*. **Wrong.**
Of 79 real work sessions, the ones that left not a single memory were **1 (1.3%)**,
and the last save's position was **p50 0.99** (right at the session's end). The discipline is being kept.

What evaporates is not the session but **the tail** — what got done after the last save:

| what | measured |
|---|---|
| sessions with real work left in the tail | **21/81 = 26%** |
| the sum of those tails | code writes **214** · commits **114** · user turns 166 |
| across all sessions | **23/163 = 14%** ended with no save |

## ★Why it does not interrupt to ask★ (the measurement that fixed this file's design)

The `Stop` hook fires not at session end but **at the end of every single assistant turn** (measured 39,126 times).
"Ask once if there is an unsaved tail" was replayed against real records:

| policy | hijacked | hit rate | when it interrupted |
|---|---|---|---|
| once per episode | 358 times | — | 22 sessions got 6+ interruptions, 18 at most |
| once per session | 84 sessions | **15~28%** | p50 **0.12** — ★99% in the session's first half★ |
| never interrupt, hand it to the next session | **0 times** | 100% by definition | at session start |

Asking "is there anything to save" at 12% into a session is wasted work — work is still in progress,
and the discipline saves later. So it **never asks. It writes it down, and the next session sees it.**

★The hook never judges what matters★ — it only writes down facts (files changed · commit count · last words).
The judging is left to the model, which holds the context. An earlier memory system saved 94.7% of conversation turns and
ended up with "small talk in first place" — that is where automatic saving leads.

## Not mistaking a live session for a dead one

Several sessions run at once. Announcing another session's tail as "over" is a false alarm.
The verdict comes from two things — ① if `SessionEnd` fired, it is certainly dead ② if it never came, whether
**the transcript file's mtime** is older than the idle threshold. Grounds for the 60-minute threshold: of
263,632 inter-event gaps within sessions, those over 60 minutes were **0.099%** (p99 = 2 minutes).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time

from brain import store

# ---- knobs (grounds are in the table above) --------------------------------
MIN_CODE = 3            # the tail must be at least this big to be worth announcing
MIN_COMMITS = 1         # ⛔ not a trigger (§is_substantive) — carried as context in the notice only
IDLE_DEAD_SEC = 3600    # no update for this long is treated as a dead session (gap p99=2min)
MAX_NOTIFY = 1          # ★only one at a time★ — same discipline as the recall hook (precision first)
KEEP_DAYS = 14          # kept as a record for this long even after being announced
LIST_CAP = 12           # cap on the file/turn lists (so the record does not bloat)
USER_SAY_CHARS = 160

_COMMIT_RE = re.compile(r"git\s+(?:-C\s+\S+\s+)?(?:commit|push)\b")
# ⛔ The memory path is not nailed in — config is canonical (§_memory_root). Someone else's environment
#    differs, and a personal path must never go into a shared repository.

# ⛔ ★A throwaway working file is not a tail★ — a scratch folder is exactly where "a file that does not
# belong to the project" lives. Measured: 9 tail cases in one session were all measurement scripts.
# Raising the threshold instead of fixing what is counted would miss the real 3 code cases.
# ⛔ ★The system's own temp folder★, not one host's folder name — an agent's scratch space sits under
#    it (`$TMPDIR` on macOS, `/tmp` elsewhere). It used to list Claude Code's `/scratchpad/` by name.
_THROWAWAY = tuple(dict.fromkeys(
    ("/private/tmp/", "/tmp/", "/var/folders/", "/.git/", "/node_modules/", "/__pycache__/",
     os.path.realpath(tempfile.gettempdir()).rstrip("/") + "/")))


def _pending_dir() -> str:
    # ⛔ the home is decided in one place (§store.brain_home) — this used to be `~/.claude/brain`
    return os.path.join(store.brain_home(), "pending")


def _cfg() -> dict:
    """config.json, through the one rule that finds it (§store.default_config_path) — this used to read
    only `<next to the package>/config.json`, which an installed copy does not have."""
    return store.load_config(tolerant=True)


def _memory_root() -> str:
    for s in _cfg().get("sources", []):
        if s.get("name") == "memory" and s.get("path"):
            return os.path.expanduser(s["path"]).rstrip("/") + "/"
    return ""


def _is_memory_path(fp: str) -> bool:
    # Any index file counts, not only the one this brain reads — a host can keep one per project, and
    # a session that wrote to another project's index did remember something (no false alarm).
    root = _memory_root()
    return bool(root and fp.startswith(root)) or os.path.basename(fp) == store.index_file_name()


def _indexed_roots() -> list[str]:
    """The roots the brain indexes — `config.json` is canonical (not nailed into the code)."""
    # ⛔ Do not put a personal path in the fallback — if config cannot be read, judge conservatively
    #    as "not an indexed document" (better than judging on a wrong path).
    return [os.path.expanduser(s["path"]).rstrip("/") + "/"
            for s in _cfg().get("sources", []) if s.get("path")]


_ROOTS: list[str] | None = None


def _is_indexed_doc(fp: str) -> bool:
    """★Writing a document the brain will read counts as 'left behind' too★ — not memory files alone.

    `docs/domains/**.md`, a project README, a wiki page all enter `config.json`'s corpus. So a session
    that wrote a domain document already left something recallable. Not counting that as a save would
    raise a false alarm every time a session ends by writing things up in a document.
    """
    global _ROOTS
    if not fp.endswith(".md"):
        return False
    if _ROOTS is None:
        _ROOTS = _indexed_roots()
    return any(fp.startswith(r) for r in _ROOTS)


def _classify(fp: str) -> str:
    """'save' = left something the brain will read · 'code' = a tail · 'skip' = throwaway.
    ⛔ A write the brain will read is a save ★wherever it lives★ — checked before the temp-folder rule, so a
       memory folder that happens to sit under a temp directory is not thrown away."""
    if _is_memory_path(fp) or _is_indexed_doc(fp):
        return "save"
    if any(m in fp for m in _THROWAWAY):
        return "skip"
    return "code"


def _blank_tail() -> dict:
    return {"code_writes": 0, "commits": 0, "files": [], "user_says": [],
            "since": None}


# ---- the pending file -------------------------------------------------------

def _path(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
    return os.path.join(_pending_dir(), safe + ".json")


def load(session_id: str) -> dict | None:
    try:
        with open(_path(session_id)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(rec: dict) -> None:
    os.makedirs(_pending_dir(), exist_ok=True)
    p = _path(rec["session_id"])
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, p)          # so the next session never reads a half-written file


# ---- incremental transcript scan ---------------------------------------------

def scan(transcript: str, rec: dict) -> dict:
    """Reads only what comes after `rec['offset']` and updates the tail.

    ★It must be incremental★ — a transcript grows to tens of MB. Reading it whole every turn adds
    hundreds of ms per turn. Hitting a save resets the tail to 0.
    """
    tail = rec.get("tail") or _blank_tail()
    offset = int(rec.get("offset") or 0)
    saves = int(rec.get("saves") or 0)

    try:
        size = os.path.getsize(transcript)
    except OSError:
        return rec
    if size < offset:               # the file was swapped out (rotated · rewritten) — start over
        offset, tail, saves = 0, _blank_tail(), 0

    try:
        with open(transcript, errors="replace") as f:
            f.seek(offset)
            for line in f:
                if not line.endswith("\n"):     # the last line has not finished being written yet
                    break
                offset += len(line.encode("utf-8", "replace"))
                _apply(line, tail, rec)
                if tail.get("_saved"):
                    tail.pop("_saved")
                    saves += 1
    except OSError:
        return rec

    rec["offset"] = offset
    rec["tail"] = tail
    rec["saves"] = saves
    rec["updated_at"] = int(time.time())
    return rec


def _apply(line: str, tail: dict, rec: dict) -> None:
    # ⛔ ★The host reads its own log★ (§hosts.Host.user_text · tool_calls) — this used to parse Claude
    #    Code's `tool_use` shape and tool names (`Edit`, `Bash`…) itself, and read ★nothing★ from any
    #    other host's log without saying so.
    from brain import hosts
    host = hosts.active()
    text = host.user_text(line)
    if text:
        tail["user_says"] = (tail["user_says"] + [text[:USER_SAY_CHARS]])[-LIST_CAP:]
        return
    for name, kind, arg in host.tool_calls(line):
        # ★A memory save resets the tail to 0★ — whether via a remember call or a direct file write.
        if name.endswith("remember"):
            tail.update(_blank_tail())
            tail["_saved"] = True
            continue
        if kind == "path":
            fp = str(arg or "")
            kind = _classify(fp)
            if kind == "skip":
                continue
            if kind == "save":
                tail.update(_blank_tail())
                tail["_saved"] = True
                continue
            tail["code_writes"] += 1
            tail["since"] = tail["since"] or int(time.time())
            base = fp.replace(os.path.expanduser("~"), "~")
            if base and base not in tail["files"]:
                tail["files"] = (tail["files"] + [base])[-LIST_CAP:]
            continue
        if kind == "shell" and _COMMIT_RE.search(str(arg or "")):
            tail["commits"] += 1
            tail["since"] = tail["since"] or int(time.time())


# ---- verdict -----------------------------------------------------------------

def is_substantive(tail: dict) -> bool:
    """★A tail is something ***built***★ — a commit is not something built, it is something recorded.★

    ⛔ A false alarm caught on the day this shipped (2026-08-13). The first version also used
    `commits ≥ 1` as a trigger, and it turned out **11 of 24 notification targets (46%) were "code 0,
    commit 1"**. Opening the content, they were all the same shape:

        · 42 saves · tail code0 commit1
        · 57 saves · tail code0 commit2   ← user: "sure, hand off and pick it up next session"

    ★They saved a memory and **committed that save**.★ If code writes are 0 since the last save,
    **nothing was built**, and a commit is just moving something already saved into git.
    The better-behaved a session is, the more it ends in this exact shape, so leaving a commit as a
    trigger means **an alarm rings on every session that kept the discipline.**

    → The trigger is code writes alone. The commit count rides along in the notice **as context only**.
    Measured effect: notification targets 24 → 6 (3.7% of 164 sessions).
    ⚠️ What this misses: a session that changed code only through the shell (`sed`, a script), not a tool.
       If a real miss is ever observed, an axis is added then, with grounds (this is a guess for now).
    """
    return tail.get("code_writes", 0) >= MIN_CODE


def looks_dead(rec: dict) -> bool:
    """Certainly dead (SessionEnd), or the transcript has been quiet longer than the idle threshold."""
    if rec.get("closed"):
        return True
    tp = rec.get("transcript")
    try:
        last = os.path.getmtime(tp) if tp else rec.get("updated_at", 0)
    except OSError:
        last = rec.get("updated_at", 0)
    return (time.time() - last) > IDLE_DEAD_SEC


def collect(current_session: str) -> list[dict]:
    """Pick the pending records worth announcing, most recent first."""
    out = []
    try:
        names = os.listdir(_pending_dir())
    except OSError:
        return out
    now = time.time()
    for n in names:
        if not n.endswith(".json"):
            continue
        p = os.path.join(_pending_dir(), n)
        try:
            with open(p) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        # clear out old records
        if now - rec.get("updated_at", 0) > KEEP_DAYS * 86400:
            try:
                os.remove(p)
            except OSError:
                pass
            continue
        if rec.get("session_id") == current_session:
            continue
        if rec.get("notified_at"):
            continue
        if not is_substantive(rec.get("tail") or {}):
            continue
        if not looks_dead(rec):
            continue                      # still alive — never raise a false alarm
        out.append(rec)
    out.sort(key=lambda r: -r.get("updated_at", 0))
    return out[:MAX_NOTIFY]


def mark_notified(rec: dict) -> None:
    rec["notified_at"] = int(time.time())
    try:
        save(rec)
    except OSError:
        pass


def format_notice(recs: list[dict]) -> str:
    lines = ["[brain] ★the previous session ended without saving a memory★ — what was done after the "
             "last save is below. **Judge for yourself whether it is worth remembering**: "
             "if it is worth it, leave it with `remember` (tell the user in one line), "
             "if it was one-off, just ignore it and move on. Do not ask back — decide it yourself."]
    for r in recs:
        t = r.get("tail") or {}
        when = r.get("updated_at", 0)
        ago = max(0, int((time.time() - when) / 60))
        cwd = (r.get("cwd") or "?").replace(os.path.expanduser("~"), "~")
        lines.append(
            "\n· session %s · %s · last activity %d minutes ago (%d saves in that session)"
            % ((r.get("session_id") or "?")[:8], cwd, ago, r.get("saves", 0)))
        lines.append("  unsaved tail: %d code writes · %d commits"
                     % (t.get("code_writes", 0), t.get("commits", 0)))
        if t.get("files"):
            lines.append("  files changed: " + ", ".join(t["files"][:8]))
        if t.get("user_says"):
            says = [s.replace("\n", " ")[:110] for s in t["user_says"][-3:]]
            lines.append("  what the user said then: " + " / ".join('"%s"' % s for s in says))
        if t.get("commits"):
            lines.append("  ⛔ commit content is not here — if needed, check it directly in that "
                         "directory with `git log --oneline -%d`"
                         % min(10, t.get("commits", 1)))
    return "\n".join(lines)


# ---- hook entry points --------------------------------------------------------
# ★None of them block the session★ — all fail quietly and exit 0.

def _stdin_payload() -> dict:
    import sys
    try:
        raw = sys.stdin.read()
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:                                    # noqa: BLE001
        return {}


def main_stop() -> int:
    """The `Stop` hook — ★no output · never blocks★. Quietly updates the tail state only.

    ⛔ Why it never asks here is in the table at the top of this file (interrupting hits 99% in the session's first half).
    """
    d = _stdin_payload()
    sid = d.get("session_id")
    tp = d.get("transcript_path")
    if not sid or not tp:
        return 0
    rec = load(sid) or {"session_id": sid, "offset": 0, "tail": _blank_tail(),
                        "saves": 0, "closed": False, "notified_at": None}
    rec["cwd"] = d.get("cwd") or rec.get("cwd")
    rec["transcript"] = tp
    rec["closed"] = False            # still alive
    try:
        save(scan(tp, rec))
    except OSError:
        pass
    return 0


def main_end() -> int:
    """The `SessionEnd` hook — marks it as certainly dead (so it does not wait for the idle threshold).

    ⚠️ Whether this hook actually fires is unmeasured (2026-08-12). So the design does not depend on
    it — even if it never fires, `looks_dead()`'s idle threshold judges it instead.
    """
    d = _stdin_payload()
    sid = d.get("session_id")
    if not sid:
        return 0
    rec = load(sid)
    if not rec:
        return 0
    tp = d.get("transcript_path") or rec.get("transcript")
    if tp:
        rec["transcript"] = tp
        try:
            rec = scan(tp, rec)
        except OSError:
            pass
    rec["closed"] = True
    rec["ended_at"] = int(time.time())
    try:
        save(rec)
    except OSError:
        pass
    return 0


def main_notice() -> int:
    """Called from the `SessionStart` hook — attaches context if there is something worth announcing."""
    import sys
    d = _stdin_payload()
    recs = collect(d.get("session_id") or "")
    parts = [format_notice(recs)] if recs else []
    # A grown index hitting its limit is announced from the same spot — two output points would
    # concatenate two JSON blobs and the hook would be ignored whole.
    try:
        from brain import indexaudit
        _size = indexaudit.size_notice()
        if _size:
            parts.append(_size)
    except Exception:                                    # noqa: BLE001
        pass
    # ★An index built by an older tokenizer★ is announced here too — the per-prompt hook refuses the
    # 22-second rebuild (its budget is 200ms), so a human has to run `brain index` once (§store.tokenizer_stale).
    try:
        from brain import i18n as _i18n, store as _store
        if _store.tokenizer_stale(_store.connect()):
            parts.append(_i18n.t("session.tokenizer_stale"))
    except Exception:                                    # noqa: BLE001
        pass
    if not parts:
        return 0
    from brain import hosts
    sys.stdout.write(hosts.active().hook_output("session_start", "\n\n".join(parts)))
    for r in recs:
        mark_notified(r)
    return 0


# ★Entry-point names★ — `pyproject.toml`'s console_scripts call these.
# ⛔ Calling only through a shell wrapper does not work on Windows (no POSIX sh). So a name callable
#    with no arguments is kept separate — an entry point cannot be passed arguments.
def cli_stop() -> int:
    from brain import hosts
    hosts.pin_from_argv()                                # the log is read in the calling host's shape
    return _safe(main_stop)


def cli_end() -> int:
    from brain import hosts
    hosts.pin_from_argv()
    return _safe(main_end)


def cli_notice() -> int:
    from brain import hosts
    hosts.pin_from_argv()                                # ★the session-start hook says who called it★
    return _safe(main_notice)


def _safe(fn) -> int:
    """⛔ ★A hook must never block the host, no matter what★ — it ends at 0 even on failure."""
    try:
        return fn() or 0
    except Exception:                                    # noqa: BLE001
        return 0


if __name__ == "__main__":
    import sys
    _cmds = {"stop": cli_stop, "end": cli_end, "notice": cli_notice}
    sys.exit(_cmds.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: 0)())
