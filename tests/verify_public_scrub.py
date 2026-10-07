"""Pre-release scrub check — ★if this repository is handed to someone, what goes with it★ (2026-09-03).

## Why this is needed — the plan's own number was counting ★only half★

`docs/PUBLIC-RELEASE-PLAN.md` said *"15 files still hold internal references"*. That number counted
★only the current files★. But `git push` sends ★history★, not files. Measured (2026-09-03): the internal
name appeared 17 more times in commit messages and 34 more in past diffs.
Deleting it from the current file still ships it.

★This repository has met this exact failure before★ — "an under-counting instrument calls itself done"
(2026-09-02: it said 24 when the real number was 67). So this check counts ★five axes★.

| axis | what | why counted separately |
|---|---|---|
| tracked | internal names inside currently tracked files | what is visible |
| history | commit messages + past diffs | ★push sends history★ |
| private | is personal data being tracked | `.gitignore` is ★a rule only — it cannot un-track what is already tracked★ |
| secret | secret-shaped strings | GitHub push protection ★refuses the push★ |
| identity | author email · a human's own path | baked into every single commit |

## ⛔ This file ★holds no internal names of its own★

Hold one and this check file itself becomes the leak (the contradiction of holding what it searches for).
Terms come from outside: `BRAIN_SCRUB_TERMS` (comma-separated) → `tests/scrub_terms.txt` (gitignored).
★Absent, that axis is `unknown` and says so loudly★ — this repository's own principle (a calibration guard
silently dead with no sample was fixed on exactly this date, 2026-09-03). Printing `0` with no term list
would be exactly "a probe that cannot fail".

## Control group — ★first prove this check can fail on its own★

`selftest()` builds a temporary repository, plants invented names, and confirms all five axes catch
them. If even one misses, the real check's result is ★not trusted★.
"""
from __future__ import annotations

import os
import re
import fnmatch
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ─── general patterns that work with no term list ──────────────────────────
# What GitHub push protection actually blocks + what identifies a person.
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
HOMEPATH = re.compile(r"(?:/Users/|/home/|[A-Z]:\\\\?Users\\\\?)([A-Za-z][A-Za-z0-9._-]{1,31})")
# ⛔ A ★generic name★ in a home path is not a person — a placeholder used in documentation
HOMEPATH_OK = {"you", "user", "username", "name", "me", "someone", "your", "yourname"}
# ★Internal infrastructure★ — GitHub does not block these, but ★they identify the company★.
#   ⛔ On 2026-09-03 this axis was missing and missed a real ES cluster IP inside a fixture.
#      (this repository's repeated "under-counting instrument" struck ★in the checker itself★ again)
PRIVATE_IP = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")
# ⛔ `com.local-brain.vec-daily` (a launchd label) is not a host — not when a hyphen follows
INTERNAL_HOST = re.compile(
    r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\.(?:internal|local|corp|intranet|lan)(?![\w-])")
# ⛔ A placeholder is an example a human made up — counting it buries the real signal
IP_OK = re.compile(r"^(?:10\.0\.0\.[01]|192\.168\.[01]\.[01]|127\.)")
SECRETS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("db-uri-with-credentials",
     re.compile(r"\b(?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis|amqp|ftp|ssh)"
                r"://[^\s`\"'<>]*:[^\s`\"'<>/@]+@[^\s`\"'<>]+")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{12,20}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
]

# ★Personal data★ — if what `.gitignore` meant to block is being tracked, the rule is leaking.
#   ⛔ `.gitignore` ignores ★only untracked files★. Once something is added, it keeps following you.
PRIVATE_HINTS = [
    (re.compile(r"^tests/eval/(?!.*\.example\.json$).+"), "a personal evaluation set (queries and gold from one's own conversations)"),
    (re.compile(r"^config\.json$"), "config holding that person's own folder layout"),
    (re.compile(r".*\.(?:db|db-shm|db-wal|log)$"), "a local build artefact"),
    (re.compile(r".*\.bak$"), "a backup copy — ★slips past the extension rule★"),
]


def _git(args, cwd=ROOT):
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    return p.stdout


def terms():
    """Read internal terms ★from the outside★. (list, source) — ([], "") if absent.

    A line starting with `!regex` is ★a false-positive exclusion★ (a short term lives inside someone
    else's word: `trg` ↔ `_trg`). ⛔ An exclusion closes an eye, so ★it must report how many lines it filtered★.
    """
    env = os.environ.get("BRAIN_SCRUB_TERMS", "").strip()
    if env:
        return [t.strip() for t in env.split(",") if t.strip()], "BRAIN_SCRUB_TERMS"
    path = os.path.join(ROOT, "tests", "scrub_terms.txt")
    if os.path.exists(path):
        out = []
        for line in open(path, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
        if out:
            return out, "tests/scrub_terms.txt"
    return [], ""


def split_terms(tl):
    """Split into (terms, exclusion regexes)."""
    words = [t for t in tl if not t.startswith("!")]
    skips = [re.compile(t[1:]) for t in tl if t.startswith("!")]
    return words, skips


def _tracked(cwd=ROOT):
    return [f for f in _git(["ls-files"], cwd).splitlines() if f]


def exclude_globs(cwd=ROOT):
    """Paths left out of the public release. `tests/public_exclude.txt` — ★reads the same file as export★."""
    path = os.path.join(cwd, "tests", "public_exclude.txt")
    out = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
    return out


def split_public(cwd=ROOT):
    """(the files that ship, the files left out). ⛔ What is left out is ★counted and shown too★ — never hidden."""
    globs = exclude_globs(cwd)
    pub, exc = [], []
    for f in _tracked(cwd):
        (exc if any(fnmatch.fnmatch(f, g) for g in globs) else pub).append(f)
    return pub, exc


def _read(path, cwd=ROOT):
    try:
        with open(os.path.join(cwd, path), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (OSError, IsADirectoryError):
        return ""


def scan_tracked(tl, cwd=ROOT, files=None):
    """Axis 1 — internal names inside currently tracked files. ({term: [(file, line, body)]}, skip count)"""
    hits = {}
    tl, skips = split_terms(tl)
    skipped = 0
    if not tl:
        return hits
    low = [t.lower() for t in tl]
    for f in (files if files is not None else _tracked(cwd)):
        text = _read(f, cwd)
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            ll = line.lower()
            if any(p.search(line) for p in skips):
                skipped += 1
                continue
            for t, t_low in zip(tl, low):
                if t_low in ll:
                    hits.setdefault(t, []).append((f, i, line.strip()[:120]))
    hits["__skipped__"] = skipped
    return hits


def scan_history(tl, cwd=ROOT, skips=()):
    """Axis 2 — ★what push sends★. {term: {'messages': n, 'diff': n}}

    A line matching a declared exclusion (`!regex` in the term list) is skipped here too — the same rule
    the tracked-file axis uses, or a declared false positive (`_trg`) would block every release forever.
    """
    hits = {}
    if not tl:
        return hits

    def keep(text: str) -> str:
        return "\n".join(l for l in text.splitlines() if not any(p.search(l) for p in skips)).lower()
    msgs = keep(_git(["log", "--all", "--format=%s%n%b"], cwd))
    diff = keep(_git(["log", "--all", "-p", "--format="], cwd))
    for t in tl:
        t_low = t.lower()
        m, d = msgs.count(t_low), diff.count(t_low)
        if m or d:
            hits[t] = {"messages": m, "diff": d}
    return hits


def scan_private(cwd=ROOT, files=None):
    """Axis 3 — is personal data being tracked. [(file, reason, bytes)]"""
    out = []
    for f in (files if files is not None else _tracked(cwd)):
        for pat, why in PRIVATE_HINTS:
            if pat.match(f):
                try:
                    size = os.path.getsize(os.path.join(cwd, f))
                except OSError:
                    size = -1
                out.append((f, why, size))
                break
    return out


def scan_secret(cwd=ROOT, files=None):
    """Axis 4 — secret-shaped strings. ★Both the working tree and history★ (where a push gets refused)."""
    out = []
    for f in (files if files is not None else _tracked(cwd)):
        text = _read(f, cwd)
        if not text:
            continue
        for name, pat in SECRETS:
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                out.append(("tracked", name, f"{f}:{line}", m.group(0)[:60]))
    diff = _git(["log", "--all", "-p", "--format="], cwd)
    for name, pat in SECRETS:
        n = len(pat.findall(diff))
        if n:
            out.append(("history", name, f"{n} spots in past diffs", ""))
    return out


def scan_identity(cwd=ROOT, files=None):
    """Axis 5 — what identifies a person or a company. {'authors': [...], 'emails': [...], 'homes': [...]}"""
    authors = {}
    for line in _git(["log", "--all", "--format=%an <%ae>"], cwd).splitlines():
        if line:
            authors[line] = authors.get(line, 0) + 1
    emails, homes = {}, {}
    fl = files if files is not None else _tracked(cwd)
    for f in fl:
        text = _read(f, cwd)
        for m in EMAIL.finditer(text):
            emails.setdefault(m.group(0), []).append(f)
        for m in HOMEPATH.finditer(text):
            who = m.group(1)
            if who.lower() not in HOMEPATH_OK:
                homes.setdefault(who, []).append(f)
    ips, hosts = {}, {}
    for f in fl:
        text = _read(f, cwd)
        for m in PRIVATE_IP.finditer(text):
            if not IP_OK.match(m.group(0)):
                ips.setdefault(m.group(0), []).append(f)
        for m in INTERNAL_HOST.finditer(text):
            hosts.setdefault(m.group(0), []).append(f)
    return {"authors": authors, "emails": emails, "homes": homes,
            "ips": ips, "hosts": hosts}


# ─── control group ──────────────────────────────────────────────────────────
# ⛔⛔ ★Never write the canary as a literal★ (this actually happened on 2026-09-03)
#
# The moment this file entered the repository, ★the checker caught its own canary★ — and that is when
# it was found: the control group had ★a real internal IP★ written out plainly. It held the very thing
# it searches for. The fix is the same as the fixture's (§verify_privacy) — ★join it from pieces★.
# What is being tested is whether the regex catches ★the assembled string★, so the test still holds.
CANARY = "zz" + "canaryfirm"
_C_HOME = "canary" + "person"
_C_MAIL = "canary@" + "example.invalid"
_C_IP = "10." + "99.99.99"
_C_HOST = "canary" + ".internal"


def selftest():
    """★Can this check fail on its own★ — do all five axes catch what was planted."""
    fails = []
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", _C_MAIL], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "Canary"], cwd=d, check=True)
        # commit 1: a secret + an internal name (removed later → survives only in history)
        os.makedirs(os.path.join(d, "tests", "eval"), exist_ok=True)
        with open(os.path.join(d, "gone.txt"), "w") as fh:
            fh.write(f"{CANARY} project\nAKIA0123456789ABCDEF\n")
        subprocess.run(["git", "add", "-A"], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", f"init {CANARY}"], cwd=d, check=True)
        # commit 2: remove it, and leave the current file · personal data · a home path · an email
        os.remove(os.path.join(d, "gone.txt"))
        with open(os.path.join(d, "here.py"), "w") as fh:
            fh.write(f"# using {CANARY}\n# /Users/{_C_HOME}/x\n# {_C_MAIL}\n"
                     f"# {_C_IP}:9200 · {_C_HOST}\n")
        with open(os.path.join(d, "tests", "eval", "short.json"), "w") as fh:
            fh.write("{}\n")
        subprocess.run(["git", "add", "-Af"], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "second"], cwd=d, check=True)

        if CANARY not in scan_tracked([CANARY], d):
            fails.append("axis1 tracked missed the planted name")
        h = scan_history([CANARY], d)
        if not h.get(CANARY, {}).get("messages"):
            fails.append("axis2 history did not see ★the commit message★")
        if not h.get(CANARY, {}).get("diff"):
            fails.append("axis2 history did not see ★the past diff of a deleted file★")
        if not any(f.startswith("tests/eval/") for f, _, _ in scan_private(d)):
            fails.append("axis3 private missed the personal evaluation set")
        s = scan_secret(d)
        if not any(w == "tracked" or "history" == w for w, _, _, _ in s):
            fails.append("axis4 secret missed the planted AWS key")
        if not any(k == "history" for k, _, _, _ in s):
            fails.append("axis4 secret missed the key ★buried in history★")
        ident = scan_identity(d)
        if _C_HOME not in ident["homes"]:
            fails.append("axis5 identity missed the person's name in the home path")
        if not ident["authors"]:
            fails.append("axis5 identity could not read the author")
        if _C_IP not in ident["ips"]:
            fails.append("axis5 identity missed ★the private IP★")
        if _C_HOST not in ident["hosts"]:
            fails.append("axis5 identity missed ★the internal host★")
    return fails


def main():
    # ★Release method★ — since 2026-10-07 this repository ★is★ the public one, so its history ships and
    #   axis 2 is a blocker. `--snapshot` keeps the old check (only the current files, into a new repository).
    inplace = "--snapshot" not in sys.argv
    print("=" * 74)
    print("Pre-release scrub check — if this repository is handed to someone, what goes with it")
    print("mode: " + ("★history and all (--inplace)★" if inplace else "snapshot release (only the current files, into a new repository)"))
    print("=" * 74)

    fails = selftest()
    if fails:
        print("\n⛔⛔ control group failed — ★do not trust this check's result★")
        for f in fails:
            print("   ·", f)
        return 2
    print("\n✅ control group passed — all five axes catch what was planted (the check can fail)")

    pub, exc = split_public()
    print(f"\n{len(pub)} files ship · {len(exc)} left out by the manifest")
    print("   (tests/public_exclude.txt — empty or absent when the repository itself is what ships)")

    tl, src = terms()
    blockers = []

    print("\n" + "-" * 74)
    if tl:
        print(f"[axis1] internal names inside the files that ship · {len(tl)} terms · source {src}")
        t1 = scan_tracked(tl, files=pub)
        t1x = scan_tracked(tl, files=exc)
        for t in tl:
            if t.startswith("!"):
                continue
            f = t1.get(t, [])
            if f:
                for path, i, line in f:
                    print(f"   ⛔ {path}:{i} [{t}] {line[:90]}")
        n_skip = t1.pop("__skipped__", 0)
        t1x.pop("__skipped__", None)
        n_now = sum(len(v) for v in t1.values())
        print(f"   ── in what ships: {n_now} lines"
              + (f"  ({n_skip} lines filtered by an exclusion rule — ★declared in scrub_terms.txt★)"
                 if n_skip else ""))
        if n_now:
            blockers.append(f"{n_now} lines of internal names in files that ship")
        n_exc = sum(len(v) for v in t1x.values())
        print(f"   ── ({n_exc} lines in excluded files — ★counted, not hidden★. They do not ship)")

        print(f"\n[axis2] ★history★ — push sends history, not files")
        _w, _sk = split_terms(tl)
        t2 = scan_history(_w, skips=_sk)
        n_hist = sum(v["messages"] + v["diff"] for v in t2.values())
        for t, h in sorted(t2.items(), key=lambda x: -(x[1]["messages"] + x[1]["diff"])):
            print(f"   {t:<16} commit msgs {h['messages']:>3} · past diffs {h['diff']:>4}")
        print(f"   ── total {n_hist}")
        if inplace:
            if n_hist:
                blockers.append(f"★{n_hist} internal-name hits in history — fixing the file does not remove them★")
        else:
            print("   → a snapshot release, so ★this history does not ship★. "
                  "⛔ but pushing this repository as-is sends all of it")
    else:
        print("[axis1·2] ⛔⛔ ★no term list — these two axes are `unknown`★")
        print("        Create `tests/scrub_terms.txt` or supply BRAIN_SCRUB_TERMS.")
        print("        ⛔ a 0 with no list is not 'clean' — it is 'not measured'.")
        blockers.append("could not measure the internal-name axis (no term list)")

    print("\n" + "-" * 74)
    priv = scan_private(files=pub)
    print(f"[axis3] personal data in the files that ship — {len(priv)} found")
    for f, why, size in priv:
        print(f"   ⛔ {f}  ({size:,}B)  {why}")
    if priv:
        blockers.append(f"{len(priv)} personal-data hits")

    print("\n" + "-" * 74)
    sec = scan_secret(files=pub)
    now = [x for x in sec if x[0] == "tracked"]
    hist = [x for x in sec if x[0] == "history"]
    print(f"[axis4] secret-shaped strings — {len(now)} in files that ship (GitHub push protection refuses these)")
    for _, name, loc, sample in now:
        print(f"   ⛔ {name}  {loc}  {sample}")
    if now:
        blockers.append(f"{len(now)} secret-shaped strings in files that ship")
    if hist:
        tag = "⛔" if inplace else "  (does not ship if this is a snapshot)"
        for _, name, loc, _s in hist:
            print(f"   {tag} [history] {name}  {loc}")
        if inplace:
            blockers.append(f"{len(hist)} secret-shaped strings in history")

    print("\n" + "-" * 74)
    ident = scan_identity(files=pub)
    print("[axis5] what identifies a person or a company")
    for e, fs in sorted(ident["emails"].items()):
        print(f"   ⛔ email {e}  ({len(fs)} spots: {fs[0]})")
    for who, fs in sorted(ident["homes"].items()):
        print(f"   ⛔ home path /Users/{who}  ({len(fs)} spots: {fs[0]})")
    for ip, fs in sorted(ident["ips"].items()):
        print(f"   ⛔ private IP {ip}  ({len(fs)} spots: {fs[0]})")
    for h, fs in sorted(ident["hosts"].items()):
        print(f"   ⛔ internal host {h}  ({len(fs)} spots: {fs[0]})")
    if ident["emails"] or ident["homes"] or ident["ips"] or ident["hosts"]:
        blockers.append("files that ship hold an email, a person's path, or internal infrastructure")
    print("   ── commit authors (★decided once — a GitHub noreply address names an account, not a mailbox★):")
    for a, n in sorted(ident["authors"].items(), key=lambda x: -x[1]):
        print(f"      {a}  {n} commits")
    # ⛔ a published history carries every author line — only a GitHub noreply address is safe by itself;
    #    anything else is a person's real mailbox shipping in every commit
    exposed = [a for a in ident["authors"] if "@users.noreply.github.com>" not in a]
    if inplace and exposed:
        blockers.append(f"{len(exposed)} commit-author identities with a real address ship as-is: "
                        + ", ".join(exposed[:3]))

    print("\n" + "=" * 74)
    if blockers == ["could not measure the internal-name axis (no term list)"]:
        # ⏭ 77 = skipped, not passed — this is the publisher's gate, and the list of names that must not
        #    ship is the publisher's own (gitignored). Every other axis above was measured and is clean.
        print("⏭ skipped — the internal-name axes need the publisher's term list "
              "(tests/scrub_terms.txt or BRAIN_SCRUB_TERMS); the other axes are clean")
        return 77
    if blockers:
        print(f"⛔ {len(blockers)} release blockers")
        for b in blockers:
            print("   ·", b)
        return 1
    print("✅ all five axes are clean in what ships")
    if not inplace:
        print("   ⛔ but this is only ★the snapshot★ — pushing this repository as-is ships the history")
    return 0


if __name__ == "__main__":
    sys.exit(main())
