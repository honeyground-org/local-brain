"""Help check — ★does what's written diverge from the actual commands★.

## Why this check exists

A command list ★rots if left alone★. Add a new command and forget to add it to help, and nobody finds
it; leave a removed command listed and whoever types it hits an error. Both are silent failures.

⛔ This repo has learned the same shape repeatedly — ★something written in two places gets fixed in only one.★
   So the dashboard also ★reads `HELP_GROUPS` straight from the CLI★ too (never writes it twice).
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from brain import cli, i18n                               # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    print("=" * 72 + "\nhelp ↔ actual commands\n" + "=" * 72)
    ap = cli.build_parser() if hasattr(cli, "build_parser") else None
    # the actually-registered subcommands are read from the usage line (never relying on parser internals)
    from tests import _needs
    brain = _needs.entry("brain", "brain.cli")          # Windows: python -m brain.cli (no sh there)
    out = subprocess.run(brain + ["--help"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    m = re.search(r"\{([a-z0-9,\-]+)\}", out)
    real = set(m.group(1).split(",")) if m else set()
    check("read the real command list", len(real) > 5, "%d found" % len(real))

    listed = set()
    for _title, items in cli.HELP_GROUPS:
        for c, _w in items:
            listed.add(c.split()[0])

    missing = sorted(real - listed - {"help"})
    check("★nothing is missing from help★", not missing,
          "missing: %s" % (", ".join(missing) or "none"))

    ghost = sorted(listed - real)
    check("★nothing not-real is listed in help★", not ghost,
          "ghost: %s" % (", ".join(ghost) or "none"))

    # ⛔ ★help text is locale-aware★ (2026-09-08) — pin English here, since these checks
    #    assert specific English wording. Restored afterward (never leaves the language changed).
    saved_env, saved_lang = os.environ.get("BRAIN_LANG"), i18n._lang
    os.environ["BRAIN_LANG"] = "en"
    i18n._lang = None
    i18n.lang(refresh=True)
    try:
        txt = cli._help_text()
    finally:
        os.environ.pop("BRAIN_LANG", None)
        if saved_env:
            os.environ["BRAIN_LANG"] = saved_env
        i18n._lang = saved_lang
    # ⛔ ★`brain` has to be able to sit on PATH★ — a user actually hit `command not found: brain`.
    #    "cd there and run ./bin/brain" is a detour, not an answer.
    import shutil as _sh
    check("help self-reports ★whether it's on PATH★ and says something different depending",
          ("works from any directory" in txt) if _sh.which("brain") else ("is not on your PATH" in txt))
    check("the install script exists",
          os.access(os.path.join(ROOT, "install", "path.sh"), os.X_OK))

    # ★does it follow a symlink★ — dirname($0) is not the repo when reached through a link
    launcher = open(os.path.join(ROOT, "bin", "brain"), encoding="utf-8").read()
    check("★the launcher follows a symlink★ (finds the repo even when reached through a link)",
          "readlink" in launcher and "while [ -L" in launcher)
    # ⛔ ★comments must never be counted★ — a comment reading "never use readlink -f" tripped the check.
    #    the check must look only at ★lines that actually run★.
    code = "\n".join(l for l in launcher.splitlines() if not l.lstrip().startswith("#"))
    check("⛔ never relies on `readlink -f`, which macOS lacks", "readlink -f" not in code)

    import subprocess as _sp
    import tempfile as _tf
    if os.name == "nt":
        print("   (the launcher through a link: n/a on Windows — `brain` is a pip-installed .exe there)")
    else:
        with _tf.TemporaryDirectory() as td:
            link = os.path.join(td, "brain")
            os.symlink(os.path.join(ROOT, "bin", "brain"), link)
            r = _sp.run([link, "help"], capture_output=True, text=True, cwd=td, encoding="utf-8", errors="replace")
            check("★runs from a different folder through the link★ (measured)",
                  r.returncode == 0 and "brain —" in r.stdout,
                  "exit code %d %s" % (r.returncode, (r.stderr or "")[:60]))

    check("the 'Where things live' section is written", "Where things live" in txt)
    check("how to check the scheduled jobs is written", "launchd" in txt and "status" in txt)
    # ⛔ ★the home this machine really uses★ — the screen used to print `~/.brain` whatever the home was
    from brain import store
    check("the data location is written — the real home", store.brain_home() in txt, store.brain_home())

    # calling it with no argument shows help (not an error)
    p = subprocess.run(brain, capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("★help comes up even with no argument★ (not an error)",
          p.returncode == 0 and "brain —" in p.stdout, "exit code %d" % p.returncode)

    # the dashboard uses the same source
    src = open(os.path.join(ROOT, "brain", "dashboard.py"), encoding="utf-8").read()
    check("★the dashboard reads the CLI's list directly★ (never writes it twice)",
          "HELP_GROUPS" in src)

    # ── ★never weighs approaches against each other by the composite★ (user flagged 2026-08-31) ──────────────
    print("\n" + "=" * 72 + "\nraw-value comparison — ★never sums things with different measurement scopes★\n" + "=" * 72)
    from brain import scorecard as sk, store
    db = store.connect()
    b = sk.bench(db)
    keys = [r[0] for r in b["rows"]]
    for want in ("Answer found", "Storage density", "Latency", "Documents read"):
        check("the raw-value table has a ★%s★ yardstick" % want,
              any(want in k for k in keys))
    nones = sum(1 for r in b["rows"] if None in (r[2], r[3], r[4]))
    check("★a structurally inapplicable cell is None, not 0★", nones >= 2,
          "%d row(s) with a None cell" % nones)
    # ⛔ ★render_bench is locale-aware★ (2026-09-08) — pin English for these literal checks.
    saved_env2, saved_lang2 = os.environ.get("BRAIN_LANG"), i18n._lang
    os.environ["BRAIN_LANG"] = "en"
    i18n._lang = None
    i18n.lang(refresh=True)
    try:
        txt = sk.render_bench(b)
    finally:
        os.environ.pop("BRAIN_LANG", None)
        if saved_env2:
            os.environ["BRAIN_LANG"] = saved_env2
        i18n._lang = saved_lang2
    check("the table renders as 'n/a' (so it never looks like 0 won)", "n/a" in txt)
    check("⛔ a warning against comparing approaches by the composite is in the table",
          "never compared by the composite score" in txt)
    lat = next(r for r in b["rows"] if "Latency" in r[0])
    auto = next(r for r in b["rows"] if "Injected automatically" in r[0])
    if b.get("no_sample"):
        # these two rows are measured on ★your★ labelled questions — a fresh machine shows them as n/a
        check("with no labelled sample, the per-question rows are n/a — not 0",
              lat[2] is None and lat[3] is None and auto[2] is None)
    else:
        check("★never hides that grep wins on latency★", lat[3] < lat[2],
              "brain %.1fms ↔ grep %.1fms" % (lat[2], lat[3]))
        check("the brain's 'amount injected automatically' is ★never written as 0★", auto[2] > 0,
              "%.2f KB" % auto[2])

    print("=" * 72)
    if FAIL:
        print("❌ %d failure(s): %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ all passed — help matches the real commands")
    return 0


if __name__ == "__main__":
    sys.exit(main())
