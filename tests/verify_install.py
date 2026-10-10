"""Installer check — ★does it run to completion in a fake HOME, and protect someone else's config★. (local · budget 0)

## Why (user requirement 2026-09-02: download and install on a different PC · Mac·Windows)

The installer is ★the most outward-reaching code★ in this tool — it edits someone else's config files,
calls `claude mcp add`, and registers scheduled jobs with the OS.
★Breaking someone else's environment is the installer's worst possible failure.★

## ⛔ What this check protects

1. ★Runs to completion in a fake HOME★ — measures every step without touching the real machine.
2. ★Preserves someone else's hook★ — a third-party hook planted beforehand must survive (+ a backup).
3. ★Safe to run more than once★ — a second run doesn't put the same hook in again.
4. ★Never touches a broken config★ — never breaks it worse trying to fix it.
5. ★`--dry-run` really changes nothing★ — on 2026-09-02, in a clean room, `claude mcp add`
   actually went out (harmless because HOME was fake, but ★relying on that
   is a design flaw★). So a switch was made, and it's measured right here.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []
THIRD_PARTY = "someone-elses-hook --do-not-remove"


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def _md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def _fake_home(tmp, settings=None):
    home = os.path.join(tmp, "home")
    mem = os.path.join(home, ".claude", "projects", "-fake", "memory")
    os.makedirs(mem, exist_ok=True)
    for i in range(6):
        with open(os.path.join(mem, "fake_%d.md" % i), "w", encoding="utf-8") as fh:
            fh.write("---\nname: fake-%d\ndescription: fake memory %d\n---\n\n"
                     "rebase PR latest commit %d\n" % (i, i, i))
    sp = os.path.join(home, ".claude", "settings.json")
    with open(sp, "w", encoding="utf-8") as fh:
        fh.write(settings if settings is not None else json.dumps(
            {"hooks": {"UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command",
                                           "command": THIRD_PARTY}]}]}}))
    return home, sp


def _run(home, args):
    env = dict(os.environ)
    env.update({"HOME": home, "USERPROFILE": home, "BRAIN_LANG": "en",
                "PYTHONPATH": ROOT,
                "BRAIN_HOME": os.path.join(home, ".claude", "brain"),
                "BRAIN_CONFIG": os.path.join(home, ".claude", "brain", "config.json")})
    return subprocess.run([sys.executable, "-m", "brain.install"] + args,
                          capture_output=True, text=True, env=env, cwd=ROOT, timeout=180, encoding="utf-8", errors="replace")


def main():
    print("=" * 72 + "\ninstaller — runs in a fake HOME and protects someone else's config\n" + "=" * 72)
    tmp = tempfile.mkdtemp()
    try:
        # ── ⑤ does --dry-run really change nothing (first — zero side effects) ──
        home, sp = _fake_home(tmp)
        before = _md5(sp)
        r = _run(home, ["--all", "--dry-run"])
        check("`--dry-run` succeeds", r.returncode == 0, (r.stderr or "")[-160:])
        check("★`--dry-run` never changes the config★", _md5(sp) == before)
        check("★`--dry-run` never creates the config★",
              not os.path.exists(os.path.join(home, ".claude", "brain", "config.json")))
        check("`--dry-run` ★says what it would do★",
              "would" in r.stdout and "DRY RUN" in r.stdout,
              "%d 'would' line(s)" % r.stdout.count("would"))
        # `claude` on PATH → the dry run names the command it would run; absent → the installer hands the
        # command to a human. ⛔ It asserted the first only, so on a machine without Claude Code (a clean
        # room, 2026-10-07) it went red while the installer did the right thing.
        has_claude = bool(shutil.which("claude"))
        check("★`--dry-run` never calls an outbound command either★ (only says so)",
              ("would run: claude mcp add" in r.stdout) if has_claude
              else ("claude mcp add" in r.stdout and "would run: claude mcp add" not in r.stdout),
              "claude on PATH" if has_claude else "no claude on PATH — the command is handed to a human")
        shutil.rmtree(home, ignore_errors=True)

        # ── ①②: does the real install run to completion · does someone else's hook survive ────────────
        home, sp = _fake_home(tmp)
        r = _run(home, ["--with-hook", "--with-guard"])
        check("install ★runs to completion★ in a fake HOME", r.returncode == 0,
              (r.stderr or "")[-200:])
        check("finds memories on its own", "memories found" in r.stdout)
        check("the index runs", "documents" in r.stdout)
        d = json.load(open(sp, encoding="utf-8"))
        cmds = [h["command"] for e in d["hooks"].get("UserPromptSubmit", [])
                for h in e.get("hooks", [])]
        check("★someone else's hook survives★ (guards against the installer's worst failure)",
              THIRD_PARTY in cmds, str(cmds)[:120])
        check("our hook went in too", any("brain" in c for c in cmds))
        check("★leaves a backup★",
              any(".bak." in f for f in os.listdir(os.path.dirname(sp))))
        # does the behaviour layer go in as ★two matchers★ (shell and editing are separate entries)
        pre = d["hooks"].get("PreToolUse", [])
        check("the behaviour layer goes in as ★two entries★ — shell·edit", len(pre) >= 2,
              "matcher: %s" % [e.get("matcher") for e in pre])

        # ── ③ safe to run more than once ────────────────────────────────────
        n1 = sum(len(e.get("hooks", [])) for ev in d["hooks"].values() for e in ev)
        r2 = _run(home, ["--with-hook", "--with-guard"])
        d2 = json.load(open(sp, encoding="utf-8"))
        n2 = sum(len(e.get("hooks", [])) for ev in d2["hooks"].values() for e in ev)
        check("★running it twice never grows the hook count★", n1 == n2, "%d → %d" % (n1, n2))
        check("the second run says 'already wired'", "already wired" in r2.stdout)
        shutil.rmtree(home, ignore_errors=True)

        # ── ④ never touches a broken config ──────────────────────────────────
        home, sp = _fake_home(tmp, settings="{ this is not json ")
        broken_before = _md5(sp)
        r3 = _run(home, ["--with-hook"])
        check("★never touches a broken config★ (never breaks it worse trying to fix it)",
              _md5(sp) == broken_before)
        check("★says★ it's broken (never quietly moves past it)",
              "not valid JSON" in r3.stdout, r3.stdout[-120:].replace("\n", " "))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAIL), ", ".join(FAIL)) if FAIL else "✅ all passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
