#!/usr/bin/env python3
"""Tail-tracking verification — ★does incremental match a full scan★ + real firing rate + cost.

Why this test: `scan()` reads only from the offset onward. Without that optimization every turn
re-reads tens of MB, but with it, **a half-written line** or **a reset on save** can go quietly
wrong. Going quietly wrong is exactly where this project has been burned, again and again.

How to run:  PYTHONPATH=. python3 tests/verify_tail.py
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import store  # noqa: E402

# ⛔ ★No config on this machine (a fresh clone) → a fixture one★ (2026-10-06, clean room). The fallbacks
#    used to be `/tmp/...`, which is exactly the throwaway folder, so every "save" case read as "skip".
#    The fixture's paths are outside any temp folder and never touched — only the strings are classified.
if not store.load_config(tolerant=True).get("sources"):
    import tempfile as _tf
    _fx = os.path.join(_tf.mkdtemp(prefix="brain-tail-cfg-"), "config.json")
    with open(_fx, "w", encoding="utf-8") as _fh:
        json.dump({"sources": [
            {"name": "memory", "path": "/srv/brain-fixture/mem", "include": ["*.md"]},
            {"name": "docs", "path": "/srv/brain-fixture/docs", "include": ["*.md"]},
            {"name": "wiki", "path": "/srv/brain-fixture/wiki", "include": ["*.md"]},
            {"name": "project", "path": "/srv/brain-fixture/proj", "include": ["*.md"]}]}, _fh)
    os.environ["BRAIN_CONFIG"] = _fx
from brain import hosts, tail  # noqa: E402

# The fixture lines and the real transcripts below are Claude Code's log shape — the host reads its own
# log (§tail._apply), so this check says which host it is reading as.
hosts.pin("claude-code")

# ⛔ Never hardcode the fixture path to a personal environment — derive it from config. That way
#    this check ★checks the same thing★ even on someone else's freshly-cloned install (a wrong path passes silently).
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.environ.get("BRAIN_CONFIG", "").startswith(tempfile.gettempdir()) or HERE.startswith(
        os.path.realpath(tempfile.gettempdir())):
    HERE = "/srv/brain-fixture/proj/local-brain"     # a checkout outside any temp folder (fixture mode)


def _src(name, default=""):
    for s in tail._cfg().get("sources", []):
        if s.get("name") == name and s.get("path"):
            return os.path.expanduser(s["path"]).rstrip("/") + "/"
    return default


MEM = _src("memory", "/tmp/mem/")
DOCS = _src("docs", "/tmp/docs/")
WIKI = _src("wiki", "/tmp/wiki/")
PROJ = _src("project", "/tmp/proj/")

TRANSCRIPTS = os.path.expanduser("~/.claude/projects")
FAILS: list[str] = []


def fresh(sid="t") -> dict:
    return {"session_id": sid, "offset": 0, "tail": tail._blank_tail(),
            "saves": 0, "closed": False, "notified_at": None}


def full_scan(path: str) -> dict:
    return tail.scan(path, fresh())


def incremental_scan(path: str, chunks: int = 25, split_lines: bool = True) -> dict:
    """Appends a little at a time, line by line, scanning on each — mimics a real Stop call."""
    lines = open(path, errors="replace").readlines()
    tmpdir = tempfile.mkdtemp()
    tmp = os.path.join(tmpdir, "t.jsonl")
    rec = fresh()
    step = max(1, len(lines) // chunks)
    try:
        with open(tmp, "w") as f:
            for i in range(0, len(lines), step):
                block = lines[i:i + step]
                # ★actually creates a half-written line★: writes the last line halfway, then scans
                if split_lines and block and block[-1].endswith("\n"):
                    head = block[:-1]
                    last = block[-1]
                    f.write("".join(head))
                    f.write(last[:len(last) // 2])
                    f.flush()
                    rec = tail.scan(tmp, rec)
                    f.write(last[len(last) // 2:])
                    f.flush()
                else:
                    f.write("".join(block))
                    f.flush()
                rec = tail.scan(tmp, rec)
    finally:
        pass
    result = rec
    shutil.rmtree(tmpdir, ignore_errors=True)
    return result


def cmp_key(rec: dict) -> tuple:
    t = rec["tail"]
    return (t["code_writes"], t["commits"], tuple(t["files"]), rec["saves"])


# ── 1. incremental == full ────────────────────────────────────────────────────────
paths = sorted(glob.glob(os.path.join(TRANSCRIPTS, "*", "*.jsonl")),
               key=os.path.getsize, reverse=True)
sample = paths[:6] + paths[len(paths) // 2:len(paths) // 2 + 6] + paths[-6:]
print("★1. incremental scan == full scan  (sample of %d · big · medium · small)" % len(sample))
for p in sample:
    a, b = full_scan(p), incremental_scan(p)
    ok = cmp_key(a) == cmp_key(b)
    if not ok:
        FAILS.append("incremental≠full: %s\n   full %s\n   incremental %s"
                     % (os.path.basename(p), cmp_key(a), cmp_key(b)))
    print("  %s %-40s %6.1fMB  tail(code%d commits%d) saves%d"
          % ("✅" if ok else "❌", os.path.basename(p)[:38],
             os.path.getsize(p) / 1e6, a["tail"]["code_writes"],
             a["tail"]["commits"], a["saves"]))

# ── 2. does a save reset the tail to 0 ────────────────────────────────────
print("\n★2. does a memory save reset the tail")
cases = [
    ("write a memory file", {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": MEM + "x.md"}}]}}),
    ("write MEMORY.md", {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit",
         "input": {"file_path": "/somewhere/MEMORY.md"}}]}}),
    ("the remember tool", {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__brain__remember", "input": {}}]}}),
]
code_ev = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Edit", "input": {"file_path": "/proj/src/a.py"}}]}}
commit_ev = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": "git commit -m x"}}]}}
for label, save_ev in cases:
    d = tempfile.mkdtemp()
    tmp = os.path.join(d, "t.jsonl")
    with open(tmp, "w") as f:
        for ev in (code_ev, code_ev, code_ev, commit_ev, save_ev):
            f.write(json.dumps(ev) + "\n")
    r = full_scan(tmp)
    ok = r["tail"]["code_writes"] == 0 and r["tail"]["commits"] == 0 and r["saves"] == 1
    if not ok:
        FAILS.append("a save failed to reset the tail(%s): %s" % (label, r["tail"]))
    print("  %s %-18s → tail code%d commits%d · saved %d time(s)"
          % ("✅" if ok else "❌", label, r["tail"]["code_writes"],
             r["tail"]["commits"], r["saves"]))
    # working again after a save stands the tail back up
    with open(tmp, "a") as f:
        f.write(json.dumps(code_ev) + "\n")
        f.write(json.dumps(commit_ev) + "\n")
    r2 = tail.scan(tmp, r)
    ok2 = r2["tail"]["code_writes"] == 1 and r2["tail"]["commits"] == 1
    if not ok2:
        FAILS.append("the tail did not stand back up after a save(%s): %s" % (label, r2["tail"]))
    print("     %s re-worked after save → tail code%d commits%d"
          % ("✅" if ok2 else "❌", r2["tail"]["code_writes"], r2["tail"]["commits"]))
    shutil.rmtree(d, ignore_errors=True)

# ── 2-b. ★what gets counted★ — what is a tail, and what has already been saved ─────────────
print("\n★2-b. path classification (fixed by changing what gets counted, not the threshold)")
CLS = [
    (HERE + "/brain/tail.py", "code", "real code"),
    ("/somewhere/an-app/app/Models/X.php", "code", "real code"),
    ("/private/tmp/claude-501/x/scratchpad/probe.py", "skip", "a one-off measurement script"),
    ("/tmp/foo.py", "skip", "temp"),
    (DOCS + "domains/local-brain/README.md", "save",
     "★a domain document the brain indexes★"),
    (MEM + "lesson_x.md", "save", "a memory file"),
    (WIKI + "note.md", "save", "an indexed wiki"),
    (HERE + "/README.md", "save", "an indexed project document"),
    (PROJ + "an-app/x.md", "save", "an indexed project md"),
    ("/somewhere/else/random.md", "code", "an md outside the index is a tail"),
]
for raw, want, why in CLS:
    fp = os.path.expanduser(raw)
    got = tail._classify(fp)
    ok = got == want
    if not ok:
        FAILS.append("classification error: %s → %s (expected %s)" % (raw, got, want))
    print("  %s %-8s %-52s %s" % ("✅" if ok else "❌", got, raw, why))

# ── 2-c. ★the trigger is code writes alone★ (a false alarm caught on deploy day) ──────────
print("\n★2-c. a tail with only commits never fires (46% were shaped like this)")
TRIG = [
    ({"code_writes": 0, "commits": 1}, False, "saved a memory and committed it — nothing was made"),
    ({"code_writes": 0, "commits": 5}, False, "several commits, still nothing was made"),
    ({"code_writes": 3, "commits": 0}, True,  "3 code writes — made and not yet saved"),
    ({"code_writes": 2, "commits": 9}, False, "code under the threshold, no matter how many commits"),
    ({"code_writes": 9, "commits": 2}, True,  "real unsaved work"),
]
for t, want, why in TRIG:
    got = tail.is_substantive(t)
    ok = got == want
    if not ok:
        FAILS.append("trigger misjudged: code%d commits%d → %s (expected %s)"
                     % (t["code_writes"], t["commits"], got, want))
    print("  %s code%-2d commits%-2d → notify %-5s  %s"
          % ("✅" if ok else "❌", t["code_writes"], t["commits"], str(got), why))

if not paths:
    # Sections 3–4 measure ★this machine's own session logs★ — a fresh machine has none (clean room, 2026-10-06).
    print("\n⏭ 3–4 skipped — no session logs on this machine (they measure real sessions)")
else:
    # ── 3. real firing rate — of 163 sessions, how many would have notified ──────────────────
    print("\n★3. real firing rate across sessions (sweeps the whole set)")
    subst = live_guess = 0
    totals = {"code": 0, "commits": 0}
    t0 = time.time()
    for p in paths:
        r = full_scan(p)
        if tail.is_substantive(r["tail"]):
            subst += 1
            totals["code"] += r["tail"]["code_writes"]
            totals["commits"] += r["tail"]["commits"]
    print("  of %d sessions ★%d would notify (%.0f%%)★ — sessions that ended with nothing saved"
          % (len(paths), subst, 100 * subst / len(paths)))
    print("  sum across those tails: %d code write(s) · %d commit(s)"
          % (totals["code"], totals["commits"]))
    print("  (full scan of %d sessions, %.1fs)" % (len(paths), time.time() - t0))

    # ── 4. cost — the time Stop pays on every turn ─────────────────────────────
    print("\n★4. cost of one Stop call (incremental, so only newly written lines are read)")
    big = paths[0]
    rec = full_scan(big)                      # already fully read = the real steady state
    t0 = time.time()
    for _ in range(20):
        rec = tail.scan(big, rec)
    dt = (time.time() - t0) / 20 * 1000
    print("  %.1fMB transcript · %.2fms/call with no new lines  %s"
          % (os.path.getsize(big) / 1e6, dt, "✅" if dt < 20 else "❌ slow"))
    if dt >= 20:
        FAILS.append("Stop cost %.1fms — over the 20ms budget" % dt)

# ── 5. does it call a live session dead ──────────────────────────────
print("\n★5. protecting a live session (idle threshold %d min)" % (tail.IDLE_DEAD_SEC // 60))
d = tempfile.mkdtemp()
tmp = os.path.join(d, "live.jsonl")
open(tmp, "w").write(json.dumps(code_ev) + "\n")
alive = {"session_id": "alive", "transcript": tmp, "updated_at": int(time.time()),
         "closed": False, "tail": {"code_writes": 5, "commits": 0}}
dead_closed = dict(alive, session_id="closed", closed=True)
os.utime(tmp, (time.time() - 7200, time.time() - 7200))
stale = dict(alive, session_id="stale")
checks = [("just-active session", alive, False),
          ("session with SessionEnd", dead_closed, True),
          ("session quiet for 2 hours", stale, True)]
for label, rec_, want in checks:
    if label == "just-active session":
        os.utime(tmp, None)
    got = tail.looks_dead(rec_)
    ok = got == want
    if not ok:
        FAILS.append("dead-check error(%s): %s ≠ %s" % (label, got, want))
    print("  %s %-18s → judged dead: %s (expected %s)"
          % ("✅" if ok else "❌", label, got, want))
    if label == "session with SessionEnd":
        os.utime(tmp, (time.time() - 7200, time.time() - 7200))
shutil.rmtree(d, ignore_errors=True)

print("\n" + ("=" * 66))
if FAILS:
    print("❌ %d failure(s)" % len(FAILS))
    for f in FAILS:
        print("  · " + f)
    sys.exit(1)
print("✅ all passed")
