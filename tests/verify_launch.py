#!/usr/bin/env python3
"""★Every way brain is started runs the installed brain — never a `brain/` in the folder it starts in★ (local · budget 0)

Why this exists (2026-10-14)
----------------------------
Every launcher ran `python3 -m brain.<module>`. With `-m`, Python puts the ★current directory★ first on
`sys.path` — ahead of PYTHONPATH. Hosts start the hook, the guard and the MCP server in the person's project
folder, so a folder with a `brain/` package of its own had ★its★ code run instead: a working copy of this
repository answered the live hook and wrote records against the live index, and any repository opened in a
host with brain installed could have done the same on every prompt.

Each launcher now goes through `bin/_run.py` (or, without it, a one-line bootstrap — §launch), which puts the
installed brain first and drops the current directory. This check starts them from a folder holding a decoy
`brain/` package that leaves a mark when imported:

  ① every `bin/` launcher (POSIX) runs from the decoy folder without importing the decoy, and does its work
  ② the command the installer writes (`launch.argv`) and its fallback without `bin/` do the same — on every
     platform
  ③ nothing that writes or runs a command still says `-m brain.` alone: the installer's last resort, the
     Codex snippet, the MCP server's own restart

⛔ Control: the old form (`PYTHONPATH=<repo> python3 -m brain.cli`) started from the same folder ★does★ run
   the decoy — so ① and ② could fail.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain import launch  # noqa: E402

FAILS = []
_TMP = tempfile.mkdtemp(prefix="brain-launch-")
DECOY = os.path.join(_TMP, "project")
MARK = os.path.join(_TMP, "decoy-was-imported")


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def _decoy() -> None:
    os.makedirs(os.path.join(DECOY, "brain"))
    body = "open(%r, 'a').write('imported\\n')\n" % MARK
    for f in ("__init__.py", "hook.py", "cli.py", "server.py", "tail.py", "guard.py", "jobs.py"):
        with open(os.path.join(DECOY, "brain", f), "w", encoding="utf-8") as fh:
            fh.write(body)


def _run(argv, stdin: str = "") -> subprocess.CompletedProcess:
    if os.path.exists(MARK):
        os.remove(MARK)
    env = dict(os.environ, BRAIN_HOME=os.path.join(_TMP, "home"), BRAIN_CONFIG=os.path.join(_TMP, "config.json"))
    env.pop("BRAIN_HOOK_DISABLED", None)
    return subprocess.run(argv, cwd=DECOY, input=stdin, capture_output=True, text=True, timeout=60,
                          env=env, encoding="utf-8", errors="replace")


def main() -> int:
    print("=" * 78)
    print("every launcher runs the installed brain, never a brain/ in the folder it starts in")
    print("=" * 78)
    _decoy()
    with open(os.path.join(_TMP, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"sources": []}, fh)

    # ── control: the old form runs the decoy ─────────────────────────────────
    env = dict(os.environ, PYTHONPATH=ROOT)
    if os.path.exists(MARK):
        os.remove(MARK)
    subprocess.run([sys.executable, "-m", "brain.cli", "help"], cwd=DECOY, env=env, capture_output=True,
                   text=True, timeout=60, encoding="utf-8", errors="replace")
    check("★control★: the old form (`PYTHONPATH=<repo> python -m brain.cli`) runs the decoy",
          os.path.exists(MARK))

    # ── ① every bin/ launcher ────────────────────────────────────────────────
    if os.name == "nt":
        print("   (the bin/ launchers are POSIX shell — ② covers what Windows runs)")
    else:
        b = os.path.join(ROOT, "bin")
        cases = (("brain", ["help"], "", lambda r: "brain" in r.stdout),
                 ("brain-hook", ["--host", "claude-code"], json.dumps({"prompt": ""}), lambda r: r.returncode == 0),
                 ("brain-end", [], "{}", lambda r: r.returncode == 0),
                 ("brain-stop", [], "{}", lambda r: r.returncode == 0),
                 ("brain-session", ["--host", "claude-code"], "{}", lambda r: r.returncode == 0),
                 ("brain-mcp", [], "", lambda r: r.returncode == 0))
        for name, args, stdin, did in cases:
            r = _run([os.path.join(b, name)] + args, stdin)
            check("① %-14s from a folder with a decoy brain/: the decoy is not imported, and it runs" % name,
                  not os.path.exists(MARK) and did(r), "exit %s · %s" % (r.returncode, (r.stderr or "")[-80:]))

    # ── ② what the installer writes, and its fallback ────────────────────────
    r = _run(launch.argv("brain.cli") + ["help"])
    check("② the installer's command (bin/_run.py) runs from the decoy folder without importing it",
          not os.path.exists(MARK) and "brain" in r.stdout, " ".join(launch.argv("brain.cli"))[-60:])
    real = launch.RUNNER
    launch.RUNNER = os.path.join(_TMP, "no-bin-here", "_run.py")
    try:
        av = launch.argv("brain.cli")
    finally:
        launch.RUNNER = real
    r = _run(av + ["help"])
    check("② without bin/ (an installed or copied package) the one-line bootstrap does the same",
          av[1] == "-c" and not os.path.exists(MARK) and "brain" in r.stdout, "exit %s" % r.returncode)

    # ── ③ nothing still writes `-m brain.` alone ─────────────────────────────
    from brain import hosts, install
    cmd = install.entry_command("brain-surely-not-on-path", "brain.cli")
    check("③ the installer's last resort goes through the runner", "_run.py" in cmd and " -m " not in cmd, cmd[-60:])
    codex = next(h for h in hosts.all_hosts() if isinstance(h, hosts.Codex))
    snip = " ".join(codex.mcp_setup([]).get("snippet") or [])
    check("③ the Codex MCP snippet goes through the runner", "_run.py" in snip and '"-m"' not in snip, snip[-80:])
    srv = open(os.path.join(ROOT, "brain", "server.py"), encoding="utf-8").read()
    check("③ the MCP server restarts itself through the runner", '"-m", "brain.server"' not in srv
          and "launch.argv(\"brain.server\")" in srv)

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — every launcher runs the installed brain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
