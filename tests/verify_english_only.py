"""English-only check — ★what is still Korean, and where exactly★ (2026-09-03).

## Why this exists

English is the base language of this project (decided 2026-09-02, scope =
output strings + design comments + docs). The first public snapshot went out
still largely in Korean, because "translate later" was written in the plan and
nobody was counting.

⛔ This repository has already been burnt by exactly this shape:
   *"translating the screen is not translating the labels"* (2026-09-02) —
   six languages of labels were added, it was reported done, and the user
   opened the English screen to find the whole comparison table in Korean.
   ★761 fragments were left, from five different sources.★

So this check does not ask "is there a translation file". It counts ★every
remaining Korean character in every file that ships★, broken down by where it
lives, because the fix differs per kind:

    docs        rewrite the document in English
    docstring   rewrite the design note in English (⛔ keep the ⛔/★ markers
                and the measured numbers — they are the point, not decoration)
    comment     same
    string      user-visible output → English (or a catalog key)
    other       config/example values, seed templates

## ⛔ What is deliberately NOT counted — and ★it is printed, not hidden★

Two files are allowed to hold Korean, because in both of them Korean is ★the
data★, not the prose:

    brain/locales/ko.json   the Korean catalog — Korean belongs there
    brain/langdata.py       patterns matched against the user's own documents
                            (inflectional endings, condition words, language markers).
                            ⛔ Translating those does not fail loudly — the
                            corpus still says the Korean word and the matcher
                            silently stops matching.

Counting them would make the target unreachable and teach us to ignore the
number. ⛔ But an exception that shrinks the number ★in silence★ is worse than
no exception: this check prints how many lines it let through, right next to the
count that must reach zero. Everything else must reach zero.
"""
from __future__ import annotations

import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_public_scrub as scrub  # noqa: E402

ROOT = scrub.ROOT
# Hangul syllables + compatibility jamo. ⛔ Do not narrow this to the syllable
#    block alone — "ㄱㄴㄷ" and "ㅋㅋ" would slip through and the count would lie.
KOREAN = re.compile(r"[가-힣ㄱ-ㆎ]")

# Korean is the data here, not the prose (see the module docstring). ⛔ Keep this
# set ★tiny and argued★ — every entry is a line the target no longer reaches.
ALLOWED = {"brain/locales/ko.json", "brain/langdata.py"}


def classify(path, text):
    """Return {kind: line_count}. Python files are parsed so that a docstring
    is not miscounted as a comment — the two are rewritten differently."""
    out = {}
    lines = text.splitlines()
    korean_lines = {i for i, l in enumerate(lines, 1) if KOREAN.search(l)}
    if not korean_lines:
        return out

    if not path.endswith(".py"):
        kind = "docs" if path.endswith((".md", ".html", ".txt")) else "other"
        out[kind] = len(korean_lines)
        return out

    doc_lines, comment_lines = set(), set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                d = ast.get_docstring(node, clean=False)
                if d and KOREAN.search(d):
                    body = node.body[0]
                    for i in range(body.lineno, (body.end_lineno or body.lineno) + 1):
                        doc_lines.add(i)
    for i in korean_lines:
        stripped = lines[i - 1].lstrip()
        if stripped.startswith("#"):
            comment_lines.add(i)

    for i in korean_lines:
        if i in doc_lines:
            out["docstring"] = out.get("docstring", 0) + 1
        elif i in comment_lines:
            out["comment"] = out.get("comment", 0) + 1
        else:
            out["string"] = out.get("string", 0) + 1
    return out


def scan(files=None, cwd=ROOT):
    """{path: {kind: n}} for every shipping file that still holds Korean."""
    if files is None:
        files, _ = scrub.split_public(cwd)
    report = {}
    for f in files:
        if f in ALLOWED:
            continue
        p = os.path.join(cwd, f)
        try:
            text = open(p, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        kinds = classify(f, text)
        if kinds:
            report[f] = kinds
    return report


# ⚠️ ★The blind spot, printed★ (2026-10-06) — this check counts Korean only. Japanese (kana and ideographs)
#    is not counted toward the target: every such line so far is language data (the Japanese catalog, tokenizer
#    and marker tables, test fixtures). But "not counted" must never mean "not seen", so the lines are listed.
OTHER_SCRIPT = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def scan_other_scripts(files=None, cwd=ROOT):
    """{path: lines} holding Japanese — reported beside the target, not inside it."""
    if files is None:
        files, _ = scrub.split_public(cwd)
    out = {}
    for f in files:
        try:
            text = open(os.path.join(cwd, f), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        n = sum(1 for l in text.splitlines() if OTHER_SCRIPT.search(l))
        if n:
            out[f] = n
    return out


def scan_allowed(cwd=ROOT):
    """{path: lines} for the declared exceptions — ★so the allowance is visible★."""
    out = {}
    for f in sorted(ALLOWED):
        try:
            text = open(os.path.join(cwd, f), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        n = sum(1 for l in text.splitlines() if KOREAN.search(l))
        if n:
            out[f] = n
    return out


def selftest():
    """⛔ Prove the check can fail before trusting a green result."""
    fails = []
    ko = "안녕"          # assembled at runtime, never a literal here
    jamo = "ㄱㄴ"
    cases = [
        ("a.md", f"# title\n{ko}\n", "docs"),
        ("b.py", f'"""doc\n{ko}\n"""\nx = 1\n', "docstring"),
        ("c.py", f"x = 1\n# {ko}\n", "comment"),
        ("d.py", f'print("{ko}")\n', "string"),
        ("e.json", f'{{"k": "{ko}"}}\n', "other"),
        ("f.py", f"# {jamo}\n", "comment"),          # jamo must not slip through
    ]
    for name, text, want in cases:
        got = classify(name, text)
        if want not in got:
            fails.append(f"{name}: expected kind {want!r}, got {got!r}")
    if classify("g.py", "x = 1  # plain ascii\n"):
        fails.append("g.py: flagged a file with no Korean at all")
    return fails


def main():
    print("=" * 74)
    print("English-only check — every shipping file must be English")
    print("=" * 74)
    fails = selftest()
    if fails:
        print("\n⛔⛔ control group failed — ★do not trust this result★")
        for f in fails:
            print("   ·", f)
        return 2
    print("\n✅ control group passed — the check can fail "
          "(syllables, jamo, and each kind are caught)")

    allowed = scan_allowed()
    if allowed:
        print("\n  declared exceptions — Korean is the data here, ★not counted★:")
        for f, n in allowed.items():
            print(f"     {n:>5}  {f}")

    other = scan_other_scripts()
    if other:
        print("\n  ⚠️ not counted — another script (Japanese), listed so it is seen:")
        for f, n in sorted(other.items(), key=lambda kv: -kv[1])[:8]:
            print(f"     {n:>5}  {f}")
        print(f"     ({sum(other.values())} lines in {len(other)} files)")

    report = scan()
    if not report:
        print("\n✅ no Korean left in any shipping file")
        return 0

    totals = {}
    for kinds in report.values():
        for k, n in kinds.items():
            totals[k] = totals.get(k, 0) + n
    grand = sum(totals.values())

    print(f"\n{len(report)} files still hold Korean · {grand} lines total")
    print("\n  by kind (the fix differs per kind):")
    for k in ("docs", "docstring", "comment", "string", "other"):
        if k in totals:
            print(f"     {k:<10} {totals[k]:>5}")

    print("\n  by file:")
    for f, kinds in sorted(report.items(), key=lambda x: -sum(x[1].values())):
        detail = " · ".join(f"{k} {n}" for k, n in sorted(kinds.items()))
        print(f"     {sum(kinds.values()):>5}  {f:<42} {detail}")

    print("\n" + "=" * 74)
    print(f"⛔ {grand} lines to go")
    return 1


if __name__ == "__main__":
    sys.exit(main())
