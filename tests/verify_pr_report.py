#!/usr/bin/env python3
"""★The pull-request rules can fail★ — the report, the review gate and CODEOWNERS, checked offline. (local · budget 0)

`.github/scripts/pr_report.py` decides what a pull request needs before it merges. A rule that cannot
fail is decoration, so every rule here is driven through a case that must be ★red★ next to one that
must be green.

  ① the roster: CODEOWNERS is exactly what `.github/governance.json` generates, every tracked file
     belongs to an area, and the report and GitHub agree on who owns each file
  ② impact: docs are low, measurement code is high, a removed check is high, a raised ratchet is high
  ③ the description: the bare template fails, a filled one passes, the bar rises with the impact
  ④ the title · ⑤ the sign-off (DCO) · ⑥ standard-library imports
  ⑦ the review gate: who counts, what blocks, what happens when the roster is too small
  ⑧ CI costs nothing: no workflow uses a paid API, a paid action or a paid runner, and the clean room
     never hands a check a paid key (the maintainers' decision, 2026-10-07)
  ⑨ the merge audit: reviewed per the rule, or a valid bypass record — and `Bypass: roster` covers only
     the gap, so it stops being possible by itself as the roster grows

    PYTHONPATH=. python3 tests/verify_pr_report.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
sys.path.insert(0, ROOT)

import pr_report as r  # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def tracked() -> list:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return [p for p in out.splitlines() if p]


GOV = r.load_governance()

print("① the roster")
with open(r.CODEOWNERS, encoding="utf-8") as fh:
    on_disk = fh.read()
check("CODEOWNERS is what governance.json generates", on_disk == r.codeowners(GOV),
      "run: python3 .github/scripts/pr_report.py --codeowners --write")
files = tracked()
orphans = [p for p in files if r.area_of(p, GOV) == "other"]
check("every tracked file belongs to an area", not orphans and bool(files),
      ("add to .github/governance.json: " + ", ".join(orphans[:5])) if orphans else "%d files" % len(files))
rules = r.parse_codeowners(on_disk)
disagree = [p for p in files if [w.lower() for w in r.codeowners_for(p, rules)]
            != [w.lower() for w in r.owners_of(r.area_of(p, GOV), GOV)]]
check("the report and GitHub's last-match rule pick the same owners for every file", not disagree,
      ", ".join(disagree[:5]))
check("every impact level is known", all(a["impact"] in r.LEVELS for a in GOV["areas"].values()))
check("every level has an approval rule", set(GOV["approvals"]) == set(r.LEVELS))
logins = r.roster(GOV)
check("roster logins look like GitHub logins", bool(logins) and all(
    __import__("re").match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$", w) for w in logins), " ".join(logins))
paths = [p for a in GOV["areas"].values() for p in a["paths"]]
check("no path is listed twice", len(paths) == len(set(paths)))
# control: an overlapping, longer path must win over its folder — or last-match and longest-match differ
g2 = {"maintainers": ["m"], "areas": {"docs": {"paths": ["docs/"], "owners": ["d"], "impact": "low"},
                                     "special": {"paths": ["docs/x.md"], "owners": ["s"], "impact": "high"}}}
rules2 = r.parse_codeowners(r.codeowners(g2))
check("control: a file inside an owned folder goes to the more specific owner, both ways",
      r.area_of("docs/x.md", g2) == "special" and r.codeowners_for("docs/x.md", rules2) == ["s", "m"]
      and r.codeowners_for("docs/y.md", rules2) == ["d", "m"])

print("\n② impact")
from brain import calibrate  # noqa: E402
with open(os.path.join(ROOT, "brain", "calibrate.py"), encoding="utf-8") as fh:
    rulers = r.ruler_modules(fh.read())
check("RULER_MODULES read as data equals the imported tuple", rulers == set(calibrate.RULER_MODULES),
      " · ".join(sorted(rulers)))
check("an unparsable calibrate.py yields no modules (and no crash)", r.ruler_modules("def (") == set())
CASES = [
    ("a docs change is low", [("M", "docs/ROADMAP.md")], "low"),
    ("a translation is low", [("M", "brain/locales/de.json")], "low"),
    ("a dashboard change is medium", [("M", "brain/dashview.py")], "medium"),
    ("⛔ measurement code is high", [("M", "brain/textindex.py")], "high"),
    ("⛔ CI is high", [("M", ".github/workflows/ci.yml")], "high"),
    ("⛔ removing a check is high", [("D", "tests/verify_i18n.py")], "high"),
    ("editing a check is medium", [("M", "tests/verify_i18n.py")], "medium"),
    ("the highest file decides", [("M", "README.md"), ("M", "brain/privacy.py")], "high"),
    ("an unknown file is medium, not low", [("A", "newthing.txt")], "medium"),
]
for label, changes, want in CASES:
    got = r.classify(changes, GOV, rulers)["impact"]
    check(label, got == want, "" if got == want else "got %s" % got)
up = r.classify([("M", r.RATCHET)], GOV, rulers, (112, 113))
down = r.classify([("M", r.RATCHET)], GOV, rulers, (112, 100))
check("⛔ raising the English ratchet is high; lowering it is not",
      up["impact"] == "high" and down["impact"] != "high", "%s / %s" % (up["impact"], down["impact"]))
check("a high impact says why", bool(r.classify([("M", "brain/search.py")], GOV, rulers)["reasons"]))

print("\n③ the description")
with open(os.path.join(ROOT, ".github", "pull_request_template.md"), encoding="utf-8") as fh:
    template = fh.read()
secs = r.parse_sections(template)
check("the template has every section the rules know", all(s in secs for s in r.SECTIONS), " · ".join(secs))
check("⛔ control: the bare template fails at every level",
      all(r.section_problems(template, lv) for lv in r.LEVELS))
filled = template
for s in r.SECTIONS:
    filled = filled.replace("## %s\n" % s, "## %s\nWritten by a person, 12 → 3 ms.\n" % s)
check("a filled template passes at every level", not any(r.section_problems(filled, lv) for lv in r.LEVELS),
      str([r.section_problems(filled, lv) for lv in r.LEVELS]))
low_only = "## Purpose\nfix a typo\n## Checks\nread it\n"
check("a docs change needs only Purpose and Checks", not r.section_problems(low_only, "low"))
check("⛔ …the same text is not enough for high impact", len(r.section_problems(low_only, "high")) == 3)
check("⛔ an unticked checkbox list alone is empty", bool(r.section_problems(
    "## Purpose\nx\n## Checks\n- [ ] \n- [x] \n", "low")))

print("\n④ the title")
for title, ok in [("stores: retry a lost connection once", True), ("README: what the AI era needs", True),
                  ("stores, tests: one fix", True), ("Fix stuff", False), ("stores:x", False),
                  ("stores: ", False), ("", False)]:
    got = not r.title_problem(title)
    check("%s %r" % ("passes" if ok else "⛔ fails", title), got == ok)

print("\n⑤ the sign-off (DCO)")
MAIL = "dev" + "@" + "example.invalid"          # assembled — a literal address would trip the scrub check
OTHER = "someone" + "@" + "example.invalid"
ok_c = {"sha": "a" * 40, "parents": ["p"], "email": MAIL, "message": "x: y\n\nSigned-off-by: Dev <%s>\n" % MAIL}
none_c = dict(ok_c, sha="b" * 40, message="x: y\n")
wrong_c = dict(ok_c, sha="c" * 40, message="x: y\n\nSigned-off-by: Someone <%s>\n" % OTHER)
merge_c = dict(none_c, sha="d" * 40, parents=["p", "q"])
case_c = dict(ok_c, sha="e" * 40, email=MAIL.upper())
check("a signed-off commit passes", not r.dco_problems([ok_c]))
check("⛔ a commit with no sign-off fails", len(r.dco_problems([none_c])) == 1)
check("⛔ a sign-off by someone else fails", len(r.dco_problems([wrong_c])) == 1)
check("a merge commit is exempt", not r.dco_problems([merge_c]))
check("the address compares without case", not r.dco_problems([case_c]))

print("\n⑥ standard-library imports")
if r.stdlib_names() is None:
    print("  ⏭ Python %s has no stdlib name list (3.10+) — CI runs this part" % sys.version.split()[0])
else:
    local = r.local_modules(files)
    check("brain, tests and sibling modules are ours", {"brain", "tests", "_needs", "pr_report"} <= local)
    check("a new stdlib import passes", not r.import_problems([("brain/x.py", "", "import sqlite3, json\n")], local))
    check("our own modules pass", not r.import_problems(
        [("tests/x.py", "", "from brain import store\nfrom tests import _needs\nfrom . import y\n")], local))
    check("⛔ a new third-party import fails", len(r.import_problems(
        [("brain/x.py", "", "import requests\n")], local)) == 1)
    check("⛔ …also as `from x import y` and inside a function", len(r.import_problems(
        [("brain/x.py", "", "def f():\n    from numpy import array\n")], local)) == 1)
    check("an import already there before the change is not new", not r.import_problems(
        [("brain/x.py", "import yaml\n", "import yaml\nimport os\n")], local))
    check("⛔ a file that does not parse fails", len(r.import_problems([("brain/x.py", "", "def (\n")], local)) == 1)

print("\n⑦ the review gate")
G = {"maintainers": ["m1", "m2"], "approvals": GOV["approvals"],
     "areas": {"a": {"owners": ["o1", "o2"], "paths": ["a/"], "impact": "high"}}}


def rv(who, state, t):
    return {"user": {"login": who}, "state": state, "submitted_at": "2026-10-07T00:00:%02dZ" % t}


def ok(impact, reviews, author="dev", gov=G):
    return r.gate(impact, reviews, gov, author)["ok"]


check("high: two approvals with a maintainer pass", ok("high", [rv("m1", "APPROVED", 1), rv("o1", "APPROVED", 2)]))
check("⛔ high: one approval is not enough", not ok("high", [rv("m1", "APPROVED", 1)]))
check("⛔ high: two owners but no maintainer is not enough",
      not ok("high", [rv("o1", "APPROVED", 1), rv("o2", "APPROVED", 2)]))
check("⛔ the author's own approval does not count", not ok("high", [rv("m1", "APPROVED", 1), rv("o1", "APPROVED", 2)],
                                                         author="o1"))
check("⛔ someone outside the roster does not count",
      not ok("high", [rv("m1", "APPROVED", 1), rv("stranger", "APPROVED", 2)]))
check("⛔ a later request for changes blocks",
      not ok("high", [rv("m1", "APPROVED", 1), rv("o1", "APPROVED", 2), rv("m2", "CHANGES_REQUESTED", 3)]))
check("⛔ an approval followed by a request for changes from the same person no longer counts",
      not ok("high", [rv("m1", "APPROVED", 1), rv("o1", "APPROVED", 2), rv("o1", "CHANGES_REQUESTED", 3)]))
check("a comment after an approval keeps the approval",
      ok("high", [rv("m1", "APPROVED", 1), rv("o1", "APPROVED", 2), rv("o1", "COMMENTED", 3)]))
check("⛔ a dismissed approval no longer counts",
      not ok("high", [rv("m1", "APPROVED", 1), rv("o1", "APPROVED", 2), rv("o1", "DISMISSED", 3)]))
check("low: one approval passes", ok("low", [rv("o1", "APPROVED", 1)]))
solo = {"maintainers": ["m1"], "approvals": GOV["approvals"], "areas": {}}
res = r.gate("high", [], solo, "m1")
check("⛔ a roster too small for the rule fails and says why (no quiet lowering)",
      not res["ok"] and any("roster" in l for l in res["lines"]), " / ".join(res["lines"]))

print("\n⑧ CI costs nothing")
WF = os.path.join(ROOT, ".github", "workflows")
flows = sorted(f for f in os.listdir(WF) if f.endswith((".yml", ".yaml")))
for name in flows:
    with open(os.path.join(WF, name), encoding="utf-8") as fh:
        found = r.paid_steps(fh.read())
    check("%s calls nothing that costs money" % name, not found, "; ".join(found))
check("there are workflows to look at", bool(flows), " · ".join(flows))
from tests import clean_room  # noqa: E402
leak = [k for k in clean_room.PASS_THROUGH if r.PAID_SECRET.search("secrets." + k)]
check("the clean room passes no paid key to a check", not leak, ", ".join(leak))
probe = {"BRAIN_TEST_QDRANT_URL": "x", "BRAIN_TEST_ANY_NEW_DB_URL": "x", "ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "x"}
check("…and its BRAIN_TEST_ prefix lets a new backend's address through, but no key",
      sorted(clean_room.passed(probe)) == ["BRAIN_TEST_ANY_NEW_DB_URL", "BRAIN_TEST_QDRANT_URL"]
      and not r.PAID_SECRET.search("secrets." + clean_room.PASS_PREFIX), str(sorted(clean_room.passed(probe))))
for label, text in [
        ("⛔ an AI review action", "    - uses: anthropics/claude-code-action@v1\n"),
        ("⛔ a paid key", "      api_key: ${{ secrets.ANTHROPIC_API_KEY }}\n"),
        ("⛔ another provider's key", "      env:\n        KEY: ${{ secrets.OPENAI_API_KEY }}\n"),
        ("⛔ a larger, paid runner", "    runs-on: ubuntu-latest-8-cores\n"),
        ("⛔ a paid runner in a matrix", "          - { os: macos-latest-xlarge, python: \"3.13\" }\n")]:
    check("control: %s is caught" % label, bool(r.paid_steps(text)))
check("free runners and other secrets pass",
      not r.paid_steps("    runs-on: ubuntu-22.04\n    runs-on: ${{ matrix.os }}\n          - { os: windows-latest }\n"
                       "      T: ${{ secrets.BRAIN_SCRUB_TERMS }}\n    runs-on: ubuntu-24.04-arm\n"))

print("\n⑨ the merge audit")


def note(who, text):
    return {"user": {"login": who}, "body": text}


SOLO = {"maintainers": ["m1"], "approvals": GOV["approvals"], "areas": {}}
DUO = {"maintainers": ["m1", "m2"], "approvals": GOV["approvals"], "areas": {}}
TRIO = {"maintainers": ["m1", "m2", "m3"], "approvals": GOV["approvals"], "areas": {}}
AUDIT = [
    ("reviewed per the rule needs no record", ("high", [rv("m2", "APPROVED", 1), rv("m3", "APPROVED", 2)], [], TRIO), True),
    ("today: the only maintainer authored it, `Bypass: roster` is valid",
     ("high", [], [note("m1", "Bypass: roster\nall checks green")], SOLO), True),
    ("⛔ no record at all fails", ("high", [], [], SOLO), False),
    ("⛔ a record by someone who is not a maintainer fails",
     ("high", [], [note("stranger", "Bypass: roster")], SOLO), False),
    ("⛔ an unknown reason fails", ("high", [], [note("m1", "Bypass: in a hurry")], SOLO), False),
    ("⛔ a second maintainer who did not approve makes `roster` invalid",
     ("high", [], [note("m1", "Bypass: roster")], DUO), False),
    ("…once they approve, `roster` covers only the missing second approval",
     ("high", [rv("m2", "APPROVED", 1)], [note("m1", "Bypass: roster")], DUO), True),
    ("⛔ with three maintainers `roster` can no longer cover a high-impact change",
     ("high", [rv("m2", "APPROVED", 1)], [note("m1", "Bypass: roster")], TRIO), False),
    ("⛔ an open request for changes is never bypassed as `roster`",
     ("high", [rv("m2", "CHANGES_REQUESTED", 1)], [note("m1", "Bypass: roster")], DUO), False),
    ("`Bypass: security` is valid (a review is still owed)", ("high", [], [note("m1", "Bypass: security")], DUO), True),
    ("the record can sit under other text, any case", ("low", [], [note("m1", "self-reviewed.\nbypass: Roster")], SOLO), True),
]
for label, (imp, reviews, comments, gov), want in AUDIT:
    got = r.audit(imp, reviews, comments, gov, "m1")
    check(label, got["ok"] == want, "%s — %s" % (got["how"], got["why"]))

print("\n" + "=" * 78)
if FAILS:
    print("❌ %d failure(s)" % len(FAILS))
    for f in FAILS:
        print("  · " + f)
    sys.exit(1)
print("✅ the pull-request rules hold, and each of them can fail")
sys.exit(0)
