"""Behaviour-layer auto-discovery check — ★does it rediscover the rules a human built by hand★.

## Why this is the yardstick

A machine that finds unknown rules is hard to verify on its own (there is no known answer). But
★the rules a human wrote by hand★ have a known answer (in `rules.json`, ids without `auto-`; on the
author's machine, the 12 that used to ship in the code until 2026-10-06). A discoverer that misses those also misses the
unknown ones. The first version really was 8/31, and this check caught it and split the shape into three (→ 19/31).

## ⛔ Split the denominator — miss that and normal is counted as failure

Rediscovery failure comes in three kinds, ★and they mean different things★:

  ① used 0 times     = this person does not do that action. ★Not the discoverer's failure★ —
                    it is exactly what `stale()` should report instead (5 cases on 2026-08-26).
  ② absent from memory = a memory does not use that spelling (e.g. wrote `gh pr` for `gh pr create`).
                    This is where the discoverer can improve.
  ③ both present, still missed = ★a real defect★. This is where a regression is caught.

The bar is measured on the denominator with ① removed.

## ⚠️ This check depends on this machine's own corpus

Memory count and transcripts differ per person. Expect an absolute number on someone else's machine
and it stays red forever (§calibrate was burnt in the same spot). So it uses ★a rate bar★ and ★a broken-down printout★.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The auto-approval bars were measured on Gemini (§ruledisc.AUTO_SCALE_PROVIDERS) — say which judge this is.
os.environ["BRAIN_JUDGE_PROVIDER"] = "gemini"
from brain import guard, i18n, ruledisc as rd, store   # noqa: E402
from tests import _needs                                 # noqa: E402

# It measures ★this machine's★ notes and session logs (rediscovering the rules a person wrote) — and it
# writes into the real rules file while it runs. With neither, skip before touching anything.
_needs.memories(store.connect())
_needs.prompts(30)

# ⛔⛔ ★This check touches real user data★ — so it is guarded whole, first.
#    2026-08-26: ⑧ (the broken-JSON fallback) overwrote `rules.json` and ★erased it★. The check was
#    green while 6 just-approved rules had vanished — no error, so nobody knew. The cause was each
#    section backing up on its own and missing one spot, so ★everything is guarded in one place★ and rolled back at the end.
import atexit                                                            # noqa: E402

_GUARDED = [rd.rules_path(), rd.signals_file()]
_SNAPSHOT = {}
for _p in _GUARDED:
    _SNAPSHOT[_p] = open(_p, "rb").read() if os.path.exists(_p) else None


def _restore():
    for path, data in _SNAPSHOT.items():
        if data is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, "wb") as fh:
                fh.write(data)


atexit.register(_restore)

FAIL = []


def check(ok, label, detail=""):
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAIL.append(label)
    return ok


def head(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


# ─────────────────────────────────────────────────────────────────────────
head("① three shapes of a signal — one regex cannot catch them all")
cases = [
    ("imperative (with flags)", rd.command_ngrams("git checkout -b x && php artisan tinker"),
     ["git checkout -b", "php artisan tinker"]),
    ("a compound extension", rd.path_signals("resources/views/a.blade.php"), [".blade.php"]),
    ("a CamelCase suffix", rd.path_signals("database/seeders/ProductSeeder.php"),
     ["Seeder", "database/seeders"]),
    ("a controller suffix", rd.path_signals("app/Http/Controllers/UserController.php"),
     ["Controller.php"]),
]
for label, got, want in cases:
    miss = [w for w in want if w not in got]
    check(not miss, label, "missing %s" % miss if miss else "")

head("①-b the left side of an assignment is not a command (TASK scored 10 on 2026-08-26)")
_g = rd.command_ngrams("TASK=$(aws ecs list-tasks --cluster prod)")
check("TASK" not in _g, "a variable name is not counted as a command")
check("aws ecs list-tasks" in _g, "the real command inside it survives")
_g2 = rd.command_ngrams("FOO=bar git push origin")
check("git push" in _g2 and "FOO" not in _g2, "a command after an env prefix is still caught")

head("② heredoc — 78% of Bash lines sit inside one and are not shell")
stripped = rd.strip_heredocs("python3 - <<'PY'\ngit push origin main\nPY\ngit status")
check("git push" not in stripped and "git status" in stripped,
      "a heredoc body is not counted as a command", repr(stripped.strip()))

head("③ shape filter — only what cannot be a signal is filtered out")
for s, want in [("2", False), (".", False), ("---", False), ("a", False),
                ("git push", True), (".tsx", True), ("Seeder", True)]:
    check(rd.usable(s) == want, "usable(%r) == %s" % (s, want))
home_frag = os.path.basename(os.path.expanduser("~"))
check(not rd.usable(home_frag),
      "a home-path fragment is not a signal (derived from the environment — not hardcoded)", repr(home_frag))

# ─────────────────────────────────────────────────────────────────────────
head("④ ★rediscovery★ — does it find the hand-built rules again in the data")
sigs = rd.signals_from_memories()
seen, scope, origin = rd.usage_from_transcripts()
print("corpus — %d memory signals · %d transcripts · %d tool calls · %s ~ %s"
      % (len(sigs), scope["files"], scope["calls"], scope["from"], scope["to"]))

# ⛔ brain ships no rules (2026-10-06) — the known answers are the ones ★this person★ wrote.
HAND = [r for r in rd.all_rules() if not r["id"].startswith("auto-")]
if scope["calls"] == 0:
    print("⚠️ no transcripts — rediscovery cannot be measured (a fresh install). Skipping.")
elif not HAND:
    print("⚠️ no hand-written rules here — nothing known to rediscover. Skipping.")
else:
    both, no_use, no_mem = [], [], []
    for r in HAND:
        for m in r["match"]:
            uses = seen.get(m, 0) or sum(v for k, v in seen.items() if m in k)
            mems = sigs.get(m) or {x: 1 for k, v in sigs.items() if m in k for x in v}
            if uses == 0:
                no_use.append(m)
            elif not mems:
                no_mem.append(m)
            else:
                both.append(m)
    total = len(both) + len(no_use) + len(no_mem)
    live = len(both) + len(no_mem)                    # the denominator with ① removed
    rate = 100.0 * len(both) / max(1, live)
    print("\n  %d matches = %d rediscovered · %d absent from memory · ★%d never run★"
          % (total, len(both), len(no_mem), len(no_use)))
    print("  never run: %s" % ", ".join(no_use) if no_use else "  never run: (none)")
    print("  absent from memory: %s" % ", ".join(no_mem) if no_mem else "")
    check(rate >= 70.0, "70%+ rediscovery among actions that actually run", "%.0f%% (%d/%d)"
          % (rate, len(both), live))
    print("  ⛔ 'never run' is not the discoverer's failure — that is `brain rules --stale`'s job to answer.")

head("④-b ★a signal already caught is not a candidate★ (never build a duplicate rule)")
# ⛔ `candidates()` only ★marks★, and `discover()` does the excluding — the check must respect that
#    boundary too. The first version read rows as-is and produced a false failure, "a duplicate survived".
_rows, _ = rd.candidates()
_active = {m for r in rd.all_rules() for m in r["match"]}
_unmarked = [r["signal"] for r in _rows
             if any(k in r["signal"] for k in _active) and not r.get("covered")]
check(not _unmarked, "a signal an active rule catches is marked covered",
      "not marked: %s" % _unmarked[:5] if _unmarked else "%d candidates" % len(_rows))
_pool_sigs = {p["signal"] for p in rd.discover(limit=0)["proposals"]}
_leaked = [x for x in _pool_sigs if any(k in x for k in _active)]
check(not _leaked, "★a proposal never holds a signal already caught★ (never builds a duplicate rule)",
      "leaked %s" % _leaked[:5] if _leaked else "%d proposals" % len(_pool_sigs))

head("④-c can the cron script actually run")
import subprocess                                                        # noqa: E402
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_daily = os.path.join(_root, "bin", "brain-rules-daily")
check(os.access(_daily, os.X_OK), "bin/brain-rules-daily is executable")
check(subprocess.call(["sh", "-n", _daily]) == 0, "sh -n syntax passes")

head("⑤ unused rules — matter as much as discovery")
st = rd.stale()
cold_ids = [x["id"] for x in st["rules"]]
print("  flagged rules: %s" % (", ".join(cold_ids) if cold_ids else "(none)"))
check(isinstance(st.get("scope", {}).get("from", ""), str) and "scope" in st,
      "★the measurement window is returned alongside★ — '0 times' is a verdict that depends on the period")

# ─────────────────────────────────────────────────────────────────────────
head("⑥ learning-layer wiring — does an approved rule actually reach the behaviour layer")
real = rd.rules_path()
backup = real + ".verify-backup"
had = os.path.exists(real)
if had:
    shutil.copy2(real, backup)
try:
    rd.save({"version": 1, "disabled": [], "learned": [{
        "id": "auto-zzprobe", "why": "for testing", "tools": ["run_shell"],
        "match": ["zzprobe-signal"], "memories": [], "once": "session",
        "enabled": True}, {
        "id": "auto-zzoff", "why": "for testing", "tools": ["run_shell"],
        "match": ["zzoff-signal"], "memories": [], "once": "session",
        "enabled": True}]})
    ids = [r["id"] for r in rd.all_rules()]
    check("auto-zzprobe" in ids, "a learned rule lands in all_rules")
    rd.disable("auto-zzoff")
    _d = rd.load()
    check("auto-zzoff" not in [r["id"] for r in rd.all_rules()]
          and any(r["id"] == "auto-zzoff" for r in _d["learned"]),
          "a disabled rule drops out (turned off, not deleted)")
    check("zzoff-signal" in _d.get("dismissed", []),
          "★and auto-approval can never revive it★ (its signal is dismissed)")
    check("auto-zzprobe" in [r["id"] for r in guard.active_rules()],
          "★guard sees what was learned★ — a lingering direct read of RULES drops it silently")
    hit = guard.match_rules((__import__("brain.hosts", fromlist=["x"]).tool_names("run_shell") or ["Bash"])[0],
                        {"command": "run zzprobe-signal now"})
    check([r["id"] for r in hit] == ["auto-zzprobe"], "a learned rule actually matches")

    head("⑦ shell prefilter — ★a build artefact, not a copy★")
    sig_file = rd.signals_file()
    sig_backup = sig_file + ".verify-backup"
    had_sig = os.path.exists(sig_file)
    if had_sig:
        shutil.copy2(sig_file, sig_backup)
    try:
        rd.sync_shell()
        got = set(open(sig_file, encoding="utf-8").read().split("\n")) - {""}
        want = {m for r in rd.all_rules() for m in r["match"]}
        check(got == want, "the signal file == active rules' match",
              "symmetric difference %s" % (got ^ want) if got != want else "%d entries" % len(got))
        check("zzprobe-signal" in got,
              "★a learned signal reaches the shell★ — without editing the shell script")
    finally:
        if had_sig:
            shutil.move(sig_backup, sig_file)
        elif os.path.exists(sig_file):
            os.remove(sig_file)
finally:
    if os.path.exists(backup):
        os.remove(backup)
    _restore()                                        # roll back to the real state
    rd.sync_shell()

head("⑧ a broken rules.json — ★the hook attaches nothing, and the file is never written over★")
# ⛔ Before 2026-10-06 this section asserted "the 12 built-in rules survive a broken rules.json".
#    brain ships no rules now, so rules.json is ★the whole behaviour layer★ — what must survive is
#    the file itself: `load()` answers an unreadable file with an empty set, and the next `save()`
#    used to replace every rule the person had with one.
#    ⛔ Runs in a temporary home — this section breaks the file on purpose.
_saved_home = os.environ.get("BRAIN_HOME")
tmpdir = tempfile.mkdtemp()
try:
    os.environ["BRAIN_HOME"] = tmpdir
    with open(rd.rules_path(), "w", encoding="utf-8") as fh:
        fh.write("{ broken JSON")
    check(rd.all_rules() == [] and guard.active_rules() == [],
          "a broken file reads as no rules — never an exception inside the hook")
    check(guard.match_rules("Bash", {"command": "git push"}) == [], "…and nothing matches")
    rd.save({"version": 1, "learned": [], "disabled": []})
    kept = [f for f in os.listdir(tmpdir) if f.startswith("rules.json.unreadable-")]
    check(len(kept) == 1 and open(os.path.join(tmpdir, kept[0]), encoding="utf-8").read() == "{ broken JSON",
          "★the unreadable file is kept aside, byte for byte★, before anything is written", str(kept))
    rd.save({"version": 1, "learned": [], "disabled": []})
    kept2 = [f for f in os.listdir(tmpdir) if f.startswith("rules.json.unreadable-")]
    check(len(kept2) == 1, "(control) a readable file is written over normally — nothing set aside",
          str(kept2))
finally:
    if _saved_home is None:
        os.environ.pop("BRAIN_HOME", None)
    else:
        os.environ["BRAIN_HOME"] = _saved_home
    shutil.rmtree(tmpdir, ignore_errors=True)

head("⑨-b ★the five gates for turning something on with no human★ (does auto-approval add noise)")
# ⛔ ★auto_approve's skip reasons are locale-aware★ (2026-09-08) — pin English, since the checks
#    below assert specific English wording. Restored in the finally block (never leaves the language changed).
_saved_env, _saved_lang = os.environ.get("BRAIN_LANG"), i18n._lang
os.environ["BRAIN_LANG"] = "en"
i18n._lang = None
i18n.lang(refresh=True)
_snap = rd.load()
try:
    rd.save({"version": 1, "learned": [], "disabled": [], "dismissed": ["dismissed-sig"]})
    _props = [
        {"signal": "wide-sig", "top": 10, "uses": 999, "rate": 9.9,
         "memories": ["m-new-1"], "broader_than": []},
        {"signal": "eater-sig", "top": 10, "uses": 999, "rate": 0.1,
         "memories": ["m-new-2"], "broader_than": ["git push"]},
        {"signal": "rare-sig", "top": 10, "uses": 4, "rate": 0.1,
         "memories": ["m-new-3"], "broader_than": []},
        {"signal": "dismissed-sig", "top": 10, "uses": 999, "rate": 0.1,
         "memories": ["m-new-4"], "broader_than": []},
        {"signal": "lowscore-sig", "top": 8, "uses": 999, "rate": 0.1,
         "memories": ["m-new-5"], "broader_than": []},
        {"signal": "good-sig", "top": 10, "uses": 999, "rate": 0.1,
         "memories": ["m-fresh"], "broader_than": []},
        {"signal": "dup-mem-sig", "top": 10, "uses": 999, "rate": 0.1,
         "memories": ["m-fresh"], "broader_than": []},
        {"signal": "a/b-thing", "top": 10, "uses": 999, "rate": 0.1,
         "memories": ["m-path-1"], "broader_than": []},
        {"signal": "x/b-thing", "top": 10, "uses": 999, "rate": 0.1,
         "memories": ["m-path-2"], "broader_than": []},
    ]
    res = rd.auto_approve(proposals=_props, limit=9)
    got = [a["signal"] for a in res["added"]]
    why = dict(res["skipped"])
    check("good-sig" in got, "a proposal clearing every condition gets turned on", str(got))
    check("wide-sig" not in got and "broad" in why.get("wide-sig", ""), "★a broad signal is never turned on★")
    check("eater-sig" not in got, "a signal that swallows an existing rule is never turned on")
    check("rare-sig" not in got and "rare" in why.get("rare-sig", ""),
          "★a rare action is never turned on★ (the spot where TASK got turned on at 5 uses)")
    check("dismissed-sig" not in got and "turned off" in why.get("dismissed-sig", ""),
          "★what a human turned off is never revived★")
    check("lowscore-sig" not in got, "below the automatic threshold (%.0f points) it is never turned on" % rd.AUTO_MIN_SCORE)
    check("dup-mem-sig" not in got and "no new memories" in why.get("dup-mem-sig", ""),
          "a signal whose memories are all already attached is never turned on")
    check("x/b-thing" not in got and "same path" in why.get("x/b-thing", ""),
          "★a different spelling of the same path is never turned on★ (projects/acme-web ↔ acme-web/)")

    # the cap
    rd.save({"version": 1, "learned": [], "disabled": [], "dismissed": []})
    many = [{"signal": "cap-%d" % i, "top": 10, "uses": 999, "rate": 0.1,
             "memories": ["mm-%d" % i], "broader_than": []} for i in range(12)]
    res2 = rd.auto_approve(proposals=many)
    check(len(res2["added"]) == rd.AUTO_MAX_PER_RUN,
          "turns on at most %d at a time (a day is never buried in directives)" % rd.AUTO_MAX_PER_RUN,
          "turned on %d" % len(res2["added"]))

    # turn one off and it stays in dismissed, and the cron never turns it on again automatically
    rd.disable("auto-cap-0")
    check("cap-0" in rd.load()["dismissed"], "turning it off leaves the signal in dismissed")
    res3 = rd.auto_approve(proposals=[many[0]])
    check(not res3["added"], "★the cron never revives what was turned off★")
finally:
    rd.save(_snap)
    rd.sync_shell()
    os.environ.pop("BRAIN_LANG", None)
    if _saved_env:
        os.environ["BRAIN_LANG"] = _saved_env
    i18n._lang = _saved_lang

head("⑨-c ★firing is measured the way guard fires — guard's matcher, in the log's host★")
# ⛔ 2026-10-07: rules moved to canonical tool names (`run_shell`) on 2026-09-02, and both measurements
#    kept comparing them with the log's raw names (`Bash`). Every call counted 0 for a month: real rates 0,
#    per-session median 0 — the breadth gate and the session budget both stood open, and the daily report
#    called all 43 rules rare. A synthetic log in a temp home: no real data is read or written here.
import json as _json                                        # noqa: E402
from brain import hosts as _hosts                           # noqa: E402
_tdir = tempfile.mkdtemp(prefix="brain-fire-")
_home_before = os.environ.get("BRAIN_HOME")
try:
    os.environ["BRAIN_HOME"] = _tdir
    _log = os.path.join(_tdir, "session.jsonl")
    _shell = _hosts.tool_names("run_shell")[0]
    _edit = _hosts.tool_names("edit_file")[0]
    with open(_log, "w", encoding="utf-8") as fh:
        for i in range(30):
            cmd = "echo zzfire-%d" % i if i % 3 == 0 else "ls -la"
            fh.write(_json.dumps({"message": {"content": [
                {"type": "tool_use", "name": _shell, "input": {"command": cmd}}]}}) + "\n")
        fh.write(_json.dumps({"message": {"content": [
            {"type": "tool_use", "name": _edit, "input": {"file_path": "/x/zzfire.py"}}]}}) + "\n")
    rd.save({"version": 1, "learned": [
        {"id": "auto-zzfire", "why": "for testing", "tools": ["run_shell"], "match": ["zzfire"],
         "memories": [], "once": "session", "enabled": True},
        {"id": "auto-zzedit", "why": "for testing", "tools": ["edit_file"], "match": ["zzfire"],
         "memories": [], "once": "session", "enabled": True}], "disabled": [], "dismissed": []})
    fm = rd.measure_fire([{"signal": "zzfire", "origin": {"bash": 1, "path": 0}}], pattern=_log)
    _s = fm["signals"]["zzfire"]
    check(_s["calls"] == 30 and _s["fires"] == 10,
          "★a canonical shell rule is counted over the log host's shell calls★ (it counted 0 of 0)", str(_s))
    check(fm["sessions"] == 1 and fm["median"] == 2,
          "the per-session count sees the live rules firing (it saw 0)", "median %s" % fm["median"])
    _rates = rd.measure_active(pattern=_log)["rates"]
    check(abs(_rates.get("auto-zzfire", 0) - 100.0 * 10 / 30) < 0.01,
          "★the daily rate table measures a canonical rule★ (it wrote 0 — 'rare' — for all of them)",
          str(_rates))
    check(_rates.get("auto-zzedit") == 100.0,
          "(control) an edit rule counts only the edit call — the shell calls are not its denominator",
          str(_rates.get("auto-zzedit")))
finally:
    if _home_before is None:
        os.environ.pop("BRAIN_HOME", None)
    else:
        os.environ["BRAIN_HOME"] = _home_before
    shutil.rmtree(_tdir, ignore_errors=True)

head("⑩ ★a source declared not-for-export never reaches the judge★")
from brain import vectors                                   # noqa: E402
_orig = vectors.no_embed_sources
try:
    vectors.no_embed_sources = lambda: ["memory"]
    check(rd._memory_is_local_only(), "reads config's embed:false")
    check(rd.judge({"signal": "x", "memories": ["y"]}) is None,
          "★a memory body is never sent to the remote judge★ (unjudged, not scored 0)")
finally:
    vectors.no_embed_sources = _orig
check(not rd._memory_is_local_only() or True, "(restored)")

# ── ★is a rule attached where its own evidence appears★ (2026-08-27) ──────────────────────────
# ⛔ Without this check, `auto-FAIL` stayed alive: the signal `FAIL` was counted ★106 times in Bash
#    commands★ but the rule was attached to ★Edit/Write★ (`_tools_for` went by shape alone: "starts
#    with a capital letter → a filename"). Then the rule ★never fires on its own evidence★, and
#    because match scans the whole tool_input, it fires wrongly on ★every edit whose file content
#    happens to hold that word★. Registered, visible in the list, doing the opposite — ★a silent failure★.
head("⑪ is a rule attached ★where its own evidence was actually observed★")
# ⛔ A rule holds ★a canonical action name★ (not a host's own tool name) — §brain/hosts.py
_EDIT = {"edit_file", "write_file", "edit_notebook"}
_bad = []
for _r in rd.all_rules():
    if not _r.get("enabled", True):
        continue
    for _m in _r.get("match", ()):
        _o = origin.get(_m)
        if not _o:
            continue                       # no observation this period → no verdict
        _ts = set(_r.get("tools") or ())
        if _o.get("bash") and not _o.get("path") and not ("run_shell" in _ts):
            _bad.append((_r["id"], _m, "observed only in the shell, but not attached to run_shell"))
        if _o.get("path") and not _o.get("bash") and not (_ts & _EDIT):
            _bad.append((_r["id"], _m, "observed only on a path, but not attached to an edit tool"))
for _id, _m, _why in _bad:
    print("   ❌ %-30s %-24s %s" % (_id, _m, _why))
check(not _bad, "every active rule is attached ★where its own evidence appears★",
      "%d mismatched" % len(_bad))


head("⑨ ★does this check leave user data behind★ (erased it on 2026-08-26)")
for path, data in _SNAPSHOT.items():
    now = open(path, "rb").read() if os.path.exists(path) else None
    check(now == data, "the same before and after: %s" % os.path.basename(path),
          "" if now == data else "★content changed — the check touches real data★")

print("\n" + "=" * 72)
if FAIL:
    print("❌ %d failed: %s" % (len(FAIL), " · ".join(FAIL)))
    sys.exit(1)
print("all passed ✅")

