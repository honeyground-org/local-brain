#!/bin/sh
# ★a 3-line wrapper for Mac·Linux★ — the real installer is `brain/install.py` (one Python copy).
#
# ⛔ why this changed (2026-09-02): this file is POSIX shell, and ★Windows has no `sh`, so not a single
#    line runs.★ Making a separate `install.ps1` would just reproduce the trap this repository keeps
#    getting burned by — ★"a rule in two places gets fixed in one."★
#    (This script was in fact already half Python heredoc — 5 blocks.)
#
#   Mac·Linux:  ./install.sh --all
#   Windows:     python -m brain.install --all
#   either way: python3 -m brain.install --dry-run   ← ★see what would change first★
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
command -v python3 >/dev/null 2>&1 || {
  echo "python3 not found. This tool needs only python3 3.8+." >&2; exit 1; }
exec env PYTHONPATH="$DIR" python3 -m brain.install "$@"
