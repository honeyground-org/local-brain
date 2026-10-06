"""Storage layer — ★files are the source of truth, the DB is derived★.

That one sentence is the first defence against memory distortion. If the index
breaks or the schema changes, throw it all away and rebuild from the files
(`reindex(full=True)`). No fact can exist only in the DB, so "I edited the DB and
now it disagrees with the original" cannot structurally happen.

An earlier memory system recorded the opposite debt — only the journal (the original) is a file
and everything else is in the DB, which conflicts with its own "incremental records
belong in the DB" principle (§9). Our nature is different: our files are not chat
logs but ★curated documents a human edits by hand★. They do not grow without bound,
a person must be able to read and fix them, and git keeps their history. So files win.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from typing import Dict, Iterable, List, Optional, Tuple

from brain import i18n, textindex

# 4 = moved the usage key from rowid to path (2026-08-11). Always bump it —
# an already-running old process reads this number and announces that it is stale.
SCHEMA_VERSION = "4"

# ★Index the change history★ (2026-08-18 · goal ③ history)
#
# The user's goal: *"more accurate and deeper than plain document search, and it
# should remember and search the history too"*. Measured: of 6 history questions,
# 4 were silent and 1 of the 2 that fired was wrong — the time axis was simply
# not in the index.
#
# The material is in git: 231 commits in this repository, 32,474 characters of
# message across the 46 commits touching memory files. A commit message is the most
# detailed history there is: "what was there → what was done → what was measured".
#
# ⛔ **Do not index commits as independent documents** — two things break at once:
#    ① the corpus grows +20%, shifting the whole IDF scale (a place this project
#       has already been burnt)
#    ② commit messages quote memory bodies, so "the document about X" exists twice
#       and the aboutness defect fixed on the morning of 2026-08-18 comes straight back.
# → Attach them as a **low-weight field** of each document instead. The document
#   count does not grow, so the scale holds.
# ⛔ ★Default 0 — putting them in tf was disproven by measurement★ (2026-08-18)
#    Not one of the 6 history questions changed (4 silent, 2 firing, still wrong),
#    and there was a weak regression instead (A in top-3 3→2 · short queries 41%→38%
#    · gold in top-3 18→17).
#    The reason: meta questions ("check the earlier conversation", "when and why did
#    it change") ★have no topic word★. Nothing you add to the index catches a query
#    with no seed — a bridge with no starting point cannot set off.
#    → History is **material for presenting results, not for matching queries**
#      (see search._present). The switch stays: the next session must be able to
#      reproduce this disproof.
_W_HISTORY = float(os.environ.get("BRAIN_W_HISTORY", "0") or 0)

# Field weights — words in the name and description outweigh the body (§textindex.term_frequencies).
_W_NAME = 6.0
_W_TITLE = 4.0
_W_DESC = 3.0
_W_BODY = 1.0

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_MDLINK = re.compile(r"\]\(([^)\s]+\.md)\)")
# ★Aliases a human declared★ — `[short-name](real-file.md)`. The label *is* another
# name for that document.
_MDLINK_LABELED = re.compile(r"\[([^\]\n]{1,64})\]\(([^)\s]+\.md)\)")
# ⛔ Accepting any label makes `[here]` and `[the doc]` into aliases. Only ★identifier-shaped★ labels count.
_SLUGISH = re.compile(r"^[a-z0-9][a-z0-9_-]{3,63}$")
_H1 = re.compile(r"^#\s+(.+)$", re.M)
_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_CODE_FENCE = re.compile(r"```.*?```", re.S)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
# How a kind is joined to the rest of a filename (`lesson_x`, `note-x`). Which kinds act as prefixes
# is ★learned from the corpus★ (§learn_kind_prefixes) — this is only the punctuation.
_KIND_SEPS = ("_", "-")


def evidence_date(text: str, meta: Dict[str, str]) -> str:
    """★When was the evidence in this memory measured★ — a **different question**
    from the file's modification time.

    A file edited yesterday can hold a number measured six months ago. That gap is
    exactly the incident this repository records as "a stale number misled a decision":
    a stale number quoted as present fact, and a decision went wrong because of it.

    So the **most recent** date written in the body is taken as the evidence date
    (measured: 360 of 434 memories carry a date, median 19 days old, 19 over 90 days).
    A `verified_at` in the frontmatter wins — a human stated that one explicitly.
    """
    explicit = (meta.get("verified_at") or "").strip()
    if _DATE.match(explicit):
        return explicit[:10]
    today = time.strftime("%Y-%m-%d")
    found = [d for d in _DATE.findall(text) if d <= today]
    return max(found) if found else ""


# The old default — from when this was Claude Code only. ⛔ Never remove it (§brain_home).
_LEGACY_HOME = os.path.expanduser("~/.claude/brain")


def brain_home() -> str:
    """Where the brain's data lives (index.db · rules.json · logs · dashboard).

    ⛔⛔ ★Never move data that already exists, silently.★ (2026-09-02)

    While adding host adapters it is tempting to make the home follow the host. Then
    the day someone uses a different host the home becomes `~/.codex/brain` and
    ★the index, the learned rules and the score history all look like they vanished★
    — the files are still there, but nobody reads them. So the order is:

        1. `BRAIN_HOME` if set              ← what a human decided always wins
        2. ★the old location, if it holds data★  ← do not move it
        3. `brain/` under the detected host  ← for a fresh install
        4. `~/.brain`                        ← works even with no host

    ★The brain's data was never the host's.★ Memories and indexes belong to the
    person, and must be the same whichever agent is looking at them. So once the
    home is decided, it stays.
    """
    env = os.environ.get("BRAIN_HOME")
    if env:
        return env
    if os.path.isdir(_LEGACY_HOME):
        return _LEGACY_HOME
    try:
        from brain import hosts
        h = hosts.active()
        if h.name != "generic":
            return os.path.join(h.root(), "brain")
    except Exception:                                    # noqa: BLE001
        pass
    return os.path.expanduser("~/.brain")


def adopt_home_from_argv() -> str:
    """Take `--home <path>` / `--home=<path>` out of `sys.argv` and use it as the home. Returns it, or "".

    ⛔ Why a hook line carries the home (2026-10-06): the shell prefilter (`bin/brain-guard`)
    cannot ask Python where the home is — not starting Python is its whole job — so it used to
    guess `~/.claude/brain`. Where the home resolved elsewhere (a Codex-only machine →
    `~/.codex/brain`), the learning layer wrote the signal file in one place and the shell read
    another: every learned rule silent, no error. So the installer resolves the home once and
    writes the same path into the hook line, and both halves of the hook read it from there.
    ⛔ `BRAIN_HOME` still wins — what a human set beats what an installer recorded.
    """
    import sys as _sys
    args, rest, home, i = _sys.argv[1:], [], "", 0
    while i < len(args):
        a = args[i]
        if a == "--home" and i + 1 < len(args):
            home, i = args[i + 1], i + 2
            continue
        if a.startswith("--home="):
            home, i = a.split("=", 1)[1], i + 1
            continue
        rest.append(a)
        i += 1
    _sys.argv = _sys.argv[:1] + rest
    if home and not os.environ.get("BRAIN_HOME"):
        os.environ["BRAIN_HOME"] = os.path.expanduser(home)
    return os.environ.get("BRAIN_HOME", "") if home else ""


def db_path() -> str:
    return os.path.join(brain_home(), "index.db")


def default_config_path() -> str:
    """Where the file that says what to remember lives.

    ⛔ ★Someone who installed with pip has no repository★ (2026-09-02). The old code
    always looked at `<next to the package>/config.json`, but in an installed copy
    that is ★site-packages★ — writing a person's settings there means ① removing the
    package deletes the settings, ② an upgrade overwrites them, and ③ it may not even
    be writable.

    Order (first wins) — the same rule as `brain_home()`:
        1. `BRAIN_CONFIG`                 what a human decided wins
        2. ★next to the repository★       does not break a development checkout
        3. `brain_home()/config.json`     where an installed copy keeps it
    """
    env = os.environ.get("BRAIN_CONFIG")
    if env:
        return env
    beside = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "config.json")
    if os.path.isfile(beside):
        return beside
    return os.path.join(brain_home(), "config.json")


def _source_path(name: str) -> str:
    """The path of one configured source. ★The door that keeps personal paths out of the code.★

    ⛔ Do not invent a new settings key — the same fact written in both the config and
    the code means one of them gets fixed and the other does not. Derive it from the
    existing `sources[].name` convention (the installer creates them with that name).
    """
    try:
        for s in load_config().get("sources", []):
            if s.get("name") == name:
                return os.path.expanduser(s.get("path") or "")
    except (OSError, ValueError, KeyError):
        pass
    return ""


def memory_dir() -> str:
    """Where memory files live. Empty string if none (callers must pass over it quietly)."""
    return _source_path("memory")


def index_file_name() -> str:
    """The index file's ★name★ (config `index_file`, MEMORY.md by default) — also known when no memory
    folder is configured, for callers that recognise the file wherever it is written."""
    try:
        return load_config().get("index_file") or "MEMORY.md"
    except (OSError, ValueError):
        return "MEMORY.md"


def index_file() -> str:
    """The index file a human maintains by hand (MEMORY.md by default).

    The name is configurable — it may be called something else on another machine.
    """
    d = memory_dir()
    if not d:
        return ""
    return os.path.join(d, index_file_name())


_REPO_CACHE: Optional[List[str]] = None


def git_repos() -> List[str]:
    """The git repositories the indexed sources belong to. ★Found, never hardcoded.★

    The material for change history (§_git_history, timeline) comes from here. For each
    source path, ask `rev-parse --show-toplevel` and de-duplicate — other people lay
    their repositories out differently.
    """
    global _REPO_CACHE
    if _REPO_CACHE is not None:
        return _REPO_CACHE
    import subprocess
    seen: List[str] = []
    try:
        srcs = load_config().get("sources", [])
    except (OSError, ValueError):
        srcs = []
    for s in srcs:
        path = os.path.expanduser(s.get("path") or "")
        if not path or not os.path.isdir(path):
            continue
        try:
            r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, timeout=10)
            top = r.stdout.strip()
            if top and top not in seen:
                seen.append(top)
        except Exception:                                    # noqa: BLE001
            continue
    _REPO_CACHE = seen
    return seen


def load_config(tolerant: bool = False) -> dict:
    """config.json — ★the single source of truth for what is remembered★.

    `tolerant=True` exists for callers that run before installation, when the file does
    not exist yet (`detect`).
    ⛔ Never use tolerant on the indexing path — silently turning "no configuration" into
       "an empty corpus" means nobody can find out why nothing comes back.
    """
    try:
        with open(default_config_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        if tolerant:
            return {"sources": []}
        raise


def save_config(cfg: dict) -> str:
    """Write the configuration ★atomically★ — dying halfway must not leave half a config."""
    path = default_config_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def add_source(src: dict) -> dict:
    """Add one source. ★Duplication is judged by path★, and the same name may repeat
    (a person can have more than one Obsidian vault — identity is the path, not the name).

    Returns: {"action": "added"|"exists"|"missing", ...}
    """
    cfg = load_config(tolerant=True)
    want = os.path.normpath(os.path.expanduser(src.get("path", "")))
    if not want or not os.path.isdir(want):
        return {"action": "missing", "path": src.get("path", "")}
    for s in cfg.setdefault("sources", []):
        if os.path.normpath(os.path.expanduser(s.get("path", ""))) == want:
            return {"action": "exists", "path": s["path"], "name": s.get("name")}
    cfg["sources"].append(src)
    save_config(cfg)
    return {"action": "added", "path": src["path"], "name": src.get("name")}


def remove_source(name_or_path: str) -> dict:
    """Remove a source by name or path. ⛔ Its indexed documents are cleaned up on the next index."""
    cfg = load_config(tolerant=True)
    want = os.path.normpath(os.path.expanduser(name_or_path))
    keep, gone = [], []
    for s in cfg.get("sources", []):
        p = os.path.normpath(os.path.expanduser(s.get("path", "")))
        if s.get("name") == name_or_path or p == want:
            gone.append(s)
        else:
            keep.append(s)
    if gone:
        cfg["sources"] = keep
        save_config(cfg)
    return {"removed": [g.get("path") for g in gone], "left": len(keep)}


# ---------------------------------------------------------------- reading documents

def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Pull ★only the flat keys★ out of the YAML frontmatter (name/description/metadata.type).

    Why not a YAML parser: to keep the zero-dependency promise. Our frontmatter has
    exactly one shape, indented two levels (measured across 434 files), and treating an
    unreadable key as absent does not kill search (the body is indexed regardless).
    """
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[:1].isspace()
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        val = v.strip().strip("'\"")
        if not val:
            continue
        # keys indented under `metadata:` are taken as-is, without the prefix (type, etc.)
        meta[key if not indented else key] = val
    return meta, text[m.end():]


def doc_name(path: str, source: str, root: str, meta: Dict[str, str]) -> str:
    """★A name is an address in the graph★ — collide, and links point at the wrong place.

    Memories keep their filename: a `[[lesson_...]]` wikilink points at that name.
    Everything else becomes `source:relative/path` — the first index produced 13 documents
    all named `CLAUDE`, and then the `docs.name = links.dst_name` join attaches anywhere.
    """
    base = os.path.splitext(os.path.basename(path))[0]
    if source == "memory":
        # ★The filename is the address — not the frontmatter name★ (measured 2026-08-10)
        # Of 434 memories, 158 had a hyphenated frontmatter `name` and an underscored
        # filename. `[[...]]` wikilinks point at the **filename**, so using the frontmatter
        # as the name breaks the graph for all 158 — with no error, silently.
        # The frontmatter name is not discarded; it is kept as an alias (§aliases).
        return base
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = os.path.basename(path)
    rel = os.path.splitext(rel)[0]
    return "%s:%s" % (source, rel)


def _triggers_of(meta: Dict[str, str]) -> List[str]:
    """Frontmatter `triggers: ["phrase one", "phrase two"]` → the list of phrases.

    ★Why a declaration and not a score★ (user instruction 2026-08-19: *"do not gate this
    with a score threshold; there must be a clearer query and a way to check it"*) — calling
    a thread by its name to continue it is ★a lookup, not a search★. Leave it to whether a
    BM25 score clears a threshold and it silently fails to appear because the file is long
    (length normalisation) or common words got mixed in — measured: one such phrase scored
    8.81 against a threshold of 9.46. So the file **declares** the phrase and the hook matches
    it **exactly** (score-independent). Being a declaration, it can be checked — the audit in
    §brain/triggers.py measures ①uniqueness ②whether it resolves to its own document ③whether
    it fires too often against real prompts.
    """
    raw = (meta.get("triggers") or "").strip()
    if not raw:
        return []
    raw = raw.strip("[]")
    out = []
    for part in raw.split('",'):
        t = part.strip().strip('"').strip("'").strip()
        if len(t) >= 4:
            out.append(t)
    return out


def read_doc(path: str, source: str, root: str = "") -> Optional[dict]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    st = os.stat(path)
    meta, body = parse_frontmatter(raw)
    name = doc_name(path, source, root or os.path.dirname(path), meta)
    h1 = _H1.search(body)
    title = meta.get("title") or (h1.group(1).strip() if h1 else name.replace("_", " "))
    desc = meta.get("description") or ""
    # ★Text inside code is not a link★ (a parser bug exposed by measurement, 2026-08-10)
    # A `[[link]]` written in a document **as an example** was parsed as a link, producing a
    # broken link to a document called "link" that never existed. In Markdown, what is inside
    # backticks is code, not a link — the parser must follow that rule or diagnostics lie.
    scan = _CODE_FENCE.sub(" ", raw)
    scan = _CODE_SPAN.sub(" ", scan)
    links = set()
    for t in _WIKILINK.findall(scan):
        t = t.strip()
        # ★`[[foo.md]]` points at `foo`★ — people write the file name as they see it.
        # Without stripping the extension it silently breaks as "no document named foo.md" (6 measured).
        if t.lower().endswith(".md"):
            t = t[:-3]
        if t:
            links.add(t)
    # ★Resolve relative-path links **by path** — never by filename★
    # The filename in `[CLAUDE.md](../../x/acme-dashboard/CLAUDE.md)` is `CLAUDE`, and the
    # corpus holds 30 such documents. **Only the path knows** which one is meant.
    link_paths = set()
    base_dir = os.path.dirname(path)
    for rel in _MDLINK.findall(scan):
        if rel.startswith(("http://", "https://")):
            continue
        link_paths.add(os.path.normpath(os.path.join(base_dir, rel)))
    links.discard(name)
    # ★Learn the aliases a human already declared★ (2026-08-31)
    #
    # ⛔ The brain had 2,600 aliases stored and still could not find `[[short_name]]`.
    #    The generation rules were only ①strip the prefix ②frontmatter name ③hyphen↔underscore,
    #    so ★a short name a human chose by hand★ appeared nowhere. Yet that name was already
    #    written down — `MEMORY.md` declares
    #    `[short_name](lesson_a_long_descriptive_name.md)` and the memories call it
    #    as `[[short_name]]`. That path was dead.
    #
    # ★One rule, and it generalises★ — any document writing `[label](target.md)` declares an
    #    alias for that target. This is not a special case for MEMORY.md. What people write is
    #    taken as knowledge directly.
    # ⛔ The rule lives in ★one place★ (§_declared_pairs) — if the index file and ordinary
    #    documents each carried their own copy, the next edit would fix only one. This
    #    repository has been through that more than once.
    # ⛔ ★A document declaring its own alias is the most natural place for it★ (2026-08-31).
    #    The first version blocked exactly that with `t != name`, on the theory that "a link
    #    to yourself is meaningless" — but ★an alias declaration is not a link★. When other
    #    memories call something `[[short_name]]`, having that document declare the short
    #    name itself is better than someone else's document (MEMORY.md) declaring it for them:
    #    the owner of the name gets to choose it.
    #    The only case filtered out is ★an alias identical to its target★ (that adds nothing).
    declared = [(a, t) for a, t in _declared_pairs(scan) if a != t]

    aliases = set()
    # The name without its kind prefix (`feedback_x` → `x`) is not added here: which prefixes are
    # kinds is a fact about ★the whole corpus★, so it is derived after indexing (§_build_kind_aliases).
    explicit = (meta.get("name") or "").strip()
    if explicit and explicit != name:
        aliases.add(explicit)
    # Hyphen/underscore wobble is treated as the same thing — people mix the two spellings.
    for variant in (name.replace("_", "-"), name.replace("-", "_")):
        if variant != name:
            aliases.add(variant)
    return {
        "name": name, "aliases": sorted(aliases), "declared": declared,
        "triggers": _triggers_of(meta),
        "path": path, "source": source,
        "evidence_date": evidence_date(raw, meta),
        "kind": meta.get("type") or meta.get("node_type") or "",
        "title": title, "description": desc, "body": body, "raw": raw,
        "mtime": st.st_mtime, "size": st.st_size,
        "sha": hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest(),
        "links": sorted(links),
        "link_paths": sorted(link_paths),
    }


def _is_worktree(path: str) -> bool:
    """★Is this a git worktree checkout★ — a **copy** of the same repository, so it is not indexed.

    Measured (2026-08-12): `acme-api` existed three times as the original plus `-wt101` and
    `-wt102`, and `acme-web` twice as the original plus `aw-av-help`. Index them all and the
    same document goes in several times, and ★whichever side has more volume dominates the
    ranking★ (the same reason source weights exist).

    ⛔ Do not filter by name pattern — worktrees keep appearing and disappearing, so any list
    goes stale immediately. A worktree's `.git` is a **file**, not a directory (a single
    `gitdir:` line). That is the definition itself, so it catches them whatever they are named.
    """
    dot = os.path.join(path, ".git")
    return os.path.isfile(dot)


def iter_source_files(cfg: dict) -> Iterable[Tuple[str, str, str]]:
    """(path, source, root) — walks only what the configuration names. A new corpus is one config.json entry."""
    for entry in cfg.get("sources", []):
        root = os.path.expanduser(entry["path"])
        source = entry["name"]
        depth = int(entry.get("max_depth", 99))
        include = entry.get("include", ["*.md"])
        excludes = entry.get("exclude", [])
        if os.path.isfile(root):
            yield root, source, os.path.dirname(root)
            continue
        base_depth = root.rstrip("/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d not in
                           ("node_modules", "vendor", "dist", "build", "__pycache__")
                           and not _is_worktree(os.path.join(dirpath, d))]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                if not any(_match(fn, pat) for pat in include):
                    continue
                if any(ex in p for ex in excludes):
                    continue
                yield p, source, root


def _match(name: str, pattern: str) -> bool:
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


# ---------------------------------------------------------------- DB

def connect() -> sqlite3.Connection:
    os.makedirs(brain_home(), exist_ok=True)
    db = sqlite3.connect(db_path(), timeout=15.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    _init(db)
    return db


def _init(db: sqlite3.Connection) -> None:
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS docs("
            " id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,"
            " source TEXT NOT NULL, kind TEXT NOT NULL DEFAULT '',"
            " title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',"
            " body TEXT NOT NULL DEFAULT '', mtime REAL NOT NULL DEFAULT 0,"
            " size INTEGER NOT NULL DEFAULT 0, sha TEXT NOT NULL DEFAULT '',"
            " doclen REAL NOT NULL DEFAULT 0, weight REAL NOT NULL DEFAULT 1.0,"
            " indexed_at TEXT NOT NULL DEFAULT '')")
        db.execute("CREATE INDEX IF NOT EXISTS idx_docs_name ON docs(name)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_docs_source ON docs(source)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS postings("
            " term TEXT NOT NULL, doc_id INTEGER NOT NULL, tf REAL NOT NULL,"
            " PRIMARY KEY(term, doc_id)) WITHOUT ROWID")
        db.execute("CREATE TABLE IF NOT EXISTS terms(term TEXT PRIMARY KEY, df INTEGER NOT NULL)")
        # ★Consonant skeleton → word★ (2026-08-20) — a derived table that lets the
        # transliteration bridge cross differences in vowel spelling, so a phonetic rendering
        # reaches the original word. Derived, so it is only built during indexing
        # (§_rebuild_skeletons) — maintained by hand it would silently diverge.
        db.execute("CREATE TABLE IF NOT EXISTS skeletons("
                   " skel TEXT NOT NULL, term TEXT NOT NULL, df INTEGER NOT NULL,"
                   " PRIMARY KEY(skel, term))")
        db.execute("CREATE INDEX IF NOT EXISTS idx_skel ON skeletons(skel)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS links("
            " src_id INTEGER NOT NULL, dst_name TEXT NOT NULL,"
            " PRIMARY KEY(src_id, dst_name)) WITHOUT ROWID")
        db.execute("CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst_name)")
        # Relative-path Markdown links — resolved to real paths once indexing finishes
        db.execute(
            "CREATE TABLE IF NOT EXISTS path_links("
            " src_id INTEGER NOT NULL, dst_path TEXT NOT NULL,"
            " PRIMARY KEY(src_id, dst_path)) WITHOUT ROWID")
        # Aliases — other spellings pointing at the same document (frontmatter name,
        # hyphen/underscore variants). Without this a link breaks silently over one character.
        db.execute(
            "CREATE TABLE IF NOT EXISTS aliases("
            " alias TEXT PRIMARY KEY, doc_id INTEGER NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_aliases_doc ON aliases(doc_id)")
        # ★Aliases a human declared★ — holds the declaring document (src_id) and the target ★name★.
        # ⛔ The ★name★, not the target's rowid: the declaration may be indexed first, and a
        #    reindexed target gets a new rowid. This repository once hung usage records on
        #    rowids and lost them (§usage). The view joins by name.
        db.execute("CREATE TABLE IF NOT EXISTS alias_decl("
                   " alias TEXT NOT NULL, target TEXT NOT NULL, src_id INTEGER NOT NULL,"
                   " PRIMARY KEY(alias, target))")
        db.execute("CREATE INDEX IF NOT EXISTS idx_alias_decl_src ON alias_decl(src_id)")
        # ★A name with its kind prefix dropped or added★ — derived from the whole corpus after every
        # index (§_build_kind_aliases), so it has a table of its own that is rebuilt, never patched.
        db.execute("CREATE TABLE IF NOT EXISTS kind_aliases("
                   " alias TEXT NOT NULL, doc_id INTEGER NOT NULL,"
                   " PRIMARY KEY(alias, doc_id)) WITHOUT ROWID")
        # The single door for name resolution — links, neighbours and diagnostics all pass here.
        # However many spelling rules accumulate, there is exactly one place to fix (extensibility).
        db.execute("DROP VIEW IF EXISTS name_map")
        db.execute("CREATE VIEW name_map AS "
                   " SELECT name AS key, id AS doc_id FROM docs"
                   " UNION ALL SELECT alias AS key, doc_id FROM aliases"
                   # ⛔ ★Never shadow a name that really exists★ — if a declaration covers a
                   #    real document name, the path to that document is cut. `NOT EXISTS` prevents it.
                   " UNION ALL SELECT a.alias AS key, d.id AS doc_id"
                   "   FROM alias_decl a JOIN docs d ON d.name = a.target"
                   "  WHERE NOT EXISTS (SELECT 1 FROM docs x WHERE x.name = a.alias)"
                   " UNION ALL SELECT k.alias AS key, k.doc_id FROM kind_aliases k"
                   "  WHERE NOT EXISTS (SELECT 1 FROM docs x WHERE x.name = k.alias)")
        # The persistence surface for self-improvement — what actually got used.
        # ★The key is the path, not the rowid★ — `docs` is a **derivative** that can be rebuilt
        # from files at any time, but this table is **earned** and cannot be. Hang it on a
        # derivative's serial number and one reindex writes someone else's history over yours
        # (measured: of 52 top-ranked records, 42 lost their own history). A path does not change
        # while the file lives, so the records stay put even through a full rebuild.
        db.execute(
            "CREATE TABLE IF NOT EXISTS usage("
            " path TEXT PRIMARY KEY, hits INTEGER NOT NULL DEFAULT 0,"
            " opens INTEGER NOT NULL DEFAULT 0, useful INTEGER NOT NULL DEFAULT 0,"
            " wrong INTEGER NOT NULL DEFAULT 0, stale INTEGER NOT NULL DEFAULT 0,"
            " last_hit TEXT NOT NULL DEFAULT '')")
        # Recall log — the evidence behind self-improvement suggestions (what was asked and missed).
        db.execute(
            "CREATE TABLE IF NOT EXISTS recalls("
            " id INTEGER PRIMARY KEY, ts TEXT NOT NULL, query TEXT NOT NULL,"
            " terms TEXT NOT NULL DEFAULT '', hits INTEGER NOT NULL DEFAULT 0,"
            " top TEXT NOT NULL DEFAULT '')")
    _migrate(db)
    # ★If the DB is newer than the code, say so★
    # On one machine **several processes share the same DB** (one MCP server per session, plus
    # the hooks). Fixing the code does not touch a process that is already running: it keeps the
    # **old module in memory** (measured 2026-08-11: 7 servers up for 1 day 18 hours). In that
    # state, old SQL against a new schema fails with `no such column`, and all the user sees is
    # "recall is throwing an error" — ⛔ with the cause written down nowhere. So leave a line here.
    # Never write the version downwards — an old process must not roll back a new DB's version.
    ver = get_meta(db, "schema_version")
    try:
        newer = ver and int(ver) > int(SCHEMA_VERSION)
    except ValueError:
        newer = False
    if newer:
        print("[brain] " + i18n.t("store.schema_newer", db_ver=ver, code_ver=SCHEMA_VERSION) + "\n"
              "[brain]    " + i18n.t("store.reconnect_writes"), file=sys.stderr)
    elif ver != SCHEMA_VERSION:
        set_meta(db, "schema_version", SCHEMA_VERSION)


def _migrate(db: sqlite3.Connection) -> None:
    """★`CREATE TABLE IF NOT EXISTS` does not add a column to an existing DB★
    — a fresh install is fine and only the machines already using it break with `no such column`.
    An earlier memory system was burnt in the same place (its own schema migration)."""
    have = {r["name"] for r in db.execute("PRAGMA table_info(docs)")}
    with db:
        if "prior" not in have:
            db.execute("ALTER TABLE docs ADD COLUMN prior REAL NOT NULL DEFAULT 1.0")
        if "evidence_date" not in have:
            db.execute("ALTER TABLE docs ADD COLUMN evidence_date TEXT NOT NULL DEFAULT ''")
    # Move usage from a rowid key to a path key (see the §usage comment).
    ucols = {r["name"] for r in db.execute("PRAGMA table_info(usage)")}
    if ucols and "path" not in ucols:
        with db:
            db.execute("ALTER TABLE usage RENAME TO usage_by_rowid")
            db.execute(
                "CREATE TABLE usage("
                " path TEXT PRIMARY KEY, hits INTEGER NOT NULL DEFAULT 0,"
                " opens INTEGER NOT NULL DEFAULT 0, useful INTEGER NOT NULL DEFAULT 0,"
                " wrong INTEGER NOT NULL DEFAULT 0, stale INTEGER NOT NULL DEFAULT 0,"
                " last_hit TEXT NOT NULL DEFAULT '')")
            db.execute(
                "INSERT OR IGNORE INTO usage(path,hits,opens,useful,wrong,stale,last_hit) "
                "SELECT d.path,u.hits,u.opens,u.useful,u.wrong,u.stale,u.last_hit "
                "FROM usage_by_rowid u JOIN docs d ON d.id=u.doc_id")
            db.execute("DROP TABLE usage_by_rowid")


def get_meta(db: sqlite3.Connection, k: str, default: str = "") -> str:
    row = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row["v"] if row else default


def set_meta(db: sqlite3.Connection, k: str, v: str) -> None:
    with db:
        db.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                   "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def tokenizer_stale(db: sqlite3.Connection) -> bool:
    """Was this index built by a tokenizer other than the one this process holds (§textindex.TOKENIZER_VERSION).

    An index with no stamp is version 1 — every index built before 2026-09-08. An ★empty★ index is
    never stale: there is nothing to rebuild, and the first pass stamps it.
    """
    if not db.execute("SELECT 1 FROM docs LIMIT 1").fetchone():
        return False
    return get_meta(db, "tokenizer", "1") != str(textindex.TOKENIZER_VERSION)




def code_repos(max_repos: int = 60) -> List[str]:
    """★The repositories the code lives in★ — the configured source paths and the git
    repositories ★one level below them★.

    Why `git_repos()` alone is not enough (measured 2026-08-24): all configured sources sit
    under one workspace directory, so `git_repos()` returns ★one★ repository. But the purpose
    and the changes of the code live in the 19 repositories beneath it (acme-web, acme-api, …).
    Answering "why was this changed" means reading those commits.

    ⛔ Worktree copies are excluded — otherwise the same history is read several times.
    """
    out: List[str] = list(git_repos())
    seen = {os.path.realpath(p) for p in out}
    try:
        srcs = load_config().get("sources", [])
    except (OSError, ValueError):
        srcs = []
    for s in srcs:
        root = os.path.expanduser(s.get("path") or "")
        if not os.path.isdir(root):
            continue
        try:
            names = sorted(os.listdir(root))[:400]
        except OSError:
            continue
        for name in names:
            if len(out) >= max_repos:
                return out
            cand = os.path.join(root, name)
            git = os.path.join(cand, ".git")
            if not os.path.isdir(cand) or not os.path.exists(git):
                continue
            if os.path.isfile(git):
                continue                          # a worktree copy
            real = os.path.realpath(cand)
            if real in seen:
                continue
            seen.add(real)
            out.append(cand)
    return out


# ★Do not copy what git already has★ (user instruction 2026-08-24):
#   *"GitHub already holds all of it — what we need is how accurately it remembers and
#     conveys things … the brain has to be efficient about memory and storage."*
#
# The first version ★copied★ commit bodies into a table — 65,800 keys, and the DB went
# 44 → 113MB (+69MB). Even tightened it was +33.5MB. But git already holds those bodies
# completely. A copy costs space, goes stale (diverging after a rebase or an amend), and
# adds ★nothing at all★ to accuracy.
#
# ⇒ What the brain stores is ★the way to get there★: file → (date, repo@sha, subject).
#   The body is fetched with `git show` when asked (a few ms). Storage is near zero and the
#   answer is always current.
RATIONALE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".php", ".go", ".rb", ".rs",
                 ".java", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".cs",
                 ".vue", ".svelte", ".sql", ".sh", ".tf", ".md", ".json", ".yml",
                 ".yaml")
PTR_PER_FILE = int(os.environ.get("BRAIN_WHY_PER_FILE", "4") or 4)


def _git_pointers(max_per_repo: int = 400):
    """(repos, commits, file→sha references) — ★never store the same thing twice★.

    ⛔ Why normalise (measured 2026-08-24): repeating "date + subject" per file made the table
       payload ★8.1MB★ — larger than the total body text indexed (7.1MB). One commit touching
       30 files put the same subject in 30 times. ⇒ Store the commit ★once★ and let files
       reference the sha. (And do not store the body at all — git has it and `why()` fetches it.)
    """
    import subprocess
    repos: Dict[str, int] = {}
    commits: Dict[tuple, tuple] = {}          # (rid, sha) → (date, subject)
    files: Dict[str, List[str]] = {}          # key → ["rid:sha", …]
    for repo in code_repos():
        rid = repos.setdefault(repo, len(repos))
        try:
            p = subprocess.run(
                ["git", "-C", repo, "log", "--no-merges", "-n", str(max_per_repo),
                 "--format=%x02%ad%x01%h%x01%s", "--date=short", "--name-only"],
                capture_output=True, text=True, timeout=90)
        except Exception:                                # noqa: BLE001
            continue
        if p.returncode != 0:
            continue
        for chunk in p.stdout.split("\x02"):
            head, _, files_part = chunk.partition("\n")
            parts = head.split("\x01")
            if len(parts) < 3:
                continue
            date, sha, subj = parts[0], parts[1], parts[2]
            ref = "%d:%s" % (rid, sha)
            touched = False
            for f in files_part.splitlines():
                f = f.strip()
                if not f or not f.endswith(RATIONALE_EXT):
                    continue
                for key in ("/".join(f.split("/")[-2:]), os.path.basename(f)):
                    lst = files.setdefault(key, [])
                    if len(lst) < PTR_PER_FILE and ref not in lst:
                        lst.append(ref)
                        touched = True
            if touched:
                commits[(rid, sha)] = (date, subj)
    return repos, commits, files


def save_git_pointers(db: sqlite3.Connection) -> int:
    """Build the pointer tables — ★once, at index time★. No body copies, and each commit stored once."""
    repos, commits, files = _git_pointers()
    try:
        db.execute("CREATE TABLE IF NOT EXISTS git_repos_tbl("
                   " rid INTEGER PRIMARY KEY, path TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS git_commits("
                   " rid INTEGER NOT NULL, sha TEXT NOT NULL, date TEXT NOT NULL,"
                   " subject TEXT NOT NULL, PRIMARY KEY(rid, sha))")
        db.execute("CREATE TABLE IF NOT EXISTS git_pointers("
                   " key TEXT PRIMARY KEY, refs TEXT NOT NULL)")
        with db:
            db.execute("DELETE FROM git_repos_tbl")
            db.execute("DELETE FROM git_commits")
            db.execute("DELETE FROM git_pointers")
            db.executemany("INSERT INTO git_repos_tbl(rid, path) VALUES(?,?)",
                           [(v, k) for k, v in repos.items()])
            db.executemany("INSERT INTO git_commits(rid, sha, date, subject) "
                           "VALUES(?,?,?,?)",
                           [(r, sh, d, su) for (r, sh), (d, su) in commits.items()])
            db.executemany("INSERT INTO git_pointers(key, refs) VALUES(?,?)",
                           [(k, ",".join(v)) for k, v in files.items()])
    except sqlite3.Error:
        return 0
    return len(files)


def _refs_for(path: str, db: sqlite3.Connection) -> List[str]:
    keys = ("/".join(path.split("/")[-2:]), os.path.basename(path))
    try:
        db.execute("CREATE TABLE IF NOT EXISTS git_pointers("
                   " key TEXT PRIMARY KEY, refs TEXT NOT NULL)")
        for key in keys:
            row = db.execute("SELECT refs FROM git_pointers WHERE key=?",
                             (key,)).fetchone()
            if row and row["refs"]:
                return [x for x in row["refs"].split(",") if x]
    except sqlite3.Error:
        pass
    return []


def _commit_meta(ref: str, db: sqlite3.Connection):
    rid, _, sha = ref.partition(":")
    try:
        row = db.execute("SELECT date, subject FROM git_commits WHERE rid=? AND sha=?",
                         (int(rid), sha)).fetchone()
        rp = db.execute("SELECT path FROM git_repos_tbl WHERE rid=?",
                        (int(rid),)).fetchone()
    except (sqlite3.Error, ValueError):
        return None
    if not row or not rp:
        return None
    return {"date": row["date"], "subject": row["subject"],
            "sha": sha, "repo_path": rp["path"]}


def why(path: str, db: sqlite3.Connection, limit: int = 3,
        body_chars: int = 900) -> List[dict]:
    """★Why★ this file looks the way it does — follow the pointers and fetch bodies from git then.

    What is stored is only (each commit once + a per-file sha reference); bodies always come
    from the original. So ①storage does not grow and ②nothing diverges after a rebase or an
    amend, because there is no copy to diverge.
    """
    import subprocess
    out: List[dict] = []
    for ref in _refs_for(path, db)[:limit]:
        meta = _commit_meta(ref, db)
        if not meta:
            continue
        body = ""
        try:
            p = subprocess.run(["git", "-C", meta["repo_path"], "show", "-s",
                                "--format=%b", meta["sha"]],
                               capture_output=True, text=True, timeout=15)
            if p.returncode == 0:
                body = " ".join(p.stdout.split())[:body_chars]
        except Exception:                                # noqa: BLE001
            pass
        out.append({"date": meta["date"],
                    "repo": os.path.basename(meta["repo_path"].rstrip("/")),
                    "sha": meta["sha"], "subject": meta["subject"], "body": body})
    return out


def history_of(path: str, db: Optional[sqlite3.Connection] = None) -> List[str]:
    """Recent commits for a file — ★read from one table (the normalised pointers) only★.

    ⛔ There used to be `git_history` (subjects only) alongside the pointers, ★holding the same
    thing twice★. The user's criterion (2026-08-24): *"GitHub already holds all of it … the
    brain has to be efficient about memory and storage."* Duplicate storage contradicts that head-on.
    """
    if db is None:
        return []
    out = []
    for ref in _refs_for(path, db):
        meta = _commit_meta(ref, db)
        if meta:
            out.append("%s %s" % (meta["date"], meta["subject"]))
    return out



def _index_one(db: sqlite3.Connection, doc: dict, prior: float = 1.0) -> int:
    fields = [
        (doc["name"].replace("_", " "), _W_NAME),
        (doc["title"], _W_TITLE),
        (doc["description"], _W_DESC),
        (doc["body"], _W_BODY),
    ]
    if _W_HISTORY > 0:
        hist = " ".join(history_of(doc["path"]))
        if hist:
            fields.append((hist, _W_HISTORY))
    tf = textindex.term_frequencies(tuple(fields))
    doclen = sum(tf.values()) or 1.0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cur = db.execute("SELECT id FROM docs WHERE path=?", (doc["path"],)).fetchone()
    if cur:
        doc_id = cur["id"]
        db.execute("DELETE FROM postings WHERE doc_id=?", (doc_id,))
        db.execute("DELETE FROM links WHERE src_id=?", (doc_id,))
        # ★Never touch weight★ — it is built by usage records, and letting a reindex
        # overwrite it sends self-improvement back to the start every time.
        db.execute(
            "UPDATE docs SET name=?,source=?,kind=?,title=?,description=?,body=?,"
            " mtime=?,size=?,sha=?,doclen=?,prior=?,evidence_date=?,indexed_at=? WHERE id=?",
            (doc["name"], doc["source"], doc["kind"], doc["title"], doc["description"],
             doc["body"], doc["mtime"], doc["size"], doc["sha"], doclen, prior,
             doc.get("evidence_date", ""), now, doc_id))
    else:
        cursor = db.execute(
            "INSERT INTO docs(name,path,source,kind,title,description,body,mtime,size,"
            " sha,doclen,prior,evidence_date,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc["name"], doc["path"], doc["source"], doc["kind"], doc["title"],
             doc["description"], doc["body"], doc["mtime"], doc["size"], doc["sha"],
             doclen, prior, doc.get("evidence_date", ""), now))
        doc_id = int(cursor.lastrowid)
    db.executemany("INSERT INTO postings(term,doc_id,tf) VALUES(?,?,?)",
                   [(t, doc_id, v) for t, v in tf.items()])
    db.executemany("INSERT OR IGNORE INTO links(src_id,dst_name) VALUES(?,?)",
                   [(doc_id, n) for n in doc["links"]])
    # Path links are resolved via `docs.path` after indexing finishes (by then everything is in)
    db.executemany("INSERT OR IGNORE INTO path_links(src_id,dst_path) VALUES(?,?)",
                   [(doc_id, p) for p in doc.get("link_paths", [])])
    db.execute("DELETE FROM path_links WHERE src_id=? AND dst_path NOT IN (%s)"
               % ",".join("?" * max(1, len(doc.get("link_paths", []) or [""]))),
               [doc_id] + (doc.get("link_paths") or [""]))
    db.execute("DELETE FROM aliases WHERE doc_id=?", (doc_id,))
    # A declaration belongs to ★the document that declared it★ — remove the link there and the declaration goes.
    db.execute("DELETE FROM alias_decl WHERE src_id=?", (doc_id,))
    db.executemany("INSERT OR IGNORE INTO alias_decl(alias,target,src_id) VALUES(?,?,?)",
                   [(a, t, doc_id) for a, t in doc.get("declared", [])])
    db.executemany("INSERT OR IGNORE INTO aliases(alias,doc_id) VALUES(?,?)",
                   [(a, doc_id) for a in doc.get("aliases", [])])
    # ★Declared trigger phrases★ — looked up independently of any score (§_triggers_of)
    db.execute("CREATE TABLE IF NOT EXISTS triggers("
               "phrase TEXT PRIMARY KEY, doc_id INTEGER NOT NULL)")
    db.execute("DELETE FROM triggers WHERE doc_id=?", (doc_id,))
    from brain import triggers as _trg
    db.executemany("INSERT OR REPLACE INTO triggers(phrase, doc_id) VALUES(?,?)",
                   [(_trg.norm(t), doc_id) for t in doc.get("triggers", [])])
    return doc_id


def _build_basename_aliases(db: sqlite3.Connection) -> None:
    """Register a filename as an alias ★only when it is globally unique★.

    People write `[[discussion-backlog]]` while the indexed name is
    `docs:domains/acme-visibility/discussion-backlog`. If the filename occurs exactly once in
    the corpus, which document is meant is **unambiguous**.

    ⛔ Why only when unique: there are 23 documents named `CLAUDE`, 7 named `AGENTS`, 16 named
    `README`. Wire such a name anywhere and **the graph starts lying quietly** — a link to the
    wrong place is **far worse than a broken one** (a broken link gives you nothing; a wrong one
    gives you a wrong answer). When in doubt, do not connect.
    """
    with db:
        db.execute(
            "INSERT OR IGNORE INTO aliases(alias, doc_id) "
            "SELECT b.base, b.id FROM ("
            "  SELECT id, "
            "    replace(replace(substr(path, length(rtrim(path, replace(path,'/',''))) + 1), "
            "            '.md',''), '.MD','') AS base "
            "  FROM docs) b "
            "JOIN (SELECT base, COUNT(*) c FROM ("
            "  SELECT replace(replace(substr(path, length(rtrim(path, replace(path,'/',''))) + 1), "
            "          '.md',''), '.MD','') AS base FROM docs) GROUP BY base HAVING c = 1"
            ") u ON u.base = b.base "
            "WHERE NOT EXISTS (SELECT 1 FROM docs d WHERE d.name = b.base)")


def _resolve_path_links(db: sqlite3.Connection) -> None:
    """Turn path links into name links. ★Only after indexing has finished★, so every target exists.
    If the path is outside the index, do nothing (that is not something we can fix)."""
    with db:
        db.execute(
            "INSERT OR IGNORE INTO links(src_id, dst_name) "
            "SELECT pl.src_id, d.name FROM path_links pl "
            "JOIN docs d ON d.path = pl.dst_path WHERE d.id != pl.src_id")


def _rebuild_terms(db: sqlite3.Connection) -> None:
    """df is recounted from the postings — ★never maintain a derived value by hand★.
    If incremental indexing updates df slightly wrong, the wrong IDF quietly ruins ranking."""
    with db:
        db.execute("DELETE FROM terms")
        db.execute("INSERT INTO terms(term, df) "
                   "SELECT term, COUNT(*) FROM postings GROUP BY term")


def _rebuild_skeletons(db: sqlite3.Connection) -> int:
    """Rebuild the consonant-skeleton table for Latin words — ★derived, so only at index time★.

    Only Latin words go in (what the transliteration bridge looks for is the original spelling).
    Measured 2026-08-20: of 97,859 words, 15,197 are Latin and 8,931 have df≥2 — the table is
    small enough that rebuilding it entirely is cheap.
    """
    rows = db.execute(
        "SELECT term, df FROM terms WHERE df >= 2 AND term GLOB '[a-z]*' "
        "AND term NOT GLOB '*[^a-z]*'").fetchall()
    from brain import translit
    vals = []
    for r in rows:
        k = translit.skeleton_key(r["term"])
        if len(k) >= 2:
            vals.append((k, r["term"], r["df"]))
    with db:
        db.execute("DELETE FROM skeletons")
        db.executemany("INSERT OR REPLACE INTO skeletons(skel,term,df) VALUES(?,?,?)",
                       vals)
    return len(vals)


def _declared_pairs(text: str) -> "List[tuple]":
    """`[label](target.md)` → (alias, target) pairs. ⛔ Only identifier-shaped labels count.

    The mirror form (the declared short name ★with the target's kind prefix attached★) is not made
    here — it needs the corpus's prefixes, so it is derived after indexing (§_build_kind_aliases).
    """
    out = []
    for label, rel in _MDLINK_LABELED.findall(text):
        if rel.startswith(("http://", "https://")):
            continue
        label = label.strip()
        target = os.path.splitext(os.path.basename(rel))[0]
        if not label or label == target or not _SLUGISH.match(label):
            continue
        out.append((label, target))
    return out


def learn_kind_prefixes(db: sqlite3.Connection) -> List[str]:
    """★Which filename prefixes are kinds, in this corpus★ — learned, never listed (2026-10-06).

    A prefix is `K_` (or `K-`) where ★some document of kind K is itself named `K_…`★: the corpus
    states its own convention. Measured on the author's notes it learns exactly the five that are
    used (`feedback_`·`lesson_`·`project_`·`reference_`, and `user_` that the fixed list missed).

    ⛔ It used to be a fixed list of the author's four. A corpus whose kinds are called anything
       else (`decision_`, `note-`, or Claude Code's own `user_`) got no prefix-free names at all,
       and one with no kinds still had the author's four stripped from its filenames.
    ⛔ Learned per ★kind★, then applied to ★every name★ — the author's notes hold 304 files whose
       prefix is not their own kind (`lesson_x` filed as `feedback`). Checking a file's prefix
       against only its own kind would have dropped all of their short names.
    Longest first, so a kind that extends another (`note_draft` over `note`) wins.
    """
    found = set()
    for r in db.execute("SELECT name, kind FROM docs WHERE kind != ''"):
        for sep in _KIND_SEPS:
            p = r["kind"] + sep
            if r["name"].startswith(p) and len(r["name"]) > len(p):
                found.add(p)
    return sorted(found, key=lambda p: (-len(p), p))


def _kind_prefix_of(name: str, prefixes: List[str]) -> str:
    for p in prefixes:
        if name.startswith(p):
            return p
    return ""


def _build_kind_aliases(db: sqlite3.Connection) -> int:
    """★The kind prefix is optional in a name★ — both directions, rebuilt whole on every index.

    ① `feedback_never_merge_your_own_pr` is also `never_merge_your_own_pr` — people drop the prefix when they
       link (18 measured links broke this way).
    ② a declared short name is also reachable ★with the target's own prefix★: the declaration is
       `[no_stale_quotes](feedback_never_quote_an_old_number.md)` and a memory writes
       `[[feedback_no_stale_quotes]]`. ⛔ Only the target's own prefix — multiplying by every prefix
       makes junk aliases that collide across targets.

    ⛔ Two documents stripping to the same name get ★neither★ — a link to the wrong place is worse
       than a broken one (the same rule as `_build_basename_aliases`). A name a document states for
       itself (`aliases`) is never overridden, and the view never lets an alias shadow a real name.
    ⛔ Rebuilt wholesale, not per document: the prefixes are a fact about the whole corpus, so when one
       is learned or forgotten every name changes with it — a per-document row would keep the old
       answer until that file happened to be re-read.
    """
    prefixes = learn_kind_prefixes(db)
    ids = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM docs")}
    claimed = {r["alias"]: r["doc_id"] for r in db.execute("SELECT alias, doc_id FROM aliases")}
    stripped: Dict[str, set] = {}
    for name, did in ids.items():
        p = _kind_prefix_of(name, prefixes)
        # a remainder of four characters or more — `lesson_ab` is not a name anyone links to as `ab`
        if p and len(name) > len(p) + 3:
            stripped.setdefault(name[len(p):], set()).add(did)
    declared = {(r["alias"], r["target"]) for r in db.execute("SELECT alias, target FROM alias_decl")}
    rows = set()
    for alias, dids in stripped.items():
        # `claimed` either way: by this document it is already reachable, by another it is not ours
        if len(dids) != 1 or alias in claimed:
            continue
        rows.add((alias, next(iter(dids))))
    for alias, target in declared:
        did = ids.get(target)
        p = _kind_prefix_of(target, prefixes)
        if (did is not None and p and not alias.startswith(p)
                and (p + alias, target) not in declared):
            rows.add((p + alias, did))
    with db:
        db.execute("DELETE FROM kind_aliases")
        db.executemany("INSERT OR IGNORE INTO kind_aliases(alias, doc_id) VALUES(?,?)",
                       sorted(rows))
    set_meta(db, "kind_prefixes", json.dumps(prefixes))
    return len(rows)


def _learn_index_aliases(db: sqlite3.Connection) -> int:
    """★Learn the aliases the index file (MEMORY.md) declares★ (2026-08-31).

    ⛔ The index file ★is not indexed as a document★ — it is loaded into context wholesale every
       session, so it is not a recall target. And yet ★that is exactly where a human names things
       short★: `[short_name](lesson_a_long_descriptive_name.md)`. So an ordinary index
       run put those declarations nowhere, and the `[[short_name]]` the memories use stayed
       ★a permanently broken link★ (measured: 2,632 aliases stored, and still not found).

    ⇒ Do not read it as a document, but ★do read its declarations★. src_id is 0 (not a document),
      so no document's reindex deletes them, and this function rewrites the whole set each time.
    """
    path = index_file()
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return 0
    pairs = [(a, t, 0) for a, t in _declared_pairs(text)]
    with db:
        db.execute("DELETE FROM alias_decl WHERE src_id=0")
        db.executemany("INSERT OR IGNORE INTO alias_decl(alias,target,src_id)"
                       " VALUES(?,?,?)", pairs)
    return len(pairs)


def reindex(db: sqlite3.Connection, cfg: Optional[dict] = None,
            full: bool = False, verbose: bool = False,
            recalibrate: bool = True,
            refresh_history: Optional[bool] = None) -> dict:
    """Incremental indexing. Change detection is (mtime, size, sha) — identical on all three, skip.

    `recalibrate=False` exists for the places that run **on every prompt, like the hook**.
    The reasoning is in the comment at the calibration call below (index 364ms vs calibrate 3,279ms).
    """
    cfg = cfg or load_config()
    # ★Was the index built by an older tokenizer★ — then unchanged files must be re-read as well, or
    # the documents keep their old terms while a query asks in the new ones (§textindex.TOKENIZER_VERSION).
    # ⛔ Not from the hook: a full pass measured ★22s★ on 1,393 documents (2026-09-08) against a 200ms
    #    budget. The hook passes `recalibrate=False`, so here it only reports; the stamp stays stale and
    #    `brain-session` announces it at the next session start. The first human-run index (`brain index`,
    #    the MCP `reindex`, `install`) rebuilds — once.
    tok_stale = tokenizer_stale(db)
    if tok_stale and recalibrate:
        full = True
    # ★Sources carry different weights★ — give a memory a human curated by hand (a ⛔ rule) the
    # same weight as an automatically accumulated document, and whichever side has more volume
    # always wins.
    # Measured: on one query, 5 domain documents pushed the relevant ⛔ memory past rank 8.
    priors = {e["name"]: float(e.get("prior", 1.0)) for e in cfg.get("sources", [])}
    t0 = time.time()
    seen_paths = set()
    added = updated = skipped = 0
    # ★`full` does not mean "empty the tables", it means "re-read every file"★
    # This used to do `DELETE FROM docs`. That renumbers rowids from 1 — and **usage and
    # aliases hold on to those rowids as keys**. One new file slipping in shifts every
    # document after it by one, and then
    #   · hard-earned usage records **land on the wrong document**, and
    #   · dead alias rows survive and quietly block `INSERT OR IGNORE`.
    # Measured (2026-08-11): 14 new memories cost **367 of 460 documents their prefix alias**
    # and brought broken links back from 0 to 31. Fixing the index effectively erased memories.
    # Now nothing is deleted — `_index_one` removes and reinserts its own rows per document, so
    # rowids survive, and only vanished files are cleaned up below.
    existing = {r["path"]: (r["id"], r["mtime"], r["size"], r["sha"])
                for r in db.execute("SELECT id,path,mtime,size,sha FROM docs")}
    # ★Index only one copy of identical content★ (measured 2026-08-19)
    #
    # Worktrees and copies were each indexed and the corpus jumped 1,176 → 1,369:
    #   acme-api 43 · -wt101 44 · -wt102 44 · acme-tier-monotonic 44  ← the same documents, 4×
    #
    # This repository's own ⛔ rule warns about exactly that accident — **widening the index
    # scope is changing the scale** (IDF depends on N). And copies add nothing to recall: the
    # content is identical, so whichever surfaces is the same text, and they only eat each
    # other's ranking.
    #
    # ⛔ Do not filter by name pattern (`-tp\d+`, `-copy`) — it goes stale immediately
    #    (measured: two of the copies matched no suffix rule, and had no `.git`, so the
    #    worktree test missed them too). ★A content sha★ is true regardless of the name.
    # The first one found wins (source order → the workspace's canonical copy comes first).
    seen_sha: Dict[str, str] = {}
    dups = 0
    with db:
        for path, source, root in iter_source_files(cfg):
            if path in seen_paths:
                # Two sources can overlap in scope (a guide source passing over docs).
                # First found wins — so a UNIQUE(path) conflict does not kill the whole index.
                continue
            prev = existing.get(path)
            if prev and not full:
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if abs(st.st_mtime - prev[1]) < 1e-6 and st.st_size == prev[2]:
                    # ⛔ Judge copies on the fast-skip path too — otherwise a copy that is
                    #    already indexed stays forever (the file is not read, so take the sha from the DB).
                    if prev[3] and prev[3] in seen_sha:
                        dups += 1
                        continue                 # not added to seen_paths → dropped by the cleanup below
                    seen_sha[prev[3]] = path
                    seen_paths.add(path)
                    skipped += 1
                    continue
            doc = read_doc(path, source, root)
            if doc is None:
                continue
            if doc["sha"] in seen_sha:
                dups += 1
                continue                         # a copy — not indexed, and any existing row is cleaned up
            seen_sha[doc["sha"]] = path
            seen_paths.add(path)
            # ⛔ Without `not full`, a full reindex **fixes nothing** — unchanged content is
            # skipped, so fixing the parser leaves aliases and links at their old values.
            if prev and not full and doc["sha"] == prev[3]:
                db.execute("UPDATE docs SET mtime=?,size=? WHERE id=?",
                           (doc["mtime"], doc["size"], prev[0]))
                skipped += 1
                continue
            _index_one(db, doc, priors.get(source, 1.0))
            if prev:
                updated += 1
            else:
                added += 1
            if verbose:
                print("  %s %s" % ("~" if prev else "+", doc["name"]))
        # A vanished file vanishes from the index too (so no orphaned evidence remains).
        gone = [(p, pid) for p, (pid, _, _, _) in existing.items() if p not in seen_paths]
        removed = [pid for _, pid in gone]
        for path, doc_id in gone:
            db.execute("DELETE FROM postings WHERE doc_id=?", (doc_id,))
            db.execute("DELETE FROM links WHERE src_id=?", (doc_id,))
            # ★Delete what hangs off it as well★ — leave it, and the moment the next document
            # inherits that rowid it wears someone else's aliases and someone else's usage history.
            db.execute("DELETE FROM aliases WHERE doc_id=?", (doc_id,))
            db.execute("DELETE FROM path_links WHERE src_id=?", (doc_id,))
            db.execute("DELETE FROM usage WHERE path=?", (path,))
            db.execute("DELETE FROM docs WHERE id=?", (doc_id,))
    _build_basename_aliases(db)
    _resolve_path_links(db)
    _rebuild_terms(db)
    # ★Stamp which tokenizer read the documents★ — only when every one of them was read by this one
    # (a full pass, or an index that was empty before this run). A partial pass keeps the old stamp,
    # so the next human-run index still rebuilds.
    if full or not existing:
        set_meta(db, "tokenizer", str(textindex.TOKENIZER_VERSION))
    stats = {"added": added, "updated": updated, "skipped": skipped,
             "removed": len(removed), "duplicates": dups,
             "seconds": round(time.time() - t0, 2)}
    # ★Derived tables are built after stats exists★ — put them earlier and `stats` is not yet
    # defined, so a NameError is raised, and the broad except above swallows it so that
    # ★only the report disappears, silently★ (this actually happened once on 2026-08-20:
    # the tables were built but never showed up in the statistics).
    try:
        stats["skeletons"] = _rebuild_skeletons(db)
    except Exception:                                    # noqa: BLE001
        pass                                             # a derived-table failure must not block indexing
    try:
        stats["declared_aliases"] = _learn_index_aliases(db)
    except Exception:                                    # noqa: BLE001
        pass                                             # an alias-learning failure must not block indexing
    # After the declarations — the mirror form is built from them, the index file's included.
    try:
        stats["kind_aliases"] = _build_kind_aliases(db)
    except Exception:                                    # noqa: BLE001
        pass                                             # an alias-learning failure must not block indexing
    set_meta(db, "last_index", time.strftime("%Y-%m-%dT%H:%M:%S"))
    set_meta(db, "last_index_stats", json.dumps(stats))
    # ★When the corpus changes, so does the scale★ — re-measure the threshold after indexing.
    # A failure here must not kill the index (the threshold can be re-measured in the hook).
    #
    # ⛔ ★Calibration is 90% of indexing★ (measured 2026-08-12: index 364ms, calibrate 3,279ms)
    #   Calibration runs 138 recalls: 18 noise controls + 120 real prompts.
    #   So the hook, which runs **on every prompt**, calls this with it turned off
    #   (`recalibrate=False`) — otherwise one index takes 3.6 seconds, the hook gives up on its
    #   time budget, and the memory you just fixed is not found for that prompt.
    #   The scale is re-measured at session start (`bin/brain-session`) and on human-run indexes.
    if recalibrate:
        try:
            from brain import calibrate
            stats["hook_threshold"] = calibrate.calibrate(db)["threshold"]
        except Exception:                                # noqa: BLE001
            pass

    # ★The git history mapping is built here too★ — reading happens from the tables
    # (§history_of), building only at index time. The default follows `recalibrate` because that
    # parameter already separates "hook" from "human" (the hook passes False).
    # ⛔ Building it on the hook path would cost 500ms on every prompt.
    if refresh_history is None:
        refresh_history = recalibrate
    if refresh_history:
        # ★The path to the "why" behind a change★ (2026-08-24) — pointers only, no bodies (§_git_pointers).
        try:
            stats["pointer_keys"] = save_git_pointers(db)
        except Exception:                                # noqa: BLE001
            pass

    # ★Re-measure the semantic-search gate — from cached controls only, at zero cost★ (2026-08-24)
    # The ratio threshold is sensitive to corpus size (as the pool went 215→294, the yield went
    # 13→8). So it is re-measured on every index, exactly like the BM25 threshold. If the
    # conditions are not met (no cache, ingest under 90%) it is skipped quietly.
    # It follows `recalibrate` for the same reason: not on the hook path.
    if recalibrate:
        try:
            from brain import vectors as _vec
            got = _vec.recalibrate_if_free(db)
            if got:
                stats["vec_min_ratio"] = got["min_ratio"]
        except Exception:                                # noqa: BLE001
            pass                                         # a failure here must not block indexing

    # ★Fill in declared triggers missing from the table★ — the hole left when old code indexed (§heal_triggers)
    try:
        n_healed = heal_triggers(db, cfg)
        if n_healed:
            stats["triggers_healed"] = n_healed
    except Exception:                                    # noqa: BLE001
        pass                                             # a healing failure must not block indexing

    if tok_stale:
        if full:
            stats["tokenizer_rebuilt"] = textindex.TOKENIZER_VERSION
        else:
            stats["tokenizer_stale"] = True          # the hook path — announced at session start instead

    # ★Record who indexed★ — so it can be diagnosed whether old code was the last writer
    try:
        from brain import calibrate as _cal
        set_meta(db, "index_code", str(_cal.code_stamp()))
    except Exception:                                    # noqa: BLE001
        pass
    return stats


def heal_triggers(db: sqlite3.Connection, cfg: Optional[dict] = None) -> int:
    """★If a trigger a file declares is missing from the table, reindex just that document★ (self-healing).

    ⛔ Why this is needed (measured 2026-08-19): a long-running MCP server holding **old code**
    has a `reindex` that does not know the `triggers` table at all (incremental and full alike).
    The document is indexed, there is no error, and only the table stays empty — so typing that
    phrase brings back nothing. Catching it in a test alone would still leave the next session
    stuck, so ★every index pass heals it by itself★.
    The fast-skip path (same mtime and size) cannot fix it — that path never reads the file.
    """
    from brain import triggers as _trg
    in_files = _trg.declared_in_files()
    if not in_files:
        return 0
    try:
        in_table = {r["phrase"] for r in db.execute("SELECT phrase FROM triggers")}
    except sqlite3.Error:
        in_table = set()
    missing = {nm for ph, nm in in_files.items() if ph not in in_table}
    if not missing:
        return 0
    base = memory_dir()
    if not base:
        return 0
    healed = 0
    cfg = cfg or load_config()
    priors = {e["name"]: float(e.get("prior", 1.0)) for e in cfg.get("sources", [])}
    with db:
        for name in sorted(missing):
            path = os.path.join(base, name + ".md")
            doc = read_doc(path, "memory", base)
            if doc is None:
                continue
            _index_one(db, doc, priors.get("memory", 1.0))
            healed += 1
    return healed


def needs_reindex(db: sqlite3.Connection, cfg: Optional[dict] = None) -> bool:
    """★Is any file newer than the index★ — a cheap check, a handful of stats.

    Without it there is a silent hole: when the agent's own memory tool, or a person, creates a
    file **directly**, the brain does not know about it. Confirmed by measurement (2026-08-10) —
    a new file was created, recall returned **only unrelated documents at 2.66** (noise), and the
    hook surfaced nothing. Not an error but an **empty hand**, so nobody notices.

    ⛔ ★It used to look only at directory mtime, and therefore **missed edits entirely**★ (2026-08-12)
    A directory's mtime changes only when a file is **added or removed**. Editing content does not
    change it. The comment at the time claimed *"edits are caught by the full incremental index at
    session start"* and called that sufficient — **it was not.** Demonstrated:

        edit a memory file       → needs_reindex = False
        recall the edited text   → ★the wrong document★ (the old index answers)
        after an explicit reindex → found exactly

    So even when the user says *"this memory is wrong, fix it"* and it is fixed, **the brain
    recalls the old content for the rest of that session.** An edit made to remove stale
    information becomes the place that answers with stale information — until the next session.

    So **every file's mtime is checked**. It looks expensive but measures **16–20ms** (951 files),
    inside the hook's 200ms budget. It goes through `iter_source_files`, so max_depth and the
    exclusion rules are respected (globbing `**/*.md` walks into node_modules and takes
    ★21 seconds★ — that was actually measured once, and gave the wrong answer).
    """
    last = get_meta(db, "last_index")
    if not last:
        return True
    try:
        last_ts = time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return True
    cfg = cfg or load_config()
    for path, _source, _root in iter_source_files(cfg):
        try:
            if os.stat(path).st_mtime > last_ts:
                return True
        except OSError:
            continue
    # A **vanished** file cannot be caught by mtime — the directory's mtime changes then.
    for entry in cfg.get("sources", []):
        try:
            if os.stat(os.path.expanduser(entry["path"])).st_mtime > last_ts:
                return True
        except OSError:
            continue
    return False


def corpus_stats(db: sqlite3.Connection) -> dict:
    row = db.execute("SELECT COUNT(*) n, AVG(doclen) avg FROM docs").fetchone()
    return {
        "docs": row["n"] or 0,
        "avg_doclen": row["avg"] or 1.0,
        "terms": db.execute("SELECT COUNT(*) c FROM terms").fetchone()["c"],
        "postings": db.execute("SELECT COUNT(*) c FROM postings").fetchone()["c"],
        "links": db.execute("SELECT COUNT(*) c FROM links").fetchone()["c"],
        "last_index": get_meta(db, "last_index"),
    }
