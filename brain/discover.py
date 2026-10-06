"""★Finds what to remember★ — the first gate of an installed brain.

## Why this is the hardest problem

User's instruction (2026-08-24): *"the biggest issue in the end is where existing important
information already lives — that differs per person, per environment… it needs to detect that
or let it be configured. So it should detect, and let `add` bring in more documents or records."*

★What this brain can do first depends on what it indexed★. A half corpus makes even the best
engine half as good. But memory lives in different places for different people — Claude's
automatic memory, an Obsidian vault, per-project `docs/`, `CLAUDE.md` rules, a personal notes folder…

⛔ **The most dangerous failure is a silent one** — writing a path that does not exist produces not
   an error but ★an empty index★, and nobody knows. So this module proposes only ★with evidence★ (file count · size · latest timestamp).

## Why this did not stay inside install.sh

The detection logic lived inside `install.sh`'s heredoc. Then it could ①run only once (at install
time) ②not be called from the CLI ③eventually grow a second version on the CLI side and drift. This
repository has been burnt in exactly this spot before (a judge transcribing a product's own rules and quietly drifting).
⇒ ★One module is canonical★, and install.sh and `brain detect` call the same functions.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Sequence

from . import i18n

HOME = os.path.expanduser("~")

# Places never walked — big, memory-free, and slow to walk
SKIP_DIRS = {
    "node_modules", "vendor", "dist", "build", "target", "__pycache__",
    ".git", ".venv", "venv", "env", ".next", ".nuxt", ".cache", "Pods",
    "site-packages", "DerivedData", "Caches", "CacheStorage", ".terraform",
    ".pytest_cache", ".mypy_cache", "coverage", "tmp", "logs",
}
TEXT_EXT = (".md", ".markdown", ".txt", ".mdx")

# Candidate roots — walking starts here. Absent, it is skipped quietly.
def candidate_roots() -> List[str]:
    names = ["work", "development", "dev", "projects", "Projects", "src", "repos",
             "code", "Documents", "notes", "Notes", "Dropbox", "obsidian",
             "Obsidian", "wiki", "Wiki"]
    out = [os.path.join(HOME, n) for n in names]
    # iCloud Obsidian (the most common spot on a Mac)
    out.append(os.path.join(HOME, "Library", "Mobile Documents",
                            "iCloud~md~obsidian", "Documents"))
    out.append(HOME)                      # last — only looked at shallowly
    # ⛔ ★The same folder gets caught twice★ (clean-room measured 2026-08-25) — the Mac filesystem is
    #    case-insensitive, so `~/notes` and `~/Notes` are the same directory yet both became candidates.
    #    The path strings differ, so even realpath misses it → judge ★by inode★ instead.
    seen, uniq = set(), []
    for p in out:
        if not os.path.isdir(p):
            continue
        try:
            key = os.stat(p).st_ino
        except OSError:
            key = os.path.realpath(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def is_worktree_copy(path: str) -> bool:
    """Is it a git ★worktree copy★ — a copy of the same repository, so never proposed.

    ⛔ Indexing already excludes this (store._is_worktree), but detection did not know and proposed 8
    (acme-web-content2/docs · -discover/docs · -geo-spec/docs …). Registering them bloats the corpus
    with near-identical documents and a copy crowds out the top ranks. ★The verdict uses the same function as the product.★
    """
    from brain import store
    p = os.path.abspath(path)
    for _ in range(6):
        if os.path.exists(os.path.join(p, ".git")):
            try:
                return store._is_worktree(p)
            except Exception:                            # noqa: BLE001
                return os.path.isfile(os.path.join(p, ".git"))
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return False


_CODE_MARKERS = ("package.json", "composer.json", "pyproject.toml", "go.mod",
                 "Cargo.toml", "pom.xml", "Gemfile", "requirements.txt")


def looks_like_notes(dirpath: str, filenames: Sequence[str]) -> bool:
    """★The shape condition for a notes folder★ — markdown is the majority, no code-repo marker.

    ⛔ The first version only looked at "8+ markdown files", so a code repository got caught as notes
    (acme-api: 11 README/CHANGELOG files). A notes folder is ★mostly writing★.
    """
    if any(m in filenames for m in _CODE_MARKERS):
        return False
    visible = [f for f in filenames if not f.startswith(".")]
    if not visible:
        return False
    md = [f for f in visible if f.endswith(TEXT_EXT)]
    return len(md) >= 8 and len(md) / len(visible) >= 0.5


class Candidate:
    """One proposal — ★it carries its own evidence★ (a proposal with no numbers cannot be trusted)."""

    def __init__(self, name: str, path: str, why: str, prior: float,
                 include: Sequence[str] = ("*.md",),
                 exclude: Sequence[str] = (), max_depth: int = 0):
        self.name, self.path, self.why, self.prior = name, path, why, prior
        self.include, self.exclude, self.max_depth = list(include), list(exclude), max_depth
        self.files = 0
        self.bytes = 0
        self.newest = 0.0

    @property
    def tilde(self) -> str:
        return self.path.replace(HOME, "~", 1)

    def as_source(self) -> dict:
        src = {"name": self.name, "path": self.tilde, "include": self.include,
               "prior": self.prior, "note": self.why}
        if self.exclude:
            src["exclude"] = self.exclude
        if self.max_depth:
            src["max_depth"] = self.max_depth
        return src

    def __repr__(self) -> str:
        return "<%s %s files=%d>" % (self.name, self.tilde, self.files)


def _count(path: str, include: Sequence[str], max_depth: int = 0,
           budget_sec: float = 3.0) -> Dict[str, float]:
    """Counts ★how many this source actually catches★. Past the budget it returns only what was measured so far."""
    import fnmatch
    n, size, newest = 0, 0, 0.0
    base = path.rstrip("/").count(os.sep)
    t0 = time.time()
    truncated = False
    for dirpath, dirnames, filenames in os.walk(path):
        if time.time() - t0 > budget_sec:
            truncated = True
            break
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in SKIP_DIRS]
        if max_depth and dirpath.count(os.sep) - base >= max_depth:
            dirnames[:] = []
        for f in filenames:
            if not any(fnmatch.fnmatch(f, pat) for pat in include):
                continue
            n += 1
            try:
                st = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            size += st.st_size
            newest = max(newest, st.st_mtime)
    return {"files": n, "bytes": size, "newest": newest, "truncated": truncated}


def _measure(c: Candidate) -> Candidate:
    got = _count(c.path, c.include, c.max_depth)
    c.files, c.bytes, c.newest = int(got["files"]), int(got["bytes"]), got["newest"]
    return c


# ── detectors ────────────────────────────────────────────────────────────────
# Each detector is ★a shape condition★ — "this shape means memory". Not a name list —
# a list always goes stale (a lesson this repository keeps repeating).

def find_host_memory() -> List[Candidate]:
    """Each detected host's own memory folders (§hosts.Host.memory_dirs) — Claude Code keeps one per
    project, Codex one in its home. ⛔ It used to know only Claude Code's folder."""
    from brain import hosts
    out = []
    for h in hosts.detected():
        for d in h.memory_dirs():
            out.append(Candidate("memory", d, i18n.t("discover.why.memory"),
                                 1.45, max_depth=1))
    return out


def find_obsidian() -> List[Candidate]:
    """An Obsidian vault — ★wherever a `.obsidian/` folder exists★ (found by shape, not by name)."""
    out, seen = [], set()
    for root in candidate_roots():
        depth = 1 if root == HOME else 4
        base = root.rstrip("/").count(os.sep)
        for dirpath, dirnames, _ in os.walk(root):
            if dirpath.count(os.sep) - base >= depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                           and (not d.startswith(".") or d == ".obsidian")]
            if ".obsidian" in dirnames and dirpath not in seen:
                seen.add(dirpath)
                out.append(Candidate("wiki", dirpath, i18n.t("discover.why.wiki"), 0.85))
                dirnames[:] = []          # never dig further inside a vault
    return out


def find_docs_dirs() -> List[Candidate]:
    """A `docs/`-style document directory — a common spot under a project root."""
    out, seen = [], set()
    for root in candidate_roots():
        if root == HOME:
            continue
        for name in sorted(os.listdir(root))[:200]:
            proj = os.path.join(root, name)
            if not os.path.isdir(proj) or name.startswith(".") or name in SKIP_DIRS:
                continue
            for docname in ("docs", "doc", "documentation"):
                d = os.path.join(proj, docname)
                if os.path.isdir(d) and d not in seen:
                    seen.add(d)
                    out.append(Candidate("docs", d, i18n.t("discover.why.docs"), 1.0,
                                         exclude=["/node_modules/"]))
    return out


def find_rule_roots() -> List[Candidate]:
    """A working root where the hosts' rule files cluster (§hosts.rule_files) — ⛔ let the prohibitions
    be asked for. ⛔ The names used to be written here (`CLAUDE.md`·`AGENTS.md`)."""
    from brain import hosts
    names = hosts.rule_files()
    out = []
    for root in candidate_roots():
        if root == HOME:
            continue
        hits = 0
        for name in sorted(os.listdir(root))[:300]:
            p = os.path.join(root, name)
            if not os.path.isdir(p):
                continue
            for f in names:
                if os.path.isfile(os.path.join(p, f)):
                    hits += 1
                    break
        # ⛔ The old version only accepted a root with ★2+ CLAUDE.md files★. A new user commonly has
        #    only one project, and then the ⛔ prohibitions drop out of the index entirely
        #    (clean-room measured: rules and docs both missing, recall came back 0).
        if hits >= 1:
            out.append(Candidate("guide", root, i18n.t("discover.why.guide"),
                                 1.25, include=list(names),
                                 exclude=["/node_modules/", "/vendor/"], max_depth=2))
    return out


def find_project_docs() -> List[Candidate]:
    """Documents ★inside★ a project — design, handoff, README. Absent, it cannot find "past work"."""
    out = []
    for root in candidate_roots():
        if root == HOME:
            continue
        n_proj = sum(1 for x in sorted(os.listdir(root))[:300]
                     if os.path.isdir(os.path.join(root, x)) and not x.startswith("."))
        if n_proj >= 2:                       # a new user has only a couple of projects
            out.append(Candidate("project", root, i18n.t("discover.why.project"), 1.0,
                                 exclude=["/node_modules/", "/vendor/", "/dist/"],
                                 max_depth=2))
    return out


def find_note_dirs(min_files: int = 8) -> List[Candidate]:
    """A flat notes folder — many markdown files piled in one folder."""
    out = []
    for root in candidate_roots():
        depth = 2 if root == HOME else 2
        base = root.rstrip("/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base >= depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d not in SKIP_DIRS]
            n = sum(1 for f in filenames if f.endswith(TEXT_EXT))
            if (n >= min_files and os.path.basename(dirpath) not in ("docs", "doc")
                    and looks_like_notes(dirpath, filenames)):
                out.append(Candidate("notes", dirpath,
                                     i18n.t("discover.why.notes", n=n), 0.9,
                                     include=["*.md", "*.txt"], max_depth=1))
                dirnames[:] = []
    return out


DETECTORS = (find_host_memory, find_obsidian, find_docs_dirs,
             find_rule_roots, find_project_docs, find_note_dirs)


# ★Each category has its own threshold★ — the noise risk differs. A notes folder only looks like one
# when there are many (few and any folder qualifies), while ⛔ rules and personal memory carry weight
# ★even at one hit★. A clean-room run missing a small corpus whole is exactly this distinction, absent.
MIN_FILES = {"memory": 1, "guide": 1, "docs": 2, "wiki": 3, "project": 2, "notes": 8}


def detect(min_files: int = 3, budget_sec: float = 25.0) -> List[Candidate]:
    """Runs every detector and returns candidates ★with evidence★ (largest first).

    ⛔ An empty candidate is discarded — writing an absent path or empty folder into config produces a silent empty index.
    """
    t0 = time.time()
    cands: List[Candidate] = []
    for fn in DETECTORS:
        if time.time() - t0 > budget_sec:
            break
        try:
            cands.extend(fn())
        except OSError:
            continue
    out = []
    for c in cands:
        if time.time() - t0 > budget_sec * 1.5:
            break
        if is_worktree_copy(c.path):
            continue                      # a copy of the same repository — bloats the corpus
        _measure(c)
        if c.files >= MIN_FILES.get(c.name, min_files):
            out.append(c)
    # If the same place appears more than once, keep only the heavier one (by prior).
    # ⛔ The key is ★realpath★ — a symlink catches the same folder under two names (measured:
    #    a host's memory folder and a symlink to it inside a workspace were the same 602 files).
    #    Comparing path strings would register the same corpus as two sources and let a duplicate crowd out the top.
    best: Dict[str, Candidate] = {}
    for c in out:
        key = os.path.realpath(c.path)
        if key not in best or c.prior > best[key].prior:
            best[key] = c
    return sorted(best.values(), key=lambda c: (-c.files, -c.prior))


def already_configured(cfg: Optional[dict]) -> Dict[str, str]:
    """Path already in config → its name. Avoids proposing a duplicate."""
    out: Dict[str, str] = {}
    for s in ((cfg or {}).get("sources") or []):
        p = os.path.expanduser(s.get("path", ""))
        if p:
            out[os.path.normpath(p)] = s.get("name", "?")
    return out


def covered_by(path: str, configured: Dict[str, str]) -> Optional[str]:
    """Does an already-registered source ★already contain★ this path — then it is never proposed again.

    ⛔ The first version only treated an exact path match as a duplicate. So with a workspace root
       already registered, a dozen of its own subfolders came back as new candidates.
    """
    p = os.path.realpath(path)
    for root, name in configured.items():
        r = os.path.realpath(root)
        if p == r or p.startswith(r.rstrip("/") + os.sep):
            return name
    return None


def render(cands: Sequence[Candidate], configured: Dict[str, str]) -> str:
    lines = ["%-8s %-46s %7s %9s %-10s %s"
             % (i18n.t("discover.col.name"), i18n.t("discover.col.path"),
                i18n.t("discover.col.files"), i18n.t("discover.col.size"),
                i18n.t("discover.col.recent"), i18n.t("discover.col.why"))]
    lines.append("-" * 108)
    for c in cands:
        got = covered_by(c.path, configured)
        mark = ("  " + i18n.t("discover.covered_by", name=got)) if got else ""
        age = "-"
        if c.newest:
            days = (time.time() - c.newest) / 86400.0
            age = i18n.t("discover.age_days", days=int(days)) if days >= 1 else i18n.t("discover.age_today")
        lines.append("%-8s %-46s %7d %8.1fMB %-10s %s%s"
                     % (c.name, c.tilde[-46:], c.files, c.bytes / 1e6, age,
                        c.why[:34], mark))
    return "\n".join(lines)
