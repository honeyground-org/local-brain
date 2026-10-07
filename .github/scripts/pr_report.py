#!/usr/bin/env python3
"""The pull-request report — what a change touches, how much review it needs, and whether it is ready.

One script, five modes, so a rule lives in exactly one place:

    --check      (CI, `pull_request`)          the hard rules; exit 1 when one is broken
    --comment    (CI, `pull_request_target`)   one sticky comment, labels, review requests — never fails
    --gate       (CI, reviews)                 enough of the right approvals for the change's impact?
    --codeowners [--write]                     CODEOWNERS, generated from .github/governance.json
    --local [--base origin/main] [--title T] [--body FILE]
                                               the same report on your machine, before you push

⛔ In `--comment` mode this runs with a token that can write to the repository, on a pull request from
anyone. It therefore reads the pull request ★only as data★ — git objects, the event payload, the API —
and never imports, runs or evaluates a file from it. `RULER_MODULES` is read with `ast.literal_eval`.

Hard rules (`--check` fails):
  · the title reads `area: what changed`
  · the description fills the sections the template asks for (which ones depends on the impact)
  · every commit carries a `Signed-off-by:` matching its author (DCO); merge commits are exempt
  · no new import outside the standard library and this repository

Shown, not enforced here (another check owns the rule):
  · measurement code touched → `tests/verify_code_generation.py --base` decides whether a bump is due
  · code changed without a test change → a warning; reviewers decide

Standard library only; Python 3.8+ (the standard-library name list needs 3.10+, as CI runs).
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOVERNANCE = os.path.join(ROOT, ".github", "governance.json")
CODEOWNERS = os.path.join(ROOT, ".github", "CODEOWNERS")
MARKER = "<!-- pr-report -->"
LEVELS = ("low", "medium", "high")
RATCHET = "tests/english_only_ratchet.txt"

TITLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*(?:, ?[A-Za-z0-9][A-Za-z0-9_./-]*)*: \S.{2,}$")
SECTIONS = ("Purpose", "Effect", "Scalability and extensibility", "Risks and rollback", "Checks")
REQUIRED = {"low": ("Purpose", "Checks"),
            "medium": ("Purpose", "Effect", "Risks and rollback", "Checks"),
            "high": SECTIONS}
SIGNOFF = re.compile(r"^Signed-off-by:\s*(.+?)\s*<([^<>\s]+)>\s*$", re.M)


# ─── the roster ──────────────────────────────────────────────────────────────

def load_governance(path: str = GOVERNANCE) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def match(path: str, pattern: str) -> bool:
    """A roster path is a file (`brain/x.py`) or a folder (`docs/`). No globs, so CODEOWNERS agrees."""
    return path.startswith(pattern) if pattern.endswith("/") else path == pattern


def area_of(path: str, gov: dict) -> str:
    """The area whose ★longest★ path matches — the same answer CODEOWNERS' last-match gives (see codeowners())."""
    best, best_len = "other", -1
    for name, area in gov["areas"].items():
        for p in area["paths"]:
            if match(path, p) and len(p) > best_len:
                best, best_len = name, len(p)
    return best


def owners_of(area: str, gov: dict) -> list:
    """Area owners first, then the maintainers — a maintainer can always approve as a code owner."""
    seen, out = set(), []
    for who in list(gov["areas"].get(area, {}).get("owners", [])) + list(gov["maintainers"]):
        if who.lower() not in seen:
            seen.add(who.lower())
            out.append(who)
    return out


def roster(gov: dict) -> list:
    out = list(gov["maintainers"])
    for area in gov["areas"].values():
        out += [w for w in area.get("owners", []) if w.lower() not in {o.lower() for o in out}]
    return out


def codeowners(gov: dict) -> str:
    """CODEOWNERS text. GitHub takes the ★last★ matching line, so lines go from short paths to long ones —
    then the last match is the longest match, which is what `area_of` picks."""
    lines = ["# Generated from .github/governance.json — edit that file, then run",
             "#   python3 .github/scripts/pr_report.py --codeowners --write",
             "# tests/verify_pr_report.py fails when this file and the roster disagree.",
             "",
             "* " + " ".join("@" + m for m in gov["maintainers"])]
    rows = []
    for name, area in gov["areas"].items():
        for p in area["paths"]:
            rows.append((len(p), p, name))
    for _n, p, name in sorted(rows):
        lines.append("/%s %s" % (p, " ".join("@" + w for w in owners_of(name, gov))))
    return "\n".join(lines) + "\n"


def parse_codeowners(text: str) -> list:
    """[(pattern, [owners])] in file order — enough of the format to read back what `codeowners()` writes."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        out.append((parts[0], [w.lstrip("@") for w in parts[1:]]))
    return out


def codeowners_for(path: str, rules: list) -> list:
    """GitHub's rule: the last matching line wins."""
    got = []
    for pat, owners in rules:
        if pat == "*":
            got = owners
        elif match(path, pat.lstrip("/")):
            got = owners
    return got


# ─── impact ──────────────────────────────────────────────────────────────────

def ruler_modules(calibrate_source: str) -> set:
    """`RULER_MODULES` read as data (never imported — the source may come from a pull request)."""
    try:
        tree = ast.parse(calibrate_source or "")
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "RULER_MODULES" for t in node.targets):
            try:
                return {str(x) for x in ast.literal_eval(node.value)}
            except ValueError:
                return set()
    return set()


def generation(calibrate_source: str):
    m = re.search(r"^CODE_GENERATION\s*=\s*(\d+)", calibrate_source or "", re.M)
    return int(m.group(1)) if m else None


def ratchet_value(text: str):
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.isdigit():
            return int(line)
    return None


def classify(changes: list, gov: dict, rulers: set, ratchet=(None, None)) -> dict:
    """changes = [(status, path)] with status A/M/D/R… · → impact, the reasons for it, and the areas.

    Impact is the highest of: each file's area, a measurement (ruler) module, a check removed, the
    English ratchet raised. The reasons are kept so the report can say ★why★ — "high" alone is noise.
    """
    files, reasons = [], []
    level = 0
    for status, path in changes:
        area = area_of(path, gov)
        imp = gov["areas"].get(area, {}).get("impact", "medium")
        why = ""
        mod = path[len("brain/"):-3] if path.startswith("brain/") and path.endswith(".py") else ""
        if mod and mod in rulers:
            imp, why = "high", "measurement code (`RULER_MODULES`) — it decides the threshold"
        if status.startswith("D") and re.match(r"^tests/verify_[^/]+\.py$", path):
            imp, why = "high", "a check is removed"
        if path == RATCHET and None not in ratchet and ratchet[1] > ratchet[0]:
            imp, why = "high", "the English-only ratchet goes up (%s → %s)" % ratchet
        if not why and imp == "high":
            why = "%s — %s" % (area, gov["areas"][area].get("summary", ""))
        files.append({"path": path, "status": status, "area": area, "impact": imp})
        if why and imp == "high":
            reasons.append((path, why))
        level = max(level, LEVELS.index(imp))
    areas = sorted({f["area"] for f in files})
    return {"impact": LEVELS[level] if files else "low", "reasons": reasons, "areas": areas, "files": files}


# ─── the description ─────────────────────────────────────────────────────────

def parse_sections(body: str) -> dict:
    """`## Heading` → its text, with HTML comments (the template's hints) and unticked boxes' text kept."""
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^#{2,3}\s+(.+?)\s*#*\s*$", line)
        if m:
            cur = m.group(1).strip()
            out[cur] = ""
        elif cur is not None:
            out[cur] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


def section_problems(body: str, impact: str) -> list:
    got = {k.lower(): v for k, v in parse_sections(body).items()}
    out = []
    for name in REQUIRED[impact]:
        text = got.get(name.lower())
        if text is None:
            out.append("missing section `%s`" % name)
        elif not re.sub(r"[\s\-*_>`]|\[[ xX]\]", "", text):
            out.append("`%s` is empty" % name)
    return out


def title_problem(title: str) -> str:
    if TITLE.match((title or "").strip()):
        return ""
    return "the title should read `area: what changed` (for example `stores: retry a lost connection once`)"


# ─── sign-off (DCO) ──────────────────────────────────────────────────────────

def dco_problems(commits: list) -> list:
    """commits = [{sha, parents, name, email, message}] · a commit needs a sign-off ★by its author★."""
    out = []
    for c in commits:
        if len(c.get("parents", [])) > 1:
            continue                                   # a merge, e.g. GitHub's "Update branch"
        offs = [(n, e.lower()) for n, e in SIGNOFF.findall(c.get("message", ""))]
        if not offs:
            out.append("%s has no `Signed-off-by:` line (commit with `git commit -s`)" % c["sha"][:8])
        elif c.get("email", "").lower() not in {e for _n, e in offs}:
            out.append("%s is signed off by someone other than its author" % c["sha"][:8])
    return out


# ─── imports ─────────────────────────────────────────────────────────────────

def top_imports(source: str) -> set:
    """Absolute top-level module names a Python source imports · None when it does not parse."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return None
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.add(node.module.split(".")[0])
    return out


def stdlib_names():
    return set(getattr(sys, "stdlib_module_names", ())) or None


def import_problems(pairs: list, local: set, stdlib=None) -> list:
    """pairs = [(path, base_source or '', head_source)] · new imports that are neither stdlib nor ours."""
    stdlib = stdlib if stdlib is not None else stdlib_names()
    if stdlib is None:
        return []
    out = []
    for path, base_src, head_src in pairs:
        head = top_imports(head_src)
        if head is None:
            out.append("`%s` does not parse" % path)
            continue
        new = head - (top_imports(base_src) or set())
        bad = sorted(m for m in new if m not in stdlib and m not in local and m != "__future__")
        if bad:
            out.append("`%s` imports %s — brain uses the standard library only"
                       % (path, ", ".join("`%s`" % b for b in bad)))
    return out


def local_modules(tracked: list) -> set:
    """Names importable from this repository: top-level folders and every module file's stem."""
    out = set()
    for p in tracked:
        parts = p.split("/")
        if len(parts) > 1:
            out.add(parts[0])
        if p.endswith(".py"):
            out.add(parts[-1][:-3])
    return out


# ─── reviews ─────────────────────────────────────────────────────────────────

def latest_states(reviews: list) -> dict:
    """login → the state of their latest review that counts. A comment does not undo an approval."""
    out = {}
    for r in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        state = r.get("state", "")
        login = ((r.get("user") or {}).get("login") or "").lower()
        if login and state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            out[login] = state
    return out


def gate(impact: str, reviews: list, gov: dict, author: str) -> dict:
    """Enough approvals from the roster for this impact? → {ok, need, got, lines}.

    The branch ruleset already requires one approval and the code owners for every change. This adds the
    rule it cannot express: ★high impact needs more people★, at least one of them a maintainer. When the
    roster is too small to supply them, it says so instead of quietly asking for less.
    """
    rule = gov["approvals"][impact]
    author = (author or "").lower()
    people = {w.lower() for w in roster(gov)} - {author}
    maint = {w.lower() for w in gov["maintainers"]} - {author}
    states = latest_states(reviews)
    approved = sorted(w for w, s in states.items() if s == "APPROVED" and w in people)
    blocked = sorted(w for w, s in states.items() if s == "CHANGES_REQUESTED" and w in people)
    need, need_m = rule["count"], rule["maintainers"]
    lines = []
    if len(people) < need or len(maint) < need_m:
        lines.append("the roster has %d eligible reviewer(s) and %d maintainer(s) besides the author — "
                     "a maintainer merges with a recorded admin bypass until it grows"
                     % (len(people), len(maint)))
    got_m = sum(1 for w in approved if w in maint)
    ok = len(approved) >= need and got_m >= need_m and not blocked and len(people) >= need and len(maint) >= need_m
    if blocked:
        lines.append("changes requested by " + ", ".join("@" + w for w in blocked))
    return {"ok": ok, "need": need, "need_maintainers": need_m, "approved": approved,
            "maintainers_approved": got_m, "lines": lines}


# ─── reading the change (git, as data) ───────────────────────────────────────

def git(*args, check=True) -> str:
    p = subprocess.run(("git",) + args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if check and p.returncode != 0:
        raise SystemExit("git %s failed: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout


def show(ref: str, path: str) -> str:
    return git("show", "%s:%s" % (ref, path), check=False)


def changed(base: str, head: str) -> list:
    out = []
    for line in git("diff", "--name-status", "-M", "%s...%s" % (base, head)).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out.append((parts[0], parts[-1]))
            if parts[0].startswith("R"):
                out.append(("D", parts[1]))          # a rename removes the old path too
    return out


def commits(base: str, head: str) -> list:
    raw = git("log", "--format=%H%x1f%P%x1f%an%x1f%ae%x1f%B%x1e", "%s..%s" % (base, head))
    out = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, name, email, msg = (rec.split("\x1f") + [""] * 5)[:5]
        out.append({"sha": sha, "parents": parents.split(), "name": name, "email": email, "message": msg})
    return out


def build(base: str, head: str, title: str, body: str, gov: dict, bot: bool = False) -> dict:
    """Everything the report says about one change. `bot` (Dependabot and the like): the title rule
    still applies; a description and a sign-off are not asked of a machine — the review still is."""
    merge_base = git("merge-base", base, head).strip()
    ch = changed(merge_base, head)
    cal_b, cal_h = show(merge_base, "brain/calibrate.py"), show(head, "brain/calibrate.py")
    rulers = ruler_modules(cal_b) | ruler_modules(cal_h)
    rat = (ratchet_value(show(merge_base, RATCHET)), ratchet_value(show(head, RATCHET)))
    cls = classify(ch, gov, rulers, rat)
    tracked = git("ls-tree", "-r", "--name-only", head).split()
    py = [(p, show(merge_base, p), show(head, p)) for s, p in ch
          if p.endswith(".py") and not s.startswith("D")]
    problems = []
    t = title_problem(title)
    if t:
        problems.append(t)
    if not bot:
        problems += section_problems(body, cls["impact"])
        problems += dco_problems(commits(merge_base, head))
    problems += import_problems(py, local_modules(tracked))
    notes = ["opened by a bot — description and sign-off are not asked; the review is"] if bot else []
    touched_rulers = sorted(p for p in (f["path"] for f in cls["files"])
                            if p.startswith("brain/") and p[6:-3] in rulers)
    if touched_rulers:
        notes.append("measurement code changed (%s) · generation %s → %s — `verify_code_generation --base` "
                     "in CI decides whether a bump is due (comment-only edits need none)"
                     % (", ".join("`%s`" % p for p in touched_rulers), generation(cal_b), generation(cal_h)))
    code = any(f["path"].startswith("brain/") and f["path"].endswith(".py") for f in cls["files"])
    tests = any(f["path"].startswith("tests/") for f in cls["files"])
    if code and not tests:
        notes.append("code in `brain/` changed and no file in `tests/` did — say in **Checks** what covers it")
    sections = {k.lower(): v for k, v in parse_sections(body).items()}
    numbers = bool(re.search(r"\d", sections.get("effect", "")))
    if cls["impact"] == "high" and not numbers:
        notes.append("high impact and **Effect** has no numbers — before → after, measured the same way")
    facts = {"added": [f["path"] for f in cls["files"] if f["status"].startswith("A")],
             "removed": [f["path"] for f in cls["files"] if f["status"].startswith("D")],
             "tests": [f["path"] for f in cls["files"] if f["path"].startswith("tests/")],
             "rulers": touched_rulers, "generation": (generation(cal_b), generation(cal_h)),
             "numbers": numbers, "stdlib": not any("standard library" in p for p in problems)}
    return {"class": cls, "problems": problems, "notes": notes, "merge_base": merge_base,
            "sections": sections, "facts": facts}


# ─── the report ──────────────────────────────────────────────────────────────

def render(rep: dict, gov: dict, author: str, gate_result=None, requested=()) -> str:
    cls = rep["class"]
    imp = cls["impact"]
    rule = gov["approvals"][imp]
    lines = [MARKER, "### PR report · impact **%s**" % imp, ""]
    if cls["reasons"]:
        by = {}
        for p, w in cls["reasons"]:
            by.setdefault(w, []).append(p.rsplit("/", 1)[-1])
        lines.append("**Why %s:** " % imp + " · ".join(
            "%s (%s)" % (w, ", ".join("`%s`" % n for n in ns[:4]) + (" +%d" % (len(ns) - 4) if len(ns) > 4 else ""))
            for w, ns in by.items()))
    lines.append("**Areas:** " + (" · ".join(cls["areas"]) or "none"))
    need = "%d approval%s" % (rule["count"], "" if rule["count"] == 1 else "s")
    if rule["maintainers"]:
        need += ", at least %d from a maintainer" % rule["maintainers"]
    lines.append("**Review:** %s, plus the code owners of every area touched (`.github/governance.json`)" % need
                 + (" · requested " + " ".join("@" + r for r in requested) if requested else ""))
    lines += ["", "| Check | |", "|---|---|"]
    probs = rep["problems"]
    groups = [("Title `area: what changed`", [p for p in probs if p.startswith("the title")]),
              ("Description (%s)" % " · ".join(REQUIRED[imp]),
               [p for p in probs if p.startswith("missing section") or p.endswith("is empty")]),
              ("Sign-off on every commit (DCO)", [p for p in probs if "Signed-off-by" in p or "signed off" in p]),
              ("Standard library only", [p for p in probs if "standard library" in p or "does not parse" in p])]
    for label, ps in groups:
        lines.append("| %s | %s |" % (label, "✅" if not ps else "❌ " + "<br>".join(ps)))
    for n in rep["notes"]:
        lines.append("| ⚠️ note | %s |" % n)
    if gate_result is not None:
        g = gate_result
        lines.append("| Approvals | %s %d/%d%s%s |" % (
            "✅" if g["ok"] else "⏳", len(g["approved"]), g["need"],
            ", maintainer %d/%d" % (g["maintainers_approved"], g["need_maintainers"]) if g["need_maintainers"] else "",
            "<br>" + "<br>".join(g["lines"]) if g["lines"] else ""))
    if imp == "high" and rep.get("facts") is not None:
        lines += ["", "#### Summary for reviewers", "",
                  "| | The description says | The diff shows |", "|---|---|---|"]
        for name, seen in reviewer_rows(rep):
            lines.append("| %s | %s | %s |" % (name, quote(rep["sections"].get(name.lower(), "")), seen))
    lines += ["", "<details><summary>%d file(s) by area</summary>" % len(cls["files"]), "",
              "| File | Area | Impact |", "|---|---|---|"]
    for f in sorted(cls["files"], key=lambda f: (-LEVELS.index(f["impact"]), f["area"], f["path"])):
        lines.append("| `%s`%s | %s | %s |" % (f["path"], " (removed)" if f["status"].startswith("D") else "",
                                               f["area"], f["impact"]))
    lines += ["", "</details>", ""]
    if imp == "high":
        lines += ["**Before approving a high-impact change** ([GOVERNANCE.md](%s#important-changes)):" % doc_url("GOVERNANCE.md"),
                  "purpose matches the diff · the effect is measured, not asserted · it extends an existing seam "
                  "rather than adding a new one · the risk has a rollback · new checks can fail.", ""]
    lines.append("<sub>Updated on every push by `.github/scripts/pr_report.py`. Rules: CONTRIBUTING.md · "
                 "GOVERNANCE.md. Run it yourself: `python3 .github/scripts/pr_report.py --local`.</sub>")
    return "\n".join(lines) + "\n"


def quote(text: str, limit: int = 280) -> str:
    """One table cell from a description section: lists flattened, ticks kept, nothing that breaks the table."""
    t = re.sub(r"^\s*[-*]\s*\[[xX]\]\s*", "✓ ", text or "", flags=re.M)
    t = re.sub(r"^\s*[-*]\s*\[ \]\s*", "☐ ", t, flags=re.M)
    t = re.sub(r"^\s*[-*]\s+", "", t, flags=re.M)
    t = " · ".join(line.strip() for line in t.splitlines() if line.strip())
    t = t.replace("|", "\\|").replace("<", "&lt;")
    return (t[:limit - 1] + "…") if len(t) > limit else (t or "—")


def reviewer_rows(rep: dict) -> list:
    """The five questions, each beside what the diff itself shows — so several reviewers can judge the
    author's answer against the change in one look, with no service to pay for."""
    f, cls = rep["facts"], rep["class"]

    def few(paths):
        return ", ".join("`%s`" % p.rsplit("/", 1)[-1] for p in paths[:3]) + (" +%d" % (len(paths) - 3)
                                                                              if len(paths) > 3 else "")
    gen = ""
    if f["rulers"]:
        a, b = f["generation"]
        gen = " · measurement code changed, generation %s → %s" % (a, b)
    return [
        ("Purpose", "areas: " + (" · ".join(cls["areas"]) or "none")),
        ("Effect", "numbers given ✅" if f["numbers"] else "⚠️ no numbers"),
        ("Scalability and extensibility",
         ("%d new file(s): %s" % (len(f["added"]), few(f["added"])) if f["added"] else "no new files")
         + (" · standard library only ✅" if f["stdlib"] else " · ❌ a new import outside the standard library")),
        ("Risks and rollback",
         ("%d file(s) removed: %s" % (len(f["removed"]), few(f["removed"])) if f["removed"] else "nothing removed")
         + gen),
        ("Checks", "%d test file(s) changed" % len(f["tests"]) if f["tests"] else "⚠️ no file in `tests/` changed"),
    ]


# ─── CI costs nothing ────────────────────────────────────────────────────────
# The maintainers' decision (2026-10-07): no CI step may cost money. Public repositories run on GitHub's
# standard runners for free; what would cost is a paid API (an AI service) or a larger runner.
PAID_SECRET = re.compile(r"secrets\.(\w*(?:ANTHROPIC|OPENAI|GEMINI|GOOGLE_API|CLAUDE|COHERE|VOYAGE|MISTRAL|GROQ"
                         r"|TOGETHER|HUGGING|HF_TOKEN|AZURE|BEDROCK|VERTEX|AWS_SECRET)\w*)", re.I)
PAID_ACTION = re.compile(r"uses:\s*['\"]?((?:anthropics/claude-code[\w-]*|openai/[\w.-]+|google-github-actions/"
                         r"run-gemini[\w-]*)(?:@[\w.-]+)?)", re.I)
FREE_RUNNER = re.compile(r"^(?:ubuntu|windows|macos)-(?:latest|\d+(?:\.\d+)?)(?:-arm)?$")


def paid_steps(workflow: str) -> list:
    """What in one workflow file would cost money · [] when nothing does."""
    out = ["uses the secret `%s` — a paid API key" % m.group(1) for m in PAID_SECRET.finditer(workflow)]
    out += ["uses `%s` — it calls a paid API" % m.group(1) for m in PAID_ACTION.finditer(workflow)]
    labels = re.findall(r"runs-on:\s*([^\s#]+)", workflow) + re.findall(r"\bos:\s*([^\s,}#]+)", workflow)
    for label in labels:
        label = label.strip("'\"")
        if not label.startswith("${{") and not FREE_RUNNER.match(label):
            out.append("runs on `%s` — not a free standard runner" % label)
    return out


def doc_url(name: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return "%s/%s/blob/main/%s" % (server, repo, name) if repo else name


# ─── GitHub (stdlib HTTP) ────────────────────────────────────────────────────

def api(method: str, path: str, body=None):
    url = "https://api.github.com" + path if path.startswith("/") else path
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return (json.loads(raw) if raw else None), r.headers.get("Link", "")


def api_all(path: str) -> list:
    out, url = [], path + ("&" if "?" in path else "?") + "per_page=100"
    while url:
        page, link = api("GET", url)
        out += page or []
        m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
        url = m.group(1) if m else ""
    return out


def event() -> dict:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
        return json.load(fh)


def summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def upsert_comment(repo: str, number: int, text: str) -> None:
    for c in api_all("/repos/%s/issues/%d/comments" % (repo, number)):
        if MARKER in (c.get("body") or "") and (c.get("user") or {}).get("type") == "Bot":
            api("PATCH", "/repos/%s/issues/comments/%d" % (repo, c["id"]), {"body": text})
            return
    api("POST", "/repos/%s/issues/%d/comments" % (repo, number), {"body": text})


def sync_labels(repo: str, number: int, current: list, cls: dict) -> None:
    """Only the labels this script owns (`impact: …`, `area: …`) are added or removed."""
    want = {"impact: " + cls["impact"]} | {"area: " + a for a in cls["areas"]}
    mine = {n for n in current if n.startswith(("impact: ", "area: "))}
    for name in sorted(mine - want):
        try:
            api("DELETE", "/repos/%s/issues/%d/labels/%s" % (repo, number, urllib.request.quote(name)))
        except urllib.error.HTTPError:
            pass
    add = sorted(want - set(current))
    if add:
        api("POST", "/repos/%s/issues/%d/labels" % (repo, number), {"labels": add})


def request_reviews(repo: str, number: int, pr: dict, gov: dict, cls: dict) -> list:
    """High impact asks the whole roster; otherwise CODEOWNERS already asked the right people."""
    if cls["impact"] != "high":
        return []
    author = (pr.get("user") or {}).get("login", "").lower()
    already = {(u.get("login") or "").lower() for u in pr.get("requested_reviewers", [])}
    ask = [w for w in roster(gov) if w.lower() not in already | {author}][:15]
    if ask:
        try:
            api("POST", "/repos/%s/pulls/%d/requested_reviewers" % (repo, number), {"reviewers": ask})
        except urllib.error.HTTPError as exc:
            print("review request skipped: %s" % exc)
            return []
    return ask


# ─── modes ───────────────────────────────────────────────────────────────────

def arg(argv: list, name: str, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def main(argv: list) -> int:
    # ⛔ A Windows console speaks cp1252 and dies on the first ✅ (CI, 2026-10-07) — write UTF-8 regardless.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    gov = load_governance()
    if "--codeowners" in argv:
        text = codeowners(gov)
        if "--write" in argv:
            with open(CODEOWNERS, "w", encoding="utf-8") as fh:
                fh.write(text)
            print("wrote %s" % os.path.relpath(CODEOWNERS, ROOT))
        else:
            sys.stdout.write(text)
        return 0

    if "--local" in argv:
        base = arg(argv, "--base", "origin/main")
        title = arg(argv, "--title") or git("log", "-1", "--format=%s").strip()
        body_file = arg(argv, "--body")
        body = open(body_file, encoding="utf-8").read() if body_file else ""
        rep = build(base, "HEAD", title, body, gov)
        print(render(rep, gov, ""))
        if not body_file:
            print("(no --body FILE given, so the description rows show what the template still needs)")
        return 1 if rep["problems"] else 0

    ev = event()
    if "merge_group" in ev:
        print("merge queue: the pull request passed this before it was queued")
        return 0
    pr = ev["pull_request"]
    repo = os.environ["GITHUB_REPOSITORY"]
    author = (pr.get("user") or {}).get("login", "")

    if "--gate" in argv:
        # impact from the API's file list; ruler modules from both sides' calibrate.py, read as data
        rulers = (ruler_modules(fetch_text(repo, "brain/calibrate.py", pr["base"]["sha"]))
                  | ruler_modules(fetch_text(repo, "brain/calibrate.py", pr["head"]["sha"])))
        impact = classify(changed_via_api(repo, pr["number"]), gov, rulers)["impact"]
        g = gate(impact, api_all("/repos/%s/pulls/%d/reviews" % (repo, pr["number"])), gov, author)
        msg = "impact %s · approvals %d/%d%s" % (
            impact, len(g["approved"]), g["need"],
            " · maintainer %d/%d" % (g["maintainers_approved"], g["need_maintainers"]) if g["need_maintainers"] else "")
        print(msg)
        for line in g["lines"]:
            print("  · " + line)
        summary("**Review gate:** %s %s" % ("✅" if g["ok"] else "⏳", msg))
        return 0 if g["ok"] else 1

    base, head = pr["base"]["sha"], pr["head"]["sha"]
    bot = (pr.get("user") or {}).get("type") == "Bot"
    rep = build(base, head, pr.get("title", ""), pr.get("body") or "", gov, bot=bot)
    if "--check" in argv:
        text = render(rep, gov, author)
        print(text)
        summary(text)
        return 1 if rep["problems"] else 0
    if "--comment" in argv:
        requested = request_reviews(repo, pr["number"], pr, gov, rep["class"])
        text = render(rep, gov, author, requested=requested)
        upsert_comment(repo, pr["number"], text)
        sync_labels(repo, pr["number"], [l["name"] for l in pr.get("labels", [])], rep["class"])
        summary(text)
        return 0
    print(__doc__)
    return 2


def changed_via_api(repo: str, number: int) -> list:
    out = []
    for f in api_all("/repos/%s/pulls/%d/files" % (repo, number)):
        st = {"added": "A", "removed": "D", "renamed": "R", "modified": "M"}.get(f.get("status"), "M")
        out.append((st, f["filename"]))
        if st == "R" and f.get("previous_filename"):
            out.append(("D", f["previous_filename"]))
    return out


def fetch_text(repo: str, path: str, ref: str) -> str:
    try:
        data, _ = api("GET", "/repos/%s/contents/%s?ref=%s" % (repo, path, ref))
    except urllib.error.HTTPError:
        return ""
    import base64
    return base64.b64decode(data.get("content", "")).decode("utf-8", "replace") if data else ""


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
