"""Behaviour-layer hook verification — ★the shell and the rules never drift into two copies★, and
★nothing speaks that is not this person's own★.

If the shell prefilter (bin/brain-guard) and the rules drift apart, growing the rules stops catching
silently. No error, no log — that is why this check exists.

⛔ It never reads this machine's rules or memories (2026-10-06). brain ships with no rules, so every
   check runs against a ★fixture home★ — its own rules.json, signal file and memory folder in a
   temporary directory. A check that passes only because the author's own rules happen to exist is
   a check that cannot fail anywhere else.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ⛔ The payloads below are Claude Code's shape (`Bash` · `Edit`) — so this check and every hook it spawns
#    speak as that host, not as "whichever host this machine has" (a clean machine has `generic`, which maps
#    no tools, and every rule went silent there: measured in a clean room, 2026-10-06).
os.environ["BRAIN_HOST"] = "claude-code"
from brain import guard, hosts, ruledisc  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "bin", "brain-guard")
ok = True


def check(label, cond, detail=""):
    global ok
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        ok = False


# ── the fixture home ────────────────────────────────────────────────────────
FIX = tempfile.mkdtemp(prefix="brain-guard-fixture-")
FHOME = os.path.join(FIX, "home")
FMEM = os.path.join(FIX, "memory")
FCFG = os.path.join(FIX, "config.json")
EMPTY = os.path.join(FIX, "empty-home")          # a stranger's first day: no rules at all
for _d in (FHOME, FMEM, EMPTY):
    os.makedirs(_d)
_NOTES = {
    "note_push_a": "pushing straight to the shared branch skipped the review twice",
    "note_push_b": "a push to an open pull request landed after it was merged",
    "note_infra": "the plan said no changes because the target address was wrong",
    "note_rebase": "a clean rebase still dropped a teammate's merged change",
    "note_ui": "the component edited was not the one rendered on the page",
}
for _n, _desc in _NOTES.items():
    with open(os.path.join(FMEM, _n + ".md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: %s\n---\n\nbody\n" % (_n, _desc))
FIXTURE_RULES = [
    {"id": "fx-push", "why": "pushes are hard to take back", "tools": ["run_shell"],
     "match": ["git push"], "memories": ["note_push_a", "note_push_b"], "once": "session"},
    {"id": "fx-infra", "why": "infrastructure plans can answer wrongly without an error",
     "tools": ["run_shell"], "match": ["terraform"], "memories": ["note_infra"], "once": "session"},
    {"id": "fx-rebase", "why": "a rebase can overwrite without a conflict", "tools": ["run_shell"],
     "match": ["git rebase"], "memories": ["note_rebase"], "once": "session"},
    {"id": "fx-ui", "why": "the edited file may not be the rendered one",
     "tools": ["edit_file", "write_file"], "match": [".tsx"], "memories": ["note_ui"],
     "once": "session"},
    # a signal that exists in no real home — so a check using it can only pass through the fixture
    {"id": "fx-unique", "why": "a signal only the fixture knows", "tools": ["run_shell"],
     "match": ["zzfixture-only-signal"], "memories": ["note_infra"], "once": "session"},
    # ⛔ a rule whose memories exist nowhere — it must stay ★completely★ silent
    {"id": "fx-ghost", "why": "GHOST-WHY a policy from somebody else's machine",
     "tools": ["run_shell"], "match": ["zzghost"], "memories": ["no_such_memory"],
     "once": "session"},
]
with open(FCFG, "w", encoding="utf-8") as fh:
    json.dump({"sources": [{"name": "memory", "path": FMEM, "include": ["*.md"],
                            "max_depth": 1}]}, fh)


@contextlib.contextmanager
def fixture_env(home=FHOME):
    """In-process: point brain at the fixture (home · config), and put everything back after."""
    saved = {k: os.environ.get(k) for k in ("BRAIN_HOME", "BRAIN_CONFIG")}
    sd, rp = guard.STATE_DIR, guard.RATES
    os.environ["BRAIN_HOME"], os.environ["BRAIN_CONFIG"] = home, FCFG
    guard.STATE_DIR = os.path.join(home, "guard")
    guard.RATES = os.path.join(home, "guard-rates.json")
    try:
        yield
    finally:
        guard.STATE_DIR, guard.RATES = sd, rp
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


with fixture_env():
    ruledisc.save({"version": 1, "learned": FIXTURE_RULES, "disabled": [], "dismissed": []})
    ruledisc.sync_shell()                    # ★the real generator★ writes the fixture's signal file

SHELL_TOOL = (hosts.tool_names("run_shell") or ["Bash"])[0]
EDIT_TOOL = (hosts.tool_names("edit_file") or ["Edit"])[0]


FAKE_HOME = os.path.join(FIX, "fake-user-home")             # $HOME with no brain in it
os.makedirs(FAKE_HOME)


def run_hook(payload: dict, compact: bool = True, home=FHOME, args=(), env_home=True,
             fake_home=False):
    # ⛔ Both forms are tested — a shell filter that depends on exact JSON formatting dies silently
    #    on one stray space (measured: a '"tool_name":"Bash"' check dropped a spaced payload whole).
    body = json.dumps(payload, separators=(",", ":")) if compact \
        else json.dumps(payload, indent=1)
    env = dict(os.environ, BRAIN_CONFIG=FCFG)
    env.pop("BRAIN_HOME", None)
    if fake_home:
        env["HOME"] = FAKE_HOME
    if env_home:
        env["BRAIN_HOME"] = home
    p = subprocess.run([HOOK] + list(args), input=body, capture_output=True, text=True, env=env)
    return p.stdout.strip()


print("=" * 72)
print("① brain ships no rules, and the shell holds no list of its own")
print("=" * 72)
check("★no built-in rules★ (guard.RULES is empty — a rule names one person's memories)",
      guard.RULES == [], "%d rule(s)" % len(guard.RULES))
sh = open(HOOK, encoding="utf-8").read()
# ⛔ Caught regardless of quoting — a check sensitive to formatting raises a false alarm, and once a
#    false alarm is seen once, the next real one is not believed either (actually happened 2026-08-18).
in_shell = set(re.findall(r"\*'([^']+)'\*", sh)) | \
           set(re.findall(r"\*([A-Za-z][A-Za-z. -]*)\*", sh))
check("★the shell holds no hand-written pattern list★ (its only list is the signal file)",
      not in_shell, "found: %s" % sorted(in_shell)[:6])
with fixture_env():
    got = set(open(ruledisc.signals_file(), encoding="utf-8").read().split("\n")) - {""}
    want = {m for r in ruledisc.all_rules() for m in r["match"]}
check("the signal file == the rules' match strings (a build artefact, not a copy)",
      got == want, "symmetric difference %s" % sorted(got ^ want) if got != want else "%d" % len(got))

print()
print("=" * 72)
print("② ★a stranger's first day★ — no rules, so nothing is said")
print("=" * 72)
for label, cmd in (("terraform plan", "terraform plan"), ("git push", "git push origin HEAD"),
                   ("git rebase", "git rebase origin/main")):
    out = run_hook({"tool_name": SHELL_TOOL, "session_id": "__vg_empty_%d" % os.getpid(),
                    "tool_input": {"command": cmd}}, home=EMPTY)
    check("an empty home stays silent on `%s`" % label, not out, "%d chars" % len(out))
_ed = run_hook({"tool_name": EDIT_TOOL, "session_id": "__vg_empty_e_%d" % os.getpid(),
                "tool_input": {"file_path": "/a/b/Card.tsx", "old_string": "a", "new_string": "b"}},
               home=EMPTY)
check("an empty home stays silent on editing a .tsx", not _ed, "%d chars" % len(_ed))
# (control) the same calls against a home that has rules — otherwise silence proves nothing
_ctl = run_hook({"tool_name": SHELL_TOOL, "session_id": "__vg_ctl_%d" % os.getpid(),
                 "tool_input": {"command": "terraform plan"}})
check("(control) the same call against the fixture's rules speaks", bool(_ctl), "%d chars" % len(_ctl))

print()
print("=" * 72)
print("③ it speaks only when it fires (control group included)")
print("=" * 72)
SID = "__verify_guard_%d__" % os.getpid()
cases = [
    ("git push fires", {"tool_name": SHELL_TOOL, "session_id": SID,
                        "tool_input": {"command": "git push origin HEAD"}}, True),
    ("an unrelated shell call stays quiet", {"tool_name": SHELL_TOOL, "session_id": SID + "b",
                                             "tool_input": {"command": "ls -la"}}, False),
    ("a different tool stays quiet", {"tool_name": "Read", "session_id": SID + "c",
                                      "tool_input": {"file_path": "/tmp/git push"}}, False),
    ("terraform fires", {"tool_name": SHELL_TOOL, "session_id": SID + "tf",
                         "tool_input": {"command": "terraform plan"}}, True),
    ("git rebase fires", {"tool_name": SHELL_TOOL, "session_id": SID + "rb",
                          "tool_input": {"command": "git rebase origin/main"}}, True),
    ("editing a .tsx fires", {"tool_name": EDIT_TOOL, "session_id": SID + "ui",
                              "tool_input": {"file_path": "/a/b/Card.tsx"}}, True),
    ("editing a .py stays quiet", {"tool_name": EDIT_TOOL, "session_id": SID + "py",
                                   "tool_input": {"file_path": "/a/b/thing.py"}}, False),
    # ⛔ control: the same string on a different tool must never fire (tool judging is Python's job)
    ("Read with .tsx still stays quiet", {"tool_name": "Read", "session_id": SID + "rt",
                                          "tool_input": {"file_path": "/a/b/Card.tsx"}}, False),
    # ⛔ a rule whose memories exist nowhere — matched, and still says ★nothing★ (not even its why)
    ("a rule with no memory here stays silent", {"tool_name": SHELL_TOOL, "session_id": SID + "gh",
                                                 "tool_input": {"command": "zzghost now"}}, False),
]
for label, payload, want in cases:
    out = run_hook(payload)
    check(label, bool(out) == want, "%d chars of output" % len(out))

print()
print("③-b ★does it behave the same with a different JSON formatting★ (a payload with spaces/indentation)")
for label, payload, want in cases:
    pay = dict(payload, session_id=(payload.get("session_id", "") + "-spaced"))
    out = run_hook(pay, compact=False)
    check("indented payload: " + label, bool(out) == want, "%d chars of output" % len(out))

with fixture_env():
    _marks = os.listdir(guard.STATE_DIR) if os.path.isdir(guard.STATE_DIR) else []
check("★a silent rule writes no 'already said' marker★ (it would spend the session budget for nothing)",
      not any(m.endswith(".fx-ghost") for m in _marks), "%d marker(s)" % len(_marks))
with fixture_env():
    _ghost = guard.render([r for r in FIXTURE_RULES if r["id"] == "fx-ghost"])
    _both = guard.render([r for r in FIXTURE_RULES if r["id"] in ("fx-ghost", "fx-infra")])
check("render: a rule with nothing resolved renders ★nothing★ — no why, no pointer",
      _ghost == "", repr(_ghost[:60]))
check("render: next to a speaking rule, the silent one still leaves no trace",
      "GHOST-WHY" not in _both and "note_infra" in _both, "%d line(s)" % len(_both.splitlines()))

print()
print("=" * 72)
print("④ is the output valid hook JSON, and ★was the directive sentence read from memory★")
print("=" * 72)
out = run_hook({"tool_name": SHELL_TOOL, "session_id": SID + "d",
                "tool_input": {"command": "git push"}})
try:
    d = json.loads(out)
    hs = d["hookSpecificOutput"]
    check("valid JSON · hookEventName=PreToolUse", hs["hookEventName"] == "PreToolUse")
    ctx = hs["additionalContext"]
    check("permissionDecision was not set", "permissionDecision" not in hs,
          "(overriding the permission decision defeats other gates)")
    check("both memory descriptions are the wording",
          _NOTES["note_push_a"] in ctx and _NOTES["note_push_b"] in ctx, ctx[:90].replace("\n", " "))
    check("the pointer names only memories that resolved", "note_push_a, note_push_b" in ctx)
    # ★Is the hook free of the sentence itself★ — it must not appear in the module's own source
    src = open(os.path.join(ROOT, "brain", "guard.py"), encoding="utf-8").read()
    check("the directive sentence is not hardcoded in the code", _NOTES["note_push_a"][:20] not in src)
except Exception as exc:                                     # noqa: BLE001
    check("JSON parsing", False, str(exc))
check("the rendered frame is ★not hardcoded★ — it comes from the message catalog",
      "There is guidance attached" not in src)

print()
print("=" * 72)
print("④-b does ★every rule★ actually fire (this check grows along with the rules)")
print("=" * 72)
for _r in FIXTURE_RULES:
    if _r["id"] == "fx-ghost":
        continue
    _pat = _r["match"][0]
    _tool = _r["tools"][0]
    # ⛔ ★A rule holds a canonical name and the hook receives a host name★ (2026-09-02) —
    #    an adapter bridges the two. A check writing `Bash` directly skips right past that seam.
    _real = (hosts.tool_names(_tool) or [_tool])[0]
    if _tool == "run_shell":
        _payload = {"tool_name": _real, "session_id": "%s_%s" % (SID, _r["id"]),
                    "tool_input": {"command": _pat + " something"}}
    else:
        _payload = {"tool_name": _real, "session_id": "%s_%s" % (SID, _r["id"]),
                    "tool_input": {"file_path": "/tmp/x/" + _pat, "old_string": "a",
                                   "new_string": "b"}}
    _out = run_hook(_payload)
    check("%-18s → fires" % _r["id"], bool(_out.strip()) and _r["why"][:12] in _out,
          _out[:120])

print()
print("=" * 72)
print("④-c ★the shell and Python read the same home★ (`--home` from the hook line)")
print("=" * 72)
# ⛔ Measured 2026-10-06: on a Codex-only machine the learning layer wrote `~/.codex/brain/guard-signals`
#    while the shell read `~/.claude/brain/guard-signals` — every learned rule silent, no error.
#    The installer now writes the home into the hook line; both halves must take it from there.
# ⛔ ★$HOME points at an empty folder and the signal exists only in the fixture★ — the first version
#    used `terraform`, and the shell ignoring `--home` still passed: it fell back to the real
#    `~/.claude/brain/guard-signals`, which on the author's machine happens to hold `terraform`.
# The payloads below are Claude Code's shape (`Bash` · `Edit`), so that is the host this check speaks as —
# not "whichever host this machine happens to have" (on a clean machine that is `generic`, which maps no tools).
_HOST = "claude-code"
_ARGS = ["--host", _HOST, "--home", FHOME]
_U = {"command": "run zzfixture-only-signal"}
_via = run_hook({"tool_name": SHELL_TOOL, "session_id": SID + "home", "tool_input": _U},
                args=_ARGS, env_home=False, fake_home=True)
check("with no BRAIN_HOME, `--home` reaches both halves (shell signal file + Python rules)",
      _NOTES["note_infra"][:20] in _via, "%d chars" % len(_via))
_eq = run_hook({"tool_name": SHELL_TOOL, "session_id": SID + "home2", "tool_input": _U},
               args=["--host", _HOST, "--home=%s" % FHOME], env_home=False,
               fake_home=True)
check("`--home=<path>` works the same", bool(_eq), "%d chars" % len(_eq))
_none = run_hook({"tool_name": SHELL_TOOL, "session_id": SID + "home4", "tool_input": _U},
                 args=["--host", _HOST], env_home=False, fake_home=True)
check("(control) without `--home` the same call is silent — so the row above is `--home`'s doing",
      not _none, "%d chars" % len(_none))
_win = run_hook({"tool_name": SHELL_TOOL, "session_id": SID + "home3", "tool_input": _U},
                home=EMPTY, args=_ARGS, fake_home=True)
check("★BRAIN_HOME still wins over `--home`★ (what a human set beats what an installer wrote)",
      not _win, "%d chars" % len(_win))
from brain import install as _inst                            # noqa: E402
_isrc = open(_inst.__file__, encoding="utf-8").read()
check("the installer writes `--home` into the guard's hook line", '--home %s' in _isrc)

print()
print("=" * 72)
print("⑤ once per session — never repeats the same line")
print("=" * 72)
sid = SID + "once"
a = run_hook({"tool_name": SHELL_TOOL, "session_id": sid, "tool_input": {"command": "git push"}})
b = run_hook({"tool_name": SHELL_TOOL, "session_id": sid, "tool_input": {"command": "git push"}})
check("round 1 speaks", bool(a))
check("round 2 stays quiet", not b)

print()
print("=" * 72)
print("⑤-b ★can the hook script itself even run★ (this check was missing on 2026-08-20 and it hurt)")
print("=" * 72)
# ⛔ Why this check exists — the shell prefilter was once ★auto-generated★ and a line continuation
#    was written as `\\` (two backslashes). sh reads that not as a continuation but as a literal,
#    breaking the `case` statement, and PreToolUse hook exit code 2 means ★block★, so every shell call
#    and edit was blocked — the hook had ★locked away its own path to being fixed★. A human had to
#    roll it back with git. Syntax must be checked before the file is written.
_sh = subprocess.run(["sh", "-n", HOOK], capture_output=True, text=True)
check("bin/brain-guard syntax OK (sh -n)", _sh.returncode == 0, (_sh.stderr or "").strip()[:120])
_live = run_hook({"tool_name": SHELL_TOOL, "session_id": SID + "shell",
                  "tool_input": {"command": "terraform plan"}})
check("the shell → Python path is alive", "additionalContext" in (_live or ""), (_live or "")[:80])

print()
print("=" * 72)
print("⑤-c ★does the judge avoid transcribing the rule table★ (index-audit --layers)")
print("=" * 72)
# Measured 2026-08-19: indexaudit held its own rule table (ACTION_SIGNALS), so it answered
# "the behaviour layer can take it" using ★8 rule names that did not exist★ (a false green). And in
# reverse, it called a seeder item that a real rule already carries "the topic layer" (a false red).
from brain import indexaudit as _ia                           # noqa: E402
check("the judge holds no hand-written rule table", not hasattr(_ia, "ACTION_SIGNALS"))
_iasrc = open(_ia.__file__, encoding="utf-8").read()
check("★the judge reads the rules the hook fires from★ (active_rules, not the empty built-in list)",
      "guard.RULES" not in re.sub(r"#.*", "", _iasrc) and "guard.active_rules()" in _iasrc)
with fixture_env():
    _carry = _ia._rules_carrying(["note_infra"])
check("…and sees the rules that live only in rules.json",
      sorted(r["id"] for r in _carry) == ["fx-infra", "fx-unique"],
      str([r["id"] for r in _carry]))

print()
print("=" * 72)
print("⑥ cost — does an uncaught shell call ever reach Python")
print("=" * 72)
import statistics as _stat
import time
# ⛔ ★An absolute millisecond limit measures this machine's mood, not this code★ (2026-09-10).
#    The row was `mean of 20 < 30ms`, then `median of 20 < 30ms`, and it still flipped: four
#    consecutive runs of the ★same code★ gave medians of 15.7, 27.7, 33.8 and 35.8ms, because
#    what dominates is process-spawn cost (12–78ms here), which the guard does not control.
#    A check that answers differently on identical code is the very defect this file exists for.
#
#    What the row actually means is ★"an uncaught call never reaches Python"★. So it is measured
#    against a ★control★: spawning a trivial process. If the guard costs about the same as that,
#    it exited in the shell; if it started an interpreter, the ratio jumps. Machine mood cancels.
_FENV = dict(os.environ, BRAIN_HOME=FHOME, BRAIN_CONFIG=FCFG)


def _median_ms(argv, stdin_text=None, n=20):
    ts = []
    for _ in range(n):
        _t0 = time.time()
        subprocess.run(argv, input=stdin_text, text=True, capture_output=True, env=_FENV)
        ts.append((time.time() - _t0) * 1000)
    return _stat.median(ts), min(ts), max(ts)

_BASE, _bmin, _bmax = _median_ms(["/bin/sh", "-c", "exit 0"])
_MISS = json.dumps({"tool_name": SHELL_TOOL, "session_id": SID + "cost",
                    "tool_input": {"command": "echo hello"}})
_UNCAUGHT, _umin, _umax = _median_ms([HOOK], _MISS)
# A call that ★does★ match is the second pole: it genuinely starts Python.
_HIT = json.dumps({"tool_name": SHELL_TOOL, "session_id": SID + "cost2",
                   "tool_input": {"command": "terraform plan"}})
_CAUGHT, _cmin, _cmax = _median_ms([HOOK], _HIT, n=8)

# ⛔ ★No magic constant.★ A ratio needed one (2.5× failed at exactly 2.5 on the third run), and any
#    constant here is really a guess about this machine. With ★both poles measured★ the question
#    answers itself: an uncaught call must sit nearer the bare process than the matching call does.
_to_bare, _to_python = _UNCAUGHT - _BASE, _CAUGHT - _UNCAUGHT
check("an uncaught shell call never pays for a Python start "
      "(nearer a bare process than a matching call)",
      _to_bare < _to_python,
      "bare %.1f → uncaught %.1f (+%.1f) → matched %.1f (+%.1f) ms"
      % (_BASE, _UNCAUGHT, _to_bare, _CAUGHT, _to_python))
check("(control) the two poles are actually apart — otherwise the row above proves nothing",
      _CAUGHT > _BASE * 2, "matched %.1fms vs bare %.1fms" % (_CAUGHT, _BASE))
per = _UNCAUGHT

shutil.rmtree(FIX, ignore_errors=True)

print()
print("=" * 72)
print("all passed ✅" if ok else "short ❌")

# ── ⑧ ★session budget★ (2026-08-31) ────────────────────────────────────────────
# ⛔ While rules went 12 → 33, the count shown per session went 8 → 15. This design's own written
#    warning is that exact state — "flood every session with guidance and a human ignores the whole layer".
#    ★Turning off a rule is not the answer★ (the knowledge is gone forever). Only how much shows is capped.
print("\n" + "="*72 + "\n⑧ session budget — what is known stays; only ★how much is shown★ is capped\n" + "="*72)
import json as _j
import tempfile as _tf

_home = os.environ.get("BRAIN_HOME")
_tmp = _tf.mkdtemp(prefix="brain-guardcap-")
try:
    os.environ["BRAIN_HOME"] = _tmp
    os.makedirs(os.path.join(_tmp, "guard"), exist_ok=True)
    _sd, _rp = guard.STATE_DIR, guard.RATES
    guard.STATE_DIR = os.path.join(_tmp, "guard")
    guard.RATES = os.path.join(_tmp, "guard-rates.json")

    # ★Without★ a firing-rate table, nothing is capped — never block on the unknown
    check("with no firing-rate table, ★no cap is applied★ (no value ≠ common)",
          guard.within_budget("S", "any-rule"))

    with open(guard.RATES, "w") as fh:
        _j.dump({"common": 6.0, "rare": 0.2}, fh)
    for i in range(guard.SESSION_CAP):
        open(os.path.join(guard.STATE_DIR, "S.filler%d" % i), "w").close()
    check("counts what has fired this session via markers",
          guard._session_spent("S") == guard.SESSION_CAP, "%d" % guard._session_spent("S"))
    check("past the budget, ★common rules are blocked★ (6%)",
          not guard.within_budget("S", "common"))
    check("★a rare rule passes even past budget★ (0.2%) — that moment carries more information",
          guard.within_budget("S", "rare"))
    check("a rule with an unknown firing rate is never passed past the budget (conservative)",
          not guard.within_budget("S", "unknown-rule"))
    check("★the budget is separate per session★ — another session is unaffected",
          guard.within_budget("OTHER", "common"))
finally:
    guard.STATE_DIR, guard.RATES = _sd, _rp
    if _home is None:
        os.environ.pop("BRAIN_HOME", None)
    else:
        os.environ["BRAIN_HOME"] = _home
    import shutil as _sh
    _sh.rmtree(_tmp, ignore_errors=True)

sys.exit(0 if ok else 1)
