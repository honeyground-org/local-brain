#!/usr/bin/env python3
"""★Somebody else's first day★ — install into a clean machine, in their language, with no labels. (local · budget 0)

## Why this exists (§7-b of the public-release plan)

Every other check runs ★here★: this author's machine, this author's language, this author's labelled
sample. All three are exactly what a new user does not have. The pieces were each covered somewhere —
`verify_install` installs into a fake HOME, `verify_i18n` swaps the language, `verify_guard_reaches_others`
hides the sample — but ★nothing ran all three at once★, which is the only combination a stranger
actually meets.

This is that run: a clean HOME, a language the author does not use, and no evaluation labels at all.

## ⛔ What it is really looking for

Not "does it crash". The failures that matter on a first day are ★quiet★ ones:

  · a screen that speaks the ★developer's★ language because a string was hardcoded
  · a directive that points at ★memories that only exist on the author's machine★
    (the 2026-09-10 release blocker: a dangling "call recall with this name" with nothing above it)
  · a calibration guard that ★claims a protection it does not have★, or stays silent about having none
  · a first run that looks fine and has quietly deployed an unmeasured ruler

⛔ It never touches the real HOME, the real database or the real config: everything is a temporary
   directory, and `BRAIN_HOME` · `BRAIN_CONFIG` · `HOME` · `USERPROFILE` all point inside it.

How to run:  PYTHONPATH=. python3 tests/verify_first_day.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HANGUL = re.compile(r"[가-힣]")

# Enough documents for the label-free proxy sample to form. ⛔ ★It takes about twice `MIN_A`★, not
# `MIN_A`: not every document yields a usable unique fragment (measured on this fixture, ~53%), so
# 45 varied notes produced only 24 pairs and the guard correctly stayed at `none`. That number is
# worth knowing outside this file — ★the label-free guard switches on at roughly 60 documents★, and
# below that a new user is told plainly that they are unprotected.
N_DOCS = 70
# ⛔ ★Every note has to be genuinely different★ — the first fixture repeated one paragraph in all 45
#    notes, the proxy builder deduplicates identical fragments, and fewer than 30 pairs survived. The
#    guard then reported `none` and the check read that as a product failure. A corpus of clones is
#    not a corpus; a fixture that is uniform measures deduplication, not the thing under test.
_SUBJECTS = ["the payment retry", "the nightly export", "the search index", "the webhook receiver",
             "the invoice PDF", "the session cache", "the image resizer", "the audit log",
             "the rate limiter", "the signup form", "the CSV importer", "the price feed",
             "the refund job", "the email digest", "the sitemap builder"]
_EVENTS = ["timed out after nine seconds", "double-charged eleven accounts",
           "silently dropped the last page", "returned yesterday's numbers",
           "ran twice on the same row", "held a lock for four minutes",
           "wrote to the wrong shard", "skipped every row with a null owner",
           "kept a stale token for six hours"]
_CAUSES = ["the migration had already run", "the cursor was not stable under ties",
           "the cache returned a different shape", "the worker was restarted mid-batch",
           "the threshold was measured on another corpus", "the retry had no jitter",
           "the flag defaulted to on", "the clock was in the wrong zone"]


def _body(i: int) -> str:
    """⛔ ★Every sentence must be unique to its note.★ The second fixture varied only the opening
    line and left the rest as shared boilerplate; the proxy builder picks a random sentence and
    deduplicates, so 45 notes yielded 17 pairs and the guard fell back to `none`. Templated notes
    are a real shape (plenty of teams write them) — but a fixture made of them measures
    deduplication, not the first day.
    """
    subj, ev, cause = _SUBJECTS[i % 15], _EVENTS[i % 9], _CAUSES[i % 8]
    # ⛔ Each ends with a full stop — `proxy._fragment` splits on `.` and drops anything over 160
    #    characters, so a body with no punctuation is ★one over-long sentence★ and yields nothing.
    #    (Third attempt. The first was uniform, the second was templated, this one is neither.)
    return " ".join(x + "." for x in [
        "On the %dth %s %s and the cause was that %s" % (i % 28 + 1, subj, ev, cause),
        "Ticket %d was opened by the on-call and closed %d hours later without a code change"
        % (4100 + i * 7, i % 9 + 2),
        "The first attempt moved %s from %d to %d and the behaviour did not move with it"
        % (subj, i * 3 + 5, i * 3 + 41),
        "Rerunning it on shard %d showed %d affected rows against %d the day before"
        % (i % 6 + 1, i * 37 + 14, i * 31 + 9),
        "The condition that finally held was written as a check named guard_%02d_%s"
        % (i, subj.split()[-1]),
        "Noticed after %d minutes and rolled back in %d because the alert watched queue depth %d"
        % (i % 19 + 3, i % 23 + 4, i * 13 + 7),
    ])


FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def _machine(tmp: str, n_docs: int) -> str:
    """A clean HOME with somebody else's notes in it — nothing of this author's."""
    home = os.path.join(tmp, "home")
    mem = os.path.join(home, ".claude", "projects", "-someone-else", "memory")
    os.makedirs(mem, exist_ok=True)
    topics = ["deploy", "rollback", "queue worker", "schema change", "alert threshold",
              "cache key", "retry budget", "index rebuild", "feature flag"]
    for i in range(n_docs):
        t = topics[i % len(topics)]
        with open(os.path.join(mem, "note_%02d.md" % i), "w", encoding="utf-8") as fh:
            fh.write("---\nname: note-%02d\ndescription: a note about %s\n---\n\n# %s %d\n\n%s\n"
                     % (i, t, t, i, _body(i) + " " + _body(i + 7)))
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    with open(os.path.join(home, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    return home


def _env(home: str, lang_env: dict) -> dict:
    env = dict(os.environ)
    for k in ("BRAIN_LANG", "LC_ALL", "LANG", "BRAIN_CONFIG", "BRAIN_HOME"):
        env.pop(k, None)
    env.update({"HOME": home, "USERPROFILE": home, "PYTHONPATH": ROOT,
                "BRAIN_HOME": os.path.join(home, ".claude", "brain"),
                "BRAIN_CONFIG": os.path.join(home, ".claude", "brain", "config.json"),
                # ⛔ no key, no network — the first day must not depend on a paid service
                "BRAIN_EMBED_PROVIDER": "stub",
                # ⛔ ★A stranger does not have this author's repository★ — and until 2026-09-10 the
                #    evaluation directory was a plain repo-relative constant, so a "clean machine"
                #    run reached back into `<this repo>/tests/eval` and the guard cheerfully
                #    reported `labelled`. That is the product bug this check found (§evalinit.eval_dir);
                #    pointing the variable inside the fake machine is what makes the simulation honest.
                "BRAIN_EVAL_DIR": os.path.join(home, ".claude", "brain", "eval")})
    env.update(lang_env)
    return env


def _py(env: dict, code: str, timeout: int = 240):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, cwd=ROOT, timeout=timeout)


def first_day(tmp: str, label: str, lang_env: dict, n_docs: int = N_DOCS) -> None:
    print("\n" + "─" * 78)
    print("★%s★  %s" % (label, " ".join("%s=%s" % kv for kv in sorted(lang_env.items())) or "(no language set)"))
    print("─" * 78)
    home = _machine(tmp, n_docs)
    env = _env(home, lang_env)
    mem = os.path.join(home, ".claude", "projects", "-someone-else", "memory")

    # ── install ──────────────────────────────────────────────────────────────
    # ⛔ ★a real install★ (2026-10-06) — this used to pass flags the installer does not have
    #    (`--yes --no-mcp --source`), fail on argparse, and quietly fall back to `--dry-run`: "the
    #    installer runs to completion" had never installed anything. Now it installs, with the guard,
    #    and nothing falls back. The host CLI is left off PATH so the MCP step stays a printed line.
    ienv = dict(env, PATH=os.pathsep.join(
        d for d in env.get("PATH", "").split(os.pathsep)
        if not os.path.exists(os.path.join(d, "claude"))))
    r = subprocess.run([sys.executable, "-m", "brain.install", "--with-guard"],
                       capture_output=True, text=True, env=ienv, cwd=ROOT, timeout=300)
    check("the installer runs to completion on a clean machine", r.returncode == 0,
          "exit %d%s" % (r.returncode, (" · " + (r.stderr or "")[-90:]) if r.returncode else ""))

    # ── index their notes (not ours) ─────────────────────────────────────────
    idx = _py(env, "\n".join([
        "import json, os",
        "from brain import store",
        "cfg = {'language': %r, 'sources': [{'name':'memory','path':%r,'include':['*.md'],'max_depth':1}]}"
        % (lang_env.get("BRAIN_LANG", ""), mem),
        "os.makedirs(os.path.dirname(store.default_config_path()), exist_ok=True)",
        "open(store.default_config_path(),'w').write(json.dumps(cfg))",
        "db = store.connect()",
        "st = store.reindex(db, cfg, full=True, recalibrate=True)",
        # ⛔ what is ★in the index★, not what this call added — the installer indexed them first
        "print(json.dumps({'docs': store.corpus_stats(db)['docs']}))",
    ]))
    got = re.search(r'\{"docs": (\d+)\}', idx.stdout or "")
    n_indexed = int(got.group(1)) if got else 0
    check("their own notes get indexed", n_indexed >= n_docs - 2,
          "%d of %d%s" % (n_indexed, n_docs, (" · " + (idx.stderr or "")[-90:]) if not got else ""))

    # ── the calibration guard must tell the truth about which sample it has ──
    g = _py(env, "\n".join([
        "import json",
        "from brain import calibrate, store",
        "db = store.connect()",
        "s = calibrate.guard_status(db)",
        "print(json.dumps({'kind': s.get('kind'), 'protected': s.get('protected'),",
        "                  'why': (s.get('why') or '')[:200]}, ensure_ascii=False))",
    ]))
    try:
        gs = json.loads((g.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        gs = {}
    check("the guard names which sample it is on — never 'labelled' on a machine with no labels",
          gs.get("kind") in ("proxy", "none"), "kind=%s · protected=%s" % (gs.get("kind"), gs.get("protected")))
    if n_docs >= N_DOCS:
        y = _py(env, "from brain import proxy, store\nd=store.connect()\n"
                     "print('YIELD=%d/%d' % (len(proxy.positives(d)), store.corpus_stats(d)['docs']))")
        check("★with a real corpus and no labels it is still protected★ (the label-free proxy)",
              gs.get("kind") == "proxy" and gs.get("protected") is True,
              "kind=%s · %s" % (gs.get("kind"), (y.stdout or "").strip()))
    check("…and it says so in a sentence, not just a flag", len(gs.get("why") or "") > 20,
          (gs.get("why") or "")[:70])

    # ── the screens must speak ★their★ language, not the author's ────────────
    want_ko = lang_env.get("BRAIN_LANG") == "ko"
    want = lang_env.get("BRAIN_LANG") or "en"
    got = (_py(env, "from brain import i18n; print(i18n.lang())").stdout or "").strip()
    check("the language is the one they chose, else ★English★ — never their OS locale", got == want,
          "%s (wanted %s)" % (got, want))
    # ⛔ `score` and `dashboard` too (2026-10-06) — both died with a traceback on a first day (no labelled
    #    sample yet), and this list held only `status` and `budget`, so nothing caught it.
    for cmd, name in (("status", "brain status"), ("budget", "brain budget"), ("score", "brain score"),
                      ("dashboard','--no-open", "brain dashboard")):
        out = _py(env, "import sys; sys.argv=['brain','%s']\nfrom brain import cli; cli.main()" % cmd)
        text = (out.stdout or "") + (out.stderr or "")
        leak = bool(HANGUL.search(text)) and not want_ko
        check("%-13s renders" % name, out.returncode == 0 and len(text) > 40,
              "exit %d · %d chars" % (out.returncode, len(text)))
        check("%-13s has no ★Korean leaking into a non-Korean screen★" % name, not leak,
              (HANGUL.search(text).group() + " …") if leak else "clean")

    # ── the behaviour layer: ★nothing of the author's speaks here★ ──────────
    # ⛔ Until 2026-10-06 brain shipped twelve rules naming the author's stack and memories, and this
    #    block asserted the opposite of what it does now ("a built-in directive still says its why").
    #    On a stranger's machine that why was another person's policy — `terraform plan` on an empty
    #    brain answered "apply is forbidden". A first day has ★no rules★, so the hook says nothing.
    gd = _py(env, "from brain import guard, ruledisc\n"
                  "print('BUILTIN=%d ACTIVE=%d' % (len(guard.RULES), len(ruledisc.all_rules())))")
    o = (gd.stdout or "").strip()
    check("no rules on a first day — none ship with brain", o == "BUILTIN=0 ACTIVE=0", o or gd.stderr[-90:])
    sp = os.path.join(home, ".claude", "settings.json")
    try:
        wired = [x["command"] for e in json.load(open(sp, encoding="utf-8"))["hooks"]["PreToolUse"]
                 for x in e.get("hooks", []) if "guard" in x.get("command", "")]
    except (OSError, ValueError, KeyError):
        wired = []
    check("the installer wired the guard hook, with the home in the line",
          bool(wired) and "--home" in wired[0], (wired[0][-70:] if wired else "(none)"))

    def _hook(cmd_text):
        body = json.dumps({"tool_name": "Bash", "session_id": "first-day",
                           "tool_input": {"command": cmd_text}})
        return subprocess.run(wired[0], shell=True, input=body, capture_output=True, text=True,
                              env=env, timeout=60)
    if wired:
        for cmd_text in ("terraform plan", "git push origin main", "git rebase origin/main"):
            hr = _hook(cmd_text)
            check("★`%s` attaches nothing★ — no other person's policy here" % cmd_text,
                  hr.returncode == 0 and not hr.stdout.strip(),
                  "exit %d · %d chars" % (hr.returncode, len(hr.stdout.strip())))
        # (control) one rule of ★their own★, pointing at one of ★their★ notes — the same wired line
        # must now speak, from their note. Otherwise the silence above could be a dead hook.
        _py(env, "\n".join([
            "from brain import ruledisc",
            "ruledisc.save({'version': 1, 'disabled': [], 'learned': [{'id': 'mine',",
            "  'why': 'my own rule', 'tools': ['run_shell'], 'match': ['terraform'],",
            "  'memories': ['note_00'], 'once': 'session', 'enabled': True}]})",
            "ruledisc.sync_shell()",
        ]))
        hr = _hook("terraform plan")
        check("(control) with one rule of their own, the same hook speaks — from their note",
              "a note about" in hr.stdout and "my own rule" in hr.stdout,
              hr.stdout.strip()[:80] or (hr.stderr or "")[-80:])

    # ── and a first recall actually returns something ────────────────────────
    rc = _py(env, "\n".join([
        "from brain import search, store",
        "db = store.connect()",
        "rows = search.recall(db, 'the rollback took eleven minutes', k=3, log=False)",
        "print('HITS=%d' % len(rows))",
    ]))
    check("a first recall on their own notes returns something", "HITS=" in (rc.stdout or "")
          and int(re.search(r"HITS=(\d+)", rc.stdout).group(1)) > 0,
          (rc.stdout or "").strip() or (rc.stderr or "")[-90:])


def path_rule() -> None:
    """★Where an installed copy keeps a person's own evaluation set★ — the rule, not a simulation.

    ⛔ It used to be `<package>/../tests/eval`, which in an installed copy is
    `site-packages/tests/eval`: outside the package, wiped by an upgrade, often unwritable — and
    `brain eval-init` would have written a person's labelled sample there. `store.default_config_path`
    had already learnt this lesson (§8-c); this is the same rule applied to the same kind of file.
    """
    from brain import evalinit as ei, store
    print("\n" + "─" * 78)
    print("★where an installed copy keeps its evaluation set★")
    print("─" * 78)
    saved = os.environ.get("BRAIN_EVAL_DIR")
    try:
        os.environ["BRAIN_EVAL_DIR"] = "/tmp/brain-eval-decided-by-a-human"
        check("1. what a human decided wins", ei.eval_dir() == "/tmp/brain-eval-decided-by-a-human")
        os.environ.pop("BRAIN_EVAL_DIR", None)
        check("2. a development checkout still uses the repository's own directory",
              ei.eval_dir() == os.path.join(ROOT, "tests", "eval"), ei.eval_dir())
        # 3. with no repository beside the package it must land in the brain's home, never in
        #    site-packages. Simulated by asking the rule what it would answer without step 2.
        import brain.evalinit as _m
        real_file = _m.__file__
        try:
            _m.__file__ = "/usr/local/lib/python3.9/site-packages/brain/evalinit.py"
            got = ei.eval_dir()
        finally:
            _m.__file__ = real_file
        check("3. ★an installed copy lands in the brain's home, not in site-packages★",
              got == os.path.join(store.brain_home(), "eval") and "site-packages" not in got, got)
    finally:
        if saved is None:
            os.environ.pop("BRAIN_EVAL_DIR", None)
        else:
            os.environ["BRAIN_EVAL_DIR"] = saved


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="brain-firstday-")
    try:
        # ⛔ Three shapes a stranger actually arrives in. The author's own (ko, labelled, warm) is
        #    deliberately ★not★ one of them — that configuration is what every other check covers.
        path_rule()
        first_day(tmp + "/a", "English · no language set at all", {})
        first_day(tmp + "/b", "German OS locale · no BRAIN_LANG → English", {"LC_ALL": "de_DE.UTF-8"})
        first_day(tmp + "/c", "a brand-new user with almost nothing", {"BRAIN_LANG": "en"}, n_docs=3)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ a stranger's first day works — their language, their notes, no labels, no key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
