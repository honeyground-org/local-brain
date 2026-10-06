"""Secret scan of what actually ships — ★the axis GitHub was never going to run for us★ (2026-09-14).

## Why this exists

`verify_public_scrub.py` already counts secret-shaped strings, but with ★this repository's own
eight regexes★. The release plan's answer to "what if those eight miss something" was *"GitHub's
secret scanner will catch it"* — and that answer was wrong twice over:

  1. GitHub's scanner is ★free only on public repositories★. The snapshot is private, so it has
     never run, and it cannot run until the very moment the repository becomes public. Measured
     2026-09-14: an axis nobody had measured was being counted as covered.
  2. On a public repository it runs ★after the push★. "It leaks, then you are told" is not a gate.

So the gate moves ★in front of★ the push: an industry rule set (gitleaks, ~200 rules vs our 8)
runs locally on the snapshot before it ever leaves this machine.

## ⛔ This file holds no secret of its own

The control samples are ★assembled at runtime★ from pieces. Write one as a literal and this
checker becomes the leak it searches for — the same contradiction `verify_public_scrub` avoids
by keeping its term list outside the repository.

## Control group — ★prove the scanner can fail before trusting a zero★

Two different rule families (a private key block, a GitHub token) are planted in a temporary
folder and must both be found. If the control does not fire, a clean result is ★not a pass★ —
it is `unknown`, and this check says so and exits 2. This repository has been burned by
"a probe that cannot fail its own calibration" and by "an under-counting instrument calls
itself done"; a scanner silently matching nothing looks exactly like both.

## Use

    python3 tests/verify_secret_scanner.py                 # the public snapshot (../local-brain-public)
    python3 tests/verify_secret_scanner.py --here          # this repository instead
    python3 tests/verify_secret_scanner.py <path>

    exit 0 = scanner armed and nothing found · 1 = findings · 2 = ★not measured★

Needs the `gitleaks` binary (`brew install gitleaks`, or `BRAIN_GITLEAKS=/path/to/gitleaks`).
It is ★not a runtime dependency★ — nothing in `brain/` knows this file exists.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TARGET = os.path.join(os.path.dirname(ROOT), "local-brain-public")


def binary() -> str:
    """The scanner, or "" — ★absent is `unknown`, never `clean`★."""
    return (os.environ.get("BRAIN_GITLEAKS") or "").strip() or (shutil.which("gitleaks") or "")


def _run(exe: str, mode: str, path: str) -> list:
    """`gitleaks dir|git <path>` → the findings, values already redacted by the scanner."""
    with tempfile.TemporaryDirectory() as tmp:
        report = os.path.join(tmp, "report.json")
        try:
            subprocess.run([exe, mode, path, "--no-banner", "--redact",
                            "--report-format", "json", "--report-path", report],
                           capture_output=True, text=True)
        except OSError:
            # ⛔ a binary that will not start must not read as "found nothing" —
            #    the control group is what turns this into `unknown`.
            return []
        try:
            with open(report, encoding="utf-8") as fh:
                return json.load(fh) or []
        except (OSError, ValueError):
            return []


def control(exe: str) -> tuple:
    """Plant two families and confirm both are found. Returns (ok, rules_found)."""
    body = "MIIEowIBAAKCAQEA" + "x" * 48          # ⛔ short bodies do not trip the private-key rule
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "id_rsa"), "w", encoding="utf-8") as fh:
            fh.write("-----BEGIN " + "RSA PRIVATE" + " KEY-----\n"
                     + body + "\n-----END " + "RSA PRIVATE" + " KEY-----\n")
        with open(os.path.join(tmp, "planted.txt"), "w", encoding="utf-8") as fh:
            fh.write("token = \"" + "gh" + "p_"
                     + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + "\"\n")
        rules = sorted({f.get("RuleID", "?") for f in _run(exe, "dir", tmp)})
    return ({"private-key", "github-pat"} <= set(rules), rules)


def report(findings: list, axis: str, what: str) -> int:
    print(f"[{axis}] {what} — {len(findings)} finding(s)")
    for f in findings:
        loc = f.get("File") or f.get("Commit", "")[:12]
        print(f"   ⛔ {f.get('RuleID', '?'):<24} {loc}:{f.get('StartLine', '?')}")
    return len(findings)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    target = ROOT if "--here" in sys.argv else (os.path.abspath(args[0]) if args else DEFAULT_TARGET)

    print("=" * 74)
    print("secret scan of what ships — an outside rule set, in front of the push")
    print("=" * 74)
    print(f"target  {target}")

    exe = binary()
    if not exe:
        print("\n⛔⛔ NOT MEASURED — no `gitleaks` on this machine.")
        print("    A zero printed here would be a lie: nothing was scanned.")
        print("    `brew install gitleaks`  ·  or  BRAIN_GITLEAKS=/path/to/gitleaks")
        # ⏭ 77 = skipped, not passed: this is the publisher's gate, and its tool is not here.
        return 77
    try:
        ver = subprocess.run([exe, "version"], capture_output=True, text=True).stdout.strip()
    except OSError as exc:                                # ⛔ a path that points at nothing
        print(f"\n⛔⛔ NOT MEASURED — `{exe}` will not start ({exc.strerror}).")
        print("    Fix BRAIN_GITLEAKS, or unset it to use the one on PATH.")
        return 2
    print(f"scanner {exe} ({ver or 'version unknown'})")

    if not os.path.isdir(target):
        print(f"\n⛔⛔ NOT MEASURED — no such folder. Build the snapshot first "
              f"(`python3 tests/export_public.py`).")
        return 77                                         # ⏭ skipped — nothing to scan yet

    print("\n" + "-" * 74)
    ok, rules = control(exe)
    print(f"[control] planted 2 families → found {rules or 'nothing'}")
    if not ok:
        print("⛔⛔ NOT MEASURED — the scanner did not catch its own planted samples.")
        print("    A clean result from a probe that cannot fail is not a pass.")
        return 2
    print("   ✅ the scanner can fail — so a zero below means something")

    print("\n" + "-" * 74)
    n = report(_run(exe, "dir", target), "axis1", "files that ship")

    print("\n" + "-" * 74)
    if os.path.isdir(os.path.join(target, ".git")):
        n += report(_run(exe, "git", target), "axis2", "history (★push sends history, not files★)")
    else:
        print("[axis2] history — none (not a git repository yet)")

    print("\n" + "=" * 74)
    if n:
        print(f"⛔ {n} finding(s) — ★do not make this public★")
        return 1
    print("✅ armed scanner, nothing found in files or history")
    print("   ⛔ a scanner bounds what is secret-shaped — ★not what is confidential★."
          "\n      Internal names and identity stay with verify_public_scrub.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
