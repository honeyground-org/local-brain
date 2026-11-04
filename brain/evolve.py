"""Self-evolution, in two layers.

Personal layer (always on): what one person's use teaches stays in that person's brain home - the threshold
measured on their own corpus, the margin learned from their own sample, the reading policy chosen from their
own evidence, the behaviour rules discovered from their own transcripts. None of it ever enters the code.

Public layer (off by default; a maintainer turns it on with `brain evolve --mode propose|merge`): when the
same defect repeats - an unhandled error in a hook, a job or a command - an agent works in an isolated
worktree of the upstream repository, writes a failing check first, fixes the code, and the change leaves the
machine only when every gate passes:

  1. personal traces: no added line carries the home folder, user or host name, the brain home, a corpus
     root, an address, a credential, or a phrase from the person's own notes (a 4-word sequence found in the
     index but not in the repository itself)
  2. language: added lines are plain ASCII outside `brain/locales/`; the words people use belong to the
     personal layer
  3. checks: `tests/clean_room.py` passes on the change
  4. reproduces: a new or changed `tests/verify_*.py` fails on the base commit - it captures the defect
  5. generality: a fresh, read-only reviewer judges the change useful to any user and free of regressions

A change that fails any gate is never pushed. Mode `propose` keeps the gated change as a local branch and a
report. Mode `merge` pushes a branch and opens a pull request that follows CONTRIBUTING.md (title rule,
sign-off, the template's five sections) and asks for auto-merge, so the repository's own rules - required
checks and reviews - decide; once it is merged, a later run fast-forwards the running checkout when it is on
`main`. A run is event-driven (a session start sees a due candidate), handles one candidate, and tries a
candidate again only after it occurs again, at most MAX_ATTEMPTS times. Standard library only.

Records live under `<brain home>/evolve/`: errors.jsonl (signals), state.json (mode, candidates, history,
open pull requests), evolve.log (the detached run's output), <branch>.md (proposals).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable, Iterable, List, Optional, Tuple

# getpass, shutil, socket, sqlite3, tempfile and traceback are imported where a run or an error needs them:
# this module is loaded by every session start, where they would cost about 5 ms for nothing.

ROOT = Path(__file__).resolve().parent.parent
MODES = ("local", "propose", "merge")
THRESHOLD = 2                  # occurrences before a candidate is due: one error can be the weather
MAX_ATTEMPTS = 3
CHECK_TIMEOUT = 1800           # the clean room takes about two minutes; a hung check must not hold the lock
AGENT_TIMEOUT = 3600
FOLLOW_EVERY = 3600            # seconds between looks at open pull requests
ERRORS_CAP = 1 << 20           # errors.jsonl stops growing here until a scan consumes it
LOCK_STALE = 4 * 3600
GRAM = 4
TITLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*(?:, ?[A-Za-z0-9][A-Za-z0-9_./-]*)*: \S.{2,}$")  # §pr_report

AGENT_SCHEMA = {"type": "object", "properties": {
    "fixable": {"type": "boolean"}, "title": {"type": "string"}, "summary": {"type": "string"},
    "effect": {"type": "string"}, "extensibility": {"type": "string"}, "risks": {"type": "string"},
    "test_files": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
    "required": ["fixable", "title", "summary", "effect", "extensibility", "risks", "test_files", "reason"],
    "additionalProperties": False}
REVIEW_SCHEMA = {"type": "object", "properties": {
    "general": {"type": "boolean"}, "regression_risk": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["general", "regression_risk", "reason"], "additionalProperties": False}


class Busy(Exception):
    """Another run holds the lock."""


# ── where things live ────────────────────────────────────────────────────────────────────────────────────

def home() -> Path:
    from brain import store
    return Path(store.brain_home()) / "evolve"


def _errors() -> Path:
    return home() / "errors.jsonl"


def _state_path() -> Path:
    return home() / "state.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── signals ──────────────────────────────────────────────────────────────────────────────────────────────

def record_error(where: str, exc: Optional[BaseException] = None) -> None:
    """Append one unhandled error: where, the exception class and the brain frames it passed through.

    ⛔ Never the message - it can hold a prompt, a note or a path. Never raises: it runs inside hooks.
    """
    try:
        import traceback
        exc = exc if exc is not None else sys.exc_info()[1]
        if exc is None or isinstance(exc, (KeyboardInterrupt, SystemExit)):
            return
        frames = []
        for frame in traceback.extract_tb(exc.__traceback__):
            if not os.path.isabs(frame.filename):
                continue                                 # `<frozen runpy>` (3.11+) would resolve against the cwd
            try:
                rel = Path(frame.filename).resolve().relative_to(ROOT)
            except (ValueError, OSError):
                continue                                 # the standard library and anything outside brain
            frames.append("%s:%s" % (rel.as_posix(), frame.name))
        path = _errors()
        if path.exists() and path.stat().st_size >= ERRORS_CAP:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": _now(), "where": anonymise(where)[:80], "error": anonymise(type(exc).__name__)[:80],
               "frames": [anonymise(f) for f in frames[-6:]]}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:                                    # noqa: BLE001
        pass


def anonymise(text) -> str:
    text = str(text or "")
    for private in sorted({str(Path.home()), os.environ.get("BRAIN_HOME", "")}, key=len, reverse=True):
        if len(private) > 3:
            text = text.replace(private, "<home>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", text)
    text = re.sub(r"(?:[A-Za-z]:)?(?:[/\\][\w.@+-]+){3,}", "<path>", text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<id>", text)
    text = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", text)
    text = re.sub(r"[^\x00-\x7f]+", "<text>", text)
    return re.sub(r"\s+", " ", text).strip()[:400]


def _key(row: dict) -> str:
    frames = row.get("frames") or []
    return "error:%s %s at %s" % (row.get("where", "?"), row.get("error", "?"), frames[-1] if frames else "?")


# ── state ────────────────────────────────────────────────────────────────────────────────────────────────

def state() -> dict:
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            value = {}
    except (OSError, ValueError):
        value = {}
    value.setdefault("mode", "local")
    value.setdefault("repo", None)
    value.setdefault("scan", {"offset": 0})
    value.setdefault("candidates", {})
    value.setdefault("history", [])
    value.setdefault("prs", [])
    return value


def save(value: dict) -> None:
    value["history"] = value["history"][-50:]
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _git_checkout(path: Path) -> bool:
    return subprocess.run(["git", "-C", str(path), "rev-parse", "--git-dir"], capture_output=True).returncode == 0


def configure(mode: Optional[str] = None, repo: Optional[str] = None) -> dict:
    value = state()
    if mode is not None:
        if mode not in MODES:
            raise ValueError("mode must be one of " + ", ".join(MODES))
        value["mode"] = mode
    if repo is not None:
        path = Path(repo).expanduser().resolve()
        if not _git_checkout(path):
            raise ValueError("The evolution repository must be a Git checkout of local-brain: " + str(path))
        value["repo"] = str(path)
    if value["mode"] != "local" and not value.get("repo"):
        if not _git_checkout(ROOT):
            raise ValueError("Modes propose and merge need --repo PATH (a clone of the upstream repository)")
        value["repo"] = str(ROOT)                        # the checkout brain runs from
    if value["mode"] != "local" and not value["scan"].get("started_at"):
        # Count only what happens from now on: earlier errors were seen, and usually fixed, by people.
        path = _errors()
        value["scan"] = {"offset": path.stat().st_size if path.exists() else 0, "started_at": _now()}
        value["candidates"] = {}
    save(value)
    return value


def scan(value: Optional[dict] = None) -> dict:
    """Count new error rows since the last scan. Cheap and incremental; consumes the file once it is read."""
    value = value or state()
    cursor = value["scan"]
    path = _errors()
    if path.exists():
        size = path.stat().st_size
        offset = cursor.get("offset", 0) if cursor.get("offset", 0) <= size else 0
        with path.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
        complete = chunk[:chunk.rfind(b"\n") + 1]        # a row still being written waits for the next scan
        cursor["offset"] = offset + len(complete)
        for line in complete.decode("utf-8", "replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            key = _key(row)
            cand = value["candidates"].setdefault(key, {"count": 0, "first": row.get("at"), "samples": [],
                                                        "attempts": 0, "attempted_at_count": 0, "status": "open"})
            cand["count"] += 1
            cand["last"] = row.get("at")
            sample = anonymise("%s %s via %s" % (row.get("where"), row.get("error"), " > ".join(row.get("frames") or [])))
            if sample not in cand["samples"]:
                cand["samples"] = (cand["samples"] + [sample])[-3:]
            if cand["status"] in ("merged", "proposed") and cand["count"] > cand["attempted_at_count"]:
                cand["status"] = "open"                  # it came back after a fix: try again
        if cursor["offset"] == size and size > 64 * 1024:
            try:                                         # everything was read; a row appended in between is lost,
                os.truncate(str(path), 0)                # and comes back the next time it happens
                cursor["offset"] = 0
            except OSError:
                pass
    save(value)
    return value


def due(value: dict) -> List[str]:
    rows = []
    for key, cand in value["candidates"].items():
        if (cand["status"] in ("open", "rejected") and cand["count"] >= THRESHOLD
                and cand["count"] > cand["attempted_at_count"] and cand["attempts"] < MAX_ATTEMPTS):
            rows.append((cand["count"], key))
    return [key for _, key in sorted(rows, reverse=True)]


def _follow_due(value: dict) -> bool:
    return bool(value.get("prs")) and time.time() - float(value.get("followed_at") or 0) >= FOLLOW_EVERY


# ── the trigger ──────────────────────────────────────────────────────────────────────────────────────────

def maybe_start(launch: Optional[Callable[[], object]] = None):
    """Called from the session-start hook. Starts one detached run when there is work; never raises or blocks.

    In mode local (the default) this is one stat of a file that does not exist.
    """
    try:
        if not _state_path().exists():
            return None
        value = state()
        if value["mode"] == "local" or not value.get("repo") or _locked():
            return None
        value = scan(value)
        if not due(value) and not _follow_due(value):
            return None
        return (launch or _launch)()
    except Exception:                                    # noqa: BLE001 - a hook must never fail because of this
        return None


def _launch():
    from brain import launch, store
    env = dict(os.environ, BRAIN_HOME=store.brain_home(), BRAIN_HOOK_DISABLED="1")
    log = home() / "evolve.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    kw = dict(stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT, close_fds=True, env=env, cwd=str(ROOT))
    if os.name == "nt":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    else:
        kw["start_new_session"] = True
    with log.open("ab") as out:
        process = subprocess.Popen(launch.argv("brain.cli") + ["evolve", "--run"], stdout=out, **kw)
    return process.pid


def _lock_path() -> Path:
    return home() / "run.lock"


def _locked() -> bool:
    path = _lock_path()
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path.exists()
    from brain import deferred
    return deferred._alive(int(held.get("pid") or 0)) and time.time() - float(held.get("at") or 0) < LOCK_STALE


class _Lock:
    """One run at a time, across processes. A file created exclusively; a dead or stale owner is taken over."""

    def __enter__(self):
        path = _lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if _locked():
                    raise Busy("another evolution run is in progress")
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps({"pid": os.getpid(), "at": time.time()}))
            return self
        raise Busy("could not take the evolution lock")

    def __exit__(self, *exc):
        try:
            _lock_path().unlink()
        except OSError:
            pass
        return False


# ── gates ────────────────────────────────────────────────────────────────────────────────────────────────

def _git(*args, cwd=None, check=True, timeout=300) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + [str(a) for a in args], cwd=None if cwd is None else str(cwd),
                          capture_output=True, check=check, timeout=timeout)


def added_lines(work: Path, base: str) -> List[Tuple[str, str]]:
    _git("-C", work, "add", "-A")
    diff = _git("-C", work, "diff", "--cached", "--unified=0", base).stdout
    rows, path = [], None
    for raw in diff.decode("utf-8", "replace").splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("+") and not raw.startswith("+++") and path:
            rows.append((path, raw[1:]))
    return rows


def _grams(text: str) -> set:
    words = re.findall(r"\w+", text.lower())
    out = set()
    for i in range(len(words) - GRAM + 1):
        gram = words[i:i + GRAM]
        if sum(len(w) for w in gram) >= 12:            # `a = b + c` is code, not a phrase
            out.add(" ".join(gram))
    return out


def private_identifiers() -> set:
    """This machine's identity and the places the person's notes live."""
    import getpass
    import socket
    from brain import store
    exact = {str(Path.home()), getpass.getuser(), socket.gethostname(), store.brain_home()}
    try:
        for source in store.load_config(tolerant=True).get("sources") or []:
            if isinstance(source, dict) and source.get("path"):
                exact.add(os.path.expanduser(str(source["path"])))
    except Exception:                                    # noqa: BLE001
        pass
    return {e for e in exact if e and len(e) >= 4}


def corpus_texts() -> Iterable[str]:
    """The person's own notes, as indexed. Read-only; nothing here is ever written anywhere."""
    import sqlite3
    from brain import store
    path = store.db_path()
    if not os.path.exists(path):
        return
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for (body, title, description) in db.execute("SELECT body, title, description FROM docs"):
            yield "%s\n%s\n%s" % (title or "", description or "", body or "")
    except sqlite3.Error:
        return
    finally:
        db.close()


def base_texts(repo: Path, base: str) -> Iterable[str]:
    """Every tracked file of the base commit - what is already public."""
    names = [n for n in _git("-C", repo, "ls-tree", "-r", "-z", "--name-only", base).stdout.decode("utf-8", "replace").split("\0") if n]
    out = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], capture_output=True,
                         input="".join("%s:%s\n" % (base, n) for n in names if "\n" not in n).encode("utf-8")).stdout
    i = 0
    while i < len(out):
        nl = out.find(b"\n", i)
        if nl < 0:
            break
        header = out[i:nl].split()
        if len(header) < 3 or header[1] == b"missing":
            i = nl + 1
            continue
        size = int(header[2])
        yield out[nl + 1:nl + 1 + size].decode("utf-8", "replace")
        i = nl + 1 + size + 1


def personal_hits(rows: List[Tuple[str, str]], repo: Path, base: str,
                  identifiers: Optional[set] = None, corpus: Optional[Iterable[str]] = None) -> List[str]:
    identifiers = private_identifiers() if identifiers is None else identifiers
    public = None
    hits = []
    added = {}
    for path, line in rows:
        lowered = line.lower()
        for item in identifiers:
            low = item.lower()
            if (os.sep in item or "/" in item) and low in lowered:
                hits.append("%s: a path on this machine" % path)
            elif re.search(r"(?<!\w)%s(?!\w)" % re.escape(low), lowered):
                if public is None:
                    public = "\n".join(base_texts(repo, base)).lower()
                if not re.search(r"(?<!\w)%s(?!\w)" % re.escape(low), public):
                    hits.append("%s: the user or host name" % path)
        if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", line) and "noreply" not in lowered and "example." not in lowered:
            hits.append("%s: an e-mail address" % path)
        if re.search(r"(ghp_|github_pat_|sk-[A-Za-z0-9]{20}|AKIA[0-9A-Z]{16}|xox[bap]-|AIza[0-9A-Za-z_-]{20})", line):
            hits.append("%s: a credential-like token" % path)
        for gram in _grams(line):
            added.setdefault(gram, path)
    if added:
        found = set()
        for text in (corpus_texts() if corpus is None else corpus):
            found |= _grams(text) & added.keys()
            if len(found) == len(added):
                break
        if found:
            known = set()
            for text in base_texts(repo, base):
                known |= _grams(text) & found
            for gram in found - known:
                hits.append("%s: a phrase from the person's own notes" % added[gram])
    return sorted(set(hits))


def language_hits(rows: List[Tuple[str, str]]) -> List[str]:
    return sorted({"%s: non-ASCII text" % path for path, line in rows
                   if re.search(r"[^\x00-\x7f]", line) and not path.startswith("brain/locales/")})


def _clean_room(where: Path, names: Optional[List[str]] = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL", "TZ")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "tests/clean_room.py"] + (names or []), cwd=str(where),
                          capture_output=True, timeout=CHECK_TIMEOUT, env=env)


def run_checks(work: Path) -> List[str]:
    """The release bar: every check on the tracked files with an empty home. Returns failures."""
    try:
        result = _clean_room(work)
    except subprocess.TimeoutExpired:
        return ["tests/clean_room.py timed out"]
    if result.returncode != 0:
        return ["tests/clean_room.py exit %d: %s" % (result.returncode,
                                                     anonymise(result.stdout.decode("utf-8", "replace")[-300:]))]
    return []


def reproduces(repo: Path, base: str, work: Path, changed: List[str]) -> List[str]:
    """New or changed checks run on the base commit; the ones that fail there capture the defect.

    A skipped check (exit 77) proves nothing: the clean room counts a skip as a pass.
    """
    tests = [p for p in changed if re.match(r"^tests/verify_[\w-]+\.py$", p)]
    if not tests:
        return []
    import shutil
    import tempfile
    control = Path(tempfile.mkdtemp(prefix="brain-evolve-base-"))
    try:
        _git("-C", repo, "worktree", "add", "--detach", control, base)
        for name in changed:
            if name.startswith("tests/") and (work / name).is_file():
                target = control / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(work / name), str(target))
        _git("-C", control, "add", "-A")
        failed = []
        for name in tests:
            try:
                if _clean_room(control, [Path(name).stem]).returncode != 0:
                    failed.append(name)
            except subprocess.TimeoutExpired:
                pass
        return failed
    finally:
        _git("-C", repo, "worktree", "remove", "--force", control, check=False)
        shutil.rmtree(str(control), ignore_errors=True)


# ── agents ───────────────────────────────────────────────────────────────────────────────────────────────

def agent_prompt(key: str, cand: dict) -> str:
    return (
        "You are improving local-brain, an open-source local memory for coding agents (Python standard library "
        "only, Python 3.8+), in an isolated worktree of its repository (the current directory). One installation "
        "recorded the same unhandled error %d times.\n"
        "Error class: %s\nAnonymised samples (where, exception class, brain frames):\n- %s\n\n"
        "Read CONTRIBUTING.md first, then:\n"
        "1. Write a failing check first: a self-contained tests/verify_<name>.py with a control case, built from "
        "synthetic data only, that exits non-zero while the defect is present (77 means skipped and proves "
        "nothing).\n2. Make the smallest general fix in brain/ or bin/.\n"
        "3. Run python3 tests/clean_room.py and make sure everything passes.\n\n"
        "Rules: never write anyone's paths, names, ids, addresses or phrases; code, comments and docs are plain "
        "ASCII English, and text a user reads goes through brain/locales/ in all six languages; if the right fix "
        "is a value, threshold or word list tuned to one person, or the error is the environment (a full disk, "
        "a network outage), do not write code - answer fixable=false, because such things belong in the "
        "installation's own learned data or are not code defects. Do not commit, push or install.\n"
        "Answer with: fixable; title in the form 'area: what changed' (lower case after the colon); summary "
        "(the problem and for whom); effect (what is different for a user); extensibility (the seam it "
        "extends, new state or settings); risks (what could break, how to undo it); the test files you added "
        "or changed; and your reason."
        % (cand["count"], key, "\n- ".join(cand["samples"])))


def _claude(argv: List[str], prompt: str, work: Path, timeout: int) -> dict:
    import shutil
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="brain-evolve-agent-"))
    try:
        env = dict(os.environ, BRAIN_HOOK_DISABLED="1", BRAIN_HOME=str(scratch / "home"),
                   BRAIN_CONFIG=str(scratch / "config.json"))
        result = subprocess.run(argv, input=prompt, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, env=env)
    finally:
        shutil.rmtree(str(scratch), ignore_errors=True)
    if result.returncode != 0:
        raise RuntimeError("claude exit %d: %s" % (result.returncode, (result.stderr or result.stdout)[-300:]))
    data = json.loads(result.stdout)
    if isinstance(data, list):
        data = next((e for e in reversed(data) if isinstance(e, dict) and e.get("type") == "result"), {})
    answer = data.get("structured_output")
    if answer is None:
        answer = json.loads(data.get("result") or "{}")
    if not isinstance(answer, dict):
        raise ValueError("the answer is not a JSON object")
    return answer


def _sandbox(deny_write: List[str]) -> str:
    return json.dumps({"sandbox": {
        "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
        "filesystem": {"denyWrite": deny_write},
        "network": {"allowedDomains": [], "deniedDomains": ["*"]}}})


def claude_agent(work: Path, prompt: str) -> dict:
    """The fixer: Claude Code without the person's settings, hooks, MCP servers or brain; commands sandboxed
    with no network and no write access to the brain home."""
    from brain import store
    argv = ["claude", "-p", "--strict-mcp-config", "--setting-sources", "", "--output-format", "json",
            "--json-schema", json.dumps(AGENT_SCHEMA), "--permission-mode", "acceptEdits",
            "--permission-prompts", "none", "--settings", _sandbox([store.brain_home()])]
    return _claude(argv, prompt, work, AGENT_TIMEOUT)


def claude_reviewer(work: Path, diff_text: str, key: str) -> dict:
    """A fresh reviewer that can read the worktree and nothing else."""
    prompt = ("Review this change to local-brain, an open-source developer tool. It was written automatically "
              "because one installation saw a repeated error (%s). Answer general=true only if it would help any "
              "user, with any language, notes and habits, and is not tuned to one person; regression_risk=true "
              "if it could break existing behaviour. Diff:\n%s" % (key, diff_text[-60000:]))
    argv = ["claude", "-p", "--tools", "Read,Glob,Grep", "--restricted", "--strict-mcp-config", "--setting-sources", "",
            "--output-format", "json", "--json-schema", json.dumps(REVIEW_SCHEMA)]
    return _claude(argv, prompt, work, 900)


def gh_cli(args: List[str], cwd: Path) -> Tuple[int, str]:
    try:
        result = subprocess.run(["gh"] + args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


# ── one run ──────────────────────────────────────────────────────────────────────────────────────────────

def run(key: Optional[str] = None, agent=None, reviewer=None, gh=None, checkout: Optional[Path] = None) -> dict:
    """One bounded attempt: follow open pull requests, then try the most frequent due candidate."""
    with _Lock():
        value = scan(state())
        if value["mode"] == "local" or not value.get("repo"):
            raise ValueError("Evolution of code is off (mode local). A maintainer turns it on: "
                             "brain evolve --mode propose|merge [--repo PATH]")
        gh = gh or gh_cli
        followed = follow(value, gh, Path(checkout) if checkout else ROOT)
        save(value)
        keys = [key] if key else due(value)
        if not keys or keys[0] not in value["candidates"]:
            return {"at": _now(), "outcome": "nothing_due", "followed": followed}
        key = keys[0]
        cand = value["candidates"][key]
        cand["attempts"] += 1
        cand["attempted_at_count"] = cand["count"]
        save(value)
        record = _attempt(value, key, cand, agent or claude_agent, reviewer or claude_reviewer, gh)
        record["followed"] = followed
        value = state()
        value["candidates"][key]["status"] = {"pr": "pr_open", "proposed": "proposed"}.get(record["outcome"], "rejected")
        if record["outcome"] == "pr":
            value["prs"].append({"url": record["pr"], "candidate": key, "branch": record["branch"], "opened_at": _now()})
        value["history"].append({k: v for k, v in record.items() if k != "diff"})
        save(value)
        return record


def follow(value: dict, gh, checkout: Path) -> List[dict]:
    """What happened to the pull requests this installation opened. A merged one is installed by fast-forward."""
    out = []
    for pr in list(value.get("prs") or []):
        rc, text = gh(["pr", "view", pr["url"], "--json", "state"], Path(value["repo"]))
        try:
            pr_state = json.loads(text).get("state") if rc == 0 else None
        except ValueError:
            pr_state = None
        cand = value["candidates"].get(pr["candidate"])
        if pr_state == "MERGED":
            row = {"at": _now(), "outcome": "merged", "pr": pr["url"], "candidate": pr["candidate"]}
            row.update(fast_forward(checkout))
            if cand:
                cand["status"] = "merged"
                cand["attempted_at_count"] = cand["count"]      # only what happens after the merge reopens it
        elif pr_state == "CLOSED":
            row = {"at": _now(), "outcome": "closed", "pr": pr["url"], "candidate": pr["candidate"]}
            if cand:
                cand["status"] = "rejected"
        else:
            continue
        value["prs"].remove(pr)
        value["history"].append(row)
        out.append(row)
    value["followed_at"] = time.time()
    return out


def fast_forward(checkout: Path) -> dict:
    """Install a merged change into the running checkout - only when it is on main, and only a fast-forward
    (git refuses when it would touch the person's own uncommitted edits)."""
    if not _git_checkout(checkout):
        return {"installed": False, "reason": "the running copy is not a git checkout"}
    branch = _git("-C", checkout, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.decode().strip()
    if branch != "main":
        return {"installed": False, "reason": "the running checkout is not on main"}
    _git("-C", checkout, "fetch", "-q", "origin", "main", check=False, timeout=120)
    merge = _git("-C", checkout, "merge", "-q", "--ff-only", "origin/main", check=False)
    if merge.returncode != 0:
        return {"installed": False, "reason": "not a fast-forward: " + anonymise(merge.stderr.decode("utf-8", "replace"))}
    return {"installed": True}


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.lower())[:48].strip("-") or "fix"


def _title(answer: dict, key: str) -> str:
    title = anonymise(answer.get("title")).strip()
    if TITLE.match(title):
        return title[:100]
    words = re.sub(r"[^a-z0-9 ]+", " ", anonymise(answer.get("summary") or key).lower()).split()
    return "evolve: " + " ".join(words[:10] or ["fix", "a", "repeated", "error"])


def pr_body(key: str, cand: dict, answer: dict, base: str, reproduced: List[str], review: dict) -> str:
    return "\n".join([
        "## Purpose", "",
        "One installation recorded the same unhandled error %d times: `%s`." % (cand["count"], key),
        "", anonymise(answer.get("summary")), "",
        "Written by the self-evolution agent (`brain evolve`, README: Self-evolution); every gate below passed "
        "before this left the machine.", "",
        "## Effect", "", anonymise(answer.get("effect")), "",
        "The new or changed check%s %s fail%s on %s and pass with this change." % (
            "s" if len(reproduced) > 1 else "", ", ".join("`%s`" % t for t in reproduced),
            "" if len(reproduced) > 1 else "s", base[:7]), "",
        "## Scalability and extensibility", "", anonymise(answer.get("extensibility")), "",
        "## Risks and rollback", "", anonymise(answer.get("risks")), "",
        "Independent review: %s" % anonymise(review.get("reason")), "",
        "Rollback: revert this commit.", "",
        "## Checks", "",
        "- [x] `python3 tests/clean_room.py` passes",
        "- [x] New behaviour has a check that can fail: it fails on the base commit",
        "- [x] No personal traces in added lines, title or description (home, user, host, corpus roots, "
        "addresses, credentials, phrases from the person's own notes)",
        "- [x] Plain ASCII outside `brain/locales/`",
        "- [x] A fresh read-only reviewer judged it general, with no regression risk", ""])


def _attempt(value: dict, key: str, cand: dict, agent, reviewer, gh) -> dict:
    import shutil
    import tempfile
    repo = Path(value["repo"])
    record = {"at": _now(), "candidate": key, "mode": value["mode"], "gates": {}}
    has_origin = _git("-C", repo, "remote", "get-url", "origin", check=False).returncode == 0
    if has_origin:
        _git("-C", repo, "fetch", "-q", "origin", check=False, timeout=120)
    ref = "origin/main" if has_origin else "main"
    base = _git("-C", repo, "rev-parse", ref, check=False).stdout.decode().strip()
    if not base:
        record.update(outcome="rejected", reason="no base commit " + ref)
        return record
    work = Path(tempfile.mkdtemp(prefix="brain-evolve-"))
    try:
        _git("-C", repo, "worktree", "add", "--detach", work, base)
        try:
            answer = agent(work, agent_prompt(key, cand)) or {}
        except Exception as error:                       # noqa: BLE001 - nothing changed upstream
            record.update(outcome="rejected", reason="agent failed: " + anonymise(error))
            return record
        record["summary"] = anonymise(answer.get("summary"))
        if not answer.get("fixable", True):
            record.update(outcome="rejected",
                          reason="the agent judged it personal or not a code defect: " + anonymise(answer.get("reason")))
            return record
        rows = added_lines(work, base)
        if not rows:
            record.update(outcome="rejected", reason="no change")
            return record
        changed = _git("-C", work, "diff", "--cached", "--name-only", base).stdout.decode().split()
        title = _title(answer, key)
        words = [("<description>", str(answer.get(f) or "")) for f in ("title", "summary", "effect", "extensibility", "risks")]
        gates = record["gates"]
        gates["personal"] = personal_hits(rows + words, repo, base)
        gates["language"] = language_hits(rows + words)
        gates["checks"] = run_checks(work)
        gates["reproduces"] = reproduces(repo, base, work, changed)
        failed = [name for name in ("personal", "language", "checks") if gates[name]]
        if not gates["reproduces"]:
            failed.append("reproduces")
        if failed:
            record.update(outcome="rejected", reason="gates failed: " + ", ".join(failed))
            return record
        diff_text = _git("-C", work, "diff", "--cached", base).stdout.decode("utf-8", "replace")
        try:
            review = reviewer(work, diff_text, key) or {}
        except Exception as error:                       # noqa: BLE001
            review = {"general": False, "regression_risk": True, "reason": "the reviewer failed: " + anonymise(error)}
        gates["review"] = {"general": bool(review.get("general")),
                           "regression_risk": bool(review.get("regression_risk", True)),
                           "reason": anonymise(review.get("reason"))}
        if not gates["review"]["general"] or gates["review"]["regression_risk"]:
            record.update(outcome="rejected", reason="independent review: " + gates["review"]["reason"])
            return record
        body = pr_body(key, cand, answer, base, gates["reproduces"], gates["review"])
        message = "%s\n\n%s" % (title, body)
        _git("-C", work, "commit", "-q", "-s", "-m", message)
        commit = _git("-C", work, "rev-parse", "HEAD").stdout.decode().strip()
        branch = "evolve/" + _slug(key)
        record.update(commit=commit, branch=branch, title=title)
        if value["mode"] == "merge":
            push = _git("-C", work, "push", "-q", "--force", "origin", "HEAD:refs/heads/" + branch, check=False, timeout=120)
            if push.returncode != 0:
                record.update(outcome="rejected", reason="push refused: " + anonymise(push.stderr.decode("utf-8", "replace")))
                return record
            rc, text = gh(["pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body], work)
            urls = re.findall(r"https?://\S+/pull/\d+", text)
            if rc != 0 or not urls:
                record.update(outcome="rejected", reason="pull request not opened: " + anonymise(text))
                return record
            record.update(outcome="pr", pr=urls[-1])
            rc, text = gh(["pr", "merge", urls[-1], "--auto", "--squash"], work)
            record["auto_merge"] = rc == 0                  # the repository's rules decide when it lands
            if rc != 0:
                record["auto_merge_reason"] = anonymise(text)
        else:
            _git("-C", repo, "branch", "-f", branch, commit)
            report = home() / (branch.split("/")[-1] + ".md")
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("# %s\n\nBranch: %s\nCommit: %s\n\n%s\n\n```diff\n%s\n```\n"
                              % (title, branch, commit, body, diff_text[-20000:]), encoding="utf-8")
            record.update(outcome="proposed", report=str(report))
        return record
    finally:
        _git("-C", repo, "worktree", "remove", "--force", work, check=False)
        shutil.rmtree(str(work), ignore_errors=True)


# ── what a person sees ───────────────────────────────────────────────────────────────────────────────────

def personal_layer() -> dict:
    """What this installation learned from its own use - it stays here."""
    from brain import store
    out = {"stored_in": store.brain_home()}
    try:
        from brain import ruledisc
        out["learned_rules"] = len(ruledisc.load().get("learned") or [])
    except Exception:                                    # noqa: BLE001
        pass
    try:
        from brain import calibrate, personal
        db = store.connect(readonly=True)
        try:
            out["threshold_moves"] = len(calibrate.history(db))
            out["reading_policy"] = personal.state(db).get("arm")
        finally:
            db.close()
    except Exception:                                    # noqa: BLE001
        pass
    return out


def view() -> dict:
    value = scan(state())
    return {"mode": value["mode"], "repo": value.get("repo"),
            "personal_layer": personal_layer(),
            "candidates": {k: {f: v.get(f) for f in ("count", "attempts", "status", "last")}
                           for k, v in value["candidates"].items()},
            "due": due(value), "open_pull_requests": value.get("prs") or [],
            "history": value["history"][-5:],
            "records": {"signals": str(_errors()), "state": str(_state_path()), "log": str(home() / "evolve.log")}}
