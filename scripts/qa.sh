#!/usr/bin/env bash
# QA gate — the checks in tests/ that need nothing but this repo and a Python.
#
# tests/ holds two kinds of scripts. The ones below are hermetic (no index, no
# ~/.claude/brain, no eval set, no MCP server) and are what CI runs. The others
# (verify_recall, verify_vs_grep, verify_guard, verify_ruledisc, …) measure a
# real brain against a real eval set and are run by hand per README §"검사".
#
#   scripts/qa.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

HERMETIC=(
  chunked_rerank diagnostics hosts install liveness package privacy
  rerank_budget scheduler symmetry
)
# Run with a throwaway HOME so nothing on the machine can make a check pass.
export HOME="$(mktemp -d)"
PY="${PYTHON:-python3}"

FAILED=()
for name in "${HERMETIC[@]}"; do
  echo; echo "━━━ tests/verify_$name.py"
  if "$PY" "tests/verify_$name.py"; then echo "    ✔ $name"; else echo "    ✘ $name"; FAILED+=("$name"); fi
done

echo; echo "━━━ byte-compile brain/"
"$PY" -m compileall -q brain && echo "    ✔ compileall" || { echo "    ✘ compileall"; FAILED+=(compileall); }

echo; if [ ${#FAILED[@]} -eq 0 ]; then echo "QA gate: PASS — $(git rev-parse --short HEAD)"; else echo "QA gate: FAIL — $(git rev-parse --short HEAD)"; printf '  ✘ %s\n' "${FAILED[@]}"; exit 1; fi
