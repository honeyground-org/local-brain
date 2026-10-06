#!/usr/bin/env python3
"""★index-audit reads the person's own index, in its own language★ — not the author's headings. (local · budget 0)

## What this replaced (2026-10-06)

`index-audit` found its sections by three of the author's own Korean headings and the English word
`recall`, treated a linked memory as a directive only if its kind
was `feedback`, and read "when / always" with Korean markers alone. On anyone else's index:
  · the English seed's four sections had ★no role★, so nothing was kept as a directive by section
  · a corpus whose rules are called anything but `feedback` had no directive kind at all
  · every condition check landed in "undecidable" — the markers were in another language
and the size warning was English outside the catalogs, naming the author's checkout path.

## What this holds down — on a fixture corpus built from ★the seed that ships★

  ① roles come from the heading's marker (`<!-- brain: directives -->`), else config
     `index_sections`; a marker wins; an undeclared heading has no role
  ② the verdict follows the role — a directives item is kept, a progress item is never kept even when
     it links a directive kind
  ③ directive kinds: config `directive_kinds` (an explicit `[]` means none), else the host's own format
     (Claude Code → `feedback`, a host without one → nothing)
  ④ condition words in the corpus's language: English is read on an English corpus — whole words only,
     code is not prose, and ⛔ another language's words do not leak in (`SI` is not French `si`)
  ⑤ the size warning — from the catalog, follows the screen language, names `brain index-audit`,
     states bytes only when they differ from characters

How to run:  PYTHONPATH=. python3 tests/verify_index_roles.py
Controls (run by hand on a copy, 2026-10-06 — each turned the named check red): the marker ignored ·
config ignored · config read before the marker · kinds fixed to `feedback` · `progress` falling through
to kinds · no word boundary · code read as prose · every language at once · bytes always stated ·
an explicit `[]` falling back to the host.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-iaroles-")
_MEM = os.path.join(_TMP, "memory")
_SAVED = {k: os.environ.get(k) for k in ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_HOST", "BRAIN_LANG")}
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")

from brain import calibrate, hosts, i18n, indexaudit as ia, store  # noqa: E402

FAILS: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", name, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


NOTES = [
    ("rule_full_suite_before_push", "rule", "Run the full test suite before every push to the shared branch."),
    ("rule_no_force_push", "rule", "Never force-push the shared branch; rewrite history only on your own."),
    ("rule_si_export_path", "rule", "The SI export runs from the dashboard, not from the API."),
    ("rule_flag_names", "rule", "Flag names: the old flag and the new flag are not interchangeable."),
    ("decision_billing_owner", "decision", "Billing code belongs to the payments team; ask before changing it."),
    ("note_weekly_sync", "note", "Weekly sync notes live in the shared drive under the team folder."),
]


def _seed_index() -> str:
    """The shipped seed, with one person's items under its own headings."""
    with open(os.path.join(ROOT, "brain", "templates", "MEMORY.seed.md"), encoding="utf-8") as fh:
        seed = fh.read()
    add = {
        "<!-- brain: directives -->": [
            "- ⛔ **Before pushing**, run the full suite — [suite](rule_full_suite_before_push.md)",
            "- ⛔ **Never** force-push the shared branch — [no force](rule_no_force_push.md)",
            "- ⛔ Run the SI export from the dashboard, not the API — [si](rule_si_export_path.md)",
            "- ⛔ The `if` flag differs from `--before` — [flags](rule_flag_names.md)",
        ],
        "<!-- brain: signals -->": [
            "- When touching billing code → recall first — [billing](decision_billing_owner.md)",
        ],
        "<!-- brain: progress -->": [
            "- Moving the suite to the new runner — [suite](rule_full_suite_before_push.md)",
        ],
        "## 🧠 How to find a memory": [
            "- no role, links a rule — [force](rule_no_force_push.md)",
            "- no role, links a note — [sync](note_weekly_sync.md)",
        ],
    }
    lines = seed.split("\n")
    out = []
    for line in lines:
        out.append(line)
        for key, items in add.items():
            if line.startswith("## ") and key in line:
                out.append("")
                out.extend(items)
                out.append("")
    return "\n".join(out)


def _cfg(extra: dict) -> None:
    d = {"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"],
                      "exclude": ["MEMORY.md", "INDEX.de.md"], "max_depth": 1}]}
    d.update(extra)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def _with_lang(code: str, fn):
    saved = i18n._lang
    os.environ["BRAIN_LANG"] = code
    i18n._lang = None
    try:
        return fn()
    finally:
        os.environ.pop("BRAIN_LANG", None)
        i18n._lang = saved


def main() -> int:
    os.makedirs(_MEM)
    for name, kind, body in NOTES:
        with open(os.path.join(_MEM, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: %s\nmetadata:\n  type: %s\n---\n\n%s\n"
                     % (name, body, kind, body))
    index = os.path.join(_MEM, "MEMORY.md")
    with open(index, "w", encoding="utf-8") as fh:
        fh.write(_seed_index())
    de_index = os.path.join(_MEM, "INDEX.de.md")
    with open(de_index, "w", encoding="utf-8") as fh:
        fh.write("# Index\n\n## Ständige Anweisungen\n\n- immer prüfen — [s](rule_no_force_push.md)\n\n"
                 "## Aktuelle Arbeit\n\n- Umzug — [s](rule_full_suite_before_push.md)\n\n"
                 "## Ständige Anweisungen, aber umgewidmet <!-- brain: progress -->\n\n"
                 "- x — [s](rule_no_force_push.md)\n")
    full_cfg = {"directive_kinds": ["rule"],
                "index_sections": {"directives": ["Ständige Anweisungen"], "progress": ["Aktuelle Arbeit"]}}
    _cfg(full_cfg)
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)

    print("=" * 72 + "\n★① roles — the heading's marker, else config★\n" + "=" * 72)
    items = ia.parse(index)
    by_line = {it.raw.split(" — ")[0][:40]: it for it in items}
    roles = {it.section[:28]: it.role for it in items}
    check("the shipped seed's sections carry roles",
          {"directives", "signals", "progress"} <= set(roles.values()), str(roles))
    check("an undeclared heading has no role (`How to find a memory`)",
          any(s.startswith("🧠 How to find") and r == "" for s, r in roles.items()), str(roles))
    check("the marker is not part of the section text", all("brain:" not in it.section for it in items))
    de = {it.section: it.role for it in ia.parse(de_index)}
    check("config `index_sections` gives roles to headings in another language",
          de.get("Ständige Anweisungen") == "directives" and de.get("Aktuelle Arbeit") == "progress", str(de))
    check("a marker in the heading wins over config",
          de.get("Ständige Anweisungen, aber umgewidmet") == "progress", str(de))

    print("\n★② the verdict follows the role★")
    res = ia.audit(index)
    verdict = {r["item"].raw.split(" — ")[0][:40]: r["verdict"] for r in res["rows"]}
    sel = lambda pre: next((v for k, v in verdict.items() if k.startswith(pre)), None)  # noqa: E731
    check("a directives item is kept", sel("- ⛔ **Before pushing**") == "directive-kept",
          str(sel("- ⛔ **Before pushing**")))
    check("⛔ a progress item is never kept — even linking a directive kind",
          sel("- Moving the suite") not in (None, "directive-kept"), str(sel("- Moving the suite")))
    check("no role + a directive kind (`rule`) → kept", sel("- no role, links a rule") == "directive-kept",
          str(sel("- no role, links a rule")))
    check("no role + another kind (`note`) → judged by recall",
          sel("- no role, links a note") not in (None, "directive-kept"), str(sel("- no role, links a note")))
    check("signals: kept through the kind it links (`decision` is not one) → judged by recall",
          sel("- When touching billing") not in (None, "directive-kept"), str(sel("- When touching billing")))

    print("\n★③ directive kinds — config, else the host★")
    check("config `directive_kinds` is read", ia.directive_kinds({"directive_kinds": ["rule"]}) == ["rule"])
    check("an explicit `[]` means none (not the host's)", ia.directive_kinds({"directive_kinds": []}) == [])
    hosts.pin("claude-code")
    check("Claude Code's own format → `feedback`", ia.directive_kinds({}) == ["feedback"],
          str(ia.directive_kinds({})))
    hosts.pin("generic")
    check("a host without such a kind → nothing", ia.directive_kinds({}) == [], str(ia.directive_kinds({})))
    hosts.pin("claude-code")
    _cfg({})
    res2 = ia.audit(index)
    v2 = {r["item"].raw.split(" — ")[0][:40]: r["verdict"] for r in res2["rows"]}
    k = next(x for x in v2 if x.startswith("- no role, links a rule"))
    check("⛔ without config, a `rule` note is not a directive for Claude Code (its word is `feedback`)",
          v2[k] != "directive-kept", v2[k])
    _cfg(full_cfg)

    print("\n★④ condition words in the corpus's language★")
    langs = calibrate.corpus_languages(db)
    check("the fixture corpus reads as English", langs == ["en"], str(langs))
    cond = ia.classify_by_condition(index)
    layer = {r["item"].raw.split(" — ")[0][:40]: r for r in cond["rows"]}
    get = lambda pre: next(v for k, v in layer.items() if k.startswith(pre))  # noqa: E731
    check("`Before pushing` → conditional", get("- ⛔ **Before pushing**")["layer"].startswith("conditional"),
          str(get("- ⛔ **Before pushing**")["conds"]))
    check("`When touching` (signals section) → conditional",
          get("- When touching billing")["layer"].startswith("conditional"))
    check("`Never` → unconditional", get("- ⛔ **Never**")["layer"].startswith("unconditional"),
          str(get("- ⛔ **Never**")["uncond"]))
    flags = get("- ⛔ The `if` flag")
    check("⛔ whole words only, and code is not prose (`differs` · `if` · `--before`)",
          not flags["conds"], str(flags["conds"]))
    si = get("- ⛔ Run the SI export")
    check("⛔ another language's word does not leak in (`SI` is not French/Spanish `si`)",
          not si["conds"], str(si["conds"]))
    check("the progress section is not in the condition list",
          not any(k.startswith("- Moving the suite") for k in layer))
    check("the screen states which languages were read",
          "en" in ia.render_conditions(cond).split("\n")[2])
    real = calibrate.corpus_languages
    try:
        calibrate.corpus_languages = lambda _db: ["de"]
        de_cond = ia.classify_by_condition(de_index)
        check("a German corpus reads German (`immer` → unconditional)",
              any(r["uncond"] == ["immer"] for r in de_cond["rows"]),
              str([(r["conds"], r["uncond"]) for r in de_cond["rows"]]))
    finally:
        calibrate.corpus_languages = real

    print("\n★⑤ the size warning★")
    hosts.pin("claude-code")          # the read limit is the host's — measured on Claude Code (§hosts)
    big = os.path.join(_TMP, "BIG.md")
    with open(big, "w", encoding="utf-8") as fh:
        fh.write("- plain ascii line\n" * 2000)
    note = _with_lang("en", lambda: ia.size_notice(big))
    check("it fires past the size and names the file", note.startswith("⚠️ BIG.md is"), note[:40])
    check("it names `brain index-audit`, not a checkout path",
          "`brain index-audit`" in note and "local-brain/" not in note)
    check("⛔ no bytes when they equal the characters (no Hangul lecture)",
          "bytes" not in note and "Hangul" not in note)
    with open(big, "w", encoding="utf-8") as fh:
        fh.write("- Prüfung über Größe\n" * 1200)
    note = _with_lang("en", lambda: ia.size_notice(big))
    check("bytes are stated when they differ", " bytes — " in note, note.split("\n")[0])
    note_ko = _with_lang("ko", lambda: ia.size_notice(big))
    hosts.pin("codex")
    check("⛔ a host with no measured read limit warns nothing", ia.size_notice(big) == "")
    hosts.pin("claude-code")
    ko_bytes = i18n.catalog("ko")["ia.size.bytes"].split("{bytes}")[1][:12]
    check("it follows the screen language", ko_bytes in note_ko, note_ko.split("\n")[0])

    db.close()
    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAILS), " · ".join(FAILS)) if FAILS
          else "✅ all passed — index-audit reads this person's index, in its own language")
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
