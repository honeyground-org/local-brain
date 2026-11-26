#!/usr/bin/env python3
"""The reference lane (§brain/lane.py) and where a ledger answers (§brain/team.py learn). (local · budget 0)

What this holds down:

  ① the lane is an index of its own: the main index never holds a ledger document, and the lane's threshold is
     set by its own probes — a sentence about nothing in particular is not offered, a question about an item is,
     and naming an item's id or alias is a lookup that passes whatever its score
  ② a ledger answers only where its work happens: the project its file lives in, never an unrelated project and
     never the workspace that holds every project
  ③ ★nobody has to write that list★ — a freshly cloned repository whose sessions work on the ledger's file is
     learned from the host's session logs and answered there; a project that touched the file once is not, and
     neither is the workspace above the ledger's project however often it touched it
  ④ the hook offers the lane's answer in a block of its own that says it is for reference only

How to run:  PYTHONPATH=. python3 tests/verify_lane.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-lane-")
HOME = os.path.join(_TMP, "home")
CLAUDE = os.path.join(_TMP, "claude")
CONFIG = os.path.join(_TMP, "config.json")
NOTES = os.path.join(_TMP, "notes")
WS = os.path.join(_TMP, "ws")                       # a workspace holding many repositories
APP = os.path.join(WS, "app")                       # the ledger's project (not a repository of its own)
CLONE = os.path.join(WS, "clone")                   # a repository cloned later, where the work moved
OTHER = os.path.join(WS, "other")                   # an unrelated repository
os.environ["BRAIN_HOME"] = HOME
os.environ["BRAIN_CONFIG"] = CONFIG
os.environ["CLAUDE_HOME"] = CLAUDE
for d in (HOME, NOTES, os.path.join(APP, "plan"), os.path.join(WS, ".git"), os.path.join(CLONE, ".git"),
          os.path.join(CLONE, "src"), os.path.join(OTHER, ".git"), os.path.join(CLAUDE, "projects", "p")):
    os.makedirs(d, exist_ok=True)

from brain import hook, hosts, store, team  # noqa: E402

hosts.pin("claude-code")       # the session logs below are Claude Code's (§hosts.ClaudeCode.sessions)

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


WORK = os.path.join(APP, "plan", "work.json")
ledger = {"items": [
    {"id": "W1", "title": "feeding alert does not fire at night", "status": "waiting",
     "next": "draft the interval chips, then ask", "links": {"tracker": ["task:D7"]},
     "steps": [{"what": "read the scheduler", "status": "done", "result": "the scheduler skips nights"}]},
    {"id": "W2", "title": "growth card checkup row", "status": "doing", "next": "add the checkup date"},
    {"id": "W3", "title": "sleep log export", "status": "done"},
] + [{"id": "W%d" % (10 + i), "title": t, "status": "done", "next": n} for i, (t, n) in enumerate([
    ("diaper count chart", "colour the weekly bars"), ("vaccination reminder", "move the date picker"),
    ("photo album sharing", "invite a second parent"), ("bath time timer", "add a sound"),
    ("milk stock list", "sort by expiry"), ("pediatrician visit notes", "attach the scan"),
    ("first words diary", "export to a book"), ("tooth chart", "mark the molars"),
    ("weight percentile line", "use the national table"), ("stroller walk map", "save the route"),
    ("lullaby playlist", "loop the last song"), ("allergy record", "flag eggs"),
])]}
with open(WORK, "w", encoding="utf-8") as fh:
    json.dump(ledger, fh)
with open(os.path.join(NOTES, "a_note.md"), "w", encoding="utf-8") as fh:
    fh.write("---\ndescription: how the nightly build is cut\ntype: reference\n---\nThe nightly build is cut at two.\n")


def write_config(folders=None) -> dict:
    led = {"name": "app", "items": WORK, "alias_fields": ["links.tracker"]}
    if folders is not None:
        led["folders"] = folders
    cfg = {"sources": [{"name": "memory", "path": NOTES, "include": ["*.md"], "embed": False}],
           "team": {"capture": True, "hook": False, "reference": True, "ledgers": [led]}}
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    return cfg


def session(n: int, cwd: str, text: str) -> None:
    """One Claude Code session log: every line carries the session's cwd, as the host writes it."""
    lines = [{"type": "user", "cwd": cwd, "sessionId": "s%d" % n, "message": {"role": "user", "content": "go on"}},
             {"type": "assistant", "cwd": cwd, "sessionId": "s%d" % n,
              "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}]
    with open(os.path.join(CLAUDE, "projects", "p", "s%d.jsonl" % n), "w", encoding="utf-8") as fh:
        for o in lines:
            fh.write(json.dumps(o, separators=(",", ":")) + "\n")


QUESTION = "why does the feeding alert not fire at night"
NOTHING = "the weather was nice and we went for a walk"

try:
    print("① a lane of its own")
    cfg = write_config()
    m = team.materialize(force=True)
    lane_m = m.get("lane") or {}
    check("the lane is built from the ledger", lane_m.get("docs") == len(ledger["items"]), json.dumps(m))
    from brain import calibrate, lane
    info = lane.info(team.lane_path())
    check("its threshold is set by its own probes, never below the main floor",
          (info.get("threshold") or 0) >= calibrate.FLOOR_MIN and info.get("floor") is not None, json.dumps(info)[:200])
    db = store.connect()
    store.reindex(db)
    n_team = db.execute("SELECT count(*) FROM docs WHERE source='team'").fetchone()[0]
    db.close()
    check("the main index holds no ledger document", n_team == 0, str(n_team))
    rows = team.reference(QUESTION, cwd=APP, cfg=cfg)
    check("a question about an item is offered that item", rows and rows[0]["rel"].endswith("W1.md"),
          str([r["rel"] for r in rows]))
    check("a sentence about nothing in particular is not offered", team.reference(NOTHING, cwd=APP, cfg=cfg) == [])
    rows = team.reference("what about D7", cwd=APP, cfg=cfg)
    check("naming an alias is a lookup", rows and rows[0]["rel"].endswith("W1.md") and rows[0].get("named"),
          str([(r["rel"], r.get("named")) for r in rows]))
    check("a document's path is written with / on every system", all("\\" not in r["rel"] for r in rows))

    print()
    print("② only where the work is")
    check("the ledger's own project answers", bool(team.reference(QUESTION, cwd=os.path.join(APP, "plan"), cfg=cfg)))
    check("an unrelated repository does not", team.reference(QUESTION, cwd=OTHER, cfg=cfg) == [])
    check("the workspace that holds every project does not", team.reference(QUESTION, cwd=WS, cfg=cfg) == [])
    check("a repository the work has not reached yet does not", team.reference(QUESTION, cwd=CLONE, cfg=cfg) == [])

    print()
    print("③ learned, not written down")
    session(1, os.path.join(CLONE, "src"), "updated app/plan/work.json: W1 next step")
    session(2, CLONE, "read %s" % WORK)
    session(3, CLONE, "W2 done, app/plan/work.json saved")
    session(4, CLONE, "ran the tests")
    session(5, OTHER, "looked at app/plan/work.json once")
    for i in range(6, 11):
        session(i, WS, "app/plan/work.json")
    m = team.materialize(force=True)
    check("the cloned repository is learned (3 of its 4 sessions worked on the ledger)",
          (m.get("folders") or {}).get("app") == [CLONE], json.dumps(m.get("folders")))
    check("the cloned repository now answers", bool(team.reference(QUESTION, cwd=os.path.join(CLONE, "src"), cfg=cfg)))
    check("a repository that touched the file once still does not", team.reference(QUESTION, cwd=OTHER, cfg=cfg) == [])
    check("the workspace still does not, though all its sessions touched the file",
          team.reference(QUESTION, cwd=WS, cfg=cfg) == [])
    check("what was learned is kept, not re-read on every prompt", team.learn(cfg).get("unchanged") is True)
    hosts.ClaudeCode.sessions, real = (lambda self, needles=(), recent_files=300: []), hosts.ClaudeCode.sessions
    try:
        check("a host that cannot say where a session worked learns nothing",
              team.learn(cfg, force=True).get("learned") == {"app": []})
    finally:
        hosts.ClaudeCode.sessions = real
    team.learn(cfg, force=True)
    cfg = write_config(folders=[OTHER])
    check("a folder the person names still answers", bool(team.reference(QUESTION, cwd=OTHER, cfg=cfg)))

    print()
    print("④ offered apart, for reference only")
    hook._PAYLOAD = {"cwd": CLONE}
    state = {"t0": __import__("time").time(), "t": {}}
    block = hook._format_reference(hook._reference(QUESTION, state))
    check("the hook's block says it is for reference only", block.startswith("[brain · reference]"), block[:80])
    check("and names the item", "W1" in block, block[:200])
    hook._PAYLOAD = {"cwd": WS}
    check("nothing is offered in the workspace", hook._format_reference(hook._reference(QUESTION, state)) == "")
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print()
print("FAIL: %s" % FAILS if FAILS else "PASS")
sys.exit(1 if FAILS else 0)
