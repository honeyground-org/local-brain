#!/usr/bin/env python3
"""Declared-trigger verification — ★lookup, not a score★ (user instruction 2026-08-19).

    "Don't hand a threshold a score — there needs to be a clearer query and a way to verify it."

What this measures
-----------
⓪ the reader — sees every declaration the indexer sees (needs no notes, so it runs in CI too)
① structural check (`brain triggers`) — uniqueness · resolves to its own doc · shadowing · char/word count
② ★end to end★ — actually runs `bin/brain-hook` and checks that phrase attaches that doc.
   ⛔ This check caught a real defect: the declaration was correct, but the hook's `MIN_PROMPT_CHARS`(12 chars) gate
      cut off `실행자 사다리 이어서`(11 chars)·`신원 예산 이어서`(9 chars) at the front and silenced them.
      A declaration doesn't need to pass the score world's heuristic — so the gate got moved ahead of it.
③ noise control group — a short phrase unrelated to any declaration ("안녕"·"커밋해줘") must stay quiet.

⛔ Frequency is not a verdict — a legitimate summons and noise can't be told apart by frequency alone (§triggers.audit).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from brain import store, triggers  # noqa: E402
from tests import _needs  # noqa: E402

NOISE = ["안녕", "커밋해줘", "고마워", "다시 해줘"]


def _hook(prompt: str) -> str:
    p = subprocess.run(_needs.entry("brain-hook", "brain.hook"),
                       input=json.dumps({"prompt": prompt}), capture_output=True,
                       text=True, timeout=30, encoding="utf-8", errors="replace")
    return p.stdout or ""


def main() -> int:
    _needs.private_index()                 # ⛔ measure on a copy — the hook reads the live index meanwhile
    # ⓪ ★the reader must see every declaration the indexer sees★ — a precondition for everything below.
    #
    #    2026-09-29: `declared_in_files()` read a fixed 2,000 bytes. A memory worked on for weeks grows
    #    a long `description:`, which pushes its `triggers:` line past that — one long project note sat
    #    at 3,583. The audit then reported ✅ while four declared phrases resolved to nothing, and
    #    `store.heal_triggers()` never repaired them, because it asks this same function what the files
    #    declare. ⛔ The check below (ⓞ) passed the whole time: ★the instrument and the thing it measures
    #    shared one blindness★, so the comparison `after == before` was true with both sides short.
    #
    #    2026-10-12: it read `^triggers:` only, while the indexer also takes the key indented under
    #    `metadata:` — the shape Claude Code writes a memory in. 8 rows were reported "only in the table"
    #    while their phrases fired every day. So the last row compares the two readers file by file.
    #
    #    ⛔ This needs no notes of anyone's, so it runs ★before★ the step that skips without them — it
    #       used to sit after it, and CI (an empty home) never once ran it.
    print("\n" + "=" * 78)
    print("⓪ the reader sees every declaration the indexer sees")
    print("=" * 78)
    import tempfile as _tf                                 # noqa: E402
    _real_dir = store.memory_dir
    with _tf.TemporaryDirectory() as _tmp:
        store.memory_dir = lambda: _tmp
        try:
            fixtures = {
                "deep_declaration": '---\nname: deep_declaration\ndescription: "%s"\n'
                                    'triggers: ["문구가 깊은 곳에 있다"]\nmetadata:\n  type: project\n---\n\nbody\n'
                                    % ("가" * 4000),       # ★well past 2,000 bytes★
                "nested_declaration": "---\nname: nested_declaration\ndescription: a note\nmetadata:\n"
                                      "  type: project\n  triggers: [\"pick the nested thread up\"]\n"
                                      "---\n\nbody\n",   # ★the shape Claude Code writes★
                "no_declaration": "---\nname: no_declaration\nmetadata:\n  type: project\n---\n\nbody\n",
                "unclosed_frontmatter": '---\nname: unclosed_frontmatter\n'
                                        'triggers: ["a block that never closes"]\n\nbody\n',
            }
            for name, text in fixtures.items():
                with open(os.path.join(_tmp, name + ".md"), "w", encoding="utf-8") as fh:
                    fh.write(text)
            seen = triggers.declared_in_files()
            deep_ok = "deep_declaration" in seen.values()
            nested_ok = "nested_declaration" in seen.values()
            ctrl_ok = not ({"no_declaration", "unclosed_frontmatter"} & set(seen.values()))
            pos = fixtures["deep_declaration"].find("\ntriggers:")
            print("  %s a phrase declared %d bytes in is seen"
                  % ("✅" if deep_ok else "❌ ★the reader is capped★", pos))
            print("  %s a phrase declared under `metadata:` is seen"
                  % ("✅" if nested_ok else "❌ ★the reader takes one shape only★"))
            # ⛔ control — a file that declares nothing, and one whose frontmatter never closes (the
            #    indexer reads no declaration there), must not be picked up, or the rows above would
            #    pass simply by reporting everything.
            print("  %s ★control★: a file with no declaration, or no closed frontmatter, is not reported"
                  % ("✅" if ctrl_ok else "❌"))
            differ = []
            for name in fixtures:
                doc = store.read_doc(os.path.join(_tmp, name + ".md"), "memory", _tmp) or {}
                by_indexer = {triggers.norm(t) for t in doc.get("triggers", [])}
                by_reader = {ph for ph, nm in seen.items() if nm == name}
                if by_indexer != by_reader:
                    differ.append(name)
            same_ok = not differ
            print("  %s the indexer and the reader agree on every file%s"
                  % ("✅" if same_ok else "❌", "" if same_ok else " — differ: " + ", ".join(differ)))
        finally:
            store.memory_dir = _real_dir
    reader_ok = deep_ok and nested_ok and ctrl_ok and same_ok
    if not reader_ok:
        print("\nverdict: short ❌ (reader FAIL)")
        return 1

    db = store.connect()
    _needs.memories(db)
    # ⓞ ★if the file and the table drift apart, does the check catch it, and does the index self-heal★ (a real 2026-08-19 incident)
    # If a long-lived MCP server is holding onto old code, that process's reindex knows nothing about the `triggers` table
    # at all — the doc gets indexed with no error, but the table alone stays empty. Then typing the phrase doesn't bring it up.
    # ⛔ At the time, this check ★passed vacuously★ (it read the table and got "0 phrases · all passed").
    print("\n" + "=" * 78)
    print("ⓞ if the table goes empty, is it caught, and does the index self-heal")
    print("=" * 78)
    before = len(triggers.declared_in_files())   # ★the baseline is the files★ (the table is derived)
    db.execute("DELETE FROM triggers")
    db.commit()
    wiped = triggers.audit(db)
    caught = bool(wiped.get("unwritten"))
    print("  %s the audit catches it when the table is emptied (%d unwritten)"
          % ("✅" if caught else "❌ ★passed vacuously★", len(wiped.get("unwritten", {}))))
    healed = store.reindex(db, recalibrate=False).get("triggers_healed", 0)
    after = len(triggers.declared(db))
    ok_heal = after == before and healed > 0
    print("  %s incremental indexing heals it (%d doc(s) reindexed → phrases %d → %d)"
          % ("✅" if ok_heal else "❌", healed, 0, after))
    res = triggers.audit(db)                      # carries the healed values into the checks below
    print(triggers.render(res))
    structural = not (res["duplicates"] or res["short"] or res["thin"]
                      or res["shadow"] or res["unresolved"] or res.get("unwritten")
                      or res.get("orphan"))

    print("\n" + "=" * 78)
    print("② end to end — actually runs the hook")
    bad = []
    for phrase, _did, name in res["declared"]:
        out = _hook(phrase)
        ok = name in out
        if not ok:
            bad.append((phrase, name))
        print("  %s %-34s → %s" % ("✅" if ok else "❌", phrase, name if ok else "didn't attach"))

    print("\n③ noise control group — a short phrase unrelated to any declaration must stay quiet")
    noisy = []
    for q in NOISE:
        out = _hook(q)
        if out.strip():
            noisy.append(q)
        print("  %s %s" % ("✅" if not out.strip() else "❌ attached", q))

    ok = (reader_ok and structural and caught and ok_heal
          and not bad and not noisy)
    print("\n" + "=" * 78)
    print("verdict: %s (reader %s · file↔table %s · end-to-end %d/%d · noise %d)"
          % ("pass ✅" if ok else "short ❌",
             ("OK" if reader_ok else "FAIL") + " · structural "
             + ("OK" if structural else "FAIL"),
             "OK" if (caught and ok_heal) else "FAIL",
             len(res["declared"]) - len(bad), len(res["declared"]), len(noisy)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
